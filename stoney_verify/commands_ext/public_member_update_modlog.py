from __future__ import annotations

"""Public-safe member update modlog coverage.

Covers the high-value moderation audit gaps that are easy to miss:
- roles added
- roles removed
- nickname/display-name changes
- timeout started/changed/removed when exposed by discord.py

This uses per-guild config only and never hardcodes channel IDs.
"""

from datetime import datetime, timezone
from typing import Any, Optional
import os

import discord

from ..guild_config import get_guild_config

_LISTENERS_REGISTERED = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            return bool(default)
        value = str(raw).strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)
    except Exception:
        return bool(default)


def _trim(value: Any, limit: int = 1024) -> str:
    text = str(value or "").strip()
    if not text:
        return "None"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _user_line(user: Any) -> str:
    try:
        uid = _safe_int(getattr(user, "id", 0), 0)
        tag = str(user)
        return f"{user.mention}\n`{tag}` • `{uid}`" if getattr(user, "mention", None) and uid else f"`{tag}`"
    except Exception:
        return "Unknown"


def _role_map(member: discord.Member) -> dict[int, discord.Role]:
    out: dict[int, discord.Role] = {}
    try:
        for role in member.roles or []:
            if role.is_default():
                continue
            out[int(role.id)] = role
    except Exception:
        pass
    return out


def _role_list(roles: list[discord.Role], *, limit: int = 15) -> str:
    if not roles:
        return "None"
    ordered = sorted(roles, key=lambda r: getattr(r, "position", 0), reverse=True)
    lines = [f"{r.mention} • `{r.id}`" for r in ordered[:limit]]
    remaining = len(ordered) - len(lines)
    if remaining > 0:
        lines.append(f"…and `{remaining}` more")
    return _trim("\n".join(lines), 1024)


def _time_value(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        try:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except Exception:
            return value
    return None


def _discord_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "None"
    try:
        return f"<t:{int(dt.timestamp())}:F>\n<t:{int(dt.timestamp())}:R>"
    except Exception:
        return str(dt)


def _timeout_attr(member: discord.Member) -> Optional[datetime]:
    # discord.py uses timed_out_until. Some forks expose communication_disabled_until.
    for attr in ("timed_out_until", "communication_disabled_until"):
        try:
            value = _time_value(getattr(member, attr, None))
            if value is not None:
                return value
        except Exception:
            pass
    return None


def _is_writable_modlog(channel: Any, guild: discord.Guild) -> bool:
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


async def _modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        cfg = await get_guild_config(guild.id)
        for attr in ("modlog_channel_id", "raidlog_channel_id", "force_verify_log_channel_id"):
            channel_id = _safe_int(getattr(cfg, attr, 0), 0)
            if channel_id <= 0:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except Exception:
                    channel = None
            if _is_writable_modlog(channel, guild):
                return channel  # type: ignore[return-value]
        return None
    except Exception as e:
        print(f"⚠️ public_member_update_modlog failed resolving modlog guild={getattr(guild, 'id', 'unknown')}: {e!r}")
        return None


async def _audit_actor(guild: discord.Guild, action_name: str, *, target_id: int, limit: int = 8) -> tuple[str, str]:
    action = getattr(discord.AuditLogAction, action_name, None)
    if action is None:
        return ("Unknown", "")
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            try:
                target = getattr(entry, "target", None)
                tid = _safe_int(getattr(target, "id", 0), 0)
                if tid and tid != int(target_id):
                    continue
                actor = getattr(entry, "user", None)
                reason = str(getattr(entry, "reason", None) or "").strip()
                return (_user_line(actor), reason)
            except Exception:
                continue
    except discord.Forbidden:
        return ("Unknown — missing View Audit Log", "")
    except Exception:
        return ("Unknown", "")
    return ("Unknown", "")


async def _send(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _modlog_channel(guild)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        print(f"⚠️ public_member_update_modlog send failed guild={guild.id}: {e!r}")


async def _on_member_update(before: discord.Member, after: discord.Member) -> None:
    try:
        guild = after.guild
        if getattr(after, "bot", False):
            return

        before_roles = _role_map(before)
        after_roles = _role_map(after)
        added_roles = [role for rid, role in after_roles.items() if rid not in before_roles]
        removed_roles = [role for rid, role in before_roles.items() if rid not in after_roles]

        before_nick = str(getattr(before, "nick", None) or "")
        after_nick = str(getattr(after, "nick", None) or "")
        nick_changed = before_nick != after_nick

        before_timeout = _timeout_attr(before)
        after_timeout = _timeout_attr(after)
        timeout_changed = before_timeout != after_timeout

        if not added_roles and not removed_roles and not nick_changed and not timeout_changed:
            return

        actor, reason = await _audit_actor(guild, "member_role_update" if (added_roles or removed_roles) else "member_update", target_id=after.id)
        if (nick_changed or timeout_changed) and actor == "Unknown":
            actor, reason = await _audit_actor(guild, "member_update", target_id=after.id)

        embed = discord.Embed(
            title="🧍 Member Updated",
            color=discord.Color.blurple(),
            timestamp=_utcnow(),
        )
        embed.add_field(name="Member", value=_user_line(after), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)

        if added_roles:
            embed.add_field(name="Roles Added", value=_role_list(added_roles), inline=False)
        if removed_roles:
            embed.add_field(name="Roles Removed", value=_role_list(removed_roles), inline=False)
        if nick_changed:
            embed.add_field(
                name="Nickname Changed",
                value=f"Before: `{_trim(before_nick or 'None', 350)}`\nAfter: `{_trim(after_nick or 'None', 350)}`",
                inline=False,
            )
        if timeout_changed:
            embed.add_field(
                name="Timeout Changed",
                value=f"Before:\n{_discord_time(before_timeout)}\n\nAfter:\n{_discord_time(after_timeout)}",
                inline=False,
            )
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 500), inline=False)

        try:
            embed.set_thumbnail(url=str(after.display_avatar.url))
        except Exception:
            pass
        embed.set_footer(text=f"Guild {guild.id} • member update modlog")
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_member_update_modlog on_member_update failed: {e!r}")


def register_public_member_update_modlog(bot, tree) -> None:
    global _LISTENERS_REGISTERED
    _ = tree
    if _LISTENERS_REGISTERED:
        return

    # The main events.py member-update path already posts the richer teal
    # member-update embed. This listener is now an emergency fallback only;
    # leaving it on by default creates duplicate blue + teal mod-log spam.
    if not _env_bool("DANK_ENABLE_PUBLIC_MEMBER_UPDATE_FALLBACK_MODLOG", False):
        try:
            print(
                "ℹ️ public_member_update_modlog: fallback listener disabled "
                "(set DANK_ENABLE_PUBLIC_MEMBER_UPDATE_FALLBACK_MODLOG=true to re-enable)"
            )
        except Exception:
            pass
        return

    bot.add_listener(_on_member_update, "on_member_update")
    _LISTENERS_REGISTERED = True
    try:
        print("✅ public_member_update_modlog: registered fallback role/nickname/timeout update listener")
    except Exception:
        pass


__all__ = ["register_public_member_update_modlog"]