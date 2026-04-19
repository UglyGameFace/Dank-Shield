# stoney_verify/vc_sessions.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from .globals import *  # noqa


_MEM_SESSIONS: Dict[str, Dict[str, Any]] = {}


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

    try:
        getter = globals().get("get_supabase")
        if callable(getter):
            v = getter()
            if v:
                return v
    except Exception:
        pass

    return None


def sb_enabled() -> bool:
    return bool(_sb())


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
        "session_extended_count": 0,
        "session_extended_reason": "",
        "session_extended_at": None,
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


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _utcnow().isoformat()
    token = str(row.get("token") or "").strip()

    normalized = dict(row)
    normalized["token"] = token
    normalized["status"] = _normalize_status(normalized.get("status"))
    normalized["meta"] = _merge_meta(normalized.get("meta"))
    normalized["access_minutes"] = int(normalized.get("access_minutes") or _access_minutes())
    normalized["ticket_channel_id"] = int(normalized.get("ticket_channel_id") or 0) or None
    normalized["requester_id"] = int(normalized.get("requester_id") or 0) or None
    normalized["owner_id"] = int(normalized.get("owner_id") or normalized.get("requester_id") or 0) or None
    normalized["vc_channel_id"] = int(normalized.get("vc_channel_id") or _configured_vc_channel_id() or 0) or None
    normalized["queue_channel_id"] = int(normalized.get("queue_channel_id") or _queue_channel_id() or 0) or None

    if not normalized.get("created_at"):
        normalized["created_at"] = now_iso
    normalized["updated_at"] = normalized.get("updated_at") or now_iso

    return normalized


def _write_mem(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_row(row)
    token = str(normalized.get("token") or "").strip()
    if token:
        _MEM_SESSIONS[token] = normalized
    return normalized


def _read_mem(token: str) -> Optional[Dict[str, Any]]:
    tok = str(token or "").strip()
    if not tok:
        return None
    row = _MEM_SESSIONS.get(tok)
    if isinstance(row, dict):
        return _normalize_row(row)
    return None


def _db_upsert(payload: Dict[str, Any]) -> None:
    sb = _sb()
    if not sb:
        return

    try:
        sb.table(_table()).upsert(payload, on_conflict="token").execute()
        return
    except Exception:
        pass

    try:
        sb.table(_table()).upsert(payload).execute()
        return
    except Exception:
        pass

    try:
        sb.table(_table()).insert(payload).execute()
    except Exception:
        return


def _db_update(token: str, patch: Dict[str, Any]) -> None:
    sb = _sb()
    if not sb:
        return
    try:
        sb.table(_table()).update(patch).eq("token", str(token)).execute()
    except Exception:
        return


def _db_get(token: str) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if not sb:
        return None

    try:
        res = sb.table(_table()).select("*").eq("token", str(token)).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows:
            return _normalize_row(dict(rows[0]))
    except Exception:
        return None

    return None


def get_session(token: str) -> Optional[Dict[str, Any]]:
    tok = str(token or "").strip()
    if not tok:
        return None

    row = _db_get(tok)
    if row:
        _write_mem(row)
        return row

    return _read_mem(tok)


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
    tok = str(token or "").strip()
    if not tok:
        return

    now_iso = _utcnow().isoformat()
    revoke_at = (_utcnow() + timedelta(minutes=int(access_minutes or 30))).isoformat()

    payload = _normalize_row(
        {
            "token": tok,
            "guild_id": int(guild_id),
            "ticket_channel_id": int(ticket_channel_id or 0) or None,
            "requester_id": int(requester_id or 0) or None,
            "owner_id": int(owner_id or 0) or None,
            "vc_channel_id": int(vc_channel_id or 0) or None,
            "queue_channel_id": int(queue_channel_id or 0) or None,
            "status": "PENDING",
            "access_minutes": int(access_minutes or 30),
            "revoke_at": revoke_at,
            "meta": _initial_meta(meta),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )

    _write_mem(payload)
    _db_upsert(payload)


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


def _update_local(token: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    current = get_session(token) or {"token": str(token)}
    updated = _normalize_row({**current, **patch, "updated_at": _utcnow().isoformat()})
    _write_mem(updated)
    return updated


def set_queue_message(*, token: str, queue_message_id: int) -> None:
    row = _update_local(str(token), {"queue_message_id": int(queue_message_id)})
    if row:
        _db_update(str(token), {"queue_message_id": int(queue_message_id), "updated_at": row["updated_at"]})


def update_meta(
    *,
    token: str,
    patch: Dict[str, Any],
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(current.get("meta"), patch)
    row = _update_local(str(token), {"meta": meta})
    if row:
        _db_update(str(token), {"meta": meta, "updated_at": row["updated_at"]})


def extend_expiry(
    *,
    token: str,
    minutes: int = 0,
    reason: str = "",
    by_staff_id: int = 0,
) -> None:
    current = get_session(token) or {}
    if not current:
        return

    access_minutes = int(current.get("access_minutes") or 0)
    extension_minutes = int(minutes or access_minutes or _access_minutes())
    if extension_minutes <= 0:
        extension_minutes = _access_minutes()

    meta_current = _merge_meta(current.get("meta"))
    extension_count = int(meta_current.get("session_extended_count") or 0) + 1
    now_iso = _utcnow().isoformat()

    meta = _merge_meta(
        meta_current,
        {
            "session_extended_count": extension_count,
            "session_extended_reason": str(reason or "session still active"),
            "session_extended_at": now_iso,
            "last_action": "extend_expiry",
            "last_action_at": now_iso,
            "last_action_by": int(by_staff_id or 0) or None,
        },
    )

    revoke_at = (_utcnow() + timedelta(minutes=extension_minutes)).isoformat()
    row = _update_local(str(token), {"revoke_at": revoke_at, "meta": meta})
    if row:
        _db_update(str(token), {"revoke_at": revoke_at, "meta": meta, "updated_at": row["updated_at"]})


def _active_statuses() -> set[str]:
    return {
        "PENDING",
        "STAFF_ACCEPTED",
        "OWNER_CONFIRMED",
        "READY",
        "IN_VC",
        "STARTED",
        "TAKEN_OVER",
        "RESTARTED",
    }


def get_reusable_session(
    *,
    guild_id: int,
    owner_id: int,
    vc_channel_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    candidates: list[Dict[str, Any]] = []

    if sb_enabled():
        sb = _sb()
        if sb:
            try:
                query = (
                    sb.table(_table())
                    .select("*")
                    .eq("guild_id", int(guild_id))
                    .eq("owner_id", int(owner_id))
                    .limit(25)
                )
                if vc_channel_id and int(vc_channel_id) > 0:
                    query = query.eq("vc_channel_id", int(vc_channel_id))
                res = query.execute()
                rows = getattr(res, "data", None) or []
                for raw in rows:
                    if isinstance(raw, dict):
                        candidates.append(_normalize_row(dict(raw)))
            except Exception:
                pass

    for raw in list(_MEM_SESSIONS.values()):
        try:
            row = _normalize_row(dict(raw))
            if int(row.get("guild_id") or 0) != int(guild_id):
                continue
            if int(row.get("owner_id") or row.get("requester_id") or 0) != int(owner_id):
                continue
            if vc_channel_id and int(vc_channel_id) > 0 and int(row.get("vc_channel_id") or 0) != int(vc_channel_id):
                continue
            candidates.append(row)
        except Exception:
            continue

    dedup: Dict[str, Dict[str, Any]] = {}
    for row in candidates:
        tok = str(row.get("token") or "").strip()
        if tok:
            dedup[tok] = row

    active_rows = []
    for row in dedup.values():
        if _normalize_status(row.get("status")) in _active_statuses():
            active_rows.append(row)

    active_rows.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return active_rows[0] if active_rows else None


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
    current = get_session(token) or {"token": str(token)}
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
        patch["meta"] = _merge_meta(current.get("meta"), extra)

    row = _update_local(str(token), patch)
    if row:
        db_patch = dict(patch)
        db_patch["updated_at"] = row["updated_at"]
        _db_update(str(token), db_patch)


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
    row = _update_local(str(token), {"last_watchdog_at": _utcnow().isoformat()})
    if row:
        _db_update(str(token), {"last_watchdog_at": row["last_watchdog_at"], "updated_at": row["updated_at"]})


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
