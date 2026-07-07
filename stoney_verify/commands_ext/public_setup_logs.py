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
# - raid/security and force-verify logs may reuse modlog when omitted
# - join/leave/member lifecycle logs are explicit: no welcome-channel fallback
# - every chosen channel is permission-validated before saving
# - listener registration stays in public_member_lifecycle_runtime only
# ============================================================


_SETUP_LOGS_ATTACHED = False


def _safe_channel_id(channel: Optional[discord.TextChannel]) -> Optional[str]:
    return _channel_value(channel) if channel is not None else None


def _display_channel(channel: Optional[discord.TextChannel], *, fallback: Optional[discord.TextChannel] = None) -> str:
    chosen = channel or fallback
    if chosen is None:
        return "Not set"
    try:
        return f"{chosen.mention} (`{chosen.id}`)"
    except Exception:
        return "Not set"


def _join_leave_alias_payload(channel_id: str) -> Dict[str, Any]:
    return {
        "join_leave_log_channel_id": channel_id,
        "join_leave_channel_id": channel_id,
        "member_join_leave_log_channel_id": channel_id,
        "member_lifecycle_log_channel_id": channel_id,
        "member_log_channel_id": channel_id,
        "member_logs_channel_id": channel_id,
        "join_log_channel_id": channel_id,
        "join_exit_log_channel_id": channel_id,
        "joinlog_channel_id": channel_id,
        "joinleave_channel_id": channel_id,
        "welcome_exit_channel_id": channel_id,
        "welcome_exit_log_channel_id": channel_id,
        "leave_log_channel_id": channel_id,
        "leave_channel_id": channel_id,
        "welcome_leave_channel_id": channel_id,
    }


def _normalize_join_leave_warning(text: str) -> str:
    if "Join/exit log channel was not set" in str(text):
        return "Join/leave log channel was not set. Join/leave event posting will stay off until a real join/leave log channel is configured."
    return str(text)


async def _setup_logs_callback(
    interaction: discord.Interaction,
    modlog_channel: discord.TextChannel,
    raidlog_channel: Optional[discord.TextChannel] = None,
    join_leave_log_channel: Optional[discord.TextChannel] = None,
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
        join_leave_log_channel,
        force_verify_log_channel,
    )
    warnings = [_normalize_join_leave_warning(w) for w in warnings]

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
        "force_verify_log_channel_id": _safe_channel_id(force_verify_log_channel) or _channel_value(modlog_channel),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    if join_leave_log_channel is not None:
        updates.update(_join_leave_alias_payload(_channel_value(join_leave_log_channel) or "0"))

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving log setup: `{e}`", ephemeral=True)

    join_leave_display = _display_channel(join_leave_log_channel) if join_leave_log_channel is not None else "Unchanged / not set"

    embed = _config_embed(guild, cfg, title="✅ Log Setup Saved")
    embed.add_field(
        name="Saved Log Routing",
        value=(
            f"Modlog: {_display_channel(modlog_channel)}\n"
            f"Raid/security log: {_display_channel(raidlog_channel, fallback=modlog_channel)}\n"
            f"Join/leave log: {join_leave_display}\n"
            f"Force-verify log: {_display_channel(force_verify_log_channel, fallback=modlog_channel)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Welcome Channel Safety",
        value="Join/leave event logs never fall back to the public welcome channel. Set `join_leave_log_channel` when you want join/leave cards posted.",
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
        description="Configure modlog, raid/security, join/leave, and force-verify log channels.",
        callback=_setup_logs_callback,
    )

    try:
        command._params["modlog_channel"].description = "Main moderation log channel. Required."
        command._params["raidlog_channel"].description = "Optional raid/security log channel. Defaults to modlog."
        command._params["join_leave_log_channel"].description = "Optional explicit join/leave member log channel. Never falls back to welcome."
        command._params["force_verify_log_channel"].description = "Optional forced-verification log channel. Defaults to modlog."
    except Exception:
        pass

    dank_group.add_command(command)
    _SETUP_LOGS_ATTACHED = True


_attach_setup_logs_command()


def _register_member_lifecycle_listeners(bot, tree) -> None:
    _ = bot, tree
    # The authoritative router is installed by public_member_lifecycle_runtime.
    # Registering legacy listeners here would duplicate join/leave posts.
    return None


def register_public_setup_logs_commands(bot, tree) -> None:
    _attach_setup_logs_command()
    _register_member_lifecycle_listeners(bot, tree)
    try:
        print("✅ public_setup_logs: attached /dank setup-logs command")
    except Exception:
        pass


__all__ = ["register_public_setup_logs_commands"]
