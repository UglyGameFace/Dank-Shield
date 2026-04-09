from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..globals import get_supabase, now_utc, reset_supabase
from .member_context_service import get_member_context_snapshot
from .verification_context_service import get_verification_context_snapshot
from .workspace_service import (
    get_ticket_workspace_snapshot,
    list_staff_queue_snapshots,
)

try:
    from .repository import list_ticket_activity_events
except Exception:
    async def list_ticket_activity_events(*args, **kwargs):  # type: ignore
        return []


# ============================================================
# tickets_new/dashboard_adapter.py
# ------------------------------------------------------------
# Purpose:
# - adapt internal ticket/member/verification/workspace snapshots
#   into stable dashboard/API payloads
# - keep frontend routes thin
# - keep raw DB / service internals out of UI contracts
# ============================================================

TICKET_CATEGORIES_TABLE = "ticket_categories"


# ============================================================
# Small helpers
# ============================================================

def _adapter_debug(msg: str) -> None:
    try:
        print(f"🧩 dashboard_adapter {msg}")
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
# Category readers
# ============================================================

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


async def list_ticket_categories_for_dashboard(
    *,
    guild_id: int | str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    if not gid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 50), 200))

    try:
        def _read_sync():
            return (
                sb.table(TICKET_CATEGORIES_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .order("sort_order", desc=False)
                .limit(max_limit)
                .execute()
            )

        resp = await _run_db_op("list ticket categories for dashboard", _read_sync)
        rows = getattr(resp, "data", None) or []

        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_category_row(row))

        out.sort(
            key=lambda c: (
                c.get("sort_order") is None,
                c.get("sort_order") if c.get("sort_order") is not None else 10_000,
                str(c.get("name") or "").lower(),
            )
        )
        return out
    except Exception as e:
        print(f"⚠️ dashboard_adapter.list_ticket_categories_for_dashboard failed: {repr(e)}")
        return []


# ============================================================
# Shape adapters
# ============================================================

def _adapt_ticket_row_for_dashboard(ticket: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(ticket.get("id")),
        "guild_id": _clean_text(ticket.get("guild_id")),
        "user_id": _clean_text(ticket.get("user_id")),
        "username": _clean_text(ticket.get("username")),
        "title": _clean_text(ticket.get("title")),
        "category": _clean_text(ticket.get("category")),
        "status": _clean_text(ticket.get("status")) or "unknown",
        "priority": _clean_text(ticket.get("priority")) or "medium",
        "claimed_by": _clean_text(ticket.get("claimed_by")),
        "assigned_to": _clean_text(ticket.get("assigned_to")),
        "closed_by": _clean_text(ticket.get("closed_by")),
        "closed_reason": _clean_text(ticket.get("closed_reason")),
        "channel_id": _clean_text(ticket.get("channel_id")),
        "channel_name": _clean_text(ticket.get("channel_name")),
        "discord_thread_id": _clean_text(ticket.get("discord_thread_id")),
        "source": _clean_text(ticket.get("source")),
        "ticket_number": ticket.get("ticket_number"),
        "is_ghost": _boolish(ticket.get("is_ghost"), False),
        "created_at": _normalize_ts(ticket.get("created_at")),
        "updated_at": _normalize_ts(ticket.get("updated_at")),
        "closed_at": _normalize_ts(ticket.get("closed_at")),
        "reopened_at": _normalize_ts(ticket.get("reopened_at")),
        "transcript_url": _clean_text(ticket.get("transcript_url")),
        "transcript_message_id": _clean_text(ticket.get("transcript_message_id")),
        "transcript_channel_id": _clean_text(ticket.get("transcript_channel_id")),
        "matched_category_id": _clean_text(ticket.get("matched_category_id")),
        "matched_category_name": _clean_text(ticket.get("matched_category_name")),
        "matched_category_slug": _clean_text(ticket.get("matched_category_slug")),
        "matched_intake_type": _clean_text(ticket.get("matched_intake_type")),
        "matched_category_reason": _clean_text(ticket.get("matched_category_reason")),
        "matched_category_score": _as_int(ticket.get("matched_category_score"), 0),
    }


def _adapt_activity_row_for_dashboard(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _safe_meta(row.get("metadata"))

    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _clean_text(row.get("guild_id")),
        "event_family": _clean_text(row.get("event_family")) or "unknown",
        "event_type": _clean_text(row.get("event_type")) or "unknown",
        "type": _clean_text(row.get("event_type")) or "unknown",
        "_source": _clean_text(row.get("source")) or "system",
        "actor_id": _clean_text(row.get("actor_user_id")),
        "actor_name": _clean_text(row.get("actor_name")),
        "target_user_id": _clean_text(row.get("target_user_id")),
        "target_name": _clean_text(row.get("target_name")),
        "channel_id": _clean_text(row.get("channel_id")),
        "channel_name": _clean_text(row.get("channel_name")),
        "ticket_id": _clean_text(row.get("ticket_id")),
        "ticket_message_id": _clean_text(row.get("ticket_message_id")),
        "related_table": _clean_text(row.get("related_table")),
        "related_id": _clean_text(row.get("related_id")),
        "title": _clean_text(row.get("title")),
        "description": _clean_text(row.get("description")),
        "reason": _clean_text(row.get("reason")),
        "message": _clean_text(row.get("description") or row.get("reason")),
        "metadata": metadata,
        "created_at": _normalize_ts(row.get("created_at")),
    }


def _adapt_category_row_for_dashboard(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
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
    }


def _adapt_member_block_for_dashboard(member_context: Dict[str, Any]) -> Dict[str, Any]:
    member = _safe_meta(member_context.get("member"))
    dashboard = _safe_meta(member_context.get("dashboard"))

    return {
        "guild_id": _clean_text(member.get("guild_id")),
        "user_id": _clean_text(member.get("user_id")),
        "discord_id": _clean_text(dashboard.get("discord_id") or member.get("user_id")),
        "username": _clean_text(member.get("username")),
        "display_name": _clean_text(member.get("display_name")),
        "nickname": _clean_text(member.get("nickname")),
        "global_name": _clean_text(member.get("display_name")),
        "avatar_url": _clean_text(member.get("avatar_url")),
        "role_names": [str(x) for x in _safe_list(member.get("role_names")) if _clean_text(x)],
        "role_ids": [str(x) for x in _safe_list(member.get("role_ids")) if _clean_text(x)],
        "has_unverified": _boolish(member.get("has_unverified"), False),
        "has_verified_role": _boolish(member.get("has_verified_role"), False),
        "has_staff_role": _boolish(member.get("has_staff_role"), False),
        "role_state": _clean_text(member.get("role_state")) or "unknown",
        "role_state_reason": _clean_text(member.get("role_state_reason")),
        "joined_at": _normalize_ts(member.get("joined_at")),
        "top_role": _clean_text(member.get("top_role")),
        "access_label": _clean_text(dashboard.get("access_label")),
        "verification_label": _clean_text(dashboard.get("verification_label")),
    }


def _adapt_relationships_for_dashboard(member_context: Dict[str, Any]) -> Dict[str, Any]:
    relationships = _safe_meta(member_context.get("relationships"))
    ticket_summary = _safe_meta(member_context.get("ticket_summary"))

    vouch_count = 1 if _clean_text(relationships.get("vouched_by") or relationships.get("vouched_by_name")) else 0

    return {
        "entry_method": _clean_text(relationships.get("entry_method")),
        "verification_source": _clean_text(relationships.get("verification_source")),
        "entry_reason": _clean_text(relationships.get("entry_reason")),
        "approval_reason": _clean_text(relationships.get("approval_reason")),
        "invite_code": _clean_text(relationships.get("invite_code")),
        "inviter_id": _clean_text(relationships.get("invited_by")),
        "inviter_name": _clean_text(relationships.get("invited_by_name")),
        "vouched_by": _clean_text(relationships.get("vouched_by")),
        "vouched_by_name": _clean_text(relationships.get("vouched_by_name")),
        "approved_by": _clean_text(relationships.get("approved_by")),
        "approved_by_name": _clean_text(relationships.get("approved_by_name")),
        "verification_ticket_id": _clean_text(relationships.get("verification_ticket_id")),
        "source_ticket_id": _clean_text(relationships.get("source_ticket_id")),
        "vouch_count": vouch_count,
        "latest_vouch_at": None,
        "ticket_total": _as_int(ticket_summary.get("total"), 0),
    }


def _adapt_entry_for_dashboard(member_context: Dict[str, Any]) -> Dict[str, Any]:
    relationships = _safe_meta(member_context.get("relationships"))
    join_summary = _safe_meta(member_context.get("join_summary"))

    invite_code = _clean_text(relationships.get("invite_code"))

    return {
        "joined_at": _clean_text(join_summary.get("joined_at") or join_summary.get("latest_joined_at")),
        "join_source": _clean_text(relationships.get("verification_source") or relationships.get("entry_method")),
        "entry_method": _clean_text(relationships.get("entry_method")),
        "invite_code": invite_code,
        "inviter_id": _clean_text(relationships.get("invited_by")),
        "inviter_name": _clean_text(relationships.get("invited_by_name")),
        "vanity_used": bool(invite_code and invite_code.lower() == "vanity"),
    }


def _adapt_verification_summary_for_dashboard(verification_context: Dict[str, Any]) -> Dict[str, Any]:
    dashboard = _safe_meta(verification_context.get("dashboard"))

    return {
        "status": _clean_text(dashboard.get("status")) or "Unknown",
        "flag_count": _as_int(dashboard.get("flag_count"), 0),
        "flagged_count": _as_int(dashboard.get("flagged_count"), 0),
        "latest_flag_at": _clean_text(dashboard.get("latest_flag_at")),
        "vc_latest_status": _clean_text(dashboard.get("vc_latest_status")),
        "vc_request_count": _as_int(dashboard.get("vc_request_count"), 0),
        "vc_completed_count": _as_int(dashboard.get("vc_completed_count"), 0),
        "token_latest_status": _clean_text(dashboard.get("token_latest_status")),
        "token_latest_decision": _clean_text(dashboard.get("token_latest_decision")),
        "token_submitted_count": _as_int(dashboard.get("token_submitted_count"), 0),
        "token_pending_count": _as_int(dashboard.get("token_pending_count"), 0),
        "token_approved_count": _as_int(dashboard.get("token_approved_count"), 0),
        "token_denied_count": _as_int(dashboard.get("token_denied_count"), 0),
    }


def _adapt_recent_vouches_for_dashboard(member_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    relationships = _safe_meta(member_context.get("relationships"))
    vouched_by = _clean_text(relationships.get("vouched_by"))
    vouched_by_name = _clean_text(relationships.get("vouched_by_name"))

    if not vouched_by and not vouched_by_name:
        return []

    return [
        {
            "id": f"vouch:{vouched_by or vouched_by_name}",
            "actor_id": vouched_by,
            "actor_name": vouched_by_name or vouched_by,
            "reason": _clean_text(relationships.get("entry_reason")) or "Member was vouched into the server.",
            "created_at": None,
        }
    ]


def _adapt_name_history_for_dashboard(member_context: Dict[str, Any]) -> Dict[str, Any]:
    name_history = _safe_meta(member_context.get("name_history"))

    usernames = [str(x) for x in _safe_list(name_history.get("usernames")) if _clean_text(x)]
    display_names = [str(x) for x in _safe_list(name_history.get("display_names")) if _clean_text(x)]
    nicknames = [str(x) for x in _safe_list(name_history.get("nicknames")) if _clean_text(x)]
    all_names = [str(x) for x in _safe_list(name_history.get("all_names")) if _clean_text(x)]

    return {
        "usernameHistory": usernames,
        "displayNameHistory": display_names,
        "nicknameHistory": nicknames,
        "historicalUsernames": all_names,
    }


def _pick_open_ticket(recent_tickets: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for row in recent_tickets:
        status = str(row.get("status") or "").strip().lower()
        if status in {"open", "claimed"}:
            return row
    return None


def _sort_recent_tickets(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _key(row: Dict[str, Any]):
        ts = _parse_ts(row.get("updated_at") or row.get("created_at"))
        return ts or datetime.min.replace(tzinfo=timezone.utc)

    return sorted(list(rows), key=_key, reverse=True)


# ============================================================
# Public payload builders
# ============================================================

async def build_ticket_detail_dashboard_payload(
    *,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    include_notes: bool = True,
    include_activity: bool = True,
    include_messages: bool = True,
    notes_limit: int = 12,
    activity_limit: int = 20,
    message_limit: int = 20,
) -> Dict[str, Any]:
    snapshot = await get_ticket_workspace_snapshot(
        ticket_id=ticket_id,
        channel_id=channel_id,
        include_notes=include_notes,
        include_activity=include_activity,
        include_messages=include_messages,
        notes_limit=notes_limit,
        activity_limit=activity_limit,
        message_limit=message_limit,
    )

    if not snapshot.get("ok"):
        return snapshot

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "ticket": _safe_meta(snapshot.get("ticket")),
        "category": _safe_meta(snapshot.get("category")),
        "assignment": _safe_meta(snapshot.get("assignment")),
        "member_context": _safe_meta(snapshot.get("member_context")),
        "verification_context": _safe_meta(snapshot.get("verification_context")),
        "notes": _safe_meta(snapshot.get("notes")),
        "activity": _safe_meta(snapshot.get("activity")),
        "messages": _safe_meta(snapshot.get("messages")),
        "response_markers": _safe_meta(snapshot.get("response_markers")),
        "sla": _safe_meta(snapshot.get("sla")),
        "risk_level": _clean_text(snapshot.get("risk_level")) or "low",
        "recommended_actions": list(_safe_list(snapshot.get("recommended_actions"))),
        "staff_header": _safe_meta(snapshot.get("staff_header")),
        "dashboard": _safe_meta(snapshot.get("dashboard")),
        "queue_card": _safe_meta(snapshot.get("queue_card")),
    }


async def build_staff_queue_dashboard_payload(
    *,
    guild_id: int | str,
    statuses: Optional[Sequence[str]] = None,
    assigned_to: Optional[int | str] = None,
    category: Optional[str] = None,
    intake_type: Optional[str] = None,
    include_ghost: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    payload = await list_staff_queue_snapshots(
        guild_id=guild_id,
        statuses=statuses,
        assigned_to=assigned_to,
        category=category,
        intake_type=intake_type,
        include_ghost=include_ghost,
        limit=limit,
        notes_limit=3,
        activity_limit=6,
    )

    if not payload.get("ok"):
        return payload

    rows = list(_safe_list(payload.get("rows")))

    queue_stats = {
        "total": len(rows),
        "overdue": len([r for r in rows if _boolish(r.get("overdue"), False)]),
        "urgent": len([r for r in rows if _clean_text(r.get("priority")) == "urgent"]),
        "high": len([r for r in rows if _clean_text(r.get("priority")) == "high"]),
        "unassigned": len([r for r in rows if not _clean_text(r.get("assigned_to_name")) and not _clean_text(r.get("claimed_by_name"))]),
        "verification": len([r for r in rows if _clean_text(r.get("intake_type")) == "verification"]),
        "appeal": len([r for r in rows if _clean_text(r.get("intake_type")) == "appeal"]),
        "report": len([r for r in rows if _clean_text(r.get("intake_type")) == "report"]),
    }

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "guild_id": _as_str_id(guild_id),
        "stats": queue_stats,
        "count": len(rows),
        "rows": rows,
        "errors": list(_safe_list(payload.get("errors"))),
    }


async def build_member_profile_dashboard_payload(
    *,
    guild_id: int | str,
    user_id: int | str,
    recent_activity_limit: int = 20,
    recent_ticket_limit: int = 15,
    category_limit: int = 25,
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

    member_context_task = asyncio.create_task(
        get_member_context_snapshot(
            guild_id=gid,
            user_id=uid,
            ticket_limit=recent_ticket_limit,
            event_limit=20,
            join_limit=10,
            include_recent_tickets=True,
            include_recent_events=True,
            include_recent_joins=True,
        )
    )

    verification_context_task = asyncio.create_task(
        get_verification_context_snapshot(
            guild_id=gid,
            user_id=uid,
            flag_limit=20,
            vc_limit=20,
            token_limit=25,
            include_flag_rows=True,
            include_vc_rows=True,
            include_token_rows=True,
        )
    )

    categories_task = asyncio.create_task(
        list_ticket_categories_for_dashboard(
            guild_id=gid,
            limit=category_limit,
        )
    )

    activity_task = asyncio.create_task(
        list_ticket_activity_events(
            guild_id=gid,
            target_user_id=uid,
            limit=recent_activity_limit,
        )
    )

    member_context = await member_context_task
    verification_context = await verification_context_task
    category_rows = await categories_task
    activity_rows = await activity_task

    recent_tickets = [
        _adapt_ticket_row_for_dashboard(row)
        for row in _sort_recent_tickets(_safe_list(member_context.get("recent_tickets")))
    ]
    open_ticket = _pick_open_ticket(recent_tickets)

    activity_payload = [
        _adapt_activity_row_for_dashboard(row)
        for row in _safe_list(activity_rows)
        if isinstance(row, dict)
    ]

    categories = [
        _adapt_category_row_for_dashboard(row)
        for row in category_rows
    ]

    member_block = _adapt_member_block_for_dashboard(member_context)
    relationships = _adapt_relationships_for_dashboard(member_context)
    entry = _adapt_entry_for_dashboard(member_context)
    verification_summary = _adapt_verification_summary_for_dashboard(verification_context)
    name_history = _adapt_name_history_for_dashboard(member_context)
    recent_vouches = _adapt_recent_vouches_for_dashboard(member_context)

    ticket_summary = _safe_meta(member_context.get("ticket_summary"))
    stats = {
        "ticket_count": _as_int(ticket_summary.get("total"), 0),
        "activity_count": len(activity_payload),
    }

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "guildId": gid,
        "viewer": {
            "discord_id": _clean_text(member_block.get("discord_id")),
            "username": _clean_text(member_block.get("username")),
            "display_name": _clean_text(member_block.get("display_name")),
            "global_name": _clean_text(member_block.get("display_name")),
            "avatar_url": _clean_text(member_block.get("avatar_url")),
            "role_names": list(_safe_list(member_block.get("role_names"))),
            "access_label": _clean_text(member_block.get("access_label")),
            "verification_label": _clean_text(member_block.get("verification_label")),
            "guild_id": gid,
        },
        "member": member_block,
        "relationships": relationships,
        "entry": entry,
        "ticketSummary": ticket_summary,
        "verification": verification_summary,
        "openTicket": open_ticket,
        "recentTickets": recent_tickets,
        "recentActivity": activity_payload,
        "verificationFlags": list(_safe_list(verification_context.get("recent_flags"))),
        "vcSessions": list(_safe_list(verification_context.get("recent_vc_sessions"))),
        "verificationTokens": list(_safe_list(verification_context.get("recent_tokens"))),
        "categories": categories,
        "vouches": recent_vouches,
        "stats": stats,
        **name_history,
    }


async def build_user_dashboard_payload(
    *,
    guild_id: int | str,
    user_id: int | str,
    recent_activity_limit: int = 20,
    recent_ticket_limit: int = 15,
    category_limit: int = 25,
) -> Dict[str, Any]:
    # For now this intentionally mirrors the member-profile dashboard payload,
    # because your current user-facing dashboard wants the same core shape.
    return await build_member_profile_dashboard_payload(
        guild_id=guild_id,
        user_id=user_id,
        recent_activity_limit=recent_activity_limit,
        recent_ticket_limit=recent_ticket_limit,
        category_limit=category_limit,
    )


async def build_ticket_activity_timeline_payload(
    *,
    guild_id: int | str,
    ticket_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    target_user_id: Optional[int | str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    gid = _as_str_id(guild_id)
    if not gid:
        return {
            "ok": False,
            "error": "guild_id is required.",
            "guild_id": gid,
        }

    activity_rows = await list_ticket_activity_events(
        guild_id=gid,
        ticket_id=ticket_id,
        channel_id=channel_id,
        target_user_id=target_user_id,
        limit=limit,
    )

    rows = [
        _adapt_activity_row_for_dashboard(row)
        for row in activity_rows
        if isinstance(row, dict)
    ]

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "guild_id": gid,
        "ticket_id": _clean_text(ticket_id),
        "channel_id": _clean_text(channel_id),
        "target_user_id": _as_str_id(target_user_id),
        "count": len(rows),
        "rows": rows,
    }


# ============================================================
# Diagnostics
# ============================================================

async def dashboard_adapter_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "supabase": False,
        "tables": {
            "ticket_categories": TICKET_CATEGORIES_TABLE,
        },
        "error": None,
    }

    sb = _sb()
    if sb is None:
        out["error"] = "supabase unavailable"
        return out

    out["supabase"] = True

    try:
        def _probe_categories():
            return sb.table(TICKET_CATEGORIES_TABLE).select("*").limit(1).execute()

        await _run_db_op("dashboard adapter healthcheck categories", _probe_categories)
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "list_ticket_categories_for_dashboard",
    "build_ticket_detail_dashboard_payload",
    "build_staff_queue_dashboard_payload",
    "build_member_profile_dashboard_payload",
    "build_user_dashboard_payload",
    "build_ticket_activity_timeline_payload",
    "dashboard_adapter_healthcheck",
]