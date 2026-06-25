from __future__ import annotations

from typing import Any, Dict, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _add_validation_summary,
    _channel_value,
    _config_embed,
    _require_setup_permission,
    _upsert_config,
    _utc_iso,
    _validate_log_setup,
    dank_group,
)
from ..guild_config import get_guild_config, invalidate_guild_config


# ============================================================
# public_setup_logs.py
# ------------------------------------------------------------
# Adds /dank setup-logs to the existing public /dank group.
#
# Production goals:
# - log-channel config is saved per guild_id, not env
# - modlog is required because moderation/audit visibility depends on it
# - raid/security, join/exit, and force-verify logs are optional and may reuse
#   modlog, but every chosen channel is permission-validated before saving
# - join/exit intentionally maps to join_log_channel_id so existing runtime code
#   and the newer welcome-exit use case share one durable setting
# - registering this module also enables per-guild join/exit log listeners
# ============================================================


_SETUP_LOGS_ATTACHED = False


def _safe_channel_id(channel: Optional[discord.TextChannel]) -> Optional[str]:
    return _channel_value(channel) if channel is not None else None


def _display_channel(channel: Optional[discord.TextChannel], *, fallback: discord.TextChannel) -> str:
    chosen = channel or fallback
    try:
        return f"{chosen.mention} (`{chosen.id}`)"
    except Exception:
        return "Not set"


async def _setup_logs_callback(
    interaction: discord.Interaction,
    modlog_channel: discord.TextChannel,
    raidlog_channel: Optional[discord.TextChannel] = None,
    welcome_exit_channel: Optional[discord.TextChannel] = None,
    force_verify_log_channel: Optional[discord.TextChannel] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    blockers, warnings, ok = _validate_log_setup(
        guild,
        modlog_channel,
        raidlog_channel,
        welcome_exit_channel,
        force_verify_log_channel,
    )

    if blockers:
        embed = discord.Embed(
            title="🚫 Log Setup Blocked",
            description="Setup was not saved because at least one selected log channel is not usable by the bot.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Blockers", value="\n".join(blockers)[:1024] or "Unknown blocker.", inline=False)
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(warnings)[:1024], inline=False)
        if ok:
            embed.add_field(name="Passing Checks", value="\n".join(ok)[:1024], inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    updates: Dict[str, Any] = {
        "modlog_channel_id": _channel_value(modlog_channel),
        "raidlog_channel_id": _safe_channel_id(raidlog_channel) or _channel_value(modlog_channel),
        "join_leave_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "join_leave_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "member_join_leave_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "member_lifecycle_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "join_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "join_exit_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "joinlog_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "joinleave_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "welcome_exit_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "welcome_exit_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "leave_log_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "leave_channel_id": _safe_channel_id(welcome_exit_channel) or _channel_value(modlog_channel),
        "force_verify_log_channel_id": _safe_channel_id(force_verify_log_channel) or _channel_value(modlog_channel),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving log setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Log Setup Saved")
    embed.add_field(
        name="Saved Log Routing",
        value=(
            f"Modlog: {_display_channel(modlog_channel, fallback=modlog_channel)}\n"
            f"Raid/security log: {_display_channel(raidlog_channel, fallback=modlog_channel)}\n"
            f"Join/exit / welcome-exit log: {_display_channel(welcome_exit_channel, fallback=modlog_channel)}\n"
            f"Force-verify log: {_display_channel(force_verify_log_channel, fallback=modlog_channel)}"
        ),
        inline=False,
    )
    _add_validation_summary(embed, warnings, ok)
    await interaction.followup.send(embed=embed, ephemeral=True)


def _attach_setup_logs_command() -> None:
    global _SETUP_LOGS_ATTACHED
    if _SETUP_LOGS_ATTACHED:
        return

    try:
        existing = dank_group.get_command("setup-logs")
    except Exception:
        existing = None

    if existing is not None:
        _SETUP_LOGS_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-logs",
        description="Configure modlog, raid/security, join/exit, and force-verify log channels.",
        callback=_setup_logs_callback,
    )

    try:
        command._params["modlog_channel"].description = "Main moderation log channel. Required."
        command._params["raidlog_channel"].description = "Optional raid/security log channel. Defaults to modlog."
        command._params["welcome_exit_channel"].description = "Optional join/exit channel, such as #welcome-exit. Defaults to modlog."
        command._params["force_verify_log_channel"].description = "Optional forced-verification log channel. Defaults to modlog."
    except Exception:
        pass

    dank_group.add_command(command)
    _SETUP_LOGS_ATTACHED = True


_attach_setup_logs_command()


def _register_member_lifecycle_listeners(bot, tree) -> None:
    try:
        from .public_member_lifecycle_logs import register_public_member_lifecycle_log_listeners

        register_public_member_lifecycle_log_listeners(bot, tree)
    except Exception as e:
        try:
            print(f"⚠️ public_setup_logs: failed registering join/exit log listeners: {repr(e)}")
        except Exception:
            pass


def register_public_setup_logs_commands(bot, tree) -> None:
    _attach_setup_logs_command()
    _register_member_lifecycle_listeners(bot, tree)
    try:
        print("✅ public_setup_logs: attached /dank setup-logs command")
    except Exception:
        pass


__all__ = ["register_public_setup_logs_commands"]
