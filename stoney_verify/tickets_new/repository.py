from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

import discord

from ..globals import get_supabase, now_utc, reset_supabase

# ============================================================
# tickets_new/repository.py
# ------------------------------------------------------------
# Purpose:
# - centralize tickets-table CRUD
# - centralize ticket notes CRUD
# - centralize ticket messages reads/writes
# - centralize ticket activity-feed reads/writes
# - centralize member_joins inserts
# - centralize ticket-user linking helpers
# - handle mixed old/new schema safely
# - support both channel_id and discord_thread_id lookup paths
# - stay compatible with tickets_new/service.py and dashboard consumers
# ============================================================

TICKETS_TABLE = "tickets"
TICKET_NOTES_TABLE = "ticket_notes"
TICKET_MESSAGES_TABLE = "ticket_messages"
ACTIVITY_FEED_EVENTS_TABLE = "activity_feed_events"
LEGACY_TICKET_INTERNAL_NOTES_TABLE = "ticket_internal_notes"
MEMBER_JOINS_TABLE = "member_joins"

# Includes current real optional columns plus legacy/future-safe ones
# referenced elsewhere in the project.
_OPTIONAL_TICKET_COLUMNS: Set[str] = {
    "ticket_number",
    "channel_name",
    "assigned_to",
    "reopened_at",
    "sla_deadline",
    "is_ghost",
    "deleted_at",
    "deleted_by",
    "transcript_url",
    "transcript_message_id",
    "transcript_channel_id",
    "source",
    "category_id",
    "category_override",
    "category_set_by",
    "category_set_at",
    "matched_category_id",
    "matched_category_name",
    "matched_category_slug",
    "matched_intake_type",
    "matched_category_reason",
    "matched_category_score",
    "last_activity_at",
    "last_message_id",
    "panel_message_id",
    "webhook_url",
    "webhook_id",
    "decision",
    "reopened_by",
    "reopen_reason",
    "close_reason",
    "delete_reason",
    "owner_id",
    "owner_name",
    "requester_id",
    "requester_name",
    "claimed_by_name",
    "assigned_to_name",
    "closed_by_name",
    "deleted_by_name",
}
_OPTIONAL_COLUMN_SUPPORT: Dict[str, Optional[bool]] = {
    col: None for col in _OPTIONAL_TICKET_COLUMNS
}

_TICKET_NOTES_BACKEND: Optional[str] = None
_PINNED_NOTE_PREFIX = "[PINNED] "

_OPTIONAL_ACTIVITY_EVENT_COLUMNS: Set[str] = {
    "meta",
    "search_text",
    "ticket_message_id",
    "related_table",
    "related_id",
    "metadata",
}
_ACTIVITY_EVENT_UNSUPPORTED_COLS: Set[str] = set()

_OPTIONAL_MEMBER_JOIN_COLUMNS: Set[str] = {
    "created_at",
    "username",
    "joined_at",
}
_MEMBER_JOIN_UNSUPPORTED_COLS: Set[str] = set()

# Columns we should never blindly write back from a fetched row.
_ROW_ONLY_TICKET_COLUMNS: Set[str] = {
    "id",
}

# Canonical ticket columns that are safe to patch directly.
_BASE_TICKET_WRITE_COLUMNS: Set[str] = {
    "guild_id",
    "user_id",
    "username",
    "title",
    "category",
    "status",
    "priority",
    "claimed_by",
    "closed_by",
    "closed_reason",
    "initial_message",
    "ai_category_confidence",
    "mod_suggestion",
    "mod_suggestion_confidence",
    "created_at",
    "updated_at",
    "closed_at",
    "discord_thread_id",
    "channel_id",
    "channel_name",
}

_ALLOWED_TICKET_WRITE_COLUMNS: Set[str] = (
    _BASE_TICKET_WRITE_COLUMNS | _OPTIONAL_TICKET_COLUMNS
)

_VALID_TICKET_STATUSES: Set[str] = {
    "open",
    "claimed",
    "closed",
    "deleted",
}
_VALID_TICKET_PRIORITIES: Set[str] = {
    "low",
    "medium",
    "high",
    "urgent",
}


# ============================================================
# Small helpers
# ============================================================

def _repo_debug(msg: str) -> None:
    try:
        print(f"🧩 tickets_repository {msg}")
    except Exception:
        pass


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


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


def _clean_text(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def _safe_meta(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalize_ticket_status(value: Any) -> str:
    raw = (_clean_text(value) or "open").lower()
    if raw in _VALID_TICKET_STATUSES:
        return raw
    if raw in {"reopened", "active"}:
        return "open"
    return "open"


def _normalize_ticket_priority(value: Any) -> str:
    raw = (_clean_text(value) or "medium").lower()
    if raw in _VALID_TICKET_PRIORITIES:
        return raw
    return "medium"


def _normalize_ticket_category(value: Any) -> str:
    return (_clean_text(value) or "verification_issue").lower()


def _normalize_ticket_row(row: Any) -> Optional[Dict[str, Any]]:
    try:
        if not isinstance(row, dict):
            return None
        out = dict(row)

        user_id = _as_str_id(out.get("user_id"))
        username = _clean_text(out.get("username"))
        out["owner_id"] = _as_str_id(out.get("owner_id")) or user_id
        out["requester_id"] = _as_str_id(out.get("requester_id")) or user_id
        out["owner_name"] = _clean_text(out.get("owner_name")) or username
        out["requester_name"] = _clean_text(out.get("requester_name")) or username

        out["status"] = _normalize_ticket_status(out.get("status"))
        out["priority"] = _normalize_ticket_priority(out.get("priority"))
        out["category"] = _normalize_ticket_category(out.get("category"))

        if out["status"] == "open":
            if _as_str_id(out.get("assigned_to")) or _as_str_id(out.get("claimed_by")):
                out["status"] = "claimed"

        return out
    except Exception:
        return None


def _normalize_note_row(row: Any) -> Optional[Dict[str, Any]]:
    try:
        if not isinstance(row, dict):
            return None
        return dict(row)
    except Exception:
        return None


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


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _normalize_note_text(value: Any, limit: int = 4000) -> str:
    try:
        text = _safe_str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
        return text[:limit]
    except Exception:
        return ""


def _normalize_message_text(value: Any, limit: int = 6000) -> str:
    try:
        text = _safe_str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
        return text[:limit]
    except Exception:
        return ""


def _normalize_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _ticket_internal_notes_table_name() -> str:
    try:
        from ..globals import TICKET_INTERNAL_NOTES_TABLE  # type: ignore
        name = _safe_str(TICKET_INTERNAL_NOTES_TABLE).strip()
        return name or LEGACY_TICKET_INTERNAL_NOTES_TABLE
    except Exception:
        return LEGACY_TICKET_INTERNAL_NOTES_TABLE


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _preserve_existing_created_at(
    existing: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    out = dict(payload or {})
    if isinstance(existing, dict):
        existing_created = existing.get("created_at")
        if existing_created:
            out["created_at"] = existing_created
    return out


def _merge_existing_ticket_identity(
    existing: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    out = dict(payload or {})
    if not isinstance(existing, dict):
        return out

    for key in (
        "guild_id",
        "user_id",
        "owner_id",
        "requester_id",
        "username",
        "owner_name",
        "requester_name",
        "created_at",
    ):
        if out.get(key) is None and existing.get(key) is not None:
            out[key] = existing.get(key)

    if out.get("ticket_number") is None and existing.get("ticket_number") is not None:
        out["ticket_number"] = existing.get("ticket_number")

    if out.get("channel_name") is None and existing.get("channel_name") is not None:
        out["channel_name"] = existing.get("channel_name")

    return out


def _owner_id_from_row(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    return (
        _as_str_id(row.get("user_id"))
        or _as_str_id(row.get("owner_id"))
        or _as_str_id(row.get("requester_id"))
    )


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "unknown"
    try:
        return _normalize_ticket_status(row.get("status"))
    except Exception:
        return "unknown"


def _ticket_claimed_by_id(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("assigned_to", "claimed_by"):
        try:
            value = int(str(row.get(key) or "0") or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _clean_ticket_payload(
    payload: Dict[str, Any],
    *,
    include_created_at: bool = False,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if key in _ROW_ONLY_TICKET_COLUMNS:
            continue
        if key not in _ALLOWED_TICKET_WRITE_COLUMNS:
            continue
        if key == "created_at" and not include_created_at:
            continue
        out[key] = value

    if "status" in out:
        out["status"] = _normalize_ticket_status(out.get("status"))
    if "priority" in out:
        out["priority"] = _normalize_ticket_priority(out.get("priority"))
    if "category" in out and out.get("category") is not None:
        out["category"] = _normalize_ticket_category(out.get("category"))

    return out


def _ticket_patch_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = _clean_ticket_payload(payload, include_created_at=False)
    out.pop("guild_id", None)
    out.pop("user_id", None)
    out.pop("username", None)
    out.pop("channel_id", None)
    out.pop("discord_thread_id", None)
    out["updated_at"] = _now_iso()
    return out


def _nonempty_ticket_patch(payload: Dict[str, Any]) -> bool:
    return any(k != "updated_at" for k in payload.keys())


def _result_rows(resp: Any) -> List[Dict[str, Any]]:
    try:
        rows = getattr(resp, "data", None) or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception:
        return []


# ============================================================
# Compatibility helpers used elsewhere in the project
# ============================================================

TICKET_NUM_RE = re.compile(r"^(?:ticket|closed)-(\d+)$", re.I)


def _extract_ticket_number_from_name(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    m = TICKET_NUM_RE.match(str(name).strip().lower())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_ticket_number_from_topic(channel: discord.TextChannel) -> Optional[int]:
    try:
        topic = channel.topic or ""
        m = re.search(r"(?:^|;)ticket_number=(\d+)(?:;|$)", topic)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


def _parse_owner_id_from_topic(channel: discord.TextChannel) -> Optional[int]:
    try:
        topic = channel.topic or ""
        m = re.search(r"(?:^|;)owner_id=(\d+)(?:;|$)", topic)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


def _title_for_ticket(owner: discord.abc.User, category: str, is_ghost: bool) -> str:
    base_name = (
        getattr(owner, "display_name", None)
        or getattr(owner, "name", None)
        or str(owner)
    )
    prefix = "[GHOST] " if is_ghost else ""
    return f"{prefix}{category.title()} - {base_name}"[:180]


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
# Schema-compat helpers
# ============================================================

def _missing_column_error(exc: Exception, column_name: str) -> bool:
    try:
        text = repr(exc)
        text_l = text.lower()
        col_l = str(column_name).lower()
        return (
            col_l in text_l
            and (
                "pgrst204" in text_l
                or "schema cache" in text_l
                or "column" in text_l
                or "does not exist" in text_l
            )
        )
    except Exception:
        return False


def _trigger_field_error(exc: Exception, field_name: str) -> bool:
    try:
        text = repr(exc).lower()
        return "42703" in text and str(field_name).lower() in text
    except Exception:
        return False


def _table_missing_error(exc: Exception, table_name: str) -> bool:
    text = repr(exc or "").lower()
    name = str(table_name or "").lower()
    return (
        name in text
        and (
            "pgrst204" in text
            or "42p01" in text
            or "does not exist" in text
            or "schema cache" in text
            or "relation" in text
            or "column" in text
        )
    )


def _strip_known_unsupported_columns(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for col, supported in _OPTIONAL_COLUMN_SUPPORT.items():
        if supported is False:
            out.pop(col, None)
    return out


def _detect_and_mark_unsupported_optional_columns(
    exc: Exception,
    payload: Dict[str, Any],
) -> List[str]:
    removed: List[str] = []
    for col in list(payload.keys()):
        if col not in _OPTIONAL_TICKET_COLUMNS:
            continue
        if _missing_column_error(exc, col):
            _OPTIONAL_COLUMN_SUPPORT[col] = False
            removed.append(col)
    return removed


async def _write_ticket_with_optional_fallback(
    *,
    op_name: str,
    payload: Dict[str, Any],
    writer,
) -> Any:
    current = _strip_known_unsupported_columns(payload)

    max_retries = len(_OPTIONAL_TICKET_COLUMNS)
    for attempt in range(1, max_retries + 2):
        snapshot = dict(current)
        try:
            return await _run_db_op(op_name, lambda: writer(snapshot))
        except Exception as e:
            removed = _detect_and_mark_unsupported_optional_columns(e, snapshot)
            if removed and attempt <= max_retries:
                current = _strip_known_unsupported_columns(payload)
                print(
                    f"⚠️ {op_name}: retrying without unsupported ticket columns {removed}"
                )
                continue
            raise


# ============================================================
# Raw sync DB functions
# ============================================================

def _insert_ticket_sync(payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None
    return sb.table(TICKETS_TABLE).insert(payload).execute()


def _upsert_ticket_sync(payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .upsert(payload, on_conflict="channel_id")
        .execute()
    )


def _select_ticket_by_id_sync(ticket_id: str):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("id", str(ticket_id))
        .limit(1)
        .execute()
    )


def _select_ticket_by_channel_id_sync(channel_id: str):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("channel_id", str(channel_id))
        .limit(1)
        .execute()
    )


def _select_ticket_by_any_channel_id_sync(channel_id: str):
    sb = _sb()
    if sb is None:
        return None

    res = (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("channel_id", str(channel_id))
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if rows:
        return res

    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("discord_thread_id", str(channel_id))
        .limit(1)
        .execute()
    )


def _select_open_ticket_for_owner_sync(
    guild_id: str,
    owner_id: str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
):
    sb = _sb()
    if sb is None:
        return None

    query = (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(owner_id))
    )

    status_list = list(statuses or ["open", "claimed"])
    if len(status_list) == 1:
        query = query.eq("status", str(status_list[0]))
    else:
        query = query.in_("status", status_list)

    if category:
        query = query.eq("category", str(category))

    return query.order("created_at", desc=True).limit(1).execute()


def _select_open_tickets_for_guild_sync(
    guild_id: str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
):
    sb = _sb()
    if sb is None:
        return None

    query = (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
    )

    status_list = list(statuses or ["open", "claimed"])
    if len(status_list) == 1:
        query = query.eq("status", str(status_list[0]))
    else:
        query = query.in_("status", status_list)

    if category:
        query = query.eq("category", str(category))

    return query.order("created_at", desc=False).execute()


def _select_tickets_for_owner_sync(
    guild_id: str,
    owner_id: str,
    limit: int,
):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(owner_id))
        .order("created_at", desc=True)
        .limit(int(limit))
        .execute()
    )


def _select_ticket_by_number_sync(guild_id: str, ticket_number: int):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("ticket_number", int(ticket_number))
        .limit(1)
        .execute()
    )


def _update_ticket_by_id_sync(ticket_id: str, payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .update(payload)
        .eq("id", str(ticket_id))
        .execute()
    )


def _update_ticket_by_channel_id_sync(channel_id: str, payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .update(payload)
        .eq("channel_id", str(channel_id))
        .execute()
    )


def _update_ticket_by_any_channel_id_sync(channel_id: str, payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None

    res = (
        sb.table(TICKETS_TABLE)
        .update(payload)
        .eq("channel_id", str(channel_id))
        .execute()
    )

    updated_rows = getattr(res, "data", None) or []
    if updated_rows:
        return res

    return (
        sb.table(TICKETS_TABLE)
        .update(payload)
        .eq("discord_thread_id", str(channel_id))
        .execute()
    )


def _delete_ticket_row_by_id_sync(ticket_id: str):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .delete()
        .eq("id", str(ticket_id))
        .execute()
    )


def _delete_ticket_row_by_channel_id_sync(channel_id: str):
    sb = _sb()
    if sb is None:
        return None
    return (
        sb.table(TICKETS_TABLE)
        .delete()
        .eq("channel_id", str(channel_id))
        .execute()
    )


# ============================================================
# Async wrappers
# ============================================================

async def _insert_ticket_async(payload: Dict[str, Any]):
    return await _run_db_op("insert ticket row", lambda: _insert_ticket_sync(payload))


async def _upsert_ticket_async(payload: Dict[str, Any]):
    return await _run_db_op("upsert ticket row", lambda: _upsert_ticket_sync(payload))


async def _select_ticket_by_id_async(ticket_id: str):
    return await _run_db_op("select ticket by id", lambda: _select_ticket_by_id_sync(ticket_id))


async def _select_ticket_by_channel_id_async(channel_id: str):
    return await _run_db_op(
        "select ticket by channel_id",
        lambda: _select_ticket_by_channel_id_sync(channel_id),
    )


async def _select_ticket_by_any_channel_id_async(channel_id: str):
    return await _run_db_op(
        "select ticket by any channel id",
        lambda: _select_ticket_by_any_channel_id_sync(channel_id),
    )


async def _select_open_ticket_for_owner_async(
    guild_id: str,
    owner_id: str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
):
    return await _run_db_op(
        "select open ticket for owner",
        lambda: _select_open_ticket_for_owner_sync(guild_id, owner_id, category, statuses),
    )


async def _select_open_tickets_for_guild_async(
    guild_id: str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
):
    return await _run_db_op(
        "select open tickets for guild",
        lambda: _select_open_tickets_for_guild_sync(guild_id, category, statuses),
    )


async def _select_tickets_for_owner_async(
    guild_id: str,
    owner_id: str,
    limit: int,
):
    return await _run_db_op(
        "select tickets for owner",
        lambda: _select_tickets_for_owner_sync(guild_id, owner_id, limit),
    )


async def _select_ticket_by_number_async(guild_id: str, ticket_number: int):
    return await _run_db_op(
        "select ticket by number",
        lambda: _select_ticket_by_number_sync(guild_id, ticket_number),
    )


async def _update_ticket_by_id_async(ticket_id: str, payload: Dict[str, Any]):
    return await _run_db_op(
        "update ticket by id",
        lambda: _update_ticket_by_id_sync(ticket_id, payload),
    )


async def _update_ticket_by_channel_id_async(channel_id: str, payload: Dict[str, Any]):
    return await _run_db_op(
        "update ticket by channel_id",
        lambda: _update_ticket_by_channel_id_sync(channel_id, payload),
    )


async def _update_ticket_by_any_channel_id_async(channel_id: str, payload: Dict[str, Any]):
    return await _run_db_op(
        "update ticket by any channel id",
        lambda: _update_ticket_by_any_channel_id_sync(channel_id, payload),
    )


async def _delete_ticket_row_by_id_async(ticket_id: str):
    return await _run_db_op(
        "delete ticket row by id",
        lambda: _delete_ticket_row_by_id_sync(ticket_id),
    )


async def _delete_ticket_row_by_channel_id_async(channel_id: str):
    return await _run_db_op(
        "delete ticket row by channel_id",
        lambda: _delete_ticket_row_by_channel_id_sync(channel_id),
    )


# ============================================================
# Public payload builders
# ============================================================

def build_ticket_payload(
    *,
    guild_id: int | str,
    owner_id: int | str,
    channel_id: int | str,
    username: Optional[str] = None,
    title: Optional[str] = None,
    category: str = "verification_issue",
    status: str = "open",
    priority: str = "medium",
    claimed_by: Optional[int | str] = None,
    closed_by: Optional[int | str] = None,
    closed_reason: Optional[str] = None,
    initial_message: Optional[str] = None,
    ai_category_confidence: Optional[int | float] = None,
    mod_suggestion: Optional[str] = None,
    mod_suggestion_confidence: Optional[int | float] = None,
    discord_thread_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_number: Optional[int] = None,
    assigned_to: Optional[int | str] = None,
    reopened_at: Optional[str] = None,
    sla_deadline: Optional[str] = None,
    is_ghost: bool = False,
    deleted_at: Optional[str] = None,
    deleted_by: Optional[int | str] = None,
    transcript_url: Optional[str] = None,
    transcript_message_id: Optional[int | str] = None,
    transcript_channel_id: Optional[int | str] = None,
    source: Optional[str] = None,
    category_id: Optional[str] = None,
    category_override: bool = False,
    category_set_by: Optional[int | str] = None,
    category_set_at: Optional[str] = None,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    panel_message_id: Optional[int | str] = None,
    webhook_url: Optional[str] = None,
    webhook_id: Optional[int | str] = None,
    last_activity_at: Optional[str] = None,
    last_message_id: Optional[int | str] = None,
    decision: Optional[str] = None,
    owner_name: Optional[str] = None,
    requester_name: Optional[str] = None,
    claimed_by_name: Optional[str] = None,
    assigned_to_name: Optional[str] = None,
    closed_by_name: Optional[str] = None,
    deleted_by_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now_iso = _now_iso()
    clean_owner_id = _as_str_id(owner_id)
    clean_username = _clean_text(username)
    clean_owner_name = _clean_text(owner_name) or clean_username
    clean_requester_name = _clean_text(requester_name) or clean_username
    clean_claimed_by = _as_str_id(claimed_by)
    clean_assigned_to = _as_str_id(assigned_to)

    normalized_status = _normalize_ticket_status(status)
    if normalized_status == "open" and (clean_claimed_by or clean_assigned_to):
        normalized_status = "claimed"

    payload: Dict[str, Any] = {
        "guild_id": _as_str_id(guild_id),
        "user_id": clean_owner_id,
        "username": clean_username,
        "title": _clean_text(title),
        "category": _normalize_ticket_category(category),
        "status": normalized_status,
        "priority": _normalize_ticket_priority(priority),
        "claimed_by": clean_claimed_by,
        "closed_by": _as_str_id(closed_by),
        "closed_reason": _clean_text(closed_reason),
        "initial_message": _clean_text(initial_message) or "",
        "ai_category_confidence": ai_category_confidence if ai_category_confidence is not None else 0,
        "mod_suggestion": _clean_text(mod_suggestion),
        "mod_suggestion_confidence": (
            mod_suggestion_confidence if mod_suggestion_confidence is not None else 0
        ),
        "created_at": now_iso,
        "updated_at": now_iso,
        "closed_at": None,
        "discord_thread_id": _as_str_id(discord_thread_id),
        "channel_id": _as_str_id(channel_id),
        "channel_name": _clean_text(channel_name),
        "ticket_number": int(ticket_number) if ticket_number is not None else None,
        "assigned_to": clean_assigned_to,
        "reopened_at": reopened_at,
        "sla_deadline": sla_deadline,
        "is_ghost": bool(is_ghost),
        "deleted_at": deleted_at,
        "deleted_by": _as_str_id(deleted_by),
        "transcript_url": _clean_text(transcript_url),
        "transcript_message_id": _as_str_id(transcript_message_id),
        "transcript_channel_id": _as_str_id(transcript_channel_id),
        "source": _clean_text(source),
        "category_id": _clean_text(category_id),
        "category_override": bool(category_override),
        "category_set_by": _as_str_id(category_set_by),
        "category_set_at": category_set_at,
        "matched_category_id": _clean_text(matched_category_id),
        "matched_category_name": _clean_text(matched_category_name),
        "matched_category_slug": _clean_text(matched_category_slug),
        "matched_intake_type": _clean_text(matched_intake_type),
        "matched_category_reason": _clean_text(matched_category_reason),
        "matched_category_score": int(matched_category_score or 0),
        "panel_message_id": _as_str_id(panel_message_id),
        "webhook_url": _clean_text(webhook_url),
        "webhook_id": _as_str_id(webhook_id),
        "last_activity_at": last_activity_at,
        "last_message_id": _as_str_id(last_message_id),
        "decision": _clean_text(decision),
        "owner_id": clean_owner_id,
        "requester_id": clean_owner_id,
        "owner_name": clean_owner_name,
        "requester_name": clean_requester_name,
        "claimed_by_name": _clean_text(claimed_by_name),
        "assigned_to_name": _clean_text(assigned_to_name),
        "closed_by_name": _clean_text(closed_by_name),
        "deleted_by_name": _clean_text(deleted_by_name),
    }

    if extra:
        payload.update(dict(extra))

    return _clean_ticket_payload(payload, include_created_at=True)


def build_ticket_payload_from_channel(
    *,
    channel: discord.abc.GuildChannel,
    owner_id: int | str,
    username: Optional[str] = None,
    title: Optional[str] = None,
    category: str = "verification_issue",
    status: str = "open",
    priority: str = "medium",
    claimed_by: Optional[int | str] = None,
    closed_by: Optional[int | str] = None,
    closed_reason: Optional[str] = None,
    initial_message: Optional[str] = None,
    ticket_number: Optional[int] = None,
    assigned_to: Optional[int | str] = None,
    is_ghost: bool = False,
    source: Optional[str] = None,
    category_id: Optional[str] = None,
    category_override: bool = False,
    category_set_by: Optional[int | str] = None,
    category_set_at: Optional[str] = None,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guild_id = getattr(channel.guild, "id", None)
    channel_id = getattr(channel, "id", None)
    channel_name = getattr(channel, "name", None)

    discord_thread_id: Optional[int | str] = None
    if isinstance(channel, discord.Thread):
        discord_thread_id = int(channel.id)

    return build_ticket_payload(
        guild_id=guild_id,
        owner_id=owner_id,
        username=username,
        title=title,
        channel_id=channel_id,
        discord_thread_id=discord_thread_id,
        channel_name=channel_name,
        category=category,
        status=status,
        priority=priority,
        claimed_by=claimed_by,
        closed_by=closed_by,
        closed_reason=closed_reason,
        initial_message=initial_message,
        ticket_number=ticket_number,
        assigned_to=assigned_to,
        is_ghost=is_ghost,
        source=source,
        category_id=category_id,
        category_override=category_override,
        category_set_by=category_set_by,
        category_set_at=category_set_at,
        matched_category_id=matched_category_id,
        matched_category_name=matched_category_name,
        matched_category_slug=matched_category_slug,
        matched_intake_type=matched_intake_type,
        matched_category_reason=matched_category_reason,
        matched_category_score=matched_category_score,
        extra=extra,
    )


# ============================================================
# Public fetch helpers
# ============================================================

async def get_ticket_by_id(ticket_id: int | str) -> Optional[Dict[str, Any]]:
    tid = _as_str_id(ticket_id)
    if not tid:
        return None

    try:
        res = await _select_ticket_by_id_async(tid)
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.get_ticket_by_id failed: {repr(e)}")
    return None


async def get_ticket_by_number(
    *,
    guild_id: int | str,
    ticket_number: int,
) -> Optional[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    if not gid:
        return None

    try:
        res = await _select_ticket_by_number_async(gid, int(ticket_number))
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.get_ticket_by_number failed: {repr(e)}")
    return None


async def get_ticket_by_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:
    cid = _as_str_id(channel_id)
    if not cid:
        return None

    try:
        res = await _select_ticket_by_channel_id_async(cid)
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.get_ticket_by_channel_id failed: {repr(e)}")
    return None


async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:
    cid = _as_str_id(channel_id)
    if not cid:
        return None

    try:
        res = await _select_ticket_by_any_channel_id_async(cid)
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.get_ticket_by_any_channel_id failed: {repr(e)}")
    return None


async def _find_ticket_row_by_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:
    return await get_ticket_by_any_channel_id(channel_id)


async def find_open_ticket_for_owner(
    *,
    guild_id: int | str,
    owner_id: int | str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    oid = _as_str_id(owner_id)
    if not gid or not oid:
        return None

    try:
        res = await _select_open_ticket_for_owner_async(gid, oid, category, statuses)
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.find_open_ticket_for_owner failed: {repr(e)}")
    return None


async def list_open_tickets_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    if not gid:
        return []

    try:
        res = await _select_open_tickets_for_guild_async(gid, category, statuses)
        rows = _result_rows(res)
        out: List[Dict[str, Any]] = []
        for row in rows:
            norm = _normalize_ticket_row(row)
            if norm is not None:
                out.append(norm)
        return out
    except Exception as e:
        print(f"⚠️ repository.list_open_tickets_for_guild failed: {repr(e)}")
        return []


async def list_tickets_for_owner(
    *,
    guild_id: int | str,
    owner_id: int | str,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    gid = _as_str_id(guild_id)
    oid = _as_str_id(owner_id)
    if not gid or not oid:
        return []

    max_limit = max(1, min(int(limit or 25), 200))

    try:
        res = await _select_tickets_for_owner_async(gid, oid, max_limit)
        rows = _result_rows(res)
        out: List[Dict[str, Any]] = []
        for row in rows:
            norm = _normalize_ticket_row(row)
            if norm is not None:
                out.append(norm)
        return out
    except Exception as e:
        print(f"⚠️ repository.list_tickets_for_owner failed: {repr(e)}")
        return []


# ============================================================
# Public write helpers
# ============================================================

async def insert_ticket(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        clean = _clean_ticket_payload(dict(payload or {}), include_created_at=True)
        clean.setdefault("updated_at", _now_iso())
        clean.setdefault("created_at", _now_iso())

        res = await _write_ticket_with_optional_fallback(
            op_name="insert ticket",
            payload=clean,
            writer=_insert_ticket_sync,
        )
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])
    except Exception as e:
        print(f"⚠️ repository.insert_ticket failed: {repr(e)}")
    return None


async def upsert_ticket(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    clean = _clean_ticket_payload(dict(payload or {}), include_created_at=True)
    clean["updated_at"] = _now_iso()
    clean.setdefault("created_at", _now_iso())

    channel_id = _as_str_id(clean.get("channel_id"))
    thread_id = _as_str_id(clean.get("discord_thread_id"))
    fallback_lookup_id = channel_id or thread_id

    try:
        existing = None
        if fallback_lookup_id:
            existing = await get_ticket_by_any_channel_id(fallback_lookup_id)

        if existing:
            clean = _merge_existing_ticket_identity(existing, clean)
            clean = _preserve_existing_created_at(existing, clean)

        # Fast path: channel_id-backed upsert
        if channel_id:
            try:
                res = await _write_ticket_with_optional_fallback(
                    op_name="upsert ticket",
                    payload=clean,
                    writer=_upsert_ticket_sync,
                )
                rows = _result_rows(res)
                if rows:
                    return _normalize_ticket_row(rows[0])

                fetched = await get_ticket_by_any_channel_id(channel_id)
                if fetched:
                    return fetched
            except Exception as upsert_error:
                print(f"⚠️ repository.upsert_ticket direct upsert fallback: {repr(upsert_error)}")

        # If row exists already, patch by id instead of guessing another insert
        if existing and existing.get("id") is not None:
            ticket_id = str(existing["id"])
            patch = _clean_ticket_payload(clean, include_created_at=False)
            patch = _preserve_existing_created_at(existing, patch)

            res = await _write_ticket_with_optional_fallback(
                op_name="update existing ticket during upsert",
                payload=patch,
                writer=lambda p: _update_ticket_by_id_sync(ticket_id, p),
            )
            rows = _result_rows(res)
            if rows:
                return _normalize_ticket_row(rows[0])
            return await get_ticket_by_id(ticket_id)

        return await insert_ticket(clean)

    except Exception as e:
        print(f"⚠️ repository.upsert_ticket failed: {repr(e)}")
        return None


async def create_ticket_record(
    *,
    guild_id: int | str,
    owner_id: int | str,
    channel_id: int | str,
    username: Optional[str] = None,
    title: Optional[str] = None,
    category: str = "verification_issue",
    status: str = "open",
    priority: str = "medium",
    claimed_by: Optional[int | str] = None,
    closed_by: Optional[int | str] = None,
    closed_reason: Optional[str] = None,
    initial_message: Optional[str] = None,
    discord_thread_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_number: Optional[int] = None,
    assigned_to: Optional[int | str] = None,
    is_ghost: bool = False,
    deleted_at: Optional[str] = None,
    deleted_by: Optional[int | str] = None,
    transcript_url: Optional[str] = None,
    transcript_message_id: Optional[int | str] = None,
    transcript_channel_id: Optional[int | str] = None,
    source: Optional[str] = None,
    category_id: Optional[str] = None,
    category_override: bool = False,
    category_set_by: Optional[int | str] = None,
    category_set_at: Optional[str] = None,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    payload = build_ticket_payload(
        guild_id=guild_id,
        owner_id=owner_id,
        username=username,
        title=title,
        channel_id=channel_id,
        category=category,
        status=status,
        priority=priority,
        claimed_by=claimed_by,
        closed_by=closed_by,
        closed_reason=closed_reason,
        initial_message=initial_message,
        discord_thread_id=discord_thread_id,
        channel_name=channel_name,
        ticket_number=ticket_number,
        assigned_to=assigned_to,
        is_ghost=is_ghost,
        deleted_at=deleted_at,
        deleted_by=deleted_by,
        transcript_url=transcript_url,
        transcript_message_id=transcript_message_id,
        transcript_channel_id=transcript_channel_id,
        source=source,
        category_id=category_id,
        category_override=category_override,
        category_set_by=category_set_by,
        category_set_at=category_set_at,
        matched_category_id=matched_category_id,
        matched_category_name=matched_category_name,
        matched_category_slug=matched_category_slug,
        matched_intake_type=matched_intake_type,
        matched_category_reason=matched_category_reason,
        matched_category_score=matched_category_score,
        extra=extra,
    )
    return await upsert_ticket(payload)


async def sync_ticket_record_from_channel(
    *,
    channel: discord.abc.GuildChannel,
    owner_id: int | str,
    username: Optional[str] = None,
    title: Optional[str] = None,
    category: str = "verification_issue",
    status: str = "open",
    priority: str = "medium",
    claimed_by: Optional[int | str] = None,
    closed_by: Optional[int | str] = None,
    closed_reason: Optional[str] = None,
    initial_message: Optional[str] = None,
    ticket_number: Optional[int] = None,
    assigned_to: Optional[int | str] = None,
    is_ghost: bool = False,
    source: Optional[str] = None,
    category_id: Optional[str] = None,
    category_override: bool = False,
    category_set_by: Optional[int | str] = None,
    category_set_at: Optional[str] = None,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    payload = build_ticket_payload_from_channel(
        channel=channel,
        owner_id=owner_id,
        username=username,
        title=title,
        category=category,
        status=status,
        priority=priority,
        claimed_by=claimed_by,
        closed_by=closed_by,
        closed_reason=closed_reason,
        initial_message=initial_message,
        ticket_number=ticket_number,
        assigned_to=assigned_to,
        is_ghost=is_ghost,
        source=source,
        category_id=category_id,
        category_override=category_override,
        category_set_by=category_set_by,
        category_set_at=category_set_at,
        matched_category_id=matched_category_id,
        matched_category_name=matched_category_name,
        matched_category_slug=matched_category_slug,
        matched_intake_type=matched_intake_type,
        matched_category_reason=matched_category_reason,
        matched_category_score=matched_category_score,
        extra=extra,
    )
    return await upsert_ticket(payload)


async def update_ticket_by_channel_id(
    channel_id: int | str,
    payload: Dict[str, Any],
    *,
    allow_thread_fallback: bool = True,
) -> Optional[Dict[str, Any]]:
    cid = _as_str_id(channel_id)
    if not cid:
        return None

    clean = _ticket_patch_payload(dict(payload or {}))
    if not _nonempty_ticket_patch(clean):
        return (
            await get_ticket_by_any_channel_id(cid)
            if allow_thread_fallback
            else await get_ticket_by_channel_id(cid)
        )

    try:
        row = (
            await get_ticket_by_any_channel_id(cid)
            if allow_thread_fallback
            else await get_ticket_by_channel_id(cid)
        )
        if row and row.get("id") is not None:
            ticket_id = str(row["id"])

            # Keep state transitions internally consistent.
            if clean.get("status") == "open":
                if "claimed_by" not in clean and "assigned_to" not in clean:
                    clean.setdefault("claimed_by", None)
                    clean.setdefault("assigned_to", None)
                    clean.setdefault("claimed_by_name", None)
                    clean.setdefault("assigned_to_name", None)

            if clean.get("status") == "claimed":
                claimed = _as_str_id(clean.get("claimed_by")) or _as_str_id(clean.get("assigned_to"))
                if claimed:
                    clean["claimed_by"] = claimed
                    clean["assigned_to"] = claimed

            res = await _write_ticket_with_optional_fallback(
                op_name="update ticket by located id",
                payload=clean,
                writer=lambda p: _update_ticket_by_id_sync(ticket_id, p),
            )
            rows = _result_rows(res)
            if rows:
                return _normalize_ticket_row(rows[0])
            return await get_ticket_by_id(ticket_id)

        writer = (
            _update_ticket_by_any_channel_id_sync
            if allow_thread_fallback
            else _update_ticket_by_channel_id_sync
        )
        res = await _write_ticket_with_optional_fallback(
            op_name="update ticket by channel id",
            payload=clean,
            writer=lambda p: writer(cid, p),
        )
        rows = _result_rows(res)
        if rows:
            return _normalize_ticket_row(rows[0])

        return await get_ticket_by_any_channel_id(cid)

    except Exception as e:
        print(f"⚠️ repository.update_ticket_by_channel_id failed: {repr(e)}")
        return None


async def safe_optional_update_by_channel_id(
    channel_id: int | str,
    payload: Dict[str, Any],
) -> bool:
    cid = _as_str_id(channel_id)
    if not cid:
        return False

    try:
        clean = _ticket_patch_payload(dict(payload or {}))
        if not _nonempty_ticket_patch(clean):
            return True

        row = await get_ticket_by_any_channel_id(cid)
        if not row or row.get("id") is None:
            return False

        ticket_id = str(row["id"])
        res = await _write_ticket_with_optional_fallback(
            op_name="safe optional update by located id",
            payload=clean,
            writer=lambda p: _update_ticket_by_id_sync(ticket_id, p),
        )
        rows = _result_rows(res)
        if rows:
            return True

        refreshed = await get_ticket_by_id(ticket_id)
        return refreshed is not None

    except Exception as e:
        print(f"⚠️ repository.safe_optional_update_by_channel_id failed: {repr(e)}")
        return False


async def _safe_optional_update_by_channel_id(
    channel_id: int | str,
    payload: Dict[str, Any],
) -> bool:
    return await safe_optional_update_by_channel_id(channel_id, payload)


async def touch_ticket(
    channel_id: int | str,
    *,
    last_activity_at: Optional[str] = None,
    last_message_id: Optional[int | str] = None,
) -> bool:
    payload: Dict[str, Any] = {
        "last_activity_at": last_activity_at or _now_iso(),
    }
    if last_message_id is not None:
        payload["last_message_id"] = _as_str_id(last_message_id)

    row = await update_ticket_by_channel_id(channel_id, payload, allow_thread_fallback=True)
    return row is not None


async def set_ticket_panel_message_id(channel_id: int | str, panel_message_id: int | str) -> bool:
    row = await update_ticket_by_channel_id(
        channel_id,
        {"panel_message_id": _as_str_id(panel_message_id)},
        allow_thread_fallback=True,
    )
    return row is not None


async def set_ticket_webhook(
    channel_id: int | str,
    *,
    webhook_url: Optional[str],
    webhook_id: Optional[int | str] = None,
) -> bool:
    row = await update_ticket_by_channel_id(
        channel_id,
        {
            "webhook_url": _clean_text(webhook_url),
            "webhook_id": _as_str_id(webhook_id),
        },
        allow_thread_fallback=True,
    )
    return row is not None


async def attach_transcript_to_ticket(
    *,
    channel_id: int | str,
    transcript_url: Optional[str],
    transcript_message_id: Optional[int | str],
    transcript_channel_id: Optional[int | str],
) -> bool:
    existing = await get_ticket_by_any_channel_id(channel_id)
    if not existing:
        return False

    same_url = _clean_text(existing.get("transcript_url")) == _clean_text(transcript_url)
    same_msg = _as_str_id(existing.get("transcript_message_id")) == _as_str_id(transcript_message_id)
    same_ch = _as_str_id(existing.get("transcript_channel_id")) == _as_str_id(transcript_channel_id)
    if same_url and same_msg and same_ch:
        return True

    return await safe_optional_update_by_channel_id(
        channel_id,
        {
            "transcript_url": _clean_text(transcript_url),
            "transcript_message_id": _as_str_id(transcript_message_id),
            "transcript_channel_id": _as_str_id(transcript_channel_id),
        },
    )


async def mark_ticket_closed(
    *,
    channel_id: int | str,
    closed_by: Optional[int | str] = None,
    reason: Optional[str] = None,
    decision: Optional[str] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing and _ticket_status(existing) == "closed":
        return True

    payload: Dict[str, Any] = {
        "status": "closed",
        "closed_at": _now_iso(),
        "closed_reason": _clean_text(reason),
        "reopened_at": None,
    }

    if closed_by is not None:
        payload["closed_by"] = _as_str_id(closed_by)
    if decision is not None:
        payload["decision"] = _clean_text(decision)
    if extra_payload:
        payload.update(dict(extra_payload))

    row = await update_ticket_by_channel_id(channel_id, payload, allow_thread_fallback=True)
    return row is not None


async def mark_ticket_deleted(
    *,
    channel_id: int | str,
    deleted_by: Optional[int | str] = None,
    reason: Optional[str] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing and _ticket_status(existing) == "deleted":
        return True

    payload: Dict[str, Any] = {
        "status": "deleted",
        "deleted_at": _now_iso(),
        "closed_at": _now_iso(),
        "closed_reason": _clean_text(reason) or "Deleted",
    }

    if deleted_by is not None:
        payload["deleted_by"] = _as_str_id(deleted_by)
        payload["closed_by"] = _as_str_id(deleted_by)
    if extra_payload:
        payload.update(dict(extra_payload))

    row = await update_ticket_by_channel_id(channel_id, payload, allow_thread_fallback=True)
    return row is not None


async def reopen_ticket(
    *,
    channel_id: int | str,
    reopened_by: Optional[int | str] = None,
    reason: Optional[str] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    existing = await get_ticket_by_any_channel_id(channel_id)

    # Only treat reopen as a true no-op if the row is already fully reset.
    if existing:
        status_now = _ticket_status(existing)
        claimed_now = _ticket_claimed_by_id(existing)
        closed_at_now = existing.get("closed_at")
        deleted_at_now = existing.get("deleted_at")
        decision_now = _clean_text(existing.get("decision"))

        if (
            status_now == "open"
            and claimed_now <= 0
            and not closed_at_now
            and not deleted_at_now
            and not decision_now
        ):
            return True

    payload: Dict[str, Any] = {
        "status": "open",
        "reopened_at": _now_iso(),
        "closed_at": None,
        "closed_by": None,
        "closed_reason": None,
        "deleted_at": None,
        "deleted_by": None,
        "claimed_by": None,
        "assigned_to": None,
        "claimed_by_name": None,
        "assigned_to_name": None,
        "decision": None,
    }

    if reopened_by is not None:
        payload["reopened_by"] = _as_str_id(reopened_by)
    if reason is not None:
        payload["reopen_reason"] = _clean_text(reason)
    if extra_payload:
        payload.update(dict(extra_payload))

    row = await update_ticket_by_channel_id(channel_id, payload, allow_thread_fallback=True)
    return row is not None


async def assign_ticket(
    *,
    channel_id: int | str,
    staff_member: discord.Member | discord.User,
) -> bool:
    staff_id = _as_str_id(getattr(staff_member, "id", None))
    staff_name = _safe_str(staff_member)

    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing:
        if _ticket_status(existing) == "claimed" and _ticket_claimed_by_id(existing) == _as_int(staff_id, 0):
            return True

    row = await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "claimed",
            "assigned_to": staff_id,
            "claimed_by": staff_id,
            "assigned_to_name": staff_name,
            "claimed_by_name": staff_name,
            "last_activity_at": _now_iso(),
        },
        allow_thread_fallback=True,
    )
    return row is not None


async def unclaim_ticket(
    *,
    channel_id: int | str,
) -> bool:
    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing:
        if _ticket_status(existing) == "open" and _ticket_claimed_by_id(existing) <= 0:
            return True

    row = await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "open",
            "assigned_to": None,
            "claimed_by": None,
            "assigned_to_name": None,
            "claimed_by_name": None,
            "last_activity_at": _now_iso(),
        },
        allow_thread_fallback=True,
    )
    return row is not None


async def transfer_ticket(
    *,
    channel_id: int | str,
    to_staff_member: discord.Member | discord.User,
) -> bool:
    staff_id = _as_str_id(getattr(to_staff_member, "id", None))
    staff_name = _safe_str(to_staff_member)

    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing:
        if _ticket_status(existing) == "claimed" and _ticket_claimed_by_id(existing) == _as_int(staff_id, 0):
            return True

    row = await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "claimed",
            "assigned_to": staff_id,
            "claimed_by": staff_id,
            "assigned_to_name": staff_name,
            "claimed_by_name": staff_name,
            "last_activity_at": _now_iso(),
        },
        allow_thread_fallback=True,
    )
    return row is not None


async def set_ticket_priority(
    *,
    channel_id: int | str,
    priority: str,
) -> bool:
    clean_priority = _clean_text(priority)
    if not clean_priority:
        return False

    normalized_priority = _normalize_ticket_priority(clean_priority)
    existing = await get_ticket_by_any_channel_id(channel_id)
    if existing and _clean_text(existing.get("priority")) == normalized_priority:
        return True

    row = await update_ticket_by_channel_id(
        channel_id,
        {"priority": normalized_priority},
        allow_thread_fallback=True,
    )
    return row is not None


async def delete_ticket_row(channel_id: int | str) -> bool:
    cid = _as_str_id(channel_id)
    if not cid:
        return False

    try:
        row = await get_ticket_by_any_channel_id(cid)
        if row and row.get("id") is not None:
            await _delete_ticket_row_by_id_async(str(row["id"]))
            return True

        return False
    except Exception as e:
        print(f"⚠️ repository.delete_ticket_row failed: {repr(e)}")
        return False


# ============================================================
# Ticket notes helpers
# ============================================================

def _normalize_ticket_notes_row(row: Dict[str, Any]) -> Dict[str, Any]:
    content = _safe_str(row.get("content") or "")
    is_pinned = _boolish(row.get("is_pinned"), False)

    if content.startswith(_PINNED_NOTE_PREFIX):
        is_pinned = True
        content = content[len(_PINNED_NOTE_PREFIX):].lstrip()

    return {
        "id": row.get("id"),
        "ticket_id": _as_str_id(row.get("ticket_id")),
        "author_id": _as_str_id(row.get("staff_id")),
        "author_name": _safe_str(row.get("staff_name") or row.get("staff_id") or "Unknown"),
        "note_body": content,
        "is_pinned": is_pinned,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
        "raw": dict(row),
        "_backend": TICKET_NOTES_TABLE,
    }


def _normalize_legacy_internal_note_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "ticket_id": _as_str_id(row.get("ticket_id")),
        "author_id": _as_str_id(row.get("author_id")),
        "author_name": _safe_str(row.get("author_name") or row.get("author_id") or "Unknown"),
        "note_body": _safe_str(row.get("note_body") or ""),
        "is_pinned": _boolish(row.get("is_pinned"), False),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
        "raw": dict(row),
        "_backend": _ticket_internal_notes_table_name(),
    }


async def add_internal_note(
    *,
    channel_id: int | str,
    author: discord.Member | discord.User,
    note: str,
    is_pinned: bool = False,
) -> bool:
    global _TICKET_NOTES_BACKEND

    sb = _sb()
    if sb is None:
        _repo_debug(f"add-note skipped no-supabase channel={channel_id}")
        return False

    row = await get_ticket_by_any_channel_id(channel_id)
    if not row or row.get("id") is None:
        _repo_debug(f"add-note failed no-ticket-row channel={channel_id}")
        return False

    note_text = _normalize_note_text(note)
    if not note_text:
        _repo_debug(f"add-note rejected empty-note channel={channel_id}")
        return False

    if _TICKET_NOTES_BACKEND in (None, TICKET_NOTES_TABLE):
        try:
            note_payload = {
                "ticket_id": str(row.get("id")),
                "staff_id": _as_str_id(getattr(author, "id", None)),
                "staff_name": _safe_str(author),
                "content": note_text,
                "is_pinned": bool(is_pinned),
                "created_at": _utc_iso(now_utc()),
                "updated_at": _utc_iso(now_utc()),
            }

            def _insert_ticket_note_sync():
                return sb.table(TICKET_NOTES_TABLE).insert(note_payload).execute()

            await _run_db_op(f"insert ticket note channel={channel_id}", _insert_ticket_note_sync)
            _TICKET_NOTES_BACKEND = TICKET_NOTES_TABLE

            await touch_ticket(channel_id, last_activity_at=_now_iso())

            _repo_debug(
                f"add-note success backend={TICKET_NOTES_TABLE} "
                f"channel={channel_id} ticket_id={row.get('id')} author={getattr(author, 'id', None)}"
            )
            return True

        except Exception as e:
            if (
                _missing_column_error(e, "updated_at")
                or _missing_column_error(e, "is_pinned")
            ):
                try:
                    compat_payload = {
                        "ticket_id": str(row.get("id")),
                        "staff_id": _as_str_id(getattr(author, "id", None)),
                        "staff_name": _safe_str(author),
                        "content": f"{_PINNED_NOTE_PREFIX}{note_text}" if is_pinned else note_text,
                        "created_at": _utc_iso(now_utc()),
                    }

                    def _insert_ticket_note_compat_sync():
                        return sb.table(TICKET_NOTES_TABLE).insert(compat_payload).execute()

                    await _run_db_op(
                        f"insert compat ticket note channel={channel_id}",
                        _insert_ticket_note_compat_sync,
                    )
                    _TICKET_NOTES_BACKEND = TICKET_NOTES_TABLE

                    await touch_ticket(channel_id, last_activity_at=_now_iso())

                    _repo_debug(
                        f"add-note compat success backend={TICKET_NOTES_TABLE} "
                        f"channel={channel_id} ticket_id={row.get('id')} author={getattr(author, 'id', None)}"
                    )
                    return True
                except Exception as e2:
                    if not _table_missing_error(e2, TICKET_NOTES_TABLE):
                        print(f"❌ Ticket note compat insert failed for channel={channel_id}: {repr(e2)}")
                        return False
                    print(
                        f"⚠️ {TICKET_NOTES_TABLE} unavailable for channel={channel_id}; "
                        f"trying legacy internal notes table."
                    )
            elif not _table_missing_error(e, TICKET_NOTES_TABLE):
                print(f"❌ Ticket note insert failed for channel={channel_id}: {repr(e)}")
                return False
            else:
                print(
                    f"⚠️ {TICKET_NOTES_TABLE} unavailable for channel={channel_id}; "
                    f"trying legacy internal notes table."
                )

    legacy_table = _ticket_internal_notes_table_name()

    if _TICKET_NOTES_BACKEND in (None, LEGACY_TICKET_INTERNAL_NOTES_TABLE, legacy_table):
        try:
            legacy_payload = {
                "ticket_id": str(row.get("id")),
                "guild_id": _as_str_id(row.get("guild_id")),
                "channel_id": _as_str_id(row.get("channel_id") or channel_id),
                "discord_thread_id": _as_str_id(row.get("discord_thread_id") or channel_id),
                "author_id": _as_str_id(getattr(author, "id", None)),
                "author_name": _safe_str(author),
                "note_body": note_text,
                "is_pinned": bool(is_pinned),
                "created_at": _utc_iso(now_utc()),
                "updated_at": _utc_iso(now_utc()),
            }

            def _insert_legacy_note_sync():
                return sb.table(legacy_table).insert(legacy_payload).execute()

            await _run_db_op(
                f"insert legacy internal note channel={channel_id}",
                _insert_legacy_note_sync,
            )
            _TICKET_NOTES_BACKEND = legacy_table

            await touch_ticket(channel_id, last_activity_at=_now_iso())

            _repo_debug(
                f"add-note success backend={legacy_table} "
                f"channel={channel_id} ticket_id={row.get('id')} author={getattr(author, 'id', None)}"
            )
            return True

        except Exception as e:
            if _table_missing_error(e, legacy_table):
                _TICKET_NOTES_BACKEND = "unavailable"
                print(
                    f"⚠️ Both note backends unavailable. "
                    f"Missing legacy table {legacy_table} for channel={channel_id}: {repr(e)}"
                )
                return False

            print(f"❌ Legacy internal note insert failed for channel={channel_id}: {repr(e)}")
            return False

    _TICKET_NOTES_BACKEND = "unavailable"
    return False


async def list_internal_notes(
    *,
    channel_id: int | str,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    global _TICKET_NOTES_BACKEND

    sb = _sb()
    if sb is None:
        return []

    row = await get_ticket_by_any_channel_id(channel_id)
    if not row or row.get("id") is None:
        return []

    ticket_id = str(row.get("id"))
    max_limit = max(1, min(int(limit or 25), 100))

    if _TICKET_NOTES_BACKEND in (None, TICKET_NOTES_TABLE):
        try:
            def _read_ticket_notes_sync():
                return (
                    sb.table(TICKET_NOTES_TABLE)
                    .select("*")
                    .eq("ticket_id", ticket_id)
                    .order("is_pinned", desc=True)
                    .order("created_at", desc=True)
                    .limit(max_limit)
                    .execute()
                )

            resp = await _run_db_op(
                f"list ticket notes channel={channel_id}",
                _read_ticket_notes_sync,
            )
            rows = _result_rows(resp)
            _TICKET_NOTES_BACKEND = TICKET_NOTES_TABLE

            out: List[Dict[str, Any]] = []
            for item in rows:
                out.append(_normalize_ticket_notes_row(item))
            return out

        except Exception as e:
            if _missing_column_error(e, "is_pinned"):
                try:
                    def _read_ticket_notes_compat_sync():
                        return (
                            sb.table(TICKET_NOTES_TABLE)
                            .select("*")
                            .eq("ticket_id", ticket_id)
                            .order("created_at", desc=True)
                            .limit(max_limit)
                            .execute()
                        )

                    resp = await _run_db_op(
                        f"list compat ticket notes channel={channel_id}",
                        _read_ticket_notes_compat_sync,
                    )
                    rows = _result_rows(resp)
                    _TICKET_NOTES_BACKEND = TICKET_NOTES_TABLE

                    out: List[Dict[str, Any]] = []
                    for item in rows:
                        out.append(_normalize_ticket_notes_row(item))
                    out.sort(
                        key=lambda r: (
                            not bool(r.get("is_pinned", False)),
                            str(r.get("created_at") or ""),
                        ),
                        reverse=False,
                    )
                    return out
                except Exception as e2:
                    if not _table_missing_error(e2, TICKET_NOTES_TABLE):
                        print(f"❌ Ticket note list compat failed for channel={channel_id}: {repr(e2)}")
                        return []
                    print(
                        f"⚠️ {TICKET_NOTES_TABLE} unavailable for channel={channel_id}; "
                        f"trying legacy internal notes table."
                    )
            elif not _table_missing_error(e, TICKET_NOTES_TABLE):
                print(f"❌ Ticket note list failed for channel={channel_id}: {repr(e)}")
                return []
            else:
                print(
                    f"⚠️ {TICKET_NOTES_TABLE} unavailable for channel={channel_id}; "
                    f"trying legacy internal notes table."
                )

    legacy_table = _ticket_internal_notes_table_name()

    if _TICKET_NOTES_BACKEND in (None, LEGACY_TICKET_INTERNAL_NOTES_TABLE, legacy_table):
        try:
            def _read_legacy_notes_sync():
                return (
                    sb.table(legacy_table)
                    .select("*")
                    .eq("ticket_id", ticket_id)
                    .order("is_pinned", desc=True)
                    .order("created_at", desc=True)
                    .limit(max_limit)
                    .execute()
                )

            resp = await _run_db_op(
                f"list legacy internal notes channel={channel_id}",
                _read_legacy_notes_sync,
            )
            rows = _result_rows(resp)
            _TICKET_NOTES_BACKEND = legacy_table

            out: List[Dict[str, Any]] = []
            for item in rows:
                out.append(_normalize_legacy_internal_note_row(item))
            return out

        except Exception as e:
            if _table_missing_error(e, legacy_table):
                _TICKET_NOTES_BACKEND = "unavailable"
                print(
                    f"⚠️ Both note backends unavailable. "
                    f"Missing legacy table {legacy_table} for channel={channel_id}: {repr(e)}"
                )
                return []

            print(f"❌ Legacy internal note list failed for channel={channel_id}: {repr(e)}")
            return []

    _TICKET_NOTES_BACKEND = "unavailable"
    return []


# ============================================================
# Ticket messages helpers
# ============================================================

def _normalize_ticket_message_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "ticket_id": _as_str_id(row.get("ticket_id")),
        "author_id": _as_str_id(row.get("author_id")),
        "author_name": _safe_str(row.get("author_name") or row.get("author_id") or "Unknown"),
        "content": _safe_str(row.get("content") or ""),
        "message_type": _safe_str(row.get("message_type") or "staff"),
        "created_at": row.get("created_at"),
        "attachments": _normalize_json_list(row.get("attachments")),
        "source": _clean_text(row.get("source")),
        "raw": dict(row),
    }


async def add_ticket_message(
    *,
    channel_id: int | str,
    author_id: int | str,
    author_name: Optional[str],
    content: str,
    message_type: str = "staff",
    attachments: Optional[List[Any]] = None,
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    row = await get_ticket_by_any_channel_id(channel_id)
    if not row or row.get("id") is None:
        return None

    clean_content = _normalize_message_text(content)
    if not clean_content:
        return None

    payload = {
        "ticket_id": str(row.get("id")),
        "author_id": _as_str_id(author_id),
        "author_name": _clean_text(author_name),
        "content": clean_content,
        "message_type": _clean_text(message_type) or "staff",
        "attachments": attachments if isinstance(attachments, list) else [],
        "source": _clean_text(source),
    }

    try:
        def _insert_message_sync():
            return sb.table(TICKET_MESSAGES_TABLE).insert(payload).execute()

        resp = await _run_db_op(
            f"insert ticket message channel={channel_id}",
            _insert_message_sync,
        )
        rows = _result_rows(resp)
        if rows:
            normalized = _normalize_ticket_message_row(rows[0])
            await touch_ticket(
                channel_id,
                last_activity_at=_now_iso(),
                last_message_id=normalized.get("id"),
            )
            return normalized
    except Exception as e:
        print(f"⚠️ repository.add_ticket_message failed: {repr(e)}")
    return None


async def list_ticket_messages(
    *,
    channel_id: int | str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    row = await get_ticket_by_any_channel_id(channel_id)
    if not row or row.get("id") is None:
        return []

    ticket_id = str(row.get("id"))
    max_limit = max(1, min(int(limit or 50), 200))

    try:
        def _read_messages_sync():
            return (
                sb.table(TICKET_MESSAGES_TABLE)
                .select("*")
                .eq("ticket_id", ticket_id)
                .order("created_at", desc=False)
                .limit(max_limit)
                .execute()
            )

        resp = await _run_db_op(
            f"list ticket messages channel={channel_id}",
            _read_messages_sync,
        )
        rows = _result_rows(resp)
        out: List[Dict[str, Any]] = []
        for item in rows:
            out.append(_normalize_ticket_message_row(item))
        return out
    except Exception as e:
        print(f"⚠️ repository.list_ticket_messages failed: {repr(e)}")
        return []


# ============================================================
# Ticket activity-feed helpers
# ============================================================

def _normalize_activity_feed_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "guild_id": _as_str_id(row.get("guild_id")),
        "event_family": _safe_str(row.get("event_family") or ""),
        "event_type": _safe_str(row.get("event_type") or ""),
        "source": _safe_str(row.get("source") or ""),
        "actor_user_id": _as_str_id(row.get("actor_user_id")),
        "actor_name": _clean_text(row.get("actor_name")),
        "target_user_id": _as_str_id(row.get("target_user_id")),
        "target_name": _clean_text(row.get("target_name")),
        "channel_id": _as_str_id(row.get("channel_id")),
        "channel_name": _clean_text(row.get("channel_name")),
        "ticket_id": _as_str_id(row.get("ticket_id")),
        "ticket_message_id": _as_str_id(row.get("ticket_message_id")),
        "related_table": _clean_text(row.get("related_table")),
        "related_id": _clean_text(row.get("related_id")),
        "title": _clean_text(row.get("title")),
        "description": _clean_text(row.get("description")),
        "reason": _clean_text(row.get("reason")),
        "search_text": _safe_str(row.get("search_text") or ""),
        "metadata": _safe_meta(row.get("metadata")),
        "meta": _safe_meta(row.get("meta") or row.get("metadata")),
        "created_at": row.get("created_at"),
        "raw": dict(row),
    }


async def insert_activity_event(
    *,
    guild_id: int | str,
    event_family: str,
    event_type: str,
    source: str = "bot",
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    target_user_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_id: Optional[str] = None,
    ticket_message_id: Optional[int | str] = None,
    related_table: Optional[str] = None,
    related_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    reason: Optional[str] = None,
    search_text: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    payload: Dict[str, Any] = {
        "guild_id": _as_str_id(guild_id),
        "event_family": _clean_text(event_family) or "unknown",
        "event_type": _clean_text(event_type) or "unknown",
        "source": _clean_text(source) or "bot",
        "actor_user_id": _as_str_id(actor_user_id),
        "actor_name": _clean_text(actor_name),
        "target_user_id": _as_str_id(target_user_id),
        "target_name": _clean_text(target_name),
        "channel_id": _as_str_id(channel_id),
        "channel_name": _clean_text(channel_name),
        "ticket_id": _as_str_id(ticket_id),
        "ticket_message_id": _as_str_id(ticket_message_id),
        "related_table": _clean_text(related_table),
        "related_id": _clean_text(related_id),
        "title": _clean_text(title),
        "description": _clean_text(description),
        "reason": _clean_text(reason),
        "search_text": _clean_text(search_text) or "",
        "meta": _safe_meta(meta),
        "created_at": _now_iso(),
    }

    if extra:
        payload.update(dict(extra))

    for col in list(_ACTIVITY_EVENT_UNSUPPORTED_COLS):
        payload.pop(col, None)

    max_retries = len(_OPTIONAL_ACTIVITY_EVENT_COLUMNS) + 1
    for attempt in range(1, max_retries + 2):
        snapshot = dict(payload)
        try:
            def _insert_event_sync():
                return sb.table(ACTIVITY_FEED_EVENTS_TABLE).insert(snapshot).execute()

            resp = await _run_db_op("insert activity event", _insert_event_sync)
            rows = _result_rows(resp)
            if rows:
                return _normalize_activity_feed_row(rows[0])
            return None
        except Exception as e:
            removed: List[str] = []
            for col in list(snapshot.keys()):
                if col in _OPTIONAL_ACTIVITY_EVENT_COLUMNS and _missing_column_error(e, col):
                    _ACTIVITY_EVENT_UNSUPPORTED_COLS.add(col)
                    payload.pop(col, None)
                    removed.append(col)

            if removed and attempt <= max_retries:
                print(
                    f"⚠️ insert activity event: retrying without unsupported event columns {removed}"
                )
                continue

            print(f"⚠️ repository.insert_activity_event failed: {repr(e)}")
            return None

    print("⚠️ repository.insert_activity_event exhausted schema-compat retries")
    return None


async def list_ticket_activity_events(
    *,
    guild_id: Optional[int | str] = None,
    ticket_id: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    target_user_id: Optional[int | str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return []

    max_limit = max(1, min(int(limit or 50), 200))
    clean_guild_id = _as_str_id(guild_id)
    clean_ticket_id = _as_str_id(ticket_id)
    clean_channel_id = _as_str_id(channel_id)
    clean_target_user_id = _as_str_id(target_user_id)

    if not any([clean_guild_id, clean_ticket_id, clean_channel_id, clean_target_user_id]):
        return []

    try:
        def _read_events_sync():
            query = sb.table(ACTIVITY_FEED_EVENTS_TABLE).select("*")

            if clean_guild_id:
                query = query.eq("guild_id", clean_guild_id)
            if clean_ticket_id:
                query = query.eq("ticket_id", clean_ticket_id)
            if clean_channel_id:
                query = query.eq("channel_id", clean_channel_id)
            if clean_target_user_id:
                query = query.eq("target_user_id", clean_target_user_id)

            return query.order("created_at", desc=True).limit(max_limit).execute()

        resp = await _run_db_op(
            "list ticket activity events",
            _read_events_sync,
        )
        rows = _result_rows(resp)
        out: List[Dict[str, Any]] = []
        for item in rows:
            out.append(_normalize_activity_feed_row(item))
        return out
    except Exception as e:
        print(f"⚠️ repository.list_ticket_activity_events failed: {repr(e)}")
        return []


async def get_latest_ticket_activity(
    *,
    guild_id: Optional[int | str] = None,
    ticket_id: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    target_user_id: Optional[int | str] = None,
) -> Optional[Dict[str, Any]]:
    rows = await list_ticket_activity_events(
        guild_id=guild_id,
        ticket_id=ticket_id,
        channel_id=channel_id,
        target_user_id=target_user_id,
        limit=1,
    )
    return rows[0] if rows else None


# ============================================================
# Member joins helpers
# ============================================================

async def insert_member_join(
    *,
    guild_id: int | str,
    user_id: int | str,
    username: Optional[str] = None,
    joined_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    payload: Dict[str, Any] = {
        "guild_id": _as_str_id(guild_id),
        "user_id": _as_str_id(user_id),
        "username": _clean_text(username),
        "joined_at": joined_at or _now_iso(),
        "created_at": _now_iso(),
    }

    if extra:
        payload.update(dict(extra))

    for col in list(_MEMBER_JOIN_UNSUPPORTED_COLS):
        payload.pop(col, None)

    max_retries = len(_OPTIONAL_MEMBER_JOIN_COLUMNS) + 1
    for attempt in range(1, max_retries + 2):
        snapshot = dict(payload)
        try:
            def _insert_join_sync():
                return sb.table(MEMBER_JOINS_TABLE).insert(snapshot).execute()

            resp = await _run_db_op("insert member join", _insert_join_sync)
            rows = _result_rows(resp)
            if rows:
                return dict(rows[0])
            return None

        except Exception as e:
            err_text = repr(e)
            removed: List[str] = []

            if "42703" in err_text:
                for col in list(snapshot.keys()):
                    if col in _OPTIONAL_MEMBER_JOIN_COLUMNS and col.lower() in err_text.lower():
                        _MEMBER_JOIN_UNSUPPORTED_COLS.add(col)
                        payload.pop(col, None)
                        removed.append(col)

            if not removed:
                for col in list(snapshot.keys()):
                    if col in _OPTIONAL_MEMBER_JOIN_COLUMNS and _missing_column_error(e, col):
                        _MEMBER_JOIN_UNSUPPORTED_COLS.add(col)
                        payload.pop(col, None)
                        removed.append(col)

            if removed and attempt <= max_retries:
                print(
                    f"⚠️ [JOIN-CONTEXT] member_joins insert failed: {repr(e)}"
                )
                print(
                    f"⚠️ [JOIN-CONTEXT] retrying without unsupported columns {removed}"
                )
                continue

            print(f"⚠️ [JOIN-CONTEXT] member_joins insert failed: {repr(e)}")
            return None

    print("⚠️ [JOIN-CONTEXT] member_joins insert exhausted schema-compat retries")
    return None


# ============================================================
# Diagnostics
# ============================================================

async def repository_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "table": TICKETS_TABLE,
        "supabase": False,
        "error": None,
        "optional_columns_marked_unsupported": sorted(
            [k for k, v in _OPTIONAL_COLUMN_SUPPORT.items() if v is False]
        ),
        "activity_event_unsupported_cols": sorted(_ACTIVITY_EVENT_UNSUPPORTED_COLS),
        "member_join_unsupported_cols": sorted(_MEMBER_JOIN_UNSUPPORTED_COLS),
        "notes_backend": _TICKET_NOTES_BACKEND,
        "messages_table": TICKET_MESSAGES_TABLE,
        "activity_feed_table": ACTIVITY_FEED_EVENTS_TABLE,
        "member_joins_table": MEMBER_JOINS_TABLE,
    }

    try:
        sb = _sb()
        if sb is None:
            out["error"] = "supabase unavailable"
            return out

        out["supabase"] = True

        def _probe_sync():
            return sb.table(TICKETS_TABLE).select("*").limit(1).execute()

        await _run_db_op("tickets repository healthcheck", _probe_sync)
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "TICKETS_TABLE",
    "TICKET_NOTES_TABLE",
    "TICKET_MESSAGES_TABLE",
    "ACTIVITY_FEED_EVENTS_TABLE",
    "MEMBER_JOINS_TABLE",
    "build_ticket_payload",
    "build_ticket_payload_from_channel",
    "get_ticket_by_id",
    "get_ticket_by_number",
    "get_ticket_by_channel_id",
    "get_ticket_by_any_channel_id",
    "find_open_ticket_for_owner",
    "list_open_tickets_for_guild",
    "list_tickets_for_owner",
    "insert_ticket",
    "upsert_ticket",
    "create_ticket_record",
    "sync_ticket_record_from_channel",
    "update_ticket_by_channel_id",
    "safe_optional_update_by_channel_id",
    "touch_ticket",
    "set_ticket_panel_message_id",
    "set_ticket_webhook",
    "attach_transcript_to_ticket",
    "mark_ticket_closed",
    "mark_ticket_deleted",
    "reopen_ticket",
    "assign_ticket",
    "unclaim_ticket",
    "transfer_ticket",
    "set_ticket_priority",
    "add_internal_note",
    "list_internal_notes",
    "add_ticket_message",
    "list_ticket_messages",
    "insert_activity_event",
    "list_ticket_activity_events",
    "get_latest_ticket_activity",
    "insert_member_join",
    "delete_ticket_row",
    "repository_healthcheck",
    "_find_ticket_row_by_channel_id",
    "_safe_optional_update_by_channel_id",
    "_safe_str",
    "_title_for_ticket",
    "_extract_ticket_number_from_name",
    "_parse_ticket_number_from_topic",
    "_parse_owner_id_from_topic",
]
