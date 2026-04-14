# stoney_verify/tickets_new/event_service.py
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..globals import get_supabase, now_utc, reset_supabase

try:
    from .repository import get_ticket_by_any_channel_id
except Exception:
    async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


# ============================================================
# tickets_new/event_service.py
# ------------------------------------------------------------
# Purpose:
# - centralize writes to activity_feed_events
# - make ticket actions visible to the dashboard immediately
# - support schema drift safely
# - build strong search_text for dashboard filtering/history
# - provide ticket-specific wrappers for common actions
# ============================================================

ACTIVITY_FEED_TABLE = "activity_feed_events"

_OPTIONAL_EVENT_COLUMNS = {
    "actor_user_id",
    "actor_name",
    "target_user_id",
    "target_name",
    "event_family",
    "source",
    "channel_id",
    "channel_name",
    "ticket_id",
    "ticket_message_id",
    "related_id",
    "related_table",
    "reason",
    "metadata",
    "meta",
    "search_text",
}

_OPTIONAL_EVENT_COLUMN_SUPPORT: Dict[str, Optional[bool]] = {
    col: None for col in _OPTIONAL_EVENT_COLUMNS
}

_MISSING_COLUMN_RE = re.compile(r"'([^']+)' column")


# ============================================================
# Small helpers
# ============================================================

def _debug(msg: str) -> None:
    try:
        print(f"🧩 ticket_events {msg}")
    except Exception:
        pass


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


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


def _clean_text(value: Any, limit: int = 2000) -> Optional[str]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit]
    except Exception:
        return None


def _normalize_slugish(value: Any) -> str:
    try:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("&", " and ")
            .replace("/", " ")
            .replace("_", " ")
            .replace("-", " ")
        )
    except Exception:
        return ""


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _sb():
    try:
        return get_supabase()
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
# Schema-compat helpers
# ============================================================

def _extract_missing_column_name(error: Exception) -> Optional[str]:
    text = repr(error)
    match = _MISSING_COLUMN_RE.search(text)
    if match:
        return str(match.group(1)).strip()
    return None


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
        )
    )


def _strip_known_unsupported_columns(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for col, supported in _OPTIONAL_EVENT_COLUMN_SUPPORT.items():
        if supported is False:
            out.pop(col, None)
    return out


def _strip_columns(payload: Dict[str, Any], columns: Sequence[str]) -> Dict[str, Any]:
    out = dict(payload or {})
    for col in columns:
        out.pop(col, None)
    return out


def _detect_and_mark_unsupported_optional_columns(exc: Exception, payload: Dict[str, Any]) -> List[str]:
    removed: List[str] = []
    for col in list(payload.keys()):
        if col not in _OPTIONAL_EVENT_COLUMNS:
            continue
        if _missing_column_error(exc, col):
            _OPTIONAL_EVENT_COLUMN_SUPPORT[col] = False
            removed.append(col)
    return removed


async def _write_event_with_optional_fallback(
    *,
    op_name: str,
    payload: Dict[str, Any],
    writer,
):
    current = _strip_known_unsupported_columns(payload)

    try:
        return await _run_db_op(op_name, lambda: writer(current))
    except Exception as e:
        removed = _detect_and_mark_unsupported_optional_columns(e, current)
        if removed:
            retry_payload = _strip_columns(current, removed)
            print(f"⚠️ {op_name}: retrying without unsupported event columns {removed}")
            return await _run_db_op(
                f"{op_name} (retry without unsupported columns)",
                lambda: writer(retry_payload),
            )
        raise


# ============================================================
# Search / indexing helpers
# ============================================================

def _build_search_text(parts: Sequence[Any], metadata: Optional[Dict[str, Any]] = None) -> str:
    blobs: List[str] = []

    for part in parts:
        text = _clean_text(part, limit=4000)
        if text:
            blobs.append(text)

    if metadata:
        meta_text = _safe_json(metadata)
        if meta_text:
            blobs.append(meta_text)

    joined = " ".join(blobs)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined[:8000]


def _title_from_event_type(event_type: str) -> str:
    text = _normalize_slugish(event_type)
    if not text:
        return "Activity Event"
    return " ".join(word.capitalize() for word in text.split())


def _description_for_ticket_event(
    *,
    event_type: str,
    actor_name: Optional[str],
    target_name: Optional[str],
    category: Optional[str],
    priority: Optional[str],
    reason: Optional[str],
    channel_name: Optional[str],
) -> str:
    actor = _clean_text(actor_name, 120) or "Someone"
    target = _clean_text(target_name, 120) or "ticket"
    category_text = _clean_text(category, 120)
    priority_text = _clean_text(priority, 40)
    reason_text = _clean_text(reason, 240)
    channel_text = _clean_text(channel_name, 120)

    event_key = _normalize_slugish(event_type)

    if event_key == "ticket created":
        base = f"{actor} created a ticket for {target}."
    elif event_key == "ticket claimed":
        base = f"{actor} claimed {target}'s ticket."
    elif event_key == "ticket unclaimed":
        base = f"{actor} unclaimed {target}'s ticket."
    elif event_key == "ticket transferred":
        base = f"{actor} transferred {target}'s ticket."
    elif event_key == "ticket priority updated":
        base = f"{actor} changed the ticket priority."
    elif event_key == "ticket note added":
        base = f"{actor} added an internal note."
    elif event_key == "ticket closed":
        base = f"{actor} closed {target}'s ticket."
    elif event_key == "ticket reopened":
        base = f"{actor} reopened {target}'s ticket."
    elif event_key == "ticket deleted":
        base = f"{actor} deleted {target}'s ticket."
    elif event_key == "ticket transcript attached":
        base = f"{actor} attached a transcript."
    else:
        base = f"{actor} performed {event_key or 'a ticket action'}."

    extras: List[str] = []
    if category_text:
        extras.append(f"Category: {category_text}")
    if priority_text:
        extras.append(f"Priority: {priority_text}")
    if channel_text:
        extras.append(f"Channel: {channel_text}")
    if reason_text:
        extras.append(f"Reason: {reason_text}")

    if extras:
        return f"{base} {' • '.join(extras)}"
    return base


# ============================================================
# Raw sync DB functions
# ============================================================

def _insert_event_sync(payload: Dict[str, Any]):
    sb = _sb()
    if sb is None:
        return None
    return sb.table(ACTIVITY_FEED_TABLE).insert(payload).execute()


# ============================================================
# Ticket context helpers
# ============================================================

async def _resolve_ticket_row(
    *,
    channel_id: Optional[int | str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if isinstance(ticket_row, dict):
        return dict(ticket_row)

    cid = _as_str_id(channel_id)
    if not cid:
        return None

    try:
        row = await get_ticket_by_any_channel_id(cid)
        if isinstance(row, dict):
            return dict(row)
    except Exception as e:
        _debug(f"ticket context lookup failed channel={cid} error={repr(e)}")

    return None


# ============================================================
# Public payload builders
# ============================================================

def build_activity_event_payload(
    *,
    guild_id: int | str,
    title: str,
    description: str,
    event_type: str,
    event_family: str = "ticket",
    source: str = "tickets_new",
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    target_user_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_id: Optional[int | str] = None,
    ticket_message_id: Optional[int | str] = None,
    related_id: Optional[int | str] = None,
    related_table: Optional[str] = "tickets",
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = _safe_dict(metadata)
    title_text = _clean_text(title, 240) or "Activity Event"
    description_text = _clean_text(description, 1000) or title_text
    event_type_text = _clean_text(event_type, 120) or "activity_event"
    event_family_text = _clean_text(event_family, 80) or "system"
    source_text = _clean_text(source, 80) or "system"

    payload: Dict[str, Any] = {
        "guild_id": _as_str_id(guild_id),
        "title": title_text,
        "description": description_text,
        "event_type": event_type_text,
        "event_family": event_family_text,
        "source": source_text,
        "actor_user_id": _as_str_id(actor_user_id),
        "actor_name": _clean_text(actor_name, 160),
        "target_user_id": _as_str_id(target_user_id),
        "target_name": _clean_text(target_name, 160),
        "channel_id": _as_str_id(channel_id),
        "channel_name": _clean_text(channel_name, 200),
        "ticket_id": _as_str_id(ticket_id),
        "ticket_message_id": _as_str_id(ticket_message_id),
        "related_id": _as_str_id(related_id) or _as_str_id(ticket_id) or _as_str_id(channel_id),
        "related_table": _clean_text(related_table, 80),
        "reason": _clean_text(reason, 500),
        "metadata": meta,
        "meta": meta,
        "created_at": created_at or _now_iso(),
    }

    payload["search_text"] = _build_search_text(
        [
            payload.get("title"),
            payload.get("description"),
            payload.get("event_type"),
            payload.get("event_family"),
            payload.get("source"),
            payload.get("actor_user_id"),
            payload.get("actor_name"),
            payload.get("target_user_id"),
            payload.get("target_name"),
            payload.get("channel_id"),
            payload.get("channel_name"),
            payload.get("ticket_id"),
            payload.get("ticket_message_id"),
            payload.get("related_id"),
            payload.get("related_table"),
            payload.get("reason"),
        ],
        meta,
    )

    if extra:
        payload.update(dict(extra))

    return payload


# ============================================================
# Public write helpers
# ============================================================

async def insert_activity_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        clean = dict(payload or {})
        clean.setdefault("created_at", _now_iso())

        res = await _write_event_with_optional_fallback(
            op_name="insert activity event",
            payload=clean,
            writer=_insert_event_sync,
        )
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return dict(rows[0])
    except Exception as e:
        if _table_missing_error(e, ACTIVITY_FEED_TABLE):
            print(f"⚠️ activity event table missing: {repr(e)}")
            return None
        print(f"⚠️ event_service.insert_activity_event failed: {repr(e)}")
    return None


async def log_activity_event(
    *,
    guild_id: int | str,
    title: str,
    description: str,
    event_type: str,
    event_family: str = "ticket",
    source: str = "tickets_new",
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    target_user_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_id: Optional[int | str] = None,
    ticket_message_id: Optional[int | str] = None,
    related_id: Optional[int | str] = None,
    related_table: Optional[str] = "tickets",
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    payload = build_activity_event_payload(
        guild_id=guild_id,
        title=title,
        description=description,
        event_type=event_type,
        event_family=event_family,
        source=source,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        target_user_id=target_user_id,
        target_name=target_name,
        channel_id=channel_id,
        channel_name=channel_name,
        ticket_id=ticket_id,
        ticket_message_id=ticket_message_id,
        related_id=related_id,
        related_table=related_table,
        reason=reason,
        metadata=metadata,
        created_at=created_at,
        extra=extra,
    )
    row = await insert_activity_event(payload)
    return row is not None


async def log_ticket_event(
    *,
    guild_id: int | str,
    event_type: str,
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    target_user_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    channel_name: Optional[str] = None,
    ticket_id: Optional[int | str] = None,
    ticket_message_id: Optional[int | str] = None,
    reason: Optional[str] = None,
    source: str = "tickets_new",
    metadata: Optional[Dict[str, Any]] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    row = await _resolve_ticket_row(channel_id=channel_id, ticket_row=ticket_row)
    meta = _safe_dict(metadata)

    resolved_guild_id = (
        _as_str_id(guild_id)
        or _as_str_id(row.get("guild_id") if isinstance(row, dict) else None)
    )
    if not resolved_guild_id:
        return False

    resolved_ticket_id = (
        _as_str_id(ticket_id)
        or _as_str_id(row.get("id") if isinstance(row, dict) else None)
    )
    resolved_channel_id = (
        _as_str_id(channel_id)
        or _as_str_id(row.get("channel_id") if isinstance(row, dict) else None)
        or _as_str_id(row.get("discord_thread_id") if isinstance(row, dict) else None)
    )
    resolved_channel_name = (
        _clean_text(channel_name, 200)
        or _clean_text(row.get("channel_name") if isinstance(row, dict) else None, 200)
    )
    resolved_target_user_id = (
        _as_str_id(target_user_id)
        or _as_str_id(row.get("user_id") if isinstance(row, dict) else None)
        or _as_str_id(row.get("owner_id") if isinstance(row, dict) else None)
        or _as_str_id(row.get("requester_id") if isinstance(row, dict) else None)
    )
    resolved_target_name = (
        _clean_text(target_name, 160)
        or _clean_text(row.get("username") if isinstance(row, dict) else None, 160)
        or _clean_text(row.get("owner_name") if isinstance(row, dict) else None, 160)
        or _clean_text(row.get("requester_name") if isinstance(row, dict) else None, 160)
    )
    resolved_category = _clean_text(row.get("category") if isinstance(row, dict) else None, 120)
    resolved_priority = _clean_text(row.get("priority") if isinstance(row, dict) else None, 40)

    title = _title_from_event_type(event_type)
    description = _description_for_ticket_event(
        event_type=event_type,
        actor_name=actor_name,
        target_name=resolved_target_name,
        category=resolved_category,
        priority=resolved_priority,
        reason=reason,
        channel_name=resolved_channel_name,
    )

    if row:
        meta.setdefault("ticket_status", row.get("status"))
        meta.setdefault("ticket_category", row.get("category"))
        meta.setdefault("ticket_priority", row.get("priority"))
        meta.setdefault("ticket_number", row.get("ticket_number"))
        meta.setdefault("is_ghost", row.get("is_ghost"))
        meta.setdefault("source_ticket_row", "repository_lookup")

    return await log_activity_event(
        guild_id=resolved_guild_id,
        title=title,
        description=description,
        event_type=event_type,
        event_family="ticket",
        source=source,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        target_user_id=resolved_target_user_id,
        target_name=resolved_target_name,
        channel_id=resolved_channel_id,
        channel_name=resolved_channel_name,
        ticket_id=resolved_ticket_id,
        ticket_message_id=ticket_message_id,
        related_id=resolved_ticket_id or resolved_channel_id,
        related_table="tickets",
        reason=reason,
        metadata=meta,
        extra=extra,
    )


# ============================================================
# Ticket wrappers
# ============================================================

async def log_ticket_created(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    channel_id: Optional[int | str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_create",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_created",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_claimed(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_claim",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_claimed",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_unclaimed(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_unclaim",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_unclaimed",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_transferred(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    target_user_id: Optional[int | str],
    target_name: Optional[str],
    channel_id: int | str,
    reason: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_transfer",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    meta = _safe_dict(metadata)
    meta.setdefault("transfer_target_user_id", _as_str_id(target_user_id))
    meta.setdefault("transfer_target_name", _clean_text(target_name, 160))

    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_transferred",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        target_user_id=target_user_id,
        target_name=target_name,
        channel_id=channel_id,
        reason=reason,
        ticket_row=ticket_row,
        source=source,
        metadata=meta,
    )


async def log_ticket_priority_updated(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    new_priority: str,
    reason: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_priority",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    meta = _safe_dict(metadata)
    meta.setdefault("new_priority", _clean_text(new_priority, 40))

    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_priority_updated",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        reason=reason,
        ticket_row=ticket_row,
        source=source,
        metadata=meta,
    )


async def log_ticket_note_added(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    note_preview: Optional[str] = None,
    is_pinned: bool = False,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_note",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    meta = _safe_dict(metadata)
    meta.setdefault("note_preview", _clean_text(note_preview, 240))
    meta.setdefault("is_pinned", bool(is_pinned))

    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_note_added",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=meta,
    )


async def log_ticket_closed(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    reason: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_close",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_closed",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        reason=reason,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_reopened(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    reason: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_reopen",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_reopened",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        reason=reason,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_deleted(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str],
    actor_name: Optional[str],
    channel_id: int | str,
    reason: Optional[str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_delete",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_deleted",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        reason=reason,
        ticket_row=ticket_row,
        source=source,
        metadata=metadata,
    )


async def log_ticket_transcript_attached(
    *,
    guild_id: int | str,
    actor_user_id: Optional[int | str] = None,
    actor_name: Optional[str] = None,
    channel_id: int | str,
    transcript_url: Optional[str] = None,
    transcript_message_id: Optional[int | str] = None,
    transcript_channel_id: Optional[int | str] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    source: str = "tickets_new_transcript",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    meta = _safe_dict(metadata)
    meta.setdefault("transcript_url", _clean_text(transcript_url, 500))
    meta.setdefault("transcript_message_id", _as_str_id(transcript_message_id))
    meta.setdefault("transcript_channel_id", _as_str_id(transcript_channel_id))

    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_transcript_attached",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=meta,
    )


# ============================================================
# Diagnostics
# ============================================================

async def event_service_healthcheck() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "table": ACTIVITY_FEED_TABLE,
        "supabase": False,
        "error": None,
        "optional_columns_marked_unsupported": sorted(
            [k for k, v in _OPTIONAL_EVENT_COLUMN_SUPPORT.items() if v is False]
        ),
    }

    try:
        sb = _sb()
        if sb is None:
            out["error"] = "supabase unavailable"
            return out

        out["supabase"] = True

        def _probe_sync():
            return sb.table(ACTIVITY_FEED_TABLE).select("*").limit(1).execute()

        await _run_db_op("ticket event service healthcheck", _probe_sync)
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = repr(e)
        return out


__all__ = [
    "ACTIVITY_FEED_TABLE",
    "build_activity_event_payload",
    "insert_activity_event",
    "log_activity_event",
    "log_ticket_event",
    "log_ticket_created",
    "log_ticket_claimed",
    "log_ticket_unclaimed",
    "log_ticket_transferred",
    "log_ticket_priority_updated",
    "log_ticket_note_added",
    "log_ticket_closed",
    "log_ticket_reopened",
    "log_ticket_deleted",
    "log_ticket_transcript_attached",
    "event_service_healthcheck",
]
