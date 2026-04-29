from __future__ import annotations

"""
Spam Guard cleanup hardening.

The core spam guard already detects hacked-account bursts and calls
`_select_cleanup_refs` to decide which recent messages should be removed. In a
real burst, only deleting the exact trigger message is not good enough: spam can
land across multiple channels before the enforcement action finishes.

This patch keeps the existing detection/enforcement path, but widens cleanup
selection when a high-confidence rule fires:

- @everyone/@here bursts delete all recent messages from that same offender in
  the guard window, not only the matching ping rows.
- invite/url bursts delete the matching rows first, then the rest of that
  offender's recent messages up to the configured limit.
- duplicate/channel-flood bursts also sweep the offender's recent window.

It does not delete messages from unrelated users. If multiple hacked accounts
are spamming, each account gets handled by its own spam window/trigger.
"""

from typing import Any, Dict, List, Set, Tuple

_PATCHED = False
_ORIGINAL_SELECT_CLEANUP_REFS = None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _row_identity(row: Dict[str, Any]) -> Tuple[int, int]:
    return (_safe_int(row.get("channel_id"), 0), _safe_int(row.get("message_id"), 0))


def _row_evidence(row: Dict[str, Any]) -> Set[str]:
    raw = row.get("evidence")
    if isinstance(raw, set):
        return {str(x) for x in raw if str(x).strip()}
    if isinstance(raw, list):
        return {str(x) for x in raw if str(x).strip()}
    return set()


def _has_any_suspicious_evidence(row: Dict[str, Any]) -> bool:
    evidence = _row_evidence(row)
    if evidence.intersection({"blocked_invite", "invite_url", "non_invite_url", "everyone_ping"}):
        return True
    try:
        if _safe_int(row.get("invite_count"), 0) > 0:
            return True
        if _safe_int(row.get("non_invite_url_count"), 0) > 0:
            return True
    except Exception:
        pass
    return False


def _hardened_select_cleanup_refs(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    original = _ORIGINAL_SELECT_CLEANUP_REFS
    if not callable(original):
        return []

    selected = list(original(*args, **kwargs) or [])

    recent_messages = list(kwargs.get("recent_messages") or [])
    delete_limit = _safe_int(kwargs.get("delete_limit"), 0)
    if delete_limit <= 0 or not recent_messages:
        return selected[: max(0, delete_limit)]

    fired_invite_rule = bool(kwargs.get("fired_invite_rule"))
    fired_duplicate_rule = bool(kwargs.get("fired_duplicate_rule"))
    fired_everyone_rule = bool(kwargs.get("fired_everyone_rule"))
    fired_url_rule = bool(kwargs.get("fired_url_rule"))
    fired_channel_flood_rule = bool(kwargs.get("fired_channel_flood_rule"))
    current_norm = str(kwargs.get("current_norm") or "")

    high_confidence = any(
        (
            fired_invite_rule,
            fired_duplicate_rule,
            fired_everyone_rule,
            fired_url_rule,
            fired_channel_flood_rule,
        )
    )
    if not high_confidence:
        return selected[:delete_limit]

    ordered = sorted(
        [r for r in recent_messages if isinstance(r, dict)],
        key=lambda x: float(x.get("ts", 0.0) or 0.0),
        reverse=True,
    )

    seen: Set[Tuple[int, int]] = set()
    hardened: List[Dict[str, Any]] = []

    def add(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            ident = _row_identity(row)
            if ident == (0, 0) or ident in seen:
                continue
            seen.add(ident)
            hardened.append(row)
            if len(hardened) >= delete_limit:
                return

    # Keep the original function's exact priority first.
    add([r for r in selected if isinstance(r, dict)])

    # Then add any directly suspicious rows the original selector missed.
    if len(hardened) < delete_limit:
        add([r for r in ordered if _has_any_suspicious_evidence(r)])

    # Duplicate spam often has little evidence besides matching normalized body.
    if len(hardened) < delete_limit and current_norm:
        add([r for r in ordered if str(r.get("norm") or "") == current_norm])

    # For high-confidence compromise behavior, sweep the rest of that user's
    # recent window up to the configured limit. The window is already keyed by
    # guild/user in spam_guard, so this should not touch unrelated users.
    if len(hardened) < delete_limit:
        add(ordered)

    return hardened[:delete_limit]


def apply_spam_cleanup_hardening() -> bool:
    global _PATCHED, _ORIGINAL_SELECT_CLEANUP_REFS
    if _PATCHED:
        return True
    try:
        from .. import spam_guard

        original = getattr(spam_guard, "_select_cleanup_refs", None)
        if not callable(original):
            print("⚠️ public_spam_cleanup_hardening: spam_guard._select_cleanup_refs missing; skipped")
            return False
        if getattr(original, "_stoney_hardened_cleanup", False):
            _PATCHED = True
            return True

        _ORIGINAL_SELECT_CLEANUP_REFS = original
        setattr(_hardened_select_cleanup_refs, "_stoney_hardened_cleanup", True)
        spam_guard._select_cleanup_refs = _hardened_select_cleanup_refs  # type: ignore[attr-defined]
        _PATCHED = True
        print("✅ public_spam_cleanup_hardening: burst cleanup selection widened")
        return True
    except Exception as e:
        try:
            print(f"⚠️ public_spam_cleanup_hardening failed: {repr(e)}")
        except Exception:
            pass
        return False


def register_public_spam_cleanup_hardening(bot, tree) -> None:
    _ = bot, tree
    apply_spam_cleanup_hardening()


__all__ = ["register_public_spam_cleanup_hardening", "apply_spam_cleanup_hardening"]
