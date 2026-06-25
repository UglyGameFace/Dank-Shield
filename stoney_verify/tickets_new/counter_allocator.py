from __future__ import annotations

"""Persistent per-guild ticket number allocation.

Ticket numbers are production identifiers. They must survive deleted open,
closed, archived, and transcript channels. Discord channel state can seed a
migration floor, but it is never the recurring source of truth.
"""

import asyncio
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import discord

from ..globals import get_supabase, now_utc, reset_supabase

COUNTER_TABLE = "ticket_counters"
TICKETS_TABLE = "tickets"

_TICKET_NAME_RE = re.compile(r"^(?:ticket|closed)-(\d{1,12})$", re.I)
_TOPIC_NUMBER_RE = re.compile(r"(?:^|[;\s])ticket_number=(\d{1,12})(?:$|[;\s])", re.I)

_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}
_RPC_WARNED: set[str] = set()
_NO_DB_WARNED: set[int] = set()


def _log(message: str) -> None:
    try:
        print(f"🎫 ticket_counter {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_counter {message}")
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


def _short(value: Any, limit: int = 220) -> str:
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _utc_iso(dt: Optional[datetime] = None) -> str:
    if dt is None:
        try:
            dt = now_utc()
        except Exception:
            dt = datetime.now(timezone.utc)
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _rows(resp: Any) -> List[Dict[str, Any]]:
    try:
        data = getattr(resp, "data", None)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            return [dict(data)]
    except Exception:
        pass
    return []


def _response_number(resp: Any) -> int:
    try:
        data = getattr(resp, "data", None)
        if isinstance(data, int):
            return int(data)
        if isinstance(data, str):
            return _safe_int(data, 0)
        if isinstance(data, dict):
            for key in ("reserve_ticket_number", "ticket_number", "last_ticket_number", "number"):
                number = _safe_int(data.get(key), 0)
                if number > 0:
                    return number
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, int):
                return int(first)
            if isinstance(first, str):
                return _safe_int(first, 0)
            if isinstance(first, dict):
                for key in ("reserve_ticket_number", "ticket_number", "last_ticket_number", "number"):
                    number = _safe_int(first.get(key), 0)
                    if number > 0:
                        return number
    except Exception:
        pass
    return 0


def _sb() -> Any:
    try:
        return get_supabase()
    except Exception:
        return None


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _execute_db_op(op_name: str, executor, max_attempts: int = 5):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as exc:
            last_error = exc
            if _is_retryable_db_error(exc) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                _warn(f"{op_name}: transient DB error attempt={attempt}/{max_attempts}: {type(exc).__name__}: {_short(exc)}")
                time.sleep(min(0.35 * (2 ** max(0, attempt - 1)), 3.0) + random.uniform(0.05, 0.25))
                continue
            raise
    raise last_error


async def _run_db_op(op_name: str, executor, max_attempts: int = 5):
    return await asyncio.to_thread(_execute_db_op, op_name, executor, max_attempts)


def _number_from_name(name: Any) -> int:
    try:
        match = _TICKET_NAME_RE.match(str(name or "").strip())
        return _safe_int(match.group(1), 0) if match else 0
    except Exception:
        return 0


def _number_from_topic(topic: Any) -> int:
    try:
        match = _TOPIC_NUMBER_RE.search(str(topic or ""))
        return _safe_int(match.group(1), 0) if match else 0
    except Exception:
        return 0


def _channel_number(channel: Any) -> int:
    if not isinstance(channel, discord.TextChannel):
        return 0
    return max(
        _number_from_name(getattr(channel, "name", "")),
        _number_from_topic(getattr(channel, "topic", "")),
    )


def _iter_text_channels(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
) -> Iterable[discord.TextChannel]:
    seen: set[int] = set()

    def add(channel: Any) -> Optional[discord.TextChannel]:
        try:
            if not isinstance(channel, discord.TextChannel):
                return None
            channel_id = int(channel.id)
            if channel_id in seen:
                return None
            seen.add(channel_id)
            return channel
        except Exception:
            return None

    try:
        if isinstance(parent, discord.CategoryChannel):
            for channel in list(getattr(parent, "text_channels", []) or []):
                selected = add(channel)
                if selected is not None:
                    yield selected
    except Exception:
        pass

    try:
        for channel in list(getattr(guild, "text_channels", []) or []):
            selected = add(channel)
            if selected is not None:
                yield selected
    except Exception:
        pass


def _highest_current_channel_number(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
) -> int:
    highest = 0
    for channel in _iter_text_channels(guild, parent):
        highest = max(highest, _channel_number(channel))
    return highest


def _db_max_ticket_number_sync(sb: Any, guild_id: int) -> int:
    try:
        resp = (
            sb.table(TICKETS_TABLE)
            .select("ticket_number")
            .eq("guild_id", str(guild_id))
            .order("ticket_number", desc=True)
            .limit(1)
            .execute()
        )
        rows = _rows(resp)
        if rows:
            return _safe_int(rows[0].get("ticket_number"), 0)
    except Exception as exc:
        _warn(f"ticket history read failed guild={guild_id}: {type(exc).__name__}: {_short(exc)}")
    return 0


def _counter_row_sync(sb: Any, guild_id: int) -> Optional[Dict[str, Any]]:
    try:
        resp = (
            sb.table(COUNTER_TABLE)
            .select("guild_id,last_ticket_number,updated_at")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute()
        )
        rows = _rows(resp)
        return rows[0] if rows else None
    except Exception as exc:
        _warn(f"counter read failed guild={guild_id}: {type(exc).__name__}: {_short(exc)}")
        return None


def _upsert_counter_floor_sync(sb: Any, guild_id: int, floor: int) -> None:
    payload = {
        "guild_id": str(guild_id),
        "last_ticket_number": int(max(0, floor)),
        "updated_at": _utc_iso(),
    }
    try:
        sb.table(COUNTER_TABLE).upsert(payload, on_conflict="guild_id").execute()
    except TypeError:
        sb.table(COUNTER_TABLE).upsert(payload).execute()


def _ensure_counter_seed_sync(sb: Any, guild_id: int, *, channel_floor: int = 0) -> int:
    """Seed/reseed upward only. Never lower the counter."""

    db_floor = _db_max_ticket_number_sync(sb, guild_id)
    desired_floor = max(0, int(db_floor), int(channel_floor))
    row = _counter_row_sync(sb, guild_id)

    if row is None:
        try:
            _upsert_counter_floor_sync(sb, guild_id, desired_floor)
        except Exception as exc:
            _warn(f"counter seed upsert failed guild={guild_id}: {type(exc).__name__}: {_short(exc)}")
        return desired_floor

    current = _safe_int(row.get("last_ticket_number"), 0)
    if desired_floor <= current:
        return current

    try:
        resp = (
            sb.table(COUNTER_TABLE)
            .update({"last_ticket_number": int(desired_floor), "updated_at": _utc_iso()})
            .eq("guild_id", str(guild_id))
            .eq("last_ticket_number", int(current))
            .select("last_ticket_number")
            .execute()
        )
        rows = _rows(resp)
        if rows:
            return _safe_int(rows[0].get("last_ticket_number"), desired_floor)
    except Exception as exc:
        _warn(f"counter seed compare/update failed guild={guild_id}: {type(exc).__name__}: {_short(exc)}")

    refreshed = _counter_row_sync(sb, guild_id)
    return max(desired_floor, _safe_int((refreshed or {}).get("last_ticket_number"), 0))


def _reserve_with_rpc_sync(sb: Any, guild_id: int) -> int:
    resp = sb.rpc("reserve_ticket_number", {"p_guild_id": str(guild_id)}).execute()
    number = _response_number(resp)
    if number <= 0:
        raise RuntimeError(f"reserve_ticket_number returned invalid value: {getattr(resp, 'data', None)!r}")
    return number


def _reserve_with_compare_and_swap_sync(
    sb: Any,
    guild_id: int,
    *,
    channel_floor: int,
    max_retries: int = 20,
) -> int:
    floor = _ensure_counter_seed_sync(sb, guild_id, channel_floor=channel_floor)

    for attempt in range(1, max_retries + 1):
        row = _counter_row_sync(sb, guild_id)
        if row is None:
            floor = _ensure_counter_seed_sync(sb, guild_id, channel_floor=channel_floor)
            time.sleep(min(0.05 * attempt, 0.5))
            continue

        current = max(_safe_int(row.get("last_ticket_number"), 0), int(floor), int(channel_floor))
        new_value = current + 1

        try:
            resp = (
                sb.table(COUNTER_TABLE)
                .update({"last_ticket_number": int(new_value), "updated_at": _utc_iso()})
                .eq("guild_id", str(guild_id))
                .eq("last_ticket_number", int(current))
                .select("last_ticket_number")
                .execute()
            )
            rows = _rows(resp)
            if rows and _safe_int(rows[0].get("last_ticket_number"), 0) == new_value:
                return int(new_value)
        except Exception as exc:
            _warn(f"counter compare/update failed guild={guild_id} attempt={attempt}: {type(exc).__name__}: {_short(exc)}")

        time.sleep(min((0.04 * attempt) + random.uniform(0.01, 0.08), 0.75))

    raise RuntimeError(f"Could not reserve a unique ticket number for guild={guild_id}")


def _reserve_number_sync(
    sb: Any,
    guild_id: int,
    *,
    channel_floor: int = 0,
    max_retries: int = 20,
) -> int:
    # Seed first so current legacy channels can only raise the DB counter.
    # After this, the DB counter is the recurring source of truth.
    floor = _ensure_counter_seed_sync(sb, guild_id, channel_floor=channel_floor)

    try:
        return _reserve_with_rpc_sync(sb, guild_id)
    except Exception as exc:
        marker = f"{type(exc).__name__}:{_short(exc, 140)}"
        if marker not in _RPC_WARNED:
            _RPC_WARNED.add(marker)
            _warn(
                "reserve_ticket_number RPC unavailable; using compare-and-swap fallback. "
                f"error={type(exc).__name__}: {_short(exc)}"
            )

    return _reserve_with_compare_and_swap_sync(
        sb,
        guild_id,
        channel_floor=floor,
        max_retries=max_retries,
    )


def _lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _NUMBER_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _NUMBER_LOCKS[gid] = lock
    return lock


async def reserve_next_ticket_number(
    guild: discord.Guild,
    *,
    parent: Optional[discord.CategoryChannel] = None,
    source: str = "ticket",
    max_retries: int = 20,
) -> int:
    """Reserve the next never-reused ticket number for one guild."""

    guild_id = int(guild.id)
    async with _lock(guild_id):
        sb = _sb()
        if sb is None:
            if guild_id not in _NO_DB_WARNED:
                _NO_DB_WARNED.add(guild_id)
                _warn(
                    f"Supabase unavailable for guild={guild_id}; refusing to allocate from Discord channels "
                    "because deleted archives can make that reuse 0001."
                )
            raise RuntimeError("Ticket numbering database unavailable; refusing to create a duplicate ticket number.")

        channel_floor = _highest_current_channel_number(guild, parent)
        number = await _run_db_op(
            "reserve ticket number",
            lambda: _reserve_number_sync(
                sb,
                guild_id,
                channel_floor=channel_floor,
                max_retries=max_retries,
            ),
            max_attempts=3,
        )

        _log(
            f"reserved guild={guild_id} number={number} source={source} "
            f"channel_floor={channel_floor}"
        )
        return int(number)

