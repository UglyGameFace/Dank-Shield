from __future__ import annotations

"""Consolidated public Dank Design Studio.

This module is the one public workflow owner for /dank design. The historical
Studio module remains a compatibility backend for mature item editors and saved
rule helpers, but it no longer owns the public home, preview apply path, or
navigation back to the Studio.
"""

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import discord

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.services import server_design_plan_service as plans

studio = legacy.studio
_PATCHED = False
_COMPATIBILITY_BRIDGE_INSTALLED = False


def _safe_str(value: Any, default: str = "") -> str:
    return legacy._safe_str(value, default)  # type: ignore[attr-defined]


def _safe_int(value: Any, default: int = 0) -> int:
    return legacy._safe_int(value, default)  # type: ignore[attr-defined]


_require_design_permission = legacy._require_design_permission  # type: ignore[attr-defined]
_load_design_options = legacy._load_design_options  # type: ignore[attr-defined]


def _rule_counts(options: Mapping[str, Any]) -> dict[str, int]:
    return legacy._lock_count(options)  # type: ignore[attr-defined]


def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    options = options or {}
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    counts = _rule_counts(options)
    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description=(
            "Pick **one job** below. The home screen never changes a Discord name.\n\n"
            "**Server-wide design, separator changes, Smart Repair, and Custom Format always use:** "
            "Choose → Preview → **Apply Reviewed Changes**.\n"
            "**Only one action is immediate:** Edit One Category / Channel → **Rename**. "
            "That item screen clearly says it applies immediately."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current server design settings",
        value=(
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\n"
            f"Strength: **{_safe_int(options.get('strength'), 4)}/5**\n"
            f"Global rule: **{'On' if counts.get('global') else 'Off'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Saved narrow rules",
        value=(
            f"Categories: **{counts.get('categories', 0)}**\n"
            f"Channels: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**\n"
            f"Protection overrides: **{counts.get('protection_items', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Choose what you want to do",
        value=(
            "🌐 **Design Entire Server** — choose the reusable theme/strength, then preview exact names.\n"
            "✏️ **Edit One Category / Channel** — rename or style one exact item.\n"
            "🩺 **Fix Inconsistent Names** — scan first, then build a safe Smart Repair preview.\n"
            "🔐 **Saved Rules & Protection** — manage what future previews enforce; this does not rename anything by itself.\n"
            "↩️ **Undo Last Apply** — review and restore the previous names from the latest applied batch."
        ),
        inline=False,
    )
    embed.add_field(
        name="What Dank Design never redesigns",
        value="Permissions, roles, topics, channel order, ticket placement, slowmode, NSFW settings, verification, or category placement.",
        inline=False,
    )
    embed.set_footer(text="Names only • Preview before batch changes • Narrow saved rules always win")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


async def _go_home(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    options = await _load_design_options(int(guild.id))
    await interaction.response.edit_message(embed=_home_embed(guild, options), view=DesignHomeView(options))


async def _store_preview(
    interaction: discord.Interaction,
    items: list[dict[str, Any]],
    options: Mapping[str, Any],
    *,
    mode: str,
    title: str,
) -> None:
    guild = interaction.guild
    assert guild is not None
    created_at = legacy._store_pending(  # type: ignore[attr-defined]
        int(guild.id),
        int(interaction.user.id),
        {"items": list(items), "options": dict(options), "mode": mode},
    )
    has_blockers = any(item.get("status") == "failed" for item in items)
    has_changes = any(item.get("status") == "changed" for item in items)
    await interaction.edit_original_response(
        embed=legacy._preview_embed(guild, items, title=title),  # type: ignore[attr-defined]
        view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
    )


class DesignServerThemeSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        choices: list[discord.SelectOption] = []
        for theme in studio.THEMES[:25]:
            font = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
            frame = str(getattr(theme, "category_frame", "plain") or "plain").replace("_", " ").title()
            choices.append(
                discord.SelectOption(
                    label=theme.label[:100],
                    value=theme.id,
                    default=theme.id == current,
                    description=f"Font: {font} • Categories: {frame}"[:100],
                )
            )
        super().__init__(placeholder="1) Choose the server theme", min_values=1, max_values=1, options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        options["theme_id"] = self.values[0]
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))


class DesignServerStrengthSelect(discord.ui.Select):
    LABELS = {
        1: ("1 — Icons only", "Icon/base cleanup only."),
        2: ("2 — Layout", "Adds the selected channel separator."),
        3: ("3 — Font + layout", "Adds the selected font style."),
        4: ("4 — Full theme (recommended)", "Adds category frames where applicable."),
        5: ("5 — Exact normalization", "Strictly normalizes the complete selected theme."),
    }

    def __init__(self, current: int) -> None:
        choices = [
            discord.SelectOption(label=label, value=str(value), default=value == current, description=description)
            for value, (label, description) in self.LABELS.items()
        ]
        super().__init__(placeholder="2) Choose how much styling to use", min_values=1, max_values=1, options=choices, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        options["strength"] = max(1, min(5, _safe_int(self.values[0], 4)))
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))


def _design_server_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    embed = discord.Embed(
        title="🌐 Design Entire Server",
        description=(
            "Choose the reusable server design. Theme and strength are **saved as settings immediately**, "
            "but they do **not** rename a single Discord channel/category.\n\n"
            "When the settings look right, press **Preview Server Changes**, review the exact names, then Apply."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Theme", value=f"**{getattr(theme, 'label', 'Gothic Clean')}**", inline=True)
    embed.add_field(name="Strength", value=f"**{_safe_int(options.get('strength'), 4)}/5**", inline=True)
    embed.add_field(
        name="Batch safety",
        value="A batch Apply is preflighted as a whole. If the preview became stale, **nothing is renamed** and you must preview again.",
        inline=False,
    )
    embed.set_footer(text="Choose settings → Preview Server Changes → Apply Reviewed Changes")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class DesignServerView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(timeout=900)
        self.add_item(DesignServerThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(DesignServerStrengthSelect(_safe_int(options.get("strength"), 4)))

    @discord.ui.button(label="Preview Server Changes", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server_preview", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await _store_preview(interaction, items, plan_options, mode="preview_server_v2", title="👁️ Server Design Preview")

    @discord.ui.button(label="Change Separators Only", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:separator_only", row=2)
    async def separator_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        _analysis, repair_options, _summary = legacy._infer_live_majority_context(guild, options)  # type: ignore[attr-defined]
        current = _safe_str(repair_options.get("separator_id"), "none")
        selected = "bar_heavy" if current == "none" else current
        await interaction.response.edit_message(
            embed=legacy._style_change_embed(guild, options, separator_id=selected),  # type: ignore[attr-defined]
            view=legacy.StyleChangeView(separator_id=selected),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:server_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _edit_one_embed() -> discord.Embed:
    embed = discord.Embed(
        title="✏️ Edit One Category / Channel",
        description=(
            "Choose **Category** or **Channel**, then pick the exact item.\n\n"
            "On the item screen, **Rename** is the only immediate name change. "
            "**Preview Fixes** and **Custom Format** always show a preview and require **Apply Reviewed Changes**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Rule priority",
        value="Exact name → Channel rule → Category rule → Global rule → Server design. Narrow rules never get silently replaced by broader ones.",
        inline=False,
    )
    return embed


class EditOneItemView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Category", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_category", row=0)
    async def category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=legacy._category_editor_embed(guild, page=0),
            view=legacy.CategoryEditorPickerView(guild, page=0),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_channel", row=0)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=legacy._channel_editor_embed(guild, page=0),
            view=legacy.ChannelEditorPickerView(guild, page=0),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:edit_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _review_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🩺 Fix Inconsistent Names",
        description=(
            "**Scan Saved Design** is read-only and tells you what disagrees with your saved rules.\n"
            "**Build Smart Repair Preview** learns the established style inside each category, keeps saved narrow rules authoritative, "
            "and blocks Apply when confidence is not high enough."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Scan first • Preview second • Nothing is renamed until Apply Reviewed Changes")
    return embed


class ReviewRepairView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Scan Saved Design", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:doctor", row=0)
    async def doctor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await interaction.edit_original_response(
            embed=legacy._doctor_embed(guild, plan_options, items),  # type: ignore[attr-defined]
            view=ReviewRepairView(),
        )

    @discord.ui.button(label="Build Smart Repair Preview", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design_v2:drift", row=0)
    async def drift(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_drift_repair_plan(guild, options)
        created_at = legacy._store_pending(  # type: ignore[attr-defined]
            int(guild.id),
            int(interaction.user.id),
            {"items": items, "options": dict(plan_options), "mode": "consistency_check_v2"},
        )
        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)
        await interaction.edit_original_response(
            embed=legacy._consistency_embed(guild, items, plan_options),  # type: ignore[attr-defined]
            view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:review_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _saved_rules_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _rule_counts(options)
    embed = discord.Embed(
        title="🔐 Saved Rules & Protection",
        description=(
            "These settings control what **future previews** enforce. Saving, unlocking, or changing a rule here does **not** rename a Discord item by itself."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current rules",
        value=(
            f"Global: **{'On' if counts.get('global') else 'Off'}**\n"
            f"Category: **{counts.get('categories', 0)}**\n"
            f"Channel: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**\n"
            f"Exact protection: **{counts.get('protection_items', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Which tool does what",
        value=(
            "**Layout Rules** = global/category/channel visual rules.\n"
            "**Unlock / Clean** = remove one saved rule or stale rule.\n"
            "**Protection** = decide which exact/default names automated styling may touch."
        ),
        inline=False,
    )
    embed.set_footer(text="Narrower rules always win • Protection is evaluated separately • No rename happens on this screen")
    return embed


class SavedRulesView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Layout Rules", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:layout_rules", row=0)
    async def layout_rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=legacy._format_locks_embed(guild, options),
            view=legacy.FormatLocksView(),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Unlock / Clean", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:unlock", row=0)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=legacy._format_lock_manager_embed(guild, options, page=0),
            view=legacy.LockManagerView(guild, options, page=0),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:protection", row=1)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=legacy._protection_manager_embed(guild, options),
            view=legacy.ProtectionManagerView(),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:rules_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class DesignHomeView(discord.ui.View):
    """Five explicit workflows. No style controls are mixed into the home."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Design Entire Server", emoji="🌐", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server", row=0)
    async def design_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))

    @discord.ui.button(label="Edit One Category / Channel", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit", row=0)
    async def edit_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_edit_one_embed(), view=EditOneItemView())

    @discord.ui.button(label="Fix Inconsistent Names", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:review", row=1)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_review_embed(), view=ReviewRepairView())

    @discord.ui.button(label="Saved Rules & Protection", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_saved_rules_embed(guild, options), view=SavedRulesView())

    @discord.ui.button(label="Undo Last Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:rollback", row=2)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await legacy._open_rollback(interaction)  # type: ignore[attr-defined]


async def _fresh_channel_map(guild: discord.Guild) -> dict[int, Any]:
    """Fetch one fresh guild-channel snapshot for apply preflight when possible."""

    fetch_all = getattr(guild, "fetch_channels", None)
    if callable(fetch_all):
        try:
            rows = await fetch_all()
            return {
                int(getattr(channel, "id", 0)): channel
                for channel in list(rows or [])
                if _safe_int(getattr(channel, "id", 0), 0) > 0
            }
        except Exception:
            pass

    out: dict[int, Any] = {}
    for channel in list(getattr(guild, "channels", []) or []) + list(getattr(guild, "categories", []) or []):
        channel_id = _safe_int(getattr(channel, "id", 0), 0)
        if channel_id > 0:
            out[channel_id] = channel
    return out


async def _preflight_apply(
    guild: discord.Guild,
    items: list[dict[str, Any]],
) -> tuple[list[tuple[Any, dict[str, Any], str, str]], int, list[str]]:
    """Validate the complete changed set before the first Discord rename."""

    fresh = await _fresh_channel_map(guild)
    ready: list[tuple[Any, dict[str, Any], str, str]] = []
    skipped = 0
    errors: list[str] = []

    for item in items:
        if item.get("status") != "changed":
            skipped += 1
            continue

        channel_id = _safe_int(item.get("channel_id"), 0)
        channel = fresh.get(channel_id) or guild.get_channel(channel_id)
        before = _safe_str(item.get("before"))
        after = _safe_str(item.get("after"))

        if channel is None:
            errors.append(f"Missing item that was previewed as `{before or channel_id}`.")
            continue
        if not before:
            errors.append(f"Preview row `{channel_id}` has no original name.")
            continue
        if not after:
            errors.append(f"`{before}` would become a blank name.")
            continue
        if len(after) > studio.DISCORD_NAME_LIMIT:
            errors.append(f"`{before}` now exceeds Discord's name limit.")
            continue

        current = _safe_str(getattr(channel, "name", ""))
        if current != before:
            errors.append(f"`{before}` is now `{current}`.")
            continue

        ready.append((channel, dict(item), before, after))

    return ready, skipped, errors


async def _persist_snapshot_rows(guild_id: int, user_id: int, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    snapshot_payload = {"created_at": time.time(), "items": list(rows), "admin_id": str(int(user_id))}
    guild_key = legacy._guild_key(int(guild_id))  # type: ignore[attr-defined]
    legacy._LAST_SNAPSHOTS.setdefault(guild_key, []).append(snapshot_payload)  # type: ignore[attr-defined]
    legacy._LAST_SNAPSHOTS[guild_key] = legacy._LAST_SNAPSHOTS[guild_key][-10:]  # type: ignore[attr-defined]
    await legacy._persist_rollback_snapshot(int(guild_id), snapshot_payload)  # type: ignore[attr-defined]


async def _rollback_attempt(
    applied: list[tuple[Any, dict[str, Any], str, str]],
    *,
    user_id: int,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    """Best-effort rollback of only the rows changed by the current failed apply."""

    restored = 0
    residual: list[dict[str, Any]] = []
    failures: list[str] = []
    for channel, item, before, after in reversed(applied):
        current = _safe_str(getattr(channel, "name", ""))
        if current and current != after:
            residual.append({**item, "old_name": before, "new_name": after, "admin_id": str(int(user_id)), "timestamp": time.time(), "action_type": "apply"})
            failures.append(f"`{after}` changed again before automatic rollback.")
            continue
        try:
            await channel.edit(name=before, reason=f"Dank Shield automatic rollback after failed design apply by {int(user_id)}")
            restored += 1
            await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
        except Exception as exc:
            residual.append({**item, "old_name": before, "new_name": after, "admin_id": str(int(user_id)), "timestamp": time.time(), "action_type": "apply"})
            failures.append(f"Could not restore `{after}`: {type(exc).__name__}")
    return restored, residual, failures


class DoneView(discord.ui.View):
    def __init__(self, *, can_rollback: bool) -> None:
        super().__init__(timeout=900)
        self.rollback.disabled = not can_rollback

    @discord.ui.button(label="Undo This Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:done_rollback", row=0)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await legacy._open_rollback(interaction)  # type: ignore[attr-defined]

    @discord.ui.button(label="Back to Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:done_home", row=0)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class ReviewedPreviewView(discord.ui.View):
    """One transactional preview/apply owner for every active Studio batch flow."""

    def __init__(self, *, can_apply: bool, pending_created_at: float | None = None) -> None:
        super().__init__(timeout=900)
        self.pending_created_at = pending_created_at
        self.apply.disabled = not can_apply

    @discord.ui.button(label="Apply Reviewed Changes", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_design_v2:apply", row=0)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = legacy._key(int(guild.id), int(interaction.user.id))  # type: ignore[attr-defined]
        payload = legacy._PENDING.get(key) or {}  # type: ignore[attr-defined]

        if not legacy._pending_matches(payload, self.pending_created_at):  # type: ignore[attr-defined]
            await interaction.response.send_message("❌ This preview is obsolete. Build a fresh preview before applying.", ephemeral=True)
            return

        items = list(payload.get("items") or [])
        if not items:
            await interaction.response.send_message("❌ No reviewed preview is available. Build the preview again.", ephemeral=True)
            return
        if any(item.get("status") == "failed" for item in items):
            await interaction.response.send_message("❌ This preview has blockers. Fix them and preview again.", ephemeral=True)
            return

        lock = legacy._lock_for(int(guild.id))  # type: ignore[attr-defined]
        if lock.locked():
            await interaction.response.send_message("⏳ A design Apply is already running for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        mode = _safe_str(payload.get("mode"), "preview")

        async with lock:
            ready, skipped, preflight_errors = await _preflight_apply(guild, items)
            if preflight_errors:
                legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                embed = discord.Embed(
                    title="❌ Preview Changed Before Apply",
                    description=(
                        "**No names were changed.** The complete batch was checked before the first rename and at least one preview row is no longer current. "
                        "Build a fresh preview instead of applying stale assumptions."
                    ),
                    color=discord.Color.orange(),
                )
                embed.add_field(name="What changed", value="\n".join(f"• {line}" for line in preflight_errors[:8])[:1024], inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=False))
                return

            applied: list[tuple[Any, dict[str, Any], str, str]] = []
            failure = ""
            for channel, item, before, after in ready:
                # Recheck immediately before each edit too. If an admin races the
                # apply after preflight, stop and roll back this attempt.
                current = _safe_str(getattr(channel, "name", ""))
                if current != before:
                    failure = f"`{before}` changed to `{current}` while Apply was running."
                    break
                try:
                    await channel.edit(name=after, reason=f"Dank Shield reviewed Server Design apply by {int(interaction.user.id)}")
                    applied.append((channel, item, before, after))
                    if len(applied) % 5 == 0:
                        await interaction.edit_original_response(content=f"🚀 Applying reviewed design… {len(applied)}/{len(ready)} changed.")
                    await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
                except Exception as exc:
                    failure = f"Discord rejected `{before}` → `{after}`: {type(exc).__name__}."
                    break

            if failure:
                restored, residual, rollback_failures = await _rollback_attempt(applied, user_id=int(interaction.user.id))
                legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                if residual:
                    await _persist_snapshot_rows(int(guild.id), int(interaction.user.id), residual)
                embed = discord.Embed(
                    title="⚠️ Apply Stopped Safely",
                    description=(
                        f"Apply stopped at the first unexpected problem. {failure}\n\n"
                        + (
                            f"Automatically restored **{restored}** earlier rename(s). **{len(residual)}** item(s) still need Undo Last Apply."
                            if residual
                            else f"Automatically restored **{restored}** earlier rename(s). **No partial design was left behind.**"
                        )
                    ),
                    color=discord.Color.orange(),
                )
                if rollback_failures:
                    embed.add_field(name="Rollback attention", value="\n".join(f"• {line}" for line in rollback_failures[:8])[:1024], inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(residual)))
                return

            snapshot_rows = [
                {**item, "old_name": before, "new_name": after, "admin_id": str(int(interaction.user.id)), "timestamp": time.time(), "action_type": "apply"}
                for _channel, item, before, after in applied
            ]
            if snapshot_rows:
                await _persist_snapshot_rows(int(guild.id), int(interaction.user.id), snapshot_rows)
            legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]

        title = "✅ Inconsistent Names Repaired" if "consistency" in mode else "✅ Reviewed Design Applied"
        embed = discord.Embed(
            title=title,
            description=f"Changed **{len(applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**.",
            color=discord.Color.green(),
        )
        if snapshot_rows:
            embed.add_field(name="Undo ready", value="The previous names were saved before this Apply.", inline=False)
        await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(snapshot_rows)))

    @discord.ui.button(label="Back to Studio", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:preview_back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class LegacyStyleChangePreviewView(ReviewedPreviewView):
    """Keep separator issue-repair buttons while using the one safe Apply owner."""

    def __init__(self, *, can_apply: bool, has_blockers: bool = False, pending_created_at: float) -> None:
        super().__init__(can_apply=can_apply, pending_created_at=pending_created_at)
        if has_blockers:
            self.add_item(legacy.StyleChangeFixMissingEmojiButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]
            self.add_item(legacy.StyleChangeApplySafeOnlyButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]


def _install_legacy_compatibility_bridge() -> None:
    """Route mature legacy sub-editors back through the consolidated owner.

    This is intentionally tiny and explicit. It does not replace plan builders,
    doctor logic, registrars, or design services. It only prevents legacy nested
    screens from escaping to the old mixed home/apply components while those
    mature editors are still reused.
    """

    global _COMPATIBILITY_BRIDGE_INSTALLED
    if _COMPATIBILITY_BRIDGE_INSTALLED:
        return
    legacy._home_embed = _home_embed  # type: ignore[attr-defined]
    legacy.DesignHomeView = DesignHomeView  # type: ignore[attr-defined]
    legacy.DesignPreviewView = ReviewedPreviewView  # type: ignore[attr-defined]
    legacy.StyleChangePreviewView = LegacyStyleChangePreviewView  # type: ignore[attr-defined]
    _COMPATIBILITY_BRIDGE_INSTALLED = True


_install_legacy_compatibility_bridge()


async def open_design_studio(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    options = await _load_design_options(int(guild.id))
    await interaction.response.send_message(
        embed=_home_embed(guild, options),
        view=DesignHomeView(options),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_public_design_studio_command(bot: Any = None, tree: Any = None) -> bool:
    """Compatibility registrar; normal ownership is public_design_group."""

    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext
        from stoney_verify.commands_ext.public_setup_group import dank_group

        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
        allowed.add("design")
        commands_ext._ALLOWED_DANK_CHILDREN = allowed
        if dank_group.get_command("design") is None:
            @dank_group.command(name="design", description="Open Dank Design Studio for safe channel/category name styling.")
            async def dank_design(interaction: discord.Interaction) -> None:
                await open_design_studio(interaction)
        _PATCHED = True
        return True
    except Exception:
        return False


__all__ = [
    "DesignHomeView",
    "DesignServerView",
    "EditOneItemView",
    "LegacyStyleChangePreviewView",
    "ReviewedPreviewView",
    "ReviewRepairView",
    "SavedRulesView",
    "_home_embed",
    "_install_legacy_compatibility_bridge",
    "_load_design_options",
    "_preflight_apply",
    "_require_design_permission",
    "open_design_studio",
    "register_public_design_studio_command",
]
