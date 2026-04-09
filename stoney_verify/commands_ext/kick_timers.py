from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import _parse_iso_datetime, get_supabase
from ..tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
)
from ..transcripts import send_tickettool_style_transcript
from . import common as _common

try:
    from ..tickets_new.service import find_open_ticket_for_owner
except Exception:
    find_open_ticket_for_owner = None  # type: ignore


# ============================================================
# Shared in-memory registries for non-ticket verification timers
# ============================================================
_JOIN_GRACE_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_JOIN_GRACE_STARTS: Dict[Tuple[int, int], datetime] = {}
_JOIN_GRACE_SOURCE_CHANNELS: Dict[Tuple[int, int], int] = {}

_MEMBER_NO_TICKET_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_MEMBER_NO_TICKET_STARTS: Dict[Tuple[int, int], datetime] = {}
_MEMBER_NO_TICKET_SOURCE_CHANNELS: Dict[Tuple[int, int], int] = {}
_MEMBER_NO_TICKET_STARTED_BY: Dict[Tuple[int, int], int] = {}


# ============================================================
# Kick timer persistence helpers (ticket-channel timers only)
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


async def kick_timer_resume_all() -> None:
    """
    On boot: reload persisted ticket-channel kick timers and reschedule tasks.
    """
    if not _common.PERSIST_KICK_TIMERS:
        return

    sb = _kick_timer_sb()
    if sb is None:
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

    for r in rows:
        try:
            ch_id = int(str(r.get("channel_id") or "0") or 0)
            g_id = int(str(r.get("guild_id") or "0") or 0)
            o_id = int(str(r.get("owner_id") or "0") or 0)
            hrs = int(r.get("hours") or VERIFY_KICK_HOURS or 24)
            started_at = _parse_iso_datetime(r.get("started_at")) or now_utc()

            if not ch_id or not g_id or not o_id:
                continue

            existing_task = _common.KICK_TIMER_TASKS.get(ch_id)
            if existing_task and not existing_task.done():
                continue

            guild = bot.get_guild(g_id)
            if guild is None:
                print(f"⚠️ kick timer resume: guild unavailable guild={g_id} channel={ch_id}")
                continue

            channel: Optional[discord.TextChannel] = None
            try:
                raw_channel = guild.get_channel(ch_id)
                if isinstance(raw_channel, discord.TextChannel):
                    channel = raw_channel
                else:
                    fetched_channel = await bot.fetch_channel(ch_id)
                    if isinstance(fetched_channel, discord.TextChannel):
                        channel = fetched_channel
            except discord.NotFound:
                print(f"⚠️ kick timer resume: stale channel row channel={ch_id}, deleting persisted timer")
                await kick_timer_persist_delete(ch_id)
                continue
            except Exception as e:
                print(
                    f"⚠️ kick timer resume: channel fetch failed guild={g_id} "
                    f"channel={ch_id} error={repr(e)}"
                )
                continue

            if not isinstance(channel, discord.TextChannel):
                await kick_timer_persist_delete(ch_id)
                continue

            owner = guild.get_member(o_id)
            if owner is None:
                try:
                    owner = await guild.fetch_member(o_id)
                except discord.NotFound:
                    print(f"⚠️ kick timer resume: owner missing owner={o_id} channel={ch_id}, deleting persisted timer")
                    await kick_timer_persist_delete(ch_id)
                    continue
                except Exception as e:
                    print(
                        f"⚠️ kick timer resume: owner fetch failed guild={g_id} "
                        f"user={o_id} channel={ch_id} error={repr(e)}"
                    )
                    continue

            if owner is None:
                continue

            if not _member_is_pending_verification(owner):
                print(
                    f"ℹ️ kick timer resume: user no longer pending verification "
                    f"guild={g_id} user={o_id} channel={ch_id}; deleting persisted timer"
                )
                await kick_timer_persist_delete(ch_id)
                continue

            _common.KICK_TIMER_STARTS[channel.id] = started_at

            try:
                starter = r.get("started_by")
                if starter:
                    _common.KICK_TIMER_STARTED_BY[channel.id] = int(str(starter))
            except Exception:
                pass

            task = asyncio.create_task(_kick_after_timer(channel, owner, hrs))
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


@bot.listen("on_ready")
async def _resume_kick_timers_on_ready():
    if getattr(bot, "_kick_timer_resume_boot_started", False):
        return

    bot._kick_timer_resume_boot_started = True

    async def _run():
        try:
            await asyncio.sleep(2)
            await kick_timer_resume_all()
        except Exception as e:
            print("⚠️ kick timer boot resume failed:", repr(e))

    task = asyncio.create_task(_run())
    _common._track_task(task, label="kick_timer_resume_boot")


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
            raw = os.getenv(key, "")
            value = int(str(raw).strip() or 0)
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
            raw = os.getenv(key, "")
            value = int(str(raw).strip() or 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 24


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
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
    candidate_ids = []

    for key in (
        "UNVERIFIED_CHAT_CHANNEL_ID",
        "UNVERIFIED_ONLY_CHAT_CHANNEL_ID",
        "VERIFY_WAIT_CHANNEL_ID",
        "VERIFY_HELP_CHANNEL_ID",
        "UNVERIFIED_CHANNEL_ID",
    ):
        try:
            raw = globals().get(key)
            cid = int(str(raw or "0").strip() or 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass

        try:
            raw = os.getenv(key, "")
            cid = int(str(raw or "0").strip() or 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass

    for cid in candidate_ids:
        ch = await _resolve_text_channel_by_id(guild, cid)
        if ch:
            return ch

    exact_names = {
        "unverified-chat",
        "unverified",
        "verify-chat",
        "verification-chat",
    }
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
            row = await find_open_ticket_for_owner(
                guild_id=member.guild.id,
                owner_id=member.id,
            )
        except Exception:
            row = None
    except Exception:
        row = None

    if not isinstance(row, dict):
        return None

    ch_id = 0
    try:
        ch_id = int(str(row.get("channel_id") or row.get("discord_thread_id") or "0") or 0)
    except Exception:
        ch_id = 0

    if ch_id <= 0:
        return None

    return await _resolve_text_channel_by_id(member.guild, ch_id)


async def _send_notice(channel: Optional[discord.TextChannel], content: str) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(content)
    except Exception:
        pass


def _cancel_join_grace_timer(guild_id: int, user_id: int) -> bool:
    key = _member_key(guild_id, user_id)
    task = _JOIN_GRACE_TASKS.get(key)
    if task and not task.done():
        task.cancel()
        return True
    return False


def _cancel_member_no_ticket_timer(guild_id: int, user_id: int) -> bool:
    key = _member_key(guild_id, user_id)
    task = _MEMBER_NO_TICKET_TASKS.get(key)
    if task and not task.done():
        task.cancel()
        return True
    return False


# ============================================================
# Ticket-channel 24h no-response timer helpers
# ============================================================
async def _user_responded_or_submitted(
    channel: discord.TextChannel,
    owner: discord.Member,
    since: datetime,
) -> bool:
    """
    Returns True only if:
    - owner posted any message since `since`, OR
    - any webhook submission message containing a token appears since `since`

    IMPORTANT:
    We intentionally do NOT trust generic TICKET_LAST_ACTIVITY here,
    because staff/bot activity can update it and incorrectly cancel the kick timer.
    """
    try:
        async for m in channel.history(limit=500, after=since, oldest_first=True):
            try:
                if m.author and getattr(m.author, "id", None) == owner.id:
                    return True
            except Exception:
                pass

            try:
                if _common.extract_token_from_message(m):
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
        remaining = (end_at - now_utc()).total_seconds()

        try:
            await asyncio.sleep(max(0, remaining))
        except asyncio.CancelledError:
            return

        guild = channel.guild
        me = guild.me

        if not me:
            return

        channel_now: Optional[discord.TextChannel] = channel

        try:
            cached_channel = guild.get_channel(channel.id)
            if isinstance(cached_channel, discord.TextChannel):
                channel_now = cached_channel
            else:
                fetched_channel = await bot.fetch_channel(channel.id)
                if isinstance(fetched_channel, discord.TextChannel):
                    channel_now = fetched_channel
        except discord.NotFound:
            return
        except Exception:
            channel_now = channel

        if not isinstance(channel_now, discord.TextChannel):
            return

        owner_now = guild.get_member(owner.id)
        if owner_now is None:
            try:
                owner_now = await guild.fetch_member(owner.id)
            except discord.NotFound:
                return
            except Exception:
                owner_now = owner if isinstance(owner, discord.Member) else None

        if owner_now is None:
            return

        if not _member_is_pending_verification(owner_now):
            print(
                f"ℹ️ kick timer expired but user no longer pending verification "
                f"guild={guild.id} user={owner_now.id} channel={channel_now.id}"
            )
            return

        if await _user_responded_or_submitted(channel_now, owner_now, start):
            print(
                f"ℹ️ kick timer cancelled by owner activity/submission "
                f"guild={guild.id} user={owner_now.id} channel={channel_now.id}"
            )
            return

        kick_ok = False
        kick_err: Optional[str] = None

        if not me.guild_permissions.kick_members:
            kick_err = "Bot lacks Kick Members permission"
            try:
                await channel_now.send(
                    "⚠️ No response detected, but I lack **Kick Members** permission."
                )
            except Exception:
                pass
        else:
            try:
                await guild.kick(
                    owner_now,
                    reason=str(
                        globals().get("KICK_REASON")
                        or "Verification no-response timer expired"
                    ),
                )
                kick_ok = True
                try:
                    await channel_now.send(
                        f"👢 {owner_now.mention} was kicked for failing to respond within **{hours} hours**."
                    )
                except Exception:
                    pass
            except discord.Forbidden:
                kick_err = "Forbidden (role hierarchy / missing perms)"
                try:
                    await channel_now.send(
                        "⚠️ Kick failed (Forbidden). Check **Kick Members** + role hierarchy."
                    )
                except Exception:
                    pass
            except discord.HTTPException as e:
                kick_err = str(e)
                try:
                    await channel_now.send(f"⚠️ Kick failed: {e}")
                except Exception:
                    pass

        starter_member: Optional[discord.Member] = None
        try:
            starter_id = _common.KICK_TIMER_STARTED_BY.get(channel_now.id)
            if starter_id:
                starter_member = guild.get_member(int(starter_id))
                if starter_member is None:
                    try:
                        starter_member = await guild.fetch_member(int(starter_id))
                    except Exception:
                        starter_member = None
        except Exception:
            starter_member = None

        decision = f"NO RESPONSE ({hours}H TIMER)"
        if not kick_ok:
            decision = f"{decision} — KICK FAILED" + (f" ({kick_err})" if kick_err else "")

        try:
            await send_tickettool_style_transcript(
                channel_now,
                owner_now,
                closed_by=starter_member,
                decision=decision,
            )
        except Exception as e:
            print("⚠️ Transcript routing failed (timer expiry):", repr(e))

        try:
            await channel_now.delete(
                reason=f"Verification ticket closed after {hours}h no-response timer"
            )
            _common.RUNTIME_STATS["tickets_closed"] = _common.RUNTIME_STATS.get("tickets_closed", 0) + 1
        except discord.Forbidden:
            try:
                await channel_now.send(
                    "⚠️ I could not delete this ticket (missing **Manage Channels**). "
                    "Transcript was still posted (if configured)."
                )
            except Exception:
                pass
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


# ============================================================
# Non-ticket member-based verification wait timers
# ============================================================
async def _member_no_ticket_timer_task(
    guild_id: int,
    user_id: int,
    hours: int,
) -> None:
    key = _member_key(guild_id, user_id)
    start = _MEMBER_NO_TICKET_STARTS.get(key, now_utc())

    try:
        end_at = start + timedelta(hours=max(0, hours))
        remaining = (end_at - now_utc()).total_seconds()

        try:
            await asyncio.sleep(max(0, remaining))
        except asyncio.CancelledError:
            return

        guild = bot.get_guild(int(guild_id))
        if guild is None:
            return

        member = await _fetch_member_if_present(guild, int(user_id))
        if member is None:
            return

        if not _member_is_pending_verification(member):
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
                await _send_notice(
                    source_channel,
                    f"⚠️ Failed to kick {member.mention}: {e}",
                )

        if not kick_ok and kick_err:
            print(
                f"⚠️ member no-ticket timer expired but kick failed "
                f"guild={guild_id} user={user_id} error={kick_err}"
            )

    finally:
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
) -> bool:
    if getattr(member, "bot", False):
        return False

    if not _member_is_pending_verification(member):
        return False

    open_ticket = await _resolve_open_verification_ticket_channel(member)
    if open_ticket is not None:
        return False

    key = _member_key(member.guild.id, member.id)

    if cancel_join_grace:
        _cancel_join_grace_timer(member.guild.id, member.id)

    _cancel_member_no_ticket_timer(member.guild.id, member.id)

    hrs = int(hours or _default_no_ticket_hours())
    if hrs <= 0:
        hrs = 24

    notice_channel = await _resolve_notice_channel(
        member.guild,
        explicit_channel=source_channel,
        stored_channel_id=_MEMBER_NO_TICKET_SOURCE_CHANNELS.get(key, 0),
    )

    _MEMBER_NO_TICKET_STARTS[key] = now_utc()
    _MEMBER_NO_TICKET_SOURCE_CHANNELS[key] = int(getattr(notice_channel, "id", 0) or 0)
    if started_by:
        _MEMBER_NO_TICKET_STARTED_BY[key] = int(started_by)

    task = asyncio.create_task(
        _member_no_ticket_timer_task(member.guild.id, member.id, hrs)
    )
    _common._track_task(task, label="member_no_ticket_timer")
    _MEMBER_NO_TICKET_TASKS[key] = task

    if announce and isinstance(notice_channel, discord.TextChannel):
        await _send_notice(
            notice_channel,
            f"⏳ {member.mention} Your **{hrs} hour** verification timer starts now.\n"
            "If you still have no verification progress by the end of it, you may be removed.",
        )

    print(
        f"⏳ Started member no-ticket timer guild={member.guild.id} "
        f"user={member.id} hours={hrs} channel={getattr(notice_channel, 'id', None)}"
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
        end_at = start + timedelta(minutes=max(0, grace_minutes))
        remaining = (end_at - now_utc()).total_seconds()

        try:
            await asyncio.sleep(max(0, remaining))
        except asyncio.CancelledError:
            return

        guild = bot.get_guild(int(guild_id))
        if guild is None:
            return

        member = await _fetch_member_if_present(guild, int(user_id))
        if member is None:
            return

        if not _member_is_pending_verification(member):
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
            hours=_default_no_ticket_hours(),
            announce=True,
            cancel_join_grace=False,
        )

    finally:
        _JOIN_GRACE_TASKS.pop(key, None)
        _JOIN_GRACE_STARTS.pop(key, None)
        _JOIN_GRACE_SOURCE_CHANNELS.pop(key, None)


async def start_join_grace_then_kick_timer_for_member(
    member: discord.Member,
    source_channel: Optional[discord.TextChannel] = None,
    grace_minutes: Optional[int] = None,
) -> bool:
    """
    Starts the 1-hour "create a ticket / begin verification progress" grace timer.
    If that expires and the member is still pending verification with no ticket,
    a 24h member-based timer starts in the provided fallback channel.
    """
    if getattr(member, "bot", False):
        return False

    if not _member_is_pending_verification(member):
        return False

    open_ticket = await _resolve_open_verification_ticket_channel(member)
    if open_ticket is not None:
        return False

    key = _member_key(member.guild.id, member.id)

    _cancel_join_grace_timer(member.guild.id, member.id)
    _cancel_member_no_ticket_timer(member.guild.id, member.id)

    mins = int(grace_minutes or _default_join_grace_minutes())
    if mins <= 0:
        mins = 60

    notice_channel = await _resolve_notice_channel(member.guild, explicit_channel=source_channel)

    _JOIN_GRACE_STARTS[key] = now_utc()
    _JOIN_GRACE_SOURCE_CHANNELS[key] = int(getattr(notice_channel, "id", 0) or 0)

    task = asyncio.create_task(
        _join_grace_then_start_member_timer_task(member.guild.id, member.id, mins)
    )
    _common._track_task(task, label="join_grace_timer")
    _JOIN_GRACE_TASKS[key] = task

    print(
        f"⏳ Started join grace timer guild={member.guild.id} "
        f"user={member.id} minutes={mins} channel={getattr(notice_channel, 'id', None)}"
    )
    return True


async def cancel_verification_wait_timers_for_member(guild_id: int, owner_id: int) -> bool:
    """
    Best-effort canceller for:
    - join grace timers
    - member no-ticket timers
    - ticket-based 24h timers for any verification ticket owned by the member
    """
    gid = int(guild_id)
    oid = int(owner_id)
    cancelled_any = False
    key = _member_key(gid, oid)

    try:
        if _cancel_join_grace_timer(gid, oid):
            cancelled_any = True
        _JOIN_GRACE_TASKS.pop(key, None)
        _JOIN_GRACE_STARTS.pop(key, None)
        _JOIN_GRACE_SOURCE_CHANNELS.pop(key, None)
    except Exception:
        pass

    try:
        if _cancel_member_no_ticket_timer(gid, oid):
            cancelled_any = True
        _MEMBER_NO_TICKET_TASKS.pop(key, None)
        _MEMBER_NO_TICKET_STARTS.pop(key, None)
        _MEMBER_NO_TICKET_SOURCE_CHANNELS.pop(key, None)
        _MEMBER_NO_TICKET_STARTED_BY.pop(key, None)
    except Exception:
        pass

    guild = bot.get_guild(gid)
    if guild is not None:
        channel_ids = list(_common.KICK_TIMER_TASKS.keys())

        for channel_id in channel_ids:
            try:
                ch = guild.get_channel(int(channel_id))
                if not isinstance(ch, discord.TextChannel):
                    continue

                if not is_verification_ticket_channel(ch):
                    continue

                owner = await find_ticket_owner_retry(ch)
                if not owner or int(owner.id) != oid:
                    continue

                if _cancel_kick_timer(ch.id):
                    cancelled_any = True

                _common.KICK_TIMER_TASKS.pop(ch.id, None)
                _common.KICK_TIMER_STARTS.pop(ch.id, None)
                _common.KICK_TIMER_STARTED_BY.pop(ch.id, None)

                try:
                    await kick_timer_persist_delete(int(ch.id))
                except Exception:
                    pass

                try:
                    await ch.send(
                        "🛑 Verification wait timer cancelled because ticket flow is now active."
                    )
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


# ============================================================
# Staff slash command helpers
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
        return await interaction.followup.send(
            "❌ That channel isn’t a verification ticket.",
            ephemeral=True,
        )

    owner = await find_ticket_owner_retry(ch)
    if not owner:
        return await interaction.followup.send(
            "❌ Could not detect the ticket owner.",
            ephemeral=True,
        )

    if not _member_is_pending_verification(owner):
        return await interaction.followup.send(
            f"❌ {owner.mention} is no longer pending verification.",
            ephemeral=True,
        )

    _cancel_kick_timer(ch.id)

    hrs = int(hours or VERIFY_KICK_HOURS)
    if hrs <= 0:
        hrs = 24

    start = now_utc()
    _common.KICK_TIMER_STARTS[ch.id] = start

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
            started_at=start,
            hours=hrs,
            started_by=started_by_id,
        )
    except Exception:
        pass

    task = asyncio.create_task(_kick_after_timer(ch, owner, hrs))
    _common._track_task(task, label="kick_timer")
    _common.KICK_TIMER_TASKS[ch.id] = task

    try:
        await ch.send(
            f"⏳ {owner.mention} Your **{hrs} hour** time limit starts now.\n"
            "If there’s still no response/submission, I’ll kick, post a transcript, and close this ticket."
        )
    except Exception:
        pass

    return await interaction.followup.send(
        f"✅ Started {hrs}h no-response timer for {owner.mention} in {ch.mention}.",
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

    hrs = int(hours or _default_no_ticket_hours())
    if hrs <= 0:
        hrs = 24

    started = await _start_member_no_ticket_timer(
        member=user,
        source_channel=ch,
        hours=hrs,
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
        f"✅ Started a **{hrs}h** no-ticket verification timer for {user.mention} in {ch.mention}.",
        ephemeral=True,
    )


async def _start_no_response_timer(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    hours: Optional[int] = None,
    user: Optional[discord.Member] = None,
):
    """
    Smart dispatcher:
    - In a verification ticket: starts the ticket-based 24h timer.
    - In a normal text channel: requires `user` and starts the no-ticket member timer.
    """
    ch = channel or interaction.channel

    if isinstance(ch, discord.TextChannel) and is_verification_ticket_channel(ch):
        return await _start_ticket_no_response_timer(
            interaction,
            channel=ch,
            hours=hours,
        )

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

    cancelled = await cancel_verification_wait_timers_for_member(
        int(user.guild.id),
        int(user.id),
    )

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
# Explicit command registration
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
    "start_join_grace_then_kick_timer_for_member",
    "cancel_verification_wait_timers_for_member",
    "_start_no_response_timer",
    "_cancel_no_response_timer",
    "_start_ticket_no_response_timer",
    "_start_member_no_ticket_timer",
    "_kick_after_timer",
    "_cancel_kick_timer",
    "register_kick_timer_commands",
]