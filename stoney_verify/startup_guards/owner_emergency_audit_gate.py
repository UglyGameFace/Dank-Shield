from __future__ import annotations

"""Require a durable audit intent before any owner emergency mutation.

The action service also writes detailed outcome events. This guard is the
fail-closed boundary: if the confirmed owner action cannot first be recorded in
the activity feed, no transfer, unclaim, close, or delete is allowed to start.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from ..tickets_new.claim_policy import (
    evaluate_ticket_action,
    ticket_claimed_by_id,
)
from ..tickets_new.owner_emergency_override import (
    OwnerEmergencyResult,
    is_actual_guild_owner,
)

_PATCHED = False
_MARKER = "_owner_emergency_audit_gate"
_VALID_ACTIONS = frozenset({"transfer", "unclaim", "close", "delete"})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _clean_reason(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:500]


def _name(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return "Discord guild owner"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guild_owner_id(guild: Any) -> int:
    return _safe_int(getattr(guild, "owner_id", 0), 0) or _safe_int(
        getattr(getattr(guild, "owner", None), "id", 0),
        0,
    )


async def _ticket_row(channel_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..tickets_new.repository import get_ticket_by_any_channel_id

        row = await get_ticket_by_any_channel_id(channel_id)
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


async def _write_audit_event(
    *,
    phase: str,
    channel: Any,
    actor: Any,
    action: str,
    reason: str,
    row: Optional[dict[str, Any]],
    target: Any = None,
    result: Optional[OwnerEmergencyResult] = None,
) -> bool:
    try:
        from ..tickets_new.event_service import log_ticket_event

        previous_claimed_by = ticket_claimed_by_id(row)
        metadata: dict[str, Any] = {
            "owner_emergency_override": True,
            "override_phase": phase,
            "override_action": action,
            "override_reason": reason,
            "override_owner_id": str(getattr(actor, "id", 0)),
            "override_owner_name": _name(actor),
            "override_timestamp": _now_iso(),
            "previous_claimed_by": str(previous_claimed_by) if previous_claimed_by else None,
            "allow_duplicate_event": True,
        }
        if target is not None:
            metadata["transfer_target_user_id"] = str(getattr(target, "id", 0))
            metadata["transfer_target_name"] = _name(target)
        if result is not None:
            metadata["override_success"] = bool(result.ok)
            metadata["override_result_code"] = result.code
            metadata["override_result_message"] = result.message
            metadata.update(dict(result.metadata or {}))

        return bool(
            await log_ticket_event(
                guild_id=channel.guild.id,
                event_type=(
                    "ticket_owner_emergency_override_authorized"
                    if phase == "authorized"
                    else "ticket_owner_emergency_override_result"
                ),
                actor_user_id=actor.id,
                actor_name=_name(actor),
                target_user_id=getattr(target, "id", None),
                target_name=_name(target) if target is not None else None,
                channel_id=channel.id,
                channel_name=getattr(channel, "name", None),
                reason=reason,
                source="tickets_new_owner_emergency_audit_gate",
                metadata=metadata,
                ticket_row=row,
            )
        )
    except Exception as exc:
        try:
            print(
                "⚠️ owner_emergency_audit_gate write failed "
                f"phase={phase} channel={getattr(channel, 'id', 0)} "
                f"action={action} error={exc!r}"
            )
        except Exception:
            pass
        return False


def _policy_action(action: str) -> str:
    return (
        "owner_emergency_delete_prepare"
        if action == "delete"
        else f"owner_emergency_{action}"
    )


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from . import owner_emergency_override_guard as ui_guard
    except Exception as exc:
        print(f"⚠️ owner_emergency_audit_gate UI import failed: {exc!r}")
        return False

    original = getattr(ui_guard, "execute_owner_emergency_override", None)
    if not callable(original):
        return False
    if bool(getattr(original, _MARKER, False)):
        _PATCHED = True
        return True

    async def audited_execute(*args: Any, **kwargs: Any) -> OwnerEmergencyResult:
        channel = kwargs.get("channel")
        actor = kwargs.get("actor")
        action = str(kwargs.get("action") or "").strip().lower().replace("-", "_")
        reason = _clean_reason(kwargs.get("reason"))
        target = kwargs.get("target_member")
        guild = getattr(channel, "guild", None)

        if (
            action not in _VALID_ACTIONS
            or channel is None
            or actor is None
            or len(reason) < 8
            or not is_actual_guild_owner(guild, actor)
        ):
            return await original(*args, **kwargs)

        row = await _ticket_row(_safe_int(getattr(channel, "id", 0), 0))
        decision = evaluate_ticket_action(
            row,
            actor_id=getattr(actor, "id", 0),
            action=_policy_action(action),
            guild_owner_id=_guild_owner_id(guild),
        )
        if not decision.allowed:
            return await original(*args, **kwargs)

        audit_ready = await _write_audit_event(
            phase="authorized",
            channel=channel,
            actor=actor,
            action=action,
            reason=reason,
            row=row,
            target=target,
        )
        if not audit_ready:
            return OwnerEmergencyResult(
                False,
                action,
                "audit_unavailable",
                "Emergency Override stopped because its audit record could not be written.",
                {
                    "mutation_started": False,
                    "previous_claimed_by": ticket_claimed_by_id(row),
                },
            )

        try:
            result = await original(*args, **kwargs)
        except Exception as exc:
            result = OwnerEmergencyResult(
                False,
                action,
                "override_exception",
                "Emergency Override stopped because the action raised an internal error.",
                {"error_type": type(exc).__name__},
            )

        try:
            refreshed = await _ticket_row(_safe_int(getattr(channel, "id", 0), 0))
            await _write_audit_event(
                phase="result",
                channel=channel,
                actor=actor,
                action=action,
                reason=reason,
                row=refreshed or row,
                target=target,
                result=result,
            )
        except Exception:
            pass

        return result

    setattr(audited_execute, _MARKER, True)
    ui_guard.execute_owner_emergency_override = audited_execute
    _PATCHED = True
    print("✅ owner_emergency_audit_gate: durable pre-mutation audit required")
    return True


apply()

__all__ = ["apply"]
