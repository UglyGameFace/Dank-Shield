from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord

from ..guild_config import get_guild_config


# ============================================================
# public_member_lifecycle_logs.py
# ------------------------------------------------------------
# Public-safe join/exit logging for configured guilds.
#
# Production rules:
# - never hardcode a channel id
# - only sends when configured channels are writable
# - never pings users/roles from generated logs
# - join logs are public-safe and go to join_log_channel_id
# - simple leave logs are public-safe and go to join_log_channel_id
# - detailed kick/ban/audit evidence is staff-only and goes to modlog_channel_id
# ============================================================


_LISTENERS_REGISTERED = False
_AUDIT_LOOKBACK_SECONDS = 45
_AUDIT_SETTLE_DELAY_SECONDS = 2.5


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


def _trim(text: Any, limit: int = 1024) -> str:
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


def _can_view_audit_log(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        if me is None:
            return False
        return bool(me.guild_permissions.view_audit_log)
    except Exception:
        return False


def _display_actor(user: Optional[discord.abc.User]) -> str:
    if user is None:
        return "Unknown"
    try:
        mention = getattr(user, "mention", None)
        uid = getattr(user, "id", None)
        tag = _safe_user_tag(user)
        if mention and uid:
            return f"{mention}\n`{tag}` • `{uid}`"
        if uid:
            return f"`{tag}` • `{uid}`"
        return f"`{tag}`"
    except Exception:
        return "Unknown"


def _entry_created_at(entry: discord.AuditLogEntry) -> Optional[datetime]:
    try:
        created_at = getattr(entry, "created_at", None)
        if created_at is None:
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc)
    except Exception:
        return None


def _entry_age_seconds(entry: discord.AuditLogEntry) -> Optional[float]:
    created_at = _entry_created_at(entry)
    if created_at is None:
        return None
    try:
        return max(0.0, (_utc_now() - created_at).total_seconds())
    except Exception:
        return None


def _target_matches_member(entry: discord.AuditLogEntry, member: discord.Member) -> bool:
    try:
        target = getattr(entry, "target", None)
        return int(getattr(target, "id", 0) or 0) == int(member.id)
    except Exception:
        return False


def _is_dank_actor(guild: discord.Guild, actor: Optional[discord.abc.User]) -> bool:
    try:
        return bool(actor is not None and guild.me is not None and int(actor.id) == int(guild.me.id))
    except Exception:
        return False


def _removal_info_default(status: str, label: str, detail: str) -> Dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "detail": detail,
        "source": "join/leave event",
        "actor": None,
        "reason": None,
        "audit_age_seconds": None,
        "confidence": "not_confirmed_by_audit_log",
        "dank_actor": False,
    }


async def _find_matching_audit_entry(
    guild: discord.Guild,
    member: discord.Member,
    action: discord.AuditLogAction,
    *,
    limit: int = 8,
) -> Optional[discord.AuditLogEntry]:
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            if not _target_matches_member(entry, member):
                continue
            age = _entry_age_seconds(entry)
            if age is None or age <= _AUDIT_LOOKBACK_SECONDS:
                return entry
    except discord.Forbidden:
        raise
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs audit lookup failed guild={guild.id} user={member.id} action={action}: {repr(e)}")
        except Exception:
            pass
    return None


async def _resolve_member_remove_cause(member: discord.Member) -> Dict[str, Any]:
    guild = member.guild

    if not _can_view_audit_log(guild):
        return _removal_info_default(
            "audit_unavailable",
            "Left or Removed — Audit Log Unavailable",
            "I do not have **View Audit Log**, so I cannot prove whether this was a voluntary leave, kick, or ban.",
        )

    try:
        # Discord audit log entries can appear shortly after the gateway
        # member-remove event. Waiting prevents false "left" labels.
        await asyncio.sleep(_AUDIT_SETTLE_DELAY_SECONDS)

        banned = await _find_matching_audit_entry(guild, member, discord.AuditLogAction.ban)
        if banned is not None:
            actor = getattr(banned, "user", None)
            return {
                "status": "banned",
                "label": "Banned",
                "detail": "Discord audit log has an exact recent ban entry for this user.",
                "source": "Discord audit log",
                "actor": actor,
                "reason": getattr(banned, "reason", None),
                "audit_age_seconds": _entry_age_seconds(banned),
                "confidence": "exact_recent_audit_match",
                "dank_actor": _is_dank_actor(guild, actor),
            }

        kicked = await _find_matching_audit_entry(guild, member, discord.AuditLogAction.kick)
        if kicked is not None:
            actor = getattr(kicked, "user", None)
            return {
                "status": "kicked",
                "label": "Kicked",
                "detail": "Discord audit log has an exact recent kick entry for this user.",
                "source": "Discord audit log",
                "actor": actor,
                "reason": getattr(kicked, "reason", None),
                "audit_age_seconds": _entry_age_seconds(kicked),
                "confidence": "exact_recent_audit_match",
                "dank_actor": _is_dank_actor(guild, actor),
            }

        return _removal_info_default(
            "left_or_unknown",
            "Left Voluntarily / No Recent Kick-Ban Audit Match",
            "No exact recent kick or ban audit-log entry matched this user. That usually means the member left voluntarily, or Discord did not expose a matching audit entry.",
        )
    except discord.Forbidden:
        return _removal_info_default(
            "audit_forbidden",
            "Left or Removed — Missing Audit Permission",
            "I need **View Audit Log** to prove whether this was a voluntary leave, kick, or ban.",
        )
    except Exception as e:
        return _removal_info_default(
            "audit_error",
            "Left or Removed — Audit Lookup Error",
            f"Audit lookup failed: {type(e).__name__}: {e}",
        )


async def _resolve_configured_channel(guild: discord.Guild, *field_names: str) -> Optional[discord.TextChannel]:
    try:
        cfg = await get_guild_config(guild.id)
        for field_name in field_names:
            channel_id = int(getattr(cfg, field_name, 0) or 0)
            if channel_id <= 0:
                continue
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
                print(f"⚠️ member_lifecycle_logs configured channel not writable guild={guild.id} field={field_name} channel={channel_id}")
            except Exception:
                pass
        return None
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs failed resolving configured channel guild={guild.id}: {repr(e)}")
        except Exception:
            pass
        return None


async def _resolve_join_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    return await _resolve_configured_channel(guild, "join_log_channel_id")


async def _resolve_staff_modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    # Detailed kick/ban/audit evidence belongs in staff-only logs, not public
    # welcome/exit channels. Use modlog first, then force/raid log as staff-ish
    # fallbacks, and intentionally do not fall back to join_log_channel_id.
    return await _resolve_configured_channel(
        guild,
        "modlog_channel_id",
        "force_verify_log_channel_id",
        "raidlog_channel_id",
    )


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


def _member_public_leave_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🍂 Member Left",
        description=(
            f"{member.mention} left the server.\n"
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
    try:
        embed.set_thumbnail(url=str(member.display_avatar.url))
    except Exception:
        pass
    try:
        embed.set_footer(text=f"Guild {member.guild.id} • Members: {member.guild.member_count or 'unknown'}")
    except Exception:
        embed.set_footer(text=f"Guild {member.guild.id}")
    return embed


def _member_staff_leave_embed(member: discord.Member, removal_info: Optional[Dict[str, Any]] = None) -> discord.Embed:
    info = removal_info or _removal_info_default(
        "unknown",
        "Left or Removed",
        "Removal cause was not resolved.",
    )

    status = str(info.get("status") or "unknown")
    color = discord.Color.orange()
    title = "🍂 Member Left"
    if status == "kicked":
        title = "👢 Member Kicked"
        color = discord.Color.red() if bool(info.get("dank_actor")) else discord.Color.orange()
    elif status == "banned":
        title = "🔨 Member Banned"
        color = discord.Color.red()
    elif status in {"audit_unavailable", "audit_forbidden", "audit_error"}:
        title = "🍂 Member Left / Removed"
        color = discord.Color.gold()

    embed = discord.Embed(
        title=title,
        description=(
            f"{member.mention} is no longer in the server.\n"
            f"`{_safe_user_tag(member)}` • `{member.id}`"
        ),
        color=color,
        timestamp=_utc_now(),
    )
    embed.add_field(name="Exit Classification", value=f"**{_trim(info.get('label'), 180)}**", inline=False)
    embed.add_field(name="Evidence", value=_trim(info.get("detail"), 1024), inline=False)
    embed.add_field(name="Source", value=f"`{_trim(info.get('source'), 128)}`", inline=True)
    embed.add_field(name="Confidence", value=f"`{_trim(info.get('confidence'), 128)}`", inline=True)

    audit_age = info.get("audit_age_seconds")
    if audit_age is not None:
        try:
            embed.add_field(name="Audit Entry Age", value=f"`{int(float(audit_age))}s ago`", inline=True)
        except Exception:
            pass

    actor = info.get("actor")
    if actor is not None:
        actor_title = "Actor"
        if bool(info.get("dank_actor")):
            actor_title = "Actor — Dank Shield"
        embed.add_field(name=actor_title, value=_display_actor(actor), inline=False)

    reason = info.get("reason")
    if reason:
        embed.add_field(name="Audit Reason", value=_trim(reason, 1024), inline=False)

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


async def _send_to_channel(channel: Optional[discord.TextChannel], embed: discord.Embed, *, label: str) -> None:
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        try:
            print(f"⚠️ member_lifecycle_logs missing send permission channel={channel.id} label={label}")
        except Exception:
            pass
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs failed sending channel={channel.id} label={label}: {repr(e)}")
        except Exception:
            pass


async def _send_public_member_log(guild: discord.Guild, embed: discord.Embed) -> None:
    await _send_to_channel(await _resolve_join_log_channel(guild), embed, label="public_join_exit")


async def _send_staff_member_log(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _resolve_staff_modlog_channel(guild)
    if channel is None:
        try:
            print(f"⚠️ member_lifecycle_logs staff removal log skipped guild={guild.id}; modlog/raidlog not configured or not writable")
        except Exception:
            pass
        return
    await _send_to_channel(channel, embed, label="staff_modlog_removal")


async def _on_member_join(member: discord.Member) -> None:
    try:
        await _send_public_member_log(member.guild, _member_join_embed(member))
    except Exception as e:
        try:
            print(f"⚠️ member_lifecycle_logs on_member_join failed guild={getattr(getattr(member, 'guild', None), 'id', 'unknown')} user={getattr(member, 'id', 'unknown')}: {repr(e)}")
        except Exception:
            pass


async def _on_member_remove(member: discord.Member) -> None:
    try:
        removal_info = await _resolve_member_remove_cause(member)

        # Public/welcome-exit channel gets only a simple non-sensitive leave card.
        await _send_public_member_log(member.guild, _member_public_leave_embed(member))

        # Staff modlog gets the detailed audit evidence, actor, reason, and roles.
        await _send_staff_member_log(member.guild, _member_staff_leave_embed(member, removal_info))
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
        print("✅ public_member_lifecycle_logs: registered public join/exit + staff-only audit removal listeners")
    except Exception:
        pass


__all__ = ["register_public_member_lifecycle_log_listeners"]
