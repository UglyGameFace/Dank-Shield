from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping, Optional

import discord

from .guild_config import get_guild_config
from .welcome_card_renderer import build_welcome_card_file, normalize_theme_name

_RECENT_SENDS: dict[tuple[int, int], float] = {}
_SEND_LOCK = asyncio.Lock()


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
    return default


def _cfg_bool(cfg: Any, *keys: str, default: bool = False) -> bool:
    for key in keys:
        value = _cfg_value(cfg, key, None)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return bool(default)


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            value = _cfg_value(cfg, key, None)
            if value is None or isinstance(value, bool):
                continue
            parsed = int(str(value).strip())
            if parsed > 0:
                return parsed
        except Exception:
            continue
    return 0


def welcome_cards_enabled(cfg: Any) -> bool:
    return _cfg_bool(cfg, "welcome_card_enabled", "join_welcome_card_enabled", default=False)


def configured_theme(cfg: Any) -> str:
    return normalize_theme_name(_cfg_value(cfg, "welcome_card_theme", "neon_pulse"))


def resolve_welcome_card_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    channel_id = _cfg_int(cfg, "join_welcome_channel_id", "welcome_card_channel_id", "welcome_channel_id")
    if channel_id <= 0:
        return None
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def welcome_card_permission_problems(channel: Optional[discord.TextChannel]) -> list[str]:
    if not isinstance(channel, discord.TextChannel):
        return ["Welcome card channel is not configured"]
    member = channel.guild.me
    if not isinstance(member, discord.Member):
        return ["Dank Shield could not resolve its server member"]
    perms = channel.permissions_for(member)
    checks = {
        "View Channel": bool(perms.view_channel),
        "Send Messages": bool(perms.send_messages),
        "Embed Links": bool(perms.embed_links),
        "Attach Files": bool(perms.attach_files),
    }
    return [name for name, ok in checks.items() if not ok]


async def _reserve(guild_id: int, member_id: int, window_seconds: float = 60.0) -> bool:
    key = (int(guild_id), int(member_id))
    now = time.monotonic()
    async with _SEND_LOCK:
        cutoff = now - max(120.0, window_seconds * 2)
        for existing, seen_at in list(_RECENT_SENDS.items()):
            if seen_at < cutoff:
                _RECENT_SENDS.pop(existing, None)
        previous = _RECENT_SENDS.get(key)
        if previous is not None and now - previous < window_seconds:
            return False
        _RECENT_SENDS[key] = now
    return True


async def _release(guild_id: int, member_id: int) -> None:
    async with _SEND_LOCK:
        _RECENT_SENDS.pop((int(guild_id), int(member_id)), None)


def _fallback_embed(member: discord.Member, cfg: Any) -> discord.Embed:
    count = int(getattr(member.guild, "member_count", 0) or 0)
    embed = discord.Embed(
        title=f"Welcome, {member.display_name}!",
        description=f"Welcome to **{member.guild.name}**. You are member **#{count}**.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text=f"Dank Shield welcome • {configured_theme(cfg)}")
    return embed


async def send_member_welcome_card(member: discord.Member) -> bool:
    if getattr(member, "bot", False):
        return False

    cfg = await get_guild_config(int(member.guild.id), refresh=True)
    if not welcome_cards_enabled(cfg):
        return False

    channel = resolve_welcome_card_channel(member.guild, cfg)
    if not isinstance(channel, discord.TextChannel):
        print(f"⚠️ welcome card skipped guild={member.guild.id} member={member.id}: channel not configured")
        return False

    if not await _reserve(member.guild.id, member.id):
        return False

    problems = welcome_card_permission_problems(channel)
    try:
        if not problems:
            file = await build_welcome_card_file(member, cfg)
            await channel.send(
                content=member.mention,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            return True

        me = channel.guild.me
        if isinstance(me, discord.Member):
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages and perms.embed_links:
                await channel.send(
                    content=member.mention,
                    embed=_fallback_embed(member, cfg),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                print(
                    f"⚠️ welcome card used embed fallback guild={member.guild.id} member={member.id} "
                    f"missing={','.join(problems)}"
                )
                return True

        print(
            f"⚠️ welcome card skipped guild={member.guild.id} member={member.id} "
            f"missing={','.join(problems)}"
        )
        await _release(member.guild.id, member.id)
        return False
    except Exception as exc:
        try:
            await channel.send(
                content=member.mention,
                embed=_fallback_embed(member, cfg),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            print(
                f"⚠️ welcome card render failed; embed fallback sent guild={member.guild.id} "
                f"member={member.id} error={type(exc).__name__}: {exc}"
            )
            return True
        except Exception as fallback_exc:
            await _release(member.guild.id, member.id)
            print(
                f"⚠️ welcome card failed guild={member.guild.id} member={member.id} "
                f"render={type(exc).__name__}: {exc} fallback={type(fallback_exc).__name__}: {fallback_exc}"
            )
            return False


__all__ = [
    "configured_theme",
    "resolve_welcome_card_channel",
    "send_member_welcome_card",
    "welcome_card_permission_problems",
    "welcome_cards_enabled",
]
