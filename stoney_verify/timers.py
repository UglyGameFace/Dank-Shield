from .globals import *

from typing import Optional, Any
import asyncio

# ✅ Kick timer persistence (Supabase) + resume on restart
# =========================

# Transcripts are required when the timer expires (before deleting the ticket)
from .transcripts import send_tickettool_style_transcript


# ---------------------------
# TASK TRACKING (avoids "task was destroyed" noise)
# (kept local so timers.py never depends on commands.py)
# ---------------------------
_BACKGROUND_TASKS = set()

def _track_task(task, label: str = "task"):
    """Track background tasks so they don't get GC'd. Safe no-op if task is None."""
    try:
        if task is None:
            return
        _BACKGROUND_TASKS.add(task)

        def _done(t):
            try:
                _BACKGROUND_TASKS.discard(t)
                exc = t.exception()
                if exc:
                    print(f"⚠️ background task '{label}' raised:", repr(exc))
            except Exception:
                pass

        task.add_done_callback(_done)
    except Exception:
        pass


def _kick_timer_sb():
    return get_supabase()


def kick_timer_persist_upsert(
    *,
    channel_id: int,
    guild_id: int,
    owner_id: int,
    started_at: datetime,
    hours: int,
    started_by: Optional[int] = None,
) -> None:
    if not PERSIST_KICK_TIMERS:
        return
    sb = _kick_timer_sb()
    if sb is None:
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
        sb.table(KICK_TIMER_TABLE).upsert(payload, on_conflict="channel_id").execute()
    except Exception as e:
        global KICK_TIMER_PERSIST_AVAILABLE, KICK_TIMER_PERSIST_DISABLED_REASON
        KICK_TIMER_PERSIST_AVAILABLE = False
        KICK_TIMER_PERSIST_DISABLED_REASON = str(e)


def kick_timer_persist_delete(channel_id: int) -> None:
    if not PERSIST_KICK_TIMERS:
        return
    sb = _kick_timer_sb()
    if sb is None:
        return
    try:
        sb.table(KICK_TIMER_TABLE).delete().eq("channel_id", str(channel_id)).execute()
    except Exception:
        pass


async def kick_timer_resume_all() -> None:
    """On boot: reload persisted kick timers and reschedule tasks."""
    if not PERSIST_KICK_TIMERS:
        return
    sb = _kick_timer_sb()
    if sb is None:
        return

    # Avoid double-resume if multiple on_ready handlers fire
    if getattr(bot, "_kick_timer_resume_ran", False):
        return
    bot._kick_timer_resume_ran = True

    try:
        res = sb.table(KICK_TIMER_TABLE).select("*").execute()
        rows = getattr(res, "data", None) or []
    except Exception as e:
        global KICK_TIMER_PERSIST_AVAILABLE, KICK_TIMER_PERSIST_DISABLED_REASON
        KICK_TIMER_PERSIST_AVAILABLE = False
        KICK_TIMER_PERSIST_DISABLED_REASON = str(e)
        return

    # Best-effort: schedule tasks that still have a valid channel + owner.
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

            # Already running in-memory
            if ch_id in KICK_TIMER_TASKS and KICK_TIMER_TASKS[ch_id] and not KICK_TIMER_TASKS[ch_id].done():
                continue

            g = bot.get_guild(g_id)
            if not g:
                continue
            ch = g.get_channel(ch_id)
            if not isinstance(ch, discord.TextChannel):
                # stale row
                kick_timer_persist_delete(ch_id)
                continue

            owner = g.get_member(o_id)
            if not owner:
                # stale row
                kick_timer_persist_delete(ch_id)
                continue

            KICK_TIMER_STARTS[ch_id] = started_at
            try:
                starter = r.get("started_by")
                if starter:
                    KICK_TIMER_STARTED_BY[ch_id] = int(str(starter))
            except Exception:
                pass

            task = asyncio.create_task(_kick_after_timer(ch, owner, hrs))
            _track_task(task, label="kick_timer_resume")
            KICK_TIMER_TASKS[ch_id] = task
            scheduled += 1
        except Exception:
            continue

    if scheduled:
        print(f"⏳ Resumed {scheduled} kick timer(s) from Supabase.")


@bot.event
async def on_ready():
    # Let caches settle, then resume timers once.
    async def _run():
        try:
            await asyncio.sleep(2)
            await kick_timer_resume_all()
        except Exception:
            pass

    t = asyncio.create_task(_run())
    _track_task(t, label="kick_timer_resume_boot")


# ✅ 24H NO-RESPONSE TIMER
# =========================
async def _user_responded_or_submitted(channel: discord.TextChannel, owner: discord.Member, since: datetime) -> bool:
    """
    Returns True if:
    - owner posted any message since `since`, OR
    - any webhook submission message containing a token appears since `since`, OR
    - ✅ button activity occurred in this ticket since `since`
    """
    try:
        last = TICKET_LAST_ACTIVITY.get(channel.id)
        if last and last >= since:
            return True
    except Exception:
        pass

    try:
        async for m in channel.history(limit=500, after=since, oldest_first=True):
            try:
                if m.author and getattr(m.author, "id", None) == owner.id:
                    return True
            except Exception:
                pass
            try:
                if extract_token_from_message(m):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


async def _kick_after_timer(channel: discord.TextChannel, owner: discord.Member, hours: int):
    # Defensive: normalize hours
    try:
        hours = int(hours)
    except Exception:
        hours = int(VERIFY_KICK_HOURS or 24)
    if hours <= 0:
        hours = 24

    start = KICK_TIMER_STARTS.get(channel.id, now_utc())
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

        # Channel was deleted
        if not guild.get_channel(channel.id):
            return

        try:
            owner_now = guild.get_member(owner.id)
            if not owner_now:
                return
        except Exception:
            return

        # If they responded or submitted anything, abort
        if await _user_responded_or_submitted(channel, owner_now, start):
            return

        kick_ok = False
        kick_err: Optional[str] = None

        if not me.guild_permissions.kick_members:
            kick_err = "Bot lacks Kick Members permission"
            try:
                await channel.send("⚠️ No response detected, but I lack **Kick Members** permission.")
            except Exception:
                pass
        else:
            try:
                await guild.kick(owner_now, reason=KICK_REASON)
                kick_ok = True
                try:
                    await channel.send(f"👢 {owner_now.mention} was kicked for failing to respond within **{hours} hours**.")
                except Exception:
                    pass
            except discord.Forbidden:
                kick_err = "Forbidden (role hierarchy / missing perms)"
                try:
                    await channel.send("⚠️ Kick failed (Forbidden). Check **Kick Members** + role hierarchy.")
                except Exception:
                    pass
            except discord.HTTPException as e:
                kick_err = str(e)
                try:
                    await channel.send(f"⚠️ Kick failed: {e}")
                except Exception:
                    pass

        starter_member: Optional[discord.Member] = None
        try:
            starter_id = KICK_TIMER_STARTED_BY.get(channel.id)
            if starter_id:
                starter_member = guild.get_member(int(starter_id))
        except Exception:
            starter_member = None

        decision = f"NO RESPONSE ({hours}H TIMER)"
        if not kick_ok:
            decision = f"{decision} — KICK FAILED" + (f" ({kick_err})" if kick_err else "")

        # Transcript BEFORE delete (required)
        try:
            await send_tickettool_style_transcript(
                channel,
                owner_now,
                closed_by=starter_member,
                decision=decision,
            )
        except Exception as e:
            print("⚠️ Transcript routing failed (timer expiry):", e)

        try:
            await channel.delete(reason="Verification ticket closed after 24h no-response timer")
            RUNTIME_STATS["tickets_closed"] += 1
        except discord.Forbidden:
            try:
                await channel.send("⚠️ I could not delete this ticket (missing **Manage Channels**). Transcript was still posted (if configured).")
            except Exception:
                pass
        except Exception as e:
            print("⚠️ Channel delete failed (timer expiry):", e)

    finally:
        # Always cleanup persisted + in-memory state
        try:
            kick_timer_persist_delete(int(channel.id))
        except Exception:
            pass
        KICK_TIMER_TASKS.pop(channel.id, None)
        KICK_TIMER_STARTS.pop(channel.id, None)
        KICK_TIMER_STARTED_BY.pop(channel.id, None)


def _cancel_kick_timer(channel_id: int) -> bool:
    t = KICK_TIMER_TASKS.get(channel_id)
    if t and not t.done():
        t.cancel()
        return True
    return False


# =========================