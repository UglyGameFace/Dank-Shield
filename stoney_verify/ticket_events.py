from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, Optional, Set

import discord

from .globals import TICKET_CATEGORY_ID, TRANSCRIPTS_CHANNEL_ID, bot
from .tickets_new.service import (
    mark_ticket_closed,
    mark_ticket_deleted,
    reopen_ticket_channel,
)
from .tickets_new.sync_service import sync_one_ticket_channel
from .tickets_new.repository import _find_ticket_row_by_channel_id

# NEW: timer-cancel bridge from active commands_ext timer system
try:
    from .commands_ext.kick_timers import cancel_verification_wait_timers_for_member
except Exception:
    async def cancel_verification_wait_timers_for_member(guild_id: int, user_id: int) -> bool:
        return False


# ============================================================
# ticket_events.py
# ------------------------------------------------------------
# Purpose:
# - thin event bridge for ticket channels
# - no worker/API startup here
# - no duplicate ticket state engine here
# - delegates persistence/state to tickets_new.service + sync_service
# - cancels pending verification wait timers when a real
#   verification ticket is created / resolved for a user
# ============================================================

TICKET_NAME_RE = re.compile(r"^(ticket|closed)-(\d+)$", re.I)
TOPIC_OWNER_ID_RE = re.compile(r"(?:^|[\s|,;])owner_id\s*=\s*(\d+)\b", re.I)
TOPIC_USER_ID_RE = re.compile(r"(?:^|[\s|,;])user_id\s*=\s*(\d+)\b", re.I)
TOPIC_REQUESTER_ID_RE = re.compile(r"(?:^|[\s|,;])requester_id\s*=\s*(\d+)\b", re.I)

_SETUP_DONE = False
_REGISTERED_LISTENERS: Set[str] = set()

_CHANNEL_LOCKS: Dict[int, asyncio.Lock] = {}
_PENDING_SYNC_TASKS: Dict[int, asyncio.Task] = {}
_RECENT_SELF_MUTATIONS: Dict[int, float] = {}

# debounce repeated timer-cancel attempts per channel
_RECENT_TIMER_CANCELS: Dict[int, float] = {}

EVENT_SYNC_DELAY_SECONDS = 0.90
MESSAGE_SYNC_DEBOUNCE_SECONDS = 1.50
SELF_MUTATION_COOLDOWN_SECONDS = 2.50
TIMER_CANCEL_COOLDOWN_SECONDS = 5.00


def _channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _CHANNEL_LOCKS.get(int(channel_id))
    if lock is None:
        lock = asyncio.Lock()
        _CHANNEL_LOCKS[int(channel_id)] = lock
    return lock


def _remember_self_mutation(channel_id: int) -> None:
    _RECENT_SELF_MUTATIONS[int(channel_id)] = time.monotonic()


def _is_recent_self_mutation(channel_id: int) -> bool:
    last = _RECENT_SELF_MUTATIONS.get(int(channel_id))
    if last is None:
        return False
    return (time.monotonic() - last) <= SELF_MUTATION_COOLDOWN_SECONDS


def _cleanup_recent_mutations() -> None:
    now = time.monotonic()
    stale = [
        cid
        for cid, ts in _RECENT_SELF_MUTATIONS.items()
        if (now - ts) > max(SELF_MUTATION_COOLDOWN_SECONDS * 3, 10.0)
    ]
    for cid in stale:
        _RECENT_SELF_MUTATIONS.pop(cid, None)


def _cleanup_recent_timer_cancels() -> None:
    now = time.monotonic()
    stale = [
        cid
        for cid, ts in _RECENT_TIMER_CANCELS.items()
        if (now - ts) > max(TIMER_CANCEL_COOLDOWN_SECONDS * 3, 15.0)
    ]
    for cid in stale:
        _RECENT_TIMER_CANCELS.pop(cid, None)


def _safe_topic(channel: discord.TextChannel) -> str:
    try:
        return str(channel.topic or "")
    except Exception:
        return ""


def _matches_ticket_name(channel_name: str) -> bool:
    try:
        return bool(TICKET_NAME_RE.match(str(channel_name or "").strip()))
    except Exception:
        return False


def _topic_looks_ticketish(topic: str) -> bool:
    text = str(topic or "").lower()
    return "owner_id=" in text and "ticket_number=" in text


def _is_closed_name(name: str) -> bool:
    try:
        return str(name or "").strip().lower().startswith("closed-")
    except Exception:
        return False


def _is_open_name(name: str) -> bool:
    try:
        return str(name or "").strip().lower().startswith("ticket-")
    except Exception:
        return False


def _looks_like_ticket_category(channel: discord.TextChannel) -> bool:
    try:
        if channel.category_id and int(TICKET_CATEGORY_ID or 0):
            if int(channel.category_id) == int(TICKET_CATEGORY_ID):
                return True
    except Exception:
        pass

    try:
        category_name = str(getattr(channel.category, "name", "") or "").lower()
        if "ticket" in category_name:
            return True
    except Exception:
        pass

    return False


def _extract_owner_id_from_topic_text(topic: str) -> int:
    text = str(topic or "").strip()
    if not text:
        return 0

    for rx in (TOPIC_OWNER_ID_RE, TOPIC_USER_ID_RE, TOPIC_REQUESTER_ID_RE):
        try:
            m = rx.search(text)
            if m:
                return int(str(m.group(1) or "0"))
        except Exception:
            continue

    return 0


def _is_recent_timer_cancel(channel_id: int) -> bool:
    last = _RECENT_TIMER_CANCELS.get(int(channel_id))
    if last is None:
        return False
    return (time.monotonic() - last) <= TIMER_CANCEL_COOLDOWN_SECONDS


def _remember_timer_cancel(channel_id: int) -> None:
    _RECENT_TIMER_CANCELS[int(channel_id)] = time.monotonic()


async def _resolve_ticket_owner_id_for_channel(channel: discord.TextChannel) -> int:
    """
    Best-effort owner resolution order:
    1) parse owner_id/user_id/requester_id from channel.topic
    2) load ticket row and inspect common owner fields
    """
    try:
        owner_id = _extract_owner_id_from_topic_text(_safe_topic(channel))
        if owner_id > 0:
            return owner_id
    except Exception:
        pass

    try:
        row = await _find_ticket_row_by_channel_id(channel.id)
    except Exception:
        row = None

    if isinstance(row, dict):
        for key in (
            "user_id",
            "owner_id",
            "requester_id",
            "member_id",
            "creator_id",
        ):
            try:
                val = int(str(row.get(key) or "0") or 0)
                if val > 0:
                    return val
            except Exception:
                continue

        try:
            meta = row.get("meta") or {}
            if isinstance(meta, dict):
                for key in ("user_id", "owner_id", "requester_id", "member_id", "creator_id"):
                    try:
                        val = int(str(meta.get(key) or "0") or 0)
                        if val > 0:
                            return val
                    except Exception:
                        continue
        except Exception:
            pass

    return 0


async def _maybe_cancel_wait_timers_for_ticket_owner(
    channel: discord.TextChannel,
    *,
    source: str,
) -> bool:
    """
    When a real verification ticket exists for a member, cancel any join-grace
    or member-scoped no-response timers for that member.
    """
    if not isinstance(channel, discord.TextChannel):
        return False

    if _is_recent_timer_cancel(channel.id):
        return False

    try:
        owner_id = await _resolve_ticket_owner_id_for_channel(channel)
    except Exception:
        owner_id = 0

    if owner_id <= 0:
        return False

    try:
        cancelled = await cancel_verification_wait_timers_for_member(channel.guild.id, owner_id)
    except Exception as e:
        print(
            f"⚠️ ticket_events timer-cancel failed for channel={channel.id} "
            f"owner={owner_id} source={source}: {repr(e)}"
        )
        return False

    if cancelled:
        _remember_timer_cancel(channel.id)
        print(
            f"⏹️ ticket_events cancelled verification wait timer(s) "
            f"for owner={owner_id} channel={channel.id} source={source}"
        )
        return True

    return False


async def _is_ticket_channel(channel: discord.TextChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        if int(getattr(channel, "id", 0) or 0) == int(TRANSCRIPTS_CHANNEL_ID or 0):
            return False
    except Exception:
        pass

    if _matches_ticket_name(channel.name):
        return True

    if _topic_looks_ticketish(_safe_topic(channel)):
        return True

    if _looks_like_ticket_category(channel):
        # category match is only a hint, so verify through DB if possible
        try:
            row = await _find_ticket_row_by_channel_id(channel.id)
            if row is not None:
                return True
        except Exception:
            pass

        # still allow event-driven sync for ticket-category channels
        return True

    try:
        row = await _find_ticket_row_by_channel_id(channel.id)
        if row is not None:
            return True
    except Exception:
        pass

    return False


def _cancel_pending_sync(channel_id: int) -> None:
    task = _PENDING_SYNC_TASKS.pop(int(channel_id), None)
    if task and not task.done():
        task.cancel()


async def _run_channel_sync(
    channel: discord.TextChannel,
    *,
    source: str,
    reason: str = "",
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    lock = _channel_lock(channel.id)

    async with lock:
        try:
            result = await sync_one_ticket_channel(
                channel,
                source=source,
                dry_run=False,
            )

            rows = result.get("rows", []) or []
            row = rows[0] if rows else {}
            action = str(row.get("action") or "").strip() or "unknown"

            if action not in {"unchanged", "skipped"}:
                extra = f" reason={reason}" if reason else ""
                print(
                    f"🧩 ticket_events sync -> channel={channel.id} "
                    f"name='{channel.name}' action={action}{extra}"
                )

            # After a successful channel sync, try to resolve the owner and
            # cancel any pre-ticket verification wait timers for them.
            try:
                await _maybe_cancel_wait_timers_for_ticket_owner(
                    channel,
                    source=f"sync:{source}",
                )
            except Exception as e:
                print(
                    f"⚠️ ticket_events post-sync timer-cancel failed for channel={channel.id}:",
                    repr(e),
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(
                f"⚠️ ticket_events sync failed for channel={getattr(channel, 'id', '?')}:",
                repr(e),
            )


def _schedule_channel_sync(
    channel: discord.TextChannel,
    *,
    source: str,
    delay: float = EVENT_SYNC_DELAY_SECONDS,
    reason: str = "",
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    _cancel_pending_sync(channel.id)

    async def _job() -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await _run_channel_sync(channel, source=source, reason=reason)
        except asyncio.CancelledError:
            return
        finally:
            existing = _PENDING_SYNC_TASKS.get(int(channel.id))
            current = asyncio.current_task()
            if existing is current:
                _PENDING_SYNC_TASKS.pop(int(channel.id), None)

    task = asyncio.create_task(
        _job(),
        name=f"ticket-events-sync-{channel.id}",
    )
    _PENDING_SYNC_TASKS[int(channel.id)] = task


async def _handle_channel_create(channel: discord.abc.GuildChannel) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    if not await _is_ticket_channel(channel):
        return

    # Best-effort immediate cancel if the owner is already encoded in the topic.
    # This makes the timer drop as soon as the ticket channel exists, without
    # waiting on the DB-backed sync as the only path.
    try:
        await _maybe_cancel_wait_timers_for_ticket_owner(
            channel,
            source="channel_create_immediate",
        )
    except Exception as e:
        print(
            f"⚠️ ticket_events immediate timer-cancel failed for channel={channel.id}:",
            repr(e),
        )

    _schedule_channel_sync(
        channel,
        source="event_channel_create",
        delay=EVENT_SYNC_DELAY_SECONDS,
        reason="channel_create",
    )


async def _handle_channel_delete(channel: discord.abc.GuildChannel) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    _cancel_pending_sync(channel.id)

    tracked = False
    try:
        tracked = await _is_ticket_channel(channel)
    except Exception:
        tracked = _matches_ticket_name(channel.name) or _topic_looks_ticketish(_safe_topic(channel))

    if not tracked:
        return

    lock = _channel_lock(channel.id)
    async with lock:
        try:
            ok = await mark_ticket_deleted(
                channel_id=channel.id,
                deleted_by=None,
                reason="Channel deleted event",
            )
            if ok:
                print(f"🧩 ticket_events delete -> channel={channel.id} name='{channel.name}'")
        except Exception as e:
            print(
                f"⚠️ ticket_events delete handling failed for channel={getattr(channel, 'id', '?')}:",
                repr(e),
            )


async def _handle_channel_update(
    before: discord.abc.GuildChannel,
    after: discord.abc.GuildChannel,
) -> None:
    if not isinstance(before, discord.TextChannel) or not isinstance(after, discord.TextChannel):
        return

    if _is_recent_self_mutation(after.id):
        return

    tracked_before = False
    tracked_after = False

    try:
        tracked_before = await _is_ticket_channel(before)
    except Exception:
        tracked_before = _matches_ticket_name(before.name) or _topic_looks_ticketish(_safe_topic(before))

    try:
        tracked_after = await _is_ticket_channel(after)
    except Exception:
        tracked_after = _matches_ticket_name(after.name) or _topic_looks_ticketish(_safe_topic(after))

    if not tracked_before and not tracked_after:
        return

    meaningful_change = any(
        [
            str(before.name or "") != str(after.name or ""),
            str(before.topic or "") != str(after.topic or ""),
            int(getattr(before, "category_id", 0) or 0) != int(getattr(after, "category_id", 0) or 0),
            before.overwrites != after.overwrites,
        ]
    )

    if not meaningful_change:
        return

    was_closed = _is_closed_name(before.name)
    is_closed = _is_closed_name(after.name)

    lock = _channel_lock(after.id)

    async with lock:
        try:
            if not was_closed and is_closed:
                _remember_self_mutation(after.id)
                ok = await mark_ticket_closed(
                    channel=after,
                    closed_by=None,
                    reason="Channel updated to closed state",
                )
                if ok:
                    print(
                        f"🧩 ticket_events close-detect -> channel={after.id} "
                        f"name='{after.name}'"
                    )
                return

            if was_closed and _is_open_name(after.name):
                _remember_self_mutation(after.id)
                ok = await reopen_ticket_channel(
                    channel=after,
                    owner=None,
                    staff_role_ids=None,
                )
                if ok:
                    print(
                        f"🧩 ticket_events reopen-detect -> channel={after.id} "
                        f"name='{after.name}'"
                    )
                return

        except Exception as e:
            print(
                f"⚠️ ticket_events update transition handling failed for channel={after.id}:",
                repr(e),
            )

    # If the topic/name/category just changed into a usable ticket shape and we
    # can now resolve the owner, cancel timers before the delayed sync too.
    try:
        await _maybe_cancel_wait_timers_for_ticket_owner(
            after,
            source="channel_update_immediate",
        )
    except Exception as e:
        print(
            f"⚠️ ticket_events immediate update timer-cancel failed for channel={after.id}:",
            repr(e),
        )

    _schedule_channel_sync(
        after,
        source="event_channel_update",
        delay=EVENT_SYNC_DELAY_SECONDS,
        reason="channel_update",
    )


async def _handle_message(message: discord.Message) -> None:
    try:
        if message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if getattr(message.author, "bot", False):
            return
    except Exception:
        return

    channel = message.channel

    try:
        if not await _is_ticket_channel(channel):
            return
    except Exception:
        return

    # Sometimes the very first staff/user message lands after the ticket exists
    # and after topic/DB state is stable. Use that as another safe fallback to
    # kill old pre-ticket timers.
    try:
        await _maybe_cancel_wait_timers_for_ticket_owner(
            channel,
            source="message_activity_immediate",
        )
    except Exception as e:
        print(
            f"⚠️ ticket_events message-activity timer-cancel failed for channel={channel.id}:",
            repr(e),
        )

    # Debounced reconciliation for real user/staff activity.
    _schedule_channel_sync(
        channel,
        source="event_message_activity",
        delay=MESSAGE_SYNC_DEBOUNCE_SECONDS,
        reason="message_activity",
    )


def setup(client: discord.Client) -> None:
    global _SETUP_DONE

    if _SETUP_DONE:
        print("ℹ️ ticket_events.setup(bot) already completed; skipping duplicate registration.")
        return

    def _register(event_name: str, coro) -> None:
        if event_name in _REGISTERED_LISTENERS:
            return
        client.add_listener(coro, event_name)
        _REGISTERED_LISTENERS.add(event_name)

    async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
        await _handle_channel_create(channel)

    async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
        await _handle_channel_delete(channel)

    async def on_guild_channel_update(
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        await _handle_channel_update(before, after)

    async def on_message(message: discord.Message) -> None:
        await _handle_message(message)

    _register("on_guild_channel_create", on_guild_channel_create)
    _register("on_guild_channel_delete", on_guild_channel_delete)
    _register("on_guild_channel_update", on_guild_channel_update)
    _register("on_message", on_message)

    _SETUP_DONE = True
    _cleanup_recent_mutations()
    _cleanup_recent_timer_cancels()
    print("✅ ticket_events listeners registered.")