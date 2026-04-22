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
# - suppress duplicate burst logs so dashboard history stays clean
# - preserve lifecycle context (open / archive / deleted) for UI timelines
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
_EVENT_DEDUP_WINDOW_SECONDS = 3.0
_EVENT_DEDUP_CACHE: Dict[str, float] = {}


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


def _normalize_event_type(value: Any) -> str:
    text = _clean_text(value, 120) or "activity_event"
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\- ]+", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "activity_event"


def _normalize_event_family(value: Any) -> str:
    text = _clean_text(value, 80) or "system"
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\- ]+", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "system"


def _normalize_source(value: Any) -> str:
    text = _clean_text(value, 80) or "system"
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\- ]+", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "system"


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


def _result_rows(resp: Any) -> List[Dict[str, Any]]:
    try:
        rows = getattr(resp, "data", None) or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception:
        return []


def _normalize_ticket_status(value: Any, default: str = "unknown") -> str:
    try:
        text = str(value or "").strip().lower()
        if text in {"open", "claimed", "closed", "deleted"}:
            return text
        if text in {"active", "reopened"}:
            return "open"
    except Exception:
        pass
    return default


def _normalize_ticket_priority(value: Any, default: str = "medium") -> str:
    try:
        text = str(value or "").strip().lower()
        if text in {"low", "medium", "high", "urgent"}:
            return text
    except Exception:
        pass
    return default


def _ticket_channel_state(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "unknown"

    status = _normalize_ticket_status(row.get("status"), "unknown")
    channel_name = str(row.get("channel_name") or "").strip().lower()
    lifecycle_location = str(
        row.get("lifecycle_location")
        or row.get("channel_lifecycle_location")
        or row.get("location")
        or ""
    ).strip().lower()

    if status == "deleted":
        return "deleted"
    if status == "closed":
        return "archived" if lifecycle_location.startswith("archive:") or channel_name.startswith("closed-") else "closed"
    if lifecycle_location.startswith("archive:"):
        return "archived"
    if channel_name.startswith("closed-"):
        return "archived"
    if status in {"open", "claimed"}:
        return "active"
    return "unknown"


def _ticket_lifecycle_location(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    for key in ("lifecycle_location", "channel_lifecycle_location", "location"):
        value = _clean_text(row.get(key), 200)
        if value:
            return value
    channel_name = _clean_text(row.get("channel_name"), 200)
    if channel_name and channel_name.lower().startswith("closed-"):
        return "archive:named_closed"
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
    missing_name = _extract_missing_column_name(exc)

    for col in list(payload.keys()):
        if col not in _OPTIONAL_EVENT_COLUMNS:
            continue
        if _missing_column_error(exc, col) or (missing_name and missing_name == col):
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

    max_retries = len(_OPTIONAL_EVENT_COLUMNS)
    for attempt in range(1, max_retries + 2):
        snapshot = dict(current)
        try:
            return await _run_db_op(op_name, lambda: writer(snapshot))
        except Exception as e:
            removed = _detect_and_mark_unsupported_optional_columns(e, snapshot)
            if removed and attempt <= max_retries:
                current = _strip_columns(current, removed)
                print(f"⚠️ {op_name}: retrying without unsupported event columns {removed}")
                continue
            raise


# ============================================================
# Dedup helpers
# ============================================================

def _prune_event_dedup_cache() -> None:
    try:
        now_ts = time.time()
        stale_keys = [
            key
            for key, ts in list(_EVENT_DEDUP_CACHE.items())
            if (now_ts - float(ts)) > max(_EVENT_DEDUP_WINDOW_SECONDS * 3, 12.0)
        ]
        for key in stale_keys:
            _EVENT_DEDUP_CACHE.pop(key, None)
    except Exception:
        pass


def _event_signature(payload: Dict[str, Any]) -> str:
    meta = _safe_dict(payload.get("metadata") or payload.get("meta"))
    important_meta = {
        "ticket_number": meta.get("ticket_number"),
        "new_priority": meta.get("new_priority"),
        "transfer_target_user_id": meta.get("transfer_target_user_id"),
        "transfer_target_name": meta.get("transfer_target_name"),
        "transcript_message_id": meta.get("transcript_message_id"),
        "transcript_channel_id": meta.get("transcript_channel_id"),
        "note_preview": meta.get("note_preview"),
        "is_pinned": meta.get("is_pinned"),
        "previous_claimed_by": meta.get("previous_claimed_by"),
        "lifecycle_location": meta.get("lifecycle_location"),
        "ticket_channel_state": meta.get("ticket_channel_state"),
        "moved_to_archive": meta.get("moved_to_archive"),
        "moved_to_active": meta.get("moved_to_active"),
        "channel_name_after_close": meta.get("channel_name_after_close"),
        "channel_name_after_reopen": meta.get("channel_name_after_reopen"),
        "matched_category_slug": meta.get("matched_category_slug"),
    }

    signature_blob = {
        "guild_id": payload.get("guild_id"),
        "event_type": payload.get("event_type"),
        "event_family": payload.get("event_family"),
        "source": payload.get("source"),
        "actor_user_id": payload.get("actor_user_id"),
        "target_user_id": payload.get("target_user_id"),
        "channel_id": payload.get("channel_id"),
        "ticket_id": payload.get("ticket_id"),
        "ticket_message_id": payload.get("ticket_message_id"),
        "related_id": payload.get("related_id"),
        "reason": payload.get("reason"),
        "title": payload.get("title"),
        "important_meta": important_meta,
    }
    return _safe_json(signature_blob)


def _event_recently_logged(payload: Dict[str, Any]) -> bool:
    try:
        meta = _safe_dict(payload.get("metadata") or payload.get("meta"))
        if bool(meta.get("allow_duplicate_event")):
            return False

        _prune_event_dedup_cache()

        sig = _event_signature(payload)
        now_ts = time.time()
        last_ts = _EVENT_DEDUP_CACHE.get(sig)
        if last_ts is not None and (now_ts - float(last_ts)) <= _EVENT_DEDUP_WINDOW_SECONDS:
            return True

        _EVENT_DEDUP_CACHE[sig] = now_ts
        return False
    except Exception:
        return False


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

        for key in (
            "ticket_status",
            "ticket_category",
            "ticket_priority",
            "ticket_number",
            "matched_category_name",
            "matched_category_slug",
            "lifecycle_location",
            "ticket_channel_state",
            "owner_name",
            "claimed_by_name",
            "assigned_to_name",
            "channel_name_after_close",
            "channel_name_after_reopen",
        ):
            text = _clean_text(metadata.get(key), 1000)
            if text:
                blobs.append(text)

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
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    actor = _clean_text(actor_name, 120) or "Someone"
    target = _clean_text(target_name, 120) or "ticket"
    category_text = _clean_text(category, 120)
    priority_text = _clean_text(priority, 40)
    reason_text = _clean_text(reason, 240)
    channel_text = _clean_text(channel_name, 120)
    meta = _safe_dict(metadata)

    transfer_target_name = _clean_text(meta.get("transfer_target_name"), 120)
    new_priority = _clean_text(meta.get("new_priority"), 40)
    note_preview = _clean_text(meta.get("note_preview"), 120)
    is_pinned = bool(meta.get("is_pinned"))
    transcript_url = _clean_text(meta.get("transcript_url"), 240)
    lifecycle_location = _clean_text(meta.get("lifecycle_location"), 160)
    channel_state = _clean_text(meta.get("ticket_channel_state"), 80)
    moved_to_archive = bool(meta.get("moved_to_archive"))
    moved_to_active = bool(meta.get("moved_to_active"))
    matched_category_name = _clean_text(meta.get("matched_category_name"), 120)
    matched_category_slug = _clean_text(meta.get("matched_category_slug"), 120)

    event_key = _normalize_slugish(event_type)

    if event_key == "ticket created":
        base = f"{actor} created a ticket for {target}."
    elif event_key == "ticket claimed":
        base = f"{actor} claimed {target}'s ticket."
    elif event_key == "ticket unclaimed":
        base = f"{actor} unclaimed {target}'s ticket."
    elif event_key == "ticket transferred":
        base = (
            f"{actor} transferred {target}'s ticket"
            + (f" to {transfer_target_name}." if transfer_target_name else ".")
        )
    elif event_key == "ticket priority updated":
        base = (
            f"{actor} changed the ticket priority"
            + (f" to {new_priority}." if new_priority else ".")
        )
    elif event_key == "ticket note added":
        base = (
            f"{actor} added "
            + ("a pinned internal note." if is_pinned else "an internal note.")
        )
    elif event_key == "ticket closed":
        if moved_to_archive:
            base = f"{actor} closed {target}'s ticket and moved it to archive."
        else:
            base = f"{actor} closed {target}'s ticket."
    elif event_key == "ticket reopened":
        if moved_to_active:
            base = f"{actor} reopened {target}'s ticket and moved it back to the active queue."
        else:
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
    if matched_category_name or matched_category_slug:
        extras.append(f"Matched: {matched_category_name or matched_category_slug}")
    if channel_state:
        extras.append(f"State: {channel_state}")
    if lifecycle_location:
        extras.append(f"Location: {lifecycle_location}")
    if note_preview:
        extras.append(f"Note: {note_preview}")
    if transcript_url:
        extras.append("Transcript attached")
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


def _ticket_owner_user_id(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    return (
        _as_str_id(row.get("user_id"))
        or _as_str_id(row.get("owner_id"))
        or _as_str_id(row.get("requester_id"))
    )


def _ticket_owner_name(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    return (
        _clean_text(row.get("username"), 160)
        or _clean_text(row.get("owner_name"), 160)
        or _clean_text(row.get("requester_name"), 160)
    )


def _enrich_ticket_metadata(
    row: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    meta = _safe_dict(metadata)

    if row:
        meta.setdefault("ticket_status", _normalize_ticket_status(row.get("status"), "unknown"))
        meta.setdefault("ticket_category", row.get("category"))
        meta.setdefault("ticket_priority", _normalize_ticket_priority(row.get("priority"), "medium"))
        meta.setdefault("ticket_number", row.get("ticket_number"))
        meta.setdefault("is_ghost", row.get("is_ghost"))
        meta.setdefault("claimed_by", row.get("claimed_by"))
        meta.setdefault("assigned_to", row.get("assigned_to"))
        meta.setdefault("claimed_by_name", row.get("claimed_by_name"))
        meta.setdefault("assigned_to_name", row.get("assigned_to_name"))
        meta.setdefault("owner_user_id", _ticket_owner_user_id(row))
        meta.setdefault("owner_name", _ticket_owner_name(row))
        meta.setdefault("matched_category_id", row.get("matched_category_id"))
        meta.setdefault("matched_category_name", row.get("matched_category_name"))
        meta.setdefault("matched_category_slug", row.get("matched_category_slug"))
        meta.setdefault("matched_intake_type", row.get("matched_intake_type"))
        meta.setdefault("matched_category_score", row.get("matched_category_score"))
        meta.setdefault("category_override", row.get("category_override"))
        meta.setdefault("category_id", row.get("category_id"))
        meta.setdefault("transcript_url", row.get("transcript_url"))
        meta.setdefault("transcript_message_id", row.get("transcript_message_id"))
        meta.setdefault("transcript_channel_id", row.get("transcript_channel_id"))
        meta.setdefault("last_activity_at", row.get("last_activity_at"))
        meta.setdefault("last_message_id", row.get("last_message_id"))
        meta.setdefault("ticket_channel_state", _ticket_channel_state(row))
        meta.setdefault("lifecycle_location", _ticket_lifecycle_location(row))
        meta.setdefault("source_ticket_row", "repository_lookup")

    return meta


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
    event_type_text = _normalize_event_type(event_type)
    event_family_text = _normalize_event_family(event_family)
    source_text = _normalize_source(source)

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

    if extra:
        payload.update(dict(extra))

    if not _clean_text(payload.get("search_text")):
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

    return payload


# ============================================================
# Public write helpers
# ============================================================

async def insert_activity_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        clean = dict(payload or {})
        clean.setdefault("created_at", _now_iso())

        if _event_recently_logged(clean):
            _debug(
                f"dedup skip event_type={clean.get('event_type')} "
                f"channel={clean.get('channel_id')} ticket={clean.get('ticket_id')}"
            )
            return {"deduped": True, **clean}

        res = await _write_event_with_optional_fallback(
            op_name="insert activity event",
            payload=clean,
            writer=_insert_event_sync,
        )
        rows = _result_rows(res)
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
    meta = _enrich_ticket_metadata(row, metadata)

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
        or _ticket_owner_user_id(row)
    )
    resolved_target_name = (
        _clean_text(target_name, 160)
        or _ticket_owner_name(row)
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
        metadata=meta,
    )

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
    meta = _safe_dict(metadata)
    meta.setdefault("allow_duplicate_event", False)
    return await log_ticket_event(
        guild_id=guild_id,
        event_type="ticket_created",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        channel_id=channel_id,
        ticket_row=ticket_row,
        source=source,
        metadata=meta,
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
        "dedup_cache_size": len(_EVENT_DEDUP_CACHE),
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
