from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord

from ..guild_config import get_guild_config


# ============================================================
# public_member_lifecycle_logs.py
# ------------------------------------------------------------
# Public-safe join/exit logging for configured guilds.
#
# Why this lives in commands_ext:
# - it can register listeners through the existing startup module system
# - it does not add another top-level slash command
# - it keeps per-guild setup behavior tied to guild_configs, not env globals
#
# Production rules:
# - never hardcode a channel id
# - only sends when join_log_channel_id is configured and writable
# - never pings users/roles from generated logs
# - listener registration is idempotent per process
# ============================================================


_LISTENERS_REGISTERED = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _unix(dt: Optional[datetime]) -> int:
    try:
        if dt is None:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def _discord_time(dt: Optional[datetime], style: str = "F") -> str:
    ts = _unix(dt)
    if ts <= 0:
        return "Unknown"
    return f"<t:{ts}:{style}>"


def _age_days(created_at: Optional[datetime]) -> int:
    try:
        if created_at is None:
            return 0
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return max(0, int((_utc_now() - created_at).total_seconds() // 86400))
    except Exception:
        return 0


def _safe_user_tag(user: discord.abc.User) -> str:
    try:
        return str(user)
    except Exception:
        return f"User {getattr(user, 'id', 'unknown')}"


def _safe_display_name(member: discord.Member) -> str:
    try:
        return str(member.display_name or member.name or member.id)
    except Exception:
        return str(getattr(member, "id", "unknown"))


def _trim(text: str, limit: int = 1024) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _top_roles(member: discord.Member, *, max_roles: int = 8) -> str:
    try:
        roles = [r for r in member.roles if not r.is_default()]
        roles.sort(key=lambda r: r.position, reverse=True)
        if not roles:
            return "None"
        shown = roles[:max_roles]
        out = ", ".join(role.mention for role in shown)
        remaining = len(roles) - len(shown)
        if remaining > 0:
            out += f" +{remaining} more"
        return _trim(out, 1024)
    except Exception:
        return "Unknown"


def _is_writable_text_channel(channel: object, guild: discord.Guild) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history)
    except Exception:
        return False


async def _resolve_join_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        cfg = await get_guild_config(guild.id)
        channel_id = int(getattr(cfg, "join_log_channel_id", 0) or 0)
        if channel_id <= 0:
            return None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                fetched = await guild.fetch_channel(channel_id)
            except Exception:
                fetched = None
            channel = fetched  # type: ignore[assignment]
        if _is_writable_text_channel(channel, guild):
            return channel  # type: ignore[return-value]
        try:
            print(f"⚠️ member_lifecycle_logs join log channel not writable guild={guild.id} channel={channel_id}")
        except Exception:
            pass
        return None
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs failed resolving join log channel guild={guild.id}: {repr(e)}")
        except Exception:
            pass
        return None


def _member_join_embed(member: discord.Member) -> discord.Embed:
    account_age = _age_days(getattr(member, "created_at", None))
    embed = discord.Embed(
        title="🌿 Member Joined",
        description=(
            f"{member.mention} joined the server.\n"
            f"`{_safe_user_tag(member)}` • `{member.id}`"
        ),
        color=discord.Color.green(),
        timestamp=_utc_now(),
    )
    embed.add_field(
        name="Account Created",
        value=f"{_discord_time(getattr(member, 'created_at', None), 'F')}\n{_discord_time(getattr(member, 'created_at', None), 'R')} • `{account_age}` day(s) old",
        inline=False,
    )
    embed.add_field(name="Profile", value=f"Display name: `{_trim(_safe_display_name(member), 128)}`\nBot account: `{'yes' if member.bot else 'no'}`", inline=False)
    try:
        embed.set_thumbnail(url=str(member.display_avatar.url))
    except Exception:
        pass
    try:
        embed.set_footer(text=f"Guild {member.guild.id} • Members: {member.guild.member_count or 'unknown'}")
    except Exception:
        embed.set_footer(text=f"Guild {member.guild.id}")
    return embed


def _member_leave_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🍂 Member Left",
        description=(
            f"{member.mention} left or was removed from the server.\n"
            f"`{_safe_user_tag(member)}` • `{member.id}`"
        ),
        color=discord.Color.orange(),
        timestamp=_utc_now(),
    )
    embed.add_field(
        name="Account Created",
        value=f"{_discord_time(getattr(member, 'created_at', None), 'F')}\n{_discord_time(getattr(member, 'created_at', None), 'R')}",
        inline=False,
    )
    embed.add_field(
        name="Server Join Date",
        value=f"{_discord_time(getattr(member, 'joined_at', None), 'F')}\n{_discord_time(getattr(member, 'joined_at', None), 'R')}",
        inline=False,
    )
    embed.add_field(name="Roles At Exit", value=_top_roles(member), inline=False)
    try:
        embed.set_thumbnail(url=str(member.display_avatar.url))
    except Exception:
        pass
    try:
        embed.set_footer(text=f"Guild {member.guild.id} • Members: {member.guild.member_count or 'unknown'}")
    except Exception:
        embed.set_footer(text=f"Guild {member.guild.id}")
    return embed


async def _send_member_log(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _resolve_join_log_channel(guild)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        try:
            print(f"⚠️ member_lifecycle_logs missing send permission guild={guild.id} channel={channel.id}")
        except Exception:
            pass
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs failed sending guild={guild.id}: {repr(e)}")
        except Exception:
            pass


async def _on_member_join(member: discord.Member) -> None:
    try:
        await _send_member_log(member.guild, _member_join_embed(member))
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs on_member_join failed guild={getattr(getattr(member, 'guild', None), 'id', 'unknown')} user={getattr(member, 'id', 'unknown')}: {repr(e)}")
        except Exception:
            pass


async def _on_member_remove(member: discord.Member) -> None:
    try:
        await _send_member_log(member.guild, _member_leave_embed(member))
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs on_member_remove failed guild={getattr(getattr(member, 'guild', None), 'id', 'unknown')} user={getattr(member, 'id', 'unknown')}: {repr(e)}")
        except Exception:
            pass


def register_public_member_lifecycle_log_listeners(bot, tree) -> None:
    global _LISTENERS_REGISTERED
    _ = tree
    if _LISTENERS_REGISTERED:
        return

    bot.add_listener(_on_member_join, "on_member_join")
    bot.add_listener(_on_member_remove, "on_member_remove")
    _LISTENERS_REGISTERED = True

    try:
        print("✅ public_member_lifecycle_logs: registered join/exit log listeners")
    except Exception:
        pass


__all__ = ["register_public_member_lifecycle_log_listeners"]
