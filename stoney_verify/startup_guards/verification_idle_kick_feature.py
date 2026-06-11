from __future__ import annotations

"""Optional per-guild verification idle kick feature.

Feature behavior:
- Off by default for public servers.
- Enabled per guild through /dank setup config keys.
- Starts a timer for new pending/unverified members.
- If the member never starts verification progress before the timer expires,
  the bot removes them and logs it.
- Progress means: no longer pending, or an open verification ticket exists.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord

_TIMER_TYPE = "verification_idle_no_start"
_TASKS: dict[tuple[int, int], asyncio.Task] = {}
_STARTS: dict[tuple[int, int], datetime] = {}
_CHANNELS: dict[tuple[int, int], int] = {}
_ATTACHED = False


def _log(message: str) -> None:
    try:
        print(f"⏳ verification_idle_kick_feature {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verification_idle_kick_feature {message}")
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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return default


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    return default


async def _cfg(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        try:
            return await get_guild_config(int(guild_id), refresh=True)
        except TypeError:
            return await get_guild_config(int(guild_id))
    except Exception:
        return None


async def _settings(guild_id: int) -> tuple[bool, int]:
    cfg = await _cfg(guild_id)
    enabled = _safe_bool(_cfg_get(cfg, "verification_idle_kick_enabled", False), False)
    minutes = _safe_int(_cfg_get(cfg, "verification_idle_kick_minutes", 60), 60)
    minutes = max(5, min(10080, minutes))
    return enabled, minutes


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        return int(role_id or 0) > 0 and any(int(role.id) == int(role_id) for role in getattr(member, "roles", []) or [])
    except Exception:
        return False


async def _is_pending(member: discord.Member) -> bool:
    cfg = await _cfg(member.guild.id)
    uv = _safe_int(_cfg_get(cfg, "unverified_role_id", 0), 0)
    safe_ids = [
        _safe_int(_cfg_get(cfg, key, 0), 0)
        for key in (
            "verified_role_id",
            "resident_role_id",
            "member_role_id",
            "staff_role_id",
            "vc_staff_role_id",
            "stoner_role_id",
            "drunken_role_id",
        )
    ]
    has_pending = _member_has_role_id(member, uv) if uv else False
    has_safe = any(_member_has_role_id(member, rid) for rid in safe_ids if rid)
    return bool(has_pending and not has_safe)


async def _open_verification_ticket(member: discord.Member) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.tickets_new.service import find_open_ticket_for_owner

        row = await find_open_ticket_for_owner(guild_id=member.guild.id, owner_id=member.id, category="verification_issue")
        if not isinstance(row, dict):
            return None
        cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
        if cid <= 0:
            return None
        ch = member.guild.get_channel(cid)
        return ch if isinstance(ch, discord.TextChannel) else None
    except Exception:
        return None


async def _notice_channel(guild: discord.Guild, stored_channel_id: int = 0) -> Optional[discord.TextChannel]:
    try:
        cfg = await _cfg(guild.id)
        ids = [
            stored_channel_id,
            _safe_int(_cfg_get(cfg, "verify_channel_id", 0), 0),
            _safe_int(_cfg_get(cfg, "welcome_channel_id", 0), 0),
            _safe_int(_cfg_get(cfg, "modlog_channel_id", 0), 0),
        ]
        for cid in ids:
            if cid <= 0:
                continue
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
    except Exception:
        pass
    return None


async def _log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.modlog import _get_modlog_channel

        ch = await _get_modlog_channel(guild)  # type: ignore[misc]
        return ch if isinstance(ch, discord.TextChannel) else None
    except Exception:
        return await _notice_channel(guild)


async def _persist_timer(member: discord.Member, *, started_at: datetime, minutes: int, channel_id: int = 0) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        await kick_timers.member_wait_timer_persist_upsert(
            guild_id=member.guild.id,
            user_id=member.id,
            timer_type=_TIMER_TYPE,
            started_at=started_at,
            grace_minutes=minutes,
            source_channel_id=channel_id or None,
        )
    except Exception:
        pass


async def _delete_timer(guild_id: int, user_id: int) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        await kick_timers.member_wait_timer_persist_delete(int(guild_id), int(user_id), timer_type=_TIMER_TYPE)
    except Exception:
        pass


def _key(guild_id: int, user_id: int) -> tuple[int, int]:
    return int(guild_id), int(user_id)


def _cancel(guild_id: int, user_id: int) -> bool:
    key = _key(guild_id, user_id)
    task = _TASKS.pop(key, None)
    _STARTS.pop(key, None)
    _CHANNELS.pop(key, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def _fetch_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


async def _timer_task(guild_id: int, user_id: int, minutes: int, started_at: datetime) -> None:
    key = _key(guild_id, user_id)
    try:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        end_at = started_at.astimezone(timezone.utc) + timedelta(minutes=max(5, int(minutes)))
        try:
            await asyncio.sleep(max(0.0, (end_at - datetime.now(timezone.utc)).total_seconds()))
        except asyncio.CancelledError:
            return

        from stoney_verify.globals import bot

        guild = bot.get_guild(int(guild_id))
        if guild is None:
            return
        enabled, active_minutes = await _settings(guild.id)
        if not enabled:
            return
        member = await _fetch_member(guild, int(user_id))
        if member is None:
            return
        if not await _is_pending(member):
            return
        if await _open_verification_ticket(member) is not None:
            return

        me = guild.me
        notice = await _notice_channel(guild, _CHANNELS.get(key, 0))
        if not me or not me.guild_permissions.kick_members:
            if notice:
                await notice.send(f"⚠️ {member.mention} has not started verification, but I lack **Kick Members** permission.")
            return

        try:
            await guild.kick(member, reason=f"Verification idle timer expired: no verification attempt after {active_minutes} minutes")
            if notice:
                await notice.send(f"👢 {member.mention} was removed after **{active_minutes} minutes** without starting verification.")
            log_ch = await _log_channel(guild)
            if log_ch:
                embed = discord.Embed(
                    title="👢 Verification Idle Kick",
                    description="A pending member was removed because this server enabled no-start verification cleanup.",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="User", value=f"`{member}`\n`{member.id}`", inline=False)
                embed.add_field(name="Timer", value=f"{active_minutes} minute(s)", inline=True)
                embed.add_field(name="Progress", value="No open verification ticket and still pending/unverified.", inline=False)
                await log_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            if notice:
                await notice.send(f"⚠️ I could not remove {member.mention}. Check **Kick Members** and role hierarchy.")
        except discord.HTTPException as e:
            if notice:
                await notice.send(f"⚠️ Verification idle kick failed for {member.mention}: {e}")
    finally:
        await _delete_timer(guild_id, user_id)
        _TASKS.pop(key, None)
        _STARTS.pop(key, None)
        _CHANNELS.pop(key, None)


async def start_timer(member: discord.Member, *, source_channel: Optional[discord.TextChannel] = None, started_at: Optional[datetime] = None) -> bool:
    if not isinstance(member, discord.Member) or getattr(member, "bot", False):
        return False
    enabled, minutes = await _settings(member.guild.id)
    if not enabled:
        return False
    if not await _is_pending(member):
        return False
    if await _open_verification_ticket(member) is not None:
        return False
    key = _key(member.guild.id, member.id)
    _cancel(member.guild.id, member.id)
    started = started_at or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    channel = source_channel or await _notice_channel(member.guild)
    channel_id = int(getattr(channel, "id", 0) or 0)
    _STARTS[key] = started
    _CHANNELS[key] = channel_id
    await _persist_timer(member, started_at=started, minutes=minutes, channel_id=channel_id)
    task = asyncio.create_task(_timer_task(member.guild.id, member.id, minutes, started), name="verification_idle_kick_timer")
    _TASKS[key] = task
    _log(f"started guild={member.guild.id} user={member.id} minutes={minutes}")
    return True


async def cancel_timer(guild_id: int, user_id: int, *, delete: bool = True) -> bool:
    cancelled = _cancel(guild_id, user_id)
    if delete:
        await _delete_timer(guild_id, user_id)
    return cancelled


async def _on_member_join(member: discord.Member) -> None:
    try:
        await start_timer(member)
    except Exception as e:
        _warn(f"join timer start failed guild={getattr(member.guild, 'id', 'unknown')} user={getattr(member, 'id', 'unknown')}: {e!r}")


async def _on_member_update(before: discord.Member, after: discord.Member) -> None:
    try:
        key = _key(after.guild.id, after.id)
        if key not in _TASKS:
            return
        if not await _is_pending(after) or await _open_verification_ticket(after) is not None:
            await cancel_timer(after.guild.id, after.id)
    except Exception:
        pass


async def _resume() -> None:
    try:
        from stoney_verify.globals import bot, _parse_iso_datetime, now_utc
        from stoney_verify.commands_ext import kick_timers

        if getattr(bot, "_verification_idle_kick_resume_ran", False):
            return
        bot._verification_idle_kick_resume_ran = True
        res = await kick_timers._member_wait_timer_persist_select_all_async()  # type: ignore[attr-defined]
        rows = getattr(res, "data", None) or []
        resumed = 0
        for row in rows:
            try:
                if str(row.get("timer_type") or "") != _TIMER_TYPE:
                    continue
                guild_id = _safe_int(row.get("guild_id"), 0)
                user_id = _safe_int(row.get("user_id"), 0)
                guild = bot.get_guild(guild_id)
                if guild is None:
                    continue
                enabled, minutes = await _settings(guild_id)
                if not enabled:
                    await _delete_timer(guild_id, user_id)
                    continue
                member = await _fetch_member(guild, user_id)
                if member is None or not await _is_pending(member) or await _open_verification_ticket(member) is not None:
                    await _delete_timer(guild_id, user_id)
                    continue
                started = _parse_iso_datetime(row.get("started_at")) or getattr(member, "joined_at", None) or now_utc()
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                await start_timer(member, started_at=started)
                resumed += 1
            except Exception:
                continue
        if resumed:
            _log(f"resumed persisted timers count={resumed}")
    except Exception as e:
        _warn(f"resume failed: {e!r}")


def apply() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        from stoney_verify.globals import bot

        bot.add_listener(_on_member_join, "on_member_join")
        bot.add_listener(_on_member_update, "on_member_update")

        async def _on_ready() -> None:
            await _resume()

        bot.add_listener(_on_ready, "on_ready")
        _ATTACHED = True
        _log("active; per-guild optional no-start verification timers attached")
        return True
    except Exception as e:
        _warn(f"attach failed: {e!r}")
        return False


apply()

__all__ = ["apply", "start_timer", "cancel_timer"]
