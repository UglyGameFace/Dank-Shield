from __future__ import annotations

"""Server Structure Backups panel (Phase 3 flagship, slices 3a/3b).

Reached from /dank home → Setup & Server Settings → Server Backups
(registry key ``server_backups``) and from the **🗂️ Server Structure
Backups** button inside Backups & History (Config History).

This panel owns whole-server *structure* backups — categories, channels,
roles and permission overwrites — stored by
``stoney_verify.services.server_backup_service`` and restored through the
plan-first engine in ``stoney_verify.services.server_backup_restore``.
Dank Shield's own settings stay in Config History; the two backup kinds
sit side by side and the copy keeps them distinct.

Gate posture (spec §1.9 + the Phase 1/2 governing rule): creating a backup
and confirming a restore are Dank Shield Pro actions, resolved through
``services.membership_gate.premium_decision`` with premium feature
``server_backup_restore``. A guild that already holds at least one stored
backup is grandfathered (it already uses the feature). Viewing the list
and deleting a backup are never gated, so a lapsed guild can always see
and remove its backups. While membership is unconfigured the gate never
fires and the panel simply works.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from .discord_time import discord_timestamp_pair
from .ui.picker import DankPagedPickerView, DankPickerOpenButton, make_choice
from .services import membership_gate
from .services import server_backup_restore as restore_engine
from .services import server_backup_service as backup_service

logger = logging.getLogger(__name__)

PREMIUM_FEATURE = "server_backup_restore"
STALE_RUNNING_SECONDS = 600  # spec §1.8: a "running" cursor older than 10 min is resumable
FIDELITY_NOTE = (
    "A structure backup saves categories, channels, roles, permission "
    "overwrites and server settings — not messages, members, or per-member "
    "permission overwrites. Stage channels and the server icon/banner are "
    "not recreated."
)
_DENIAL_DETAIL = (
    "Whole-server structure backups save your categories, channels, roles "
    "and permission overwrites so a damaged or rebuilt server can be "
    "restored from a reviewed plan. Viewing and deleting backups you "
    "already have stays free."
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


async def _require_setup_permission(interaction: discord.Interaction) -> bool:
    from .commands_ext.public_setup_group import _require_setup_permission as require

    return await require(interaction)


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_solid as solid

    await solid._safe_defer_update(interaction)


async def _edit(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: Optional[discord.ui.View],
) -> None:
    from .commands_ext import public_setup_solid as solid

    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _private(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
) -> None:
    kwargs: dict[str, Any] = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception:
        logger.warning("server backups private reply failed", exc_info=True)


def _format_timestamp(value: Any) -> str:
    return discord_timestamp_pair(value, absolute_style="f", fallback="Unknown time")


def _format_size(value: Any) -> str:
    size = _safe_int(value, 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _backup_title(row: Mapping[str, Any]) -> str:
    label = _safe_str(row.get("label"))
    if label:
        return label
    return f"Backup #{_safe_int(row.get('backup_id'))}"


def _parse_iso(value: Any) -> Optional[datetime]:
    text = _safe_str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _restore_state(row: Mapping[str, Any]) -> Mapping[str, Any]:
    state = row.get("restore_state")
    return state if isinstance(state, Mapping) else {}


def _is_resumable(row: Mapping[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Resume is offered for a stopped restore, or a "running" cursor that
    has gone stale (spec §1.8: older than 10 minutes means nobody is
    actively driving it; the engine revalidates every row anyway)."""

    state = _restore_state(row)
    status = _safe_str(state.get("status"))
    if status == "cancelled":
        return True
    if status != "running":
        return False
    started = _parse_iso(state.get("started_at"))
    if started is None:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - started).total_seconds() >= STALE_RUNNING_SECONDS


def _state_suffix(row: Mapping[str, Any]) -> str:
    state = _restore_state(row)
    status = _safe_str(state.get("status"))
    if status == "running":
        return " • ⏸️ restore paused — resumable" if _is_resumable(row) else " • ⏳ restore in progress"
    if status == "cancelled":
        return " • ⏸️ restore stopped — resumable"
    if status == "failed":
        return " • ⚠️ last restore failed"
    if status == "done":
        return " • ✅ last restore finished"
    return ""


def _backup_line(row: Mapping[str, Any]) -> str:
    created_by = _safe_int(row.get("created_by"))
    by = f" • by <@{created_by}>" if created_by > 0 else ""
    return (
        f"**{_backup_title(row)}** — {_format_timestamp(row.get('created_at'))} "
        f"• {_format_size(row.get('snapshot_bytes'))}{by}{_state_suffix(row)}"
    )


@dataclass
class _PlanCounts:
    creates: int = 0
    updates: int = 0
    skips: int = 0
    blocked: int = 0
    deletes: int = 0
    blocked_reasons: list[str] = field(default_factory=list)
    setting_changes: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> int:
        return self.creates + self.updates + self.deletes


def _plan_counts(plan: list[Mapping[str, Any]]) -> _PlanCounts:
    counts = _PlanCounts()
    for row in plan:
        action = _safe_str(row.get("action"))
        status = _safe_str(row.get("status"))
        if action == "create" and status == "changed":
            counts.creates += 1
        elif action == "update" and status == "changed":
            counts.updates += 1
            if _safe_str(row.get("kind")) == "guild_setting":
                after = row.get("after")
                if isinstance(after, Mapping):
                    counts.setting_changes.extend(str(key) for key in after.keys())
        elif action == "delete_candidate":
            counts.deletes += 1
        elif action == "blocked":
            counts.blocked += 1
            blockers = row.get("blockers")
            if isinstance(blockers, (list, tuple)):
                for blocker in blockers:
                    text = _safe_str(blocker)
                    if text and text not in counts.blocked_reasons:
                        counts.blocked_reasons.append(text)
        else:
            counts.skips += 1
    return counts


async def _has_any_backup(guild_id: int) -> bool:
    rows = await backup_service.list_backups(int(guild_id), limit=1)
    return len(rows) > 0


async def _gate_denial(
    interaction: discord.Interaction,
    *,
    action: str,
    grandfathered: bool,
) -> Optional[Any]:
    """Return the guard's denied decision, or None when the action may proceed."""

    if interaction.guild is None:
        return None
    return await membership_gate.premium_decision(
        guild_id=int(interaction.guild.id),
        client=interaction.client,
        action=action,
        premium_feature=PREMIUM_FEATURE,
        grandfathered=grandfathered,
    )


async def _deny_with_pro_embed(interaction: discord.Interaction, decision: Any) -> None:
    await _private(
        interaction,
        embed=membership_gate.pro_denial_embed(
            PREMIUM_FEATURE, decision, detail=_DENIAL_DETAIL
        ),
    )


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------


def _list_embed(
    backups: list[Mapping[str, Any]],
    *,
    selected_id: int = 0,
    locked: bool = False,
    notice: str = "",
    error: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="🗂️ Server Structure Backups",
        description=(
            "Back up this server's **structure** — categories, channels, roles and "
            "permission overwrites — and restore it later from a plan you review "
            "first. This is different from **Backups & History**, which saves "
            "Dank Shield's own settings.\n\n" + FIDELITY_NOTE
        ),
        color=discord.Color.red() if error else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if backups:
        backups_name = f"Saved backups ({len(backups)}/{backup_service.MAX_BACKUPS_PER_GUILD})"
        backups_value = "\n".join(_backup_line(row) for row in backups)[:1024]
    else:
        backups_name = "Saved backups"
        backups_value = "No structure backups yet. Create one before a big redesign or cleanup."
    embed.add_field(name=backups_name, value=backups_value, inline=False)
    selected = next(
        (row for row in backups if _safe_int(row.get("backup_id")) == int(selected_id or 0)),
        None,
    )
    if selected is not None:
        embed.add_field(
            name=f"Selected: {_backup_title(selected)}",
            value=(
                f"Saved {_format_timestamp(selected.get('created_at'))} • "
                f"{_format_size(selected.get('snapshot_bytes'))}. "
                "Use **Restore** to preview a restore plan, or **Delete Backup** to remove it."
            ),
            inline=False,
        )
    if locked:
        embed.add_field(
            name="⭐ Dank Shield Pro",
            value=(
                "Creating and restoring structure backups is a **Dank Shield Pro** "
                "feature, and this server is not covered yet. Viewing and deleting "
                "backups stays free. Open `/dank home` → **Setup & Server Settings** "
                "→ **Dank Shield Pro** to subscribe or link this server."
            ),
            inline=False,
        )
    message = error or notice
    if message:
        embed.add_field(
            name="⚠️ Problem" if error else "Note",
            value=str(message)[:1024],
            inline=False,
        )
    return embed


def _unavailable_embed() -> discord.Embed:
    return discord.Embed(
        title="🗂️ Server Structure Backups",
        description=(
            "Server structure backups aren't available yet — the backup storage "
            "hasn't been switched on for this bot. Nothing is broken in your "
            "server; this feature turns on once the bot operator enables its "
            "storage.\n\n**Backups & History** (Dank Shield settings backups) "
            "still works as normal."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )


def _mode_embed(row: Mapping[str, Any], *, resume: bool = False) -> discord.Embed:
    state = _restore_state(row)
    embed = discord.Embed(
        title=f"♻️ Restore: {_backup_title(row)}",
        description=(
            f"Saved {_format_timestamp(row.get('created_at'))} • "
            f"{_format_size(row.get('snapshot_bytes'))}.\n\n"
            "Choose how much of this backup to apply. The next screen shows the "
            "full plan — creates, updates, blocked items and anything extra that "
            "would be deleted — before anything changes."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if resume:
        embed.description = (
            (embed.description or "")
            + "\n\n**Resuming:** the plan is rebuilt from the backup, and items "
            "that already match are skipped, so a stopped restore picks up "
            "where it left off."
        )
    elif _safe_str(state.get("status")) == "running":
        embed.add_field(
            name="⏳ A restore is marked in progress",
            value=(
                "Another restore from a backup is still marked as running for "
                "this server. Starting a new one is refused until it finishes "
                "or goes stale (10 minutes), so two restores never fight."
            ),
            inline=False,
        )
    embed.add_field(
        name="Restore modes",
        value=(
            "• **Everything** — match the backup: create what's missing and "
            "update what changed. Items that exist now but aren't in the backup "
            "are listed separately and only deleted if you confirm that on the "
            "final screen.\n"
            "• **Missing only** — the safest recovery: recreate what's gone and "
            "leave everything else untouched. Nothing is updated or deleted."
        ),
        inline=False,
    )
    return embed


def _plan_embed(
    row: Mapping[str, Any],
    *,
    mode: str,
    plan: list[Mapping[str, Any]],
) -> discord.Embed:
    counts = _plan_counts(plan)
    mode_label = "Missing only" if mode == "missing" else "Everything"
    embed = discord.Embed(
        title=f"📋 Restore Plan: {_backup_title(row)}",
        description=(
            f"Mode: **{mode_label}**. This is a preview only — nothing has "
            "changed yet. A safety check runs on the next step, and the restore "
            "only starts after a final confirmation."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="What would happen",
        value=(
            f"• **{counts.creates}** to create\n"
            f"• **{counts.updates}** to update\n"
            f"• **{counts.skips}** already match (skipped)\n"
            f"• **{counts.blocked}** blocked (cannot be applied)"
        ),
        inline=False,
    )
    if counts.blocked_reasons:
        shown = "\n".join(f"• {reason}" for reason in counts.blocked_reasons[:5])
        if len(counts.blocked_reasons) > 5:
            shown += f"\n• …and {len(counts.blocked_reasons) - 5} more"
        embed.add_field(name="Blocked items", value=shown[:1024], inline=False)
    if counts.setting_changes:
        names = ", ".join(sorted(set(counts.setting_changes))[:8])
        embed.add_field(
            name="Server settings that would change",
            value=f"Settings in the backup that differ now: {names}.",
            inline=False,
        )
    if counts.deletes:
        embed.add_field(
            name=f"Extra items not in this backup: {counts.deletes}",
            value=(
                "These exist now but are not in the backup. They are **kept** "
                "unless you separately confirm deleting them on the final "
                "screen. Deletions run last, are permanent, and cannot be "
                "rolled back."
            ),
            inline=False,
        )
    return embed


def _confirm_embed(
    row: Mapping[str, Any],
    *,
    mode: str,
    plan: list[Mapping[str, Any]],
    delete_armed: bool = False,
) -> discord.Embed:
    counts = _plan_counts(plan)
    mode_label = "Missing only" if mode == "missing" else "Everything"
    embed = discord.Embed(
        title=f"⚠️ Confirm Restore: {_backup_title(row)}",
        description=(
            f"The safety check passed. Restoring in **{mode_label}** mode will "
            f"create **{counts.creates}** and update **{counts.updates}** "
            "items. If anything fails midway, changes already made are rolled "
            "back where possible and the rest is reported."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Safety backup first",
        value=(
            "Before the first change, Dank Shield saves a fresh backup of the "
            "server exactly as it is right now, so you can restore back to "
            "this moment. If that safety backup can't be saved, the restore "
            "does not start."
        ),
        inline=False,
    )
    if counts.deletes:
        if delete_armed:
            value = (
                f"**{counts.deletes} extra items WILL be deleted** at the end "
                "of the restore. Deletions are permanent and cannot be rolled "
                "back. A final safety check runs when the restore starts and "
                "refuses to delete anything Dank Shield's settings point at. "
                "Tap the delete button again to disarm."
            )
        else:
            value = (
                f"{counts.deletes} items exist now that are not in this backup. "
                "They will be **kept**. To remove them as part of this restore, "
                "press **Also Delete Extra Items** below — deletions are "
                "permanent and cannot be rolled back."
            )
        embed.add_field(name="Extra items (optional deletions)", value=value, inline=False)
    return embed


def _preflight_failed_embed(
    row: Mapping[str, Any],
    *,
    mode: str,
    errors: list[str],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"❌ Safety Check Failed: {_backup_title(row)}",
        description=(
            "The restore did not start and **nothing was changed**. The server "
            "no longer matches the preview — fix the problems below (or go "
            "back and rebuild the plan) and try again."
        ),
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    shown = "\n".join(f"• {error}" for error in errors[:8])
    if len(errors) > 8:
        shown += f"\n• …and {len(errors) - 8} more"
    embed.add_field(name="Problems found", value=shown[:1024], inline=False)
    return embed


def _progress_embed(
    row: Mapping[str, Any],
    *,
    done: int,
    total: int,
    stopping: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"⏳ Restoring: {_backup_title(row)}",
        description=(
            f"Applied **{done}** of **{total}** changes so far. "
            "Progress is saved after every item, so a stopped restore can be "
            "resumed from the backup list."
            + ("\n\n**Stopping** after the current item…" if stopping else "")
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    return embed


def _result_embed(
    row: Mapping[str, Any],
    result: Any,
    *,
    cancelled: bool = False,
) -> discord.Embed:
    ok = bool(getattr(result, "ok", False))
    failure = _safe_str(getattr(result, "failure", ""))
    applied = list(getattr(result, "applied", []) or [])
    compensated = list(getattr(result, "compensated", []) or [])
    residual = list(getattr(result, "residual", []) or [])
    delete_results = list(getattr(result, "delete_results", []) or [])
    skipped_deletes = list(getattr(result, "skipped_deletes", []) or [])
    dropped = list(getattr(result, "dropped_overwrites", []) or [])
    pre_restore_id = _safe_int(getattr(result, "pre_restore_backup_id", 0))

    if ok:
        title = f"✅ Restore Complete: {_backup_title(row)}"
        color = discord.Color.green()
    elif cancelled:
        title = f"⏸️ Restore Stopped: {_backup_title(row)}"
        color = discord.Color.orange()
    else:
        title = f"❌ Restore Failed: {_backup_title(row)}"
        color = discord.Color.red()
    description = failure if failure else "The restore finished."
    if cancelled and not failure:
        description = "The restore was stopped. Everything already applied stays in place, and the restore can be resumed."
    embed = discord.Embed(
        title=title,
        description=description[:2000],
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    applied_lines = "\n".join(f"• `{key}`" for key in applied[:8])
    if len(applied) > 8:
        applied_lines += f"\n• …and {len(applied) - 8} more"
    embed.add_field(
        name=f"Applied changes ({len(applied)})",
        value=applied_lines or "Nothing was applied.",
        inline=False,
    )
    if pre_restore_id > 0:
        embed.add_field(
            name="Safety backup",
            value=(
                f"A backup of the server as it was just before this restore "
                f"was saved as backup #{pre_restore_id} (\"Pre-restore "
                "auto-backup\") in the list."
            ),
            inline=False,
        )
    if delete_results or skipped_deletes:
        deleted_ok = sum(1 for item in delete_results if item.get("ok"))
        failed = [item for item in delete_results if not item.get("ok")]
        lines = [f"• {deleted_ok} extra item(s) deleted."]
        for item in failed[:5]:
            lines.append(f"• ❌ `{_safe_str(item.get('key'))}`: {_safe_str(item.get('error'))[:120]}")
        if skipped_deletes:
            lines.append(
                f"• {len(skipped_deletes)} extra item(s) were kept because "
                "deletions were not confirmed."
            )
        embed.add_field(name="Deletions", value="\n".join(lines)[:1024], inline=False)
    notes: list[str] = []
    if compensated:
        notes.append(f"{len(compensated)} change(s) were rolled back after the failure.")
    if residual:
        notes.append(
            f"{len(residual)} planned change(s) were not applied; re-run the "
            "restore to rebuild a fresh plan for what remains."
        )
    if dropped:
        notes.append(
            f"{len(dropped)} permission overwrite(s) were dropped because "
            "their role could not be matched or created."
        )
    if notes:
        embed.add_field(name="Notes", value="\n".join(f"• {note}" for note in notes)[:1024], inline=False)
    return embed


def _delete_confirm_embed(row: Mapping[str, Any]) -> discord.Embed:
    return discord.Embed(
        title=f"🗑️ Delete Backup: {_backup_title(row)}",
        description=(
            f"Saved {_format_timestamp(row.get('created_at'))} • "
            f"{_format_size(row.get('snapshot_bytes'))}.\n\n"
            "Deleting this backup removes the saved structure snapshot. It "
            "does not change the server itself, and it cannot be undone."
        ),
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )


def _closed_embed() -> discord.Embed:
    return discord.Embed(
        title="🗂️ Server Backups Closed",
        description="Nothing else was changed. Reopen Server Backups whenever you need it.",
        color=discord.Color.blurple(),
    )


@dataclass
class _RestoreRunState:
    """Shared between the apply coroutine and the Stop button."""

    cancel_requested: bool = False
    done: int = 0
    total: int = 0


# ---------------------------------------------------------------------------
# Panel loading / rendering
# ---------------------------------------------------------------------------


async def _render_panel(
    interaction: discord.Interaction,
    *,
    selected_id: int = 0,
    notice: str = "",
    error: str = "",
) -> None:
    guild = interaction.guild
    if guild is None:
        return
    guild_id = int(guild.id)
    ready = await asyncio.to_thread(backup_service.backups_ready_sync)
    if not ready:
        view = _UnavailableView()
        await _edit(interaction, embed=_unavailable_embed(), view=view)
        return
    rows = list(await backup_service.list_backups(guild_id))
    if selected_id and not any(_safe_int(r.get("backup_id")) == int(selected_id) for r in rows):
        selected_id = 0
    decision = await _gate_denial(
        interaction,
        action="create a server structure backup",
        grandfathered=len(rows) > 0,
    )
    locked = decision is not None
    view = ServerBackupsView(rows, selected_id=selected_id, locked=locked)
    embed = _list_embed(rows, selected_id=selected_id, locked=locked, notice=notice, error=error)
    await _edit(interaction, embed=embed, view=view)


async def open_server_backups(interaction: discord.Interaction, *, notice: str = "") -> None:
    """Open the Server Structure Backups panel (registry route + Config
    History button both land here)."""

    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.", ephemeral=True
        )
    await _safe_defer_update(interaction)
    await _render_panel(interaction, notice=notice)


class _UnavailableView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:unavailable_history",
        row=0,
    )
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .config_history_ui import open_config_history

        await open_config_history(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:unavailable_close",
        row=0,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


class BackupSelect(DankPagedPickerView):
    """House picker for choosing a backup on the Server Backups panel.

    Was a raw ``discord.ui.Select`` that silently truncated the list
    with ``backups[:25]`` — backups past the first 25 could never be
    chosen. The picker pages the full list with ``chunk_choices``
    (page-suffixed custom_ids, Prev/Next), and picking a backup runs
    the exact flow the raw select used: the setup-permission gate,
    then ``_render_panel`` with the picked ``backup_id`` — so a pick
    selects exactly the same backup record as before. Back/Close
    re-renders the panel with the previous selection untouched.
    """

    def __init__(
        self,
        backups: list[Mapping[str, Any]],
        *,
        selected_id: int = 0,
        page: int = 0,
    ) -> None:
        self.backups = list(backups)
        self.selected_id = int(selected_id or 0)
        choices = []
        for row in self.backups:
            backup_id = _safe_int(row.get("backup_id"))
            created = _parse_iso(row.get("created_at"))
            when = created.strftime("%Y-%m-%d %H:%M UTC") if created else "Unknown time"
            choices.append(
                make_choice(
                    _backup_title(row)[:100],
                    str(backup_id),
                    description=f"{when} • {_format_size(row.get('snapshot_bytes'))}"[:100],
                    emoji="🗂️",
                    default=backup_id == self.selected_id,
                )
            )

        async def on_pick(interaction: discord.Interaction, value: str) -> None:
            if not await _require_setup_permission(interaction):
                return
            await _safe_defer_update(interaction)
            await _render_panel(interaction, selected_id=_safe_int(value))

        async def on_home(interaction: discord.Interaction) -> None:
            if not await _require_setup_permission(interaction):
                return
            await _safe_defer_update(interaction)
            await _render_panel(interaction, selected_id=self.selected_id)

        super().__init__(
            author_id=0,
            choices=choices,
            on_pick=on_pick,
            custom_id="dank:server_backups:backup:picker:v1",
            placeholder="Choose a backup to restore or delete…",
            title="Server Backups",
            timeout=900,
            on_home=on_home,
            on_cancel=on_home,
            page=page,
        )

    def _for_page(self, page: int) -> "BackupSelect":
        return BackupSelect(self.backups, selected_id=self.selected_id, page=page)


class ServerBackupsView(discord.ui.View):
    def __init__(
        self,
        backups: list[Mapping[str, Any]],
        *,
        selected_id: int = 0,
        locked: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.backups = list(backups)
        self.selected_id = int(selected_id or 0)
        self.locked = bool(locked)
        if self.backups:
            self.add_item(
                DankPickerOpenButton(
                    label="Choose Backup",
                    emoji="🗂️",
                    custom_id="dank:server_backups:backup:open",
                    row=0,
                    build=lambda interaction: BackupSelect(
                        self.backups, selected_id=self.selected_id
                    ),
                )
            )
        selected = self._selected_row()
        self.restore.disabled = selected is None
        self.delete_backup.disabled = selected is None
        if selected is None or not _is_resumable(selected):
            self.remove_item(self.resume)
        if self.locked:
            self.remove_item(self.create_backup)
            self.remove_item(self.restore)
            self.remove_item(self.resume)

    def _selected_row(self) -> Optional[Mapping[str, Any]]:
        for row in self.backups:
            if _safe_int(row.get("backup_id")) == self.selected_id:
                return row
        return None

    @discord.ui.button(
        label="Create Backup",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="dank_server_backups:create",
        row=1,
    )
    async def create_backup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        decision = await _gate_denial(
            interaction,
            action="create a server structure backup",
            grandfathered=await _has_any_backup(int(interaction.guild.id)),
        )
        if decision is not None:
            return await _deny_with_pro_embed(interaction, decision)
        await interaction.response.send_modal(CreateBackupModal())

    @discord.ui.button(
        label="Restore",
        emoji="♻️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_server_backups:restore",
        row=1,
    )
    async def restore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        if self._selected_row() is None:
            return await _private(interaction, "Choose a backup first.")
        await _open_restore_flow(interaction, self.selected_id)

    @discord.ui.button(
        label="Resume Restore",
        emoji="▶️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_server_backups:resume",
        row=1,
    )
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        if self._selected_row() is None:
            return await _private(interaction, "Choose a backup first.")
        await _open_restore_flow(interaction, self.selected_id, resume=True)

    @discord.ui.button(
        label="Delete Backup",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:delete",
        row=1,
    )
    async def delete_backup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        row = self._selected_row()
        if row is None:
            return await _private(interaction, "Choose a backup first.")
        await _safe_defer_update(interaction)
        await _edit(
            interaction,
            embed=_delete_confirm_embed(row),
            view=DeleteBackupConfirmView(_safe_int(row.get("backup_id"))),
        )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:refresh",
        row=2,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=self.selected_id)

    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:history",
        row=2,
    )
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .config_history_ui import open_config_history

        await open_config_history(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:close",
        row=2,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


class CreateBackupModal(discord.ui.Modal, title="Create Server Structure Backup"):
    label_input = discord.ui.TextInput(
        label="Label (optional)",
        placeholder="e.g. Before the big redesign",
        required=False,
        max_length=80,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ This must be used inside a server.")
        decision = await _gate_denial(
            interaction,
            action="create a server structure backup",
            grandfathered=await _has_any_backup(int(guild.id)),
        )
        if decision is not None:
            return await _deny_with_pro_embed(interaction, decision)
        await _safe_defer_update(interaction)
        label = _safe_str(self.label_input.value)
        try:
            row = await backup_service.create_backup(
                guild, created_by=int(interaction.user.id), label=label
            )
        except backup_service.SnapshotTooLargeError:
            return await _render_panel(
                interaction,
                error=(
                    "This server's structure is too large to save as one backup "
                    f"(over {backup_service.MAX_SNAPSHOT_BYTES // (1024 * 1024)} MB "
                    "of saved data). Nothing was saved."
                ),
            )
        except Exception as exc:
            logger.warning("server backup create failed", exc_info=True)
            return await _render_panel(
                interaction,
                error=f"The backup could not be saved: `{type(exc).__name__}: {str(exc)[:200]}`",
            )
        await _render_panel(
            interaction,
            selected_id=_safe_int(row.get("backup_id")),
            notice=f"✅ Backup saved: **{_backup_title(row)}** ({_format_size(row.get('snapshot_bytes'))}).",
        )


class DeleteBackupConfirmView(discord.ui.View):
    def __init__(self, backup_id: int) -> None:
        super().__init__(timeout=300)
        self.backup_id = int(backup_id)

    @discord.ui.button(
        label="Delete Backup",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:delete_confirm",
        row=0,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await _safe_defer_update(interaction)
        removed = await backup_service.delete_backup(int(guild.id), self.backup_id)
        await _render_panel(
            interaction,
            notice="✅ Backup deleted." if removed else "",
            error="" if removed else "That backup could not be deleted (it may already be gone).",
        )

    @discord.ui.button(
        label="Cancel",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:delete_cancel",
        row=0,
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=self.backup_id)


# ---------------------------------------------------------------------------
# Restore flow
# ---------------------------------------------------------------------------


async def _open_restore_flow(
    interaction: discord.Interaction,
    backup_id: int,
    *,
    resume: bool = False,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ This must be used inside a server.")
    await _safe_defer_update(interaction)
    row = await backup_service.get_backup(int(guild.id), int(backup_id))
    if row is None or not isinstance(row.get("snapshot"), Mapping):
        return await _render_panel(
            interaction,
            error="That backup could not be loaded (it may have been deleted). Nothing was changed.",
        )
    view = RestoreModeView(row, resume=resume)
    await _edit(interaction, embed=_mode_embed(row, resume=resume), view=view)


class RestoreModeSelect(DankPagedPickerView):
    """House picker for the restore mode (Everything / Missing only).

    Was a raw ``discord.ui.Select`` inside RestoreModeView. Picking
    runs the same flow: setup-permission gate, record the mode, then
    re-render RestoreModeView so the panel shows the new default.
    Back/Close re-renders the panel with the previous mode untouched.
    """

    def __init__(
        self,
        *,
        default_mode: str = "all",
        backup: Optional[Mapping[str, Any]] = None,
        resume: bool = False,
    ) -> None:
        self.default_mode = default_mode if default_mode in ("all", "missing") else "all"
        self.mode = self.default_mode
        self.backup = dict(backup) if isinstance(backup, Mapping) else None
        self.resume = bool(resume)

        async def on_pick(interaction: discord.Interaction, value: str) -> None:
            if not await _require_setup_permission(interaction):
                return
            self.mode = _safe_str(value, "all")
            await _safe_defer_update(interaction)
            # Re-render so the panel shows the new default.
            if self.backup is not None and interaction.guild is not None:
                await _edit(
                    interaction,
                    embed=_mode_embed(self.backup, resume=self.resume),
                    view=RestoreModeView(self.backup, resume=self.resume, mode=self.mode),
                )

        async def on_home(interaction: discord.Interaction) -> None:
            if not await _require_setup_permission(interaction):
                return
            await _safe_defer_update(interaction)
            if self.backup is not None and interaction.guild is not None:
                await _edit(
                    interaction,
                    embed=_mode_embed(self.backup, resume=self.resume),
                    view=RestoreModeView(
                        self.backup, resume=self.resume, mode=self.default_mode
                    ),
                )

        super().__init__(
            author_id=0,
            choices=[
                make_choice(
                    "Everything — match the backup",
                    "all",
                    description="Create what's missing and update what changed.",
                    emoji="♻️",
                    default=self.default_mode == "all",
                ),
                make_choice(
                    "Missing only — safest recovery",
                    "missing",
                    description="Recreate what's gone; update and delete nothing.",
                    emoji="🛡️",
                    default=self.default_mode == "missing",
                ),
            ],
            on_pick=on_pick,
            custom_id="dank:server_backups:mode:picker:v1",
            placeholder="Choose a restore mode…",
            title="Restore Mode",
            timeout=600,
            on_home=on_home,
            on_cancel=on_home,
        )


class RestoreModeView(discord.ui.View):
    def __init__(
        self,
        backup: Mapping[str, Any],
        *,
        resume: bool = False,
        mode: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=600)
        self.backup = dict(backup)
        self.resume = bool(resume)
        state = _restore_state(self.backup)
        cursor_mode = _safe_str(state.get("plan_mode"))
        chosen = mode or (cursor_mode if self.resume and cursor_mode in ("all", "missing") else "all")
        self.mode = chosen if chosen in ("all", "missing") else "all"
        self.add_item(
            DankPickerOpenButton(
                label="Choose Restore Mode",
                emoji="♻️",
                custom_id="dank:server_backups:mode:open",
                row=0,
                build=lambda interaction: RestoreModeSelect(
                    default_mode=self.mode, backup=self.backup, resume=self.resume
                ),
            )
        )

    @discord.ui.button(
        label="Preview Restore Plan",
        emoji="📋",
        style=discord.ButtonStyle.primary,
        custom_id="dank_server_backups:mode_preview",
        row=1,
    )
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        snapshot = self.backup.get("snapshot")
        if not isinstance(snapshot, Mapping):
            return await _private(interaction, "❌ That backup could not be loaded.")
        await _safe_defer_update(interaction)
        try:
            plan = restore_engine.build_restore_plan(guild, snapshot, mode=self.mode)
        except Exception as exc:
            logger.warning("restore plan build failed", exc_info=True)
            return await _render_panel(
                interaction,
                error=f"The restore plan could not be built: `{type(exc).__name__}: {str(exc)[:200]}`",
            )
        view = RestorePlanView(self.backup, mode=self.mode, plan=plan)
        await _edit(
            interaction,
            embed=_plan_embed(self.backup, mode=self.mode, plan=plan),
            view=view,
        )

    @discord.ui.button(
        label="Back to Backups",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:mode_back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=_safe_int(self.backup.get("backup_id")))

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:mode_close",
        row=1,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


class RestorePlanView(discord.ui.View):
    def __init__(
        self,
        backup: Mapping[str, Any],
        *,
        mode: str,
        plan: list[Mapping[str, Any]],
    ) -> None:
        super().__init__(timeout=600)
        self.backup = dict(backup)
        self.mode = mode
        self.plan = [dict(row) for row in plan]
        if _plan_counts(self.plan).actionable == 0:
            self.remove_item(self.continue_button)

    @discord.ui.button(
        label="Run Safety Check",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_server_backups:plan_check",
        row=0,
    )
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await _safe_defer_update(interaction)
        errors = await restore_engine.preflight_restore(
            guild, self.plan, delete_confirmed=False
        )
        if errors:
            view = PreflightFailedView(self.backup, mode=self.mode, plan=self.plan, errors=list(errors))
            await _edit(
                interaction,
                embed=_preflight_failed_embed(self.backup, mode=self.mode, errors=list(errors)),
                view=view,
            )
            return
        view = RestoreConfirmView(self.backup, mode=self.mode, plan=self.plan)
        await _edit(
            interaction,
            embed=_confirm_embed(self.backup, mode=self.mode, plan=self.plan),
            view=view,
        )

    @discord.ui.button(
        label="Back to Backups",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:plan_back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=_safe_int(self.backup.get("backup_id")))

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:plan_close",
        row=0,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


class PreflightFailedView(discord.ui.View):
    def __init__(
        self,
        backup: Mapping[str, Any],
        *,
        mode: str,
        plan: list[Mapping[str, Any]],
        errors: list[str],
    ) -> None:
        super().__init__(timeout=600)
        self.backup = dict(backup)
        self.mode = mode
        self.plan = [dict(row) for row in plan]
        self.errors = list(errors)

    @discord.ui.button(
        label="Back to Plan",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:preflight_back_plan",
        row=0,
    )
    async def back_plan(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        view = RestorePlanView(self.backup, mode=self.mode, plan=self.plan)
        await _edit(
            interaction,
            embed=_plan_embed(self.backup, mode=self.mode, plan=self.plan),
            view=view,
        )

    @discord.ui.button(
        label="Back to Backups",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:preflight_back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=_safe_int(self.backup.get("backup_id")))

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:preflight_close",
        row=0,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


class RestoreConfirmView(discord.ui.View):
    def __init__(
        self,
        backup: Mapping[str, Any],
        *,
        mode: str,
        plan: list[Mapping[str, Any]],
        delete_armed: bool = False,
    ) -> None:
        super().__init__(timeout=600)
        self.backup = dict(backup)
        self.mode = mode
        self.plan = [dict(row) for row in plan]
        self.delete_armed = bool(delete_armed)
        counts = _plan_counts(self.plan)
        if counts.deletes == 0:
            self.remove_item(self.delete_toggle)
        else:
            self.delete_toggle.label = (
                f"✅ Deletions Armed ({counts.deletes}) — tap to disarm"
                if self.delete_armed
                else f"Also Delete {counts.deletes} Extra Items"
            )

    @discord.ui.button(
        label="Confirm Restore",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:confirm_restore",
        row=0,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        decision = await _gate_denial(
            interaction,
            action="restore a server structure backup",
            grandfathered=await _has_any_backup(int(guild.id)),
        )
        if decision is not None:
            return await _deny_with_pro_embed(interaction, decision)
        await _safe_defer_update(interaction)
        await _run_restore(interaction, self)

    @discord.ui.button(
        label="Also Delete Extra Items",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:confirm_delete_toggle",
        row=0,
    )
    async def delete_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        view = RestoreConfirmView(
            self.backup, mode=self.mode, plan=self.plan, delete_armed=not self.delete_armed
        )
        await _edit(
            interaction,
            embed=_confirm_embed(
                self.backup, mode=self.mode, plan=self.plan, delete_armed=view.delete_armed
            ),
            view=view,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:confirm_cancel",
        row=0,
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=_safe_int(self.backup.get("backup_id")))


class RestoreProgressView(discord.ui.View):
    def __init__(self, run: _RestoreRunState, backup: Mapping[str, Any]) -> None:
        super().__init__(timeout=None)
        self.run = run
        self.backup = dict(backup)

    @discord.ui.button(
        label="Stop Restore",
        emoji="⏹️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:progress_stop",
        row=0,
    )
    async def stop_restore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        self.run.cancel_requested = True
        button.disabled = True
        await _safe_defer_update(interaction)
        await _edit(
            interaction,
            embed=_progress_embed(
                self.backup, done=self.run.done, total=self.run.total, stopping=True
            ),
            view=self,
        )


class RestoreResultView(discord.ui.View):
    def __init__(self, backup: Mapping[str, Any]) -> None:
        super().__init__(timeout=900)
        self.backup = dict(backup)

    @discord.ui.button(
        label="Back to Backups",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_server_backups:result_back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        await _render_panel(interaction, selected_id=_safe_int(self.backup.get("backup_id")))

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_server_backups:result_close",
        row=0,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _safe_defer_update(interaction)
        await _edit(interaction, embed=_closed_embed(), view=None)


async def _run_restore(interaction: discord.Interaction, confirm_view: RestoreConfirmView) -> None:
    guild = interaction.guild
    if guild is None:
        return
    backup = confirm_view.backup
    plan = confirm_view.plan
    counts = _plan_counts(plan)
    total = counts.creates + counts.updates
    run = _RestoreRunState(total=total)
    progress_view = RestoreProgressView(run, backup)
    await _edit(
        interaction,
        embed=_progress_embed(backup, done=0, total=total),
        view=progress_view,
    )

    async def _progress(done: int, total_items: int) -> None:
        run.done = int(done)
        run.total = int(total_items)
        if done == total_items or done % 5 == 0:
            try:
                await interaction.edit_original_response(
                    embed=_progress_embed(
                        backup, done=run.done, total=run.total,
                        stopping=run.cancel_requested,
                    ),
                    view=progress_view,
                )
            except Exception:
                pass

    try:
        result = await restore_engine.apply_restore_plan(
            guild,
            plan,
            user_id=int(interaction.user.id),
            backup_id=_safe_int(backup.get("backup_id")),
            progress=_progress,
            delete_confirmed=confirm_view.delete_armed,
            plan_mode=confirm_view.mode,
            cancel_check=lambda: run.cancel_requested,
        )
    except Exception as exc:
        logger.warning("restore apply crashed", exc_info=True)
        result = restore_engine.RestoreResult(
            ok=False,
            failure=(
                "The restore stopped unexpectedly "
                f"(`{type(exc).__name__}: {str(exc)[:200]}`). Changes already "
                "applied stay in place; rebuild the plan to see what remains."
            ),
        )
    cancelled = bool(run.cancel_requested) and not bool(getattr(result, "ok", False))
    progress_view.stop()
    await _edit(
        interaction,
        embed=_result_embed(backup, result, cancelled=cancelled),
        view=RestoreResultView(backup),
    )


__all__ = [
    "PREMIUM_FEATURE",
    "BackupSelect",
    "CreateBackupModal",
    "DeleteBackupConfirmView",
    "PreflightFailedView",
    "RestoreConfirmView",
    "RestoreModeView",
    "RestorePlanView",
    "RestoreProgressView",
    "RestoreResultView",
    "ServerBackupsView",
    "open_server_backups",
]
