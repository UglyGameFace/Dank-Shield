from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..globals import get_supabase, now_utc, reset_supabase
from .repository import find_open_ticket_for_owner, list_tickets_for_owner


# ============================================================
# tickets_new/member_context_service.py
# ------------------------------------------------------------
# Purpose:
# - reusable member intelligence layer for tickets / verification / dashboard
# - centralize reads from:
#     - guild_members
#     - member_joins
#     - member_events
#     - tickets (via repository)
# - return normalized member context snapshots
# - keep the rest of the bot from needing raw table knowledge
# ============================================================

GUILD_MEMBERS_TABLE = "guild_members"
MEMBER_JOINS_TABLE = "member_joins"
MEMBER_EVENTS_TABLE = "member_events"


# ============================================================
# Small helpers
# ============================================================

def _ctx_debug(msg: str) -> None:
    try:
        print(f"🧠 member_context_service {msg}")
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

def _normalize_guild_member_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": _as_str_id(row.get("guild_id")),
        "user_id": _as_str_id(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "display_name": _clean_text(row.get("display_name")),
        "nickname": _clean_text(row.get("nickname")),
        "avatar_url": _clean_text(row.get("avatar_url")),
        "avatar_hash": _clean_text(row.get("avatar_hash")),
        "role_ids": [str(x) for x in _safe_list(row.get("role_ids")) if _clean_text(x)],
        "role_names": [str(x) for x in _safe_list(row.get("role_names")) if _clean_text(x)],
        "roles": _safe_list(row.get("roles")),
        "highest_role_id": _clean_text(row.get("highest_role_id")),
        "highest_role_name": _clean_text(row.get("highest_role_name")),
        "top_role": _clean_text(row.get("top_role")),
        "in_guild": _boolish(row.get("in_guild"), True),
        "has_any_role": _boolish(row.get("has_any_role"), False),
        "has_unverified": _boolish(row.get("has_unverified"), False),
        "has_verified_role": _boolish(row.get("has_verified_role"), False),
        "has_staff_role": _boolish(row.get("has_staff_role"), False),
        "has_secondary_verified_role": _boolish(row.get("has_secondary_verified_role"), False),
        "has_cosmetic_only": _boolish(row.get("has_cosmetic_only"), False),
        "is_bot": _boolish(row.get("is_bot"), False),
        "data_health": _clean_text(row.get("data_health")) or "unknown",
        "role_state": _clean_text(row.get("role_state")) or "unknown",
        "role_state_reason": _clean_text(row.get("role_state_reason")),
        "joined_at": _normalize_ts(row.get("joined_at")),
        "first_seen_at": _normalize_ts(row.get("first_seen_at")),
        "last_seen_at": _normalize_ts(row.get("last_seen_at")),
        "left_at": _normalize_ts(row.get("left_at")),
        "rejoined_at": _normalize_ts(row.get("rejoined_at")),
        "times_joined": _as_int(row.get("times_joined"), 0),
        "times_left": _as_int(row.get("times_left"), 0),
        "created_at": _normalize_ts(row.get("created_at")),
        "updated_at": _normalize_ts(row.get("updated_at")),
        "synced_at": _normalize_ts(row.get("synced_at")),
        "previous_usernames": [str(x) for x in _safe_list(row.get("previous_usernames")) if _clean_text(x)],
        "previous_display_names": [str(x) for x in _safe_list(row.get("previous_display_names")) if _clean_text(x)],
        "previous_nicknames": [str(x) for x in _safe_list(row.get("previous_nicknames")) if _clean_text(x)],
        "last_seen_username": _clean_text(row.get("last_seen_username")),
        "last_seen_display_name": _clean_text(row.get("last_seen_display_name")),
        "last_seen_nickname": _clean_text(row.get("last_seen_nickname")),
        "invited_by": _clean_text(row.get("invited_by")),
        "invited_by_name": _clean_text(row.get("invited_by_name")),
        "invite_code": _clean_text(row.get("invite_code")),
        "vouched_by": _clean_text(row.get("vouched_by")),
        "vouched_by_name": _clean_text(row.get("vouched_by_name")),
        "approved_by": _clean_text(row.get("approved_by")),
        "approved_by_name": _clean_text(row.get("approved_by_name")),
        "verification_ticket_id": _clean_text(row.get("verification_ticket_id")),
        "source_ticket_id": _clean_text(row.get("source_ticket_id")),
        "entry_method": _clean_text(row.get("entry_method")),
        "verification_source": _clean_text(row.get("verification_source")),
        "entry_reason": _clean_text(row.get("entry_reason")),
        "approval_reason": _clean_text(row.get("approval_reason")),
        "raw": dict(row),
    }


def _normalize_member_join_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _as_str_id(row.get("guild_id")),
        "user_id": _as_str_id(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "display_name": _clean_text(row.get("display_name")),
        "avatar_url": _clean_text(row.get("avatar_url")),
        "joined_at": _normalize_ts(row.get("joined_at")),
        "invited_by": _clean_text(row.get("invited_by")),
        "invited_by_name": _clean_text(row.get("invited_by_name")),
        "invite_code": _clean_text(row.get("invite_code")),
        "entry_method": _clean_text(row.get("entry_method")),
        "verification_source": _clean_text(row.get("verification_source")),
        "vouched_by": _clean_text(row.get("vouched_by")),
        "vouched_by_name": _clean_text(row.get("vouched_by_name")),
        "approved_by": _clean_text(row.get("approved_by")),
        "approved_by_name": _clean_text(row.get("approved_by_name")),
        "source_ticket_id": _clean_text(row.get("source_ticket_id")),
        "join_note": _clean_text(row.get("join_note")),
        "raw": dict(row),
    }


def _normalize_member_event_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _as_str_id(row.get("guild_id")),
        "user_id": _as_str_id(row.get("user_id")),
        "actor_id": _clean_text(row.get("actor_id")),
        "actor_name": _clean_text(row.get("actor_name")),
        "event_type": _clean_text(row.get("event_type")) or "unknown",
        "title": _clean_text(row.get("title")),
        "reason": _clean_text(row.get("reason")),
        "metadata": _safe_meta(row.get("metadata")),
        "created_at": _normalize_ts(row.get("created_at")),
        "raw": dict(row),
    }


def _normalize_ticket_row_light(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _clean_text(row.get("id")),
        "guild_id": _clean_text(row.get("guild_id")),
        "user_id": _clean_text(row.get("user_id")),
        "username": _clean_text(row.get("username")),
        "title": _clean_text(row.get("title")),
        "category": _clean_text(row.get("category")),
        "status": _clean_text(row.get("status")) or "unknown",
        "priority": _clean_text(row.get("priority")) or "medium",
        "claimed_by": _clean_text(row.get("claimed_by")),
        "assigned_to": _clean_text(row.get("assigned_to")),
        "closed_by": _clean_text(row.get("closed_by")),
        "closed_reason": _clean_text(row.get("closed_reason")),
        "channel_id": _clean_text(row.get("channel_id")),
        "channel_name": _clean_text(row.get("channel_name")),
        "discord_thread_id": _clean_text(row.get("discord_thread_id")),
        "source": _clean_text(row.get("source")),
        "ticket_number": row.get("ticket_number"),
        "is_ghost": _boolish(row.get("is_ghost"), False),
        "created_at": _normalize_ts(row.get("created_at")),
        "updated_at": _normalize_ts(row.get("updated_at")),
        "closed_at": _normalize_ts(row.get("closed_at")),
        "reopened_at": _normalize_ts(row.get("reopened_at")),
        "matched_category_id": _clean_text(row.get("matched_category_id")),
        "matched_category_name": _clean_text(row.get("matched_category_name")),
        "matched_category_slug": _clean_text(row.get("matched_category_slug")),
        "matched_intake_type": _clean_text(row.get("matched_intake_type")),
        "matched_category_reason": _clean_text(row.get("matched_category_reason")),
        "matched_category_score": _as_int(row.get("matched_category_score"), 0),
        "raw": dict(row),
    }


# ============================================================
# Low-level readers
# ============================================================

async def get_guild_member_row(
    *,
    guild_id: int | str,
    user_id: int | str,
) -> Optional[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return None

    sb = _sb()
    if sb is None:
        return None

    try:
        def _read_sync():
            return (
                sb.table(GUILD_MEMBERS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .eq("user_id", uid)
                .limit(1)
                .execute()
            )

        resp = await _run_db_op("get guild member row", _read_sync)
        rows = getattr(resp, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return _normalize_guild_member_row(rows[0])
    except Exception as e:
        print(f"⚠️ member_context_service.get_guild_member_row failed: {repr(e)}")

    return None


async def list_member_join_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 10), 100))

    try:
        def _read_sync():
            return (
                sb.table(MEMBER_JOINS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .eq("user_id", uid)
                .order("joined_at", desc=True)
                .limit(max_limit)
                .execute()
            )

        resp = await _run_db_op("list member join rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_member_join_row(row))
        return out
    except Exception as e:
        print(f"⚠️ member_context_service.list_member_join_rows failed: {repr(e)}")
        return []


async def list_member_event_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 20,
    event_types: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 20), 200))
    clean_types = [str(x).strip() for x in (event_types or []) if _clean_text(x)]

    try:
        def _read_sync():
            query = (
                sb.table(MEMBER_EVENTS_TABLE)
                .select("*")
                .eq("guild_id", gid)
                .eq("user_id", uid)
            )

            if len(clean_types) == 1:
                query = query.eq("event_type", clean_types[0])
            elif len(clean_types) > 1:
                query = query.in_("event_type", clean_types)

            return query.order("created_at", desc=True).limit(max_limit).execute()

        resp = await _run_db_op("list member event rows", _read_sync)
        rows = getattr(resp, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_member_event_row(row))
        return out
    except Exception as e:
        print(f"⚠️ member_context_service.list_member_event_rows failed: {repr(e)}")
        return []


async def list_member_ticket_rows(
    *,
    guild_id: int | str,
    user_id: int | str,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return []

    try:
        rows = await list_tickets_for_owner(
            guild_id=gid,
            owner_id=uid,
            limit=limit,
        )
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_normalize_ticket_row_light(row))
        return out
    except Exception as e:
        print(f"⚠️ member_context_service.list_member_ticket_rows failed: {repr(e)}")
        return []


# ============================================================
# Derived summaries
# ============================================================

def _derive_access_label(member_row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(member_row, dict):
        return "Not Synced Yet"

    if _boolish(member_row.get("has_staff_role"), False):
        return "Staff"
    if _boolish(member_row.get("has_verified_role"), False):
        return "Verified"
    if _boolish(member_row.get("has_unverified"), False):
        return "Limited"

    role_state = str(member_row.get("role_state") or "").strip().lower()
    if role_state == "staff_ok":
        return "Staff"
    if role_state == "verified_ok":
        return "Verified"
    if role_state == "unverified_only":
        return "Limited"

    return "Not Synced Yet"


def _derive_verification_label(member_row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(member_row, dict):
        return "Not Synced Yet"

    if _boolish(member_row.get("has_staff_role"), False):
        return "Staff"
    if _boolish(member_row.get("has_verified_role"), False):
        return "Verified"
    if _boolish(member_row.get("has_unverified"), False):
        return "Pending Verification"

    role_state = str(member_row.get("role_state") or "").strip().lower()

    if role_state == "staff_ok":
        return "Staff"
    if role_state == "verified_ok":
        return "Verified"
    if role_state == "unverified_only":
        return "Pending Verification"
    if role_state in {
        "verified_conflict",
        "staff_conflict",
        "missing_verified_role",
        "missing_unverified",
    }:
        return "Needs Review"
    if role_state in {"left_guild"}:
        return "Left Guild"

    return "Not Synced Yet"


def _build_relationships(
    member_row: Optional[Dict[str, Any]],
    join_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    latest_join = join_rows[0] if join_rows else {}

    def _pick(*values: Any) -> Optional[str]:
        for value in values:
            text = _clean_text(value)
            if text:
                return text
        return None

    return {
        "entry_method": _pick(
            member_row.get("entry_method") if isinstance(member_row, dict) else None,
            latest_join.get("entry_method") if isinstance(latest_join, dict) else None,
        ),
        "verification_source": _pick(
            member_row.get("verification_source") if isinstance(member_row, dict) else None,
            latest_join.get("verification_source") if isinstance(latest_join, dict) else None,
        ),
        "entry_reason": _pick(
            member_row.get("entry_reason") if isinstance(member_row, dict) else None,
            latest_join.get("join_note") if isinstance(latest_join, dict) else None,
        ),
        "approval_reason": _pick(
            member_row.get("approval_reason") if isinstance(member_row, dict) else None,
        ),
        "invited_by": _pick(
            member_row.get("invited_by") if isinstance(member_row, dict) else None,
            latest_join.get("invited_by") if isinstance(latest_join, dict) else None,
        ),
        "invited_by_name": _pick(
            member_row.get("invited_by_name") if isinstance(member_row, dict) else None,
            latest_join.get("invited_by_name") if isinstance(latest_join, dict) else None,
        ),
        "invite_code": _pick(
            member_row.get("invite_code") if isinstance(member_row, dict) else None,
            latest_join.get("invite_code") if isinstance(latest_join, dict) else None,
        ),
        "vouched_by": _pick(
            member_row.get("vouched_by") if isinstance(member_row, dict) else None,
            latest_join.get("vouched_by") if isinstance(latest_join, dict) else None,
        ),
        "vouched_by_name": _pick(
            member_row.get("vouched_by_name") if isinstance(member_row, dict) else None,
            latest_join.get("vouched_by_name") if isinstance(latest_join, dict) else None,
        ),
        "approved_by": _pick(
            member_row.get("approved_by") if isinstance(member_row, dict) else None,
            latest_join.get("approved_by") if isinstance(latest_join, dict) else None,
        ),
        "approved_by_name": _pick(
            member_row.get("approved_by_name") if isinstance(member_row, dict) else None,
            latest_join.get("approved_by_name") if isinstance(latest_join, dict) else None,
        ),
        "verification_ticket_id": _pick(
            member_row.get("verification_ticket_id") if isinstance(member_row, dict) else None,
        ),
        "source_ticket_id": _pick(
            member_row.get("source_ticket_id") if isinstance(member_row, dict) else None,
            latest_join.get("source_ticket_id") if isinstance(latest_join, dict) else None,
        ),
    }


def _build_name_history(
    member_row: Optional[Dict[str, Any]],
    join_rows: Sequence[Dict[str, Any]],
    ticket_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    usernames: List[Any] = []
    display_names: List[Any] = []
    nicknames: List[Any] = []

    if isinstance(member_row, dict):
        usernames.extend(
            [
                member_row.get("username"),
                member_row.get("last_seen_username"),
                *(_safe_list(member_row.get("previous_usernames"))),
            ]
        )
        display_names.extend(
            [
                member_row.get("display_name"),
                member_row.get("last_seen_display_name"),
                *(_safe_list(member_row.get("previous_display_names"))),
            ]
        )
        nicknames.extend(
            [
                member_row.get("nickname"),
                member_row.get("last_seen_nickname"),
                *(_safe_list(member_row.get("previous_nicknames"))),
            ]
        )

    for row in join_rows:
        usernames.append(row.get("username"))
        display_names.append(row.get("display_name"))

    for row in ticket_rows:
        usernames.append(row.get("username"))

    username_list = _sort_unique_texts(usernames, limit=50)
    display_name_list = _sort_unique_texts(display_names, limit=50)
    nickname_list = _sort_unique_texts(nicknames, limit=50)
    all_names = _sort_unique_texts(
        [*username_list, *display_name_list, *nickname_list],
        limit=100,
    )

    return {
        "usernames": username_list,
        "display_names": display_name_list,
        "nicknames": nickname_list,
        "all_names": all_names,
    }


def _build_join_summary(
    member_row: Optional[Dict[str, Any]],
    join_rows: Sequence[Dict[str, Any]],
    event_rows: Sequence[Dict[str, Any]],
    relationships: Dict[str, Any],
) -> Dict[str, Any]:
    latest_join = join_rows[0] if join_rows else {}
    earliest_join = join_rows[-1] if join_rows else {}

    member_times_joined = _as_int(member_row.get("times_joined"), 0) if isinstance(member_row, dict) else 0
    member_times_left = _as_int(member_row.get("times_left"), 0) if isinstance(member_row, dict) else 0

    leave_events = 0
    for row in event_rows:
        event_type = str(row.get("event_type") or "").strip().lower()
        if event_type in {"leave", "left", "left_guild", "member_left", "departed"}:
            leave_events += 1

    times_joined = member_times_joined or len(join_rows) or (1 if isinstance(member_row, dict) else 0)
    times_left = member_times_left or leave_events

    first_joined_at = _latest_ts(
        earliest_join.get("joined_at") if isinstance(earliest_join, dict) else None,
        member_row.get("first_seen_at") if isinstance(member_row, dict) else None,
    )
    latest_joined_at = _latest_ts(
        latest_join.get("joined_at") if isinstance(latest_join, dict) else None,
        member_row.get("rejoined_at") if isinstance(member_row, dict) else None,
        member_row.get("joined_at") if isinstance(member_row, dict) else None,
    )

    return {
        "joined_at": member_row.get("joined_at") if isinstance(member_row, dict) else None,
        "first_joined_at": first_joined_at,
        "latest_joined_at": latest_joined_at,
        "first_seen_at": member_row.get("first_seen_at") if isinstance(member_row, dict) else None,
        "last_seen_at": member_row.get("last_seen_at") if isinstance(member_row, dict) else None,
        "left_at": member_row.get("left_at") if isinstance(member_row, dict) else None,
        "rejoined_at": member_row.get("rejoined_at") if isinstance(member_row, dict) else None,
        "times_joined": int(times_joined),
        "times_left": int(times_left),
        "in_guild": _boolish(member_row.get("in_guild"), True) if isinstance(member_row, dict) else True,
        "latest_entry_method": relationships.get("entry_method"),
        "latest_verification_source": relationships.get("verification_source"),
        "latest_invite_code": relationships.get("invite_code"),
        "latest_invited_by_name": relationships.get("invited_by_name"),
        "latest_vouched_by_name": relationships.get("vouched_by_name"),
        "latest_approved_by_name": relationships.get("approved_by_name"),
        "latest_join_note": _clean_text(latest_join.get("join_note")) if isinstance(latest_join, dict) else None,
        "recent_join_count": len(join_rows),
    }


def _normalized_ticket_bucket(ticket_row: Dict[str, Any]) -> str:
    intake = str(ticket_row.get("matched_intake_type") or "").strip().lower()
    if intake:
        return intake

    category = str(ticket_row.get("matched_category_slug") or ticket_row.get("category") or "").strip().lower()

    if category in {"verification", "verification_issue", "verification-issue", "verify"}:
        return "verification"
    if category in {"appeal", "ban-appeal", "timeout-appeal"}:
        return "appeal"
    if category in {"report", "incident", "report-incident"}:
        return "report"
    if category in {"question", "support", "general-support", "general_support"}:
        return "question"
    if category in {"partnership", "partner", "collab"}:
        return "partnership"

    return category or "unknown"


def _build_ticket_history_summary(
    ticket_rows: Sequence[Dict[str, Any]],
    open_ticket: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    total = len(ticket_rows)
    status_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}

    ghost_count = 0
    latest_ticket_at: Optional[str] = None

    for row in ticket_rows:
        status = str(row.get("status") or "unknown").strip().lower()
        status_counts[status] = status_counts.get(status, 0) + 1

        bucket = _normalized_ticket_bucket(row)
        category_counts[bucket] = category_counts.get(bucket, 0) + 1

        if _boolish(row.get("is_ghost"), False):
            ghost_count += 1

        latest_ticket_at = _latest_ts(latest_ticket_at, row.get("updated_at"), row.get("created_at"))

    open_count = status_counts.get("open", 0)
    claimed_count = status_counts.get("claimed", 0)
    closed_count = status_counts.get("closed", 0)
    deleted_count = status_counts.get("deleted", 0)

    return {
        "total": total,
        "open": open_count,
        "claimed": claimed_count,
        "closed": closed_count,
        "deleted": deleted_count,
        "ghost": ghost_count,
        "verification": category_counts.get("verification", 0),
        "appeal": category_counts.get("appeal", 0),
        "report": category_counts.get("report", 0),
        "question": category_counts.get("question", 0),
        "partnership": category_counts.get("partnership", 0),
        "latest_ticket_at": latest_ticket_at,
        "status_counts": status_counts,
        "category_counts": category_counts,
        "open_ticket_id": _clean_text(open_ticket.get("id")) if isinstance(open_ticket, dict) else None,
        "open_ticket_channel_id": _clean_text(open_ticket.get("channel_id")) if isinstance(open_ticket, dict) else None,
    }


def _build_event_summary(event_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    type_counts: Dict[str, int] = {}
    latest_event_at: Optional[str] = None

    moderation_count = 0
    verification_count = 0

    for row in event_rows:
        event_type = str(row.get("event_type") or "unknown").strip().lower()
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        latest_event_at = _latest_ts(latest_event_at, row.get("created_at"))

        if any(token in event_type for token in ("kick", "ban", "timeout", "warn", "mute")):
            moderation_count += 1
        if any(token in event_type for token in ("verify", "verification", "approve", "deny", "resubmit")):
            verification_count += 1

    latest_event = event_rows[0] if event_rows else None

    return {
        "total": len(event_rows),
        "latest_event_at": latest_event_at,
        "latest_event_type": latest_event.get("event_type") if isinstance(latest_event, dict) else None,
        "latest_event_title": latest_event.get("title") if isinstance(latest_event, dict) else None,
        "type_counts": type_counts,
        "moderation_count": moderation_count,
        "verification_count": verification_count,
    }


def _build_dashboard_block(
    member_row: Optional[Dict[str, Any]],
    relationships: Dict[str, Any],
    ticket_summary: Dict[str, Any],
    join_summary: Dict[str, Any],
) -> Dict[str, Any]:
    display_name = None
    username = None
    avatar_url = None
    discord_id = None

    if isinstance(member_row, dict):
        display_name = _clean_text(member_row.get("display_name")) or _clean_text(member_row.get("nickname"))
        username = _clean_text(member_row.get("username"))
        avatar_url = _clean_text(member_row.get("avatar_url"))
        discord_id = _clean_text(member_row.get("user_id"))

    return {
        "discord_id": discord_id,
        "display_name": display_name,
        "username": username,
        "avatar_url": avatar_url,
        "joined_at": join_summary.get("joined_at") or join_summary.get("latest_joined_at"),
        "entry_method": relationships.get("entry_method"),
        "invite_code": relationships.get("invite_code"),
        "inviter_name": relationships.get("invited_by_name"),
        "inviter_id": relationships.get("invited_by"),
        "vouched_by_name": relationships.get("vouched_by_name"),
        "approved_by_name": relationships.get("approved_by_name"),
        "role_state": member_row.get("role_state") if isinstance(member_row, dict) else "unknown",
        "role_state_reason": member_row.get("role_state_reason") if isinstance(member_row, dict) else None,
        "access_label": _derive_access_label(member_row),
        "verification_label": _derive_verification_label(member_row),
        "ticket_total": ticket_summary.get("total", 0),
        "ticket_open": ticket_summary.get("open", 0) + ticket_summary.get("claimed", 0),
    }


# ============================================================
# Public higher-level helpers
# ============================================================

async def get_member_name_history(
    *,
    guild_id: int | str,
    user_id: int | str,
    member_row: Optional[Dict[str, Any]] = None,
    join_rows: Optional[Sequence[Dict[str, Any]]] = None,
    ticket_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    row = member_row or await get_guild_member_row(guild_id=guild_id, user_id=user_id)
    joins = list(join_rows) if join_rows is not None else await list_member_join_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=10,
    )
    tickets = list(ticket_rows) if ticket_rows is not None else await list_member_ticket_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=25,
    )

    return _build_name_history(row, joins, tickets)


async def get_member_join_history_summary(
    *,
    guild_id: int | str,
    user_id: int | str,
    member_row: Optional[Dict[str, Any]] = None,
    join_rows: Optional[Sequence[Dict[str, Any]]] = None,
    event_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    row = member_row or await get_guild_member_row(guild_id=guild_id, user_id=user_id)
    joins = list(join_rows) if join_rows is not None else await list_member_join_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=10,
    )
    events = list(event_rows) if event_rows is not None else await list_member_event_rows(
        guild_id=guild_id,
        user_id=user_id,
        limit=20,
    )

    relationships = _build_relationships(row, joins)
    return _build_join_summary(row, joins, events, relationships)


async def get_member_ticket_history_summary(
    *,
    guild_id: int | str,
    user_id: int | str,
    ticket_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    gid = _as_str_id(guild_id)
    uid = _as_str_id(user_id)
    if not gid or not uid:
        return {
            "total": 0,
            "open": 0,
            "claimed": 0,
            "closed": 0,
            "deleted": 0,
            "ghost": 0,
            "verification": 0,
            "appeal": 0,
            "report": 0,
            "question": 0,
            "partnership": 0,
            "latest_ticket_at": None,
            "status_counts": {},
            "category_counts": {},
            "open_ticket_id": None,
            "open_ticket_channel_id": None,
        }

    tickets = list(ticket_rows) if ticket_rows is not None else await list_member_ticket_rows(
        guild_id=gid,
        user_id=uid,
        limit=25,
    )

    try:
        open_ticket_raw = await find_open_ticket_for_owner(
            guild_id=gid,
            owner_id=uid,
            category=None,
        )
        open_ticket = _normalize_ticket_row_light(open_ticket_raw) if isinstance(open_ticket_raw, dict) else None
    except Exception:
        open_ticket = None

    return _build_ticket_history_summary(tickets, open_ticket)


async def get_member_context_snapshot(
    *,
    guild_id: int | str,
    user_id: int | str,
    ticket_limit: int = 25,
    event_limit: int = 15,
    join_limit: int = 10,
    include_recent_tickets: bool = True,
    include_recent_events: bool = True,
    include_recent_joins: bool = True,
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

    member_row = await get_guild_member_row(guild_id=gid, user_id=uid)

    join_rows: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    ticket_rows: List[Dict[str, Any]] = []

    if include_recent_joins or member_row is None:
        join_rows = await list_member_join_rows(
            guild_id=gid,
            user_id=uid,
            limit=join_limit,
        )

    if include_recent_events:
        event_rows = await list_member_event_rows(
            guild_id=gid,
            user_id=uid,
            limit=event_limit,
        )

    if include_recent_tickets:
        ticket_rows = await list_member_ticket_rows(
            guild_id=gid,
            user_id=uid,
            limit=ticket_limit,
        )

    try:
        open_ticket_raw = await find_open_ticket_for_owner(
            guild_id=gid,
            owner_id=uid,
            category=None,
        )
        open_ticket = _normalize_ticket_row_light(open_ticket_raw) if isinstance(open_ticket_raw, dict) else None
    except Exception as e:
        print(f"⚠️ member_context_service.get_member_context_snapshot open-ticket lookup failed: {repr(e)}")
        open_ticket = None

    relationships = _build_relationships(member_row, join_rows)
    name_history = _build_name_history(member_row, join_rows, ticket_rows)
    join_summary = _build_join_summary(member_row, join_rows, event_rows, relationships)
    ticket_summary = _build_ticket_history_summary(ticket_rows, open_ticket)
    event_summary = _build_event_summary(event_rows)
    dashboard = _build_dashboard_block(member_row, relationships, ticket_summary, join_summary)

    snapshot: Dict[str, Any] = {
        "ok": True,
        "guild_id": gid,
        "user_id": uid,
        "generated_at": _now_iso(),
        "member": member_row or {
            "guild_id": gid,
            "user_id": uid,
            "username": None,
            "display_name": None,
            "nickname": None,
            "avatar_url": None,
            "role_names": [],
            "role_ids": [],
            "roles": [],
            "in_guild": True,
            "has_unverified": False,
            "has_verified_role": False,
            "has_staff_role": False,
            "role_state": "unknown",
            "role_state_reason": None,
            "joined_at": None,
            "first_seen_at": None,
            "last_seen_at": None,
            "left_at": None,
            "rejoined_at": None,
            "times_joined": 0,
            "times_left": 0,
            "entry_method": None,
            "verification_source": None,
            "raw": {},
        },
        "relationships": relationships,
        "name_history": name_history,
        "join_summary": join_summary,
        "ticket_summary": ticket_summary,
        "event_summary": event_summary,
        "open_ticket": open_ticket,
        "recent_joins": join_rows if include_recent_joins else [],
        "recent_events": event_rows if include_recent_events else [],
        "recent_tickets": ticket_rows if include_recent_tickets else [],
        "dashboard": dashboard,
    }

    _ctx_debug(
        f"snapshot guild={gid} user={uid} "
        f"tickets={len(ticket_rows)} events={len(event_rows)} joins={len(join_rows)} "
        f"open_ticket={'yes' if open_ticket else 'no'}"
    )

    return snapshot


# ============================================================
# Diagnostics
# ============================================================

async def member_context_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "supabase": False,
        "tables": {
            "guild_members": GUILD_MEMBERS_TABLE,
            "member_joins": MEMBER_JOINS_TABLE,
            "member_events": MEMBER_EVENTS_TABLE,
        },
        "error": None,
    }

    sb = _sb()
    if sb is None:
        out["error"] = "supabase unavailable"
        return out

    out["supabase"] = True

    try:
        def _probe_members():
            return sb.table(GUILD_MEMBERS_TABLE).select("*").limit(1).execute()

        def _probe_joins():
            return sb.table(MEMBER_JOINS_TABLE).select("*").limit(1).execute()

        def _probe_events():
            return sb.table(MEMBER_EVENTS_TABLE).select("*").limit(1).execute()

        await _run_db_op("member context healthcheck members", _probe_members)
        await _run_db_op("member context healthcheck joins", _probe_joins)
        await _run_db_op("member context healthcheck events", _probe_events)

        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "GUILD_MEMBERS_TABLE",
    "MEMBER_JOINS_TABLE",
    "MEMBER_EVENTS_TABLE",
    "get_guild_member_row",
    "list_member_join_rows",
    "list_member_event_rows",
    "list_member_ticket_rows",
    "get_member_name_history",
    "get_member_join_history_summary",
    "get_member_ticket_history_summary",
    "get_member_context_snapshot",
    "member_context_healthcheck",
]