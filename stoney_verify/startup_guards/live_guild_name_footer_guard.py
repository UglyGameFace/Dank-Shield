from __future__ import annotations

"""Keep public panel footers using the live Discord guild name.

Embeds already posted to Discord do not update automatically when a server is
renamed. This guard refreshes saved public ticket panels on startup and after a
guild rename so old saved/server names do not stay visible forever.
"""

import asyncio
from typing import Any

import discord

_PATCHED = False
_READY_RAN = False


def _log(message: str) -> None:
    try:
        print(f"✅ live_guild_name_footer_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ live_guild_name_footer_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, *names: str) -> Any:
    for name in names:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(name)
                if value not in (None, "", 0, "0"):
                    return value
        except Exception:
            pass
        try:
            value = getattr(cfg, name, None)
            if value not in (None, "", 0, "0"):
                return value
        except Exception:
            pass
        for bucket in ("settings", "config", "metadata", "meta"):
            try:
                nested = getattr(cfg, bucket, None)
                if isinstance(nested, dict) and nested.get(name) not in (None, "", 0, "0"):
                    return nested.get(name)
            except Exception:
                pass
    return None


async def _load_cfg(guild: discord.Guild) -> Any:
    from stoney_verify.guild_config import get_guild_config

    return await get_guild_config(int(guild.id), refresh=True)


def _expected_ticket_footer(guild: discord.Guild) -> str:
    name = str(getattr(guild, "name", "This server") or "This server").strip() or "This server"
    return f"{name} • Dank Shield ticket panel • category-menu"


async def refresh_ticket_panel_footer(guild: discord.Guild, *, reason: str = "live guild name refresh") -> bool:
    try:
        from stoney_verify.commands_ext import public_ticket_panel_clean as panel

        cfg = await _load_cfg(guild)
        channel_id = _safe_int(_cfg_value(cfg, "ticket_panel_channel_id", "support_channel_id", "ticket_support_channel_id"), 0)
        message_id = _safe_int(_cfg_value(cfg, "ticket_panel_message_id", "support_message_id", "ticket_support_message_id"), 0)
        if channel_id <= 0 or message_id <= 0:
            return False

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            return False

        expected = _expected_ticket_footer(guild)
        current = ""
        try:
            first = list(getattr(message, "embeds", []) or [None])[0]
            current = str(getattr(getattr(first, "footer", None), "text", "") or "")
        except Exception:
            current = ""
        if current == expected:
            return True

        await message.edit(embed=panel._panel_embed(guild), view=panel.PublicCreateTicketPanelView())
        _log(f"refreshed ticket panel footer guild={guild.id} channel={channel.id} message={message.id} reason={reason}")
        return True
    except Exception as exc:
        _warn(f"ticket panel footer refresh failed guild={getattr(guild, 'id', 'unknown')}: {type(exc).__name__}: {exc}")
        return False


async def _refresh_all(bot: Any) -> None:
    try:
        for guild in list(getattr(bot, "guilds", []) or []):
            if isinstance(guild, discord.Guild):
                await refresh_ticket_panel_footer(guild, reason="startup")
                await asyncio.sleep(0.2)
    except Exception as exc:
        _warn(f"startup footer refresh sweep failed: {type(exc).__name__}: {exc}")


async def _on_ready() -> None:
    global _READY_RAN
    if _READY_RAN:
        return
    _READY_RAN = True
    try:
        from stoney_verify.globals import bot
        await _refresh_all(bot)
    except Exception as exc:
        _warn(f"on_ready refresh failed: {type(exc).__name__}: {exc}")


async def _on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    try:
        if str(getattr(before, "name", "")) != str(getattr(after, "name", "")):
            await refresh_ticket_panel_footer(after, reason="guild rename")
    except Exception as exc:
        _warn(f"guild update footer refresh failed guild={getattr(after, 'id', 'unknown')}: {type(exc).__name__}: {exc}")


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.globals import bot

        existing = getattr(bot, "extra_events", {}) or {}
        ready_events = list(existing.get("on_ready") or []) if isinstance(existing, dict) else []
        update_events = list(existing.get("on_guild_update") or []) if isinstance(existing, dict) else []
        if not any(getattr(fn, "__module__", "") == __name__ and getattr(fn, "__name__", "") == "_on_ready" for fn in ready_events):
            bot.add_listener(_on_ready, "on_ready")
        if not any(getattr(fn, "__module__", "") == __name__ and getattr(fn, "__name__", "") == "_on_guild_update" for fn in update_events):
            bot.add_listener(_on_guild_update, "on_guild_update")
        _PATCHED = True
        _log("active; saved ticket panel footers refresh from live guild names")
        return True
    except Exception as exc:
        _warn(f"failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply", "refresh_ticket_panel_footer"]