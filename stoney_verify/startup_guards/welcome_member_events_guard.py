from __future__ import annotations

"""Optional per-guild welcome/goodbye event automation.

This does not replace the static /dank welcome start-here message. It adds
ProBot-style join/leave messages when guild owners enable them in config.
Default behavior is safe: disabled unless explicitly enabled by setup/commands.
"""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

_PATCHED = False


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_str(cfg: Any, *keys: str, default: str = "") -> str:
    for key in keys:
        try:
            text = str(_cfg_value(cfg, key, "") or "").strip()
            if text:
                return text
        except Exception:
            continue
    return default


def _cfg_bool(cfg: Any, *keys: str, default: bool = False) -> bool:
    for key in keys:
        raw = _cfg_value(cfg, key, None)
        if raw is not None:
            return _safe_bool(raw, default)
    return bool(default)


def _clean_name(value: Any) -> str:
    return str(value or "").lower().replace("_", "-").replace(" ", "-")


def _text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(channel_id or 0)) if int(channel_id or 0) > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(_clean_name(token) for token in tokens if str(token or "").strip())
    if not wanted:
        return None
    for channel in list(getattr(guild, "text_channels", []) or []):
        if not isinstance(channel, discord.TextChannel):
            continue
        name = _clean_name(getattr(channel, "name", ""))
        if any(token in name for token in wanted):
            return channel
    return None


def _target_channel(guild: discord.Guild, cfg: Any, *, kind: str) -> Optional[discord.TextChannel]:
    if kind == "leave":
        cid = _safe_int(_cfg_value(cfg, "goodbye_channel_id", None) or _cfg_value(cfg, "leave_channel_id", None) or _cfg_value(cfg, "welcome_channel_id", None), 0)
        return _text_channel(guild, cid) or _channel_by_name(guild, "goodbye", "farewell", "welcome")
    cid = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None) or _cfg_value(cfg, "welcome_channel_id", None), 0)
    return _text_channel(guild, cid) or _channel_by_name(guild, "welcome", "start-here")


def _format(text: str, member: discord.Member) -> str:
    guild = member.guild
    replacements = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": member.mention,
        "user": member.mention,
        "username": str(member),
        "member_count": str(getattr(guild, "member_count", "") or ""),
    }
    out = str(text or "")
    for key, value in replacements.items():
        out = out.replace("{" + key + "}", value)
    return out[:1900]


def _embed(title: str, body: str, member: discord.Member, *, goodbye: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=_format(title, member)[:256],
        description=_format(body, member)[:4000],
        color=discord.Color.dark_grey() if goodbye else discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="dank_shield:welcome_event:v1")
    return embed


async def _send_join(member: discord.Member) -> None:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not _cfg_bool(cfg, "welcome_join_enabled", "join_welcome_enabled", default=False):
            return
        channel = _target_channel(member.guild, cfg, kind="join")
        if not isinstance(channel, discord.TextChannel):
            return
        title = _cfg_str(cfg, "welcome_join_title", default="👋 Welcome, {username}!")
        body = _cfg_str(cfg, "welcome_join_body", default="Welcome to **{server_name}**, {member}! Head to the start-here channels to get settled.")
        await channel.send(embed=_embed(title, body, member), allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events join failed guild={getattr(getattr(member, 'guild', None), 'id', 0)} user={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass


async def _send_leave(member: discord.Member) -> None:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not _cfg_bool(cfg, "welcome_leave_enabled", "goodbye_enabled", "leave_message_enabled", default=False):
            return
        channel = _target_channel(member.guild, cfg, kind="leave")
        if not isinstance(channel, discord.TextChannel):
            return
        title = _cfg_str(cfg, "welcome_leave_title", default="👋 {username} left")
        body = _cfg_str(cfg, "welcome_leave_body", default="{username} left **{server_name}**. Member count: {member_count}.")
        await channel.send(embed=_embed(title, body, member, goodbye=True), allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events leave failed guild={getattr(getattr(member, 'guild', None), 'id', 0)} user={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify import client as bot  # type: ignore
    except Exception:
        try:
            from stoney_verify.globals import bot  # type: ignore
        except Exception:
            bot = None  # type: ignore
    if bot is None:
        return False
    try:
        bot.add_listener(_send_join, "on_member_join")
        bot.add_listener(_send_leave, "on_member_remove")
        _PATCHED = True
        print("✅ welcome_member_events_guard active; optional join/leave messages attached")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
