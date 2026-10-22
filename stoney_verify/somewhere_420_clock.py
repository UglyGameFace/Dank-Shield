from __future__ import annotations

"""Pure, timezone-aware 4:20 worldwide clock. No Discord/network/database I/O."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# Representative places, with real IANA zones (not manually maintained UTC offsets).
# Half/quarter-hour zones are included. City names are presentation only.
PLACES: tuple[tuple[str, str], ...] = (
    ("Honolulu", "Pacific/Honolulu"),
    ("Anchorage", "America/Anchorage"),
    ("Los Angeles", "America/Los_Angeles"),
    ("Denver", "America/Denver"),
    ("Chicago", "America/Chicago"),
    ("New York", "America/New_York"),
    ("Halifax", "America/Halifax"),
    ("St. John's", "America/St_Johns"),
    ("São Paulo", "America/Sao_Paulo"),
    ("London", "Europe/London"),
    ("Paris", "Europe/Paris"),
    ("Athens", "Europe/Athens"),
    ("Johannesburg", "Africa/Johannesburg"),
    ("Nairobi", "Africa/Nairobi"),
    ("Dubai", "Asia/Dubai"),
    ("Kabul", "Asia/Kabul"),
    ("Karachi", "Asia/Karachi"),
    ("New Delhi", "Asia/Kolkata"),
    ("Kathmandu", "Asia/Kathmandu"),
    ("Dhaka", "Asia/Dhaka"),
    ("Bangkok", "Asia/Bangkok"),
    ("Singapore", "Asia/Singapore"),
    ("Tokyo", "Asia/Tokyo"),
    ("Adelaide", "Australia/Adelaide"),
    ("Sydney", "Australia/Sydney"),
    ("Auckland", "Pacific/Auckland"),
    ("Chatham Islands", "Pacific/Chatham"),
)


@dataclass(frozen=True)
class ClockPlace:
    city: str
    zone: str
    local_time: datetime
    live: bool
    next_utc: datetime


def world_420(now: datetime, *, hour: int = 16) -> tuple[ClockPlace, ...]:
    """List world cities ordered by their next local 4:20 AM/PM.

    DST and date changes come from zoneinfo. `live` lasts only the actual
    clock minute, with no claim that 4:20 is literally happening at other times.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("world_420 requires a timezone-aware instant")
    if hour not in (4, 16):
        raise ValueError("4:20 display hour must be 4 or 16")

    instant = now.astimezone(timezone.utc)
    places: list[ClockPlace] = []
    for city, name in PLACES:
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            continue
        local = instant.astimezone(zone)
        live = local.hour == hour and local.minute == 20
        scheduled = datetime.combine(local.date(), time(hour, 20), tzinfo=zone)
        if scheduled.astimezone(timezone.utc) <= instant:
            scheduled = datetime.combine(
                local.date() + timedelta(days=1), time(hour, 20), tzinfo=zone
            )
        places.append(
            ClockPlace(city, name, local, live, scheduled.astimezone(timezone.utc))
        )
    return tuple(sorted(places, key=lambda place: (place.next_utc, place.city)))


def next_wave(places: tuple[ClockPlace, ...], *, limit: int = 5) -> tuple[tuple[datetime, tuple[str, ...]], ...]:
    """Group simultaneous next events, avoiding five identical countdowns."""
    waves: dict[datetime, list[str]] = {}
    for place in places:
        waves.setdefault(place.next_utc, []).append(place.city)
    return tuple(
        (when, tuple(names))
        for when, names in sorted(waves.items())[:max(0, min(limit, 10))]
    )


def live_cities(places: tuple[ClockPlace, ...]) -> tuple[str, ...]:
    return tuple(place.city for place in places if place.live)
