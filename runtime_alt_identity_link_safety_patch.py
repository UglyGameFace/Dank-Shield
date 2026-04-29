from __future__ import annotations

"""
Alt identity link safety layer.

Purpose:
- Never let the risk profile tie a user to themself.
- Dedupe known/possible linked accounts before modlog/dashboard output.
- Separate hard alt ties from soft similarity matches.
- Give staff a clear "tied to / possibly similar to" list without pretending
  weak cluster/name matches are proof.

This patch does not add paid/intelligence-network data. It only makes the bot's
existing local evidence safer and clearer.
"""

import builtins
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

_HARD_REASONS = {
    "identity_fingerprint_match",
    "manual_alt_link",
    "manual_confirmed_duplicate",
    "confirmed_duplicate",
}

_LIKELY_REASONS = {
    "manual_review_likely_link",
    "same_person_likely",
}

_SOFT_REASONS = {
    "same_fingerprint",
    "shared_behavior_fingerprint",
    "similar_recent_username",
    "same_age_bucket",
    "age_bucket_cluster",
    "name_similarity",
    "cluster_triad",
}


def _log(message: str) -> None:
    try:
        print(f"🔗 runtime_alt_identity_link_safety {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(value)
    except Exception:
        try:
            return int(str(value).strip())
        except Exception:
            return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_list(value: Any) -> List[Any]:
    try:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        if value is None:
            return []
        return [value]
    except Exception:
        return []


def _string_list(value: Any, max_items: int = 30) -> List[str]:
    out: List[str] = []
    for item in _safe_list(value):
        text = _safe_str(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _append_unique(existing: Any, additions: Iterable[str], max_items: int = 30) -> List[str]:
    out = _string_list(existing, max_items=max_items)
    for item in additions:
        text = _safe_str(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out[:max_items]


def _reason_from_row(row: Mapping[str, Any]) -> str:
    raw = _safe_str(
        row.get("reason")
        or row.get("link_type")
        or row.get("source")
        or row.get("match_type")
        or row.get("cluster_reason")
        or ""
    )
    if raw.startswith("name_similarity:"):
        return "name_similarity"
    return raw or "unknown"


def _relation_from_reason(reason: str) -> tuple[str, str]:
    r = _safe_str(reason).lower()
    if r in _HARD_REASONS:
        return "known_alt", "hard"
    if r in _LIKELY_REASONS:
        return "likely_same_person", "reviewed_likely"
    if r in _SOFT_REASONS or any(token in r for token in ("similar", "cluster", "fingerprint", "age_bucket")):
        return "possible_related_account", "heuristic"
    return "possible_related_account", "unknown"


def _member_label(guild: Any, user_id: int, row: Mapping[str, Any]) -> tuple[str, Optional[str], Optional[str]]:
    member = None
    try:
        if guild is not None and user_id > 0:
            member = guild.get_member(int(user_id))
    except Exception:
        member = None

    if member is not None:
        try:
            display = _safe_str(getattr(member, "display_name", None) or getattr(member, "global_name", None) or getattr(member, "name", None), f"User {user_id}")
            username = _safe_str(getattr(member, "name", None), None) or None
            return f"{display} (`{user_id}`)", display, username
        except Exception:
            pass

    display_name = _safe_str(row.get("display_name") or row.get("display") or row.get("name"), "")
    username = _safe_str(row.get("username") or row.get("user_name"), "")
    label_name = display_name or username or f"User {user_id}"
    return f"{label_name} (`{user_id}`)", (display_name or None), (username or None)


def _normalize_cluster_row(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, Mapping):
        return None
    uid = _safe_int(row.get("user_id") or row.get("matched_user_id") or row.get("other_user_id") or row.get("id"), 0)
    if uid <= 0:
        return None
    out = dict(row)
    out["user_id"] = uid
    out["reason"] = _reason_from_row(row)
    return out


def _sanitize_cluster_rows(member: Any, profile: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
    target_id = _safe_int(getattr(member, "id", None) or profile.get("user_id"), 0)
    guild = getattr(member, "guild", None)
    seen: set[int] = set()
    sanitized: List[Dict[str, Any]] = []
    suppressed_self = 0

    for raw in _safe_list(profile.get("cluster_members")):
        row = _normalize_cluster_row(raw)
        if row is None:
            continue
        uid = _safe_int(row.get("user_id"), 0)
        if uid <= 0:
            continue
        if target_id and uid == target_id:
            suppressed_self += 1
            continue
        if uid in seen:
            continue
        seen.add(uid)

        reason = _reason_from_row(row)
        relation, confidence = _relation_from_reason(reason)
        label, display_name, username = _member_label(guild, uid, row)
        row.update(
            {
                "user_id": uid,
                "label": label,
                "display_name": display_name,
                "username": username,
                "relation": relation,
                "confidence": confidence,
                "reason": reason,
                "mention": f"<@{uid}>",
            }
        )
        sanitized.append(row)
        if len(sanitized) >= 12:
            break

    return sanitized, suppressed_self


def _summary_lines(rows: List[Dict[str, Any]], *, hard_only: bool = False, max_items: int = 5) -> List[str]:
    lines: List[str] = []
    for row in rows:
        relation = _safe_str(row.get("relation"))
        confidence = _safe_str(row.get("confidence"))
        if hard_only and relation not in {"known_alt", "likely_same_person"}:
            continue
        label = _safe_str(row.get("label"), f"User {row.get('user_id')}")
        reason = _safe_str(row.get("reason"), "unknown")
        if relation == "known_alt":
            prefix = "Known alt tie"
        elif relation == "likely_same_person":
            prefix = "Likely same person"
        else:
            prefix = "Possible related account"
        lines.append(f"{prefix}: {label} — {reason} ({confidence})")
        if len(lines) >= max_items:
            break
    return lines


def _escalate_for_known_ties(profile: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    hard_count = sum(1 for row in rows if row.get("relation") == "known_alt")
    likely_count = sum(1 for row in rows if row.get("relation") == "likely_same_person")
    if hard_count <= 0 and likely_count <= 0:
        return

    current = max(_safe_int(profile.get("risk_score"), 0), _safe_int(profile.get("score"), 0))
    if hard_count > 0:
        score = max(current, 92)
        profile["risk_score"] = score
        profile["score"] = score
        profile["risk_level"] = "critical"
        profile["level"] = "critical"
        profile["evidence_tier"] = "confirmed_duplicate"
        profile["review_recommendation"] = "hard_evidence_review_or_enforce"
        profile["recommended_action"] = "hard_evidence_review_or_enforce"
    elif likely_count > 0:
        score = max(current, 72)
        profile["risk_score"] = score
        profile["score"] = score
        if _safe_str(profile.get("risk_level"), "low") in {"low", "medium"}:
            profile["risk_level"] = "high"
            profile["level"] = "high"
        if _safe_str(profile.get("evidence_tier"), "clear") in {"clear", "suspicious"}:
            profile["evidence_tier"] = "strongly_linked"
        profile["review_recommendation"] = "staff_review_strong_identity_signal"
        profile["recommended_action"] = "staff_review_strong_identity_signal"


def _apply_alt_link_safety(member: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        return profile

    try:
        if bool(profile.get("is_bot_account")):
            return profile

        rows, suppressed_self = _sanitize_cluster_rows(member, profile)
        hard_or_likely = [row for row in rows if row.get("relation") in {"known_alt", "likely_same_person"}]
        soft = [row for row in rows if row.get("relation") not in {"known_alt", "likely_same_person"}]

        profile["cluster_members"] = rows
        profile["known_alt_ties"] = hard_or_likely
        profile["possible_related_accounts"] = soft
        profile["known_alt_tie_count"] = len(hard_or_likely)
        profile["possible_related_account_count"] = len(soft)
        profile["self_match_suppressed_count"] = int(suppressed_self)
        profile["alt_link_safety_version"] = "v1_self_dedupe_and_clear_relations"

        profile["same_fingerprint_count"] = min(_safe_int(profile.get("same_fingerprint_count"), 0), len(rows)) if rows else 0
        profile["alt_cluster_size"] = len(rows) + 1 if rows else 0

        summary_lines = _summary_lines(rows, hard_only=False, max_items=5)
        hard_lines = _summary_lines(rows, hard_only=True, max_items=5)
        if summary_lines:
            profile["alt_tie_summary"] = "\n".join(summary_lines)
        if hard_lines:
            profile["known_alt_tie_summary"] = "\n".join(hard_lines)

        reason_lines: List[str] = []
        if hard_lines:
            reason_lines.extend(hard_lines)
        elif summary_lines:
            reason_lines.append("Possible related accounts found, but this is heuristic and not hard proof.")
            reason_lines.extend(summary_lines[:3])
        if suppressed_self:
            reason_lines.append(f"Ignored {suppressed_self} self-match candidate(s) so the account cannot be linked to itself.")

        if reason_lines:
            profile["reasons"] = _append_unique(profile.get("reasons"), reason_lines, max_items=20)
            profile["risk_reasons"] = _append_unique(profile.get("risk_reasons"), reason_lines, max_items=20)

        _escalate_for_known_ties(profile, rows)
        return profile
    except Exception:
        return profile


def _patch_raidguard(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return
    patch_key = f"{module_name}:alt_identity_link_safety"
    if patch_key in _PATCHED_MODULES:
        return

    original = getattr(module, "build_member_risk_profile", None)
    if not callable(original) or getattr(original, "_alt_identity_link_safety_wrapped", False):
        return

    def _build_member_risk_profile_safe(member: Any) -> Dict[str, Any]:
        profile = original(member)
        return _apply_alt_link_safety(member, profile)

    try:
        setattr(_build_member_risk_profile_safe, "_alt_identity_link_safety_wrapped", True)
    except Exception:
        pass

    setattr(module, "build_member_risk_profile", _build_member_risk_profile_safe)
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}.build_member_risk_profile with self-dedupe alt-link safety")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.raidguard")
        if module is not None:
            _patch_raidguard(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.raidguard" or name.endswith(".raidguard"):
            target = sys.modules.get("stoney_verify.raidguard") or sys.modules.get(name)
            if target is not None:
                _patch_raidguard(target)
        else:
            _maybe_patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded()
_log("loaded; self-dedupe alt-link safety active")
