from __future__ import annotations

"""Indexed, opted-in world 4:20 schedules. Supabase service-role storage only.

No per-guild polling: a bounded RPC claims *due rows only* and advances their
local-day slot atomically before any Discord send. A failed send is never replayed.
"""

import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .globals import get_supabase

TABLE = "dank_somewhere_420_schedules"
CLAIM_RPC = "dank_420_claim_due"


class Somewhere420StorageUnavailable(RuntimeError):
    pass


def valid_timezone(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 90:
        raise ValueError("Enter an IANA timezone like America/New_York.")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError("Unknown timezone. Try America/New_York or Europe/London.") from exc
    return name


def next_daily_fire(zone_name: str, *, now: datetime | None = None, reminder_minutes: int = 0) -> datetime:
    if reminder_minutes not in (0, 5, 10, 15):
        raise ValueError("Choose 0, 5, 10 or 15 minutes before 4:20.")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must have a timezone")
    local = instant.astimezone(ZoneInfo(valid_timezone(zone_name)))
    day = local.date()
    candidate = datetime.combine(day, time(hour=16, minute=20), tzinfo=local.tzinfo)
    candidate = candidate.astimezone(timezone.utc) - timedelta(minutes=reminder_minutes)
    if candidate <= instant.astimezone(timezone.utc):
        candidate = datetime.combine(
            day + timedelta(days=1), time(16, 20), tzinfo=local.tzinfo
        ).astimezone(timezone.utc) - timedelta(minutes=reminder_minutes)
    return candidate


def quiet_at(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False  # no quiet hours
    return start <= hour < end if start < end else (hour >= start or hour < end)


def _execute_sync(label: str, callback):
    try:
        db = get_supabase()
        if db is None:
            raise Somewhere420StorageUnavailable("Service-role storage is not configured")
        return callback(db)
    except Somewhere420StorageUnavailable:
        raise
    except Exception as exc:
        raise Somewhere420StorageUnavailable(
            f"4:20 schedule storage unavailable for {label}: {type(exc).__name__}"
        ) from exc


async def _execute(label: str, callback):
    return await asyncio.to_thread(_execute_sync, label, callback)


def _row(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return dict(data[0]) if data and isinstance(data[0], Mapping) else None
    return dict(data) if isinstance(data, Mapping) else None


async def get_schedule(kind: str, item_id: int) -> dict[str, Any] | None:
    if kind not in ("guild", "member") or int(item_id) <= 0:
        raise ValueError("Invalid 4:20 subscription identifier")
    prefix = "g" if kind == "guild" else "u"
    response = await _execute(
        "read",
        lambda db: db.table(TABLE).select("*").eq("id", f"{prefix}:{item_id}").limit(1).execute(),
    )
    return _row(response)


async def save_schedule(
    kind: str, item_id: int, *, zone_name: str, enabled: bool,
    channel_id: int = 0, favorite_city: str = "",
    notify_role: bool = False, quiet_start: int = 22, quiet_end: int = 8,
    reminder_minutes: int = 0, reminder_once: bool = False,
) -> dict[str, Any]:
    zone = valid_timezone(zone_name)
    if reminder_minutes not in (0, 5, 10, 15):
        raise ValueError("Heads-up must be off, 5, 10 or 15 minutes.")
    if kind not in ("guild", "member") or int(item_id) <= 0:
        raise ValueError("Invalid 4:20 schedule owner")
    if not (0 <= int(quiet_start) <= 23 and 0 <= int(quiet_end) <= 23):
        raise ValueError("Quiet hours must be between 0 and 23.")
    if kind == "guild" and bool(enabled) and int(channel_id) <= 0:
        raise ValueError("Choose a real text channel before enabling daily announcements.")
    from .somewhere_420_clock import PLACES
    if favorite_city and favorite_city not in {name for name, _ in PLACES}:
        raise ValueError("Choose a city displayed in the world clock.")
    ident = f"{'g' if kind == 'guild' else 'u'}:{item_id}"
    data: dict[str, Any] = {
        "id": ident, "kind": kind,
        "guild_id": str(item_id) if kind == "guild" else None,
        "user_id": str(item_id) if kind == "member" else None,
        "timezone": zone, "channel_id": str(int(channel_id)) if kind == "guild" and channel_id else None,
        "favorite_city": favorite_city if kind == "member" else "",
        "notify_role": bool(notify_role) if kind == "guild" else False,
        "quiet_start": int(quiet_start), "quiet_end": int(quiet_end),
        "enabled": bool(enabled),
        "next_fire_at": next_daily_fire(zone).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = await _execute(
        "save",
        lambda db: db.table(TABLE).upsert(data, on_conflict="id").execute(),
    )
    row = _row(response)
    if row is None:
        raise Somewhere420StorageUnavailable("4:20 schedule save returned no row")
    return row


async def claim_due(*, limit: int = 16) -> list[dict[str, Any]]:
    count = max(1, min(int(limit), 25))
    response = await _execute(
        "claim",
        lambda db: db.rpc(CLAIM_RPC, {"p_limit": count}).execute(),
    )
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        raise Somewhere420StorageUnavailable("Claim RPC did not return a list")
    return [dict(item) for item in data if isinstance(item, Mapping)]
