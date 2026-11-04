from __future__ import annotations

"""Private opt-in birthday storage and indexed claims via the existing service-role client."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .globals import get_supabase

MEMBERS = "dank_birthday_members"
GUILDS = "dank_birthday_guild_settings"
LEASES = "dank_birthday_role_leases"


class BirthdayStorageUnavailable(RuntimeError):
    pass


def valid_date(month: int, day: int) -> tuple[int, int]:
    """Year 2000 permits February 29 without ever storing a birth year."""
    try:
        date(2000, int(month), int(day))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Enter a valid month and day (for example, 06-20).") from exc
    return int(month), int(day)


def valid_zone(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 90:
        raise ValueError("Choose an IANA timezone, for example America/New_York.")
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError) as exc:
        raise ValueError("Unknown timezone. Use a name like America/New_York.") from exc
    return name


def next_daily_fire(zone_name: str, *, hour: int = 9, now: datetime | None = None) -> datetime:
    zone = ZoneInfo(valid_zone(zone_name))
    if not 0 <= int(hour) <= 23:
        raise ValueError("Birthday announcement hour must be between 0 and 23.")
    instant = now or datetime.now(timezone.utc)
    local = instant.astimezone(zone)
    candidate = datetime.combine(local.date(), time(hour=int(hour)), tzinfo=zone).astimezone(timezone.utc)
    if candidate <= instant.astimezone(timezone.utc):
        candidate = datetime.combine(local.date() + timedelta(days=1), time(hour=int(hour)), tzinfo=zone).astimezone(timezone.utc)
    return candidate


def celebrated_on(month: int, day: int, local_day: date, leap_policy: str = "feb28") -> bool:
    month, day = valid_date(month, day)
    if (month, day) == (local_day.month, local_day.day):
        return True
    return (
        (month, day) == (2, 29)
        and local_day.month in (2, 3)
        and local_day.day == (28 if leap_policy == "feb28" else 1)
        and not (local_day.year % 4 == 0 and (local_day.year % 100 != 0 or local_day.year % 400 == 0))
    )


def birthday_role_expires(zone_name: str, local_day: date) -> datetime:
    return datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=ZoneInfo(valid_zone(zone_name))).astimezone(timezone.utc)


def _execute_sync(label: str, callback):
    try:
        client = get_supabase()
        if client is None:
            raise BirthdayStorageUnavailable("Birthday storage has not been configured.")
        return callback(client)
    except BirthdayStorageUnavailable:
        raise
    except Exception as exc:
        raise BirthdayStorageUnavailable(f"Birthday {label} storage error: {type(exc).__name__}") from exc


async def _execute(label: str, callback):
    return await asyncio.to_thread(_execute_sync, label, callback)


def _one(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        return dict(data[0])
    return None


def _many(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        raise BirthdayStorageUnavailable("Unexpected birthday storage response")
    return [dict(row) for row in data if isinstance(row, Mapping)]


def _ident(value: int) -> str:
    if int(value) <= 0:
        raise ValueError("Invalid Discord identifier")
    return str(int(value))


async def get_member(guild_id: int, user_id: int) -> dict[str, Any] | None:
    response = await _execute(
        "read member",
        lambda db: db.table(MEMBERS).select("*").eq("guild_id", _ident(guild_id))
        .eq("user_id", _ident(user_id)).limit(1).execute(),
    )
    return _one(response)


async def save_member(
    guild_id: int, user_id: int, *, month: int, day: int,
    public_wishes: bool = False, birthday_role: bool = False, dm_wishes: bool = False,
) -> dict[str, Any]:
    month, day = valid_date(month, day)
    payload = {
        "guild_id": _ident(guild_id), "user_id": _ident(user_id),
        "birth_month": month, "birth_day": day,
        "public_wishes": bool(public_wishes), "birthday_role": bool(birthday_role),
        "dm_wishes": bool(dm_wishes), "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = await _execute(
        "save member",
        lambda db: db.table(MEMBERS).upsert(payload, on_conflict="guild_id,user_id").execute(),
    )
    row = _one(response)
    if not row:
        raise BirthdayStorageUnavailable("Birthday save returned no row")
    return row


async def delete_member(guild_id: int, user_id: int) -> None:
    # Never delete an active role lease: the cleanup worker must still revoke it.
    await _execute(
        "delete member",
        lambda db: db.table(MEMBERS).delete().eq("guild_id", _ident(guild_id))
        .eq("user_id", _ident(user_id)).execute(),
    )


async def get_guild(guild_id: int) -> dict[str, Any] | None:
    response = await _execute(
        "read server",
        lambda db: db.table(GUILDS).select("*").eq("guild_id", _ident(guild_id)).limit(1).execute(),
    )
    return _one(response)


async def save_guild(
    guild_id: int, *, enabled: bool, zone_name: str, channel_id: int = 0,
    role_id: int = 0, leap_policy: str = "feb28", hour: int = 9,
) -> dict[str, Any]:
    zone_name = valid_zone(zone_name)
    if leap_policy not in ("feb28", "mar1"):
        raise ValueError("Leap-day celebration must be February 28 or March 1.")
    if enabled and not channel_id:
        raise ValueError("Choose an announcement channel before enabling birthdays.")
    payload = {
        "guild_id": _ident(guild_id), "enabled": bool(enabled),
        "timezone": zone_name, "channel_id": _ident(channel_id) if channel_id else None,
        "role_id": _ident(role_id) if role_id else None,
        "leap_day_policy": leap_policy, "announce_hour": int(hour),
        "next_fire_at": next_daily_fire(zone_name, hour=hour).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = await _execute(
        "save server",
        lambda db: db.table(GUILDS).upsert(payload, on_conflict="guild_id").execute(),
    )
    row = _one(response)
    if not row:
        raise BirthdayStorageUnavailable("Birthday server save returned no row")
    return row


async def claim_due(*, limit: int = 24) -> list[dict[str, Any]]:
    response = await _execute(
        "claim schedule",
        lambda db: db.rpc("dank_birthdays_claim_due", {"p_limit": max(1, min(int(limit), 50))}).execute(),
    )
    return _many(response)


async def members_for_day(guild_id: int, local_day: date, leap_policy: str) -> list[dict[str, Any]]:
    # The indexed exact-date query is the normal path. A separate query handles
    # February 29 in non-leap years; do not scan every guild member.
    exact = await _execute(
        "read matching birthdays",
        lambda db: db.table(MEMBERS).select("*").eq("guild_id", _ident(guild_id))
        .eq("birth_month", local_day.month).eq("birth_day", local_day.day).execute(),
    )
    rows = _many(exact)
    if celebrated_on(2, 29, local_day, leap_policy) and (local_day.month, local_day.day) != (2, 29):
        extra = await _execute(
            "read leap birthdays",
            lambda db: db.table(MEMBERS).select("*").eq("guild_id", _ident(guild_id))
            .eq("birth_month", 2).eq("birth_day", 29).execute(),
        )
        rows.extend(_many(extra))
    return rows


async def upcoming(guild_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
    response = await _execute(
        "read public upcoming birthdays",
        lambda db: db.table(MEMBERS).select("user_id,birth_month,birth_day")
        .eq("guild_id", _ident(guild_id)).eq("public_wishes", True)
        .order("birth_month").order("birth_day").limit(1000).execute(),
    )
    return _many(response)[:1000]


async def claim_delivery(guild_id: int, user_id: int, local_day: date, kind: str) -> bool:
    response = await _execute(
        "reserve delivery",
        lambda db: db.rpc("dank_birthdays_claim_delivery", {
            "p_guild_id": _ident(guild_id), "p_user_id": _ident(user_id),
            "p_local_date": local_day.isoformat(), "p_kind": str(kind),
        }).execute(),
    )
    value = getattr(response, "data", None)
    if not isinstance(value, bool):
        raise BirthdayStorageUnavailable("Birthday delivery reservation returned no boolean")
    return value


async def save_role_lease(guild_id: int, user_id: int, role_id: int, expires_at: datetime) -> None:
    payload = {
        "guild_id": _ident(guild_id), "user_id": _ident(user_id),
        "role_id": _ident(role_id), "expires_at": expires_at.isoformat(),
        "next_try_at": expires_at.isoformat(),
    }
    await _execute(
        "save role lease",
        lambda db: db.table(LEASES).upsert(payload, on_conflict="guild_id,user_id").execute(),
    )


async def claim_expired_roles(*, limit: int = 24) -> list[dict[str, Any]]:
    response = await _execute(
        "claim expired roles",
        lambda db: db.rpc("dank_birthdays_claim_expired_roles", {"p_limit": max(1, min(int(limit), 50))}).execute(),
    )
    return _many(response)


async def clear_role_lease(guild_id: int, user_id: int) -> None:
    await _execute(
        "clear role lease",
        lambda db: db.table(LEASES).delete().eq("guild_id", _ident(guild_id))
        .eq("user_id", _ident(user_id)).execute(),
    )
