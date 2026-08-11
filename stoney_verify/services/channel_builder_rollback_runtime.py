from __future__ import annotations

"""First-class Channel Builder rollback runtime service.

Rollback is source-job based: the dashboard sends the completed apply job ID.
The bot can recover that job from persistent queue storage after a dashboard or
bot restart, validates the stored rollback plan, and queues the rollback through
the same channel-mutation lane.
"""

from typing import Any

import discord
from aiohttp import web

from .channel_builder_runtime import get_guild_or_response, safe_int, safe_str
from ..operation_queue import get_operation_job_persistent, submit_operation, with_retry


async def source_job_rollback_plan(source_job_id: str, guild_id: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    try:
        job = await get_operation_job_persistent(source_job_id)
    except Exception as exc:
        return [], None, f"Unable to read source operation: {exc!r}"

    if not job:
        return [], None, "Source operation job was not found."
    if safe_str(job.get("guild_id")) != str(guild_id):
        return [], job, "Source operation belongs to a different guild."
    if safe_str(job.get("operation_type")) != "channel_builder_apply_plan":
        return [], job, "Source operation is not a Channel Builder apply job."
    if safe_str(job.get("status")) not in {"succeeded", "partial"}:
        return [], job, "Source operation is not finished successfully enough to roll back."

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    raw_plan = result.get("rollback_plan") if isinstance(result, dict) else []
    if not isinstance(raw_plan, list) or not raw_plan:
        return [], job, "Source operation has no rollback plan."

    plan = [dict(row) for row in raw_plan[:150] if isinstance(row, dict)]
    if not plan:
        return [], job, "Rollback plan was empty after validation."
    return plan, job, ""


def category_by_id(guild: discord.Guild, category_id: Any) -> discord.CategoryChannel | None:
    cid = safe_int(category_id, 0)
    if cid <= 0:
        return None
    channel = guild.get_channel(cid)
    return channel if isinstance(channel, discord.CategoryChannel) else None


async def rollback_delete_created(guild: discord.Guild, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_id = safe_int(row.get("channel_id"), 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return {
            "ok": True,
            "action": "delete_created_channel",
            "channel_id": str(channel_id),
            "skipped": True,
            "reason": "already missing",
        }
    before = safe_str(getattr(channel, "name", ""))
    await with_retry(
        lambda: channel.delete(reason=reason),
        attempts=3,
        concurrency_key=f"channel-builder-rollback:{guild.id}",
    )
    return {"ok": True, "action": "delete_created_channel", "channel_id": str(channel_id), "deleted_name": before}


async def rollback_rename(guild: discord.Guild, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_id = safe_int(row.get("channel_id"), 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return {"ok": False, "action": "rename_channel", "channel_id": str(channel_id), "error": "channel not found"}

    before = safe_str(getattr(channel, "name", ""))
    target_name = safe_str(row.get("name"), before)[:100]
    kwargs: dict[str, Any] = {"name": target_name}
    category = category_by_id(guild, row.get("category_id"))
    if category is not None and hasattr(channel, "edit"):
        kwargs["category"] = category
    position = safe_int(row.get("position"), -1)
    if position >= 0:
        kwargs["position"] = position
    await with_retry(
        lambda: channel.edit(reason=reason, **kwargs),
        attempts=3,
        concurrency_key=f"channel-builder-rollback:{guild.id}",
    )
    return {"ok": True, "action": "rename_channel", "channel_id": str(channel_id), "before": before, "after": target_name}


async def execute_rollback_plan(
    *,
    server: Any,
    guild_id: int,
    actor_id: int,
    source_job_id: str,
    rollback_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    guild, err = await get_guild_or_response(server, guild_id)
    if err is not None:
        return {"status": "failed", "error": "guild not found", "counts": {"deleted": 0, "restored": 0, "skipped": 0, "failed": len(rollback_plan)}}
    assert guild is not None

    reason = f"Dank Shield Channel Builder rollback by {actor_id or 'dashboard'} source_job={source_job_id}"
    results: list[dict[str, Any]] = []
    counts = {"deleted": 0, "restored": 0, "skipped": 0, "failed": 0}

    for row in rollback_plan:
        action = safe_str(row.get("action"))
        try:
            if action == "delete_created_channel":
                result = await rollback_delete_created(guild, row, reason=reason)
                counts["skipped" if result.get("skipped") else "deleted"] += 1
            elif action == "rename_channel":
                result = await rollback_rename(guild, row, reason=reason)
                counts["restored" if result.get("ok") else "failed"] += 1
            else:
                result = {"ok": True, "action": action or "unknown", "skipped": True, "reason": "unsupported rollback action"}
                counts["skipped"] += 1
        except Exception as exc:
            result = {"ok": False, "action": action or "unknown", "error": type(exc).__name__, "detail": str(exc)[:180]}
            counts["failed"] += 1
        results.append(result)

    return {
        "status": "failed" if counts["failed"] and not (counts["deleted"] or counts["restored"] or counts["skipped"]) else ("partial" if counts["failed"] else "succeeded"),
        "source_job_id": source_job_id,
        "guild_id": str(guild_id),
        "attempted": len(rollback_plan),
        "succeeded": counts["deleted"] + counts["restored"],
        "skipped_count": counts["skipped"],
        "failed_count": counts["failed"],
        "counts": counts,
        "results": results,
    }


async def submit_rollback_job(server: Any, request: web.Request):
    data = await server._request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return server._json_error("Invalid JSON body")

    guild_id = safe_int(data.get("guild_id"), 0)
    actor_id = safe_int(data.get("actor_id") or data.get("staff_id"), 0)
    source_job_id = safe_str(data.get("source_job_id") or data.get("job_id"))
    if guild_id <= 0:
        return server._json_error("guild_id required")
    if not source_job_id:
        return server._json_error("source_job_id required")

    rollback_plan, source_job, error = await source_job_rollback_plan(source_job_id, guild_id)
    if error:
        return server._json_error(error, 409, source_job=source_job)

    try:
        job = await submit_operation(
            guild_id=guild_id,
            actor_id=actor_id or None,
            operation_type="channel_builder_rollback",
            risk_level="dangerous",
            source="dashboard",
            payload={"source_job_id": source_job_id, "rollback_count": len(rollback_plan)},
            idempotency_key=f"channel-builder-rollback:{guild_id}:{source_job_id}",
            concurrency_class="channel_mutation",
            concurrency_key="channel_builder",
            timeout_seconds=900.0,
            progress_total=len(rollback_plan),
            factory=lambda: execute_rollback_plan(
                server=server,
                guild_id=guild_id,
                actor_id=actor_id,
                source_job_id=source_job_id,
                rollback_plan=rollback_plan,
            ),
        )
        return server._json_ok(queued=True, job=job, source_job_id=source_job_id, rollback_count=len(rollback_plan))
    except Exception as exc:
        return server._json_error("Failed to queue Channel Builder rollback", 500, detail=repr(exc))


__all__ = [
    "execute_rollback_plan",
    "source_job_rollback_plan",
    "submit_rollback_job",
]
