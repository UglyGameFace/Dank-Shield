from __future__ import annotations

"""Authorize confirmed owner emergency close without widening normal close.

A short-lived ContextVar capability exists only while the actual Discord guild
owner submits the dedicated confirmation modal. The canonical ticket service
keeps the real owner as ``closed_by``, so both the database row and the single
lifecycle event retain the owner's Discord identity. Outside that exact context,
ordinary close remains claimant-only and a copied reason prefix grants nothing.
"""

from contextvars import ContextVar
from typing import Any, Optional

from ..tickets_new.claim_policy import evaluate_ticket_action
from ..tickets_new.owner_emergency_override import is_actual_guild_owner

_PATCHED = False
_PREFIX = "Owner emergency override:"
_CONFIRMED_CLOSE: ContextVar[Optional[tuple[int, int, str]]] = ContextVar(
    "dank_confirmed_owner_emergency_close",
    default=None,
)
_EXECUTOR_MARKER = "_owner_emergency_close_confirmation_context"
_AUTHORIZER_MARKER = "_owner_emergency_close_authorizer"
_LOGGER_MARKER = "_owner_emergency_close_logger"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _name(actor: Any) -> str:
    try:
        return str(actor)
    except Exception:
        return "Discord guild owner"


def _confirmed_close_context() -> Optional[tuple[int, int, str]]:
    return _CONFIRMED_CLOSE.get()


def _confirmed_close_matches(channel: Any, actor: Any) -> bool:
    capability = _confirmed_close_context()
    if capability is None:
        return False
    return capability[:2] == (
        _safe_int(getattr(channel, "id", 0), 0),
        _safe_int(getattr(actor, "id", 0), 0),
    )


def _patch_confirmed_ui_executor() -> bool:
    """Issue the capability only around the modal-confirmed close call."""
    try:
        from . import owner_emergency_override_guard as ui_guard
    except Exception as exc:
        print(f"⚠️ owner_emergency_close_bridge UI import failed: {exc!r}")
        return False

    original = getattr(ui_guard, "execute_owner_emergency_override", None)
    if not callable(original):
        return False
    if bool(getattr(original, _EXECUTOR_MARKER, False)):
        return True

    async def confirmed_ui_execute(*args: Any, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower().replace("-", "_")
        channel = kwargs.get("channel")
        actor = kwargs.get("actor")

        if action != "close" or not is_actual_guild_owner(
            getattr(channel, "guild", None),
            actor,
        ):
            return await original(*args, **kwargs)

        channel_id = _safe_int(getattr(channel, "id", 0), 0)
        owner_id = _safe_int(getattr(actor, "id", 0), 0)
        if channel_id <= 0 or owner_id <= 0:
            return await original(*args, **kwargs)

        token = _CONFIRMED_CLOSE.set((channel_id, owner_id, _name(actor)))
        try:
            return await original(*args, **kwargs)
        finally:
            _CONFIRMED_CLOSE.reset(token)

    setattr(confirmed_ui_execute, _EXECUTOR_MARKER, True)
    ui_guard.execute_owner_emergency_override = confirmed_ui_execute
    return True


def _patch_close_authorizer(service: Any) -> bool:
    """Translate only the confirmed close call into the emergency policy action."""
    original_authorizer = getattr(service, "authorize_ticket_action", None)
    if not callable(original_authorizer):
        return False
    if bool(getattr(original_authorizer, _AUTHORIZER_MARKER, False)):
        return True

    async def owner_emergency_authorizer(*args: Any, **kwargs: Any) -> Any:
        capability = _confirmed_close_context()
        channel_id = _safe_int(kwargs.get("channel_id"), 0)
        actor = kwargs.get("actor")
        action = str(kwargs.get("action") or "").strip().lower().replace(" ", "_")
        actor_id = _safe_int(getattr(actor, "id", 0), 0)

        if (
            capability is None
            or action != "close"
            or channel_id != capability[0]
            or actor_id != capability[1]
        ):
            return await original_authorizer(*args, **kwargs)

        row = kwargs.get("row")
        if not isinstance(row, dict):
            try:
                row = await service._ticket_row_for_channel_id(channel_id)
            except Exception:
                row = None

        return evaluate_ticket_action(
            row,
            actor_id=actor_id,
            action="owner_emergency_close",
            guild_owner_id=capability[1],
        )

    setattr(owner_emergency_authorizer, _AUTHORIZER_MARKER, True)
    service.authorize_ticket_action = owner_emergency_authorizer
    return True


def _patch_close_logger(service: Any) -> bool:
    """Enrich the service's one canonical close event; never add a duplicate."""
    original_logger = getattr(service, "log_ticket_closed", None)
    if not callable(original_logger):
        return False
    if bool(getattr(original_logger, _LOGGER_MARKER, False)):
        return True

    async def owner_attributed_close_logger(*args: Any, **kwargs: Any) -> Any:
        capability = _confirmed_close_context()
        channel_id = _safe_int(kwargs.get("channel_id"), 0)
        if capability is None or channel_id != capability[0]:
            return await original_logger(*args, **kwargs)

        updated = dict(kwargs)
        updated["actor_user_id"] = capability[1]
        updated["actor_name"] = capability[2]
        updated["source"] = "tickets_new_owner_emergency_close"

        reason = str(updated.get("reason") or "").strip()
        if reason.startswith(_PREFIX):
            updated["reason"] = reason.removeprefix(_PREFIX).strip()

        metadata = dict(updated.get("metadata") or {})
        metadata.update(
            {
                "owner_emergency_override": True,
                "confirmed_ui_context": True,
                "allow_duplicate_event": True,
            }
        )
        updated["metadata"] = metadata
        return await original_logger(*args, **updated)

    setattr(owner_attributed_close_logger, _LOGGER_MARKER, True)
    service.log_ticket_closed = owner_attributed_close_logger
    return True


def _patch_close_lifecycle() -> bool:
    try:
        from ..tickets_new import service
    except Exception as exc:
        print(f"⚠️ owner_emergency_close_bridge lifecycle import failed: {exc!r}")
        return False

    authorizer_ready = _patch_close_authorizer(service)
    logger_ready = _patch_close_logger(service)
    return bool(authorizer_ready and logger_ready)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    ui_ready = _patch_confirmed_ui_executor()
    lifecycle_ready = _patch_close_lifecycle()
    _PATCHED = bool(ui_ready and lifecycle_ready)
    if _PATCHED:
        print(
            "✅ owner_emergency_close_bridge: confirmed owner close lifecycle active "
            "context_bound=True database_owner_attribution=True "
            "canonical_event_attribution=True"
        )
    return _PATCHED


apply()

__all__ = ["apply"]
