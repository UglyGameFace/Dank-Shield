from __future__ import annotations

"""Add queued Channel Builder routes to the structured Bot API.

The dashboard must never direct-fire multi-step Discord mutations. These routes
accept an approved dry-run plan, submit it to the shared operation queue, and
expose job status for polling/re-attach after refresh.
"""

import builtins
import sys
from typing import Any, Optional

import discord
from aiohttp import web

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_api_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_api_guard {message}")
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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_str(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_action(value: Any) -> str:
    text = _safe_str(value).lower().replace("-", "_")
    if text in {"create", "rename", "keep", "skip", "conflict"}:
        return text
    return "skip"


def _normalize_channel_type(value: Any) -> str:
    text = _safe_str(value).lower().replace("announcement", "news")
    if text in {"text", "voice", "forum", "news", "category"}:
        return text
    return "text"


def _normalize_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(raw[:150]):
        if not isinstance(row, dict):
            continue
        final_name = _safe_str(row.get("finalName") or row.get("final_name"))[:100]
        base_name = _safe_str(row.get("baseName") or row.get("base_name") or row.get("name"))[:100]
        current_name = _safe_str(row.get("currentName") or row.get("current_name"))[:100]
        current_id = _safe_int(row.get("channelId") or row.get("channel_id") or row.get("currentId") or row.get("current_id"), 0)
        action = _normalize_action(row.get("action"))
        selected = row.get("selected") is not False
        if not selected:
            action = "skip"
        items.append(
            {
                "index": index,
                "id": _safe_str(row.get("id") or f"row-{index + 1}"),
                "action": action,
                "type": _normalize_channel_type(row.get("type")),
                "base_name": base_name,
                "final_name": final_name,
                "current_name": current_name,
                "current_id": current_id,
                "category": _safe_str(row.get("category"))[:100],
                "protected": bool(row.get("protected")),
                "selected": selected,
            }
        )
    return items


def _validate_items(items: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    targets: dict[str, int] = {}
    for item in items:
        action = item.get("action")
        if action in {"skip", "keep"}:
            continue
        final_name = _safe_str(item.get("final_name"))
        if not final_name:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name required")
            continue
        if len([*final_name]) > 100:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name is over Discord's 100 character limit")
        key = final_name.lower()
        if key in targets:
            errors.append(f"duplicate target name #{final_name}")
        targets[key] = int(item.get("index", 0))
        if action == "conflict":
            errors.append(f"row {int(item.get('index', 0)) + 1}: conflict must be fixed before queueing")
        if action == "rename" and not item.get("current_id") and not item.get("current_name"):
            errors.append(f"row {int(item.get('index', 0)) + 1}: rename requires current channel id or current name")
    return errors[:25]


def _json_ok(server: Any, **extra: Any):
    return server._json_ok(**extra)


def _json_error(server: Any, message: str, status: int = 400, **extra: Any):
    return server._json_error(message, status, **extra)


async def _get_guild(server: Any, guild_id: Any) -> tuple[Optional[discord.Guild], Optional[web.Response]]:
    if hasattr(server, "_get_guild_or_error"):
        return await server._get_guild_or_error(guild_id)
    gid = _safe_int(guild_id, 0)
    guild = server.bot.get_guild(gid) if gid else None
    if guild is None:
        return None, _json_error(server, "Guild not found", 404)
    return guild, None


def _find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    target = _safe_str(name).lower()
    if not target:
        return None
    for category in getattr(guild, "categories", []) or []:
        if _safe_str(category.name).lower() == target:
            return category
    return None


async def _ensure_category(guild: discord.Guild, name: str, *, reason: str) -> Optional[discord.CategoryChannel]:
    existing = _find_category(guild, name)
    if existing is not None:
        return existing
    if not name:
        return None
    return await guild.create_category(name=name[:100], reason=reason)


def _find_channel(guild: discord.Guild, item: dict[str, Any]) -> Optional[discord.abc.GuildChannel]:
    current_id = _safe_int(item.get("current_id"), 0)
    if current_id > 0:
        channel = guild.get_channel(current_id)
        if channel is not None:
            return channel
    current_name = _safe_str(item.get("current_name")).lower()
    if current_name:
        for channel in getattr(guild, "channels", []) or []:
            if _safe_str(getattr(channel, "name", "")).lower() == current_name:
                return channel
    return None


async def _create_channel(guild: discord.Guild, item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_type = _safe_str(item.get("type"), "text")
    final_name = _safe_str(item.get("final_name"))[:100]
    category = await _ensure_category(guild, _safe_str(item.get("category")), reason=reason)

    if channel_type == "voice":
        channel = await guild.create_voice_channel(name=final_name, category=category, reason=reason)
    elif channel_type == "forum" and hasattr(guild, "create_forum"):
        channel = await guild.create_forum(name=final_name, category=category, reason=reason)
    elif channel_type == "news":
        try:
            channel = await guild.create_text_channel(name=final_name, category=category, news=True, reason=reason)
        except TypeError:
            channel = await guild.create_text_channel(name=final_name, category=category, reason=reason)
    elif channel_type == "category":
        channel = await _ensure_category(guild, final_name, reason=reason)
    else:
        channel = await guild.create_text_channel(name=final_name, category=category, reason=reason)

    return {
        "ok": True,
        "action": "create",
        "row_id": item.get("id"),
        "channel_id": str(getattr(channel, "id", "")),
        "name": getattr(channel, "name", final_name),
        "type": channel_type,
    }


async def _rename_channel(guild: discord.Guild, item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel = _find_channel(guild, item)
    if channel is None:
        return {
            "ok": False,
            "action": "rename",
            "row_id": item.get("id"),
            "error": "existing channel not found",
            "current_name": item.get("current_name"),
            "current_id": item.get("current_id"),
        }
    before = _safe_str(getattr(channel, "name", ""))
    final_name = _safe_str(item.get("final_name"))[:100]
    if before == final_name:
        return {
            "ok": True,
            "action": "keep",
            "row_id": item.get("id"),
            "channel_id": str(getattr(channel, "id", "")),
            "name": before,
        }
    await channel.edit(name=final_name, reason=reason)
    return {
        "ok": True,
        "action": "rename",
        "row_id": item.get("id"),
        "channel_id": str(getattr(channel, "id", "")),
        "before": before,
        "after": final_name,
    }


async def _execute_channel_builder_plan(
    *,
    server: Any,
    guild_id: int,
    actor_id: int,
    items: list[dict[str, Any]],
    mode: str,
    dry_run: bool,
) -> dict[str, Any]:
    guild, err = await _get_guild(server, guild_id)
    if err is not None:
        return {"status": "failed", "error": "guild not found"}
    assert guild is not None

    reason = f"Dank Shield Channel Builder {mode} by {actor_id or 'dashboard'}"
    results: list[dict[str, Any]] = []
    counts = {"create": 0, "rename": 0, "keep": 0, "skip": 0, "failed": 0}

    for item in items:
        action = _safe_str(item.get("action"))
        if action in {"skip", "conflict"}:
            counts["skip"] += 1
            results.append({"ok": True, "action": "skip", "row_id": item.get("id")})
            continue
        if action == "keep":
            counts["keep"] += 1
            results.append({"ok": True, "action": "keep", "row_id": item.get("id"), "name": item.get("final_name")})
            continue
        if dry_run:
            counts[action if action in counts else "skip"] = counts.get(action, 0) + 1
            results.append({"ok": True, "dry_run": True, "action": action, "row_id": item.get("id"), "target": item.get("final_name")})
            continue
        try:
            if action == "create":
                result = await _create_channel(guild, item, reason=reason)
                counts["create"] += 1
            elif action == "rename":
                result = await _rename_channel(guild, item, reason=reason)
                if result.get("ok"):
                    counts["rename"] += 1 if result.get("action") == "rename" else 0
                    counts["keep"] += 1 if result.get("action") == "keep" else 0
                else:
                    counts["failed"] += 1
            else:
                result = {"ok": True, "action": "skip", "row_id": item.get("id")}
                counts["skip"] += 1
        except Exception as e:
            counts["failed"] += 1
            result = {"ok": False, "action": action, "row_id": item.get("id"), "error": repr(e)}
        results.append(result)

    status = "partial" if counts["failed"] else "succeeded"
    return {
        "status": status,
        "mode": mode,
        "dry_run": dry_run,
        "guild_id": str(guild_id),
        "counts": counts,
        "results": results,
    }


async def submit_channel_builder_job(server: Any, request: web.Request):
    data = await server._request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error(server, "Invalid JSON body")

    guild_id = _safe_int(data.get("guild_id"), 0)
    actor_id = _safe_int(data.get("actor_id") or data.get("staff_id"), 0)
    mode = _safe_str(data.get("mode") or "apply_plan").lower()[:60]
    dry_run = _safe_bool(data.get("dry_run"), False)
    items = _normalize_items(data.get("items"))

    if guild_id <= 0:
        return _json_error(server, "guild_id required")
    if not items:
        return _json_error(server, "items required")

    errors = _validate_items(items)
    if errors:
        return _json_error(server, "Channel Builder plan is not queueable", 409, errors=errors)

    try:
        from ..operation_queue import submit_operation

        job = await submit_operation(
            guild_id=guild_id,
            actor_id=actor_id or None,
            operation_type="channel_builder_apply_plan" if not dry_run else "channel_builder_dry_run_job",
            risk_level="dangerous" if not dry_run else "moderate",
            source="dashboard",
            payload={"mode": mode, "items": items, "dry_run": dry_run},
            concurrency_class="channel_mutation",
            concurrency_key="channel_builder",
            timeout_seconds=900.0,
            progress_total=len(items),
            factory=lambda: _execute_channel_builder_plan(
                server=server,
                guild_id=guild_id,
                actor_id=actor_id,
                items=items,
                mode=mode,
                dry_run=dry_run,
            ),
        )
        return _json_ok(server, queued=True, job=job)
    except Exception as e:
        return _json_error(server, "Failed to queue Channel Builder job", 500, detail=repr(e))


async def get_operation_job(server: Any, request: web.Request):
    job_id = _safe_str(request.match_info.get("job_id") or request.query.get("job_id"))
    if not job_id:
        return _json_error(server, "job_id required")
    try:
        from ..operation_queue import get_operation_job as _get_operation_job

        job = _get_operation_job(job_id)
        if not job:
            return _json_error(server, "job not found", 404)
        return _json_ok(server, job=job)
    except Exception as e:
        return _json_error(server, "Failed to read operation job", 500, detail=repr(e))


def _register_channel_builder_routes(server: Any, app: web.Application) -> None:
    app.router.add_post("/channel-builder/jobs", lambda request: submit_channel_builder_job(server, request))
    app.router.add_get("/operation/{job_id}", lambda request: get_operation_job(server, request))
    app.router.add_get("/operations/{job_id}", lambda request: get_operation_job(server, request))


def _patch_server_module(server: Any) -> None:
    global _PATCHED
    if getattr(server, "_CHANNEL_BUILDER_API_GUARD_PATCHED", False):
        return

    original_start_api = getattr(server, "start_api", None)
    if not callable(original_start_api):
        _warn("server.start_api missing; cannot add routes")
        return

    async def start_api_with_channel_builder(bot_instance: discord.Client):
        if getattr(server, "_API_RUNNER", None) is not None:
            print("⚠️ New structured Bot API already running; skipping duplicate start.")
            return

        server._validate_api_startup_config()
        app = web.Application(middlewares=[server._auth_middleware])

        app.router.add_get("/health", server.health)
        app.router.add_post("/ticket/create", server.create_ticket)
        app.router.add_post("/ticket/close", server.close_ticket)
        app.router.add_post("/ticket/delete", server.delete_ticket)
        app.router.add_post("/ticket/reopen", server.reopen_ticket_endpoint)
        app.router.add_post("/ticket/assign", server.assign_ticket_endpoint)
        if getattr(server, "unclaim_ticket", None) is not None:
            app.router.add_post("/ticket/unclaim", server.unclaim_ticket_endpoint)
        if getattr(server, "transfer_ticket", None) is not None:
            app.router.add_post("/ticket/transfer", server.transfer_ticket_endpoint)
        app.router.add_get("/tickets/queue", server.get_ticket_queue)
        app.router.add_post("/tickets/queue", server.get_ticket_queue)
        app.router.add_get("/tickets/unclaimed", server.get_unclaimed_tickets)
        app.router.add_post("/tickets/unclaimed", server.get_unclaimed_tickets)
        app.router.add_get("/tickets/claimed", server.get_claimed_tickets)
        app.router.add_post("/tickets/claimed", server.get_claimed_tickets)
        app.router.add_get("/tickets/my-claimed", server.get_my_claimed_tickets)
        app.router.add_post("/tickets/my-claimed", server.get_my_claimed_tickets)
        app.router.add_post("/tickets/sync-active", server.sync_active_tickets)
        app.router.add_post("/tickets/sync-one", server.sync_one_ticket)
        app.router.add_post("/members/sync", server.force_member_sync)
        app.router.add_post("/members/reconcile", server.reconcile_departed)
        app.router.add_post("/members/role-sync", server.role_member_sync)
        _register_channel_builder_routes(server, app)

        runner = web.AppRunner(app)
        await runner.setup()
        bind_host = server._api_bind_host()
        bind_port = server._api_bind_port()
        site = web.TCPSite(runner, bind_host, bind_port)
        await site.start()
        server._API_RUNNER = runner
        server._API_SITE = site
        print(
            f"🌐 New structured Bot API started on {bind_host}:{bind_port} "
            f"(auth_required={server._should_require_api_auth()}, channel_builder_routes=true)"
        )

    setattr(start_api_with_channel_builder, "_channel_builder_api_original", original_start_api)
    server.start_api = start_api_with_channel_builder
    server._CHANNEL_BUILDER_API_GUARD_PATCHED = True
    _PATCHED = True
    _log("patched structured API startup with queued Channel Builder routes")


def _maybe_patch_loaded() -> None:
    try:
        server = sys.modules.get("stoney_verify.api_new.server")
        if server is not None:
            _patch_server_module(server)
    except Exception as e:
        _warn(f"patch loaded server failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.api_new.server" or name.endswith(".api_new.server"):
            target = sys.modules.get("stoney_verify.api_new.server") or sys.modules.get(name)
            if target is not None:
                _patch_server_module(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_api_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_api_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; queued Channel Builder API guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
