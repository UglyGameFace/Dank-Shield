from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..globals import get_supabase, now_utc, reset_supabase


# ============================================================
# tickets_new/verification_context_service.py
# ------------------------------------------------------------
# Purpose:
# - reusable verification intelligence layer
# - centralize reads from:
#     - verification_flags
#     - vc_verify_sessions
#     - verification_tokens
# - return normalized verification context snapshots
# - keep ticket workspace / dashboard / verification flows
#   from needing raw table knowledge
# ============================================================

VERIFICATION_FLAGS_TABLE = "verification_flags"
VC_VERIFY_SESSIONS_TABLE = "vc_verify_sessions"
VERIFICATION_TOKENS_TABLE = "verification_tokens"


# ============================================================
# Small helpers
# ============================================================

def _ctx_debug(msg: str) -> None:
    try:
        print(f"🛡️ verification_context_service {msg}")
    except Exception:
        pass


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _clean_text(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def _as_str_id(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return None
        return text
    except Exception:
        return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _boolish(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(value)
    except Exception:
        return default


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _safe_meta(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _sort_unique_texts(values: Sequence[Any], *, limit: int = 100) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []

    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break

    return out


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _normalize_ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return _utc_iso(value)
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def _latest_ts(*values: Any) -> Optional[str]:
    best_value: Optional[str] = None
    best_dt: Optional[datetime] = None

    for value in values:
        text = _normalize_ts(value)
        if not text:
            continue

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        except Exception:
            continue

        if best_dt is None or parsed > best_dt:
            best_dt = parsed
            best_value = parsed.isoformat()

    return best_value


# ============================================================
# Retry / DB execution helpers
# ============================================================

def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor, max_attempts: int = 5):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise

    raise last_error


async def _run_db_op(op_name: str, executor, max_attempts: int = 5):
    return await asyncio.to_thread(_execute_db_op, op_name, executor, max_attempts)


# ============================================================
# Row normalizers
# ============================================================

def _normalize_verification_flag_row(row: Dict[str, Any]) -> Dict[str, Any]:
    reasons = _safe_list(row.get("reasons"))
    normalized_reasons = _sort_unique_texts(reasons, limit=50)

    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _as_str_id(row.get("guild_id")),
        "user_id": _as_str_id(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "score": _as_int(row.get("score"), 0),
        "reasons": normalized_reasons,
        "flagged": _boolish(row.get("flagged"), False),
        "created_at": _normalize_ts(row.get("created_at")),
        "raw": dict(row),
    }


def _normalize_vc_session_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "token": _clean_text(row.get("token")),
        "guild_id": _as_str_id(row.get("guild_id")),
        "ticket_channel_id": _as_str_id(row.get("ticket_channel_id")),
        "requester_id": _as_str_id(row.get("requester_id")),
        "owner_id": _as_str_id(row.get("owner_id")),
        "vc_channel_id": _as_str_id(row.get("vc_channel_id")),
        "queue_channel_id": _as_str_id(row.get("queue_channel_id")),
        "queue_message_id": _as_str_id(row.get("queue_message_id")),
        "status": _clean_text(row.get("status")) or "UNKNOWN",
        "created_at": _normalize_ts(row.get("created_at")),
        "accepted_at": _normalize_ts(row.get("accepted_at")),
        "accepted_by": _as_str_id(row.get("accepted_by")),
        "started_at": _normalize_ts(row.get("started_at")),
        "completed_at": _normalize_ts(row.get("completed_at")),
        "canceled_at": _normalize_ts(row.get("canceled_at")),
        "canceled_by": _as_str_id(row.get("canceled_by")),
        "access_minutes": _as_int(row.get("access_minutes"), 0),
        "revoke_at": _normalize_ts(row.get("revoke_at")),
        "last_watchdog_at": _normalize_ts(row.get("last_watchdog_at")),
        "meta": _safe_meta(row.get("meta")),
        "raw": dict(row),
    }


def _normalize_verification_token_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "token": _clean_text(row.get("token")),
        "webhook_url": _clean_text(row.get("webhook_url")),
        "expires_at": _normalize_ts(row.get("expires_at")),
        "used": _boolish(row.get("used"), False),
        "created_at": _normalize_ts(row.get("created_at")),
        "updated_at": _normalize_ts(row.get("updated_at")),
        "guild_id": _as_str_id(row.get("guild_id")),
        "channel_id": _as_str_id(row.get("channel_id")),
        "requester_id": _as_str_id(row.get("requester_id")),
        "requester_display_name": _clean_text(row.get("requester_display_name")),
        "requester_username": _clean_text(row.get("requester_username")),
        "requester_avatar_url": _clean_text(row.get("requester_avatar_url")),
        "requester_role_ids": [str(x) for x in _safe_list(row.get("requester_role_ids")) if _clean_text(x)],
        "requester_role_names": [str(x) for x in _safe_list(row.get("requester_role_names")) if _clean_text(x)],
        "decision": _clean_text(row.get("decision")) or "PENDING",
        "decided_by": _as_str_id(row.get("decided_by")),
        "decided_at": _normalize_ts(row.get("decided_at")),
        "decided_by_display_name": _clean_text(row.get("decided_by_display_name")),
        "decided_by_username": _clean_text(row.get("decided_by_username")),
        "decided_by_avatar_url": _clean_text(row.get("decided_by_avatar_url")),
        "user_id": _as_str_id(row.get("user_id")),
        "approved_user_id": _as_str_id(row.get("approved_user_id")),
        "submitted": _boolish(row.get("submitted"), False),
        "submitted_at": _normalize_ts(row.get("submitted_at")),
        "ai_status": _clean_text(row.get("ai_status")),
        "status": _clean_text(row.get("status")) or "pending",
        "owner_display_name": _clean_text(row.get("owner_display_name")),
        "owner_username": _clean_text(row.get("owner_username")),
        "owner_tag": _clean_text(row.get("owner_tag")),
        "expected_role_state": _clean_text(row.get("expected_role_state")) or "unknown",
        "actual_role_state": _clean_text(row.get("actual_role_state")) or "unknown",
        "role_sync_ok": _boolish(row.get("role_sync_ok"), False),
        "role_sync_reason": _clean_text(row.get("role_sync_reason")),
        "raw": dict(row),
    }


# ============================================================
# Low-level readers
# ============================================================

async def list_verification_flag_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 20), 200))

    try:
        def _read_sync():
            return (
                sb.table(VERIFICATION_FLAGS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .eq("user_id", uid)
                .order("created_at", desc=True)
                .limit(max_limit)
                .execute()
            )

        resp = await _run_db_op("list verification flag rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_verification_flag_row(row))
        return out
    except Exception as e:
        print(f"⚠️ verification_context_service.list_verification_flag_rows failed: {repr(e)}")
        return []


async def get_latest_verification_flag_row(
    *,
    guild_id: int | str,
    user_id: int | str,
) -> Optional[Dict[str, Any]]:
    rows = await list_verification_flag_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=1,
    )
    return rows[0] if rows else None


async def list_vc_session_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 20), 200))

    try:
        def _read_sync():
            query = (
                sb.table(VC_VERIFY_SESSIONS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .or_(f"owner_id.eq.{uid},requester_id.eq.{uid}")
                .order("created_at", desc=True)
                .limit(max_limit)
            )
            return query.execute()

        resp = await _run_db_op("list vc session rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_vc_session_row(row))
        return out
    except Exception as e:
        print(f"⚠️ verification_context_service.list_vc_session_rows failed: {repr(e)}")
        return []


async def get_latest_vc_session_row(
    *,
    guild_id: int | str,
    user_id: int | str,
) -> Optional[Dict[str, Any]]:
    rows = await list_vc_session_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=1,
    )
    return rows[0] if rows else None


async def list_verification_token_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 25), 200))

    try:
        def _read_sync():
            query = (
                sb.table(VERIFICATION_TOKENS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .or_(
                    f"requester_id.eq.{uid},user_id.eq.{uid},approved_user_id.eq.{uid}"
                )
                .order("created_at", desc=True)
                .limit(max_limit)
            )
            return query.execute()

        resp = await _run_db_op("list verification token rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_verification_token_row(row))
        return out
    except Exception as e:
        print(f"⚠️ verification_context_service.list_verification_token_rows failed: {repr(e)}")
        return []


async def get_latest_verification_token_row(
    *,
    guild_id: int | str,
    user_id: int | str,
) -> Optional[Dict[str, Any]]:
    rows = await list_verification_token_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=1,
    )
    return rows[0] if rows else None


# ============================================================
# Derived summaries
# ============================================================

def _build_flags_summary(flag_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest_flag = flag_rows[0] if flag_rows else None
    flagged_rows = [row for row in flag_rows if _boolish(row.get("flagged"), False)]
    scores = [_as_int(row.get("score"), 0) for row in flag_rows]
    max_score = max(scores) if scores else 0

    all_reasons: List[str] = []
    for row in flag_rows:
        all_reasons.extend(_safe_list(row.get("reasons")))

    return {
        "count": len(flag_rows),
        "flagged_count": len(flagged_rows),
        "has_any_flagged": bool(flagged_rows),
        "latest_flag_at": latest_flag.get("created_at") if isinstance(latest_flag, dict) else None,
        "latest_flag_score": _as_int(latest_flag.get("score"), 0) if isinstance(latest_flag, dict) else 0,
        "latest_flagged": _boolish(latest_flag.get("flagged"), False) if isinstance(latest_flag, dict) else False,
        "latest_reasons": _sort_unique_texts(
            latest_flag.get("reasons", []) if isinstance(latest_flag, dict) else [],
            limit=25,
        ),
        "all_reasons": _sort_unique_texts(all_reasons, limit=100),
        "max_score": max_score,
    }


def _is_vc_status_active(status: str) -> bool:
    value = str(status or "").strip().upper()
    return value in {
        "PENDING",
        "ACCEPTED",
        "STAFF_ACCEPTED",
        "READY",
        "IN_VC",
        "STARTED",
        "TAKEN_OVER",
        "RESTARTED",
        "UPLOAD_REQUESTED",
    }


def _build_vc_summary(vc_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest_vc = vc_rows[0] if vc_rows else None

    active_rows = [row for row in vc_rows if _is_vc_status_active(str(row.get("status") or ""))]
    completed_rows = [row for row in vc_rows if _normalize_ts(row.get("completed_at"))]
    canceled_rows = [row for row in vc_rows if _normalize_ts(row.get("canceled_at"))]

    latest_activity_at = None
    for row in vc_rows:
        latest_activity_at = _latest_ts(
            latest_activity_at,
            row.get("completed_at"),
            row.get("started_at"),
            row.get("accepted_at"),
            row.get("canceled_at"),
            row.get("created_at"),
        )

    active_token = None
    if active_rows:
        active_token = _clean_text(active_rows[0].get("token"))

    latest_status = _clean_text(latest_vc.get("status")) if isinstance(latest_vc, dict) else None

    return {
        "count": len(vc_rows),
        "active_count": len(active_rows),
        "completed_count": len(completed_rows),
        "canceled_count": len(canceled_rows),
        "has_active": bool(active_rows),
        "active_token": active_token,
        "latest_status": latest_status,
        "latest_vc_at": latest_activity_at,
        "latest_created_at": latest_vc.get("created_at") if isinstance(latest_vc, dict) else None,
        "latest_started_at": latest_vc.get("started_at") if isinstance(latest_vc, dict) else None,
        "latest_completed_at": latest_vc.get("completed_at") if isinstance(latest_vc, dict) else None,
        "latest_canceled_at": latest_vc.get("canceled_at") if isinstance(latest_vc, dict) else None,
        "latest_ticket_channel_id": _clean_text(latest_vc.get("ticket_channel_id")) if isinstance(latest_vc, dict) else None,
        "latest_vc_channel_id": _clean_text(latest_vc.get("vc_channel_id")) if isinstance(latest_vc, dict) else None,
        "latest_queue_channel_id": _clean_text(latest_vc.get("queue_channel_id")) if isinstance(latest_vc, dict) else None,
    }


def _build_token_summary(token_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest_token = token_rows[0] if token_rows else None

    status_counts: Dict[str, int] = {}
    decision_counts: Dict[str, int] = {}

    pending_count = 0
    submitted_count = 0
    approved_count = 0
    denied_count = 0
    expired_count = 0
    used_count = 0

    latest_submission_at = None
    latest_decision_at = None
    latest_expiry_at = None

    for row in token_rows:
        status = str(row.get("status") or "unknown").strip().lower()
        decision = str(row.get("decision") or "PENDING").strip().upper()

        status_counts[status] = status_counts.get(status, 0) + 1
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

        if status == "pending":
            pending_count += 1
        elif status == "submitted":
            submitted_count += 1
        elif status == "approved":
            approved_count += 1
        elif status == "denied":
            denied_count += 1
        elif status == "expired":
            expired_count += 1
        elif status == "used":
            used_count += 1

        latest_submission_at = _latest_ts(latest_submission_at, row.get("submitted_at"))
        latest_decision_at = _latest_ts(latest_decision_at, row.get("decided_at"))
        latest_expiry_at = _latest_ts(latest_expiry_at, row.get("expires_at"))

    return {
        "count": len(token_rows),
        "pending_count": pending_count,
        "submitted_count": submitted_count,
        "approved_count": approved_count,
        "denied_count": denied_count,
        "expired_count": expired_count,
        "used_count": used_count,
        "latest_status": _clean_text(latest_token.get("status")) if isinstance(latest_token, dict) else None,
        "latest_decision": _clean_text(latest_token.get("decision")) if isinstance(latest_token, dict) else None,
        "latest_created_at": latest_token.get("created_at") if isinstance(latest_token, dict) else None,
        "latest_updated_at": latest_token.get("updated_at") if isinstance(latest_token, dict) else None,
        "latest_submitted_at": latest_submission_at,
        "latest_decided_at": latest_decision_at,
        "latest_expires_at": latest_expiry_at,
        "latest_channel_id": _clean_text(latest_token.get("channel_id")) if isinstance(latest_token, dict) else None,
        "latest_requester_id": _clean_text(latest_token.get("requester_id")) if isinstance(latest_token, dict) else None,
        "latest_approved_user_id": _clean_text(latest_token.get("approved_user_id")) if isinstance(latest_token, dict) else None,
        "latest_role_sync_ok": _boolish(latest_token.get("role_sync_ok"), False) if isinstance(latest_token, dict) else False,
        "latest_role_sync_reason": _clean_text(latest_token.get("role_sync_reason")) if isinstance(latest_token, dict) else None,
        "status_counts": status_counts,
        "decision_counts": decision_counts,
    }


def _derive_overall_verification_status(
    flags_summary: Dict[str, Any],
    vc_summary: Dict[str, Any],
    token_summary: Dict[str, Any],
) -> str:
    latest_token_status = str(token_summary.get("latest_status") or "").strip().lower()
    latest_token_decision = str(token_summary.get("latest_decision") or "").strip().upper()
    latest_vc_status = str(vc_summary.get("latest_status") or "").strip().upper()

    if token_summary.get("approved_count", 0) > 0 or latest_token_status == "approved":
        return "Verified"

    if latest_token_decision.startswith("DENIED") or token_summary.get("denied_count", 0) > 0:
        return "Denied"

    if flags_summary.get("has_any_flagged") or flags_summary.get("flagged_count", 0) > 0:
        return "Needs Review"

    if vc_summary.get("has_active") or _is_vc_status_active(latest_vc_status):
        return "VC In Progress"

    if latest_token_status in {"submitted", "pending", "used", "resubmit"}:
        return "Pending Verification"

    if token_summary.get("submitted_count", 0) > 0 or token_summary.get("pending_count", 0) > 0:
        return "Pending Verification"

    if vc_summary.get("count", 0) > 0 and vc_summary.get("completed_count", 0) > 0:
        return "VC Completed"

    if token_summary.get("count", 0) > 0:
        return "Verification History Found"

    return "No Verification Context Yet"


def _build_dashboard_block(
    flags_summary: Dict[str, Any],
    vc_summary: Dict[str, Any],
    token_summary: Dict[str, Any],
    overall_status: str,
) -> Dict[str, Any]:
    return {
        "status": overall_status,
        "flag_count": flags_summary.get("count", 0),
        "flagged_count": flags_summary.get("flagged_count", 0),
        "latest_flag_at": flags_summary.get("latest_flag_at"),
        "vc_latest_status": vc_summary.get("latest_status"),
        "vc_request_count": vc_summary.get("count", 0),
        "vc_completed_count": vc_summary.get("completed_count", 0),
        "token_latest_status": token_summary.get("latest_status"),
        "token_latest_decision": token_summary.get("latest_decision"),
        "token_submitted_count": token_summary.get("submitted_count", 0),
        "token_pending_count": token_summary.get("pending_count", 0),
        "token_approved_count": token_summary.get("approved_count", 0),
        "token_denied_count": token_summary.get("denied_count", 0),
    }


# ============================================================
# Public higher-level helpers
# ============================================================

async def get_verification_flags_summary(
    *,
    guild_id: int | str,
    user_id: int | str,
    flag_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    rows = list(flag_rows) if flag_rows is not None else await list_verification_flag_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=20,
    )
    return _build_flags_summary(rows)


async def get_vc_verification_summary(
    *,
    guild_id: int | str,
    user_id: int | str,
    vc_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    rows = list(vc_rows) if vc_rows is not None else await list_vc_session_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=20,
    )
    return _build_vc_summary(rows)


async def get_verification_token_summary(
    *,
    guild_id: int | str,
    user_id: int | str,
    token_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    rows = list(token_rows) if token_rows is not None else await list_verification_token_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=25,
    )
    return _build_token_summary(rows)


async def get_verification_context_snapshot(
    *,
    guild_id: int | str,
    user_id: int | str,
    flag_limit: int = 20,
    vc_limit: int = 20,
    token_limit: int = 25,
    include_flag_rows: bool = True,
    include_vc_rows: bool = True,
    include_token_rows: bool = True,
) -> Dict[str, Any]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)

    if not gid or not uid:
        return {
            "ok": False,
            "error": "guild_id and user_id are required.",
            "guild_id": gid,
            "user_id": uid,
        }

    flag_rows: List[Dict[str, Any]] = []
    vc_rows: List[Dict[str, Any]] = []
    token_rows: List[Dict[str, Any]] = []

    if include_flag_rows:
        flag_rows = await list_verification_flag_rows(
            guild_id=gid,
            user_id=uid,
            limit=flag_limit,
        )

    if include_vc_rows:
        vc_rows = await list_vc_session_rows(
            guild_id=gid,
            user_id=uid,
            limit=vc_limit,
        )

    if include_token_rows:
        token_rows = await list_verification_token_rows(
            guild_id=gid,
            user_id=uid,
            limit=token_limit,
        )

    flags_summary = _build_flags_summary(flag_rows)
    vc_summary = _build_vc_summary(vc_rows)
    token_summary = _build_token_summary(token_rows)

    overall_status = _derive_overall_verification_status(
        flags_summary=flags_summary,
        vc_summary=vc_summary,
        token_summary=token_summary,
    )

    dashboard = _build_dashboard_block(
        flags_summary=flags_summary,
        vc_summary=vc_summary,
        token_summary=token_summary,
        overall_status=overall_status,
    )

    latest_flag = flag_rows[0] if flag_rows else None
    latest_vc = vc_rows[0] if vc_rows else None
    latest_token = token_rows[0] if token_rows else None

    snapshot: Dict[str, Any] = {
        "ok": True,
        "guild_id": gid,
        "user_id": uid,
        "generated_at": _now_iso(),
        "status": overall_status,
        "flags_summary": flags_summary,
        "vc_summary": vc_summary,
        "token_summary": token_summary,
        "latest_flag": latest_flag,
        "latest_vc_session": latest_vc,
        "latest_token": latest_token,
        "recent_flags": flag_rows if include_flag_rows else [],
        "recent_vc_sessions": vc_rows if include_vc_rows else [],
        "recent_tokens": token_rows if include_token_rows else [],
        "dashboard": dashboard,
    }

    _ctx_debug(
        f"snapshot guild={gid} user={uid} "
        f"flags={len(flag_rows)} vc={len(vc_rows)} tokens={len(token_rows)} "
        f"status={overall_status!r}"
    )

    return snapshot


# ============================================================
# Diagnostics
# ============================================================

async def verification_context_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "supabase": False,
        "tables": {
            "verification_flags": VERIFICATION_FLAGS_TABLE,
            "vc_verify_sessions": VC_VERIFY_SESSIONS_TABLE,
            "verification_tokens": VERIFICATION_TOKENS_TABLE,
        },
        "error": None,
    }

    sb = _sb()
    if sb is None:
        out["error"] = "supabase unavailable"
        return out

    out["supabase"] = True

    try:
        def _probe_flags():
            return sb.table(VERIFICATION_FLAGS_TABLE).select("*").limit(1).execute()

        def _probe_vc():
            return sb.table(VC_VERIFY_SESSIONS_TABLE).select("*").limit(1).execute()

        def _probe_tokens():
            return sb.table(VERIFICATION_TOKENS_TABLE).select("*").limit(1).execute()

        await _run_db_op("verification context healthcheck flags", _probe_flags)
        await _run_db_op("verification context healthcheck vc", _probe_vc)
        await _run_db_op("verification context healthcheck tokens", _probe_tokens)

        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "VERIFICATION_FLAGS_TABLE",
    "VC_VERIFY_SESSIONS_TABLE",
    "VERIFICATION_TOKENS_TABLE",
    "list_verification_flag_rows",
    "get_latest_verification_flag_row",
    "list_vc_session_rows",
    "get_latest_vc_session_row",
    "list_verification_token_rows",
    "get_latest_verification_token_row",
    "get_verification_flags_summary",
    "get_vc_verification_summary",
    "get_verification_token_summary",
    "get_verification_context_snapshot",
    "verification_context_healthcheck",
]