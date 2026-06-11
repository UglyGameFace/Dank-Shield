from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .globals import *  # noqa


# ============================================================
# VC session storage / cache
# ------------------------------------------------------------
# DB is the source of truth when available.
# Memory is only a warm cache / fallback.
#
# Hardening goals:
# - idempotent state transitions
# - do not write unsupported columns to Supabase
# - safe in-memory concurrency
# - avoid reusing expired sessions
# ============================================================

_MEM_SESSIONS: Dict[str, Dict[str, Any]] = {}
_MEM_LOCK = threading.RLock()

_ALLOWED_STATUSES: set[str] = {
    "PENDING",
    "STAFF_ACCEPTED",
    "OWNER_CONFIRMED",
    "READY",
    "IN_VC",
    "STARTED",
    "TAKEN_OVER",
    "RESTARTED",
    "COMPLETED",
    "DONE",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
}

_ACTIVE_REUSABLE_STATUSES: set[str] = {
    "PENDING",
    "STAFF_ACCEPTED",
    "OWNER_CONFIRMED",
    "READY",
    "IN_VC",
    "STARTED",
    "TAKEN_OVER",
    "RESTARTED",
}

_ALLOWED_UNLOCK_STATUSES: set[str] = {
    "STAFF_ACCEPTED",
    "OWNER_CONFIRMED",
    "READY",
    "TAKEN_OVER",
    "RESTARTED",
}

# Keep DB writes aligned to the schema you showed.
# Anything else stays memory-only / meta-only.
_DB_ALLOWED_COLUMNS: set[str] = {
    "token",
    "guild_id",
    "ticket_channel_id",
    "requester_id",
    "owner_id",
    "vc_channel_id",
    "queue_channel_id",
    "queue_message_id",
    "status",
    "created_at",
    "accepted_at",
    "accepted_by",
    "started_at",
    "completed_at",
    "canceled_at",
    "canceled_by",
    "access_minutes",
    "revoke_at",
    "last_watchdog_at",
    "meta",
}


# ============================================================
# Small helpers
# ============================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: Optional[datetime] = None) -> str:
    dt = value or _utcnow()
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _as_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _normalize_ts(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        text = _as_str(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _log(msg: str) -> None:
    try:
        print(f"🧩 vc_sessions {msg}")
    except Exception:
        pass


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
        value = int(globals().get("VC_VERIFY_ACCESS_MINUTES") or 30)
        return value if value > 0 else 30
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
        "last_action_at": _utc_iso(),
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
        "started_by": None,
        "completed_by": None,
        "restarted_by": None,
        "expired_at": None,
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
    raw = _as_str(value, "PENDING").upper()

    aliases = {
        "ACCEPTED": "STAFF_ACCEPTED",
        "STAFF_ACCEPT": "STAFF_ACCEPTED",
        "OWNER_ACCEPTED": "OWNER_CONFIRMED",
        "CONFIRMED": "OWNER_CONFIRMED",
        "UNLOCKED": "READY",
        "START": "STARTED",
        "ACTIVE": "IN_VC",
        "COMPLETE": "COMPLETED",
        "FINISHED": "COMPLETED",
        "DONE": "COMPLETED",
        "CANCEL": "CANCELED",
        "CANCELLED": "CANCELED",
        "EXPIRE": "EXPIRED",
    }
    status = aliases.get(raw, raw)
    if status not in _ALLOWED_STATUSES:
        return "PENDING"
    if status == "DONE":
        return "COMPLETED"
    if status == "CANCELLED":
        return "CANCELED"
    return status


def _is_terminal_status(status: Any) -> bool:
    return _normalize_status(status) in {"COMPLETED", "CANCELED", "EXPIRED"}


def _row_is_expired(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False

    status = _normalize_status(row.get("status"))
    if status == "EXPIRED":
        return True

    revoke_at = _parse_ts(row.get("revoke_at"))
    if revoke_at is None:
        return False

    try:
        return _utcnow() >= revoke_at
    except Exception:
        return False


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _utc_iso()
    token = _as_str(row.get("token"))

    normalized = dict(row)

    normalized["token"] = token
    normalized["status"] = _normalize_status(normalized.get("status"))
    normalized["guild_id"] = _as_int(normalized.get("guild_id"), 0)
    normalized["ticket_channel_id"] = _as_int(normalized.get("ticket_channel_id"), 0) or None
    normalized["requester_id"] = _as_int(normalized.get("requester_id"), 0) or None
    normalized["owner_id"] = _as_int(normalized.get("owner_id"), 0) or _as_int(normalized.get("requester_id"), 0) or None
    normalized["vc_channel_id"] = _as_int(normalized.get("vc_channel_id"), 0) or _configured_vc_channel_id() or None
    normalized["queue_channel_id"] = _as_int(normalized.get("queue_channel_id"), 0) or _queue_channel_id() or None
    normalized["queue_message_id"] = _as_int(normalized.get("queue_message_id"), 0) or None

    normalized["accepted_by"] = _as_int(normalized.get("accepted_by"), 0) or None
    normalized["started_by"] = _as_int(normalized.get("started_by"), 0) or None
    normalized["completed_by"] = _as_int(normalized.get("completed_by"), 0) or None
    normalized["canceled_by"] = _as_int(normalized.get("canceled_by"), 0) or None
    normalized["restarted_by"] = _as_int(normalized.get("restarted_by"), 0) or None

    normalized["access_minutes"] = _as_int(normalized.get("access_minutes"), _access_minutes())
    if normalized["access_minutes"] <= 0:
        normalized["access_minutes"] = _access_minutes()

    normalized["created_at"] = _normalize_ts(normalized.get("created_at")) or now_iso
    normalized["updated_at"] = _normalize_ts(normalized.get("updated_at")) or now_iso
    normalized["accepted_at"] = _normalize_ts(normalized.get("accepted_at"))
    normalized["ready_at"] = _normalize_ts(normalized.get("ready_at"))
    normalized["started_at"] = _normalize_ts(normalized.get("started_at"))
    normalized["completed_at"] = _normalize_ts(normalized.get("completed_at"))
    normalized["canceled_at"] = _normalize_ts(normalized.get("canceled_at"))
    normalized["expired_at"] = _normalize_ts(normalized.get("expired_at"))
    normalized["restarted_at"] = _normalize_ts(normalized.get("restarted_at"))
    normalized["revoke_at"] = _normalize_ts(normalized.get("revoke_at"))
    normalized["last_watchdog_at"] = _normalize_ts(normalized.get("last_watchdog_at"))

    normalized["meta"] = _merge_meta(normalized.get("meta"))

    if _row_is_expired(normalized) and normalized["status"] not in {"COMPLETED", "CANCELED"}:
        normalized["status"] = "EXPIRED"
        normalized["expired_at"] = normalized["expired_at"] or now_iso
        normalized["meta"] = _merge_meta(
            normalized.get("meta"),
            {
                "expired_at": normalized["expired_at"],
                "last_action": "expire",
                "last_action_at": now_iso,
            },
        )

    return normalized


def _db_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_row(row)

    payload: Dict[str, Any] = {
        "token": normalized["token"],
        "guild_id": normalized["guild_id"],
        "ticket_channel_id": normalized["ticket_channel_id"],
        "requester_id": normalized["requester_id"],
        "owner_id": normalized["owner_id"],
        "vc_channel_id": normalized["vc_channel_id"],
        "queue_channel_id": normalized["queue_channel_id"],
        "queue_message_id": normalized["queue_message_id"],
        "status": normalized["status"],
        "created_at": normalized["created_at"],
        "accepted_at": normalized["accepted_at"],
        "accepted_by": normalized["accepted_by"],
        "started_at": normalized["started_at"],
        "completed_at": normalized["completed_at"],
        "canceled_at": normalized["canceled_at"],
        "canceled_by": normalized["canceled_by"],
        "access_minutes": normalized["access_minutes"],
        "revoke_at": normalized["revoke_at"],
        "last_watchdog_at": normalized["last_watchdog_at"],
        "meta": normalized["meta"],
    }
    return {k: v for k, v in payload.items() if k in _DB_ALLOWED_COLUMNS}


def _write_mem(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_row(row)
    token = _as_str(normalized.get("token"))
    if token:
        with _MEM_LOCK:
            _MEM_SESSIONS[token] = normalized
    return normalized


def _read_mem(token: str) -> Optional[Dict[str, Any]]:
    tok = _as_str(token)
    if not tok:
        return None
    with _MEM_LOCK:
        row = _MEM_SESSIONS.get(tok)
    if isinstance(row, dict):
        return _normalize_row(row)
    return None


# ============================================================
# DB helpers
# ============================================================

def _db_upsert(payload: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False

    clean = _db_payload(payload)

    try:
        sb.table(_table()).upsert(clean, on_conflict="token").execute()
        return True
    except Exception as e:
        _log(f"db_upsert primary failed token={clean.get('token')} err={repr(e)}")

    try:
        sb.table(_table()).upsert(clean).execute()
        return True
    except Exception as e:
        _log(f"db_upsert fallback failed token={clean.get('token')} err={repr(e)}")

    try:
        sb.table(_table()).insert(clean).execute()
        return True
    except Exception as e:
        _log(f"db_insert final failed token={clean.get('token')} err={repr(e)}")
        return False


def _db_update(token: str, patch: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False

    tok = _as_str(token)
    if not tok:
        return False

    clean = dict(patch or {})
    clean.pop("token", None)

    if "status" in clean:
        clean["status"] = _normalize_status(clean.get("status"))
    if "meta" in clean:
        clean["meta"] = _merge_meta(clean.get("meta"))

    clean = {k: v for k, v in clean.items() if k in _DB_ALLOWED_COLUMNS}
    if not clean:
        return True

    try:
        sb.table(_table()).update(clean).eq("token", tok).execute()
        return True
    except Exception as e:
        _log(f"db_update failed token={tok} err={repr(e)}")
        return False


def _db_get(token: str) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if not sb:
        return None

    tok = _as_str(token)
    if not tok:
        return None

    try:
        res = sb.table(_table()).select("*").eq("token", tok).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows:
            return _normalize_row(dict(rows[0]))
    except Exception as e:
        _log(f"db_get failed token={tok} err={repr(e)}")
        return None

    return None


def _db_list_for_owner(
    *,
    guild_id: int,
    owner_id: int,
    vc_channel_id: Optional[int] = None,
    limit: int = 25,
) -> list[Dict[str, Any]]:
    sb = _sb()
    if not sb:
        return []

    out: list[Dict[str, Any]] = []
    try:
        query = (
            sb.table(_table())
            .select("*")
            .eq("guild_id", int(guild_id))
            .eq("owner_id", int(owner_id))
            .limit(int(limit))
        )
        if vc_channel_id and int(vc_channel_id) > 0:
            query = query.eq("vc_channel_id", int(vc_channel_id))
        res = query.execute()
        rows = getattr(res, "data", None) or []
        for raw in rows:
            if isinstance(raw, dict):
                out.append(_normalize_row(dict(raw)))
    except Exception as e:
        _log(
            f"db_list_for_owner failed guild={guild_id} owner={owner_id} "
            f"vc={int(vc_channel_id or 0)} err={repr(e)}"
        )
    return out


# ============================================================
# Public session helpers
# ============================================================

def get_session(token: str) -> Optional[Dict[str, Any]]:
    tok = _as_str(token)
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
    tok = _as_str(token)
    if not tok:
        return

    now_iso = _utc_iso()
    access_mins = int(access_minutes or _access_minutes())
    if access_mins <= 0:
        access_mins = _access_minutes()

    payload = _normalize_row(
        {
            "token": tok,
            "guild_id": int(guild_id),
            "ticket_channel_id": int(ticket_channel_id or 0) or None,
            "requester_id": int(requester_id or 0) or None,
            "owner_id": int(owner_id or 0) or int(requester_id or 0) or None,
            "vc_channel_id": int(vc_channel_id or 0) or None,
            "queue_channel_id": int(queue_channel_id or 0) or None,
            "queue_message_id": None,
            "status": "PENDING",
            "created_at": now_iso,
            "updated_at": now_iso,
            "accepted_at": None,
            "accepted_by": None,
            "ready_at": None,
            "started_at": None,
            "started_by": None,
            "completed_at": None,
            "completed_by": None,
            "canceled_at": None,
            "canceled_by": None,
            "expired_at": None,
            "restarted_at": None,
            "restarted_by": None,
            "access_minutes": access_mins,
            "revoke_at": _utc_iso(_utcnow() + timedelta(minutes=access_mins)),
            "last_watchdog_at": None,
            "meta": _initial_meta(meta),
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
    tok = _as_str(token)
    if not tok:
        return None

    row = get_session(tok)
    if row:
        patch: Dict[str, Any] = {}
        desired_queue_channel_id = int(queue_channel_id or _queue_channel_id() or 0) or None
        desired_vc_channel_id = int(vc_channel_id or 0) or None
        desired_ticket_channel_id = int(ticket_channel_id or 0) or None
        desired_owner_id = int(owner_id or 0) or int(requester_id or 0) or None
        desired_requester_id = int(requester_id or 0) or None
        desired_access_minutes = int(access_minutes or row.get("access_minutes") or _access_minutes())

        status = _normalize_status(row.get("status"))
        expired_or_terminal = _row_is_expired(row) or status in {"EXPIRED", "COMPLETED", "CANCELED"}
        if expired_or_terminal:
            now_iso = _utc_iso()
            refreshed_access_minutes = desired_access_minutes if desired_access_minutes > 0 else _access_minutes()

            refresh_meta = _merge_meta(
                row.get("meta"),
                {
                    "owner_confirmed": False,
                    "staff_confirmed": False,
                    "unlocked": False,
                    "last_action": "refresh_expired_session",
                    "last_action_at": now_iso,
                    "expired_refresh_from_status": status,
                    "expired_refresh_previous_revoke_at": row.get("revoke_at"),
                },
            )

            patch.update(
                {
                    "status": "PENDING",
                    "ticket_channel_id": desired_ticket_channel_id or row.get("ticket_channel_id"),
                    "requester_id": desired_requester_id or row.get("requester_id"),
                    "owner_id": desired_owner_id or row.get("owner_id"),
                    "vc_channel_id": desired_vc_channel_id or row.get("vc_channel_id"),
                    "queue_channel_id": desired_queue_channel_id or row.get("queue_channel_id"),
                    "queue_message_id": None,
                    "accepted_at": None,
                    "accepted_by": None,
                    "ready_at": None,
                    "started_at": None,
                    "started_by": None,
                    "completed_at": None,
                    "completed_by": None,
                    "canceled_at": None,
                    "canceled_by": None,
                    "expired_at": None,
                    "restarted_at": None,
                    "restarted_by": None,
                    "access_minutes": refreshed_access_minutes,
                    "revoke_at": _utc_iso(_utcnow() + timedelta(minutes=refreshed_access_minutes)),
                    "last_watchdog_at": None,
                    "meta": refresh_meta,
                }
            )

        if not row.get("queue_channel_id") and desired_queue_channel_id:
            patch["queue_channel_id"] = desired_queue_channel_id
        if not row.get("vc_channel_id") and desired_vc_channel_id:
            patch["vc_channel_id"] = desired_vc_channel_id
        if not row.get("ticket_channel_id") and desired_ticket_channel_id:
            patch["ticket_channel_id"] = desired_ticket_channel_id
        if not row.get("owner_id") and desired_owner_id:
            patch["owner_id"] = desired_owner_id
        if not row.get("requester_id") and desired_requester_id:
            patch["requester_id"] = desired_requester_id
        if int(row.get("access_minutes") or 0) <= 0 and desired_access_minutes > 0:
            patch["access_minutes"] = desired_access_minutes
            patch["revoke_at"] = _utc_iso(_utcnow() + timedelta(minutes=desired_access_minutes))
        if isinstance(meta, dict) and meta:
            patch["meta"] = _merge_meta(patch.get("meta", row.get("meta")), meta)

        if patch:
            updated = _update_local(tok, patch)
            if updated:
                _db_update(tok, updated)
                return updated
        return row

    create_session(
        token=tok,
        guild_id=int(guild_id),
        ticket_channel_id=int(ticket_channel_id or 0),
        requester_id=int(requester_id or 0),
        owner_id=int(owner_id or 0),
        vc_channel_id=int(vc_channel_id or 0),
        queue_channel_id=int(queue_channel_id or _queue_channel_id()),
        access_minutes=int(access_minutes or _access_minutes()),
        meta=meta,
    )
    return get_session(tok)


def _update_local(token: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tok = _as_str(token)
    if not tok:
        return None

    current = get_session(tok) or {"token": tok}
    merged = dict(current)

    for key, value in dict(patch or {}).items():
        if key == "meta":
            merged["meta"] = _merge_meta(current.get("meta"), value if isinstance(value, dict) else None)
        else:
            merged[key] = value

    merged["updated_at"] = _utc_iso()
    updated = _normalize_row(merged)
    _write_mem(updated)
    return updated


def set_queue_message(*, token: str, queue_message_id: int) -> None:
    row = _update_local(str(token), {"queue_message_id": int(queue_message_id)})
    if row:
        _db_update(str(token), {"queue_message_id": int(queue_message_id)})


def update_meta(
    *,
    token: str,
    patch: Dict[str, Any],
) -> None:
    current = get_session(token) or {}
    meta = _merge_meta(current.get("meta"), patch)
    row = _update_local(str(token), {"meta": meta})
    if row:
        _db_update(str(token), {"meta": meta})


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

    now_iso = _utc_iso()
    current_meta = _merge_meta(current.get("meta"))
    extension_count = int(current_meta.get("session_extended_count") or 0) + 1

    meta = _merge_meta(
        current_meta,
        {
            "session_extended_count": extension_count,
            "session_extended_reason": str(reason or "session still active"),
            "session_extended_at": now_iso,
            "last_action": "extend_expiry",
            "last_action_at": now_iso,
            "last_action_by": int(by_staff_id or 0) or None,
        },
    )

    revoke_at = _utc_iso(_utcnow() + timedelta(minutes=extension_minutes))
    row = _update_local(str(token), {"revoke_at": revoke_at, "meta": meta})
    if row:
        _db_update(
            str(token),
            {
                "revoke_at": revoke_at,
                "meta": meta,
            },
        )


def get_reusable_session(
    *,
    guild_id: int,
    owner_id: int,
    vc_channel_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    candidates: list[Dict[str, Any]] = []

    candidates.extend(
        _db_list_for_owner(
            guild_id=int(guild_id),
            owner_id=int(owner_id),
            vc_channel_id=int(vc_channel_id or 0) or None,
            limit=25,
        )
    )

    with _MEM_LOCK:
        mem_values = list(_MEM_SESSIONS.values())

    for raw in mem_values:
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
        tok = _as_str(row.get("token"))
        if tok:
            dedup[tok] = row

    active_rows = []
    for row in dedup.values():
        status = _normalize_status(row.get("status"))
        if status not in _ACTIVE_REUSABLE_STATUSES:
            continue
        if _row_is_expired(row):
            continue
        active_rows.append(row)

    active_rows.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return active_rows[0] if active_rows else None


def session_is_unlockable(
    *,
    token: str,
    expected_guild_id: int = 0,
    expected_staff_id: int = 0,
) -> tuple[bool, str]:
    row = get_session(token)
    if not row:
        return False, "Session not found."

    if _row_is_expired(row):
        transition(token=token, new_status="EXPIRED", staff_id=expected_staff_id or 0)
        return False, "Session expired."

    status = _normalize_status(row.get("status"))
    if status not in _ALLOWED_UNLOCK_STATUSES:
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
    tok = _as_str(token)
    if not tok:
        return

    current = get_session(tok) or {"token": tok}
    now = _utc_iso()
    status = _normalize_status(new_status)
    patch: Dict[str, Any] = {"status": status}

    meta = _merge_meta(current.get("meta"))

    if status == "STAFF_ACCEPTED":
        patch["accepted_at"] = current.get("accepted_at") or now
        patch["accepted_by"] = int(staff_id or current.get("accepted_by") or 0) or None
        meta = _merge_meta(
            meta,
            {
                "staff_confirmed": True,
                "last_action": "staff_accept",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "OWNER_CONFIRMED":
        meta = _merge_meta(
            meta,
            {
                "owner_confirmed": True,
                "last_action": "owner_confirm",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "READY":
        patch["ready_at"] = current.get("ready_at") or now
        meta = _merge_meta(
            meta,
            {
                "unlocked": True,
                "last_action": "unlock",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status in {"IN_VC", "STARTED"}:
        patch["started_at"] = current.get("started_at") or now
        meta = _merge_meta(
            meta,
            {
                "unlocked": True,
                "started_by": int(staff_id or 0) or None,
                "last_action": "start",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )
        if not current.get("revoke_at"):
            patch["revoke_at"] = _utc_iso(_utcnow() + timedelta(minutes=int(current.get("access_minutes") or _access_minutes())))

    elif status == "COMPLETED":
        patch["completed_at"] = current.get("completed_at") or now
        meta = _merge_meta(
            meta,
            {
                "unlocked": False,
                "completed_by": int(staff_id or 0) or None,
                "last_action": "complete",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "CANCELED":
        patch["canceled_at"] = current.get("canceled_at") or now
        patch["canceled_by"] = int(staff_id or current.get("canceled_by") or 0) or None
        meta = _merge_meta(
            meta,
            {
                "unlocked": False,
                "last_action": "cancel",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "EXPIRED":
        meta = _merge_meta(
            meta,
            {
                "unlocked": False,
                "expired_at": now,
                "last_action": "expire",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "TAKEN_OVER":
        patch["accepted_at"] = now
        patch["accepted_by"] = int(staff_id or 0) or None
        meta = _merge_meta(
            meta,
            {
                "staff_confirmed": True,
                "unlocked": False,
                "last_action": "takeover",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    elif status == "RESTARTED":
        meta = _merge_meta(
            meta,
            {
                "owner_confirmed": False,
                "staff_confirmed": True,
                "unlocked": False,
                "restarted_by": int(staff_id or 0) or None,
                "restarted_at": now,
                "last_action": "restart",
                "last_action_at": now,
                "last_action_by": int(staff_id or 0) or None,
            },
        )

    if extra is not None:
        meta = _merge_meta(meta, extra)

    patch["meta"] = meta

    row = _update_local(tok, patch)
    if row:
        _db_update(tok, patch)


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
            "last_action_at": _utc_iso(),
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
    current_meta = _merge_meta(current.get("meta"))
    meta = _merge_meta(
        current_meta,
        {
            "owner_confirmed": True,
            "last_action": "owner_confirm",
            "last_action_at": _utc_iso(),
            "last_action_by": int(owner_id),
        },
    )
    new_status = "READY" if bool(current_meta.get("staff_confirmed")) else "OWNER_CONFIRMED"
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
            "last_action_at": _utc_iso(),
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
            "unlocked": True,
            "started_by": int(by_staff_id),
            "last_action": "start",
            "last_action_at": _utc_iso(),
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
            "completed_by": int(by_staff_id),
            "last_action": "complete",
            "last_action_at": _utc_iso(),
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
            "last_action_at": _utc_iso(),
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
            "restarted_by": int(by_staff_id),
            "restarted_at": _utc_iso(),
            "last_action": "restart",
            "last_action_at": _utc_iso(),
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
            "last_action_at": _utc_iso(),
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
            "last_action_at": _utc_iso(),
            "last_action_by": int(by_staff_id or 0) or None,
        },
    )
    update_meta(token=token, patch=meta)


def touch_watchdog(token: str) -> None:
    row = _update_local(str(token), {"last_watchdog_at": _utc_iso()})
    if row:
        _db_update(
            str(token),
            {
                "last_watchdog_at": row["last_watchdog_at"],
            },
        )


# ============================================================
# Async wrappers used by higher-level VC flow
# ============================================================

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
    except Exception as e:
        _log(f"start_session ensure_session failed token={token} err={repr(e)}")

    try:
        mark_started(token=str(token), by_staff_id=int(staff_id))
    except Exception as e:
        _log(f"start_session mark_started failed token={token} err={repr(e)}")


async def end_session(
    *,
    guild_id: int,
    token: str,
    status: str,
    staff_id: int,
) -> None:
    try:
        norm = _normalize_status(status or "COMPLETED")
        if norm == "COMPLETED":
            mark_completed(token=str(token), by_staff_id=int(staff_id))
        elif norm == "CANCELED":
            mark_canceled(token=str(token), by_staff_id=int(staff_id))
        elif norm == "EXPIRED":
            transition(token=str(token), new_status="EXPIRED", staff_id=int(staff_id))
        else:
            transition(token=str(token), new_status=norm, staff_id=int(staff_id))
    except Exception as e:
        _log(f"end_session failed token={token} status={status} err={repr(e)}")


__all__ = [
    "sb_enabled",
    "get_session",
    "create_session",
    "ensure_session",
    "set_queue_message",
    "update_meta",
    "extend_expiry",
    "get_reusable_session",
    "session_is_unlockable",
    "transition",
    "set_staff_accepted",
    "set_owner_confirmed",
    "mark_unlocked",
    "mark_started",
    "mark_completed",
    "mark_canceled",
    "restart_session",
    "takeover_session",
    "clear_unlock",
    "touch_watchdog",
    "start_session",
    "end_session",
]
