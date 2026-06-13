from __future__ import annotations

"""Add queued rollback support for Channel Builder jobs.

Rollback is intentionally source-job based. The dashboard supplies the completed
Channel Builder job ID, and the bot reads the stored rollback_plan from the
operation queue instead of trusting a client-provided rollback payload.
"""

import builtins
import sys
from typing import Any

import discord
from aiohttp import web

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_rollback_api_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_rollback_api_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _json_ok(server: Any, **extra: Any):
    return server._json_ok(**extra)


def _json_error(server: Any, message: str, status: int = 400, **extra: Any):
    return server._json_error(message, status, **extra)


async def _get_guild(server: Any, guild_id: Any) -> tuple[discord.Guild | None, web.Response | None]:
    if hasattr(server, "_get_guild_or_error"):
        return await server._get_guild_or_error(guild_id)
    gid = _safe_int(guild_id, 0)
    guild = server.bot.get_guild(gid) if gid else None
    if guild is None:
        return None, _json_error(server, "Guild not found", 404)
    return guild, None


def _source_job_rollback_plan(source_job_id: str, guild_id: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    try:
        from ..operation_queue import get_operation_job

        job = get_operation_job(source_job_id)
    except Exception as e:
        return [], None, f"Unable to read source operation: {e!r}"

    if not job:
        return [], None, "Source operation job was not found."
    if _safe_str(job.get("guild_id")) != str(guild_id):
        return [], job, "Source operation belongs to a different guild."
    if _safe_str(job.get("operation_type")) != "channel_builder_apply_plan":
        return [], job, "Source operation is not a Channel Builder apply job."
    if _safe_str(job.get("status")) not in {"succeeded", "partial"}:
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


def _category_by_id(guild: discord.Guild, category_id: Any) -> discord.CategoryChannel | None:
    cid = _safe_int(category_id, 0)
    if cid <= 0:
        return None
    channel = guild.get_channel(cid)
    return channel if isinstance(channel, discord.CategoryChannel) else None


async def _rollback_delete_created(guild: discord.Guild, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_id = _safe_int(row.get("channel_id"), 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return {"ok": True, "action": "delete_created_channel", "channel_id": str(channel_id), "skipped": True, "reason": "already missing"}
    before = _safe_str(getattr(channel, "name", ""))
    await channel.delete(reason=reason)
    return {"ok": True, "action": "delete_created_channel", "channel_id": str(channel_id), "deleted_name": before}


async def _rollback_rename(guild: discord.Guild, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_id = _safe_int(row.get("channel_id"), 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return {"ok": False, "action": "rename_channel", "channel_id": str(channel_id), "error": "channel not found"}

    before = _safe_str(getattr(channel, "name", ""))
    kwargs: dict[str, Any] = {"name": _safe_str(row.get("name"), before)[:100]}
    category = _category_by_id(guild, row.get("category_id"))
    if category is not None and hasattr(channel, "edit"):
        kwargs["category"] = category
    position = _safe_int(row.get("position"), -1)
    if position >= 0:
        kwargs["position"] = position
    await channel.edit(reason=reason, **kwargs)
    return {"ok": True, "action": "rename_channel", "channel_id": str(channel_id), "before": before, "after": kwargs.get("name")}


async def _execute_rollback_plan(*, server: Any, guild_id: int, actor_id: int, source_job_id: str, rollback_plan: list[dict[str, Any]]) -> dict[str, Any]:
    guild, err = await _get_guild(server, guild_id)
    if err is not None:
        return {"status": "failed", "error": "guild not found"}
    assert guild is not None

    reason = f"Dank Shield Channel Builder rollback by {actor_id or 'dashboard'} source_job={source_job_id}"
    results: list[dict[str, Any]] = []
    counts = {"deleted": 0, "restored": 0, "skipped": 0, "failed": 0}

    for row in rollback_plan:
        action = _safe_str(row.get("action"))
        try:
            if action == "delete_created_channel":
                result = await _rollback_delete_created(guild, row, reason=reason)
                counts["skipped" if result.get("skipped") else "deleted"] += 1
            elif action == "rename_channel":
                result = await _rollback_rename(guild, row, reason=reason)
                counts["restored" if result.get("ok") else "failed"] += 1
            else:
                result = {"ok": True, "action": action or "unknown", "skipped": True, "reason": "unsupported rollback action"}
                counts["skipped"] += 1
        except Exception as e:
            result = {"ok": False, "action": action or "unknown", "error": repr(e)}
            counts["failed"] += 1
        results.append(result)

    return {
        "status": "partial" if counts["failed"] else "succeeded",
        "source_job_id": source_job_id,
        "guild_id": str(guild_id),
        "counts": counts,
        "results": results,
    }


async def submit_channel_builder_rollback_job(server: Any, request: web.Request):
    data = await server._request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error(server, "Invalid JSON body")

    guild_id = _safe_int(data.get("guild_id"), 0)
    actor_id = _safe_int(data.get("actor_id") or data.get("staff_id"), 0)
    source_job_id = _safe_str(data.get("source_job_id") or data.get("job_id"))
    if guild_id <= 0:
        return _json_error(server, "guild_id required")
    if not source_job_id:
        return _json_error(server, "source_job_id required")

    rollback_plan, source_job, error = _source_job_rollback_plan(source_job_id, guild_id)
    if error:
        return _json_error(server, error, 409, source_job=source_job)

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
            factory=lambda: _execute_rollback_plan(
                server=server,
                guild_id=guild_id,
                actor_id=actor_id,
                source_job_id=source_job_id,
                rollback_plan=rollback_plan,
            ),
        )
        return _json_ok(server, queued=True, job=job, source_job_id=source_job_id, rollback_count=len(rollback_plan))
    except Exception as e:
        return _json_error(server, "Failed to queue Channel Builder rollback", 500, detail=repr(e))


def _patch_channel_builder_guard(cb_guard: Any) -> bool:
    original = getattr(cb_guard, "_register_channel_builder_routes", None)
    if not callable(original) or getattr(original, "_rollback_routes_wrapped", False):
        return False

    def register_with_rollback(server: Any, app: web.Application) -> None:
        original(server, app)
        app.router.add_post("/channel-builder/rollback", lambda request: submit_channel_builder_rollback_job(server, request))

    setattr(register_with_rollback, "_rollback_routes_wrapped", True)
    setattr(register_with_rollback, "_rollback_routes_original", original)
    setattr(cb_guard, "_register_channel_builder_routes", register_with_rollback)
    _log("patched Channel Builder routes with queued rollback endpoint")
    return True


def _maybe_patch_loaded() -> None:
    try:
        cb_guard = sys.modules.get("stoney_verify.startup_guards.channel_builder_api_guard")
        if cb_guard is None:
            from . import channel_builder_api_guard as cb_guard  # type: ignore
        _patch_channel_builder_guard(cb_guard)
    except Exception as e:
        _warn(f"patch loaded channel_builder_api_guard failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.startup_guards.channel_builder_api_guard" or name.endswith(".channel_builder_api_guard"):
            target = sys.modules.get("stoney_verify.startup_guards.channel_builder_api_guard") or sys.modules.get(name)
            if target is not None:
                _patch_channel_builder_guard(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_rollback_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_rollback_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; Channel Builder rollback queue guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
