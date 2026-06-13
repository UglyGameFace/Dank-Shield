from __future__ import annotations

"""Route structured Bot API mutations through the shared operation queue.

This guard protects dashboard/API calls from duplicate submits, refresh/retry
storms, and same-guild race conditions without changing the public API response
shape for successful calls.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False

_MUTATION_ENDPOINTS = {
    "create_ticket",
    "close_ticket",
    "reopen_ticket_endpoint",
    "assign_ticket_endpoint",
    "unclaim_ticket_endpoint",
    "transfer_ticket_endpoint",
    "delete_ticket",
    "sync_active_tickets",
    "sync_one_ticket",
    "force_member_sync",
    "reconcile_departed",
    "role_member_sync",
}


def _log(message: str) -> None:
    try:
        print(f"🧱 api_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ api_operation_queue_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value)
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


async def _request_data_for_guard(server: Any, request: Any) -> dict[str, Any]:
    try:
        data = await server._merged_request_data(request)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        data = await server._request_data(request)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _guild_id_from_payload(server: Any, endpoint_name: str, data: dict[str, Any]) -> int:
    gid = _safe_int(data.get("guild_id"), 0)
    if gid > 0:
        return gid

    channel_id = _safe_int(data.get("channel_id"), 0)
    if channel_id > 0:
        try:
            channel = server.bot.get_channel(channel_id)
            guild = getattr(channel, "guild", None)
            gid = _safe_int(getattr(guild, "id", 0), 0)
            if gid > 0:
                return gid
        except Exception:
            pass

    return 0


def _actor_id_from_payload(data: dict[str, Any]) -> int:
    for key in ("actor_id", "staff_id", "user_id", "closed_by", "deleted_by"):
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    return 0


def _operation_type(endpoint_name: str) -> str:
    mapping = {
        "create_ticket": "ticket_open",
        "close_ticket": "ticket_close",
        "reopen_ticket_endpoint": "ticket_reopen",
        "assign_ticket_endpoint": "ticket_assign",
        "unclaim_ticket_endpoint": "ticket_unclaim",
        "transfer_ticket_endpoint": "ticket_transfer",
        "delete_ticket": "ticket_delete",
        "sync_active_tickets": "tickets_sync_active",
        "sync_one_ticket": "ticket_sync_one",
        "force_member_sync": "member_full_sync",
        "reconcile_departed": "member_departed_reconcile",
        "role_member_sync": "member_role_sync",
    }
    return mapping.get(endpoint_name, endpoint_name)


def _concurrency(endpoint_name: str, data: dict[str, Any]) -> tuple[str, str]:
    channel_id = _safe_str(data.get("channel_id"), "").strip()
    user_id = _safe_str(data.get("user_id"), "").strip()
    category = _safe_str(data.get("category") or "support", "support").strip().lower() or "support"
    role_id = _safe_str(data.get("role_id"), "").strip()

    if endpoint_name == "create_ticket":
        return "ticket_channel_mutation", f"open:{user_id or 'unknown'}:{category}"

    if endpoint_name in {
        "close_ticket",
        "reopen_ticket_endpoint",
        "assign_ticket_endpoint",
        "unclaim_ticket_endpoint",
        "transfer_ticket_endpoint",
        "delete_ticket",
        "sync_one_ticket",
    }:
        return "ticket_channel_mutation", f"channel:{channel_id or 'unknown'}"

    if endpoint_name == "role_member_sync":
        return "member_role_mutation", f"role:{role_id or 'unknown'}"

    if endpoint_name in {"force_member_sync", "reconcile_departed"}:
        return "member_sync", endpoint_name

    return "guild_config_write", endpoint_name


def _timeout(endpoint_name: str) -> float:
    if endpoint_name in {"force_member_sync", "reconcile_departed", "role_member_sync", "sync_active_tickets"}:
        return 600.0
    if endpoint_name in {"delete_ticket"}:
        return 300.0
    return 180.0


def _busy_response(server: Any, *, endpoint_name: str, state: str, job: dict[str, Any] | None):
    message = (
        "A matching operation is already running for this server. "
        "Refresh in a moment and check the latest status."
    )
    if state == "duplicate":
        return server._json_ok(
            duplicate=True,
            operation_in_progress=True,
            operation_type=_operation_type(endpoint_name),
            job=job,
        )
    return server._json_error(
        message,
        409,
        operation_in_progress=True,
        operation_type=_operation_type(endpoint_name),
        job=job,
    )


def _wrap_endpoint(server: Any, endpoint_name: str) -> bool:
    original = getattr(server, endpoint_name, None)
    if not callable(original) or getattr(original, "_api_operation_queue_wrapped", False):
        return False

    async def wrapped(request: Any):
        data = await _request_data_for_guard(server, request)
        guild_id = _guild_id_from_payload(server, endpoint_name, data)
        actor_id = _actor_id_from_payload(data)
        concurrency_class, concurrency_key = _concurrency(endpoint_name, data)
        operation_type = _operation_type(endpoint_name)

        try:
            from ..operation_queue import run_exclusive

            state, result, job = await run_exclusive(
                guild_id=guild_id or "global",
                actor_id=actor_id or None,
                operation_type=operation_type,
                risk_level="dangerous",
                source="dashboard",
                payload=data,
                concurrency_class=concurrency_class,
                concurrency_key=concurrency_key,
                timeout_seconds=_timeout(endpoint_name),
                reject_if_busy=True,
                factory=lambda: original(request),
            )

            if state in {"duplicate", "busy"}:
                return _busy_response(server, endpoint_name=endpoint_name, state=state, job=job)

            if state == "failed":
                return server._json_error(
                    "Operation failed before it could finish",
                    500,
                    operation_type=operation_type,
                    job=job,
                )

            return result
        except Exception as e:
            _warn(f"queue wrapper failed endpoint={endpoint_name}; running original: {e!r}")
            return await original(request)

    try:
        setattr(wrapped, "_api_operation_queue_wrapped", True)
        setattr(wrapped, "_api_operation_queue_original", original)
    except Exception:
        pass
    setattr(server, endpoint_name, wrapped)
    return True


def _patch_server_module(server: Any) -> None:
    global _PATCHED
    if getattr(server, "_api_operation_queue_guard_patched", False):
        return

    wrapped = 0
    for name in sorted(_MUTATION_ENDPOINTS):
        try:
            if _wrap_endpoint(server, name):
                wrapped += 1
        except Exception as e:
            _warn(f"failed wrapping endpoint={name}: {e!r}")

    try:
        setattr(server, "_api_operation_queue_guard_patched", True)
    except Exception:
        pass

    _PATCHED = True
    _log(f"patched structured API mutation endpoints wrapped={wrapped}")


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
    if getattr(builtins, "_stoney_api_operation_queue_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_api_operation_queue_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; structured API mutation queue guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
