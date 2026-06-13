from __future__ import annotations

"""First-class structured Bot API routes for Channel Builder."""

from typing import Any

from aiohttp import web

from ..operation_queue import get_operation_job, submit_operation
from ..services.channel_builder_runtime import (
    execute_channel_builder_plan,
    get_guild_or_response,
    list_channels_payload,
    normalize_channel_builder_items,
    preflight_channel_builder_plan,
    safe_int,
    safe_str,
    validate_channel_builder_items,
)
from ..services.channel_builder_rollback_runtime import submit_rollback_job


async def list_channel_builder_channels(server: Any, request: web.Request):
    data = await server._merged_request_data(request) if hasattr(server, "_merged_request_data") else dict(request.query)
    payload, err = await list_channels_payload(server=server, guild_id=data.get("guild_id"))
    if err is not None:
        return err
    return server._json_ok(**(payload or {}))


async def preflight_channel_builder_job(server: Any, request: web.Request):
    data = await server._request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return server._json_error("Invalid JSON body")

    guild_id = safe_int(data.get("guild_id"), 0)
    items = normalize_channel_builder_items(data.get("items"), options=data.get("options"))
    if guild_id <= 0:
        return server._json_error("guild_id required")
    if not items:
        return server._json_error("items required")

    validation_errors = validate_channel_builder_items(items)
    guild, err = await get_guild_or_response(server, guild_id)
    if err is not None:
        return err
    assert guild is not None

    preflight = preflight_channel_builder_plan(guild, items)
    ok = not validation_errors and bool(preflight.get("ok"))
    return server._json_ok(
        queueable=ok,
        validation_errors=validation_errors,
        preflight=preflight,
        item_count=len(items),
    )


async def submit_channel_builder_job(server: Any, request: web.Request):
    data = await server._request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return server._json_error("Invalid JSON body")

    guild_id = safe_int(data.get("guild_id"), 0)
    actor_id = safe_int(data.get("actor_id") or data.get("staff_id"), 0)
    mode = safe_str(data.get("mode") or "apply_plan").lower()[:60]
    dry_run = str(data.get("dry_run", "false")).strip().lower() in {"1", "true", "yes", "y", "on"}
    items = normalize_channel_builder_items(data.get("items"), options=data.get("options"))

    if guild_id <= 0:
        return server._json_error("guild_id required")
    if not items:
        return server._json_error("items required")

    errors = validate_channel_builder_items(items)
    if errors:
        return server._json_error("Channel Builder plan is not queueable", 409, errors=errors)

    guild, err = await get_guild_or_response(server, guild_id)
    if err is not None:
        return err
    assert guild is not None
    preflight = preflight_channel_builder_plan(guild, items)
    if not bool(preflight.get("ok")):
        return server._json_error("Channel Builder preflight failed", 409, preflight=preflight)

    try:
        job = await submit_operation(
            guild_id=guild_id,
            actor_id=actor_id or None,
            operation_type="channel_builder_apply_plan" if not dry_run else "channel_builder_dry_run_job",
            risk_level="dangerous" if not dry_run else "moderate",
            source="dashboard",
            payload={"mode": mode, "items": items, "options": data.get("options") or {}, "dry_run": dry_run, "preflight": preflight},
            concurrency_class="channel_mutation",
            concurrency_key="channel_builder",
            timeout_seconds=900.0,
            progress_total=len(items),
            factory=lambda: execute_channel_builder_plan(
                server=server,
                guild_id=guild_id,
                actor_id=actor_id,
                items=items,
                mode=mode,
                dry_run=dry_run,
            ),
        )
        return server._json_ok(queued=True, job=job, preflight=preflight)
    except Exception as exc:
        return server._json_error("Failed to queue Channel Builder job", 500, detail=repr(exc))


async def get_channel_builder_operation(server: Any, request: web.Request):
    job_id = safe_str(request.match_info.get("job_id") or request.query.get("job_id"))
    if not job_id:
        return server._json_error("job_id required")
    try:
        job = get_operation_job(job_id)
        if not job:
            return server._json_error("job not found", 404)
        return server._json_ok(job=job)
    except Exception as exc:
        return server._json_error("Failed to read operation job", 500, detail=repr(exc))


def register_channel_builder_routes(app: web.Application, server: Any) -> None:
    """Register Channel Builder routes on a structured Bot API app."""

    app.router.add_get("/channel-builder/channels", lambda request: list_channel_builder_channels(server, request))
    app.router.add_post("/channel-builder/channels", lambda request: list_channel_builder_channels(server, request))
    app.router.add_post("/channel-builder/preflight", lambda request: preflight_channel_builder_job(server, request))
    app.router.add_post("/channel-builder/jobs", lambda request: submit_channel_builder_job(server, request))
    app.router.add_post("/channel-builder/rollback", lambda request: submit_rollback_job(server, request))
    app.router.add_get("/operation/{job_id}", lambda request: get_channel_builder_operation(server, request))
    app.router.add_get("/operations/{job_id}", lambda request: get_channel_builder_operation(server, request))


__all__ = ["register_channel_builder_routes"]
