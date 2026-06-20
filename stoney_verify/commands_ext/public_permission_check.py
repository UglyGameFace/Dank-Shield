from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _safe_str,
    dank_group,
)
from ..guild_config import get_guild_config


# ============================================================
# public_permission_check.py
# ------------------------------------------------------------
# Audit #6 / P0: production setup permission visibility.
#
# Why this exists:
# - app.py's legacy startup self-check validates broad bot permissions and a
#   single env/modlog view.
# - public production needs the same visibility against per-guild DB config:
#   open ticket category, archive ticket category, transcript channel,
#   verify/log channels, and manageable roles.
#
# This module stays inside the public grouped command surface:
# - /dank permission-check is read-only.
# - An on_ready background check prints per-guild findings and optionally posts
#   findings to the configured modlog channel.
# - It does not rely on one env GUILD_ID.
# ============================================================


_PERMISSION_COMMAND_ATTACHED = False
_STARTUP_LISTENER_ATTACHED = False
_STARTUP_RUN_LOCK = asyncio.Lock()
_STARTUP_CHECKED_GUILDS: set[int] = set()


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float = 0.0) -> float:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _truncate_lines(lines: list[str], limit: int = 8) -> list[str]:
    if len(lines) <= limit:
        return list(lines)
    return list(lines[:limit]) + [f"…and {len(lines) - limit} more"]


def _health_status(blockers: list[str], warnings: list[str]) -> tuple[str, discord.Color]:
    if blockers:
        return "blocked", discord.Color.red()
    if warnings:
        return "warnings", discord.Color.gold()
    return "ready", discord.Color.green()


def _status_description(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return "🚫 **Not production-ready.** Fix blockers before relying on ticket/verification automation."
    if warnings:
        return "⚠️ **Usable with warnings.** Safe to test, but review these before public rollout."
    return "✅ **Ready.** No setup permission blockers or warnings were found."


def _channel_name(guild: discord.Guild, channel_id: int) -> str:
    try:
        ch = guild.get_channel(int(channel_id or 0))
        if ch is not None:
            return f"#{getattr(ch, 'name', ch.id)}"
    except Exception:
        pass
    return "not configured"


def _modlog_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    try:
        channel_id = int(getattr(cfg, "modlog_channel_id", 0) or 0)
        if channel_id <= 0:
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None
    except Exception:
        return None


def _permission_check_embed(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> discord.Embed:
    status, color = _health_status(blockers, warnings)
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
    embed = discord.Embed(
        title="🩺 Dank Shield Runtime Permission Check",
        description=(
            f"{_status_description(blockers, warnings)}\n\n"
            f"Status: `{status}`\n"
            f"Config source: `{source}`\n"
            f"Guild: `{guild.id}`"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.add_field(
        name="Configured Destinations",
        value=(
            f"Open tickets: `{_channel_name(guild, int(getattr(cfg, 'ticket_category_id', 0) or 0))}`\n"
            f"Closed/archive: `{_channel_name(guild, int(getattr(cfg, 'ticket_archive_category_id', 0) or 0))}`\n"
            f"Transcripts: `{_channel_name(guild, int(getattr(cfg, 'transcripts_channel_id', 0) or 0))}`\n"
            f"Modlog: `{_channel_name(guild, int(getattr(cfg, 'modlog_channel_id', 0) or 0))}`\n"
            f"Join/exit: `{_channel_name(guild, int(getattr(cfg, 'join_log_channel_id', 0) or 0))}`"
        )[:1024],
        inline=False,
    )
    embed.set_footer(text="Read-only check. No server config was changed.")
    return embed


def _print_health(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    try:
        status, _ = _health_status(blockers, warnings)
        print(
            "🔎 Runtime setup permission self-check: "
            f"guild={guild.id} status={status} source={_safe_str(getattr(cfg, 'source', 'unknown'), 'unknown')} "
            f"blockers={len(blockers)} warnings={len(warnings)} ok={len(ok)}"
        )
        for line in _truncate_lines(blockers):
            print(f"   - BLOCKER: {line}")
        for line in _truncate_lines(warnings):
            print(f"   - WARNING: {line}")
        for line in _truncate_lines(ok, limit=5):
            print(f"   - OK: {line}")
    except Exception:
        pass


async def _post_health_if_needed(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    try:
        post_modlog = _env_bool("DANK_SETUP_HEALTH_POST_MODLOG", True)
        post_ok = _env_bool("DANK_SETUP_HEALTH_POST_OK", False)
        if not post_modlog:
            return
        if not blockers and not warnings and not post_ok:
            return

        channel = _modlog_channel(guild, cfg)
        if channel is None:
            return

        me = guild.me
        if isinstance(me, discord.Member):
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                print(f"⚠️ Runtime setup permission self-check could not post to modlog guild={guild.id}: missing channel perms")
                return

        await channel.send(embed=_permission_check_embed(guild, cfg, blockers, warnings, ok))
    except Exception as e:
        try:
            print(f"⚠️ Runtime setup permission self-check modlog post failed guild={guild.id}: {repr(e)}")
        except Exception:
            pass


async def _run_guild_permission_check(guild: discord.Guild, *, refresh: bool = True) -> tuple[Any, list[str], list[str], list[str]]:
    cfg = await get_guild_config(guild.id, refresh=refresh)
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    return cfg, blockers, warnings, ok


async def _run_startup_permission_checks(bot: Any) -> None:
    if not _env_bool("DANK_SETUP_HEALTH_ON_READY", True):
        return

    async with _STARTUP_RUN_LOCK:
        try:
            max_guilds = max(1, _env_int("DANK_SETUP_HEALTH_MAX_GUILDS_PER_READY", 50))
            guilds = list(getattr(bot, "guilds", []) or [])[:max_guilds]
            if not guilds:
                print("ℹ️ Runtime setup permission self-check skipped: no cached guilds yet.")
                return

            for guild in guilds:
                try:
                    gid = int(guild.id)
                    if gid in _STARTUP_CHECKED_GUILDS and not _env_bool("DANK_SETUP_HEALTH_REPEAT_ON_READY", False):
                        continue
                    _STARTUP_CHECKED_GUILDS.add(gid)

                    cfg, blockers, warnings, ok = await _run_guild_permission_check(guild, refresh=True)
                    _print_health(guild, cfg, blockers, warnings, ok)
                    await _post_health_if_needed(guild, cfg, blockers, warnings, ok)
                except Exception as e:
                    print(f"⚠️ Runtime setup permission self-check failed guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
        except Exception as e:
            print(f"⚠️ Runtime setup permission self-check runner failed: {repr(e)}")


async def _permission_check_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg, blockers, warnings, ok = await _run_guild_permission_check(guild, refresh=True)
        await interaction.followup.send(
            embeds=[
                _permission_check_embed(guild, cfg, blockers, warnings, ok),
                _config_embed(guild, cfg, title="📌 Current Saved Config"),
            ],
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Permission check failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach_permission_check_command() -> None:
    global _PERMISSION_COMMAND_ATTACHED
    if _PERMISSION_COMMAND_ATTACHED:
        return

    try:
        existing = dank_group.get_command("permission-check")
    except Exception:
        existing = None

    if existing is not None:
        _PERMISSION_COMMAND_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="permission-check",
        description="Check saved channels, categories, and role hierarchy without changing anything.",
        callback=_permission_check_callback,
    )
    dank_group.add_command(command)
    _PERMISSION_COMMAND_ATTACHED = True


def _attach_startup_listener(bot: Any) -> None:
    global _STARTUP_LISTENER_ATTACHED
    if _STARTUP_LISTENER_ATTACHED:
        return

    async def _on_ready_setup_health() -> None:
        delay = max(0.0, _env_float("DANK_SETUP_HEALTH_READY_DELAY_SECONDS", 8.0))
        if delay:
            await asyncio.sleep(delay)
        await _run_startup_permission_checks(bot)

    try:
        bot.listen("on_ready")(_on_ready_setup_health)
        _STARTUP_LISTENER_ATTACHED = True
    except Exception as e:
        print(f"⚠️ public_permission_check failed attaching on_ready listener: {repr(e)}")


_attach_permission_check_command()


def register_public_permission_check_commands(bot: Any, tree: Any) -> None:
    _ = tree
    _attach_permission_check_command()
    _attach_startup_listener(bot)
    try:
        print("✅ public_permission_check: attached /dank permission-check command + startup setup health listener")
    except Exception:
        pass


__all__ = ["register_public_permission_check_commands"]
