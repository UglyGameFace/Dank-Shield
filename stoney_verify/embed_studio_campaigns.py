"""Bounded, opt-in recurring Embed Studio campaigns.

A database claim advances the recurrence before Discord I/O. No retries after
ambiguous sends; no full-guild scans; the worker verifies permissions per send.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
import os
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .embed_studio_schedules import (
    StudioScheduleError, _client, _rows, safe_scheduled_project, schedules_enabled,
)

TABLE = "dank_embed_studio_campaigns"
CLAIM_RPC = "dank_embed_studio_claim_campaigns"
FLAG = "DANK_EMBED_STUDIO_RECURRING_ENABLED"
WEEKDAYS = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5,
            "sat": 6, "sun": 7}


def campaigns_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return schedules_enabled(env) and str(env.get(FLAG) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def parse_campaign(frequency: str, zone_name: str, clock_text: str,
                   count: int, *, now: datetime | None = None
                   ) -> dict[str, Any]:
    """Validate IANA civil-time recurrence and find its first valid occurrence."""
    cadence = str(frequency or "").strip().lower().split()
    if len(cadence) == 1 and cadence[0] == "daily":
        kind, weekday = "daily", None
    elif (len(cadence) == 2 and cadence[0] == "weekly"
          and cadence[1] in {
              "mon", "monday", "tue", "tuesday", "wed", "wednesday",
              "thu", "thursday", "fri", "friday", "sat", "saturday",
              "sun", "sunday",
          }):
        kind, weekday = "weekly", WEEKDAYS[cadence[1][:3]]
    else:
        raise StudioScheduleError("Use 'daily' or 'weekly Monday' through 'weekly Sunday'.")
    if (not isinstance(zone_name, str) or not 1 <= len(zone_name) <= 90
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-/]{0,89}", zone_name)):
        raise StudioScheduleError("Enter a valid IANA timezone, like America/New_York.")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise StudioScheduleError("Unknown IANA timezone.") from exc
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", str(clock_text or "")):
        raise StudioScheduleError("Use a 24-hour local time, HH:MM.")
    hour, minute = (int(s) for s in clock_text.split(":"))
    if type(count) is not int or not 1 <= count <= 365:
        raise StudioScheduleError("Repeat count must be between 1 and 365.")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise StudioScheduleError("Cannot schedule against a clock without timezone.")
    after = instant.astimezone(timezone.utc) + timedelta(minutes=2)
    anchor = after.astimezone(zone).date()
    first: datetime | None = None
    for offset in range(16):
        day = anchor + timedelta(days=offset)
        if weekday is not None and day.isoweekday() != weekday:
            continue
        # fold=1 agrees with PostgreSQL's preference for standard time on
        # autumn overlaps. The UTC round-trip skips spring-forward gaps.
        candidate = datetime.combine(day, time(hour, minute), tzinfo=zone).replace(fold=1)
        utc = candidate.astimezone(timezone.utc)
        if utc > after and utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None):
            first = utc
            break
    if first is None:
        raise StudioScheduleError("No valid recurring occurrence in the next 16 days.")
    return {
        "frequency": kind, "timezone": zone_name,
        "local_clock": clock_text + ":00", "weekday": weekday,
        "next_fire_at": first.isoformat(), "max_occurrences": count,
    }


def _enqueue_sync(guild_id: int, channel_id: int, creator_id: int,
                  project: Mapping[str, Any], recurrence: Mapping[str, Any]
                  ) -> dict[str, Any]:
    if not campaigns_enabled():
        raise StudioScheduleError("Recurring publishing is disabled.")
    if any(type(value) is not int or value <= 0
           for value in (guild_id, channel_id, creator_id)):
        raise StudioScheduleError("Invalid server, channel or creator.")
    model = safe_scheduled_project(project)
    allowed = {"frequency", "timezone", "local_clock", "weekday",
               "next_fire_at", "max_occurrences"}
    if set(recurrence) != allowed:
        raise StudioScheduleError("Invalid campaign schedule fields.")
    payload = dict(recurrence)
    payload.update(guild_id=str(guild_id), channel_id=str(channel_id),
                   scheduled_by=str(creator_id), project=model)
    rows = _rows(_client().table(TABLE).insert(payload).execute())
    if len(rows) != 1 or not rows[0].get("id"):
        raise StudioScheduleError("Campaign persistence was not confirmed.")
    return rows[0]


async def enqueue(guild_id: int, channel_id: int, creator_id: int,
                  project: Mapping[str, Any], recurrence: Mapping[str, Any]
                  ) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            _enqueue_sync, guild_id, channel_id, creator_id, project, recurrence
        ), timeout=12)
    except (StudioScheduleError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise StudioScheduleError(
            f"Campaign storage unavailable ({type(exc).__name__}). Nothing was queued."
        ) from exc


async def list_campaigns(guild_id: int) -> list[dict[str, Any]]:
    if not campaigns_enabled() or type(guild_id) is not int or guild_id <= 0:
        raise StudioScheduleError("Recurring campaigns are unavailable.")
    def read() -> list[dict[str, Any]]:
        return _rows(_client().table(TABLE).select(
            "id,channel_id,frequency,timezone,local_clock,weekday,"
            "next_fire_at,claimed_count,max_occurrences"
        ).eq("guild_id", str(guild_id)).eq("enabled", True)
            .order("next_fire_at").limit(20).execute())
    return await asyncio.wait_for(asyncio.to_thread(read), timeout=12)


async def cancel(guild_id: int, campaign_id: str) -> bool:
    if (not campaigns_enabled() or type(guild_id) is not int or guild_id <= 0
            or len(str(campaign_id)) > 80):
        raise StudioScheduleError("Invalid campaign cancellation.")
    def write() -> bool:
        return bool(_rows(_client().table(TABLE).update({
            "enabled": False, "last_status": "cancelled",
        }).eq("guild_id", str(guild_id)).eq("id", str(campaign_id))
            .eq("enabled", True).execute()))
    return await asyncio.wait_for(asyncio.to_thread(write), timeout=12)


async def claim_due(*, shard_count: int = 1,
                    owned_shards: tuple[int, ...] = (0,)) -> list[dict[str, Any]]:
    if not campaigns_enabled():
        return []
    if (type(shard_count) is not int or not 1 <= shard_count <= 4096
            or not isinstance(owned_shards, tuple) or not owned_shards
            or len(owned_shards) > shard_count
            or any(type(x) is not int or not 0 <= x < shard_count for x in owned_shards)
            or len(set(owned_shards)) != len(owned_shards)):
        raise StudioScheduleError("Unverified campaign shard ownership.")
    def read() -> list[dict[str, Any]]:
        return _rows(_client().rpc(CLAIM_RPC, {
            "p_limit": 4, "p_shard_count": shard_count,
            "p_owned_shards": list(owned_shards),
        }).execute())
    return await asyncio.wait_for(asyncio.to_thread(read), timeout=12)


async def finish(campaign_id: str, claim_id: str, *,
                 message_id: int = 0, error_code: str = "") -> bool:
    if not campaigns_enabled():
        return False
    result = {"last_status": "sent" if message_id > 0 else "failed",
              "last_message_id": str(message_id) if message_id > 0 else None}
    if message_id <= 0:
        result["last_status"] = str(error_code or "failed")[:80]
    def write() -> bool:
        return bool(_rows(_client().table(TABLE).update(result)
            .eq("id", str(campaign_id)).eq("last_claim_id", str(claim_id))
            .eq("last_status", "claimed").execute()))
    return await asyncio.wait_for(asyncio.to_thread(write), timeout=12)
