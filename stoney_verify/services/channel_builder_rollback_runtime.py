from __future__ import annotations

"""First-class Channel Builder rollback runtime service.

Rollback is source-job based: the dashboard only sends the completed apply job ID.
The bot reads the stored rollback_plan from the operation queue result and then
queues a rollback job through the same channel mutation concurrency lane.
"""

from typing import Any

import discord
from aiohttp import web

from .channel_builder_runtime import get_guild_or_response, safe_int, safe_str


def source_job_rollback_plan(source_job_id: str, guild_id: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    try:
        from ..operation_queue import get_operation_job

        job = get_operation_job(source_job_id)
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

    plan: list[dict[str, Any]] = []
    for row in raw_plan[:150]:
        if isinstance(row, dict):
            plan.append(dict(row))
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
    await channel.delete(reason=reason)
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
    await channel.edit(reason=reason, **kwargs)
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
        return {"status": "failed", "error": "guild not found"}
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
            result = {"ok": False, "action": action or "unknown", "error": repr(exc)}
            counts["failed"] += 1
        results.append(result)

    return {
        "status": "partial" if counts["failed"] else "succeeded",
        "source_job_id": source_job_id,
        "guild_id": str(guild_id),
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

    rollback_plan, source_job, error = source_job_rollback_plan(source_job_id, guild_id)
    if error:
        return server._json_error(error, 409, source_job=source_job)

    try:
        from ..operation_queue import submit_operation

        job = await submit_operation(
            guild_id=guild_id,
            actor_id=actor_id or None,
            operation_type="channel_builder_rollback",
            risk_level="dangerous",
            source="dashboard",
            payload={"source_job_id": source_job_id, "rollback_count": len(rollback_plan)},
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
