from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from .globals import *  # noqa


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sb():
    for k in ("sb", "supabase", "SUPABASE"):
        try:
            v = globals().get(k)
            if v:
                return v
        except Exception:
            pass
    return None


def sb_enabled() -> bool:
    try:
        return bool(globals().get("SUPABASE_ENABLED", False))
    except Exception:
        return False


def _table() -> str:
    try:
        t = str(globals().get("VC_SESSIONS_TABLE") or "").strip()
        return t or "vc_verify_sessions"
    except Exception:
        return "vc_verify_sessions"


def _queue_channel_id() -> int:
    try:
        return int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID") or 0)
    except Exception:
        return 0


def _configured_vc_channel_id() -> int:
    try:
        return int(globals().get("VC_VERIFY_CHANNEL_ID") or globals().get("VC_VERIFY_VC_ID") or 0)
    except Exception:
        return 0


def _access_minutes() -> int:
    try:
        return int(globals().get("VC_VERIFY_ACCESS_MINUTES") or 30)
    except Exception:
        return 30


def _initial_meta(meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "owner_confirmed": False,
        "staff_confirmed": False,
        "unlocked": False,
        "assigned_staff_id": None,
        "assigned_staff_name": None,
        "takeover_count": 0,
        "restart_count": 0,
        "last_action": "created",
        "last_action_at": _utcnow().isoformat(),
        "last_action_by": None,
        "started_via": None,
        "restart_reason": None,
        "takeover_reason": None,
        "cancel_reason": None,
        "unlock_guard_passed": False,
        "unlock_guard_reason": "",
        "ticket_required": True,
        "vc_locked_by_default": True,
    }
    if isinstance(meta, dict):
        base.update(meta)
    return base


def _merge_meta(old_meta: Any, new_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = _initial_meta(old_meta if isinstance(old_meta, dict) else None)
    if isinstance(new_meta, dict):
        merged.update(new_meta)
    return merged


def _normalize_status(value: Any) -> str:
    try:
        return str(value or "").upper().strip() or "PENDING"
    except Exception:
        return "PENDING"


def get_session(token: str) -> Optional[Dict[str, Any]]:
    if not sb_enabled():
        return None
    sb = _sb()
    if not sb:
        return None

    try:
        res = sb.table(_table()).select("*").eq("token", str(token)).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows:
            row = dict(rows[0])
            row["meta"] = _merge_meta(row.get("meta"))
            row["status"] = _normalize_status(row.get("status"))
            return row
    except Exception:
        return None
    return None


def create_session(
    *,
    token: str,
    guild_id: int,
    ticket_channel_id: int,
    requester_id: int,
    owner_id: int,
    vc_channel_id: int,
    queue_channel_id: int,
    access_minutes: int,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if not sb_enabled():
        return
    sb = _sb()
    if not sb:
        return

    try:
        revoke_at = _utcnow() + timedelta(minutes=int(access_minutes or 30))
        payload = {
            "token": str(token),
            "guild_id": int(guild_id),
            "ticket_channel_id": int(ticket_channel_id or 0) or None,
            "requester_id": int(requester_id or 0) or None,
            "owner_id": int(owner_id or 0) or None,
            "vc_channel_id": int(vc_channel_id or 0) or None,
            "queue_channel_id": int(queue_channel_id or 0) or None,
            "status": "PENDING",
            "access_minutes": int(access_minutes or 30),
            "revoke_at": revoke_at.isoformat(),
            "meta": _initial_meta(meta),
        }
        sb.table(_table()).upsert(payload).execute()
    except Exception:
        return


def ensure_session(
    *,
    token: str,
    guild_id: int,
    ticket_channel_id: int,
    requester_id: int,
    owner_id: int,
    vc_channel_id: int,
    queue_channel_id: Optional[int] = None,
    access_minutes: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    row = get_session(token)
    if row:
        return row

    create_session(
        token=str(token),
        guild_id=int(guild_id),
        ticket_channel_id=int(ticket_channel_id or 0),
        requester_id=int(requester_id or 0),
        owner_id=int(owner_id or 0),
        vc_channel_id=int(vc_channel_id or 0),
        queue_channel_id=int(queue_channel_id or _queue_channel_id()),
        access_minutes=int(access_minutes or _access_minutes()),
        meta=meta,
    )
    return get_session(token)


def set_queue_message(*, token: str, queue_message_id: int) -> None:
    if not sb_enabled():
        return
    sb = _sb()
    if not sb:
        return
    try:
        sb.table(_table()).update({"queue_message_id": int(queue_message_id)}).eq("token", str(token)).execute()
    except Exception:
        return


def update_meta(
    *,
    token: str,
    patch: Dict[str, Any],
) -> None:
    if not sb_enabled():
        return
    sb = _sb()
    if not sb:
        return

    try:
        current = get_session(token) or {}
        meta = _merge_meta(current.get("meta"), patch)
        sb.table(_table()).update({"meta": meta}).eq("token", str(token)).execute()
    except Exception:
        return


def _allowed_unlock_statuses() -> set[str]:
    return {
        "STAFF_ACCEPTED",
        "OWNER_CONFIRMED",
        "READY",
        "TAKEN_OVER",
        "RESTARTED",
    }


def session_is_unlockable(
    *,
    token: str,
    expected_guild_id: int = 0,
    expected_staff_id: int = 0,
) -> tuple[bool, str]:
    row = get_session(token)
    if not row:
        return False, "Session not found."

    status = _normalize_status(row.get("status"))
    if status not in _allowed_unlock_statuses():
        return False, f"Session status `{status}` cannot unlock VC."

    guild_id = int(row.get("guild_id") or 0)
    if expected_guild_id and guild_id != int(expected_guild_id):
        return False, "Session guild mismatch."

    ticket_channel_id = int(row.get("ticket_channel_id") or 0)
    owner_id = int(row.get("owner_id") or row.get("requester_id") or 0)
    vc_channel_id = int(row.get("vc_channel_id") or 0)
    configured_vc_id = int(_configured_vc_channel_id() or 0)

    if ticket_channel_id <= 0:
        return False, "Session is missing a ticket channel."

    if owner_id <= 0:
        return False, "Session is missing an owner."

    if vc_channel_id <= 0:
        return False, "Session is missing a VC channel."

    if configured_vc_id > 0 and vc_channel_id != configured_vc_id:
        return False, "Session VC does not match configured verify VC."

    meta = _merge_meta(row.get("meta"))
    assigned_staff_id = int(meta.get("assigned_staff_id") or 0)

    if expected_staff_id > 0 and assigned_staff_id > 0 and assigned_staff_id != int(expected_staff_id):
        return False, "Only the assigned staff member can unlock this VC session."

    return True, "Session is unlockable."


def transition(
    *,
    token: str,
    new_status: str,
    staff_id: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not sb_enabled():
        return
    sb = _sb()
    if not sb:
        return

    now = _utcnow().isoformat()
    status = _normalize_status(new_status)
    patch: Dict[str, Any] = {"status": status}

    if status in ("ACCEPTED", "STAFF_ACCEPTED"):
        patch["accepted_at"] = now
        patch["accepted_by"] = int(staff_id or 0) or None
    elif status in ("READY",):
        patch["ready_at"] = now
    elif status in ("IN_VC", "STARTED"):
        patch["started_at"] = now
        patch["started_by"] = int(staff_id or 0) or None
    elif status in ("COMPLETED", "DONE"):
        patch["completed_at"] = now
        patch["completed_by"] = int(staff_id or 0) or None
    elif status in ("CANCELED", "CANCELLED"):
        patch["canceled_at"] = now
        patch["canceled_by"] = int(staff_id or 0) or None
    elif status in ("EXPIRED",):
        patch["expired_at"] = now
    elif status in ("TAKEN_OVER",):
        patch["accepted_at"] = now
        patch["accepted_by"] = int(staff_id or 0) or None
    elif status in ("RESTARTED",):
        patch["restarted_at"] = now
        patch["restarted_by"] = int(staff_id or 0) or None

    if extra is not None:
        current = get_session(token) or {}
        patch["meta"] = _merge_meta(current.get("meta"), extra)

    try:
        sb.table(_table()).update(patch).eq("token", str(token)).execute()
    except Exception:
        return


def set_staff_accepted(
    *,
    token: str,
    staff_id: int,
    staff_name: str = "",
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "staff_confirmed": True,
            "assigned_staff_id": int(staff_id),
            "assigned_staff_name": str(staff_name or ""),
            "last_action": "staff_accept",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(staff_id),
        },
    )
    transition(token=token, new_status="STAFF_ACCEPTED", staff_id=staff_id, extra=meta)


def set_owner_confirmed(
    *,
    token: str,
    owner_id: int,
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "owner_confirmed": True,
            "last_action": "owner_confirm",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(owner_id),
        },
    )
    row = get_session(token) or {}
    row_meta = _merge_meta(row.get("meta"))
    new_status = "READY" if row_meta.get("staff_confirmed") else "OWNER_CONFIRMED"
    transition(token=token, new_status=new_status, staff_id=0, extra=meta)


def mark_unlocked(
    *,
    token: str,
    by_staff_id: int = 0,
    guard_reason: str = "",
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "unlocked": True,
            "unlock_guard_passed": True,
            "unlock_guard_reason": str(guard_reason or "passed"),
            "last_action": "unlock",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id or 0) or None,
        },
    )
    transition(token=token, new_status="READY", staff_id=by_staff_id, extra=meta)


def mark_started(
    *,
    token: str,
    by_staff_id: int,
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "last_action": "start",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id),
        },
    )
    transition(token=token, new_status="IN_VC", staff_id=by_staff_id, extra=meta)


def mark_completed(
    *,
    token: str,
    by_staff_id: int,
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "unlocked": False,
            "last_action": "complete",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id),
        },
    )
    transition(token=token, new_status="COMPLETED", staff_id=by_staff_id, extra=meta)


def mark_canceled(
    *,
    token: str,
    by_staff_id: int,
    reason: str = "",
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "unlocked": False,
            "last_action": "cancel",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id),
            "cancel_reason": str(reason or ""),
        },
    )
    transition(token=token, new_status="CANCELED", staff_id=by_staff_id, extra=meta)


def restart_session(
    *,
    token: str,
    by_staff_id: int,
    by_staff_name: str = "",
    reason: str = "",
) -> None:
    current = get_session(token) or {}
    current_meta = _merge_meta(current.get("meta"))
    restart_count = int(current_meta.get("restart_count") or 0) + 1

    meta = _merge_meta(
        current_meta,
        {
            "owner_confirmed": False,
            "staff_confirmed": True,
            "unlocked": False,
            "assigned_staff_id": int(by_staff_id),
            "assigned_staff_name": str(by_staff_name or ""),
            "restart_count": restart_count,
            "restart_reason": str(reason or ""),
            "last_action": "restart",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id),
        },
    )
    transition(token=token, new_status="RESTARTED", staff_id=by_staff_id, extra=meta)


def takeover_session(
    *,
    token: str,
    new_staff_id: int,
    new_staff_name: str = "",
    reason: str = "",
) -> None:
    current = get_session(token) or {}
    current_meta = _merge_meta(current.get("meta"))
    takeover_count = int(current_meta.get("takeover_count") or 0) + 1

    meta = _merge_meta(
        current_meta,
        {
            "staff_confirmed": True,
            "unlocked": False,
            "assigned_staff_id": int(new_staff_id),
            "assigned_staff_name": str(new_staff_name or ""),
            "takeover_count": takeover_count,
            "takeover_reason": str(reason or ""),
            "last_action": "takeover",
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(new_staff_id),
        },
    )
    transition(token=token, new_status="TAKEN_OVER", staff_id=new_staff_id, extra=meta)


def clear_unlock(
    *,
    token: str,
    by_staff_id: int = 0,
    action_name: str = "relock",
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(
        current.get("meta"),
        {
            "unlocked": False,
            "last_action": action_name,
            "last_action_at": _utcnow().isoformat(),
            "last_action_by": int(by_staff_id or 0) or None,
        },
    )
    update_meta(token=token, patch=meta)


def touch_watchdog(token: str) -> None:
    if not sb_enabled():
        return
    sb = _sb()
    if not sb:
        return
    try:
        sb.table(_table()).update({"last_watchdog_at": _utcnow().isoformat()}).eq("token", str(token)).execute()
    except Exception:
        return


async def start_session(
    *,
    guild_id: int,
    token: str,
    ticket_channel_id: int,
    vc_channel_id: int,
    user_id: int,
    staff_id: int,
) -> None:
    try:
        ensure_session(
            token=str(token),
            guild_id=int(guild_id),
            ticket_channel_id=int(ticket_channel_id or 0),
            requester_id=int(user_id or 0),
            owner_id=int(user_id or 0),
            vc_channel_id=int(vc_channel_id or 0),
            queue_channel_id=int(_queue_channel_id()),
            access_minutes=int(_access_minutes()),
            meta={"started_via": "button"},
        )
    except Exception:
        pass
    try:
        mark_started(token=str(token), by_staff_id=int(staff_id))
    except Exception:
        pass


async def end_session(
    *,
    guild_id: int,
    token: str,
    status: str,
    staff_id: int,
) -> None:
    try:
        norm = _normalize_status(status or "COMPLETED")
        if norm in ("COMPLETED", "DONE"):
            mark_completed(token=str(token), by_staff_id=int(staff_id))
        elif norm in ("CANCELED", "CANCELLED"):
            mark_canceled(token=str(token), by_staff_id=int(staff_id))
        elif norm == "EXPIRED":
            transition(token=str(token), new_status="EXPIRED", staff_id=int(staff_id))
        else:
            transition(token=str(token), new_status=norm, staff_id=int(staff_id))
    except Exception:
        pass