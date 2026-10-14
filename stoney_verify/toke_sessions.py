from __future__ import annotations

"""Small per-guild durable store for active /toke meetup cards.

The store persists identifiers and choices, never member messages or media.
Discloud deployments must preserve DANK_DATA_DIR between restarts.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

SESSIONS_FILE = Path(os.getenv("DANK_TOKE_SESSIONS_FILE", str(Path(os.getenv("DANK_DATA_DIR", "data")) / "toke_sessions.json")))
_LOCK = asyncio.Lock()
_RETENTION_SECONDS = 7 * 86400


def _read() -> dict[str, dict[str, Any]]:
    if not SESSIONS_FILE.exists():
        return {}
    data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Toke session storage must be a JSON object.")
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, Mapping)}


def _write(data: dict[str, dict[str, Any]]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".toke-", suffix=".json", dir=str(SESSIONS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, SESSIONS_FILE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _id(guild_id: int, message_id: int) -> str:
    return f"{int(guild_id)}:{int(message_id)}"


def _expire(data: dict[str, dict[str, Any]], now: float) -> bool:
    changed = False
    for key, session in list(data.items()):
        until = float(session.get("expires_at", 0) or 0)
        if session.get("active", False) and until <= now:
            session["active"] = False
            session["close_reason"] = "expired"
            session["closed_at"] = until
            session["close_pending"] = True
            changed = True
        # A pre-fix restart may already have marked a card inactive without
        # ever editing the Discord message. Reconcile those too.
        if not session.get("active", False) and not session.get("close_synced", False):
            if not session.get("close_pending", False):
                session["close_pending"] = True
                session.setdefault("close_reason", "expired")
                session.setdefault("closed_at", until)
                changed = True
        # A permanently inaccessible Discord message cannot hold its local
        # session record forever. Retain unsynced closures for seven days, then
        # retire them even if permissions were never restored.
        if until + _RETENTION_SECONDS < now:
            data.pop(key, None)
            changed = True
    return changed


async def save_new(session: Mapping[str, Any]) -> dict[str, Any]:
    gid, mid = int(session["guild_id"]), int(session["message_id"])
    if gid <= 0 or mid <= 0:
        raise ValueError("Toke session identifiers are invalid.")
    value = dict(session)
    value["guild_id"], value["message_id"] = gid, mid
    value["created_at"] = time.time()
    value["cheers_ids"], value["going_ids"] = [], []
    value["voice_seen_ids"] = []  # Only RSVP identifiers observed in the session VC.
    value["host_seen_in_vc"] = False
    value["host_away_since"] = 0
    value["voice_had_humans"] = False
    value["empty_voice_since"] = 0
    value["empty_vc_end_minutes"] = min(
        120, max(0, int(value.get("empty_vc_end_minutes", 0) or 0))
    )
    value["past_cheers_ids"], value["past_going_ids"] = [], []
    value["active"] = True
    value["close_pending"] = False
    value["close_synced"] = False
    async with _LOCK:
        data = _read()
        _expire(data, time.time())
        data[_id(gid, mid)] = value
        _write(data)
        return dict(value)


async def get(guild_id: int, message_id: int) -> dict[str, Any] | None:
    async with _LOCK:
        data = _read()
        changed = _expire(data, time.time())
        if changed:
            _write(data)
        result = data.get(_id(guild_id, message_id))
        return dict(result) if result else None


async def all_active(guild_id: int | None = None) -> list[dict[str, Any]]:
    async with _LOCK:
        data = _read()
        if _expire(data, time.time()):
            _write(data)
        return [
            dict(session) for session in data.values()
            if session.get("active", False)
            and (guild_id is None or int(session.get("guild_id", 0)) == int(guild_id))
        ]


async def recent_launch_times(guild_id: int, starter_id: int) -> tuple[float, float]:
    """Persist cooldown boundaries over process restarts without storing messages."""
    async with _LOCK:
        data = _read()
        guild_last = 0.0
        member_last = 0.0
        for session in data.values():
            if int(session.get("guild_id", 0)) != int(guild_id):
                continue
            sent_at = float(session.get("created_at", 0) or 0)
            guild_last = max(guild_last, sent_at)
            if int(session.get("starter_id", 0)) == int(starter_id):
                member_last = max(member_last, sent_at)
        return guild_last, member_last


async def react(
    guild_id: int,
    message_id: int,
    actor_id: int,
    action: str,
    *,
    max_interested: int = 100,
) -> tuple[dict[str, Any] | None, str]:
    if actor_id <= 0 or action not in {"cheers", "going", "withdraw", "end"}:
        return None, "Invalid response."
    async with _LOCK:
        data = _read()
        changed = _expire(data, time.time())
        key = _id(guild_id, message_id)
        session = data.get(key)
        if not session or not session.get("active", False):
            if changed:
                _write(data)
            return None, "This sesh has ended or expired."
        cheers = list(dict.fromkeys(int(x) for x in session.get("cheers_ids", []) if int(x) > 0))
        going = list(dict.fromkeys(int(x) for x in session.get("going_ids", []) if int(x) > 0))
        if action == "end":
            session["active"] = False
            session["close_reason"] = "ended"
            session["closed_at"] = time.time()
            session["close_pending"] = True
        elif action == "withdraw":
            cheers = [x for x in cheers if x != actor_id]
            going = [x for x in going if x != actor_id]
            session["voice_seen_ids"] = [x for x in session.get("voice_seen_ids", []) if x != actor_id]
        else:
            # Cheers is a reaction; Joining is a separate RSVP. Neither click
            # silently creates the other. Capacity applies to unique people,
            # not only the Cheers list (or a person clicking both buttons).
            interested = set(cheers) | set(going)
            if actor_id not in interested and len(interested) >= max_interested:
                if changed:
                    _write(data)
                return dict(session), "This sesh has reached its participant limit."
            if action == "cheers" and actor_id not in cheers:
                cheers.append(actor_id)
            if action == "going" and actor_id not in going:
                going.append(actor_id)
        session["cheers_ids"], session["going_ids"] = cheers, going
        _write(data)
        return dict(session), ""


async def extend(
    guild_id: int, message_id: int, *,
    minutes: int, max_lifetime_minutes: int = 240,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically extend an active sesh without resetting the original cap.

    Caller must recheck host/staff authorization. A fixed lifetime measured
    from creation prevents unlimited extensions through repeated clicks.
    """
    if minutes not in (15, 30):
        return None, "Choose a supported extension."
    async with _LOCK:
        data = _read()
        changed = _expire(data, time.time())
        session = data.get(_id(guild_id, message_id))
        if not session or not session.get("active", False):
            if changed:
                _write(data)
            return None, "This sesh has ended or expired."
        limit = float(session.get("created_at", 0) or 0) + max_lifetime_minutes * 60
        expiry = float(session.get("expires_at", 0) or 0)
        if limit <= expiry:
            if changed:
                _write(data)
            return dict(session), "This sesh has reached its maximum lifetime."
        session["expires_at"] = min(limit, expiry + minutes * 60)
        _write(data)
        return dict(session), ""


async def transfer_host(
    guild_id: int, message_id: int, *,
    expected_starter_id: int, new_starter_id: int,
) -> tuple[dict[str, Any] | None, str]:
    """Atomically transfer an active session using a previous-host CAS.

    Permission and role checks belong to the interaction boundary. The store
    refuses stale confirmations so two moderators cannot silently override
    each other's transfer. No extra notifications or session reset occurs.
    """
    if expected_starter_id <= 0 or new_starter_id <= 0:
        return None, "Invalid host selection."
    async with _LOCK:
        data = _read()
        expired = _expire(data, time.time())
        session = data.get(_id(guild_id, message_id))
        if not session or not session.get("active", False):
            if expired:
                _write(data)
            return None, "This sesh has ended or expired."
        current = int(session.get("starter_id", 0))
        if current != expected_starter_id:
            if expired:
                _write(data)
            return dict(session), "The sesh host has already changed. Reopen Manage."
        if current == new_starter_id:
            if expired:
                _write(data)
            return dict(session), "That member is already hosting this sesh."
        session.setdefault("original_starter_id", current)
        session["starter_id"] = new_starter_id
        # A new host begins their own voice-presence lifecycle.
        session["host_seen_in_vc"] = False
        session["host_away_since"] = 0
        # A deliberate handoff grants the new host a full empty-room grace
        # period rather than inheriting the old host's elapsed countdown.
        session["empty_voice_since"] = 0
        session["host_transfer_count"] = int(session.get("host_transfer_count", 0) or 0) + 1
        _write(data)
        return dict(session), ""


async def reconcile_voice(
    guild_id: int,
    message_id: int,
    present_ids: set[int],
    *,
    departed_ids: set[int] | None = None,
) -> dict[str, Any] | None:
    """Reconcile live attendance atomically with RSVP updates.

    A person who entered this sesh's VC and then left is no longer marked as
    joining or cheering. A planned RSVP that has *never entered* remains an
    intention, not proof of presence. Voice events carry departures as well,
    since someone can join and leave entirely within a debounce window.
    """
    present = {int(uid) for uid in present_ids if int(uid) > 0}
    departed = {int(uid) for uid in (departed_ids or set()) if int(uid) > 0} - present
    async with _LOCK:
        data = _read()
        expired = _expire(data, time.time())
        session = data.get(_id(guild_id, message_id))
        if session is None:
            if expired:
                _write(data)
            return None
        if not session.get("active", False):
            if expired:
                _write(data)
            return dict(session)
        cheers = [int(uid) for uid in session.get("cheers_ids", [])]
        going = [int(uid) for uid in session.get("going_ids", [])]
        interested = set(cheers) | set(going)
        seen = {int(uid) for uid in session.get("voice_seen_ids", []) if int(uid) > 0}
        leaving = (seen - present) | (departed & interested)
        updated_cheers = [uid for uid in cheers if uid not in leaving]
        updated_going = [uid for uid in going if uid not in leaving]
        updated_seen = sorted((seen | (interested & present)) & present & (set(updated_cheers) | set(updated_going)))
        past_cheers = {int(uid) for uid in session.get("past_cheers_ids", [])}
        past_going = {int(uid) for uid in session.get("past_going_ids", [])}
        host = int(session.get("starter_id", 0) or 0)
        previously_seen = bool(session.get("host_seen_in_vc", False))
        is_host_present = host in present
        has_seen_host = previously_seen or is_host_present or host in departed
        old_away = float(session.get("host_away_since", 0) or 0)
        # Never label a host "away" when they have not been observed in VC
        # at all. Rapid join/leave events still count as observation.
        host_away = 0 if is_host_present or not has_seen_host else (old_away or time.time())
        had_humans = bool(session.get("voice_had_humans", False))
        had_humans = had_humans or bool(present) or bool(departed & (interested | {host}))
        old_empty = float(session.get("empty_voice_since", 0) or 0)
        empty_since = (old_empty or time.time()) if had_humans and not present else 0
        new_past_cheers = past_cheers | (leaving & set(cheers))
        new_past_going = past_going | (leaving & set(going))
        if (updated_cheers != cheers or updated_going != going
                or updated_seen != sorted(seen)
                or new_past_cheers != past_cheers or new_past_going != past_going
                or has_seen_host != previously_seen
                or host_away != old_away or had_humans != bool(session.get("voice_had_humans", False))
                or empty_since != old_empty):
            session["host_seen_in_vc"] = has_seen_host
            session["host_away_since"] = host_away
            session["voice_had_humans"] = had_humans
            session["empty_voice_since"] = empty_since
            session["cheers_ids"] = updated_cheers
            session["going_ids"] = updated_going
            session["voice_seen_ids"] = updated_seen
            # Historical participation is recorded only in the same durable
            # session and never presented as current attendance.
            session["past_cheers_ids"] = sorted(new_past_cheers)
            session["past_going_ids"] = sorted(new_past_going)
            _write(data)
        elif expired:
            _write(data)
        return dict(session)


async def idle_voice_candidates(limit: int = 25) -> list[dict[str, Any]]:
    """Due, opt-in auto-closures only. Read is bounded in returned work."""
    async with _LOCK:
        data = _read()
        now = time.time()
        changed = _expire(data, now)
        if changed:
            _write(data)
        return [
            dict(s) for s in data.values()
            if s.get("active", False)
            and int(s.get("voice_channel_id", 0) or 0) > 0
            and int(s.get("empty_vc_end_minutes", 0) or 0) >= 5
            and float(s.get("empty_voice_since", 0) or 0) > 0
            and float(s["empty_voice_since"]) + int(s["empty_vc_end_minutes"]) * 60 <= now
        ][:max(1, min(25, limit))]


async def close_idle_voice(guild_id: int, message_id: int, *, confirmed_empty: bool) -> dict[str, Any] | None:
    """Confirm a previously observed empty VC under the session store lock.

    The caller must verify a real, available Discord VC at decision time.
    Unknown channels, never-populated rooms and default-disabled timeouts
    are never enough to close a session.
    """
    if not confirmed_empty:
        return None
    async with _LOCK:
        data = _read()
        now = time.time()
        changed = _expire(data, now)
        session = data.get(_id(guild_id, message_id))
        if session is None or not session.get("active", False):
            if changed:
                _write(data)
            return None
        empty_since = float(session.get("empty_voice_since", 0) or 0)
        minutes = int(session.get("empty_vc_end_minutes", 0) or 0)
        if (minutes < 5 or not session.get("voice_had_humans", False)
                or empty_since <= 0 or empty_since + minutes * 60 > now):
            if changed:
                _write(data)
            return None
        session["active"] = False
        session["close_reason"] = "voice_idle"
        session["closed_at"] = now
        session["close_pending"] = True
        _write(data)
        return dict(session)


async def pending_closed(limit: int = 100) -> list[dict[str, Any]]:
    """Durable retry queue for Discord cards awaiting a terminal edit.

    Unlike an edge-triggered expiry snapshot, this queue survives read calls,
    outages and process restarts. A card leaves the queue only after edit.
    """
    async with _LOCK:
        data = _read()
        now = time.time()
        if _expire(data, now):
            _write(data)
        return [
            dict(session) for session in data.values()
            if not session.get("active", False)
            and session.get("close_pending", False)
            and float(session.get("close_retry_at", 0) or 0) <= now
        ][:max(1, limit)]


async def defer_closed_retry(guild_id: int, message_id: int) -> None:
    """Persist per-message exponential backoff for failed Discord card edits.

    A failed card does not starve other terminal cards in the bounded queue,
    and repeated restarts do not reset its retry budget.
    """
    async with _LOCK:
        data = _read()
        session = data.get(_id(guild_id, message_id))
        if not session or not session.get("close_pending", False):
            return
        attempts = min(32, int(session.get("close_retry_count", 0) or 0) + 1)
        session["close_retry_count"] = attempts
        session["close_retry_at"] = time.time() + min(3600, 15 * (2 ** min(attempts - 1, 8)))
        _write(data)


async def recoverable_views(guild_id: int | None = None) -> list[dict[str, Any]]:
    """Register callbacks for active cards *and* old buttons awaiting cleanup."""
    async with _LOCK:
        data = _read()
        if _expire(data, time.time()):
            _write(data)
        return [
            dict(s) for s in data.values()
            if (s.get("active", False) or s.get("close_pending", False))
            and (guild_id is None or int(s.get("guild_id", 0)) == int(guild_id))
        ]


async def mark_closed_synced(guild_id: int, message_id: int) -> None:
    """Acknowledge closure only after the Discord card was actually updated."""
    async with _LOCK:
        data = _read()
        entry = data.get(_id(guild_id, message_id))
        if entry and not entry.get("active", False) and entry.get("close_pending", False):
            entry["close_pending"] = False
            entry["close_synced"] = True
            entry.pop("close_retry_count", None)
            entry.pop("close_retry_at", None)
            _write(data)


async def end_expired() -> list[dict[str, Any]]:
    """Backwards-compatible alias; return *closed* session snapshots."""
    return await pending_closed()
