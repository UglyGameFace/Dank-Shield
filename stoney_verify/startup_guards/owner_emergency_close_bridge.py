from __future__ import annotations

"""Route confirmed owner emergency close through the full ticket lifecycle.

The canonical close service intentionally authorizes ordinary ``close`` as a
claimant action. This bridge issues a short-lived ContextVar capability only
while the real guild owner submits the dedicated confirmation modal. A matching
owner, channel, action, and reason prefix are all required before the lifecycle
can run with internal authority, so typing the prefix into another command can
never imitate Emergency Override.
"""

from contextvars import ContextVar
from typing import Any, Optional

from ..tickets_new.owner_emergency_override import is_actual_guild_owner

_PATCHED = False
_PREFIX = "Owner emergency override:"
_CONFIRMED_CLOSE: ContextVar[Optional[tuple[int, int]]] = ContextVar(
    "dank_confirmed_owner_emergency_close",
    default=None,
)
_EXECUTOR_MARKER = "_owner_emergency_close_confirmation_context"


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


def _confirmed_close_matches(channel: Any, actor: Any) -> bool:
    capability = _CONFIRMED_CLOSE.get()
    if capability is None:
        return False
    return capability == (
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

        token = _CONFIRMED_CLOSE.set((channel_id, owner_id))
        try:
            return await original(*args, **kwargs)
        finally:
            _CONFIRMED_CLOSE.reset(token)

    setattr(confirmed_ui_execute, _EXECUTOR_MARKER, True)
    ui_guard.execute_owner_emergency_override = confirmed_ui_execute
    return True


def _patch_close_lifecycle() -> bool:
    try:
        from ..tickets_new import service
        from ..tickets_new.explicit_system_action_guard import (
            explicit_ticket_system_action,
        )
    except Exception as exc:
        print(f"⚠️ owner_emergency_close_bridge lifecycle import failed: {exc!r}")
        return False

    original = getattr(service, "mark_ticket_closed", None)
    if not callable(original):
        return False
    if bool(getattr(original, "_owner_emergency_close_bridge", False)):
        return True

    async def wrapped_mark_ticket_closed(*args: Any, **kwargs: Any) -> bool:
        channel = kwargs.get("channel")
        actor = kwargs.get("closed_by")
        reason = str(kwargs.get("reason") or "").strip()
        guild = getattr(channel, "guild", None)

        is_confirmed_owner_override = bool(
            channel is not None
            and actor is not None
            and reason.startswith(_PREFIX)
            and is_actual_guild_owner(guild, actor)
            and _confirmed_close_matches(channel, actor)
        )
        if not is_confirmed_owner_override:
            return bool(await original(*args, **kwargs))

        system_kwargs = dict(kwargs)
        system_kwargs["closed_by"] = None
        async with explicit_ticket_system_action("confirmed-owner-emergency-close"):
            closed = bool(await original(*args, **system_kwargs))

        if not closed:
            return False

        try:
            await service._freeze_open_ticket_controls_safe(channel, closed_by=actor)
        except Exception:
            pass
        try:
            await service._post_staff_closed_message_safe(channel, closed_by=actor)
        except Exception:
            pass

        try:
            row = await service._ticket_row_for_channel_id(channel.id)
            from ..tickets_new.event_service import log_ticket_closed

            await log_ticket_closed(
                guild_id=channel.guild.id,
                actor_user_id=actor.id,
                actor_name=_name(actor),
                channel_id=channel.id,
                reason=reason.removeprefix(_PREFIX).strip(),
                ticket_row=row,
                source="tickets_new_owner_emergency_close",
                metadata={
                    "owner_emergency_override": True,
                    "confirmed_ui_context": True,
                    "allow_duplicate_event": True,
                },
            )
        except Exception:
            pass

        return True

    setattr(wrapped_mark_ticket_closed, "_owner_emergency_close_bridge", True)
    setattr(service, "mark_ticket_closed", wrapped_mark_ticket_closed)
    return True


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
            "context_bound=True"
        )
    return _PATCHED


apply()

__all__ = ["apply"]
