from __future__ import annotations

"""Dank Design repair confidence scoring.

Pure helper module. It does not mutate Discord, roles, permissions, database
settings, channel order, topics, slowmode, NSFW state, or ticket config.

The key rule: do not rely on a fixed list of decorative heading marks. Any user
can invent their own style. We score style loss generically.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any

MAX_DISCORD_CHANNEL_NAME_LENGTH = 100

SAFE_AUTO_FIX = "SAFE_AUTO_FIX"
REVIEW_ONLY = "REVIEW_ONLY"
BLOCKED_AESTHETIC_DOWNGRADE = "BLOCKED_AESTHETIC_DOWNGRADE"
BLOCKED_SYSTEM_SURFACE = "BLOCKED_SYSTEM_SURFACE"
BLOCKED_DISCORD_LIMIT = "BLOCKED_DISCORD_LIMIT"
BLOCKED_LOW_CONFIDENCE = "BLOCKED_LOW_CONFIDENCE"

_SYSTEM_WORDS = {
    "archive",
    "archived",
    "transcript",
    "transcripts",
    "mod-log",
    "modlog",
    "staff",
    "ticket",
    "tickets",
    "verify",
    "verification",
    "welcome-exit",
    "logs",
    "log",
}


def _text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _strip_code_format(value: Any) -> str:
    text = _text(value)
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].strip()
    return text


def _ascii_core(value: Any) -> str:
    text = _strip_code_format(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s/-]+", "", text, flags=re.UNICODE)
    text = text.replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _symbol_count(value: Any) -> int:
    text = _strip_code_format(value)
    total = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(("S", "P")) and ch not in {"-", "_"}:
            total += 1
    return total


def _styled_unicode_letter_count(value: Any) -> int:
    text = _strip_code_format(value)
    return sum(1 for ch in text if ord(ch) > 127 and ch.isalpha())


def _uppercase_ratio(value: Any) -> float:
    letters = [ch for ch in _strip_code_format(value) if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isupper()) / max(1, len(letters))


def _display_score(value: Any) -> int:
    """Generic visual style score.

    This intentionally supports any user-made decoration instead of a small
    hardcoded symbol list.
    """

    text = _strip_code_format(value)
    if not text:
        return 0

    score = 0
    if " " in text:
        score += 2
    if "/" in text:
        score += 2
    if _uppercase_ratio(text) >= 0.55 and len([ch for ch in text if ch.isalpha()]) >= 3:
        score += 2

    score += min(5, _symbol_count(text))
    score += min(5, _styled_unicode_letter_count(text))

    # Repeated non-word decoration or deliberate framing.
    if re.search(r"([^\w\s])\1+", text, flags=re.UNICODE):
        score += 2
    if len(re.findall(r"[^\w\s-]", text, flags=re.UNICODE)) >= 2:
        score += 2

    return score


def _looks_plain_slug(value: Any) -> bool:
    text = _strip_code_format(value).strip()
    if not text:
        return False

    core = re.sub(r"^[^\w#]+", "", text, flags=re.UNICODE).strip()
    if not core:
        return False

    return (
        " " not in core
        and core == core.lower()
        and ("-" in core or core.islower())
        and _display_score(core) <= 1
    )


def _looks_system_surface(value: Any) -> bool:
    core = _ascii_core(value)
    if not core:
        return False
    tokens = set(core.split("-"))
    if tokens & _SYSTEM_WORDS:
        return True
    return any(word in core for word in _SYSTEM_WORDS)


def _similarity(before: str, after: str) -> float:
    left = _ascii_core(before)
    right = _ascii_core(after)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _is_aesthetic_downgrade(before: str, after: str, *, kind: str) -> bool:
    before_score = _display_score(before)
    after_score = _display_score(after)

    if kind == "category" and before_score >= 3 and _looks_plain_slug(after):
        return True

    if before_score >= 5 and after_score <= 1:
        return True

    if _styled_unicode_letter_count(before) >= 2 and _styled_unicode_letter_count(after) == 0:
        return True

    if _symbol_count(before) >= 2 and _symbol_count(after) == 0:
        return True

    return False


def _safe_line(before: str, after: str, reason: str) -> str:
    return f"• `{before}` → `{after}` — {reason}"[:260]


def score_repair_item(item: Mapping[str, Any], *, context: str = "generic") -> dict[str, Any]:
    status = _text(item.get("status"))
    kind = _text(item.get("kind"), "text")
    before = _text(item.get("before"))
    after = _text(item.get("after"))

    if status in {"protected", "failed"}:
        return {
            "classification": BLOCKED_SYSTEM_SURFACE,
            "confidence": 0,
            "reason": "Protected/failed row is not eligible for automatic apply.",
            "before": before,
            "after": after,
        }

    if status != "changed":
        return {
            "classification": SAFE_AUTO_FIX,
            "confidence": 100,
            "reason": "No rename needed.",
            "before": before,
            "after": after,
        }

    if not after:
        return {
            "classification": BLOCKED_DISCORD_LIMIT,
            "confidence": 0,
            "reason": "Generated name is blank.",
            "before": before,
            "after": after,
        }

    if len(after) > MAX_DISCORD_CHANNEL_NAME_LENGTH:
        return {
            "classification": BLOCKED_DISCORD_LIMIT,
            "confidence": 0,
            "reason": f"Generated name is over Discord's {MAX_DISCORD_CHANNEL_NAME_LENGTH}-character limit.",
            "before": before,
            "after": after,
        }

    if context == "live_majority" and _is_aesthetic_downgrade(before, after, kind=kind):
        return {
            "classification": BLOCKED_AESTHETIC_DOWNGRADE,
            "confidence": 0,
            "reason": "Would simplify or strip this server's existing visual style.",
            "before": before,
            "after": after,
        }

    if _looks_system_surface(before):
        return {
            "classification": REVIEW_ONLY,
            "confidence": 55,
            "reason": "System/ticket/log-looking surface needs review before rename.",
            "before": before,
            "after": after,
        }

    ratio = _similarity(before, after)

    if ratio >= 0.88:
        return {
            "classification": SAFE_AUTO_FIX,
            "confidence": 92,
            "reason": "Small naming drift only.",
            "before": before,
            "after": after,
        }

    if ratio >= 0.68:
        return {
            "classification": REVIEW_ONLY,
            "confidence": 65,
            "reason": "Rename changes visible wording; review recommended.",
            "before": before,
            "after": after,
        }

    return {
        "classification": BLOCKED_LOW_CONFIDENCE,
        "confidence": 30,
        "reason": "Rename changes too much to apply automatically.",
        "before": before,
        "after": after,
    }


def evaluate_repair_plan(items: Iterable[Mapping[str, Any]], *, context: str = "generic") -> dict[str, Any]:
    scored = [score_repair_item(item, context=context) for item in items]
    changed_scores = [row for row in scored if _text(row.get("before")) != _text(row.get("after"))]
    counts = Counter(_text(row.get("classification")) for row in changed_scores)

    blocked_classes = {
        BLOCKED_AESTHETIC_DOWNGRADE,
        BLOCKED_SYSTEM_SURFACE,
        BLOCKED_DISCORD_LIMIT,
        BLOCKED_LOW_CONFIDENCE,
    }

    blocked = [row for row in changed_scores if row.get("classification") in blocked_classes]
    review = [row for row in changed_scores if row.get("classification") == REVIEW_ONLY]
    safe = [row for row in changed_scores if row.get("classification") == SAFE_AUTO_FIX]

    if blocked:
        label = "Blocked"
        apply_allowed = False
    elif review:
        label = "Review"
        apply_allowed = False
    elif safe:
        label = "High"
        apply_allowed = True
    else:
        label = "No changes"
        apply_allowed = False

    total_confidence = 100
    total_confidence -= len(blocked) * 25
    total_confidence -= len(review) * 8
    total_confidence = max(0, min(100, total_confidence))

    blocked_lines = [
        _safe_line(_text(row.get("before")), _text(row.get("after")), _text(row.get("reason")))
        for row in blocked[:6]
    ]
    review_lines = [
        _safe_line(_text(row.get("before")), _text(row.get("after")), _text(row.get("reason")))
        for row in review[:6]
    ]

    return {
        "label": label,
        "score": total_confidence,
        "apply_allowed": apply_allowed,
        "counts": dict(counts),
        "safe_count": len(safe),
        "review_count": len(review),
        "blocked_count": len(blocked),
        "blocked_lines": blocked_lines,
        "review_lines": review_lines,
    }


def confidence_summary_text(result: Mapping[str, Any]) -> str:
    counts = result.get("counts") if isinstance(result.get("counts"), Mapping) else {}
    return (
        f"Apply confidence: **{_text(result.get('label'), 'Unknown')}**\n"
        f"Score: **{int(result.get('score', 0) or 0)}/100**\n"
        f"Safe: **{int(result.get('safe_count', 0) or 0)}**\n"
        f"Needs review: **{int(result.get('review_count', 0) or 0)}**\n"
        f"Blocked: **{int(result.get('blocked_count', 0) or 0)}**\n"
        f"Aesthetic blocks: **{int(counts.get(BLOCKED_AESTHETIC_DOWNGRADE, 0) or 0)}**"
    )[:1024]


__all__ = [
    "BLOCKED_AESTHETIC_DOWNGRADE",
    "BLOCKED_DISCORD_LIMIT",
    "BLOCKED_LOW_CONFIDENCE",
    "BLOCKED_SYSTEM_SURFACE",
    "REVIEW_ONLY",
    "SAFE_AUTO_FIX",
    "confidence_summary_text",
    "evaluate_repair_plan",
    "score_repair_item",
]
