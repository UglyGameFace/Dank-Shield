from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..globals import get_supabase, now_utc, reset_supabase
from .member_context_service import get_member_context_snapshot
from .verification_context_service import get_verification_context_snapshot

try:
    from .repository import (
        get_ticket_by_id,
        get_ticket_by_any_channel_id,
        list_internal_notes,
        list_ticket_messages,
        list_ticket_activity_events,
        get_latest_ticket_activity,
    )
except Exception:
    async def get_ticket_by_id(*args, **kwargs):  # type: ignore
        return None

    async def get_ticket_by_any_channel_id(*args, **kwargs):  # type: ignore
        return None

    async def list_internal_notes(*args, **kwargs):  # type: ignore
        return []

    async def list_ticket_messages(*args, **kwargs):  # type: ignore
        return []

    async def list_ticket_activity_events(*args, **kwargs):  # type: ignore
        return []

    async def get_latest_ticket_activity(*args, **kwargs):  # type: ignore
        return None


# ============================================================
# tickets_new/workspace_service.py
# ------------------------------------------------------------
# Purpose:
# - unified ticket workspace intelligence layer
# - centralize ticket + owner + verification + activity + SLA
# - provide one reusable ticket snapshot for:
#     - staff ticket detail panels
#     - staff queue cards
#     - dashboard ticket payloads
#     - future moderation / verification review screens
# ============================================================

TICKETS_TABLE = "tickets"
TICKET_CATEGORIES_TABLE = "ticket_categories"
GUILD_MEMBERS_TABLE = "guild_members"
STAFF_METRICS_TABLE = "staff_metrics"


# ============================================================
# Small helpers
# ============================================================

def _ws_debug(msg: str) -> None:
    try:
        print(f"🧭 workspace_service {msg}")
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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(str(value).strip())
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


def _normalize_ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            text = str(value).strip()
            return text or None
        except Exception:
            return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip().replace("Z", "+00:00")
            if not text:
                return None
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _latest_ts(*values: Any) -> Optional[str]:
    best: Optional[datetime] = None
    best_text: Optional[str] = None

    for value in values:
        parsed = _parse_ts(value)
        if parsed is None:
            continue
        if best is None or parsed > best:
            best = parsed
            best_text = parsed.isoformat()

    return best_text


def _minutes_between(start_value: Any, end_value: Any = None) -> Optional[int]:
    start = _parse_ts(start_value)
    if start is None:
        return None

    end = _parse_ts(end_value) if end_value is not None else now_utc()
    if end is None:
        return None

    try:
        delta = end - start
        return max(0, int(delta.total_seconds() // 60))
    except Exception:
        return None


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower() or "unknown"


def _normalize_priority(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high", "urgent"}:
        return text
    return "medium"


def _priority_rank(priority: str) -> int:
    mapping = {
        "urgent": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }
    return mapping.get(str(priority or "").strip().lower(), 0)


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
# Normalizers
# ============================================================

def _normalize_ticket_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _clean_text(row.get("guild_id")),
        "user_id": _clean_text(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "title": _clean_text(row.get("title")),
        "category": _clean_text(row.get("category")),
        "status": _clean_text(row.get("status")) or "unknown",
        "priority": _normalize_priority(row.get("priority")),
        "claimed_by": _clean_text(row.get("claimed_by")),
        "assigned_to": _clean_text(row.get("assigned_to")),
        "closed_by": _clean_text(row.get("closed_by")),
        "closed_reason": _clean_text(row.get("closed_reason")),
        "initial_message": _clean_text(row.get("initial_message")),
        "source": _clean_text(row.get("source")),
        "channel_id": _clean_text(row.get("channel_id")),
        "channel_name": _clean_text(row.get("channel_name")),
        "discord_thread_id": _clean_text(row.get("discord_thread_id")),
        "transcript_url": _clean_text(row.get("transcript_url")),
        "transcript_message_id": _clean_text(row.get("transcript_message_id")),
        "transcript_channel_id": _clean_text(row.get("transcript_channel_id")),
        "ticket_number": row.get("ticket_number"),
        "is_ghost": _boolish(row.get("is_ghost"), False),
        "created_at": _normalize_ts(row.get("created_at")),
        "updated_at": _normalize_ts(row.get("updated_at")),
        "closed_at": _normalize_ts(row.get("closed_at")),
        "reopened_at": _normalize_ts(row.get("reopened_at")),
        "deleted_at": _normalize_ts(row.get("deleted_at")),
        "deleted_by": _clean_text(row.get("deleted_by")),
        "sla_deadline": _normalize_ts(row.get("sla_deadline")),
        "category_id": _clean_text(row.get("category_id")),
        "category_override": _boolish(row.get("category_override"), False),
        "category_set_by": _clean_text(row.get("category_set_by")),
        "category_set_at": _normalize_ts(row.get("category_set_at")),
        "matched_category_id": _clean_text(row.get("matched_category_id")),
        "matched_category_name": _clean_text(row.get("matched_category_name")),
        "matched_category_slug": _clean_text(row.get("matched_category_slug")),
        "matched_intake_type": _clean_text(row.get("matched_intake_type")),
        "matched_category_reason": _clean_text(row.get("matched_category_reason")),
        "matched_category_score": _as_int(row.get("matched_category_score"), 0),
        "ai_category_confidence": _as_float(row.get("ai_category_confidence"), 0.0),
        "mod_suggestion": _clean_text(row.get("mod_suggestion")),
        "mod_suggestion_confidence": _as_float(row.get("mod_suggestion_confidence"), 0.0),
        "raw": dict(row),
    }


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _clean_text(row.get("guild_id")),
        "name": _clean_text(row.get("name")),
        "slug": _clean_text(row.get("slug")),
        "color": _clean_text(row.get("color")) or "#45d483",
        "description": _clean_text(row.get("description")),
        "intake_type": _clean_text(row.get("intake_type")) or "general",
        "button_label": _clean_text(row.get("button_label")),
        "sort_order": row.get("sort_order"),
        "is_default": _boolish(row.get("is_default"), False),
        "staff_role_ids": [str(x) for x in _safe_list(row.get("staff_role_ids")) if _clean_text(x)],
        "staff_role_names": [str(x) for x in _safe_list(row.get("staff_role_names")) if _clean_text(x)],
        "match_keywords": _sort_unique_texts(_safe_list(row.get("match_keywords")), limit=50),
        "raw": dict(row),
    }


def _normalize_guild_member_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": _clean_text(row.get("guild_id")),
        "user_id": _clean_text(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "display_name": _clean_text(row.get("display_name")),
        "nickname": _clean_text(row.get("nickname")),
        "avatar_url": _clean_text(row.get("avatar_url")),
        "role_names": [str(x) for x in _safe_list(row.get("role_names")) if _clean_text(x)],
        "role_ids": [str(x) for x in _safe_list(row.get("role_ids")) if _clean_text(x)],
        "has_staff_role": _boolish(row.get("has_staff_role"), False),
        "has_verified_role": _boolish(row.get("has_verified_role"), False),
        "has_unverified": _boolish(row.get("has_unverified"), False),
        "role_state": _clean_text(row.get("role_state")) or "unknown",
        "role_state_reason": _clean_text(row.get("role_state_reason")),
        "top_role": _clean_text(row.get("top_role")),
        "joined_at": _normalize_ts(row.get("joined_at")),
        "raw": dict(row),
    }


def _normalize_staff_metrics_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _clean_text(row.get("guild_id")),
        "staff_id": _clean_text(row.get("staff_id")),
        "staff_name": _clean_text(row.get("staff_name")),
        "tickets_handled": _as_int(row.get("tickets_handled"), 0),
        "approvals": _as_int(row.get("approvals"), 0),
        "denials": _as_int(row.get("denials"), 0),
        "avg_response_minutes": _as_int(row.get("avg_response_minutes"), 0),
        "last_active": _normalize_ts(row.get("last_active")),
        "raw": dict(row),
    }


# ============================================================
# Low-level readers
# ============================================================

async def _get_ticket_row(
    *,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if isinstance(ticket_row, dict):
        return _normalize_ticket_row(ticket_row)

    if _clean_text(ticket_id):
        row = await get_ticket_by_id(ticket_id)
        if isinstance(row, dict):
            return _normalize_ticket_row(row)

    if _clean_text(channel_id):
        row = await get_ticket_by_any_channel_id(channel_id)
        if isinstance(row, dict):
            return _normalize_ticket_row(row)

    return None


async def _get_category_row_for_ticket(ticket: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    guild_id = _clean_text(ticket.get("guild_id"))
    category_id = _clean_text(ticket.get("matched_category_id") or ticket.get("category_id"))
    category_slug = _clean_text(ticket.get("matched_category_slug"))
    raw_category = _clean_text(ticket.get("category"))

    if not guild_id:
        return None

    try:
        def _read_sync():
            query = sb.table(TICKET_CATEGORIES_TABLE).select("*").eq("guild_id", guild_id)

            if category_id:
                return query.eq("id", category_id).limit(1).execute()

            if category_slug:
                return query.eq("slug", category_slug).limit(1).execute()

            if raw_category:
                return query.eq("slug", raw_category).limit(1).execute()

            return None

        resp = await _run_db_op("get category row for ticket", _read_sync)
        rows = getattr(resp, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return _normalize_category_row(rows[0])
    except Exception as e:
        print(f"⚠️ workspace_service._get_category_row_for_ticket failed: {repr(e)}")

    return None


async def _get_guild_member_rows_by_ids(
    *,
    guild_id: int | str,
    user_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    clean_ids = [str(x).strip() for x in user_ids if _clean_text(x)]
    if not gid or not clean_ids:
        return {}

    sb = _sb()
    if sb is None:
        return {}

    try:
        def _read_sync():
            query = sb.table(GUILD_MEMBERS_TABLE).select("*").eq("guild_id", gid)
            if len(clean_ids) == 1:
                return query.eq("user_id", clean_ids[0]).execute()
            return query.in_("user_id", clean_ids).execute()

        resp = await _run_db_op("get guild member rows by ids", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                norm = _normalize_guild_member_row(row)
                uid = _clean_text(norm.get("user_id"))
                if uid:
                    out[uid] = norm
        return out
    except Exception as e:
        print(f"⚠️ workspace_service._get_guild_member_rows_by_ids failed: {repr(e)}")
        return {}


async def _get_staff_metrics_rows_by_ids(
    *,
    guild_id: int | str,
    staff_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    clean_ids = [str(x).strip() for x in staff_ids if _clean_text(x)]
    if not gid or not clean_ids:
        return {}

    sb = _sb()
    if sb is None:
        return {}

    try:
        def _read_sync():
            query = sb.table(STAFF_METRICS_TABLE).select("*").eq("guild_id", gid)
            if len(clean_ids) == 1:
                return query.eq("staff_id", clean_ids[0]).execute()
            return query.in_("staff_id", clean_ids).execute()

        resp = await _run_db_op("get staff metrics rows by ids", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                norm = _normalize_staff_metrics_row(row)
                sid = _clean_text(norm.get("staff_id"))
                if sid:
                    out[sid] = norm
        return out
    except Exception as e:
        print(f"⚠️ workspace_service._get_staff_metrics_rows_by_ids failed: {repr(e)}")
        return {}


async def _list_queue_ticket_rows(
    *,
    guild_id: int | str,
    statuses: Optional[Sequence[str]] = None,
    assigned_to: Optional[int | str] = None,
    category: Optional[str] = None,
    intake_type: Optional[str] = None,
    include_ghost: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    if not gid:
        return []

    sb = _sb()
    if sb is None:
        return []

    clean_statuses = [str(x).strip().lower() for x in (statuses or ["open", "claimed"]) if _clean_text(x)]
    clean_assigned_to = _as_str_id(assigned_to)
    clean_category = _clean_text(category)
    clean_intake_type = _clean_text(intake_type)
    max_limit = max(1, min(int(limit or 50), 200))

    try:
        def _read_sync():
            query = sb.table(TICKETS_TABLE).select("*").eq("guild_id", gid)

            if len(clean_statuses) == 1:
                query = query.eq("status", clean_statuses[0])
            elif len(clean_statuses) > 1:
                query = query.in_("status", clean_statuses)

            if clean_assigned_to:
                query = query.eq("assigned_to", clean_assigned_to)

            if not include_ghost:
                query = query.eq("is_ghost", False)

            if clean_category:
                query = query.or_(
                    f"category.eq.{clean_category},matched_category_slug.eq.{clean_category}"
                )

            if clean_intake_type:
                query = query.eq("matched_intake_type", clean_intake_type)

            return query.order("created_at", desc=False).limit(max_limit).execute()

        resp = await _run_db_op("list queue ticket rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_ticket_row(row))
        return out
    except Exception as e:
        print(f"⚠️ workspace_service._list_queue_ticket_rows failed: {repr(e)}")
        return []


# ============================================================
# Derived helpers
# ============================================================

def _resolve_display_name(member_row: Optional[Dict[str, Any]], fallback_id: Optional[str] = None) -> str:
    if isinstance(member_row, dict):
        for key in ("display_name", "nickname", "username", "user_id"):
            text = _clean_text(member_row.get(key))
            if text:
                return text
    return _clean_text(fallback_id) or "Unknown"


def _resolve_avatar_url(member_row: Optional[Dict[str, Any]]) -> Optional[str]:
    if isinstance(member_row, dict):
        return _clean_text(member_row.get("avatar_url"))
    return None


def _build_assignment_block(
    *,
    ticket: Dict[str, Any],
    member_lookup: Dict[str, Dict[str, Any]],
    staff_metrics_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    assigned_to = _clean_text(ticket.get("assigned_to"))
    claimed_by = _clean_text(ticket.get("claimed_by"))
    closed_by = _clean_text(ticket.get("closed_by"))

    assigned_member = member_lookup.get(assigned_to or "")
    claimed_member = member_lookup.get(claimed_by or "")
    closed_member = member_lookup.get(closed_by or "")

    assigned_metrics = staff_metrics_lookup.get(assigned_to or "")
    claimed_metrics = staff_metrics_lookup.get(claimed_by or "")

    return {
        "assigned_to": assigned_to,
        "assigned_to_name": _resolve_display_name(assigned_member, assigned_to),
        "assigned_to_avatar_url": _resolve_avatar_url(assigned_member),
        "assigned_to_roles": assigned_member.get("role_names", []) if isinstance(assigned_member, dict) else [],
        "claimed_by": claimed_by,
        "claimed_by_name": _resolve_display_name(claimed_member, claimed_by),
        "claimed_by_avatar_url": _resolve_avatar_url(claimed_member),
        "closed_by": closed_by,
        "closed_by_name": _resolve_display_name(closed_member, closed_by),
        "closed_by_avatar_url": _resolve_avatar_url(closed_member),
        "assignee_staff_metrics": assigned_metrics or None,
        "claimer_staff_metrics": claimed_metrics or None,
        "is_claimed": bool(assigned_to or claimed_by),
        "is_unassigned": not bool(assigned_to or claimed_by),
    }


def _event_actor_id(row: Dict[str, Any]) -> Optional[str]:
    actor = _clean_text(row.get("actor_user_id"))
    if actor:
        return actor

    meta = _safe_meta(row.get("metadata"))
    for key in ("actor_id", "staff_id", "approved_by", "handled_by"):
        value = _clean_text(meta.get(key))
        if value:
            return value

    return None


def _event_actor_name(row: Dict[str, Any]) -> Optional[str]:
    name = _clean_text(row.get("actor_name"))
    if name:
        return name

    meta = _safe_meta(row.get("metadata"))
    for key in ("actor_name", "staff_name", "approved_by_name", "handled_by_name"):
        value = _clean_text(meta.get(key))
        if value:
            return value

    return None


def _message_author_id(row: Dict[str, Any]) -> Optional[str]:
    return _clean_text(row.get("author_id"))


def _message_author_name(row: Dict[str, Any]) -> Optional[str]:
    return _clean_text(row.get("author_name"))


def _message_is_staff_like(row: Dict[str, Any], owner_id: Optional[str]) -> bool:
    author_id = _message_author_id(row)
    if author_id and owner_id and author_id == owner_id:
        return False

    message_type = str(row.get("message_type") or "").strip().lower()
    if message_type in {"member", "user", "owner", "requester"}:
        return False

    return True


def _event_is_staff_like(row: Dict[str, Any], owner_id: Optional[str]) -> bool:
    actor_id = _event_actor_id(row)
    if actor_id and owner_id and actor_id == owner_id:
        return False

    if actor_id:
        return True

    source = str(row.get("source") or "").strip().lower()
    event_family = str(row.get("event_family") or "").strip().lower()
    event_type = str(row.get("event_type") or "").strip().lower()

    if source == "system" and not actor_id:
        if any(token in event_type for token in ("claim", "assign", "close", "transfer", "approve", "deny")):
            return True
        if event_family in {"ticket", "verification"} and event_type not in {"message"}:
            return True

    return False


def _build_response_markers(
    *,
    ticket: Dict[str, Any],
    owner_id: Optional[str],
    activity_rows: Sequence[Dict[str, Any]],
    message_rows: Sequence[Dict[str, Any]],
    notes_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    first_response_at: Optional[str] = None
    first_response_actor_id: Optional[str] = None
    first_response_actor_name: Optional[str] = None
    latest_staff_activity_at: Optional[str] = None
    latest_staff_actor_id: Optional[str] = None
    latest_staff_actor_name: Optional[str] = None

    chronological_events = sorted(
        list(activity_rows),
        key=lambda r: _parse_ts(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    chronological_messages = sorted(
        list(message_rows),
        key=lambda r: _parse_ts(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    for row in chronological_messages:
        if not _message_is_staff_like(row, owner_id):
            continue
        first_response_at = _normalize_ts(row.get("created_at"))
        first_response_actor_id = _message_author_id(row)
        first_response_actor_name = _message_author_name(row)
        break

    for row in chronological_events:
        if not _event_is_staff_like(row, owner_id):
            continue
        candidate_at = _normalize_ts(row.get("created_at"))
        if first_response_at is None:
            first_response_at = candidate_at
            first_response_actor_id = _event_actor_id(row)
            first_response_actor_name = _event_actor_name(row)
            break
        parsed_existing = _parse_ts(first_response_at)
        parsed_candidate = _parse_ts(candidate_at)
        if parsed_existing and parsed_candidate and parsed_candidate < parsed_existing:
            first_response_at = candidate_at
            first_response_actor_id = _event_actor_id(row)
            first_response_actor_name = _event_actor_name(row)

    latest_activity_candidates: List[Dict[str, Any]] = []

    for row in activity_rows:
        if _event_is_staff_like(row, owner_id):
            latest_activity_candidates.append(
                {
                    "at": _normalize_ts(row.get("created_at")),
                    "actor_id": _event_actor_id(row),
                    "actor_name": _event_actor_name(row),
                }
            )

    for row in message_rows:
        if _message_is_staff_like(row, owner_id):
            latest_activity_candidates.append(
                {
                    "at": _normalize_ts(row.get("created_at")),
                    "actor_id": _message_author_id(row),
                    "actor_name": _message_author_name(row),
                }
            )

    for row in notes_rows:
        latest_activity_candidates.append(
            {
                "at": _normalize_ts(row.get("updated_at") or row.get("created_at")),
                "actor_id": _clean_text(row.get("author_id")),
                "actor_name": _clean_text(row.get("author_name")),
            }
        )

    latest_dt = None
    for row in latest_activity_candidates:
        parsed = _parse_ts(row.get("at"))
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_staff_activity_at = parsed.isoformat()
            latest_staff_actor_id = _clean_text(row.get("actor_id"))
            latest_staff_actor_name = _clean_text(row.get("actor_name"))

    return {
        "first_response_at": first_response_at,
        "first_response_actor_id": first_response_actor_id,
        "first_response_actor_name": first_response_actor_name,
        "latest_staff_activity_at": latest_staff_activity_at,
        "latest_staff_actor_id": latest_staff_actor_id,
        "latest_staff_actor_name": latest_staff_actor_name,
    }


def _build_sla_block(
    *,
    ticket: Dict[str, Any],
    response_markers: Dict[str, Any],
) -> Dict[str, Any]:
    status = _normalize_status(ticket.get("status"))
    created_at = ticket.get("created_at")
    closed_at = ticket.get("closed_at")
    sla_deadline = ticket.get("sla_deadline")
    first_response_at = response_markers.get("first_response_at")

    is_closed = status in {"closed", "deleted"}
    age_minutes = _minutes_between(created_at)
    response_minutes = _minutes_between(created_at, first_response_at) if first_response_at else None
    resolution_minutes = _minutes_between(created_at, closed_at) if closed_at else None

    now_dt = now_utc()
    deadline_dt = _parse_ts(sla_deadline)
    overdue = bool(deadline_dt and deadline_dt < now_dt and not is_closed)
    minutes_until_deadline = None
    minutes_overdue = None

    if deadline_dt:
        diff_minutes = int((deadline_dt - now_dt).total_seconds() // 60)
        if diff_minutes >= 0:
            minutes_until_deadline = diff_minutes
        else:
            minutes_overdue = abs(diff_minutes)

    if overdue:
        sla_status = "overdue"
    elif first_response_at:
        sla_status = "responded"
    elif is_closed:
        sla_status = "closed"
    elif deadline_dt:
        sla_status = "counting_down"
    else:
        sla_status = "no_deadline"

    return {
        "status": sla_status,
        "deadline_at": sla_deadline,
        "overdue": overdue,
        "minutes_until_deadline": minutes_until_deadline,
        "minutes_overdue": minutes_overdue,
        "age_minutes": age_minutes,
        "response_minutes": response_minutes,
        "resolution_minutes": resolution_minutes,
        "first_response_at": first_response_at,
        "latest_staff_activity_at": response_markers.get("latest_staff_activity_at"),
    }


def _build_notes_block(
    notes_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    latest_note = notes_rows[0] if notes_rows else None
    pinned_count = len([row for row in notes_rows if _boolish(row.get("is_pinned"), False)])

    return {
        "count": len(notes_rows),
        "pinned_count": pinned_count,
        "latest_note": latest_note,
        "latest_note_at": (
            _normalize_ts(latest_note.get("updated_at") or latest_note.get("created_at"))
            if isinstance(latest_note, dict)
            else None
        ),
        "latest_note_author_id": _clean_text(latest_note.get("author_id")) if isinstance(latest_note, dict) else None,
        "latest_note_author_name": _clean_text(latest_note.get("author_name")) if isinstance(latest_note, dict) else None,
    }


def _build_activity_block(
    *,
    activity_rows: Sequence[Dict[str, Any]],
    latest_activity_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    type_counts: Dict[str, int] = {}
    family_counts: Dict[str, int] = {}

    for row in activity_rows:
        event_type = str(row.get("event_type") or "unknown").strip().lower()
        event_family = str(row.get("event_family") or "unknown").strip().lower()
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        family_counts[event_family] = family_counts.get(event_family, 0) + 1

    latest_activity_at = None
    if isinstance(latest_activity_row, dict):
        latest_activity_at = _normalize_ts(latest_activity_row.get("created_at"))

    return {
        "count": len(activity_rows),
        "latest_activity": latest_activity_row,
        "latest_activity_at": latest_activity_at,
        "type_counts": type_counts,
        "family_counts": family_counts,
    }


def _build_messages_block(message_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest_message = message_rows[-1] if message_rows else None
    return {
        "count": len(message_rows),
        "latest_message": latest_message,
        "latest_message_at": _normalize_ts(latest_message.get("created_at")) if isinstance(latest_message, dict) else None,
    }


def _derive_risk_level(
    *,
    member_context: Dict[str, Any],
    verification_context: Dict[str, Any],
    ticket: Dict[str, Any],
    sla_block: Dict[str, Any],
) -> str:
    moderation_count = _as_int(
        _safe_meta(member_context.get("event_summary")).get("moderation_count"),
        0,
    )
    flagged_count = _as_int(
        _safe_meta(verification_context.get("flags_summary")).get("flagged_count"),
        0,
    )
    max_score = _as_int(
        _safe_meta(verification_context.get("flags_summary")).get("max_score"),
        0,
    )
    ticket_total = _as_int(
        _safe_meta(member_context.get("ticket_summary")).get("total"),
        0,
    )
    overdue = _boolish(sla_block.get("overdue"), False)
    priority = _normalize_priority(ticket.get("priority"))

    if priority == "urgent" or overdue or flagged_count > 0 or max_score >= 5 or moderation_count >= 3:
        return "high"

    if priority == "high" or max_score >= 2 or moderation_count >= 1 or ticket_total >= 3:
        return "medium"

    return "low"


def _build_recommended_actions(
    *,
    ticket: Dict[str, Any],
    member_context: Dict[str, Any],
    verification_context: Dict[str, Any],
    sla_block: Dict[str, Any],
    notes_block: Dict[str, Any],
) -> List[str]:
    actions: List[str] = []

    if not _clean_text(ticket.get("assigned_to")) and not _clean_text(ticket.get("claimed_by")):
        actions.append("Claim this ticket.")

    if _boolish(sla_block.get("overdue"), False):
        actions.append("Respond now — this ticket is overdue.")

    vc_status = str(_safe_meta(verification_context.get("vc_summary")).get("latest_status") or "").strip().upper()
    if vc_status in {"PENDING", "READY", "ACCEPTED", "STAFF_ACCEPTED"}:
        actions.append("Check VC verification status before replying.")

    verification_status = str(verification_context.get("status") or "").strip().lower()
    if verification_status == "needs review":
        actions.append("Review verification flags before making a decision.")

    if _as_int(_safe_meta(notes_block).get("count"), 0) == 0:
        actions.append("Add an internal staff note for continuity.")

    relationship_entry_method = _clean_text(
        _safe_meta(member_context.get("relationships")).get("entry_method")
    )
    if relationship_entry_method:
        actions.append(f"Confirm member entry path: {relationship_entry_method}.")

    return _sort_unique_texts(actions, limit=8)


def _build_staff_header(
    *,
    ticket: Dict[str, Any],
    category: Optional[Dict[str, Any]],
    member_context: Dict[str, Any],
    verification_context: Dict[str, Any],
    assignment: Dict[str, Any],
    sla_block: Dict[str, Any],
    notes_block: Dict[str, Any],
    activity_block: Dict[str, Any],
    risk_level: str,
) -> Dict[str, Any]:
    owner = _safe_meta(member_context.get("member"))
    ticket_summary = _safe_meta(member_context.get("ticket_summary"))
    relationships = _safe_meta(member_context.get("relationships"))
    verification_dashboard = _safe_meta(verification_context.get("dashboard"))

    return {
        "ticket_title": _clean_text(ticket.get("title")) or "Ticket",
        "ticket_number": ticket.get("ticket_number"),
        "ticket_status": _clean_text(ticket.get("status")) or "unknown",
        "ticket_priority": _normalize_priority(ticket.get("priority")),
        "ticket_channel_name": _clean_text(ticket.get("channel_name")),
        "category_name": (
            _clean_text(category.get("name")) if isinstance(category, dict)
            else _clean_text(ticket.get("matched_category_name") or ticket.get("category"))
        ),
        "category_slug": (
            _clean_text(category.get("slug")) if isinstance(category, dict)
            else _clean_text(ticket.get("matched_category_slug") or ticket.get("category"))
        ),
        "owner_user_id": _clean_text(owner.get("user_id") or ticket.get("user_id")),
        "owner_display_name": (
            _clean_text(owner.get("display_name"))
            or _clean_text(owner.get("nickname"))
            or _clean_text(owner.get("username"))
            or _clean_text(ticket.get("username"))
            or "Unknown"
        ),
        "owner_avatar_url": _clean_text(owner.get("avatar_url")),
        "owner_role_state": _clean_text(owner.get("role_state")) or "unknown",
        "owner_verification_label": _clean_text(verification_dashboard.get("status")) or "Unknown",
        "owner_access_label": _clean_text(_safe_meta(member_context.get("dashboard")).get("access_label")) or "Unknown",
        "entry_method": _clean_text(relationships.get("entry_method")),
        "invite_code": _clean_text(relationships.get("invite_code")),
        "invited_by_name": _clean_text(relationships.get("invited_by_name")),
        "assigned_to_name": _clean_text(assignment.get("assigned_to_name")),
        "claimed_by_name": _clean_text(assignment.get("claimed_by_name")),
        "sla_status": _clean_text(sla_block.get("status")) or "unknown",
        "sla_overdue": _boolish(sla_block.get("overdue"), False),
        "latest_activity_at": _clean_text(activity_block.get("latest_activity_at")),
        "note_count": _as_int(notes_block.get("count"), 0),
        "member_ticket_total": _as_int(ticket_summary.get("total"), 0),
        "risk_level": risk_level,
    }


def _build_dashboard_payload(
    *,
    ticket: Dict[str, Any],
    category: Optional[Dict[str, Any]],
    member_context: Dict[str, Any],
    verification_context: Dict[str, Any],
    assignment: Dict[str, Any],
    sla_block: Dict[str, Any],
    notes_block: Dict[str, Any],
    activity_block: Dict[str, Any],
    risk_level: str,
) -> Dict[str, Any]:
    owner_dashboard = _safe_meta(member_context.get("dashboard"))
    verification_dashboard = _safe_meta(verification_context.get("dashboard"))
    ticket_summary = _safe_meta(member_context.get("ticket_summary"))

    return {
        "ticket": {
            "id": _clean_text(ticket.get("id")),
            "ticket_number": ticket.get("ticket_number"),
            "title": _clean_text(ticket.get("title")),
            "status": _clean_text(ticket.get("status")),
            "priority": _normalize_priority(ticket.get("priority")),
            "channel_id": _clean_text(ticket.get("channel_id")),
            "channel_name": _clean_text(ticket.get("channel_name")),
            "created_at": _normalize_ts(ticket.get("created_at")),
            "updated_at": _normalize_ts(ticket.get("updated_at")),
            "closed_at": _normalize_ts(ticket.get("closed_at")),
            "transcript_url": _clean_text(ticket.get("transcript_url")),
            "matched_category_name": (
                _clean_text(category.get("name")) if isinstance(category, dict)
                else _clean_text(ticket.get("matched_category_name"))
            ),
            "matched_category_slug": (
                _clean_text(category.get("slug")) if isinstance(category, dict)
                else _clean_text(ticket.get("matched_category_slug"))
            ),
            "matched_intake_type": (
                _clean_text(category.get("intake_type")) if isinstance(category, dict)
                else _clean_text(ticket.get("matched_intake_type"))
            ),
        },
        "owner": owner_dashboard,
        "verification": verification_dashboard,
        "assignment": {
            "assigned_to": _clean_text(assignment.get("assigned_to")),
            "assigned_to_name": _clean_text(assignment.get("assigned_to_name")),
            "claimed_by": _clean_text(assignment.get("claimed_by")),
            "claimed_by_name": _clean_text(assignment.get("claimed_by_name")),
            "closed_by": _clean_text(assignment.get("closed_by")),
            "closed_by_name": _clean_text(assignment.get("closed_by_name")),
        },
        "sla": sla_block,
        "notes": {
            "count": _as_int(notes_block.get("count"), 0),
            "pinned_count": _as_int(notes_block.get("pinned_count"), 0),
            "latest_note_at": _clean_text(notes_block.get("latest_note_at")),
        },
        "activity": {
            "count": _as_int(activity_block.get("count"), 0),
            "latest_activity_at": _clean_text(activity_block.get("latest_activity_at")),
        },
        "member_ticket_summary": ticket_summary,
        "risk_level": risk_level,
    }


def _build_queue_card(
    *,
    ticket: Dict[str, Any],
    category: Optional[Dict[str, Any]],
    member_context: Dict[str, Any],
    verification_context: Dict[str, Any],
    assignment: Dict[str, Any],
    sla_block: Dict[str, Any],
    notes_block: Dict[str, Any],
    activity_block: Dict[str, Any],
    risk_level: str,
    recommended_actions: Sequence[str],
) -> Dict[str, Any]:
    owner_dashboard = _safe_meta(member_context.get("dashboard"))
    verification_dashboard = _safe_meta(verification_context.get("dashboard"))

    return {
        "ticket_id": _clean_text(ticket.get("id")),
        "ticket_number": ticket.get("ticket_number"),
        "title": _clean_text(ticket.get("title")),
        "status": _clean_text(ticket.get("status")),
        "priority": _normalize_priority(ticket.get("priority")),
        "priority_rank": _priority_rank(ticket.get("priority")),
        "category_name": (
            _clean_text(category.get("name")) if isinstance(category, dict)
            else _clean_text(ticket.get("matched_category_name") or ticket.get("category"))
        ),
        "category_slug": (
            _clean_text(category.get("slug")) if isinstance(category, dict)
            else _clean_text(ticket.get("matched_category_slug") or ticket.get("category"))
        ),
        "intake_type": (
            _clean_text(category.get("intake_type")) if isinstance(category, dict)
            else _clean_text(ticket.get("matched_intake_type"))
        ),
        "channel_id": _clean_text(ticket.get("channel_id")),
        "channel_name": _clean_text(ticket.get("channel_name")),
        "created_at": _normalize_ts(ticket.get("created_at")),
        "updated_at": _normalize_ts(ticket.get("updated_at")),
        "owner_user_id": _clean_text(owner_dashboard.get("discord_id") or ticket.get("user_id")),
        "owner_display_name": (
            _clean_text(owner_dashboard.get("display_name"))
            or _clean_text(owner_dashboard.get("username"))
            or _clean_text(ticket.get("username"))
            or "Unknown"
        ),
        "owner_avatar_url": _clean_text(owner_dashboard.get("avatar_url")),
        "owner_verification_label": _clean_text(verification_dashboard.get("status")),
        "owner_entry_method": _clean_text(owner_dashboard.get("entry_method")),
        "owner_inviter_name": _clean_text(owner_dashboard.get("inviter_name")),
        "assigned_to_name": _clean_text(assignment.get("assigned_to_name")),
        "claimed_by_name": _clean_text(assignment.get("claimed_by_name")),
        "note_count": _as_int(notes_block.get("count"), 0),
        "latest_activity_at": _clean_text(activity_block.get("latest_activity_at")),
        "sla_status": _clean_text(sla_block.get("status")),
        "overdue": _boolish(sla_block.get("overdue"), False),
        "minutes_overdue": _as_int(sla_block.get("minutes_overdue"), 0),
        "age_minutes": _as_int(sla_block.get("age_minutes"), 0),
        "risk_level": risk_level,
        "recommended_actions": list(recommended_actions),
    }


def _queue_sort_key(card: Dict[str, Any]) -> tuple:
    overdue = 1 if _boolish(card.get("overdue"), False) else 0
    priority_rank = _as_int(card.get("priority_rank"), 0)
    age_minutes = _as_int(card.get("age_minutes"), 0)
    created_at = _parse_ts(card.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)

    return (
        overdue,
        priority_rank,
        age_minutes,
        created_at.timestamp(),
    )


# ============================================================
# Public main helpers
# ============================================================

async def get_ticket_workspace_snapshot(
    *,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    include_notes: bool = True,
    include_activity: bool = True,
    include_messages: bool = False,
    notes_limit: int = 8,
    activity_limit: int = 15,
    message_limit: int = 15,
) -> Dict[str, Any]:
    ticket = await _get_ticket_row(
        ticket_id=ticket_id,
        channel_id=channel_id,
        ticket_row=ticket_row,
    )

    if not isinstance(ticket, dict):
        return {
            "ok": False,
            "error": "Ticket not found.",
            "ticket_id": ticket_id,
            "channel_id": channel_id,
        }

    guild_id = _clean_text(ticket.get("guild_id"))
    owner_id = _clean_text(ticket.get("user_id"))
    if not guild_id or not owner_id:
        return {
            "ok": False,
            "error": "Ticket is missing guild_id or user_id.",
            "ticket": ticket,
        }

    category_task = asyncio.create_task(_get_category_row_for_ticket(ticket))
    member_context_task = asyncio.create_task(
        get_member_context_snapshot(
            guild_id=guild_id,
            user_id=owner_id,
            ticket_limit=25,
            event_limit=15,
            join_limit=10,
            include_recent_tickets=True,
            include_recent_events=True,
            include_recent_joins=True,
        )
    )
    verification_context_task = asyncio.create_task(
        get_verification_context_snapshot(
            guild_id=guild_id,
            user_id=owner_id,
            flag_limit=20,
            vc_limit=20,
            token_limit=25,
            include_flag_rows=True,
            include_vc_rows=True,
            include_token_rows=True,
        )
    )

    notes_task = asyncio.create_task(
        list_internal_notes(channel_id=ticket.get("channel_id"), limit=notes_limit)
    ) if include_notes and _clean_text(ticket.get("channel_id")) else None

    activity_task = asyncio.create_task(
        list_ticket_activity_events(
            guild_id=guild_id,
            ticket_id=ticket.get("id"),
            channel_id=ticket.get("channel_id"),
            target_user_id=owner_id,
            limit=activity_limit,
        )
    ) if include_activity else None

    latest_activity_task = asyncio.create_task(
        get_latest_ticket_activity(
            guild_id=guild_id,
            ticket_id=ticket.get("id"),
            channel_id=ticket.get("channel_id"),
            target_user_id=owner_id,
        )
    ) if include_activity else None

    messages_task = asyncio.create_task(
        list_ticket_messages(channel_id=ticket.get("channel_id"), limit=message_limit)
    ) if include_messages and _clean_text(ticket.get("channel_id")) else None

    category = await category_task
    member_context = await member_context_task
    verification_context = await verification_context_task
    notes_rows = await notes_task if notes_task else []
    activity_rows = await activity_task if activity_task else []
    latest_activity_row = await latest_activity_task if latest_activity_task else None
    message_rows = await messages_task if messages_task else []

    notes_rows = list(notes_rows or [])
    activity_rows = list(activity_rows or [])
    message_rows = list(message_rows or [])

    staff_ids = _sort_unique_texts(
        [
            ticket.get("assigned_to"),
            ticket.get("claimed_by"),
            ticket.get("closed_by"),
            *[
                row.get("author_id")
                for row in notes_rows
                if isinstance(row, dict)
            ],
            *[
                _event_actor_id(row)
                for row in activity_rows
                if isinstance(row, dict)
            ],
        ],
        limit=50,
    )

    member_lookup = await _get_guild_member_rows_by_ids(
        guild_id=guild_id,
        user_ids=staff_ids,
    )
    staff_metrics_lookup = await _get_staff_metrics_rows_by_ids(
        guild_id=guild_id,
        staff_ids=staff_ids,
    )

    assignment = _build_assignment_block(
        ticket=ticket,
        member_lookup=member_lookup,
        staff_metrics_lookup=staff_metrics_lookup,
    )

    response_markers = _build_response_markers(
        ticket=ticket,
        owner_id=owner_id,
        activity_rows=activity_rows,
        message_rows=message_rows,
        notes_rows=notes_rows,
    )

    sla_block = _build_sla_block(
        ticket=ticket,
        response_markers=response_markers,
    )

    notes_block = _build_notes_block(notes_rows)
    activity_block = _build_activity_block(
        activity_rows=activity_rows,
        latest_activity_row=latest_activity_row if isinstance(latest_activity_row, dict) else None,
    )
    messages_block = _build_messages_block(message_rows)

    risk_level = _derive_risk_level(
        member_context=member_context,
        verification_context=verification_context,
        ticket=ticket,
        sla_block=sla_block,
    )

    recommended_actions = _build_recommended_actions(
        ticket=ticket,
        member_context=member_context,
        verification_context=verification_context,
        sla_block=sla_block,
        notes_block=notes_block,
    )

    staff_header = _build_staff_header(
        ticket=ticket,
        category=category,
        member_context=member_context,
        verification_context=verification_context,
        assignment=assignment,
        sla_block=sla_block,
        notes_block=notes_block,
        activity_block=activity_block,
        risk_level=risk_level,
    )

    dashboard_payload = _build_dashboard_payload(
        ticket=ticket,
        category=category,
        member_context=member_context,
        verification_context=verification_context,
        assignment=assignment,
        sla_block=sla_block,
        notes_block=notes_block,
        activity_block=activity_block,
        risk_level=risk_level,
    )

    queue_card = _build_queue_card(
        ticket=ticket,
        category=category,
        member_context=member_context,
        verification_context=verification_context,
        assignment=assignment,
        sla_block=sla_block,
        notes_block=notes_block,
        activity_block=activity_block,
        risk_level=risk_level,
        recommended_actions=recommended_actions,
    )

    snapshot: Dict[str, Any] = {
        "ok": True,
        "generated_at": _now_iso(),
        "ticket": ticket,
        "category": category,
        "assignment": assignment,
        "member_context": member_context,
        "verification_context": verification_context,
        "notes": {
            "summary": notes_block,
            "rows": notes_rows,
        },
        "activity": {
            "summary": activity_block,
            "rows": activity_rows,
        },
        "messages": {
            "summary": messages_block,
            "rows": message_rows,
        },
        "response_markers": response_markers,
        "sla": sla_block,
        "risk_level": risk_level,
        "recommended_actions": recommended_actions,
        "staff_header": staff_header,
        "dashboard": dashboard_payload,
        "queue_card": queue_card,
    }

    _ws_debug(
        f"snapshot ticket_id={ticket.get('id')} "
        f"channel_id={ticket.get('channel_id')} "
        f"status={ticket.get('status')} risk={risk_level}"
    )

    return snapshot


async def get_ticket_staff_header(
    *,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    snapshot = await get_ticket_workspace_snapshot(
        ticket_id=ticket_id,
        channel_id=channel_id,
        ticket_row=ticket_row,
        include_notes=True,
        include_activity=True,
        include_messages=False,
        notes_limit=5,
        activity_limit=10,
        message_limit=0,
    )
    if not snapshot.get("ok"):
        return None
    return _safe_meta(snapshot.get("staff_header"))


async def get_ticket_dashboard_payload(
    *,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    snapshot = await get_ticket_workspace_snapshot(
        ticket_id=ticket_id,
        channel_id=channel_id,
        ticket_row=ticket_row,
        include_notes=True,
        include_activity=True,
        include_messages=False,
        notes_limit=5,
        activity_limit=10,
        message_limit=0,
    )
    if not snapshot.get("ok"):
        return None
    return _safe_meta(snapshot.get("dashboard"))


async def list_staff_queue_snapshots(
    *,
    guild_id: int | str,
    statuses: Optional[Sequence[str]] = None,
    assigned_to: Optional[int | str] = None,
    category: Optional[str] = None,
    intake_type: Optional[str] = None,
    include_ghost: bool = False,
    limit: int = 50,
    notes_limit: int = 3,
    activity_limit: int = 6,
) -> Dict[str, Any]:
    ticket_rows = await _list_queue_ticket_rows(
        guild_id=guild_id,
        statuses=statuses or ["open", "claimed"],
        assigned_to=assigned_to,
        category=category,
        intake_type=intake_type,
        include_ghost=include_ghost,
        limit=limit,
    )

    if not ticket_rows:
        return {
            "ok": True,
            "generated_at": _now_iso(),
            "guild_id": _as_str_id(guild_id),
            "count": 0,
            "rows": [],
        }

    snapshots = await asyncio.gather(
        *[
            get_ticket_workspace_snapshot(
                ticket_row=row,
                include_notes=True,
                include_activity=True,
                include_messages=False,
                notes_limit=notes_limit,
                activity_limit=activity_limit,
                message_limit=0,
            )
            for row in ticket_rows
        ],
        return_exceptions=True,
    )

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    for item in snapshots:
        if isinstance(item, Exception):
            errors.append(repr(item))
            continue

        if not isinstance(item, dict):
            continue

        if not item.get("ok"):
            errors.append(_clean_text(item.get("error")) or "snapshot build failed")
            continue

        queue_card = _safe_meta(item.get("queue_card"))
        queue_card["ticket"] = _safe_meta(item.get("ticket"))
        queue_card["assignment"] = _safe_meta(item.get("assignment"))
        queue_card["sla"] = _safe_meta(item.get("sla"))
        queue_card["staff_header"] = _safe_meta(item.get("staff_header"))
        rows.append(queue_card)

    rows.sort(key=_queue_sort_key, reverse=True)

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "guild_id": _as_str_id(guild_id),
        "count": len(rows),
        "rows": rows,
        "errors": errors,
    }


# ============================================================
# Diagnostics
# ============================================================

async def workspace_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "supabase": False,
        "tables": {
            "tickets": TICKETS_TABLE,
            "ticket_categories": TICKET_CATEGORIES_TABLE,
            "guild_members": GUILD_MEMBERS_TABLE,
            "staff_metrics": STAFF_METRICS_TABLE,
        },
        "error": None,
    }

    sb = _sb()
    if sb is None:
        out["error"] = "supabase unavailable"
        return out

    out["supabase"] = True

    try:
        def _probe_tickets():
            return sb.table(TICKETS_TABLE).select("*").limit(1).execute()

        def _probe_categories():
            return sb.table(TICKET_CATEGORIES_TABLE).select("*").limit(1).execute()

        def _probe_members():
            return sb.table(GUILD_MEMBERS_TABLE).select("*").limit(1).execute()

        await _run_db_op("workspace healthcheck tickets", _probe_tickets)
        await _run_db_op("workspace healthcheck categories", _probe_categories)
        await _run_db_op("workspace healthcheck members", _probe_members)

        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "get_ticket_workspace_snapshot",
    "get_ticket_staff_header",
    "get_ticket_dashboard_payload",
    "list_staff_queue_snapshots",
    "workspace_healthcheck",
]