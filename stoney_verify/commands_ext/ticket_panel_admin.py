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
    DEFAULT_PANEL_RULES,
    apply_panel_preset_to_panel,
    bind_panel_message,
    build_panel_runtime_config,
    delete_ticket_panel,
    delete_ticket_panel_preset,
    ensure_ticket_panel_exists,
    get_panel_mutation_lock,
    get_ticket_panel,
    get_ticket_panel_bundle,
    get_ticket_panel_preset,
    get_ticket_panel_rules,
    list_ticket_panel_categories,
    list_ticket_panel_presets,
    list_ticket_panels,
    replace_ticket_panel_categories,
    upsert_ticket_panel,
    upsert_ticket_panel_preset,
    upsert_ticket_panel_rules,
)

from .common import _staff_check, reply_once, safe_defer

try:
    from ..tickets_new.panel import send_ticket_panel
except Exception:
    send_ticket_panel = None  # type: ignore


# ============================================================
# commands_ext/ticket_panel_admin.py
# ------------------------------------------------------------
# Admin commands for DB-backed multi-panel setup.
#
# Goals:
# - no per-server .env requirement for panels
# - .env remains fallback only through guild_config.py
# - panel configs are guild-scoped and DB-backed
# - setup commands stay safe under simultaneous usage
# - no cross-server config bleed
# ============================================================


PANEL_STYLE_CHOICES = [
    app_commands.Choice(name="Buttons", value="buttons"),
    app_commands.Choice(name="Select Menu", value="select"),
    app_commands.Choice(name="Hybrid", value="hybrid"),
    app_commands.Choice(name="Modal", value="modal"),
]

TRANSCRIPT_MODE_CHOICES = [
    app_commands.Choice(name="On Close", value="on_close"),
    app_commands.Choice(name="Always", value="always"),
    app_commands.Choice(name="Manual", value="manual"),
    app_commands.Choice(name="Disabled", value="disabled"),
]

CONFIG_KEY_CHOICES = [
    app_commands.Choice(name="Verify Channel", value="verify_channel_id"),
    app_commands.Choice(name="VC Verify Channel", value="vc_verify_channel_id"),
    app_commands.Choice(name="VC Verify Queue Channel", value="vc_verify_queue_channel_id"),
    app_commands.Choice(name="Ticket Category", value="ticket_category_id"),
    app_commands.Choice(name="Transcripts Channel", value="transcripts_channel_id"),
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


def _slugify(value: Any, limit: int = 80) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False

    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True

    return "".join(out).strip("-")[:limit]


def _truncate(value: Any, limit: int = 900) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _comma_list(value: Any) -> List[str]:
    raw = _safe_str(value)
    if not raw:
        return []

    out: List[str] = []
    for part in raw.split(","):
        item = _slugify(part, limit=120)
        if item and item not in out:
            out.append(item)

    return out


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


def _panel_line(row: Dict[str, Any]) -> str:
    key = _safe_str(row.get("panel_key"), "unknown")
    name = _safe_str(row.get("panel_name"), key)
    enabled = "enabled" if _safe_bool(row.get("is_enabled"), True) else "disabled"
    style = _safe_str(row.get("panel_style"), "buttons")
    ch = _safe_int(row.get("panel_channel_id"), 0)
    msg = _safe_int(row.get("panel_message_id"), 0)

    location = ""
    if ch > 0:
        location = f" • <#{ch}>"
        if msg > 0:
            location += f" / `{msg}`"

    return f"• `{key}` — **{name}** • `{style}` • `{enabled}`{location}"


def _rules_value(rules: Dict[str, Any]) -> str:
    return "\n".join(
        [
            f"cooldown_seconds: `{_safe_int(rules.get('cooldown_seconds'), 0)}`",
            f"max_tickets_per_window: `{_safe_int(rules.get('max_tickets_per_window'), 0)}`",
            f"window_minutes: `{_safe_int(rules.get('window_minutes'), 0)}`",
            f"per_owner_open_limit: `{_safe_int(rules.get('per_owner_open_limit'), 1)}`",
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
    )


def _panel_embed(bundle: Dict[str, Any]) -> discord.Embed:
    panel = dict(bundle.get("panel") or {})
    rules = dict(bundle.get("rules") or {})
    categories = list(bundle.get("categories") or [])
    preset = dict(bundle.get("preset") or {})

    embed = discord.Embed(
        title=f"🎛️ Ticket Panel: {_safe_str(panel.get('panel_name'), 'Unnamed')}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    enabled = "Yes" if _safe_bool(panel.get("is_enabled"), True) else "No"
    channel_id = _safe_int(panel.get("panel_channel_id"), 0)
    message_id = _safe_int(panel.get("panel_message_id"), 0)

    embed.add_field(name="Panel Key", value=f"`{_safe_str(panel.get('panel_key'), 'unknown')}`", inline=True)
    embed.add_field(name="Style", value=f"`{_safe_str(panel.get('panel_style'), 'buttons')}`", inline=True)
    embed.add_field(name="Enabled", value=enabled, inline=True)

    if channel_id > 0:
        location = f"<#{channel_id}>"
        if message_id > 0:
            location += f"\nMessage ID: `{message_id}`"
        embed.add_field(name="Location", value=location, inline=False)

    embed.add_field(
        name="Prompt",
        value=(
            f"**Title:** {_safe_str(panel.get('prompt_title'), 'not set')}\n"
            f"**Description:** {_truncate(panel.get('prompt_description'), 700) or 'not set'}"
        ),
        inline=False,
    )

    embed.add_field(
        name="UI Text",
        value=(
            f"**Button:** {_safe_str(panel.get('button_label'), 'not set')}\n"
            f"**Menu Placeholder:** {_safe_str(panel.get('menu_placeholder'), 'not set')}"
        ),
        inline=False,
    )

    if preset:
        embed.add_field(
            name="Preset",
            value=f"`{_safe_str(preset.get('preset_key'), 'unknown')}` — {_safe_str(preset.get('preset_name'), 'Unnamed')}",
            inline=False,
        )
    elif _safe_str(panel.get("preset_key")):
        embed.add_field(name="Preset", value=f"`{_safe_str(panel.get('preset_key'))}`", inline=False)

    if categories:
        lines = []
        for row in categories[:25]:
            slug = _safe_str(row.get("category_slug"), "unknown")
            state = "enabled" if _safe_bool(row.get("is_enabled"), True) else "disabled"
            lines.append(f"• `{slug}` — `{state}`")
        embed.add_field(name="Bound Categories", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Bound Categories", value="All categories are allowed.", inline=False)

    embed.add_field(name="Rules", value=_rules_value(rules)[:1024], inline=False)

    return embed


def _config_embed(summary: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Guild Setup Config",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    embed.add_field(name="Guild", value=f"{_safe_str(summary.get('guild_name'), 'Unknown')}\n`{_safe_str(summary.get('guild_id'))}`", inline=False)
    embed.add_field(name="Source", value=f"`{_safe_str(summary.get('source'), 'unknown')}`", inline=True)
    embed.add_field(name="Use .env Fallbacks", value=str(_safe_bool(summary.get("use_env_fallbacks"), True)), inline=True)
    embed.add_field(name="Runtime Discovery", value=str(_safe_bool(summary.get("allow_runtime_discovery"), True)), inline=True)

    ids = [
        ("Verify Channel", "verify_channel_id", True),
        ("Ticket Category", "ticket_category_id", True),
        ("Transcripts Channel", "transcripts_channel_id", True),
        ("Modlog Channel", "modlog_channel_id", True),
        ("Unverified Role", "unverified_role_id", True),
        ("Verified Role", "verified_role_id", True),
        ("Resident Role", "resident_role_id", True),
        ("Staff Role", "staff_role_id", True),
    ]

    for label, key, inline in ids:
        value = _safe_str(summary.get(key), "not set")
        embed.add_field(name=label, value=f"`{value}`", inline=inline)

    return embed


def _preset_embed(row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🧩 Panel Preset: {_safe_str(row.get('preset_name'), 'Unnamed')}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Preset Key", value=f"`{_safe_str(row.get('preset_key'), 'unknown')}`", inline=True)
    embed.add_field(name="Style", value=f"`{_safe_str(row.get('panel_style'), 'buttons')}`", inline=True)
    embed.add_field(name="Prompt Title", value=_safe_str(row.get("default_prompt_title"), "not set"), inline=False)
    embed.add_field(name="Prompt Description", value=_truncate(row.get("default_prompt_description"), 1000) or "not set", inline=False)
    embed.add_field(name="Button Label", value=_safe_str(row.get("default_button_label"), "not set"), inline=True)
    embed.add_field(name="Menu Placeholder", value=_safe_str(row.get("default_menu_placeholder"), "not set"), inline=True)

    rules = row.get("default_rules_json")
    if isinstance(rules, dict) and rules:
        lines = [f"`{k}` = `{v}`" for k, v in list(rules.items())[:20]]
        embed.add_field(name="Default Rules", value="\n".join(lines)[:1024], inline=False)

    return embed


async def _infer_last_bot_message(channel: discord.TextChannel, bot_user_id: int) -> Optional[discord.Message]:
    try:
        async for msg in channel.history(limit=8):
            if msg.author and int(msg.author.id) == int(bot_user_id):
                return msg
    except Exception:
        return None
    return None


async def _post_existing_panel(channel: discord.TextChannel) -> Optional[discord.Message]:
    if send_ticket_panel is None:
        return None

    before_id = 0
    try:
        async for msg in channel.history(limit=1):
            before_id = int(msg.id)
            break
    except Exception:
        before_id = 0

    result = await send_ticket_panel(channel)

    if isinstance(result, discord.Message):
        return result

    try:
        bot_user = getattr(channel.guild.me, "id", None)
        if bot_user:
            async for msg in channel.history(limit=10):
                if int(msg.id) == before_id:
                    break
                if msg.author and int(msg.author.id) == int(bot_user):
                    return msg
    except Exception:
        pass

    return None


def register_ticket_panel_admin_commands(bot, tree) -> None:
    # ========================================================
    # Guild setup / fallback config
    # ========================================================

    @tree.command(
        name="ticket_setup_status",
        description="Show this server's ticket setup config and fallback state.",
    )
    async def ticket_setup_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        summary = await config_summary_for_guild(guild)
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(
        name="ticket_setup_discover",
        description="Discover obvious roles/channels for this server without saving them.",
    )
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

    @tree.command(
        name="ticket_setup_save_discovered",
        description="Save discovered ticket setup roles/channels into DB for this server.",
    )
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

    @tree.command(
        name="ticket_setup_set_channel",
        description="Save a ticket setup channel/category ID for this server.",
    )
    @app_commands.describe(
        key="Which setup value to update",
        channel="Channel/category to save",
    )
    @app_commands.choices(key=CONFIG_KEY_CHOICES)
    async def ticket_setup_set_channel(
        interaction: discord.Interaction,
        key: app_commands.Choice[str],
        channel: discord.abc.GuildChannel,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if not key.value.endswith("_channel_id") and not key.value.endswith("_category_id"):
            return await reply_once(
                interaction,
                {"content": "❌ That setup key expects a role, not a channel/category.", "ephemeral": True},
            )

        config = await upsert_guild_config(guild.id, {key.value: _maybe_channel_id(channel)})
        clear_guild_config_cache(guild.id)

        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(
        name="ticket_setup_set_role",
        description="Save a ticket setup role ID for this server.",
    )
    @app_commands.describe(
        key="Which setup role to update",
        role="Role to save",
    )
    @app_commands.choices(key=CONFIG_KEY_CHOICES)
    async def ticket_setup_set_role(
        interaction: discord.Interaction,
        key: app_commands.Choice[str],
        role: discord.Role,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if not key.value.endswith("_role_id"):
            return await reply_once(
                interaction,
                {"content": "❌ That setup key expects a channel/category, not a role.", "ephemeral": True},
            )

        config = await upsert_guild_config(guild.id, {key.value: _maybe_role_id(role)})
        clear_guild_config_cache(guild.id)

        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    @tree.command(
        name="ticket_setup_fallbacks",
        description="Enable/disable .env fallback and runtime discovery for this server.",
    )
    @app_commands.describe(
        use_env_fallbacks="Allow .env as fallback when DB config is missing",
        allow_runtime_discovery="Allow runtime discovery by obvious role/channel names",
    )
    async def ticket_setup_fallbacks(
        interaction: discord.Interaction,
        use_env_fallbacks: bool,
        allow_runtime_discovery: bool,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        config = await upsert_guild_config(
            guild.id,
            {
                "use_env_fallbacks": bool(use_env_fallbacks),
                "allow_runtime_discovery": bool(allow_runtime_discovery),
            },
        )
        clear_guild_config_cache(guild.id)

        summary = dict(config)
        summary["guild_id"] = str(guild.id)
        summary["guild_name"] = guild.name
        await reply_once(interaction, {"embed": _config_embed(summary), "ephemeral": True})

    # ========================================================
    # Panel CRUD
    # ========================================================

    @tree.command(
        name="ticket_panel_create",
        description="Create or update a DB-backed ticket panel.",
    )
    @app_commands.describe(
        panel_key="Unique panel key, like support, verify, appeals",
        panel_name="Friendly panel name",
        style="Panel style",
        channel="Optional channel where this panel lives",
        prompt_title="Optional panel prompt title",
        prompt_description="Optional panel prompt description",
        button_label="Optional button label",
        menu_placeholder="Optional select menu placeholder",
        preset_key="Optional preset key to apply",
    )
    @app_commands.choices(style=PANEL_STYLE_CHOICES)
    async def ticket_panel_create(
        interaction: discord.Interaction,
        panel_key: str,
        panel_name: str,
        style: app_commands.Choice[str] = PANEL_STYLE_CHOICES[0],
        channel: Optional[discord.TextChannel] = None,
        prompt_title: Optional[str] = None,
        prompt_description: Optional[str] = None,
        button_label: Optional[str] = None,
        menu_placeholder: Optional[str] = None,
        preset_key: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        if not key:
            return await reply_once(interaction, {"content": "❌ Panel key cannot be empty.", "ephemeral": True})

        async with get_panel_mutation_lock(guild_id=guild.id, panel_key=key):
            row = await ensure_ticket_panel_exists(
                guild_id=guild.id,
                panel_key=key,
                panel_name=panel_name,
                panel_style=style.value,
                prompt_title=prompt_title,
                prompt_description=prompt_description,
                button_label=button_label,
                menu_placeholder=menu_placeholder,
                preset_key=preset_key,
            )

            payload = dict(row)
            payload.update(
                {
                    "guild_id": str(guild.id),
                    "panel_key": key,
                    "panel_name": panel_name,
                    "panel_style": style.value,
                    "panel_channel_id": str(channel.id) if channel else row.get("panel_channel_id"),
                    "prompt_title": prompt_title or row.get("prompt_title"),
                    "prompt_description": prompt_description or row.get("prompt_description"),
                    "button_label": button_label or row.get("button_label"),
                    "menu_placeholder": menu_placeholder or row.get("menu_placeholder"),
                    "preset_key": _slugify(preset_key) if preset_key else row.get("preset_key"),
                    "is_enabled": True,
                }
            )
            updated = await upsert_ticket_panel(payload)

        bundle = await get_ticket_panel_bundle(guild.id, key)
        if bundle:
            return await reply_once(interaction, {"embed": _panel_embed(bundle), "ephemeral": True})

        await reply_once(
            interaction,
            {"content": f"✅ Panel `{key}` saved, but could not load its full bundle.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_panel_list",
        description="List all DB-backed ticket panels for this server.",
    )
    async def ticket_panel_list(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await list_ticket_panels(guild.id)

        embed = discord.Embed(
            title="🎛️ Ticket Panels",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.description = "No DB-backed panels have been created yet."
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        embed.description = "\n".join(_panel_line(row) for row in rows[:25])[:4000]
        embed.set_footer(text=f"Showing {min(len(rows), 25)} panel(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_panel_show",
        description="Show a DB-backed ticket panel config.",
    )
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

    @tree.command(
        name="ticket_panel_delete",
        description="Delete a DB-backed ticket panel config.",
    )
    @app_commands.describe(panel_key="Panel key to delete")
    async def ticket_panel_delete(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        ok = await delete_ticket_panel(guild.id, key)

        if not ok:
            return await reply_once(interaction, {"content": f"❌ Failed deleting panel `{key}`.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Deleted panel `{key}`.", "ephemeral": True})

    @tree.command(
        name="ticket_panel_enable",
        description="Enable a DB-backed ticket panel.",
    )
    @app_commands.describe(panel_key="Panel key to enable")
    async def ticket_panel_enable(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        row = await get_ticket_panel(guild.id, key)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        row["is_enabled"] = True
        await upsert_ticket_panel(row)
        bundle = await get_ticket_panel_bundle(guild.id, key)

        await reply_once(
            interaction,
            {"embed": _panel_embed(bundle) if bundle else None, "content": None if bundle else f"✅ Enabled `{key}`.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_panel_disable",
        description="Disable a DB-backed ticket panel.",
    )
    @app_commands.describe(panel_key="Panel key to disable")
    async def ticket_panel_disable(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        row = await get_ticket_panel(guild.id, key)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        row["is_enabled"] = False
        await upsert_ticket_panel(row)
        bundle = await get_ticket_panel_bundle(guild.id, key)

        await reply_once(
            interaction,
            {"embed": _panel_embed(bundle) if bundle else None, "content": None if bundle else f"✅ Disabled `{key}`.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_panel_bind_categories",
        description="Bind allowed ticket categories to a panel using comma-separated slugs.",
    )
    @app_commands.describe(
        panel_key="Panel key to update",
        categories="Comma-separated category slugs. Example: support,appeal,report",
    )
    async def ticket_panel_bind_categories(
        interaction: discord.Interaction,
        panel_key: str,
        categories: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        slugs = _comma_list(categories)
        rows = await replace_ticket_panel_categories(guild.id, key, slugs)

        bound_slugs = ", ".join(f"`{r.get('category_slug')}`" for r in rows[:20])
        if not bound_slugs:
            bound_slugs = "No categories; panel allows all categories."
        plural = "y" if len(rows) == 1 else "ies"
        await reply_once(
            interaction,
            {
                "content": (
                    f"✅ Bound `{len(rows)}` categor{plural} to panel `{key}`.\n"
                    f"{bound_slugs}"
                ),
                "ephemeral": True,
            },
        )

    @tree.command(
        name="ticket_panel_clear_categories",
        description="Clear category bindings so a panel allows all categories.",
    )
    @app_commands.describe(panel_key="Panel key to update")
    async def ticket_panel_clear_categories(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        await replace_ticket_panel_categories(guild.id, key, [])
        await reply_once(interaction, {"content": f"✅ Cleared category bindings for `{key}`.", "ephemeral": True})

    @tree.command(
        name="ticket_panel_bind_message",
        description="Bind an existing panel message to a DB-backed panel.",
    )
    @app_commands.describe(
        panel_key="Panel key to bind",
        channel="Channel containing the panel message",
        message_id="Panel message ID",
    )
    async def ticket_panel_bind_message(
        interaction: discord.Interaction,
        panel_key: str,
        channel: discord.TextChannel,
        message_id: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        msg_id = _safe_int(message_id, 0)
        if msg_id <= 0:
            return await reply_once(interaction, {"content": "❌ Invalid message ID.", "ephemeral": True})

        updated = await bind_panel_message(
            guild_id=guild.id,
            panel_key=key,
            channel_id=channel.id,
            message_id=msg_id,
        )

        if not updated:
            return await reply_once(interaction, {"content": f"❌ Failed binding panel `{key}`.", "ephemeral": True})

        await reply_once(
            interaction,
            {"content": f"✅ Bound panel `{key}` to {channel.mention} message `{msg_id}`.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_panel_post",
        description="Post the existing ticket panel and bind it to a DB-backed panel key.",
    )
    @app_commands.describe(
        panel_key="Panel key to post/bind",
        channel="Where to post the panel. Leave empty for current channel.",
    )
    async def ticket_panel_post(
        interaction: discord.Interaction,
        panel_key: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await reply_once(interaction, {"content": "❌ Must be a text channel.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        if send_ticket_panel is None:
            return await reply_once(interaction, {"content": "❌ Existing panel sender is unavailable.", "ephemeral": True})

        await safe_defer(interaction, ephemeral=True)

        try:
            msg = await _post_existing_panel(target)
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed posting panel: `{e}`", ephemeral=True)

        if msg is not None:
            await bind_panel_message(
                guild_id=guild.id,
                panel_key=key,
                channel_id=target.id,
                message_id=msg.id,
            )
            await interaction.followup.send(
                f"✅ Posted panel `{key}` in {target.mention} and bound message `{msg.id}`.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"⚠️ Panel sender ran, but I could not infer the message ID. Use `/ticket_panel_bind_message` manually.",
            ephemeral=True,
        )

    # ========================================================
    # Panel rules
    # ========================================================

    @tree.command(
        name="ticket_panel_rules",
        description="Show rules for a panel.",
    )
    @app_commands.describe(panel_key="Panel key")
    async def ticket_panel_rules(interaction: discord.Interaction, panel_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        rules = await get_ticket_panel_rules(guild.id, key)

        embed = discord.Embed(
            title=f"⚙️ Panel Rules: {key}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
            description=_rules_value(rules),
        )
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_panel_rules_set",
        description="Update common panel rules.",
    )
    @app_commands.describe(
        panel_key="Panel key",
        cooldown_seconds="Cooldown before same user opens another ticket through this panel",
        per_owner_open_limit="Open ticket limit per owner for this panel",
        auto_close_enabled="Enable auto-close for this panel",
        auto_close_minutes="Minutes before auto-close",
        inactivity_reminders_enabled="Enable inactivity reminders for this panel",
        inactivity_reminder_minutes="Minutes before inactivity reminder",
        staff_alert_channel="Staff alert channel for this panel",
        ghost_allowed="Whether ghost tickets are allowed for this panel",
        close_confirmation_required="Require close confirmation controls",
        transcript_mode="Transcript mode",
    )
    @app_commands.choices(transcript_mode=TRANSCRIPT_MODE_CHOICES)
    async def ticket_panel_rules_set(
        interaction: discord.Interaction,
        panel_key: str,
        cooldown_seconds: Optional[int] = None,
        per_owner_open_limit: Optional[int] = None,
        auto_close_enabled: Optional[bool] = None,
        auto_close_minutes: Optional[int] = None,
        inactivity_reminders_enabled: Optional[bool] = None,
        inactivity_reminder_minutes: Optional[int] = None,
        staff_alert_channel: Optional[discord.TextChannel] = None,
        ghost_allowed: Optional[bool] = None,
        close_confirmation_required: Optional[bool] = None,
        transcript_mode: Optional[app_commands.Choice[str]] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        current = await get_ticket_panel_rules(guild.id, key)
        patch: Dict[str, Any] = {
            "guild_id": str(guild.id),
            "panel_key": key,
            **current,
        }

        if cooldown_seconds is not None:
            patch["cooldown_seconds"] = max(0, int(cooldown_seconds))
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
        if staff_alert_channel is not None:
            patch["staff_alert_channel_id"] = str(staff_alert_channel.id)
        if ghost_allowed is not None:
            patch["ghost_allowed"] = bool(ghost_allowed)
        if close_confirmation_required is not None:
            patch["close_confirmation_required"] = bool(close_confirmation_required)
        if transcript_mode is not None:
            patch["transcript_mode"] = transcript_mode.value

        updated = await upsert_ticket_panel_rules(patch)

        embed = discord.Embed(
            title=f"✅ Updated Panel Rules: {key}",
            color=discord.Color.green(),
            timestamp=now_utc(),
            description=_rules_value(updated),
        )
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_panel_rules_access",
        description="Update which member states can use a panel.",
    )
    @app_commands.describe(
        panel_key="Panel key",
        allow_unverified="Allow unverified members",
        allow_verified="Allow verified members",
        allow_resident="Allow residents",
        allow_staff="Allow staff",
        allow_unknown_members="Allow members when role state cannot be resolved",
    )
    async def ticket_panel_rules_access(
        interaction: discord.Interaction,
        panel_key: str,
        allow_unverified: Optional[bool] = None,
        allow_verified: Optional[bool] = None,
        allow_resident: Optional[bool] = None,
        allow_staff: Optional[bool] = None,
        allow_unknown_members: Optional[bool] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(panel_key)
        panel = await get_ticket_panel(guild.id, key)
        if not panel:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})

        current = await get_ticket_panel_rules(guild.id, key)
        patch: Dict[str, Any] = {
            "guild_id": str(guild.id),
            "panel_key": key,
            **current,
        }

        if allow_unverified is not None:
            patch["allow_unverified"] = bool(allow_unverified)
        if allow_verified is not None:
            patch["allow_verified"] = bool(allow_verified)
        if allow_resident is not None:
            patch["allow_resident"] = bool(allow_resident)
        if allow_staff is not None:
            patch["allow_staff"] = bool(allow_staff)
        if allow_unknown_members is not None:
            patch["allow_unknown_members"] = bool(allow_unknown_members)

        updated = await upsert_ticket_panel_rules(patch)

        embed = discord.Embed(
            title=f"✅ Updated Panel Access Rules: {key}",
            color=discord.Color.green(),
            timestamp=now_utc(),
            description=_rules_value(updated),
        )
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    # ========================================================
    # Presets
    # ========================================================

    @tree.command(
        name="ticket_panel_preset_create",
        description="Create or update a reusable panel preset.",
    )
    @app_commands.describe(
        preset_key="Unique preset key, like default-support",
        preset_name="Friendly preset name",
        style="Default panel style",
        prompt_title="Default prompt title",
        prompt_description="Default prompt description",
        button_label="Default button label",
        menu_placeholder="Default select menu placeholder",
    )
    @app_commands.choices(style=PANEL_STYLE_CHOICES)
    async def ticket_panel_preset_create(
        interaction: discord.Interaction,
        preset_key: str,
        preset_name: str,
        style: app_commands.Choice[str] = PANEL_STYLE_CHOICES[0],
        prompt_title: Optional[str] = None,
        prompt_description: Optional[str] = None,
        button_label: Optional[str] = None,
        menu_placeholder: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(preset_key)
        if not key:
            return await reply_once(interaction, {"content": "❌ Preset key cannot be empty.", "ephemeral": True})

        payload = {
            "guild_id": str(guild.id),
            "preset_key": key,
            "preset_name": preset_name,
            "panel_style": style.value,
            "default_prompt_title": prompt_title,
            "default_prompt_description": prompt_description,
            "default_button_label": button_label,
            "default_menu_placeholder": menu_placeholder,
            "default_rules_json": dict(DEFAULT_PANEL_RULES),
        }

        row = await upsert_ticket_panel_preset(payload)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Failed saving preset `{key}`.", "ephemeral": True})

        await reply_once(interaction, {"embed": _preset_embed(row), "ephemeral": True})

    @tree.command(
        name="ticket_panel_preset_list",
        description="List panel presets for this server.",
    )
    async def ticket_panel_preset_list(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await list_ticket_panel_presets(guild.id)

        embed = discord.Embed(
            title="🧩 Ticket Panel Presets",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.description = "No panel presets have been created yet."
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        embed.description = "\n".join(
            f"• `{_safe_str(row.get('preset_key'), 'unknown')}` — **{_safe_str(row.get('preset_name'), 'Unnamed')}** • `{_safe_str(row.get('panel_style'), 'buttons')}`"
            for row in rows[:25]
        )[:4000]
        embed.set_footer(text=f"Showing {min(len(rows), 25)} preset(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_panel_preset_show",
        description="Show a panel preset.",
    )
    @app_commands.describe(preset_key="Preset key to inspect")
    async def ticket_panel_preset_show(interaction: discord.Interaction, preset_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(preset_key)
        row = await get_ticket_panel_preset(guild.id, key)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Preset `{key}` was not found.", "ephemeral": True})

        await reply_once(interaction, {"embed": _preset_embed(row), "ephemeral": True})

    @tree.command(
        name="ticket_panel_preset_delete",
        description="Delete a panel preset.",
    )
    @app_commands.describe(preset_key="Preset key to delete")
    async def ticket_panel_preset_delete(interaction: discord.Interaction, preset_key: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        key = _slugify(preset_key)
        ok = await delete_ticket_panel_preset(guild.id, key)
        if not ok:
            return await reply_once(interaction, {"content": f"❌ Failed deleting preset `{key}`.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Deleted preset `{key}`.", "ephemeral": True})

    @tree.command(
        name="ticket_panel_apply_preset",
        description="Apply a preset to an existing panel.",
    )
    @app_commands.describe(
        panel_key="Panel key",
        preset_key="Preset key",
        overwrite_rules="Overwrite panel rules with preset defaults",
    )
    async def ticket_panel_apply_preset(
        interaction: discord.Interaction,
        panel_key: str,
        preset_key: str,
        overwrite_rules: bool = True,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        panel = _slugify(panel_key)
        preset = _slugify(preset_key)

        bundle = await apply_panel_preset_to_panel(
            guild_id=guild.id,
            panel_key=panel,
            preset_key=preset,
            overwrite_rules=bool(overwrite_rules),
        )

        if not bundle:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed applying preset `{preset}` to panel `{panel}`.", "ephemeral": True},
            )

        await reply_once(interaction, {"embed": _panel_embed(bundle), "ephemeral": True})

    # ========================================================
    # Diagnostics
    # ========================================================

    @tree.command(
        name="ticket_panel_runtime",
        description="Show the effective runtime config for a panel.",
    )
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

        panel = dict(runtime.get("panel") or {})
        rules = dict(runtime.get("rules") or {})
        categories = list(runtime.get("categories") or [])
        preset = dict(runtime.get("preset") or {})

        embed = discord.Embed(
            title=f"🧪 Panel Runtime: {key}",
            color=discord.Color.dark_teal(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Panel", value=_truncate(str(panel), 1000), inline=False)
        embed.add_field(name="Rules", value=_truncate(str(rules), 1000), inline=False)
        embed.add_field(name="Categories", value=_truncate(str(categories), 1000), inline=False)
        embed.add_field(name="Preset", value=_truncate(str(preset), 1000), inline=False)

        await reply_once(interaction, {"embed": embed, "ephemeral": True})
