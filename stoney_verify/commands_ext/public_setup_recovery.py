from __future__ import annotations

"""Setup recovery tools for the public /dank setup flow.

Owners need a panic button that is safe by default. This module adds a Recovery
Center to /dank setup with reversible DB-level reset actions:

- Start over safely: clears saved setup + ticket menu options, but does not
  delete Discord roles/channels/tickets.
- Reset saved channels/roles only.
- Reset ticket menu options only.
- Restore the last reset snapshot.
- Rebuild the recommended ticket menu.

Discord objects are intentionally not deleted here. Deleting channels/roles is
much harder to make safe and should live behind a separate audit-first cleanup
flow later.
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from ..globals import get_supabase, now_utc
from ..guild_config import GUILD_CONFIG_TABLE_FALLBACKS, get_guild_config, invalidate_guild_config
from . import public_setup_recommend as recommend
from . import public_setup_solid as solid

_PATCHED = False

CONFIG_KEYS: tuple[str, ...] = (
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
    "staff_role_id",
    "vc_staff_role_id",
    "ticket_prefix",
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
    safe = []
    last_dash = False
    for ch in text:
        if ch.isalnum():
            safe.append(ch)
            last_dash = False
        elif not last_dash:
            safe.append("-")
            last_dash = True
    return "".join(safe).strip("-")[:80]


def _category_count_text(count: int) -> str:
    return f"{count} ticket menu option" + ("" if count == 1 else "s")


def _config_count_text(count: int) -> str:
    return f"{count} saved setup value" + ("" if count == 1 else "s")


def _supabase_required() -> Any:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase is not available.")
    return sb


def _fetch_config_row_sync(guild_id: int) -> tuple[str, Optional[dict[str, Any]], str]:
    sb = _supabase_required()
    last_error = ""
    for table in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            res = sb.table(table).select("*").eq("guild_id", str(guild_id)).limit(1).execute()
            rows = getattr(res, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return table, dict(rows[0]), ""
            return table, None, ""
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:250]}"
            continue
    return str(GUILD_CONFIG_TABLE_FALLBACKS[0] if GUILD_CONFIG_TABLE_FALLBACKS else "guild_configs"), None, last_error


def _fetch_ticket_categories_sync(guild_id: int) -> tuple[list[dict[str, Any]], str]:
    sb = _supabase_required()
    try:
        res = sb.table("ticket_categories").select("*").eq("guild_id", str(guild_id)).execute()
        rows = getattr(res, "data", None) or []
        clean: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            item["guild_id"] = str(guild_id)
            clean.append(item)
        clean.sort(key=lambda r: (r.get("sort_order") is None, r.get("sort_order") or 999999, str(r.get("slug") or "")))
        return clean, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:250]}"


def _clean_category_for_restore(row: Mapping[str, Any], guild_id: int) -> dict[str, Any]:
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
    clean["intake_type"] = str(clean.get("intake_type") or "custom").strip().lower()[:40]
    keywords = clean.get("match_keywords")
    if not isinstance(keywords, list):
        keywords = []
    clean["match_keywords"] = [str(x).strip()[:50] for x in keywords if str(x).strip()][:20]
    try:
        clean["sort_order"] = int(clean.get("sort_order") or 0) or None
    except Exception:
        clean["sort_order"] = None
    clean["is_default"] = bool(clean.get("is_default"))
    return clean


def _current_snapshot_sync(guild_id: int, user: discord.abc.User) -> dict[str, Any]:
    table, row, row_error = _fetch_config_row_sync(guild_id)
    settings = _merge_settings(row)
    categories, category_error = _fetch_ticket_categories_sync(guild_id)
    config_values = {key: settings.get(key) for key in CONFIG_KEYS if settings.get(key) is not None}
    return {
        "created_at": _now_iso(),
        "guild_id": str(guild_id),
        "by_id": str(getattr(user, "id", "")),
        "by_name": str(user),
        "table": table,
        "config_error": row_error,
        "category_error": category_error,
        "config": config_values,
        "ticket_categories": [_clean_category_for_restore(row, guild_id) for row in categories],
    }


def _write_config_patch_sync(guild_id: int, patch: dict[str, Any], snapshot: Optional[dict[str, Any]] = None) -> str:
    sb = _supabase_required()
    table, row, row_error = _fetch_config_row_sync(guild_id)
    if row_error and row is None:
        raise RuntimeError(row_error)

    settings = _merge_settings(row)
    for key, value in patch.items():
        settings[key] = value
    if snapshot is not None:
        settings["last_setup_snapshot"] = snapshot
    settings["setup_recovery_updated_at"] = _now_iso()

    base = {"guild_id": str(guild_id), "updated_at": _now_iso()}
    direct_with_settings = {**base, **patch, "settings": settings}
    direct_only = {**base, **patch}
    settings_only = {**base, "settings": settings}
    config_only = {**base, "config": settings}

    payloads = (direct_with_settings, direct_only, settings_only, config_only)
    last_error = ""

    for payload in payloads:
        try:
            if row:
                sb.table(table).update(payload).eq("guild_id", str(guild_id)).execute()
            else:
                try:
                    sb.table(table).upsert(payload, on_conflict="guild_id").execute()
                except TypeError:
                    sb.table(table).upsert(payload).execute()
            return table
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:250]}"
            continue

    raise RuntimeError(last_error or "Could not update guild setup config.")


def _delete_ticket_categories_sync(guild_id: int) -> tuple[int, str]:
    sb = _supabase_required()
    rows, read_error = _fetch_ticket_categories_sync(guild_id)
    if read_error:
        return 0, read_error
    try:
        sb.table("ticket_categories").delete().eq("guild_id", str(guild_id)).execute()
        return len(rows), ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:250]}"


def _restore_categories_sync(guild_id: int, rows: list[dict[str, Any]]) -> tuple[int, str]:
    sb = _supabase_required()
    try:
        sb.table("ticket_categories").delete().eq("guild_id", str(guild_id)).execute()
    except Exception as e:
        return 0, f"Could not clear current ticket menu options: {type(e).__name__}: {str(e)[:220]}"

    clean_rows = [_clean_category_for_restore(row, guild_id) for row in rows if isinstance(row, Mapping)]
    if not clean_rows:
        return 0, ""
    try:
        sb.table("ticket_categories").insert(clean_rows).execute()
        return len(clean_rows), ""
    except Exception as e:
        return 0, f"Could not restore ticket menu options: {type(e).__name__}: {str(e)[:220]}"


async def _reset_saved_setup(guild: discord.Guild, user: discord.abc.User, *, include_menu: bool) -> tuple[str, bool]:
    gid = int(guild.id)
    snapshot = await asyncio.to_thread(_current_snapshot_sync, gid, user)
    patch = {key: None for key in CONFIG_KEYS}
    patch.update(
        {
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
            "setup_reset_at": _now_iso(),
            "setup_reset_by_id": str(getattr(user, "id", "")),
            "setup_reset_by_name": str(user),
        }
    )
    table = await asyncio.to_thread(_write_config_patch_sync, gid, patch, snapshot)
    menu_text = ""
    ok = True
    if include_menu:
        deleted, error = await asyncio.to_thread(_delete_ticket_categories_sync, gid)
        if error:
            ok = False
            menu_text = f"\n⚠️ Ticket menu options were not cleared: `{error}`"
        else:
            menu_text = f"\n✅ Cleared {_category_count_text(deleted)}."
    invalidate_guild_config(gid)
    return f"✅ Saved setup reset in `{table}`.\n✅ Restore snapshot saved.{menu_text}", ok


async def _reset_ticket_menu_only(guild: discord.Guild, user: discord.abc.User) -> tuple[str, bool]:
    gid = int(guild.id)
    snapshot = await asyncio.to_thread(_current_snapshot_sync, gid, user)
    await asyncio.to_thread(_write_config_patch_sync, gid, {"setup_menu_reset_at": _now_iso()}, snapshot)
    deleted, error = await asyncio.to_thread(_delete_ticket_categories_sync, gid)
    if error:
        return f"🚫 Ticket menu reset failed: `{error}`", False
    return f"✅ Cleared {_category_count_text(deleted)}.\n✅ Restore snapshot saved.", True


async def _restore_last_reset(guild: discord.Guild) -> tuple[str, bool]:
    gid = int(guild.id)

    def _restore_sync() -> tuple[str, bool]:
        table, row, error = _fetch_config_row_sync(gid)
        if error and row is None:
            return f"🚫 Could not read saved setup: `{error}`", False
        settings = _merge_settings(row)
        snapshot = _mapping(settings.get("last_setup_snapshot"))
        if not snapshot:
            return "🚫 No setup restore snapshot found for this server.", False
        config = _mapping(snapshot.get("config"))
        categories = snapshot.get("ticket_categories")
        if not isinstance(categories, list):
            categories = []
        patch = {key: config.get(key) for key in CONFIG_KEYS if key in config}
        patch.update(
            {
                "setup_restored_at": _now_iso(),
                "use_env_fallbacks": False,
                "allow_runtime_discovery": True,
            }
        )
        _write_config_patch_sync(gid, patch, snapshot)
        restored_categories, category_error = _restore_categories_sync(gid, categories)
        if category_error:
            return f"⚠️ Restored saved setup values from `{table}`, but menu restore had an issue: `{category_error}`", False
        return f"✅ Restored last setup snapshot from `{snapshot.get('created_at', 'unknown time')}`.\n✅ Restored {_category_count_text(restored_categories)}.", True

    message, ok = await asyncio.to_thread(_restore_sync)
    invalidate_guild_config(gid)
    return message, ok


async def _rebuild_recommended_menu(guild: discord.Guild) -> tuple[str, bool]:
    created, skipped, error = await solid._seed_recommended_categories(guild)
    if error:
        return f"🚫 Recommended menu could not be rebuilt: `{error}`", False
    if created:
        return f"✅ Created recommended ticket menu options: {', '.join(f'`{x}`' for x in created)}", True
    return "✅ Recommended ticket menu already exists. Nothing new was needed.", True


async def _recovery_snapshot_summary(guild: discord.Guild) -> tuple[int, int, bool, str]:
    gid = int(guild.id)

    def _sync() -> tuple[int, int, bool, str]:
        table, row, error = _fetch_config_row_sync(gid)
        settings = _merge_settings(row)
        config_count = sum(1 for key in CONFIG_KEYS if settings.get(key) is not None)
        categories, cat_error = _fetch_ticket_categories_sync(gid)
        snapshot = _mapping(settings.get("last_setup_snapshot"))
        detail_error = error or cat_error
        return config_count, len(categories), bool(snapshot), detail_error

    return await asyncio.to_thread(_sync)


async def _build_recovery_embed(guild: discord.Guild, *, title: str = "🛟 Setup Recovery Center") -> discord.Embed:
    config_count, category_count, has_snapshot, error = await _recovery_snapshot_summary(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "Use this when setup got messy, the wrong channels were picked, or the owner wants to start over.\n\n"
            "Safe recovery does **not** delete Discord channels, roles, messages, or tickets. It only clears what Dank Shield has saved for this server."
        ),
        color=discord.Color.gold(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Current Saved State",
        value=(
            f"• {_config_count_text(config_count)} currently saved.\n"
            f"• {_category_count_text(category_count)} currently saved.\n"
            f"• Restore snapshot: {'available' if has_snapshot else 'not available yet'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recommended fix if someone messed up",
        value="Use **Safe Start Over**. It saves a restore snapshot, clears saved setup/menu choices, then lets the owner rerun Quick Setup.",
        inline=False,
    )
    embed.add_field(
        name="What this will never delete",
        value="Discord channels, Discord roles, old tickets, transcripts, server messages, members, bans, or modlogs.",
        inline=False,
    )
    if error:
        embed.add_field(name="Recovery Warning", value=_short(error, 900), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • Recovery actions require confirmation")
    return embed


class RecoveryButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Repair / Restart", emoji="🛟", style=discord.ButtonStyle.danger, custom_id="stoney_recovery:open", row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await _build_recovery_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=RecoveryCenterView())


class RecoveryCenterView(solid.BackToSetupView):
    @discord.ui.button(label="Safe Start Over", emoji="🛟", style=discord.ButtonStyle.danger, custom_id="stoney_recovery:start_over", row=0)
    async def start_over(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmRecoveryModal(action="start_over"))

    @discord.ui.button(label="Reset Saved Channels/Roles", emoji="🧽", style=discord.ButtonStyle.secondary, custom_id="stoney_recovery:reset_config", row=1)
    async def reset_config(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmRecoveryModal(action="reset_config"))

    @discord.ui.button(label="Reset Ticket Menu Only", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="stoney_recovery:reset_menu", row=1)
    async def reset_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmRecoveryModal(action="reset_menu"))

    @discord.ui.button(label="Restore Last Reset", emoji="↩️", style=discord.ButtonStyle.primary, custom_id="stoney_recovery:restore", row=2)
    async def restore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmRecoveryModal(action="restore"))

    @discord.ui.button(label="Rebuild Recommended Menu", emoji="🧱", style=discord.ButtonStyle.success, custom_id="stoney_recovery:rebuild_menu", row=2)
    async def rebuild_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        message, ok = await _rebuild_recommended_menu(guild)
        embed = await _build_recovery_embed(guild, title="✅ Recovery Action Complete" if ok else "🚫 Recovery Action Failed")
        embed.color = discord.Color.green() if ok else discord.Color.red()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        embed.add_field(name="Next Step", value="Run Review Setup, then post a ticket panel if setup is ready.", inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=RecoveryCenterView())


class ConfirmRecoveryModal(discord.ui.Modal):
    def __init__(self, *, action: str) -> None:
        self.action = action
        titles = {
            "start_over": "Confirm Safe Start Over",
            "reset_config": "Confirm Config Reset",
            "reset_menu": "Confirm Menu Reset",
            "restore": "Confirm Restore",
        }
        super().__init__(title=titles.get(action, "Confirm Recovery"))
        expected = "START OVER" if action == "start_over" else "RESET" if action in {"reset_config", "reset_menu"} else "RESTORE"
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
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        typed = str(self.confirm.value or "").strip().upper()
        if typed != self.expected:
            return await interaction.response.send_message(f"🚫 Cancelled. You typed `{typed or 'nothing'}` instead of `{self.expected}`.", ephemeral=True)

        await solid._safe_defer_modal(interaction)
        ok = False
        if self.action == "start_over":
            message, ok = await _reset_saved_setup(guild, interaction.user, include_menu=True)
            next_step = "Run `/dank setup`, then choose Quick Setup."
        elif self.action == "reset_config":
            message, ok = await _reset_saved_setup(guild, interaction.user, include_menu=False)
            next_step = "Run `/dank setup` → Manage Setup → All Features & Settings → Setup Plan & Server Items → Choose Roles & Channels."
        elif self.action == "reset_menu":
            message, ok = await _reset_ticket_menu_only(guild, interaction.user)
            next_step = "Run `/dank setup` → Manage Setup → All Features & Settings → Tickets → Ticket Choices."
        elif self.action == "restore":
            message, ok = await _restore_last_reset(guild)
            next_step = "Run Review Setup to confirm the restored setup is usable."
        else:
            message = "Unknown recovery action."
            next_step = "Go back to setup and try again."

        embed = await _build_recovery_embed(guild, title="✅ Recovery Action Complete" if ok else "🚫 Recovery Action Failed")
        embed.color = discord.Color.green() if ok else discord.Color.red()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        embed.add_field(name="Next Step", value=next_step[:1024], inline=False)
        try:
            await interaction.followup.send(embed=embed, view=RecoveryCenterView(), ephemeral=True)
        except Exception:
            await solid._edit_or_followup(interaction, embed=embed, view=RecoveryCenterView())


def _add_recovery_button(view: discord.ui.View) -> discord.ui.View:
    try:
        if any(getattr(child, "custom_id", "") == "stoney_recovery:open" for child in getattr(view, "children", []) or []):
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
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed = await _build_recovery_embed(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=RecoveryCenterView())



def register_public_setup_recovery_commands(bot: Any, tree: Any) -> None:
    """Register recovery helpers without replacing the canonical setup home."""
    global _PATCHED
    _ = bot, tree
    _PATCHED = True
    print("✅ public_setup_recovery: direct repair/restart center ready")

__all__ = ["register_public_setup_recovery_commands", "open_recovery_center"]
