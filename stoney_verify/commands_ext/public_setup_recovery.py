from __future__ import annotations

"""Safe recovery actions for the public ``/dank setup`` flow.

Recovery has two deliberately different reset levels:

- Safe Start Over clears Dank Shield's saved setup plan, feature switches,
  completion state, saved mappings, and ticket choices. It never deletes
  Discord roles, channels, messages, tickets, or members.
- Clear Saved Roles & Channels clears only saved Discord mappings. It preserves
  the selected setup plan and enabled feature switches.

Every destructive Discord-object cleanup remains owned by
``public_setup_cleanup`` and requires its own preview/confirmation flow.
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from ..globals import get_supabase, now_utc
from ..guild_config import (
    GUILD_CONFIG_TABLE_FALLBACKS,
    invalidate_guild_config,
)
from . import public_setup_solid as solid

_PATCHED = False

# Saved Discord mappings / operational values. Clearing only these lets an
# owner remap the server without changing which features were selected.
MAPPING_CONFIG_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "vc_verify_channel_id",
    "vc_verify_queue_channel_id",
    "ticket_panel_channel_id",
    "support_channel_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "transcripts_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
    "force_verify_log_channel_id",
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "member_role_id",
    "staff_role_id",
    "vc_staff_role_id",
    "server_control_role_id",
    "control_role_id",
    "bot_manager_role_id",
    "ticket_prefix",
)

# Canonical Quick Setup/service/completion state. Safe Start Over must clear
# these too; otherwise Setup Home can still believe an old plan is active.
SETUP_STATE_KEYS: tuple[str, ...] = (
    "setup_choice",
    "setup_choice_label",
    "setup_choice_description",
    "setup_choice_member_sees",
    "setup_template_version",
    "setup_choice_selected_at",
    "setup_choice_selected_by_id",
    "setup_choice_selected_by_name",
    "setup_service_mode_saved_at",
    "tickets_enabled",
    "ticket_service_enabled",
    "ticketing_enabled",
    "verification_enabled",
    "basic_verify_enabled",
    "basic_button_verify_enabled",
    "voice_verification_enabled",
    "vc_verify_enabled",
    "voice_verify_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
    "logs_enabled",
    "id_verify_enabled",
    "web_verify_enabled",
    "id_web_verify_enabled",
    "verification_requires_id",
    "verification_allows_voice",
    "verification_panel_style",
    "verification_mode",
    "verify_mode",
    "verification_style_label",
    "ticket_flow_mode",
    "ticket_flow_style",
    "ticket_form_required",
    "ticket_form_mode",
    "ticket_open_requires_modal",
    "ticket_open_requires_form",
    "ticket_types_enabled",
    "setup_completed",
    "setup_completed_at",
    "setup_completed_by_id",
    "setup_completed_by_name",
    "setup_completion_invalidated_at",
    "setup_completion_invalidated_reason",
    "stoney_baloney_style_enabled",
)

# Snapshots include both groups so Restore Last Reset can reverse either reset.
CONFIG_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(MAPPING_CONFIG_KEYS + SETUP_STATE_KEYS)
)

CATEGORY_RESTORE_KEYS: tuple[str, ...] = (
    "guild_id",
    "slug",
    "name",
    "description",
    "intake_type",
    "match_keywords",
    "is_default",
    "sort_order",
)


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _merge_settings(row: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = _mapping(row)
    merged: dict[str, Any] = {}
    for key in ("settings", "config", "metadata", "meta"):
        nested = _mapping(raw.get(key))
        if nested:
            merged.update(nested)
    for key, value in raw.items():
        if key in {"settings", "config", "metadata", "meta"}:
            continue
        if value is not None:
            merged[key] = value
    return merged


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _safe_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    safe: list[str] = []
    last_dash = False
    for char in text:
        if char.isalnum():
            safe.append(char)
            last_dash = False
        elif not last_dash:
            safe.append("-")
            last_dash = True
    return "".join(safe).strip("-")[:80]


def _category_count_text(count: int) -> str:
    return f"{count} ticket choice" + ("" if count == 1 else "s")


def _config_count_text(count: int) -> str:
    return f"{count} saved setup value" + ("" if count == 1 else "s")


def _supabase_required() -> Any:
    supabase = get_supabase()
    if supabase is None:
        raise RuntimeError("Supabase is not available.")
    return supabase


def _fetch_config_row_sync(
    guild_id: int,
) -> tuple[str, Optional[dict[str, Any]], str]:
    supabase = _supabase_required()
    last_error = ""
    for table in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            response = (
                supabase.table(table)
                .select("*")
                .eq("guild_id", str(guild_id))
                .limit(1)
                .execute()
            )
            rows = getattr(response, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return table, dict(rows[0]), ""
            return table, None, ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
    fallback = (
        GUILD_CONFIG_TABLE_FALLBACKS[0]
        if GUILD_CONFIG_TABLE_FALLBACKS
        else "guild_configs"
    )
    return str(fallback), None, last_error


def _fetch_ticket_categories_sync(
    guild_id: int,
) -> tuple[list[dict[str, Any]], str]:
    supabase = _supabase_required()
    try:
        response = (
            supabase.table("ticket_categories")
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(response, "data", None) or []
        clean: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            item["guild_id"] = str(guild_id)
            clean.append(item)
        clean.sort(
            key=lambda row: (
                row.get("sort_order") is None,
                row.get("sort_order") or 999999,
                str(row.get("slug") or ""),
            )
        )
        return clean, ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:250]}"


def _clean_category_for_restore(
    row: Mapping[str, Any],
    guild_id: int,
) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in CATEGORY_RESTORE_KEYS:
        if key in row:
            clean[key] = row.get(key)

    clean["guild_id"] = str(guild_id)
    slug = _safe_slug(clean.get("slug"))
    if not slug:
        slug = _safe_slug(clean.get("name")) or "restored"
    clean["slug"] = slug
    clean["name"] = str(clean.get("name") or slug).strip()[:120]
    clean["description"] = str(clean.get("description") or "")[:500]
    clean["intake_type"] = str(
        clean.get("intake_type") or "custom"
    ).strip().lower()[:40]

    keywords = clean.get("match_keywords")
    if not isinstance(keywords, list):
        keywords = []
    clean["match_keywords"] = [
        str(item).strip()[:50]
        for item in keywords
        if str(item).strip()
    ][:20]

    try:
        clean["sort_order"] = int(clean.get("sort_order") or 0) or None
    except Exception:
        clean["sort_order"] = None
    clean["is_default"] = bool(clean.get("is_default"))
    return clean


def _current_snapshot_sync(
    guild_id: int,
    user: discord.abc.User,
) -> dict[str, Any]:
    table, row, row_error = _fetch_config_row_sync(guild_id)
    settings = _merge_settings(row)
    categories, category_error = _fetch_ticket_categories_sync(guild_id)
    config_values = {
        key: settings.get(key)
        for key in CONFIG_KEYS
        if settings.get(key) is not None
    }
    return {
        "created_at": _now_iso(),
        "guild_id": str(guild_id),
        "by_id": str(getattr(user, "id", "")),
        "by_name": str(user),
        "table": table,
        "config_error": row_error,
        "category_error": category_error,
        "config": config_values,
        "ticket_categories": [
            _clean_category_for_restore(category, guild_id)
            for category in categories
        ],
    }


def _write_config_patch_sync(
    guild_id: int,
    patch: dict[str, Any],
    snapshot: Optional[dict[str, Any]] = None,
) -> str:
    supabase = _supabase_required()
    table, row, row_error = _fetch_config_row_sync(guild_id)
    if row_error and row is None:
        raise RuntimeError(row_error)

    settings = _merge_settings(row)
    for key, value in patch.items():
        settings[key] = value
    if snapshot is not None:
        settings["last_setup_snapshot"] = snapshot
    settings["setup_recovery_updated_at"] = _now_iso()

    base = {
        "guild_id": str(guild_id),
        "updated_at": _now_iso(),
    }
    payloads = (
        {**base, **patch, "settings": settings},
        {**base, **patch},
        {**base, "settings": settings},
        {**base, "config": settings},
    )
    last_error = ""

    for payload in payloads:
        try:
            if row:
                (
                    supabase.table(table)
                    .update(payload)
                    .eq("guild_id", str(guild_id))
                    .execute()
                )
            else:
                try:
                    (
                        supabase.table(table)
                        .upsert(payload, on_conflict="guild_id")
                        .execute()
                    )
                except TypeError:
                    supabase.table(table).upsert(payload).execute()
            return table
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:250]}"

    raise RuntimeError(last_error or "Could not update guild setup config.")


def _delete_ticket_categories_sync(guild_id: int) -> tuple[int, str]:
    supabase = _supabase_required()
    rows, read_error = _fetch_ticket_categories_sync(guild_id)
    if read_error:
        return 0, read_error
    try:
        (
            supabase.table("ticket_categories")
            .delete()
            .eq("guild_id", str(guild_id))
            .execute()
        )
        return len(rows), ""
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {str(exc)[:250]}"


def _restore_categories_sync(
    guild_id: int,
    rows: list[dict[str, Any]],
) -> tuple[int, str]:
    supabase = _supabase_required()
    try:
        (
            supabase.table("ticket_categories")
            .delete()
            .eq("guild_id", str(guild_id))
            .execute()
        )
    except Exception as exc:
        return (
            0,
            "Could not clear current ticket choices: "
            f"{type(exc).__name__}: {str(exc)[:220]}",
        )

    clean_rows = [
        _clean_category_for_restore(row, guild_id)
        for row in rows
        if isinstance(row, Mapping)
    ]
    if not clean_rows:
        return 0, ""

    try:
        supabase.table("ticket_categories").insert(clean_rows).execute()
        return len(clean_rows), ""
    except Exception as exc:
        return (
            0,
            "Could not restore ticket choices: "
            f"{type(exc).__name__}: {str(exc)[:220]}",
        )


def _reset_keys(*, include_menu: bool) -> tuple[str, ...]:
    """Return the exact keys cleared by the requested recovery level."""

    return CONFIG_KEYS if include_menu else MAPPING_CONFIG_KEYS


async def _reset_saved_setup(
    guild: discord.Guild,
    user: discord.abc.User,
    *,
    include_menu: bool,
) -> tuple[str, bool]:
    guild_id = int(guild.id)
    snapshot = await asyncio.to_thread(
        _current_snapshot_sync,
        guild_id,
        user,
    )

    patch = {
        key: None
        for key in _reset_keys(include_menu=include_menu)
    }
    patch.update(
        {
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
            "setup_reset_at": _now_iso(),
            "setup_reset_by_id": str(getattr(user, "id", "")),
            "setup_reset_by_name": str(user),
        }
    )

    await asyncio.to_thread(
        _write_config_patch_sync,
        guild_id,
        patch,
        snapshot,
    )

    menu_text = ""
    ok = True
    if include_menu:
        deleted, error = await asyncio.to_thread(
            _delete_ticket_categories_sync,
            guild_id,
        )
        if error:
            ok = False
            menu_text = (
                "\n⚠️ Ticket choices were not cleared: "
                f"`{error}`"
            )
        else:
            menu_text = f"\n✅ Cleared {_category_count_text(deleted)}."

    invalidate_guild_config(guild_id)

    if include_menu:
        result = (
            "✅ Cleared the saved Quick Setup plan, feature selections, "
            "completion state, and saved role/channel mappings.\n"
            "✅ Emergency restore snapshot saved."
        )
    else:
        result = (
            "✅ Cleared saved role/channel mappings while keeping the current "
            "setup plan and feature selections.\n"
            "✅ Emergency restore snapshot saved."
        )
    return result + menu_text, ok


async def _reset_ticket_menu_only(
    guild: discord.Guild,
    user: discord.abc.User,
) -> tuple[str, bool]:
    guild_id = int(guild.id)
    snapshot = await asyncio.to_thread(
        _current_snapshot_sync,
        guild_id,
        user,
    )
    await asyncio.to_thread(
        _write_config_patch_sync,
        guild_id,
        {"setup_menu_reset_at": _now_iso()},
        snapshot,
    )
    deleted, error = await asyncio.to_thread(
        _delete_ticket_categories_sync,
        guild_id,
    )
    if error:
        return f"🚫 Ticket choice reset failed: `{error}`", False
    return (
        f"✅ Cleared {_category_count_text(deleted)}.\n"
        "✅ Emergency restore snapshot saved.",
        True,
    )


async def _restore_last_reset(guild: discord.Guild) -> tuple[str, bool]:
    guild_id = int(guild.id)

    def _restore_sync() -> tuple[str, bool]:
        table, row, error = _fetch_config_row_sync(guild_id)
        if error and row is None:
            return f"🚫 Could not read saved setup: `{error}`", False

        settings = _merge_settings(row)
        snapshot = _mapping(settings.get("last_setup_snapshot"))
        if not snapshot:
            return (
                "🚫 No emergency reset snapshot exists for this server. "
                "Use Backups & History for normal saved versions.",
                False,
            )

        config = _mapping(snapshot.get("config"))
        categories = snapshot.get("ticket_categories")
        if not isinstance(categories, list):
            categories = []

        # Clear every recovery-owned key first, then restore only what existed
        # in the snapshot. This prevents post-reset values from leaking through.
        patch = {key: None for key in CONFIG_KEYS}
        patch.update(
            {
                key: config.get(key)
                for key in CONFIG_KEYS
                if key in config
            }
        )
        patch.update(
            {
                "setup_restored_at": _now_iso(),
                "use_env_fallbacks": False,
                "allow_runtime_discovery": True,
            }
        )
        _write_config_patch_sync(guild_id, patch, snapshot)

        restored_categories, category_error = _restore_categories_sync(
            guild_id,
            categories,
        )
        if category_error:
            return (
                f"⚠️ Restored saved setup values from `{table}`, but ticket "
                f"choices had an issue: `{category_error}`",
                False,
            )

        return (
            "✅ Restored the emergency snapshot created before the most recent "
            "reset.\n"
            f"✅ Restored {_category_count_text(restored_categories)}.",
            True,
        )

    message, ok = await asyncio.to_thread(_restore_sync)
    invalidate_guild_config(guild_id)
    return message, ok


async def _rebuild_recommended_menu(
    guild: discord.Guild,
) -> tuple[str, bool]:
    created, skipped, error = await solid._seed_recommended_categories(guild)
    if error:
        return f"🚫 Default ticket choices could not be rebuilt: `{error}`", False
    if created:
        return (
            "✅ Created default ticket choices: "
            + ", ".join(f"`{item}`" for item in created),
            True,
        )
    return "✅ Default ticket choices already exist. Nothing changed.", True


async def _recovery_snapshot_summary(
    guild: discord.Guild,
) -> tuple[int, int, bool, str]:
    guild_id = int(guild.id)

    def _sync() -> tuple[int, int, bool, str]:
        _table, row, error = _fetch_config_row_sync(guild_id)
        settings = _merge_settings(row)
        config_count = sum(
            1
            for key in CONFIG_KEYS
            if settings.get(key) is not None
        )
        categories, category_error = _fetch_ticket_categories_sync(guild_id)
        snapshot = _mapping(settings.get("last_setup_snapshot"))
        return (
            config_count,
            len(categories),
            bool(snapshot),
            error or category_error,
        )

    return await asyncio.to_thread(_sync)


async def _build_recovery_embed(
    guild: discord.Guild,
    *,
    title: str = "🛟 Repair & Restart Setup",
) -> discord.Embed:
    config_count, category_count, has_snapshot, error = (
        await _recovery_snapshot_summary(guild)
    )
    embed = discord.Embed(
        title=title,
        description=(
            "Use this when the saved setup is wrong or you want to restart "
            "Quick Setup. These recovery actions do **not** delete Discord "
            "roles, channels, messages, tickets, or members."
        ),
        color=discord.Color.gold(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Current Saved State",
        value=(
            f"• {_config_count_text(config_count)} currently saved.\n"
            f"• {_category_count_text(category_count)} currently saved.\n"
            "• Emergency reset snapshot: "
            f"{'available' if has_snapshot else 'not available yet'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Safe Start Over",
        value=(
            "Clears the saved Quick Setup plan, feature selections, mappings, "
            "completion state, and ticket choices. Then `/dank setup` starts "
            "from the beginning. No Discord objects are deleted."
        ),
        inline=False,
    )
    embed.add_field(
        name="Restore Last Reset vs. Backups & History",
        value=(
            "**Restore Last Reset** restores only the emergency snapshot made "
            "immediately before the most recent reset.\n"
            "Use **Backups & History** when you want normal saved versions or "
            "selective recovery."
        ),
        inline=False,
    )
    if error:
        embed.add_field(
            name="Recovery Warning",
            value=_short(error, 900),
            inline=False,
        )
    embed.set_footer(
        text=(
            f"Guild {guild.id} • reset and restore actions require confirmation"
        )
    )
    return embed


class RecoveryButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Repair / Restart",
            emoji="🛟",
            style=discord.ButtonStyle.danger,
            custom_id="stoney_recovery:open",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await solid._safe_defer_update(interaction)
        embed = await _build_recovery_embed(guild)
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=RecoveryCenterView(),
        )


class RecoveryCenterView(solid.BackToSetupView):
    @discord.ui.button(
        label="Safe Start Over",
        emoji="🛟",
        style=discord.ButtonStyle.danger,
        custom_id="stoney_recovery:start_over",
        row=0,
    )
    async def start_over(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(
            ConfirmRecoveryModal(action="start_over")
        )

    @discord.ui.button(
        label="Clear Saved Roles & Channels",
        emoji="🧽",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_recovery:reset_config",
        row=1,
    )
    async def reset_config(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(
            ConfirmRecoveryModal(action="reset_config")
        )

    @discord.ui.button(
        label="Clear Ticket Choices Only",
        emoji="🧾",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_recovery:reset_menu",
        row=1,
    )
    async def reset_menu(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(
            ConfirmRecoveryModal(action="reset_menu")
        )

    @discord.ui.button(
        label="Restore Last Reset",
        emoji="↩️",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_recovery:restore",
        row=2,
    )
    async def restore(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(
            ConfirmRecoveryModal(action="restore")
        )

    @discord.ui.button(
        label="Rebuild Default Ticket Choices",
        emoji="🧱",
        style=discord.ButtonStyle.success,
        custom_id="stoney_recovery:rebuild_menu",
        row=2,
    )
    async def rebuild_menu(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await solid._safe_defer_update(interaction)
        message, ok = await _rebuild_recommended_menu(guild)
        embed = await _build_recovery_embed(
            guild,
            title=(
                "✅ Recovery Action Complete"
                if ok
                else "🚫 Recovery Action Failed"
            ),
        )
        embed.color = discord.Color.green() if ok else discord.Color.red()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        embed.add_field(
            name="Next Step",
            value="Run **Review Setup** to confirm the ticket choices are ready.",
            inline=False,
        )
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=RecoveryCenterView(),
        )


class ConfirmRecoveryModal(discord.ui.Modal):
    def __init__(self, *, action: str) -> None:
        self.action = action
        titles = {
            "start_over": "Confirm Safe Start Over",
            "reset_config": "Clear Saved Roles & Channels",
            "reset_menu": "Clear Ticket Choices",
            "restore": "Restore Last Reset",
        }
        super().__init__(title=titles.get(action, "Confirm Recovery"))

        expected = (
            "START OVER"
            if action == "start_over"
            else "CLEAR"
            if action in {"reset_config", "reset_menu"}
            else "RESTORE"
        )
        self.expected = expected
        self.confirm = discord.ui.TextInput(
            label=f"Type {expected} to continue",
            placeholder=expected,
            min_length=len(expected),
            max_length=20,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        typed = str(self.confirm.value or "").strip().upper()
        if typed != self.expected:
            return await interaction.response.send_message(
                (
                    f"🚫 Cancelled. You typed `{typed or 'nothing'}` "
                    f"instead of `{self.expected}`."
                ),
                ephemeral=True,
            )

        await solid._safe_defer_modal(interaction)
        ok = False

        if self.action == "start_over":
            message, ok = await _reset_saved_setup(
                guild,
                interaction.user,
                include_menu=True,
            )
            next_step = (
                "Run `/dank setup` and choose **Quick Setup**. "
                "It will start from a fresh saved state."
            )
        elif self.action == "reset_config":
            message, ok = await _reset_saved_setup(
                guild,
                interaction.user,
                include_menu=False,
            )
            next_step = (
                "Open `/dank setup` → **Manage Setup** → "
                "**All Features & Settings** → **Setup Plan & Server Items** "
                "to remap roles and channels."
            )
        elif self.action == "reset_menu":
            message, ok = await _reset_ticket_menu_only(
                guild,
                interaction.user,
            )
            next_step = (
                "Open `/dank setup` → **Manage Setup** → "
                "**All Features & Settings** → **Tickets** → "
                "**Ticket Choices**."
            )
        elif self.action == "restore":
            message, ok = await _restore_last_reset(guild)
            next_step = (
                "Run **Review Setup** to confirm the restored setup is usable."
            )
        else:
            message = "Unknown recovery action."
            next_step = "Return to Setup Home and try again."

        embed = await _build_recovery_embed(
            guild,
            title=(
                "✅ Recovery Action Complete"
                if ok
                else "🚫 Recovery Action Failed"
            ),
        )
        embed.color = discord.Color.green() if ok else discord.Color.red()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        embed.add_field(name="Next Step", value=next_step[:1024], inline=False)
        try:
            await interaction.followup.send(
                embed=embed,
                view=RecoveryCenterView(),
                ephemeral=True,
            )
        except Exception:
            await solid._edit_or_followup(
                interaction,
                embed=embed,
                view=RecoveryCenterView(),
            )


def _add_recovery_button(view: discord.ui.View) -> discord.ui.View:
    try:
        if any(
            getattr(child, "custom_id", "") == "stoney_recovery:open"
            for child in getattr(view, "children", []) or []
        ):
            return view
        view.add_item(RecoveryButton())
    except Exception:
        pass
    return view


async def open_recovery_center(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await solid._safe_defer_update(interaction)
    embed = await _build_recovery_embed(guild)
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=RecoveryCenterView(),
    )


def register_public_setup_recovery_commands(
    bot: Any,
    tree: Any,
) -> None:
    """Register recovery helpers without replacing canonical setup owners."""

    global _PATCHED
    _ = bot, tree
    _PATCHED = True
    print("✅ public_setup_recovery: direct repair/restart center ready")


__all__ = [
    "register_public_setup_recovery_commands",
    "open_recovery_center",
]
