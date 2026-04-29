from __future__ import annotations

"""
Interactive setup assistant for fresh public guilds.

This is the friendly layer on top of /stoney health:
- scans what the server is missing, including optional-but-important items like bot status
- offers a safe default setup for brand-new servers
- offers a calm custom setup path for owners who already have their own layout
- lets admins set the current channel as the bot-status channel with one button
- can create only the missing bot-status channel without touching the rest of the server
"""

import asyncio
import os
from typing import Any, Mapping, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _health_embed,
    _require_setup_permission,
    _upsert_config,
    _utc_iso,
    stoney_group,
)
from ..guild_config import get_guild_config, invalidate_guild_config


_ATTACHED = False

STATUS_CHANNEL_NAME = "📡・bot-status"
MANAGEMENT_CATEGORY_NAME = "🛠️ STAFF TOOLS"


def _short_lines(lines: list[str], *, limit: int = 900, empty: str = "✅ Nothing missing") -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        extra = len(text) + 1
        if total + extra > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) or empty


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _casefold(value: Any) -> str:
    try:
        return str(value or "").strip().casefold()
    except Exception:
        return ""


def _table_name() -> str:
    try:
        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _nested_settings(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        for key in ("settings", "config", "metadata", "meta"):
            value = row.get(key)
            if isinstance(value, Mapping):
                merged.update(dict(value))
        merged.update(dict(row))
    except Exception:
        try:
            merged.update(dict(row))
        except Exception:
            pass
    return merged


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return None
        res = (
            sb.table(_table_name())
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        return dict(row) if isinstance(row, Mapping) else None
    except Exception:
        return None


async def _fetch_config_row(guild_id: int) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_config_row_sync, int(guild_id))


def _status_channel_id_from_row(row: Optional[Mapping[str, Any]]) -> int:
    if not row:
        return 0
    data = _nested_settings(row)
    for key in ("status_channel_id", "bot_status_channel_id", "uptime_channel_id", "health_channel_id"):
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    return 0


def _control_role_id_from_row(row: Optional[Mapping[str, Any]]) -> int:
    if not row:
        return 0
    data = _nested_settings(row)
    for key in ("server_control_role_id", "control_role_id", "perm_role_id", "admin_role_id"):
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    return 0


def _text_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    target = _casefold(name)
    try:
        for channel in guild.text_channels:
            if _casefold(channel.name) == target:
                return channel
    except Exception:
        pass
    return None


def _category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    target = _casefold(name)
    try:
        for category in guild.categories:
            if _casefold(category.name) == target:
                return category
    except Exception:
        pass
    return None


def _status_channel_perms_missing(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
    missing: list[str] = []
    me = guild.me
    if me is None:
        return missing
    perms = channel.permissions_for(me)
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    if not perms.read_message_history:
        missing.append("Read Message History")
    return missing


async def _status_channel_health(guild: discord.Guild) -> tuple[bool, list[str], list[str]]:
    """Return (is_configured_and_writable, warnings, ok)."""
    warnings: list[str] = []
    ok: list[str] = []

    row = await _fetch_config_row(guild.id)
    status_channel_id = _status_channel_id_from_row(row)
    if status_channel_id <= 0:
        existing_named = _text_channel_by_name(guild, STATUS_CHANNEL_NAME)
        if existing_named is not None:
            warnings.append(f"Bot status channel exists as {existing_named.mention}, but it is not saved yet. Press **Use This Channel for Status** inside it, or press **Create Status Channel** to save/reuse it.")
        else:
            warnings.append("Bot status channel is missing. Press **Create Status Channel**, or go to the channel you want and press **Use This Channel for Status**.")
        return False, warnings, ok

    channel = guild.get_channel(status_channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(status_channel_id)
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        warnings.append(f"Bot status channel is configured but missing/not a text channel: `{status_channel_id}`. Press **Create Status Channel** to recreate it.")
        return False, warnings, ok

    missing = _status_channel_perms_missing(guild, channel)
    if missing:
        warnings.append(f"Bot status channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
        return False, warnings, ok

    ok.append(f"Bot status channel is configured and writable: {channel.mention}.")
    return True, warnings, ok


def _private_status_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
        )

    for role in (staff_role, control_role):
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
            )

    return overwrites


async def _create_or_reuse_status_channel(guild: discord.Guild) -> tuple[Optional[discord.TextChannel], str, list[str]]:
    """Create/reuse a bot-status channel without running full default setup."""
    notes: list[str] = []
    row = await _fetch_config_row(guild.id)
    cfg = await get_guild_config(guild.id, refresh=True)

    control_role_id = _control_role_id_from_row(row)
    staff_role_id = _safe_int(getattr(cfg, "staff_role_id", 0), 0)
    modlog_channel_id = _safe_int(getattr(cfg, "modlog_channel_id", 0), 0)

    control_role = guild.get_role(control_role_id) if control_role_id > 0 else None
    staff_role = guild.get_role(staff_role_id) if staff_role_id > 0 else None
    overwrites = _private_status_overwrites(guild, staff_role=staff_role, control_role=control_role)

    existing = _text_channel_by_name(guild, STATUS_CHANNEL_NAME)
    if existing is not None:
        try:
            await existing.edit(overwrites=overwrites, topic="Bot status and restored-service notices are posted here.", reason="Stoney setup assistant status channel refresh")
        except Exception as e:
            notes.append(f"Reused existing channel, but could not refresh permissions: {type(e).__name__}")
        return existing, "reused", notes

    category: Optional[discord.CategoryChannel] = None
    modlog_channel = guild.get_channel(modlog_channel_id) if modlog_channel_id > 0 else None
    if isinstance(modlog_channel, discord.TextChannel):
        category = modlog_channel.category

    if category is None:
        category = _category_by_name(guild, MANAGEMENT_CATEGORY_NAME)

    me = guild.me
    if me is None:
        notes.append("Bot member could not be resolved.")
        return None, "failed", notes

    if not me.guild_permissions.manage_channels:
        notes.append("Bot is missing Manage Channels, so it cannot create a bot-status channel.")
        return None, "failed", notes

    if category is None:
        try:
            category = await guild.create_category(
                name=MANAGEMENT_CATEGORY_NAME,
                overwrites=overwrites,
                reason="Stoney setup assistant created staff tools category for bot status",
            )
            notes.append(f"Created category `{MANAGEMENT_CATEGORY_NAME}`.")
        except Exception as e:
            notes.append(f"Could not create `{MANAGEMENT_CATEGORY_NAME}` category: {type(e).__name__}")
            return None, "failed", notes

    try:
        channel = await guild.create_text_channel(
            name=STATUS_CHANNEL_NAME,
            category=category,
            overwrites=overwrites,
            topic="Bot status and restored-service notices are posted here.",
            reason="Stoney setup assistant created bot status channel",
        )
        return channel, "created", notes
    except Exception as e:
        notes.append(f"Could not create `{STATUS_CHANNEL_NAME}`: {type(e).__name__}")
        return None, "failed", notes


def _custom_setup_summary() -> str:
    return (
        "You do **not** need to memorize a pile of commands.\n\n"
        "**Best custom path:** run `/stoney setup-picker` and choose your existing channels/roles from dropdowns.\n\n"
        "Use the manual setup commands only if a dropdown is annoying or Discord does not show what you need."
    )


async def _build_assistant_payload(guild: discord.Guild) -> tuple[discord.Embed, "SetupAssistantView"]:
    cfg = await get_guild_config(guild.id, refresh=True)
    blockers, warnings, ok = _build_setup_health(guild, cfg)

    status_ok, status_warnings, status_ok_lines = await _status_channel_health(guild)
    warnings.extend(status_warnings)
    ok.extend(status_ok_lines)

    ready_core = not blockers
    ready_full = ready_core and not warnings

    if ready_full:
        description = "✅ **Everything important looks ready.** You can re-check anytime or review custom setup options."
        color = discord.Color.green()
    elif ready_core:
        description = "✅ **Core setup is ready**, but a few nice-to-have items still need attention."
        color = discord.Color.gold()
    else:
        description = (
            "I found missing setup pieces. For a fresh server, use **Create Recommended Defaults**. "
            "For an existing server, use **Custom Setup** so nothing gets renamed or replaced without you choosing it."
        )
        color = discord.Color.blurple()

    embed = discord.Embed(
        title="🧭 Stoney Setup Assistant",
        description=description,
        color=color,
    )
    embed.add_field(name="Missing / Blockers", value=_short_lines(blockers), inline=False)
    embed.add_field(name="Warnings / Optional Fixes", value=_short_lines(warnings, empty="✅ None"), inline=False)
    if ok:
        embed.add_field(name="Already Working", value=_field_text(ok[:10], empty="Nothing checked yet."), inline=False)

    if not ready_core:
        recommended = "Fresh server: press **Create Recommended Defaults**. Existing/custom server: press **Custom Setup**."
    elif not status_ok:
        recommended = "Press **Create Status Channel** to make `📡・bot-status`, or press **Use This Channel for Status** in a channel you already want to use."
    else:
        recommended = "No required action. Use **Run Health Check** after changing roles/channels."

    embed.add_field(name="Recommended Next Step", value=recommended, inline=False)
    embed.set_footer(text=f"Guild {guild.id} • setup assistant")

    return embed, SetupAssistantView(core_ready=ready_core, status_ok=status_ok)


class SetupAssistantView(discord.ui.View):
    def __init__(self, *, core_ready: bool = False, status_ok: bool = False) -> None:
        super().__init__(timeout=300)
        self.core_ready = bool(core_ready)
        self.status_ok = bool(status_ok)

        # Avoid tempting owners to run the full default builder on already-customized servers.
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id and "create_defaults" in child.custom_id and self.core_ready:
                    child.disabled = True
                if child.custom_id and "use_current_status" in child.custom_id and self.status_ok:
                    child.disabled = True
                if child.custom_id and "create_status_channel" in child.custom_id and self.status_ok:
                    child.disabled = True

    async def _require(self, interaction: discord.Interaction) -> bool:
        return await _require_setup_permission(interaction)

    @discord.ui.button(
        label="Create Recommended Defaults",
        emoji="✨",
        style=discord.ButtonStyle.success,
        custom_id="stoney_setup_assistant:create_defaults",
        row=0,
    )
    async def create_defaults(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        try:
            from .public_setup_defaults import _setup_defaults_callback
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Default setup is unavailable right now: `{type(e).__name__}`",
                ephemeral=True,
            )
        await _setup_defaults_callback(
            interaction,
            control_role=None,
            staff_role=None,
            create_missing_roles=True,
            apply_channel_permissions=True,
        )

    @discord.ui.button(
        label="Create Status Channel",
        emoji="📡",
        style=discord.ButtonStyle.success,
        custom_id="stoney_setup_assistant:create_status_channel",
        row=0,
    )
    async def create_status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("❌ This button must be used inside a server.", ephemeral=True)

        channel, action, notes = await _create_or_reuse_status_channel(guild)
        if channel is None:
            embed = discord.Embed(
                title="🚫 Could Not Create Status Channel",
                description=_short_lines(notes, empty="Unknown error."),
                color=discord.Color.red(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            await _upsert_config(
                guild.id,
                {
                    "status_channel_id": str(int(channel.id)),
                    "configured_by_id": str(interaction.user.id),
                    "configured_by_name": str(interaction.user),
                    "configured_at": _utc_iso(),
                },
            )
            invalidate_guild_config(guild.id)
        except Exception as e:
            return await interaction.followup.send(f"❌ Created/reused {channel.mention}, but failed saving it: `{e}`", ephemeral=True)

        embed, view = await _build_assistant_payload(guild)
        prefix = "✅ Created" if action == "created" else "✅ Reused"
        extra = f"\nNotes:\n{_short_lines(notes, empty='None')}" if notes else ""
        await interaction.followup.send(f"{prefix} bot status channel: {channel.mention}.{extra}", embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="Custom Setup",
        emoji="🛠️",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_setup_assistant:custom_setup",
        row=1,
    )
    async def custom_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        embed = discord.Embed(
            title="🛠️ Custom Setup",
            description=_custom_setup_summary(),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Start here",
            value="Run `/stoney setup-picker` to select existing roles/channels with dropdowns.",
            inline=False,
        )
        embed.add_field(
            name="When you only need one specific thing",
            value=(
                "`/stoney setup-status` → bot online/status channel\n"
                "`/stoney setup-logs` → mod/security/join logs\n"
                "`/stoney setup-tickets` → ticket categories/staff/transcripts\n"
                "`/stoney setup-verify` → verify channel/roles/VC verify"
            ),
            inline=False,
        )
        embed.add_field(
            name="Safe choice",
            value="If this server already has its own layout, use custom setup instead of recommended defaults.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Use This Channel for Status",
        emoji="📌",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_setup_assistant:use_current_status",
        row=1,
    )
    async def use_current_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Use this button inside the text channel you want as the bot-status channel.", ephemeral=True)

        missing = _status_channel_perms_missing(guild, channel)
        if missing:
            return await interaction.followup.send(
                f"🚫 I cannot use {channel.mention} for status yet. Missing: {', '.join(missing)}.",
                ephemeral=True,
            )

        try:
            await _upsert_config(
                guild.id,
                {
                    "status_channel_id": str(int(channel.id)),
                    "configured_by_id": str(interaction.user.id),
                    "configured_by_name": str(interaction.user),
                    "configured_at": _utc_iso(),
                },
            )
            invalidate_guild_config(guild.id)
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed saving bot status channel: `{e}`", ephemeral=True)

        embed, view = await _build_assistant_payload(guild)
        await interaction.followup.send(f"✅ Bot status reports will use {channel.mention}.", embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="Run Health Check",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_setup_assistant:run_health",
        row=1,
    )
    async def run_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed, view = await _build_assistant_payload(guild)
        health = _health_embed(guild, cfg)
        health.add_field(name="Assistant Notes", value="Use the assistant buttons below for status/custom/default setup fixes.", inline=False)
        await interaction.followup.send(embed=health, view=view, ephemeral=True)


async def _setup_assistant_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    embed, view = await _build_assistant_payload(guild)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = stoney_group.get_command("setup-assistant")
    except Exception:
        existing = None

    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-assistant",
        description="Show missing setup pieces and choose automatic or custom setup.",
        callback=_setup_assistant_callback,
    )
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_assistant_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_assistant: attached /stoney setup-assistant command")
    except Exception:
        pass


__all__ = ["register_public_setup_assistant_commands"]
