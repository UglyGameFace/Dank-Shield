"""Private, opt-in one-shot Embed Studio schedule store.

Indexed DB due-claim RPC, never scans all guilds and never relies on process RAM
for idempotency. A claimed job is not replayed after a crash.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Mapping

from .embed_studio_core import StudioValidationError, validate_project
from .embed_studio_storage_v2 import verify_project, storage_v2_enabled

TABLE = "dank_embed_studio_scheduled_posts"
CLAIM_RPC = "dank_embed_studio_claim_due"
FLAG = "DANK_EMBED_STUDIO_SCHEDULING_ENABLED"


class StudioScheduleError(StudioValidationError):
    pass


def schedules_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (
        str(env.get(FLAG) or "").strip().lower() in {"1","true","yes","on"}
        and storage_v2_enabled(env)
    )


def parse_schedule_time(value: str, *, now: datetime | None = None) -> datetime:
    try:
        when = datetime.fromisoformat(str(value or "").strip().replace("Z","+00:00"))
        if when.tzinfo is None:
            raise ValueError("no timezone")
        utc = when.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise StudioScheduleError(
            "Enter date/time with an offset, like 2026-11-01T18:00-05:00."
        ) from exc
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise StudioScheduleError("Invalid scheduling clock.")
    if not timedelta(minutes=2) <= utc-instant.astimezone(timezone.utc) <= timedelta(days=90):
        raise StudioScheduleError("Choose a time 2 minutes to 90 days from now.")
    return utc


def safe_scheduled_project(project: Mapping[str, Any]) -> dict[str, Any]:
    result = validate_project(project)
    if result["role_actions"]:
        raise StudioScheduleError(
            "Role controls cannot be scheduled unattended. Publish those with a moderator."
        )
    return result


def _client() -> Any:
    verify_project()
    from .globals import get_supabase
    db = get_supabase()
    if db is None:
        raise StudioScheduleError("Studio schedule storage is offline.")
    return db


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or any(not isinstance(i, Mapping) for i in data):
        raise StudioScheduleError("Schedule database returned an invalid response.")
    return [dict(r) for r in data]


def _enqueue_sync(guild_id: int, channel_id: int, creator_id: int,
                  project: Mapping[str, Any], when: datetime) -> dict[str, Any]:
    if not schedules_enabled():
        raise StudioScheduleError("Scheduled publishing is disabled on this deployment.")
    if min(int(guild_id), int(channel_id), int(creator_id)) <= 0:
        raise StudioScheduleError("Choose a real server and channel.")
    model = safe_scheduled_project(project)
    response = _client().table(TABLE).insert({
        "guild_id": str(int(guild_id)), "channel_id": str(int(channel_id)),
        "scheduled_by": str(int(creator_id)), "project": model,
        "execute_at": when.astimezone(timezone.utc).isoformat(),
        "status": "pending",
    }).execute()
    rows = _rows(response)
    if len(rows) != 1 or rows[0].get("status") != "pending":
        raise StudioScheduleError("Schedule was not confirmed by the database.")
    return rows[0]


async def enqueue(guild_id: int, channel_id: int, creator_id: int,
                  project: Mapping[str, Any], when: datetime) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            _enqueue_sync, guild_id, channel_id, creator_id, project, when
        ), timeout=12)
    except (StudioValidationError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise StudioScheduleError(
            f"Could not queue the post ({type(exc).__name__}). No message was sent."
        ) from exc


async def list_pending(guild_id: int) -> list[dict[str, Any]]:
    if int(guild_id) <= 0:
        raise StudioScheduleError("Invalid server.")
    if not schedules_enabled():
        raise StudioScheduleError("Scheduled publishing is disabled.")
    def run():
        return _rows(_client().table(TABLE)
                     .select("id,channel_id,execute_at,scheduled_by,status")
                     .eq("guild_id",str(int(guild_id))).eq("status","pending")
                     .order("execute_at").limit(25).execute())
    return await asyncio.wait_for(asyncio.to_thread(run), timeout=12)


async def cancel(guild_id: int, job_id: str) -> bool:
    if not schedules_enabled() or int(guild_id) <= 0 or len(str(job_id)) > 80:
        raise StudioScheduleError("Invalid schedule operation.")
    def run():
        return bool(_rows(_client().table(TABLE).update({"status":"cancelled"})
            .eq("guild_id",str(int(guild_id))).eq("id",str(job_id))
            .eq("status","pending").execute()))
    return await asyncio.wait_for(asyncio.to_thread(run), timeout=12)


async def claim_due() -> list[dict[str, Any]]:
    if not schedules_enabled():
        return []
    def run():
        return _rows(_client().rpc(CLAIM_RPC, {"p_limit": 8}).execute())
    return await asyncio.wait_for(asyncio.to_thread(run), timeout=12)


async def finish(job_id: str, *, message_id: int = 0, error_code: str = "") -> bool:
    if not schedules_enabled():
        return False
    status = "sent" if int(message_id) > 0 else "failed"
    row = {"status":status,"sent_message_id":str(int(message_id)) if message_id else None,
           "result_code":str(error_code or "")[:80] or None}
    def run():
        return bool(_rows(_client().table(TABLE).update(row)
             .eq("id",str(job_id)).eq("status","claimed").execute()))
    return await asyncio.wait_for(asyncio.to_thread(run), timeout=12)
