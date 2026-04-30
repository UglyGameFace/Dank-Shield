from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from ..guild_config import (
    clear_guild_config_cache,
    config_summary_for_guild,
    discover_runtime_guild_config,
    save_runtime_discovered_config,
    upsert_guild_config,
)
from ..tickets_new.panel_repository import (
    build_panel_runtime_config,
    get_ticket_panel,
    get_ticket_panel_bundle,
    get_ticket_panel_rules,
    list_ticket_panels,
    replace_ticket_panel_categories,
    upsert_ticket_panel_rules,
)
from .common import _staff_check, reply_once


# ============================================================
# commands_ext/ticket_panel_admin_safe.py
# ------------------------------------------------------------
# Safe replacement for the broken ticket_panel_admin.py module.
# This module keeps panel/setup commands registering while the
# larger panel admin file is repaired later.
# ============================================================

CONFIG_KEY_CHOICES = [
    app_commands.Choice(name="Verify Channel", value="verify_channel_id"),
    app_commands.Choice(name="VC Verify Channel", value="vc_verify_channel_id"),
    app_commands.Choice(name="VC Verify Queue Channel", value="vc_verify_queue_channel_id"),
    app_commands.Choice(name="Ticket Category", value="ticket_category_id"),
    app_commands.Choice(name="Archive Ticket Category", value="ticket_archive_category_id"),
    app_commands.Choice(name="Transcripts Channel", value="transcripts_channel_id"),
    app_commands.Choice(name="Status Channel", value="status_channel_id"),
    app_commands.Choice(name="Modlog Channel", value="modlog_channel_id"),
    app_commands.Choice(name="Raidlog Channel", value="raidlog_channel_id"),
    app_commands.Choice(name="Join Log Channel", value="join_log_channel_id"),
    app_commands.Choice(name="Force Verify Log Channel", value="force_verify_log_channel_id"),
    app_commands.Choice(name="Unverified Role", value="unverified_role_id"),
    app_commands.Choice(name="Verified Role", value="verified_role_id"),
    app_commands.Choice(name="Resident Role", value="resident_role_id"),
    app_commands.Choice(name="Staff Role", value="staff_role_id"),
    app_commands.Choice(name="VC Staff Role", value="vc_staff_role_id"),
]

TRANSCRIPT_MODE_CHOICES = [
    app_commands.Choice(name="On Close", value="on_close"),
    app_commands.Choice(name="Always", value="always"),
    app_commands.Choice(name="Manual", value="manual"),
    app_commands.Choice(name="Disabled", value="disabled"),
]


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _slugify(value: Any, limit: int = 100) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/", ":"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")[:limit]


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _maybe_channel_id(channel: Optional[discord.abc.GuildChannel]) -> Optional[str]:
    try:
        return str(channel.id) if channel is not None else None
    except Exception:
        return None


def _maybe_role_id(role: Optional[discord.Role]) -> Optional[str]:
    try:
        return str(role.id) if role is not None else None
    except Exception:
        return None


def _comma_slugs(value: Any) -> List[str]:
    raw = _safe_str(value)
    if not raw:
        return []
    out: List[str] = []
    for part in raw.split(","):
        slug = _slugify(part, limit=120)
        if slug and slug not in out:
            out.append(slug)
    return out


def _config_embed(summary: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Guild Setup Config",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Guild", value=f"{_safe_str(summary.get('guild_name'), 'Unknown')}\n`{_safe_str(summary.get('guild_id'))}`", inline=False)
    embed.add_field(name="Source", value=f"`{_safe_str(summary.get('source'), 'unknown')}`", inline=True)
    embed.add_field(name="Use .env Fallbacks", value=f"`{_safe_bool(summary.get('use_env_fallbacks'), True)}`", inline=True)
    embed.add_field(name="Runtime Discovery", value=f"`{_safe_bool(summary.get('allow_runtime_discovery'), True)}`", inline=True)

    fields = [
        ("Verify Channel", "verify_channel_id"),
        ("Ticket Category", "ticket_category_id"),
        ("Archive Category", "ticket_archive_category_id"),
        ("Transcripts", "transcripts_channel_id"),
        ("Status", "status_channel_id"),
        ("Modlog", "modlog_channel_id"),
        ("Unverified Role", "unverified_role_id"),
        ("Verified Role", "verified_role_id"),
        ("Resident Role", "resident_role_id"),
        ("Staff Role", "staff_role_id"),
    ]
    for label, key in fields:
        embed.add_field(name=label, value=f"`{_safe_str(summary.get(key), 'not set')}`", inline=True)
    return embed


def _panel_line(row: Dict[str, Any]) -> str:
    key = _safe_str(row.get("panel_key"), "unknown")
    name = _safe_str(row.get("panel_name"), key)
    enabled = "enabled" if _safe_bool(row.get("is_enabled"), True) else "disabled"
    style = _safe_str(row.get("panel_style"), "buttons")
    ch = _safe_int(row.get("panel_channel_id"), 0)
    msg = _safe_int(row.get("panel_message_id"), 0)
    location = f" • <#{ch}>" if ch > 0 else ""
    if ch > 0 and msg > 0:
        location += f" / `{msg}`"
    return f"• `{key}` — **{name}** • `{style}` • `{enabled}`{location}"


def _rules_text(rules: Dict[str, Any]) -> str:
    lines = [
        f"per_owner_open_limit: `{_safe_int(rules.get('per_owner_open_limit'), 1)}`",
        f"cooldown_seconds: `{_safe_int(rules.get('cooldown_seconds'), 0)}`",
        f"auto_close_enabled: `{_safe_bool(rules.get('auto_close_enabled'), False)}`",
        f"auto_close_minutes: `{_safe_int(rules.get('auto_close_minutes'), 1440)}`",
        f"inactivity_reminders_enabled: `{_safe_bool(rules.get('inactivity_reminders_enabled'), True)}`",
        f"inactivity_reminder_minutes: `{_safe_int(rules.get('inactivity_reminder_minutes'), 240)}`",
        f"allow_unverified: `{_safe_bool(rules.get('allow_unverified'), True)}`",
        f"allow_verified: `{_safe_bool(rules.get('allow_verified'), True)}`",
        f"allow_resident: `{_safe_bool(rules.get('allow_resident'), True)}`",
        f"allow_staff: `{_safe_bool(rules.get('allow_staff'), True)}`",
        f"allow_unknown_members: `{_safe_bool(rules.get('allow_unknown_members'), True)}`",
        f"ghost_allowed: `{_safe_bool(rules.get('ghost_allowed'), False)}`",
        f"transcript_mode: `{_safe_str(rules.get('transcript_mode'), 'on_close')}`",
        f"close_confirmation_required: `{_safe_bool(rules.get('close_confirmation_required'), True)}`",
        f"staff_alert_channel_id: `{_safe_str(rules.get('staff_alert_channel_id'), 'not set')}`",
    ]
    return "\n".join(lines)


def _panel_embed(bundle: Dict[str, Any]) -> discord.Embed:
    panel = dict(bundle.get("panel") or {})
    rules = dict(bundle.get("rules") or {})
    categories = list(bundle.get("categories") or [])
    embed = discord.Embed(
        title=f"🎛️ Ticket Panel: {_safe_str(panel.get('panel_name'), 'Unnamed')}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Panel Key", value=f"`{_safe_str(panel.get('panel_key'), 'unknown')}`", inline=True)
    embed.add_field(name="Style", value=f"`{_safe_str(panel.get('panel_style'), 'buttons')}`", inline=True)
    embed.add_field(name="Enabled", value=f"`{_safe_bool(panel.get('is_enabled'), True)}`", inline=True)
    embed.add_field(name="Prompt", value=_truncate(_safe_str(panel.get("prompt_description"), "not set"), 1000), inline=False)
    if categories:
        lines = [f"• `{_safe_str(row.get('category_slug'), 'unknown')}`" for row in categories[:25] if isinstance(row, dict)]
        embed.add_field(name="Bound Categories", value="\n".join(lines)[:1024] or "All categories are allowed.", inline=False)
    else:
        embed.add_field(name="Bound Categories", value="All categories are allowed.", inline=False)
    embed.add_field(name="Rules", value=_rules_text(rules)[:1024], inline=False)
    return embed


def register_ticket_panel_admin_commands(bot, tree) -> None:
    @tree.command(name="ticket_setup_status", description="Show this server's ticket setup config and fallback state.")
    async def ticket_setup_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        await reply_once(interaction, {"embed": _config_embed(await config_summary_for_guild(guild)), "ephemeral": True})

    @tree.command(name="ticket_setup_discover", description="Discover obvious roles/channels for this server without saving them.")
    async def ticket_setup_discover(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        config = await discover_runtime_guild_config(guild)
        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(name="ticket_setup_save_discovered", description="Save discovered roles/channels into DB for this server.")
    async def ticket_setup_save_discovered(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        config = await save_runtime_discovered_config(guild)
        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(name="ticket_setup_set_channel", description="Save a setup channel/category ID for this server.")
    @app_commands.describe(key="Which setup value to update", channel="Channel/category to save")
    @app_commands.choices(key=CONFIG_KEY_CHOICES)
    async def ticket_setup_set_channel(interaction: discord.Interaction, key: app_commands.Choice[str], channel: discord.abc.GuildChannel):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        if not key.value.endswith("_channel_id") and not key.value.endswith("_category_id"):
            return await reply_once(interaction, {"content": "❌ That setup key expects a channel/category.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        config = await upsert_guild_config(guild.id, {key.value: _maybe_channel_id(channel)})
        clear_guild_config_cache(guild.id)
        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(name="ticket_setup_set_role", description="Save a setup role ID for this server.")
    @app_commands.describe(key="Which setup role to update", role="Role to save")
    @app_commands.choices(key=CONFIG_KEY_CHOICES)
    async def ticket_setup_set_role(interaction: discord.Interaction, key: app_commands.Choice[str], role: discord.Role):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        if not key.value.endswith("_role_id"):
            return await reply_once(interaction, {"content": "❌ That setup key expects a role.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        config = await upsert_guild_config(guild.id, {key.value: _maybe_role_id(role)})
        clear_guild_config_cache(guild.id)
        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(name="ticket_panel_list", description="List DB-backed ticket panels for this server.")
    async def ticket_panel_list(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        rows = await list_ticket_panels(guild.id)
        embed = discord.Embed(title="🎛️ Ticket Panels", color=discord.Color.blurple(), timestamp=now_utc())
        embed.description = "\n".join(_panel_line(row) for row in rows[:25])[:4000] if rows else "No DB-backed panels have been created yet."
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(name="ticket_panel_show", description="Show a DB-backed ticket panel config.")
    @app_commands.describe(panel_key="Panel key to inspect")
    async def ticket_panel_show(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        key = _slugify(panel_key)
        bundle = await get_ticket_panel_bundle(guild.id, key)
        if not bundle:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})
        await reply_once(interaction, {"embed": _panel_embed(bundle), "ephemeral": True})

    @tree.command(name="ticket_panel_bind_categories", description="Bind allowed ticket category slugs to a panel.")
    @app_commands.describe(panel_key="Panel key", categories="Comma-separated slugs. Example: support,appeal,report")
    async def ticket_panel_bind_categories(interaction: discord.Interaction, panel_key: str, categories: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        key = _slugify(panel_key)
        slugs = _comma_slugs(categories)
        rows = await replace_ticket_panel_categories(guild.id, key, slugs)
        category_text = ", ".join(f"`{_safe_str(row.get('category_slug'))}`" for row in rows[:20] if isinstance(row, dict) and row.get("category_slug"))
        if not category_text:
            category_text = "No categories; panel allows all categories."
        await reply_once(interaction, {"content": f"✅ Bound `{len(rows)}` categories to panel `{key}`.\n{category_text}", "ephemeral": True})

    @tree.command(name="ticket_panel_rules", description="Show rules for a panel.")
    @app_commands.describe(panel_key="Panel key")
    async def ticket_panel_rules(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        key = _slugify(panel_key)
        rules = await get_ticket_panel_rules(guild.id, key)
        embed = discord.Embed(title=f"⚙️ Panel Rules: {key}", description=_rules_text(rules), color=discord.Color.blurple(), timestamp=now_utc())
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(name="ticket_panel_rules_set", description="Update common panel rules.")
    @app_commands.describe(
        panel_key="Panel key",
        per_owner_open_limit="Open ticket limit per owner for this panel",
        auto_close_enabled="Enable auto-close",
        auto_close_minutes="Minutes before auto-close",
        inactivity_reminders_enabled="Enable inactivity reminders",
        inactivity_reminder_minutes="Minutes before inactivity reminder",
        transcript_mode="Transcript mode",
    )
    @app_commands.choices(transcript_mode=TRANSCRIPT_MODE_CHOICES)
    async def ticket_panel_rules_set(
        interaction: discord.Interaction,
        panel_key: str,
        per_owner_open_limit: Optional[int] = None,
        auto_close_enabled: Optional[bool] = None,
        auto_close_minutes: Optional[int] = None,
        inactivity_reminders_enabled: Optional[bool] = None,
        inactivity_reminder_minutes: Optional[int] = None,
        transcript_mode: Optional[app_commands.Choice[str]] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        key = _slugify(panel_key)
        current = await get_ticket_panel_rules(guild.id, key)
        patch: Dict[str, Any] = {"guild_id": str(guild.id), "panel_key": key, **current}
        if per_owner_open_limit is not None:
            patch["per_owner_open_limit"] = max(1, int(per_owner_open_limit))
        if auto_close_enabled is not None:
            patch["auto_close_enabled"] = bool(auto_close_enabled)
        if auto_close_minutes is not None:
            patch["auto_close_minutes"] = max(5, int(auto_close_minutes))
        if inactivity_reminders_enabled is not None:
            patch["inactivity_reminders_enabled"] = bool(inactivity_reminders_enabled)
        if inactivity_reminder_minutes is not None:
            patch["inactivity_reminder_minutes"] = max(1, int(inactivity_reminder_minutes))
        if transcript_mode is not None:
            patch["transcript_mode"] = transcript_mode.value
        updated = await upsert_ticket_panel_rules(patch)
        embed = discord.Embed(title=f"✅ Updated Panel Rules: {key}", description=_rules_text(updated), color=discord.Color.green(), timestamp=now_utc())
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(name="ticket_panel_runtime", description="Show the effective runtime config for a panel.")
    @app_commands.describe(panel_key="Panel key")
    async def ticket_panel_runtime(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        key = _slugify(panel_key)
        runtime = await build_panel_runtime_config(guild.id, key)
        if not runtime:
            return await reply_once(interaction, {"content": f"❌ Runtime config for `{key}` was not found.", "ephemeral": True})
        embed = discord.Embed(title=f"🧪 Panel Runtime: {key}", color=discord.Color.dark_teal(), timestamp=now_utc())
        embed.add_field(name="Panel", value=_truncate(str(runtime.get("panel") or {}), 1000), inline=False)
        embed.add_field(name="Rules", value=_truncate(str(runtime.get("rules") or {}), 1000), inline=False)
        embed.add_field(name="Categories", value=_truncate(str(runtime.get("categories") or []), 1000), inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
