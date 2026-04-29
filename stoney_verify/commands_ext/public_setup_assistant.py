from __future__ import annotations

"""
Interactive setup assistant for fresh public guilds.

This is the friendly layer on top of /stoney health:
- scans what the server is missing, including optional-but-important items like bot status
- offers a safe default setup for brand-new servers
- offers a calm custom setup path for owners who already have their own layout
- lets admins set the current channel as the bot-status channel with one button
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


async def _status_channel_health(guild: discord.Guild) -> tuple[bool, list[str], list[str]]:
    """Return (is_configured_and_writable, warnings, ok)."""
    warnings: list[str] = []
    ok: list[str] = []

    row = await _fetch_config_row(guild.id)
    status_channel_id = _status_channel_id_from_row(row)
    if status_channel_id <= 0:
        warnings.append("Bot status channel is not set. Press **Use This Channel for Status** or run `/stoney setup-status`.")
        return False, warnings, ok

    channel = guild.get_channel(status_channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(status_channel_id)
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        warnings.append(f"Bot status channel is configured but missing/not a text channel: `{status_channel_id}`.")
        return False, warnings, ok

    me = guild.me
    if me is not None:
        perms = channel.permissions_for(me)
        missing: list[str] = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if not perms.read_message_history:
            missing.append("Read Message History")
        if missing:
            warnings.append(f"Bot status channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
            return False, warnings, ok

    ok.append(f"Bot status channel is configured and writable: {channel.mention}.")
    return True, warnings, ok


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
        recommended = "Press **Use This Channel for Status** here in your preferred status/log channel, or run `/stoney setup-status`."
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

    async def _require(self, interaction: discord.Interaction) -> bool:
        return await _require_setup_permission(interaction)

    @discord.ui.button(
        label="Create Recommended Defaults",
        emoji="✨",
        style=discord.ButtonStyle.success,
        custom_id="stoney_setup_assistant:create_defaults",
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
        label="Custom Setup",
        emoji="🛠️",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_setup_assistant:custom_setup",
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
        emoji="📡",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_setup_assistant:use_current_status",
    )
    async def use_current_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Use this button inside the text channel you want as the bot-status channel.", ephemeral=True)

        me = guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            missing: list[str] = []
            if not perms.view_channel:
                missing.append("View Channel")
            if not perms.send_messages:
                missing.append("Send Messages")
            if not perms.embed_links:
                missing.append("Embed Links")
            if not perms.read_message_history:
                missing.append("Read Message History")
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
