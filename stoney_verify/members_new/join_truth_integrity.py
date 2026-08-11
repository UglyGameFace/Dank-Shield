from __future__ import annotations

"""Canonical truth helpers for member entry and verification attribution.

Join attribution and verification approval are related but are not the same
fact. A confirmed staff approval must not invent or overwrite a member's
original invite source. These helpers normalize historical field aliases,
serialize invite cache work per guild, merge stronger evidence without hiding
conflicts, and preserve the original entry-quality fields while approval truth
is carried separately in metadata.
"""

import asyncio
from typing import Any, Dict, Mapping, Optional


_INVITE_LOCKS: dict[int, asyncio.Lock] = {}
_INVITE_READY: set[int] = set()

_ENTRY_ALIASES = {
    "inviter_id": "invited_by",
    "inviter_name": "invited_by_name",
    "invite_creator_id": "invited_by",
    "invite_creator_name": "invited_by_name",
    "entry_source": "join_source",
    "source": "join_source",
    "ticket_id": "source_ticket_id",
    "verification_ticket_id": "source_ticket_id",
}

_ENTRY_FIELDS = (
    "entry_method",
    "join_source",
    "invite_code",
    "invited_by",
    "invited_by_name",
    "vouched_by",
    "vouched_by_name",
    "entry_reason",
    "join_note",
    "channel_id",
    "channel_name",
    "vanity_used",
    "entry_truth_quality",
    "entry_confidence",
    "entry_quality_reason",
    "entry_conflict",
)

_APPROVAL_FIELDS = (
    "approved_by",
    "approved_by_name",
    "approval_reason",
    "verification_source",
    "source_ticket_id",
    "approval_truth_quality",
    "approval_confidence",
    "approval_quality_reason",
    "approval_conflict",
)

_UNKNOWN = {
    "",
    "unknown",
    "unknown_join",
    "invite_unresolved",
    "invite_cache_warming",
    "invite_tracking_unavailable",
    "none",
    "null",
}


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def invite_lock_for(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _INVITE_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _INVITE_LOCKS[gid] = lock
    return lock


def mark_invite_cache_ready(guild_id: int, ready: bool = True) -> None:
    gid = int(guild_id)
    if ready:
        _INVITE_READY.add(gid)
    else:
        _INVITE_READY.discard(gid)


def invite_cache_ready(guild_id: int) -> bool:
    return int(guild_id) in _INVITE_READY


def normalize_join_context(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = dict(value or {})
    out: Dict[str, Any] = dict(raw)
    for old, canonical in _ENTRY_ALIASES.items():
        if out.get(canonical) in (None, "") and out.get(old) not in (None, ""):
            out[canonical] = out.get(old)

    entry_method = _safe_str(out.get("entry_method")) or "unknown_join"
    join_source = _safe_str(out.get("join_source")) or entry_method
    verification_source = _safe_str(out.get("verification_source")) or "unknown"
    out["entry_method"] = entry_method
    out["join_source"] = join_source
    out["verification_source"] = verification_source
    out["entry_conflict"] = bool(out.get("entry_conflict", False))
    out["approval_conflict"] = bool(out.get("approval_conflict", False))
    out["entry_confidence"] = max(0, min(100, _safe_int(out.get("entry_confidence"), 0)))
    out["approval_confidence"] = max(0, min(100, _safe_int(out.get("approval_confidence"), 0)))
    if out.get("vanity_used") is not None:
        out["vanity_used"] = bool(out.get("vanity_used"))
    return out


def _known(value: Any) -> bool:
    return _safe_str(value).lower() not in _UNKNOWN


def _stronger(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    old_conf = _safe_int(existing.get("entry_confidence"), 0)
    new_conf = _safe_int(incoming.get("entry_confidence"), 0)
    if new_conf != old_conf:
        return new_conf > old_conf
    old_quality = _safe_str(existing.get("entry_truth_quality")).lower()
    new_quality = _safe_str(incoming.get("entry_truth_quality")).lower()
    rank = {"unknown": 0, "partial": 1, "confirmed": 2}
    return rank.get(new_quality, 0) > rank.get(old_quality, 0)


def entry_conflicts(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> list[str]:
    conflicts: list[str] = []
    for field in ("invite_code", "invited_by", "join_source", "entry_method"):
        old = _safe_str(existing.get(field))
        new = _safe_str(incoming.get(field))
        if _known(old) and _known(new) and old != new:
            # manual/ticket verification is approval evidence, not a competing
            # join source, so it must never create a fake entry conflict.
            if field in {"join_source", "entry_method"} and new in {
                "manual_verification", "ticket_verification", "ticket_staff_approval",
                "vc_staff_approval", "verification",
            }:
                continue
            conflicts.append(field)
    return conflicts


def merge_join_context(
    existing: Optional[Mapping[str, Any]],
    incoming: Optional[Mapping[str, Any]],
    *,
    incoming_is_approval: bool = False,
) -> Dict[str, Any]:
    """Merge evidence without letting approval overwrite original join truth."""
    old = normalize_join_context(existing)
    new = normalize_join_context(incoming)
    if not old:
        return new
    if not new:
        return old

    merged = dict(old)
    conflicts = entry_conflicts(old, new)

    if incoming_is_approval:
        # Approval fields are allowed to advance independently. Entry fields only
        # backfill values that were genuinely unknown; a staff decision does not
        # redefine how the member originally joined.
        for field in _APPROVAL_FIELDS:
            if new.get(field) not in (None, "", 0):
                merged[field] = new.get(field)
        for field in _ENTRY_FIELDS:
            if not _known(merged.get(field)) and _known(new.get(field)) and field not in {
                "entry_truth_quality", "entry_confidence", "entry_quality_reason", "entry_conflict"
            }:
                merged[field] = new.get(field)
    else:
        if _stronger(old, new):
            for field in _ENTRY_FIELDS:
                if new.get(field) not in (None, ""):
                    merged[field] = new.get(field)
        else:
            for field in _ENTRY_FIELDS:
                if not _known(merged.get(field)) and _known(new.get(field)):
                    merged[field] = new.get(field)
        for field in _APPROVAL_FIELDS:
            if new.get(field) not in (None, "", 0):
                merged[field] = new.get(field)

    if conflicts:
        merged["entry_conflict"] = True
        detail = "Conflicting join evidence: " + ", ".join(conflicts) + "."
        current = _safe_str(merged.get("entry_quality_reason"))
        if detail not in current:
            merged["entry_quality_reason"] = (current + " " + detail).strip()
        # A contradiction cannot remain labelled unconditionally confirmed.
        if _safe_str(merged.get("entry_truth_quality")).lower() == "confirmed":
            merged["entry_truth_quality"] = "partial"
            merged["entry_confidence"] = min(_safe_int(merged.get("entry_confidence"), 70), 70)

    return normalize_join_context(merged)


def approval_context(
    *,
    approved_by: Any,
    approved_by_name: Any,
    verification_source: Any,
    approval_reason: Any,
    source_ticket_id: Any = None,
) -> Dict[str, Any]:
    return {
        "approved_by": _safe_str(approved_by) or None,
        "approved_by_name": _safe_str(approved_by_name) or None,
        "verification_source": _safe_str(verification_source) or "verification",
        "approval_reason": _safe_str(approval_reason) or None,
        "source_ticket_id": _safe_str(source_ticket_id) or None,
        "approval_truth_quality": "confirmed",
        "approval_confidence": 95 if _safe_str(source_ticket_id) else 90,
        "approval_quality_reason": "Approval came from an explicit authenticated staff workflow.",
        "approval_conflict": False,
    }


def merge_with_persisted_member_sync(
    sb: Any,
    guild_id: str,
    user_id: str,
    incoming: Mapping[str, Any],
    *,
    incoming_is_approval: bool = False,
) -> Dict[str, Any]:
    """Read the current canonical row and merge it with new evidence.

    If the optional row cannot be read, return normalized incoming evidence so
    older schemas continue to work rather than losing the update entirely.
    """
    existing: Dict[str, Any] = {}
    try:
        response = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            existing = dict(rows[0])
    except Exception:
        existing = {}
    return merge_join_context(existing, incoming, incoming_is_approval=incoming_is_approval)


__all__ = [
    "approval_context",
    "entry_conflicts",
    "invite_cache_ready",
    "invite_lock_for",
    "mark_invite_cache_ready",
    "merge_join_context",
    "merge_with_persisted_member_sync",
    "normalize_join_context",
]
