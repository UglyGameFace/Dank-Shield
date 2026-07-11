from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .globals import get_supabase, reset_supabase


MEMBER_REVIEW_EVENT = "member_review_verdict"
SOURCE_REVIEW_EVENT = "source_review_verdict"

VERDICT_LABELS: Dict[str, str] = {
    "looks_safe": "Looks Safe",
    "watch_member": "Watch Member",
    "false_positive": "False Positive",
    "approved_bot": "Approved Bot",
    "suspicious_bot": "Suspicious Bot",
    "bad_invite_source": "Bad Invite Source",
    "clear_invite_source": "Invite Source Cleared",
    "likely_alt": "Likely Alt",
    "confirmed_alt": "Confirmed Alt",
    "reset": "Reset Review Verdict",
}

ALT_VERDICTS = {"likely_alt", "confirmed_alt"}
BOT_VERDICTS = {"approved_bot", "suspicious_bot"}
SOURCE_VERDICTS = {"bad_invite_source", "clear_invite_source"}


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        return str(value)
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_retryable(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "remoteprotocolerror",
            "temporarily unavailable",
            "too many requests",
            "broken pipe",
            "stream closed",
            "eof",
        )
    )


def _execute(
    op_name: str,
    executor: Callable[[], Any],
    *,
    attempts: int = 5,
) -> Any:
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return executor()
        except Exception as exc:
            last_error = exc
            if _is_retryable(exc) and attempt < attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                time.sleep(
                    min(0.35 * (2 ** (attempt - 1)), 3.0)
                    + random.uniform(0.05, 0.20)
                )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{op_name} failed")


def _insert_member_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    def _write() -> Dict[str, Any]:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is not configured.")

        sb.table("member_events").insert(payload).execute()
        return dict(payload)

    return _execute("insert member review event", _write)


def source_key_from_join_context(context: Optional[Dict[str, Any]]) -> str:
    data = dict(context or {})

    invite_code = _safe_str(data.get("invite") or data.get("invite_code"))
    if invite_code.lower() not in {"", "unknown", "none", "null"}:
        return f"invite:{invite_code.lower()}"

    join_source = _safe_str(data.get("source") or data.get("join_source"))
    if join_source.lower() not in {"", "unknown", "unknown_join", "none", "null"}:
        return f"source:{join_source.lower()}"

    entry_method = _safe_str(data.get("entry_method"))
    if entry_method.lower() not in {"", "unknown", "unknown_join", "none", "null"}:
        return f"entry:{entry_method.lower()}"

    return ""


def infer_latest_source_key(*, guild_id: Any, user_id: Any) -> str:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return ""

    def _read() -> str:
        sb = get_supabase()
        if sb is None:
            return ""

        res = (
            sb.table("member_joins")
            .select("invite_code,join_source,entry_method,entry_confidence")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .order("joined_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return ""
        return source_key_from_join_context(dict(rows[0]))

    try:
        return _execute("infer latest review source", _read)
    except Exception:
        return ""


def _save_identity_link(
    *,
    guild_id: str,
    user_id: str,
    related_user_id: str,
    verdict: str,
    created_by: str,
    reason: str,
    evidence: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if verdict not in ALT_VERDICTS:
        return None

    if not related_user_id:
        raise ValueError("Related member is required for alt verdicts.")
    if user_id == related_user_id:
        raise ValueError("A member cannot be linked to themselves.")

    from .identity_proof_service import (
        confirm_duplicate_users,
        mark_users_likely_same_person,
    )

    if verdict == "confirmed_alt":
        return confirm_duplicate_users(
            guild_id=guild_id,
            user_a_id=user_id,
            user_b_id=related_user_id,
            created_by=created_by,
            reason=reason,
            evidence=evidence,
        )

    return mark_users_likely_same_person(
        guild_id=guild_id,
        user_a_id=user_id,
        user_b_id=related_user_id,
        created_by=created_by,
        reason=reason,
        evidence=evidence,
    )


def record_member_review_feedback(
    *,
    guild_id: Any,
    user_id: Any,
    verdict: str,
    created_by: Any,
    created_by_name: Optional[str] = None,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    related_user_id: Optional[Any] = None,
    source_key: Optional[str] = None,
) -> Dict[str, Any]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)
    actor_text = _safe_str(created_by)
    verdict_text = _safe_str(verdict).lower()
    reason_text = _safe_str(reason, "No reason supplied.")
    related_text = _safe_str(related_user_id)
    source_text = _safe_str(source_key)

    if not guild_text:
        raise ValueError("guild_id is required")
    if not user_text:
        raise ValueError("user_id is required")
    if not actor_text:
        raise ValueError("created_by is required")
    if verdict_text not in VERDICT_LABELS:
        raise ValueError(f"Unsupported verdict: {verdict_text}")
    if verdict_text in ALT_VERDICTS and not related_text:
        raise ValueError("Related member is required for alt verdicts.")
    if verdict_text in SOURCE_VERDICTS and not source_text:
        raise ValueError("A known invite/source is required for source verdicts.")

    evidence_payload = _json_safe(dict(evidence or {}))
    identity_link = _save_identity_link(
        guild_id=guild_text,
        user_id=user_text,
        related_user_id=related_text,
        verdict=verdict_text,
        created_by=actor_text,
        reason=reason_text,
        evidence=dict(evidence_payload or {}),
    )

    metadata = {
        "verdict": verdict_text,
        "verdict_label": VERDICT_LABELS[verdict_text],
        "related_user_id": related_text or None,
        "source_key": source_text or None,
        "evidence_snapshot": evidence_payload,
        "identity_link_id": (
            _safe_str((identity_link or {}).get("id")) or None
        ),
        "supersedes_previous": True,
        "automatic_enforcement": False,
        "identity_links_unchanged": verdict_text == "reset",
    }

    created_at = _now_iso()

    member_payload = {
        "guild_id": guild_text,
        "user_id": user_text,
        "actor_id": actor_text,
        "actor_name": _safe_str(created_by_name, actor_text),
        "event_type": MEMBER_REVIEW_EVENT,
        "title": f"Staff Verdict: {VERDICT_LABELS[verdict_text]}",
        "reason": reason_text,
        "metadata": metadata,
        "created_at": created_at,
    }

    saved_member = _insert_member_event(member_payload)
    source_saved = False

    if verdict_text in SOURCE_VERDICTS:
        source_payload = {
            "guild_id": guild_text,
            "user_id": user_text,
            "actor_id": actor_text,
            "actor_name": _safe_str(created_by_name, actor_text),
            "event_type": SOURCE_REVIEW_EVENT,
            "title": f"Source Verdict: {VERDICT_LABELS[verdict_text]}",
            "reason": reason_text,
            "metadata": {
                "verdict": verdict_text,
                "verdict_label": VERDICT_LABELS[verdict_text],
                "source_key": source_text,
                "trigger_user_id": user_text,
                "evidence_snapshot": evidence_payload,
                "automatic_enforcement": False,
            },
            "created_at": created_at,
        }
        _insert_member_event(source_payload)
        source_saved = True

    return {
        "member_event": saved_member,
        "identity_link": identity_link,
        "source_event_saved": source_saved,
        "verdict": verdict_text,
        "verdict_label": VERDICT_LABELS[verdict_text],
    }


def get_latest_member_review_feedback(
    *,
    guild_id: Any,
    user_id: Any,
) -> Optional[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return None

    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("event_type", MEMBER_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return None

        row = dict(rows[0])
        metadata = dict(row.get("metadata") or {})
        if _safe_str(metadata.get("verdict")).lower() == "reset":
            return None
        return row

    try:
        return _execute("get latest member review", _read)
    except Exception:
        return None


def get_member_review_history(
    *,
    guild_id: Any,
    user_id: Any,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return []

    safe_limit = max(1, min(int(limit or 10), 25))

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("event_type", MEMBER_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        return [
            dict(row)
            for row in (getattr(res, "data", None) or [])
            if isinstance(row, dict)
        ]

    try:
        return _execute("get member review history", _read)
    except Exception:
        return []


def get_latest_source_review_feedback(
    *,
    guild_id: Any,
    source_key: str,
) -> Optional[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    source_text = _safe_str(source_key)

    if not guild_text or not source_text:
        return None

    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("event_type", SOURCE_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )

        for raw in getattr(res, "data", None) or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            metadata = dict(row.get("metadata") or {})
            if _safe_str(metadata.get("source_key")) == source_text:
                return row
        return None

    try:
        return _execute("get latest source review", _read)
    except Exception:
        return None


def feedback_display_value(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""

    metadata = dict(row.get("metadata") or {})
    verdict = _safe_str(metadata.get("verdict"))
    label = _safe_str(
        metadata.get("verdict_label"),
        VERDICT_LABELS.get(verdict, verdict.replace("_", " ").title()),
    )
    actor = _safe_str(row.get("actor_name") or row.get("actor_id"), "Unknown staff")
    reason = _safe_str(row.get("reason"), "No reason supplied.")
    created_at = _safe_str(row.get("created_at"), "unknown time")
    related = _safe_str(metadata.get("related_user_id"))

    lines = [
        f"Verdict: **{label}**",
        f"By: **{actor}**",
        f"Reason: {reason[:500]}",
        f"Recorded: `{created_at}`",
    ]

    if related:
        lines.append(f"Related member: <@{related}> (`{related}`)")

    return "\n".join(lines)[:1000]


__all__ = [
    "ALT_VERDICTS",
    "BOT_VERDICTS",
    "SOURCE_VERDICTS",
    "VERDICT_LABELS",
    "feedback_display_value",
    "get_latest_member_review_feedback",
    "get_latest_source_review_feedback",
    "get_member_review_history",
    "infer_latest_source_key",
    "record_member_review_feedback",
    "source_key_from_join_context",
]
