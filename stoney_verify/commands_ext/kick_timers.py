from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import _parse_iso_datetime, get_supabase
from ..tickets import find_ticket_owner_retry, is_verification_ticket_channel
from ..transcripts import send_tickettool_style_transcript
from . import common as _common

try:
    from ..tickets_new.service import find_open_ticket_for_owner
except Exception:
    find_open_ticket_for_owner = None  # type: ignore


# ============================================================
# In-memory registries
# ============================================================
_JOIN_GRACE_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_JOIN_GRACE_STARTS: Dict[Tuple[int, int], datetime] = {}
_JOIN_GRACE_SOURCE_CHANNELS: Dict[Tuple[int, int], int] = {}

_MEMBER_NO_TICKET_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_MEMBER_NO_TICKET_STARTS: Dict[Tuple[int, int], datetime] = {}
_MEMBER_NO_TICKET_SOURCE_CHANNELS: Dict[Tuple[int, int], int] = {}
_MEMBER_NO_TICKET_STARTED_BY: Dict[Tuple[int, int], int] = {}

_MEMBER_WAIT_TIMER_TABLE = str(
    os.getenv("MEMBER_WAIT_TIMER_TABLE")
    or os.getenv("MEMBER_VERIFY_TIMER_TABLE")
    or "member_verify_timers"
).strip() or "member_verify_timers"

_MEMBER_WAIT_TIMER_PERSIST_ENABLED = str(
    os.getenv("PERSIST_MEMBER_WAIT_TIMERS")
    or os.getenv("PERSIST_MEMBER_VERIFY_TIMERS")
    or ("true" if bool(get_supabase()) else "false")
).strip().lower() in {"1", "true", "yes", "y", "on"}

_MEMBER_WAIT_TIMER_PERSIST_AVAILABLE = True
_MEMBER_WAIT_TIMER_PERSIST_DISABLED_REASON: Optional[str] = None


# ============================================================
# Ticket timer persistence
# ============================================================
def _kick_timer_sb():
    return get_supabase()


def _kick_timer_persist_upsert_sync(payload: Dict[str, Any]) -> None:
    sb = _kick_timer_sb()
    if sb is None:
        return
    sb.table(_common.KICK_TIMER_TABLE).upsert(payload, on_conflict="channel_id").execute()


def _kick_timer_persist_delete_sync(channel_id: int) -> None:
    sb = _kick_timer_sb()
    if sb is None:
        return
    sb.table(_common.KICK_TIMER_TABLE).delete().eq("channel_id", str(channel_id)).execute()


def _kick_timer_persist_select_all_sync():
    sb = _kick_timer_sb()
    if sb is None:
        return None
    return sb.table(_common.KICK_TIMER_TABLE).select("*").execute()


async def _kick_timer_persist_upsert_async(payload: Dict[str, Any]) -> None:
    await asyncio.to_thread(_kick_timer_persist_upsert_sync, payload)


async def _kick_timer_persist_delete_async(channel_id: int) -> None:
    await asyncio.to_thread(_kick_timer_persist_delete_sync, int(channel_id))


async def _kick_timer_persist_select_all_async():
    return await asyncio.to_thread(_kick_timer_persist_select_all_sync)


async def kick_timer_persist_upsert(
    *,
    channel_id: int,
    guild_id: int,
    owner_id: int,
    started_at: datetime,
    hours: int,
    started_by: Optional[int] = None,
) -> None:
    if not _common.PERSIST_KICK_TIMERS:
        return

    payload = {
        "channel_id": str(channel_id),
        "guild_id": str(guild_id),
        "owner_id": str(owner_id),
        "started_at": started_at.isoformat(),
        "hours": int(hours),
        "started_by": str(started_by) if started_by else None,
    }
    try:
        await _kick_timer_persist_upsert_async(payload)
    except Exception as e:
        _common.KICK_TIMER_PERSIST_AVAILABLE = False
        _common.KICK_TIMER_PERSIST_DISABLED_REASON = str(e)
        print("⚠️ kick timer persist upsert failed:", repr(e))


async def kick_timer_persist_delete(channel_id: int) -> None:
    if not _common.PERSIST_KICK_TIMERS:
        return
    try:
        await _kick_timer_persist_delete_async(int(channel_id))
    except Exception:
        pass


# ============================================================
# Member wait timer persistence
# ============================================================
def _member_wait_timer_sb():
    return get_supabase()


def _member_wait_timer_persist_enabled() -> bool:
    try:
        return bool(
            _MEMBER_WAIT_TIMER_PERSIST_ENABLED
            and _MEMBER_WAIT_TIMER_PERSIST_AVAILABLE
            and _member_wait_timer_sb() is not None
        )
    except Exception:
        return False


def _member_wait_timer_persist_upsert_sync(payload: Dict[str, Any]) -> None:
    sb = _member_wait_timer_sb()
    if sb is None:
        return
    (
        sb.table(_MEMBER_WAIT_TIMER_TABLE)
        .upsert(payload, on_conflict="guild_id,user_id,timer_type")
        .execute()
    )


def _member_wait_timer_persist_delete_sync(
    guild_id: int,
    user_id: int,
    timer_type: Optional[str] = None,
) -> None:
    sb = _member_wait_timer_sb()
    if sb is None:
        return

    query = (
        sb.table(_MEMBER_WAIT_TIMER_TABLE)
        .delete()
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
    )
    if timer_type:
        query = query.eq("timer_type", str(timer_type))
    query.execute()


def _member_wait_timer_persist_select_all_sync():
    sb = _member_wait_timer_sb()
    if sb is None:
        return None
    return sb.table(_MEMBER_WAIT_TIMER_TABLE).select("*").execute()


async def _member_wait_timer_persist_upsert_async(payload: Dict[str, Any]) -> None:
    await asyncio.to_thread(_member_wait_timer_persist_upsert_sync, payload)


async def _member_wait_timer_persist_delete_async(
    guild_id: int,
    user_id: int,
    timer_type: Optional[str] = None,
) -> None:
    await asyncio.to_thread(
        _member_wait_timer_persist_delete_sync,
        int(guild_id),
        int(user_id),
        str(timer_type) if timer_type else None,
    )


async def _member_wait_timer_persist_select_all_async():
    return await asyncio.to_thread(_member_wait_timer_persist_select_all_sync)


async def member_wait_timer_persist_upsert(
    *,
    guild_id: int,
    user_id: int,
    timer_type: str,
    started_at: datetime,
    grace_minutes: Optional[int] = None,
    hours: Optional[int] = None,
    source_channel_id: Optional[int] = None,
    started_by: Optional[int] = None,
) -> None:
    global _MEMBER_WAIT_TIMER_PERSIST_AVAILABLE, _MEMBER_WAIT_TIMER_PERSIST_DISABLED_REASON

    if not _member_wait_timer_persist_enabled():
        return

    payload = {
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "timer_type": str(timer_type),
        "started_at": started_at.isoformat(),
        "grace_minutes": int(grace_minutes) if grace_minutes is not None else None,
        "hours": int(hours) if hours is not None else None,
        "source_channel_id": str(source_channel_id) if source_channel_id else None,
        "started_by": str(started_by) if started_by else None,
        "updated_at": now_utc().isoformat(),
    }
    try:
        await _member_wait_timer_persist_upsert_async(payload)
    except Exception as e:
        _MEMBER_WAIT_TIMER_PERSIST_AVAILABLE = False
        _MEMBER_WAIT_TIMER_PERSIST_DISABLED_REASON = str(e)
        print("⚠️ member wait timer persist upsert failed:", repr(e))


async def member_wait_timer_persist_delete(
    guild_id: int,
    user_id: int,
    timer_type: Optional[str] = None,
) -> None:
    if not _member_wait_timer_persist_enabled():
        return
    try:
        await _member_wait_timer_persist_delete_async(
            int(guild_id),
            int(user_id),
            str(timer_type) if timer_type else None,
        )
    except Exception:
        pass


# ============================================================
# General helpers
# ============================================================
def _member_key(guild_id: int, user_id: int) -> Tuple[int, int]:
    return (int(guild_id), int(user_id))


def _default_join_grace_minutes() -> int:
    for key in (
        "VERIFY_CREATE_TICKET_GRACE_MINUTES",
        "JOIN_VERIFY_GRACE_MINUTES",
        "NO_TICKET_GRACE_MINUTES",
    ):
        try:
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            pass
        try:
            value = int(str(os.getenv(key, "")).strip() or 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 60


def _default_no_ticket_hours() -> int:
    for key in (
        "VERIFY_NO_TICKET_HOURS",
        "NO_TICKET_24H_TIMER_HOURS",
        "VERIFY_KICK_HOURS",
    ):
        try:
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            pass
        try:
            value = int(str(os.getenv(key, "")).strip() or 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 24



def _cfg_value_for_timer(cfg: Any, key: str, default: Any = None) -> Any:
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


async def _guild_verify_wait_hours(guild_id: int) -> int:
    """Per-guild verification wait timer hours.

    Falls back to legacy env/global defaults only when no saved guild value
    exists. This keeps multi-server setup scoped by guild_id.
    """

    try:
        from stoney_verify.guild_config import get_guild_config

        try:
            cfg = await get_guild_config(int(guild_id), refresh=True)
        except TypeError:
            cfg = await get_guild_config(int(guild_id))

        for key in ("verify_kick_hours", "verification_wait_timer_hours", "verification_no_progress_hours"):
            raw = _cfg_value_for_timer(cfg, key)
            try:
                value = int(str(raw or "").strip() or 0)
                if value > 0:
                    return max(1, min(720, value))
            except Exception:
                pass
    except Exception:
        pass

    return max(1, min(720, int(_default_no_ticket_hours() or 24)))


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        return bool(role_id) and any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _member_is_pending_verification(member: discord.Member) -> bool:
    try:
        if getattr(member, "bot", False):
            return False
        uv_id = int(globals().get("UNVERIFIED_ROLE_ID", 0) or 0)
        verified_id = int(globals().get("VERIFIED_ROLE_ID", 0) or 0)
        resident_id = int(globals().get("RESIDENT_ROLE_ID", 0) or 0)
        staff_id = int(globals().get("STAFF_ROLE_ID", 0) or 0)
        has_unverified = _member_has_role_id(member, uv_id) if uv_id else False
        has_verified = _member_has_role_id(member, verified_id) if verified_id else False
        has_resident = _member_has_role_id(member, resident_id) if resident_id else False
        has_staff = _member_has_role_id(member, staff_id) if staff_id else False
        return bool(has_unverified and not has_verified and not has_resident and not has_staff)
    except Exception:
        return False


async def _fetch_member_if_present(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
    except Exception:
        pass
    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


async def _resolve_text_channel_by_id(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.TextChannel]:
    if not channel_id:
        return None
    try:
        ch = guild.get_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    try:
        ch = await bot.fetch_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    return None


async def _resolve_unverified_chat_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    candidate_ids: List[int] = []
    for key in (
        "UNVERIFIED_CHAT_CHANNEL_ID",
        "UNVERIFIED_ONLY_CHAT_CHANNEL_ID",
        "VERIFY_WAIT_CHANNEL_ID",
        "VERIFY_HELP_CHANNEL_ID",
        "UNVERIFIED_CHANNEL_ID",
    ):
        try:
            cid = int(str(globals().get(key) or "0").strip() or 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass
        try:
            cid = int(str(os.getenv(key, "") or "0").strip() or 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass

    for cid in candidate_ids:
        ch = await _resolve_text_channel_by_id(guild, cid)
        if ch:
            return ch

    exact_names = {"unverified-chat", "unverified", "verify-chat", "verification-chat"}
    fuzzy_terms = ("unverified", "verify", "verification")
    try:
        for ch in list(guild.text_channels):
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if name in exact_names:
                return ch
    except Exception:
        pass
    try:
        for ch in list(guild.text_channels):
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if any(term in name for term in fuzzy_terms):
                return ch
    except Exception:
        pass
    return None


async def _resolve_notice_channel(
    guild: discord.Guild,
    explicit_channel: Optional[discord.TextChannel] = None,
    stored_channel_id: int = 0,
) -> Optional[discord.TextChannel]:
    if isinstance(explicit_channel, discord.TextChannel):
        return explicit_channel
    if stored_channel_id:
        ch = await _resolve_text_channel_by_id(guild, stored_channel_id)
        if ch:
            return ch
    return await _resolve_unverified_chat_channel(guild)


async def _resolve_open_verification_ticket_channel(
    member: discord.Member,
) -> Optional[discord.TextChannel]:
    if not find_open_ticket_for_owner:
        return None

    row = None
    try:
        row = await find_open_ticket_for_owner(
            guild_id=member.guild.id,
            owner_id=member.id,
            category="verification_issue",
        )
    except TypeError:
        try:
            row = await find_open_ticket_for_owner(guild_id=member.guild.id, owner_id=member.id)
        except Exception:
            row = None
    except Exception:
        row = None

    if not isinstance(row, dict):
        return None

    try:
        channel_id = int(str(row.get("channel_id") or row.get("discord_thread_id") or "0") or 0)
    except Exception:
        channel_id = 0

    if channel_id <= 0:
        return None
    return await _resolve_text_channel_by_id(member.guild, channel_id)


async def _send_notice(channel: Optional[discord.TextChannel], content: str) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(content)
    except Exception:
        pass


def _cancel_join_grace_timer(guild_id: int, user_id: int) -> bool:
    task = _JOIN_GRACE_TASKS.get(_member_key(guild_id, user_id))
    if task and not task.done():
        task.cancel()
        return True
    return False


def _cancel_member_no_ticket_timer(guild_id: int, user_id: int) -> bool:
    task = _MEMBER_NO_TICKET_TASKS.get(_member_key(guild_id, user_id))
    if task and not task.done():
        task.cancel()
        return True
    return False


# ============================================================
# Ticket-channel timer helpers
# ============================================================
async def _user_responded_or_submitted(
    channel: discord.TextChannel,
    owner: discord.Member,
    since: datetime,
) -> bool:
    try:
        async for message in channel.history(limit=500, after=since, oldest_first=True):
            try:
                if message.author and getattr(message.author, "id", None) == owner.id:
                    return True
            except Exception:
                pass
            try:
                if _common.extract_token_from_message(message):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


async def _kick_after_timer(channel: discord.TextChannel, owner: discord.Member, hours: int):
    try:
        hours = int(hours)
    except Exception:
        hours = int(VERIFY_KICK_HOURS or 24)
    if hours <= 0:
        hours = 24

    start = _common.KICK_TIMER_STARTS.get(channel.id, now_utc())

    try:
        end_at = start + timedelta(hours=hours)
        try:
            await asyncio.sleep(max(0, (end_at - now_utc()).total_seconds()))
        except asyncio.CancelledError:
            return

        guild = channel.guild
        me = guild.me
        if not me:
            return

        try:
            current_channel = guild.get_channel(channel.id) or await bot.fetch_channel(channel.id)
        except Exception:
            current_channel = channel
        if not isinstance(current_channel, discord.TextChannel):
            return

        owner_now = guild.get_member(owner.id)
        if owner_now is None:
            try:
                owner_now = await guild.fetch_member(owner.id)
            except Exception:
                owner_now = owner if isinstance(owner, discord.Member) else None
        if owner_now is None:
            return

        if not _member_is_pending_verification(owner_now):
            print(
                f"ℹ️ kick timer expired but user no longer pending verification "
                f"guild={guild.id} user={owner_now.id} channel={current_channel.id}"
            )
            return

        if await _user_responded_or_submitted(current_channel, owner_now, start):
            print(
                f"ℹ️ kick timer cancelled by owner activity/submission "
                f"guild={guild.id} user={owner_now.id} channel={current_channel.id}"
            )
            return

        kick_ok = False
        kick_err: Optional[str] = None

        if not me.guild_permissions.kick_members:
            kick_err = "Bot lacks Kick Members permission"
            await _send_notice(
                current_channel,
                "⚠️ No response detected, but I lack **Kick Members** permission.",
            )
        else:
            try:
                await guild.kick(
                    owner_now,
                    reason=str(globals().get("KICK_REASON") or "Verification no-response timer expired"),
                )
                kick_ok = True
                await _send_notice(
                    current_channel,
                    f"👢 {owner_now.mention} was kicked for failing to respond within **{hours} hours**.",
                )
            except discord.Forbidden:
                kick_err = "Forbidden (role hierarchy / missing perms)"
                await _send_notice(
                    current_channel,
                    "⚠️ Kick failed (Forbidden). Check **Kick Members** + role hierarchy.",
                )
            except discord.HTTPException as e:
                kick_err = str(e)
                await _send_notice(current_channel, f"⚠️ Kick failed: {e}")

        starter_member: Optional[discord.Member] = None
        try:
            started_by = _common.KICK_TIMER_STARTED_BY.get(current_channel.id)
            if started_by:
                starter_member = guild.get_member(int(started_by)) or await guild.fetch_member(int(started_by))
        except Exception:
            starter_member = None

        decision = f"NO RESPONSE ({hours}H TIMER)"
        if not kick_ok:
            decision = f"{decision} — KICK FAILED" + (f" ({kick_err})" if kick_err else "")

        try:
            await send_tickettool_style_transcript(
                current_channel,
                owner_now,
                closed_by=starter_member,
                decision=decision,
            )
        except Exception as e:
            print("⚠️ Transcript routing failed (timer expiry):", repr(e))

        try:
            await current_channel.delete(
                reason=f"Verification ticket closed after {hours}h no-response timer"
            )
            _common.RUNTIME_STATS["tickets_closed"] = _common.RUNTIME_STATS.get("tickets_closed", 0) + 1
        except discord.Forbidden:
            await _send_notice(
                current_channel,
                "⚠️ I could not delete this ticket (missing **Manage Channels**). Transcript was still posted.",
            )
        except Exception as e:
            print("⚠️ Channel delete failed (timer expiry):", repr(e))

    finally:
        try:
            await kick_timer_persist_delete(int(channel.id))
        except Exception:
            pass
        _common.KICK_TIMER_TASKS.pop(channel.id, None)
        _common.KICK_TIMER_STARTS.pop(channel.id, None)
        _common.KICK_TIMER_STARTED_BY.pop(channel.id, None)


def _cancel_kick_timer(channel_id: int) -> bool:
    task = _common.KICK_TIMER_TASKS.get(channel_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def kick_timer_resume_all() -> None:
    if not _common.PERSIST_KICK_TIMERS:
        return
    if _kick_timer_sb() is None:
        return
    if getattr(bot, "_kick_timer_resume_ran", False):
        return

    try:
        res = await _kick_timer_persist_select_all_async()
        rows = getattr(res, "data", None) or []
    except Exception as e:
        _common.KICK_TIMER_PERSIST_AVAILABLE = False
        _common.KICK_TIMER_PERSIST_DISABLED_REASON = str(e)
        print("⚠️ kick timer resume query failed:", repr(e))
        return

    scheduled = 0
    for row in rows:
        try:
            channel_id = int(str(row.get("channel_id") or "0") or 0)
            guild_id = int(str(row.get("guild_id") or "0") or 0)
            owner_id = int(str(row.get("owner_id") or "0") or 0)
            hours = int(row.get("hours") or VERIFY_KICK_HOURS or 24)
            started_at = _parse_iso_datetime(row.get("started_at")) or now_utc()

            if not channel_id or not guild_id or not owner_id:
                continue
            if _common.KICK_TIMER_TASKS.get(channel_id) and not _common.KICK_TIMER_TASKS[channel_id].done():
                continue

            guild = bot.get_guild(guild_id)
            if guild is None:
                continue

            channel = await _resolve_text_channel_by_id(guild, channel_id)
            if not isinstance(channel, discord.TextChannel):
                await kick_timer_persist_delete(channel_id)
                continue

            owner = await _fetch_member_if_present(guild, owner_id)
            if owner is None:
                await kick_timer_persist_delete(channel_id)
                continue

            if not _member_is_pending_verification(owner):
                await kick_timer_persist_delete(channel_id)
                continue

            _common.KICK_TIMER_STARTS[channel.id] = started_at
            try:
                started_by = row.get("started_by")
                if started_by:
                    _common.KICK_TIMER_STARTED_BY[channel.id] = int(str(started_by))
            except Exception:
                pass

            task = asyncio.create_task(_kick_after_timer(channel, owner, hours))
            _common._track_task(task, label="kick_timer_resume")
            _common.KICK_TIMER_TASKS[channel.id] = task
            scheduled += 1
        except Exception as e:
            print("⚠️ kick timer resume row failed:", repr(e))
            continue

    bot._kick_timer_resume_ran = True
    if scheduled:
        print(f"⏳ Resumed {scheduled} kick timer(s) from Supabase.")
    else:
        print("ℹ️ No kick timers resumed from Supabase.")


# ============================================================
# Member wait timer helpers
# ============================================================
async def _member_no_ticket_timer_task(guild_id: int, user_id: int, hours: int) -> None:
    key = _member_key(guild_id, user_id)
    start = _MEMBER_NO_TICKET_STARTS.get(key, now_utc())

    try:
        end_at = start + timedelta(hours=max(0, int(hours)))
        try:
            await asyncio.sleep(max(0, (end_at - now_utc()).total_seconds()))
        except asyncio.CancelledError:
            return

        guild = bot.get_guild(int(guild_id))
        if guild is None:
            return

        member = await _fetch_member_if_present(guild, int(user_id))
        if member is None or not _member_is_pending_verification(member):
            return

        open_ticket = await _resolve_open_verification_ticket_channel(member)
        if open_ticket is not None:
            return

        source_channel = await _resolve_notice_channel(
            guild,
            stored_channel_id=_MEMBER_NO_TICKET_SOURCE_CHANNELS.get(key, 0),
        )

        me = guild.me
        kick_ok = False
        kick_err: Optional[str] = None

        if not me or not me.guild_permissions.kick_members:
            kick_err = "Bot lacks Kick Members permission"
            await _send_notice(
                source_channel,
                f"⚠️ {member.mention} exceeded the verification wait time, but I lack **Kick Members** permission.",
            )
        else:
            try:
                await guild.kick(
                    member,
                    reason=str(
                        globals().get("KICK_REASON")
                        or f"Verification timer expired with no ticket or progress after {hours}h"
                    ),
                )
                kick_ok = True
                _common.RUNTIME_STATS["verification_no_ticket_kicks"] = (
                    _common.RUNTIME_STATS.get("verification_no_ticket_kicks", 0) + 1
                )
                await _send_notice(
                    source_channel,
                    f"👢 {member.mention} was kicked after **{hours} hours** with no verification progress and no ticket.",
                )
            except discord.Forbidden:
                kick_err = "Forbidden (role hierarchy / missing perms)"
                await _send_notice(
                    source_channel,
                    f"⚠️ Failed to kick {member.mention}. Check **Kick Members** and role hierarchy.",
                )
            except discord.HTTPException as e:
                kick_err = str(e)
                await _send_notice(source_channel, f"⚠️ Failed to kick {member.mention}: {e}")

        if not kick_ok and kick_err:
            print(
                f"⚠️ member no-ticket timer expired but kick failed "
                f"guild={guild_id} user={user_id} error={kick_err}"
            )

    finally:
        try:
            await member_wait_timer_persist_delete(guild_id, user_id, timer_type="member_no_ticket")
        except Exception:
            pass
        _MEMBER_NO_TICKET_TASKS.pop(key, None)
        _MEMBER_NO_TICKET_STARTS.pop(key, None)
        _MEMBER_NO_TICKET_SOURCE_CHANNELS.pop(key, None)
        _MEMBER_NO_TICKET_STARTED_BY.pop(key, None)


async def _start_member_no_ticket_timer(
    *,
    member: discord.Member,
    source_channel: Optional[discord.TextChannel] = None,
    hours: Optional[int] = None,
    started_by: Optional[int] = None,
    announce: bool = True,
    cancel_join_grace: bool = True,
    started_at: Optional[datetime] = None,
    persist: bool = True,
) -> bool:
    if getattr(member, "bot", False):
        return False
    if not _member_is_pending_verification(member):
        return False
    if await _resolve_open_verification_ticket_channel(member) is not None:
        return False

    key = _member_key(member.guild.id, member.id)

    if cancel_join_grace:
        _cancel_join_grace_timer(member.guild.id, member.id)
        try:
            await member_wait_timer_persist_delete(member.guild.id, member.id, timer_type="join_grace")
        except Exception:
            pass

    _cancel_member_no_ticket_timer(member.guild.id, member.id)

    timer_hours = int(hours) if hours is not None else await _guild_verify_wait_hours(member.guild.id)
    if timer_hours <= 0:
        timer_hours = 24

    notice_channel = await _resolve_notice_channel(
        member.guild,
        explicit_channel=source_channel,
        stored_channel_id=_MEMBER_NO_TICKET_SOURCE_CHANNELS.get(key, 0),
    )

    timer_started_at = started_at or now_utc()
    if timer_started_at.tzinfo is None:
        timer_started_at = timer_started_at.replace(tzinfo=now_utc().tzinfo)

    _MEMBER_NO_TICKET_STARTS[key] = timer_started_at
    _MEMBER_NO_TICKET_SOURCE_CHANNELS[key] = int(getattr(notice_channel, "id", 0) or 0)
    if started_by:
        _MEMBER_NO_TICKET_STARTED_BY[key] = int(started_by)

    if persist:
        try:
            await member_wait_timer_persist_upsert(
                guild_id=member.guild.id,
                user_id=member.id,
                timer_type="member_no_ticket",
                started_at=timer_started_at,
                hours=timer_hours,
                source_channel_id=int(getattr(notice_channel, "id", 0) or 0) or None,
                started_by=int(started_by) if started_by else None,
            )
        except Exception:
            pass

    task = asyncio.create_task(_member_no_ticket_timer_task(member.guild.id, member.id, timer_hours))
    _common._track_task(task, label="member_no_ticket_timer")
    _MEMBER_NO_TICKET_TASKS[key] = task

    if announce and isinstance(notice_channel, discord.TextChannel):
        await _send_notice(
            notice_channel,
            f"⏳ {member.mention} Your **{timer_hours} hour** verification timer starts now.\n"
            "If you still have no verification progress by the end of it, you may be removed.",
        )

    print(
        f"⏳ Started member no-ticket timer guild={member.guild.id} "
        f"user={member.id} hours={timer_hours} channel={getattr(notice_channel, 'id', None)}"
    )
    return True


async def _join_grace_then_start_member_timer_task(
    guild_id: int,
    user_id: int,
    grace_minutes: int,
) -> None:
    key = _member_key(guild_id, user_id)
    start = _JOIN_GRACE_STARTS.get(key, now_utc())

    try:
        end_at = start + timedelta(minutes=max(0, int(grace_minutes)))
        try:
            await asyncio.sleep(max(0, (end_at - now_utc()).total_seconds()))
        except asyncio.CancelledError:
            return

        guild = bot.get_guild(int(guild_id))
        if guild is None:
            return

        member = await _fetch_member_if_present(guild, int(user_id))
        if member is None or not _member_is_pending_verification(member):
            return

        open_ticket = await _resolve_open_verification_ticket_channel(member)
        if open_ticket is not None:
            return

        notice_channel = await _resolve_notice_channel(
            guild,
            stored_channel_id=_JOIN_GRACE_SOURCE_CHANNELS.get(key, 0),
        )

        await _start_member_no_ticket_timer(
            member=member,
            source_channel=notice_channel,
            hours=await _guild_verify_wait_hours(guild_id),
            announce=True,
            cancel_join_grace=False,
            started_at=end_at,
            persist=True,
        )

    finally:
        try:
            await member_wait_timer_persist_delete(guild_id, user_id, timer_type="join_grace")
        except Exception:
            pass
        _JOIN_GRACE_TASKS.pop(key, None)
        _JOIN_GRACE_STARTS.pop(key, None)
        _JOIN_GRACE_SOURCE_CHANNELS.pop(key, None)


async def start_join_grace_then_kick_timer_for_member(
    member: discord.Member,
    source_channel: Optional[discord.TextChannel] = None,
    grace_minutes: Optional[int] = None,
    started_at: Optional[datetime] = None,
    persist: bool = True,
) -> bool:
    """
    Starts the short grace window to create a ticket / begin verification progress.
    If that expires and the member is still pending verification with no ticket,
    a longer member-based timer begins.
    """
    if getattr(member, "bot", False):
        return False
    if not _member_is_pending_verification(member):
        return False
    if await _resolve_open_verification_ticket_channel(member) is not None:
        return False

    key = _member_key(member.guild.id, member.id)

    _cancel_join_grace_timer(member.guild.id, member.id)
    _cancel_member_no_ticket_timer(member.guild.id, member.id)

    try:
        await member_wait_timer_persist_delete(member.guild.id, member.id, timer_type="join_grace")
    except Exception:
        pass
    try:
        await member_wait_timer_persist_delete(member.guild.id, member.id, timer_type="member_no_ticket")
    except Exception:
        pass

    timer_minutes = int(grace_minutes or _default_join_grace_minutes())
    if timer_minutes <= 0:
        timer_minutes = 60

    notice_channel = await _resolve_notice_channel(member.guild, explicit_channel=source_channel)
    timer_started_at = started_at or now_utc()
    if timer_started_at.tzinfo is None:
        timer_started_at = timer_started_at.replace(tzinfo=now_utc().tzinfo)

    _JOIN_GRACE_STARTS[key] = timer_started_at
    _JOIN_GRACE_SOURCE_CHANNELS[key] = int(getattr(notice_channel, "id", 0) or 0)

    if persist:
        try:
            await member_wait_timer_persist_upsert(
                guild_id=member.guild.id,
                user_id=member.id,
                timer_type="join_grace",
                started_at=timer_started_at,
                grace_minutes=timer_minutes,
                source_channel_id=int(getattr(notice_channel, "id", 0) or 0) or None,
            )
        except Exception:
            pass

    task = asyncio.create_task(
        _join_grace_then_start_member_timer_task(member.guild.id, member.id, timer_minutes)
    )
    _common._track_task(task, label="join_grace_timer")
    _JOIN_GRACE_TASKS[key] = task

    print(
        f"⏳ Started join grace timer guild={member.guild.id} "
        f"user={member.id} minutes={timer_minutes} channel={getattr(notice_channel, 'id', None)}"
    )
    return True


async def _resume_member_wait_timers_from_live_state(
    *,
    exclude_keys: Optional[Set[Tuple[int, int]]] = None,
) -> int:
    exclude: Set[Tuple[int, int]] = set(exclude_keys or set())
    resumed = 0
    grace_minutes = max(1, int(_default_join_grace_minutes() or 60))
    no_ticket_hours = max(1, int(_default_no_ticket_hours() or 24))
    now = now_utc()

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception:
                members = list(getattr(guild, "members", []) or [])

            fallback_channel = await _resolve_unverified_chat_channel(guild)

            for member in members:
                try:
                    key = _member_key(guild.id, member.id)
                    if key in exclude or getattr(member, "bot", False):
                        continue
                    if not _member_is_pending_verification(member):
                        continue

                    if await _resolve_open_verification_ticket_channel(member) is not None:
                        try:
                            await member_wait_timer_persist_delete(guild.id, member.id, timer_type=None)
                        except Exception:
                            pass
                        continue

                    joined_at = getattr(member, "joined_at", None) or now
                    started = joined_at if isinstance(joined_at, datetime) else now
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=now.tzinfo)

                    elapsed = max(0.0, (now - started).total_seconds())
                    grace_seconds = float(grace_minutes * 60)
                    no_ticket_start = started + timedelta(minutes=grace_minutes)

                    if elapsed < grace_seconds:
                        ok = await start_join_grace_then_kick_timer_for_member(
                            member,
                            source_channel=fallback_channel,
                            grace_minutes=grace_minutes,
                            started_at=started,
                            persist=True,
                        )
                    else:
                        ok = await _start_member_no_ticket_timer(
                            member=member,
                            source_channel=fallback_channel,
                            hours=await _guild_verify_wait_hours(guild.id),
                            announce=False,
                            cancel_join_grace=True,
                            started_at=no_ticket_start,
                            persist=True,
                        )

                    if ok:
                        exclude.add(key)
                        resumed += 1
                except Exception as e:
                    print(
                        f"⚠️ live-state member timer recovery failed guild={getattr(guild, 'id', 'unknown')} "
                        f"user={getattr(member, 'id', 'unknown')} error={repr(e)}"
                    )
                    continue
        except Exception as e:
            print(
                f"⚠️ live-state member timer recovery guild sweep failed "
                f"guild={getattr(guild, 'id', 'unknown')} error={repr(e)}"
            )
            continue

    return resumed


async def member_wait_timer_resume_all() -> None:
    global _MEMBER_WAIT_TIMER_PERSIST_AVAILABLE, _MEMBER_WAIT_TIMER_PERSIST_DISABLED_REASON

    if getattr(bot, "_member_wait_timer_resume_ran", False):
        return

    resumed_keys: Set[Tuple[int, int]] = set()
    resumed = 0

    if _member_wait_timer_persist_enabled():
        try:
            res = await _member_wait_timer_persist_select_all_async()
            rows = getattr(res, "data", None) or []
        except Exception as e:
            _MEMBER_WAIT_TIMER_PERSIST_AVAILABLE = False
            _MEMBER_WAIT_TIMER_PERSIST_DISABLED_REASON = str(e)
            print("⚠️ member wait timer resume query failed:", repr(e))
            rows = []

        for row in rows:
            try:
                timer_type = str(row.get("timer_type") or "").strip().lower()
                guild_id = int(str(row.get("guild_id") or "0") or 0)
                user_id = int(str(row.get("user_id") or "0") or 0)
                if timer_type not in {"join_grace", "member_no_ticket"} or not guild_id or not user_id:
                    continue

                key = _member_key(guild_id, user_id)
                if key in resumed_keys:
                    continue

                guild = bot.get_guild(guild_id)
                if guild is None:
                    continue

                member = await _fetch_member_if_present(guild, user_id)
                if member is None or not _member_is_pending_verification(member):
                    await member_wait_timer_persist_delete(guild_id, user_id, timer_type=timer_type)
                    continue

                if await _resolve_open_verification_ticket_channel(member) is not None:
                    await member_wait_timer_persist_delete(guild_id, user_id, timer_type=timer_type)
                    continue

                source_channel_id = 0
                try:
                    source_channel_id = int(str(row.get("source_channel_id") or "0") or 0)
                except Exception:
                    source_channel_id = 0
                source_channel = await _resolve_notice_channel(guild, stored_channel_id=source_channel_id)

                started_at = _parse_iso_datetime(row.get("started_at")) or (
                    getattr(member, "joined_at", None) or now_utc()
                )
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=now_utc().tzinfo)

                if timer_type == "join_grace":
                    grace_minutes = int(row.get("grace_minutes") or _default_join_grace_minutes() or 60)
                    ok = await start_join_grace_then_kick_timer_for_member(
                        member,
                        source_channel=source_channel,
                        grace_minutes=grace_minutes,
                        started_at=started_at,
                        persist=False,
                    )
                else:
                    hours = int(row.get("hours") or await _guild_verify_wait_hours(guild_id) or 24)
                    started_by = None
                    try:
                        raw_started_by = row.get("started_by")
                        started_by = int(str(raw_started_by)) if raw_started_by else None
                    except Exception:
                        started_by = None
                    ok = await _start_member_no_ticket_timer(
                        member=member,
                        source_channel=source_channel,
                        hours=hours,
                        started_by=started_by,
                        announce=False,
                        cancel_join_grace=True,
                        started_at=started_at,
                        persist=False,
                    )

                if ok:
                    resumed_keys.add(key)
                    resumed += 1
                else:
                    await member_wait_timer_persist_delete(guild_id, user_id, timer_type=timer_type)
            except Exception as e:
                print("⚠️ member wait timer resume row failed:", repr(e))
                continue

    resumed += int(await _resume_member_wait_timers_from_live_state(exclude_keys=resumed_keys))

    bot._member_wait_timer_resume_ran = True
    if resumed:
        print(f"⏳ Resumed {resumed} member wait timer(s) from persistence/live state.")
    else:
        print("ℹ️ No member wait timers resumed from persistence/live state.")


async def cancel_verification_wait_timers_for_member(guild_id: int, owner_id: int) -> bool:
    """
    Best-effort canceller for:
    - join grace timers
    - member no-ticket timers
    - ticket-based 24h timers for any verification ticket owned by the member
    """
    gid = int(guild_id)
    oid = int(owner_id)
    key = _member_key(gid, oid)
    cancelled_any = False

    try:
        if _cancel_join_grace_timer(gid, oid):
            cancelled_any = True
        _JOIN_GRACE_TASKS.pop(key, None)
        _JOIN_GRACE_STARTS.pop(key, None)
        _JOIN_GRACE_SOURCE_CHANNELS.pop(key, None)
        try:
            await member_wait_timer_persist_delete(gid, oid, timer_type="join_grace")
        except Exception:
            pass
    except Exception:
        pass

    try:
        if _cancel_member_no_ticket_timer(gid, oid):
            cancelled_any = True
        _MEMBER_NO_TICKET_TASKS.pop(key, None)
        _MEMBER_NO_TICKET_STARTS.pop(key, None)
        _MEMBER_NO_TICKET_SOURCE_CHANNELS.pop(key, None)
        _MEMBER_NO_TICKET_STARTED_BY.pop(key, None)
        try:
            await member_wait_timer_persist_delete(gid, oid, timer_type="member_no_ticket")
        except Exception:
            pass
    except Exception:
        pass

    guild = bot.get_guild(gid)
    if guild is not None:
        for channel_id in list(_common.KICK_TIMER_TASKS.keys()):
            try:
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.TextChannel):
                    continue
                if not is_verification_ticket_channel(channel):
                    continue

                owner = await find_ticket_owner_retry(channel)
                if not owner or int(owner.id) != oid:
                    continue

                if _cancel_kick_timer(channel.id):
                    cancelled_any = True

                _common.KICK_TIMER_TASKS.pop(channel.id, None)
                _common.KICK_TIMER_STARTS.pop(channel.id, None)
                _common.KICK_TIMER_STARTED_BY.pop(channel.id, None)

                try:
                    await kick_timer_persist_delete(int(channel.id))
                except Exception:
                    pass

                try:
                    await channel.send("🛑 Verification wait timer cancelled because ticket flow is now active.")
                except Exception:
                    pass
            except Exception:
                continue

    if cancelled_any:
        print(
            f"⏹️ cancel_verification_wait_timers_for_member: "
            f"cancelled wait timer(s) for guild={gid} owner={oid}"
        )
    return cancelled_any


async def verification_wait_timer_summary(guild_id: int) -> dict[str, int]:
    gid = int(guild_id)
    active_join = sum(1 for key, task in list(_JOIN_GRACE_TASKS.items()) if int(key[0]) == gid and task and not task.done())
    active_member = sum(1 for key, task in list(_MEMBER_NO_TICKET_TASKS.items()) if int(key[0]) == gid and task and not task.done())

    active_ticket = 0
    try:
        guild = bot.get_guild(gid)
        if guild is not None:
            for channel_id, task in list(_common.KICK_TIMER_TASKS.items()):
                if not task or task.done():
                    continue
                ch = guild.get_channel(int(channel_id))
                if isinstance(ch, discord.TextChannel) and is_verification_ticket_channel(ch):
                    active_ticket += 1
    except Exception:
        pass

    persisted = 0
    try:
        res = await _member_wait_timer_persist_select_all_async()
        rows = getattr(res, "data", None) or []
        for row in rows:
            if int(str(row.get("guild_id") or "0") or 0) != gid:
                continue
            if str(row.get("timer_type") or "").strip().lower() in {"join_grace", "member_no_ticket"}:
                persisted += 1
    except Exception:
        pass

    return {
        "active_join_grace": int(active_join),
        "active_member_no_ticket": int(active_member),
        "active_ticket_no_response": int(active_ticket),
        "persisted_wait_rows": int(persisted),
    }


async def clear_verification_wait_timers_for_guild(guild_id: int) -> dict[str, int]:
    gid = int(guild_id)
    cleared_join = 0
    cleared_member = 0
    cleared_ticket = 0
    cleared_persisted = 0

    for key, task in list(_JOIN_GRACE_TASKS.items()):
        if int(key[0]) != gid:
            continue
        if task and not task.done():
            task.cancel()
            cleared_join += 1
        _JOIN_GRACE_TASKS.pop(key, None)
        _JOIN_GRACE_STARTS.pop(key, None)
        _JOIN_GRACE_SOURCE_CHANNELS.pop(key, None)
        try:
            await member_wait_timer_persist_delete(gid, int(key[1]), timer_type="join_grace")
            cleared_persisted += 1
        except Exception:
            pass

    for key, task in list(_MEMBER_NO_TICKET_TASKS.items()):
        if int(key[0]) != gid:
            continue
        if task and not task.done():
            task.cancel()
            cleared_member += 1
        _MEMBER_NO_TICKET_TASKS.pop(key, None)
        _MEMBER_NO_TICKET_STARTS.pop(key, None)
        _MEMBER_NO_TICKET_SOURCE_CHANNELS.pop(key, None)
        _MEMBER_NO_TICKET_STARTED_BY.pop(key, None)
        try:
            await member_wait_timer_persist_delete(gid, int(key[1]), timer_type="member_no_ticket")
            cleared_persisted += 1
        except Exception:
            pass

    try:
        guild = bot.get_guild(gid)
        if guild is not None:
            for channel_id, task in list(_common.KICK_TIMER_TASKS.items()):
                ch = guild.get_channel(int(channel_id))
                if not isinstance(ch, discord.TextChannel) or not is_verification_ticket_channel(ch):
                    continue
                if task and not task.done():
                    task.cancel()
                    cleared_ticket += 1
                _common.KICK_TIMER_TASKS.pop(int(channel_id), None)
                _common.KICK_TIMER_STARTS.pop(int(channel_id), None)
                _common.KICK_TIMER_STARTED_BY.pop(int(channel_id), None)
                try:
                    await kick_timer_persist_delete(int(channel_id))
                except Exception:
                    pass
    except Exception:
        pass

    try:
        res = await _member_wait_timer_persist_select_all_async()
        rows = getattr(res, "data", None) or []
        for row in rows:
            if int(str(row.get("guild_id") or "0") or 0) != gid:
                continue
            timer_type = str(row.get("timer_type") or "").strip().lower()
            if timer_type not in {"join_grace", "member_no_ticket"}:
                continue
            user_id = int(str(row.get("user_id") or "0") or 0)
            if user_id:
                await member_wait_timer_persist_delete(gid, user_id, timer_type=timer_type)
                cleared_persisted += 1
    except Exception:
        pass

    return {
        "join_grace": int(cleared_join),
        "member_no_ticket": int(cleared_member),
        "ticket_no_response": int(cleared_ticket),
        "persisted_rows": int(cleared_persisted),
    }


# ============================================================
# Staff command helpers
# ============================================================
async def _start_ticket_no_response_timer(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    hours: Optional[int] = None,
):
    if not _common._staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel) or not interaction.guild:
        return await interaction.followup.send("❌ Invalid channel.", ephemeral=True)
    if not is_verification_ticket_channel(ch):
        return await interaction.followup.send("❌ That channel isn’t a verification ticket.", ephemeral=True)

    owner = await find_ticket_owner_retry(ch)
    if not owner:
        return await interaction.followup.send("❌ Could not detect the ticket owner.", ephemeral=True)
    if not _member_is_pending_verification(owner):
        return await interaction.followup.send(
            f"❌ {owner.mention} is no longer pending verification.",
            ephemeral=True,
        )

    _cancel_kick_timer(ch.id)

    timer_hours = int(hours) if hours is not None else await _guild_verify_wait_hours(interaction.guild.id)
    if timer_hours <= 0:
        timer_hours = 24

    started_at = now_utc()
    _common.KICK_TIMER_STARTS[ch.id] = started_at

    started_by_id: Optional[int] = None
    try:
        started_by_id = int(getattr(interaction.user, "id", 0) or 0) or None
        if started_by_id:
            _common.KICK_TIMER_STARTED_BY[ch.id] = started_by_id
    except Exception:
        started_by_id = None

    try:
        await kick_timer_persist_upsert(
            channel_id=int(ch.id),
            guild_id=int(interaction.guild.id),
            owner_id=int(owner.id),
            started_at=started_at,
            hours=timer_hours,
            started_by=started_by_id,
        )
    except Exception:
        pass

    task = asyncio.create_task(_kick_after_timer(ch, owner, timer_hours))
    _common._track_task(task, label="kick_timer")
    _common.KICK_TIMER_TASKS[ch.id] = task

    try:
        await ch.send(
            f"⏳ {owner.mention} Your **{timer_hours} hour** time limit starts now.\n"
            "If there’s still no response/submission, I’ll kick, post a transcript, and close this ticket."
        )
    except Exception:
        pass

    return await interaction.followup.send(
        f"✅ Started {timer_hours}h no-response timer for {owner.mention} in {ch.mention}.",
        ephemeral=True,
    )


async def _start_member_no_ticket_timer_slash(
    interaction: discord.Interaction,
    *,
    user: discord.Member,
    channel: Optional[discord.TextChannel] = None,
    hours: Optional[int] = None,
):
    if not _common._staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        return await interaction.followup.send(
            "❌ In non-ticket mode you must run this in a text channel.",
            ephemeral=True,
        )

    if getattr(user, "bot", False):
        return await interaction.followup.send("❌ Bots cannot use verification timers.", ephemeral=True)
    if not _member_is_pending_verification(user):
        return await interaction.followup.send(
            f"❌ {user.mention} is not currently pending verification.",
            ephemeral=True,
        )
    open_ticket = await _resolve_open_verification_ticket_channel(user)
    if open_ticket is not None:
        return await interaction.followup.send(
            f"❌ {user.mention} already has an open verification ticket: {open_ticket.mention}\n"
            "Run the timer inside that ticket instead.",
            ephemeral=True,
        )

    timer_hours = int(hours) if hours is not None else await _guild_verify_wait_hours(member.guild.id)
    if timer_hours <= 0:
        timer_hours = 24

    started = await _start_member_no_ticket_timer(
        member=user,
        source_channel=ch,
        hours=timer_hours,
        started_by=int(getattr(interaction.user, "id", 0) or 0) or None,
        announce=True,
        cancel_join_grace=True,
    )
    if not started:
        return await interaction.followup.send(
            f"❌ Could not start a no-ticket verification timer for {user.mention}.",
            ephemeral=True,
        )

    return await interaction.followup.send(
        f"✅ Started a **{timer_hours}h** no-ticket verification timer for {user.mention} in {ch.mention}.",
        ephemeral=True,
    )


async def _start_no_response_timer(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    hours: Optional[int] = None,
    user: Optional[discord.Member] = None,
):
    ch = channel or interaction.channel
    if isinstance(ch, discord.TextChannel) and is_verification_ticket_channel(ch):
        return await _start_ticket_no_response_timer(interaction, channel=ch, hours=hours)

    if not isinstance(user, discord.Member):
        if not interaction.response.is_done():
            return await interaction.response.send_message(
                "❌ Outside a verification ticket, you must provide `user:`.",
                ephemeral=True,
            )
        return await interaction.followup.send(
            "❌ Outside a verification ticket, you must provide `user:`.",
            ephemeral=True,
        )

    return await _start_member_no_ticket_timer_slash(
        interaction,
        user=user,
        channel=ch if isinstance(ch, discord.TextChannel) else None,
        hours=hours,
    )


async def _cancel_no_response_timer(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    user: Optional[discord.Member] = None,
):
    if not _common._staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    ch = channel or interaction.channel
    if isinstance(ch, discord.TextChannel) and is_verification_ticket_channel(ch):
        if _cancel_kick_timer(ch.id):
            _common.KICK_TIMER_STARTS.pop(ch.id, None)
            _common.KICK_TIMER_TASKS.pop(ch.id, None)
            _common.KICK_TIMER_STARTED_BY.pop(ch.id, None)
            try:
                await kick_timer_persist_delete(int(ch.id))
            except Exception:
                pass
            try:
                await ch.send("🛑 No-response timer cancelled by staff.")
            except Exception:
                pass
            return await interaction.followup.send("✅ Ticket timer cancelled.", ephemeral=True)

        return await interaction.followup.send(
            "ℹ️ No ticket timer was running for that channel.",
            ephemeral=True,
        )

    if not isinstance(user, discord.Member):
        return await interaction.followup.send(
            "❌ Outside a verification ticket, you must provide `user:` to cancel their no-ticket timer.",
            ephemeral=True,
        )

    cancelled = await cancel_verification_wait_timers_for_member(int(user.guild.id), int(user.id))
    if cancelled:
        try:
            if isinstance(ch, discord.TextChannel):
                await ch.send(f"🛑 Verification wait timer cancelled for {user.mention}.")
        except Exception:
            pass
        return await interaction.followup.send(
            f"✅ Cancelled verification wait timers for {user.mention}.",
            ephemeral=True,
        )

    return await interaction.followup.send(
        f"ℹ️ No active verification wait timers were found for {user.mention}.",
        ephemeral=True,
    )


# ============================================================
# Startup resume hook
# ============================================================
@bot.listen("on_ready")
async def _resume_kick_timers_on_ready():
    if getattr(bot, "_kick_timer_resume_boot_started", False):
        return

    bot._kick_timer_resume_boot_started = True

    async def _run():
        try:
            await asyncio.sleep(2)
            await kick_timer_resume_all()
            await member_wait_timer_resume_all()
        except Exception as e:
            print("⚠️ kick timer boot resume failed:", repr(e))

    task = asyncio.create_task(_run())
    _common._track_task(task, label="kick_timer_resume_boot")


# ============================================================
# Slash command registration
# ============================================================
_REGISTERED = False


def register_kick_timer_commands(_bot: Any = None, tree: Any = None) -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    target_tree = tree or bot.tree

    @target_tree.command(
        name="start_no_response_timer",
        description="(Staff) Start a verification timer for a ticket, or for an unverified user in a normal channel.",
    )
    @app_commands.describe(
        channel="Ticket channel or text channel (leave empty to use current channel)",
        hours="Hours until kick/removal (default 24)",
        user="Required outside ticket channels: the unverified member",
    )
    async def start_no_response_timer_slash(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        hours: Optional[int] = None,
        user: Optional[discord.Member] = None,
    ):
        return await _start_no_response_timer(
            interaction,
            channel=channel,
            hours=hours,
            user=user,
        )

    @target_tree.command(
        name="cancel_no_response_timer",
        description="(Staff) Cancel a verification timer for a ticket or for an unverified user.",
    )
    @app_commands.describe(
        channel="Ticket channel or text channel (leave empty to use current channel)",
        user="Required outside ticket channels: the unverified member",
    )
    async def cancel_no_response_timer_slash(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        user: Optional[discord.Member] = None,
    ):
        return await _cancel_no_response_timer(
            interaction,
            channel=channel,
            user=user,
        )

    _REGISTERED = True
    print("✅ commands_ext.kick_timers: registered kick timer commands")


__all__ = [
    "kick_timer_persist_upsert",
    "kick_timer_persist_delete",
    "kick_timer_resume_all",
    "member_wait_timer_resume_all",
    "start_join_grace_then_kick_timer_for_member",
    "cancel_verification_wait_timers_for_member",
    "verification_wait_timer_summary",
    "clear_verification_wait_timers_for_guild",
    "_start_no_response_timer",
    "_cancel_no_response_timer",
    "_start_ticket_no_response_timer",
    "_start_member_no_ticket_timer",
    "_kick_after_timer",
    "_cancel_kick_timer",
    "register_kick_timer_commands",
]
