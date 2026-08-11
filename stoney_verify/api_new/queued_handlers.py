from __future__ import annotations

"""Direct structured-API operation-queue adapters.

These wrappers are selected when routes are registered in ``server.py``. They do
not monkeypatch modules or replace import hooks.
"""

from typing import Any, Awaitable, Callable

from ..operation_queue import run_exclusive

Handler = Callable[[Any], Awaitable[Any]]

_MUTATION_TYPES = {
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


async def _request_data(server: Any, request: Any) -> dict[str, Any]:
    for name in ("_merged_request_data", "_request_data"):
        try:
            func = getattr(server, name, None)
            if callable(func):
                data = await func(request)
                if isinstance(data, dict):
                    return dict(data)
        except Exception:
            continue
    return {}


def _guild_id(server: Any, data: dict[str, Any]) -> int:
    gid = _safe_int(data.get("guild_id"), 0)
    if gid > 0:
        return gid
    channel_id = _safe_int(data.get("channel_id"), 0)
    if channel_id > 0:
        try:
            channel = server.bot.get_channel(channel_id)
            return _safe_int(getattr(getattr(channel, "guild", None), "id", 0), 0)
        except Exception:
            pass
    return 0


def _actor_id(data: dict[str, Any]) -> int:
    for key in ("actor_id", "staff_id", "closed_by", "deleted_by", "user_id"):
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    return 0


def _scope(endpoint_name: str, data: dict[str, Any]) -> tuple[str, str]:
    channel_id = _safe_str(data.get("channel_id"), "unknown")
    user_id = _safe_str(data.get("user_id"), "unknown")
    category = _safe_str(data.get("category"), "support").lower()
    role_id = _safe_str(data.get("role_id"), "unknown")
    if endpoint_name == "create_ticket":
        return "ticket_channel_mutation", f"open:{user_id}:{category}"
    if endpoint_name in {
        "close_ticket", "reopen_ticket_endpoint", "assign_ticket_endpoint",
        "unclaim_ticket_endpoint", "transfer_ticket_endpoint", "delete_ticket", "sync_one_ticket",
    }:
        return "ticket_channel_mutation", f"channel:{channel_id}"
    if endpoint_name == "role_member_sync":
        return "member_role_mutation", f"role:{role_id}"
    if endpoint_name in {"force_member_sync", "reconcile_departed"}:
        return "member_sync", endpoint_name
    return "guild_config_write", endpoint_name


def _timeout(endpoint_name: str) -> float:
    if endpoint_name in {"force_member_sync", "reconcile_departed", "role_member_sync", "sync_active_tickets"}:
        return 600.0
    if endpoint_name == "delete_ticket":
        return 300.0
    return 180.0


def queued_api_handler(server: Any, endpoint_name: str, handler: Handler) -> Handler:
    """Return a route handler protected by the canonical operation queue."""

    operation_type = _MUTATION_TYPES.get(endpoint_name, endpoint_name)

    async def wrapped(request: Any):
        data = await _request_data(server, request)
        guild_id = _guild_id(server, data)
        actor_id = _actor_id(data)
        concurrency_class, concurrency_key = _scope(endpoint_name, data)
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
            factory=lambda: handler(request),
        )
        if state == "duplicate":
            return server._json_ok(
                duplicate=True,
                operation_in_progress=bool(job and job.get("status") in {"queued", "running", "waiting_rate_limit"}),
                operation_type=operation_type,
                job=job,
            )
        if state == "busy":
            return server._json_error(
                "A matching operation is already running for this server. Refresh and check its status.",
                409,
                operation_in_progress=True,
                operation_type=operation_type,
                job=job,
            )
        if state == "failed":
            return server._json_error("Operation failed before it could finish", 500, operation_type=operation_type, job=job)
        return result

    wrapped.__name__ = f"queued_{endpoint_name}"
    return wrapped


__all__ = ["queued_api_handler"]
