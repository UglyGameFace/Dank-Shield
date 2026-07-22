from __future__ import annotations

"""Native enrichment for member alt and spam-account risk.

This module is intentionally pure: it does not read Discord state or the database
and it never monkey-patches another module. ``raidguard`` owns collection of join
and identity evidence; this module turns that evidence into separate alt, spam,
and overall review dimensions.
"""

import re
from typing import Any, Dict, Iterable, Mapping

_LISTING_SOURCE_TERMS = (
    "disboard",
    "discord.me",
    "discordservers",
    "top.gg",
    "listing",
    "server list",
    "server-list",
)

_YEAR_SUFFIX_RE = re.compile(r"(?:19|20)\d{2}$")
_LONG_DIGIT_SUFFIX_RE = re.compile(r"\d{5,}$")
_REPEATED_DIGIT_RE = re.compile(r"(\d)\1{3,}$")

_BEHAVIOR_WEIGHTS = {
    "spam_guard_triggered": 18,
    "invite_flood": 35,
    "duplicate_message_burst": 28,
    "mention_burst": 32,
    "url_flood": 38,
    "cross_channel_flood": 24,
    "rapid_message_burst": 18,
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", ""}:
        return False
    return bool(default)


def _safe_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _dedupe(values: Iterable[Any], limit: int = 20) -> list[str]:
    out: list[str] = []
    for raw in values:
        text = _safe_text(raw)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _source_blob(join_context: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "source",
        "join_source",
        "entry_method",
        "verification_source",
        "invite_source",
        "invite_code",
        "source_key",
        "entry_reason",
    ):
        values.append(_safe_text(join_context.get(key)).lower())
    return " ".join(value for value in values if value)


def _is_listing_source(join_context: Mapping[str, Any]) -> bool:
    blob = _source_blob(join_context)
    return bool(blob and any(term in blob for term in _LISTING_SOURCE_TERMS))


def _risk_level(score: int) -> str:
    value = max(0, min(100, int(score)))
    if value >= 85:
        return "critical"
    if value >= 65:
        return "high"
    if value >= 35:
        return "medium"
    return "low"


def _numeric_profile_score(
    *,
    username: str,
    account_age_days: int,
    default_avatar: bool,
) -> tuple[int, list[str], list[str]]:
    """Return conservative bot-farm hints without treating birth years as proof."""

    clean = _safe_text(username).lower()
    reasons: list[str] = []
    flags: list[str] = []
    score = 0

    if not clean:
        return 0, reasons, flags

    year_match = _YEAR_SUFFIX_RE.search(clean)
    long_digits = _LONG_DIGIT_SUFFIX_RE.search(clean)
    repeated_digits = _REPEATED_DIGIT_RE.search(clean)

    if year_match and account_age_days >= 30 and not default_avatar:
        # Common human username style (name + birth year). Explicitly neutral.
        flags.append("ordinary_year_suffix")
        return 0, reasons, flags

    if long_digits:
        flags.append("long_numeric_suffix")
        score += 10
        reasons.append("Username ends with a long generated-looking number sequence.")

    if repeated_digits:
        flags.append("repeated_numeric_suffix")
        score += 7
        reasons.append("Username ends with a heavily repeated digit pattern.")

    if account_age_days <= 3 and default_avatar and (long_digits or repeated_digits):
        flags.append("fresh_generated_profile_combo")
        score += 18
        reasons.append(
            "Fresh account, default avatar, and generated-looking numeric username appeared together."
        )
    elif account_age_days <= 7 and default_avatar and long_digits:
        flags.append("new_generated_profile_combo")
        score += 10

    # Profile shape alone is review context, never identity proof.
    return min(44, score), reasons, flags


def _spam_behavior_score(
    behavior_context: Mapping[str, Any],
) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    flags: list[str] = []

    labels = {
        "spam_guard_triggered": "SpamGuard confirmed a behavior threshold was crossed.",
        "invite_flood": "Repeated invite-link flooding was observed.",
        "duplicate_message_burst": "Repeated duplicate-message spam was observed.",
        "mention_burst": "Repeated @everyone/@here behavior was observed.",
        "url_flood": "Rapid URL posting was observed.",
        "cross_channel_flood": "Rapid posting across multiple channels was observed.",
        "rapid_message_burst": "A rapid message burst was observed.",
    }

    for key, weight in _BEHAVIOR_WEIGHTS.items():
        if not _safe_bool(behavior_context.get(key), False):
            continue
        flags.append(key)
        score += int(weight)
        reasons.append(labels[key])

    action = _safe_text(behavior_context.get("action_taken")).lower()
    if action.startswith(("ban", "kick", "quarantine", "timeout")):
        score += 12
    elif action in {"delete-only", "shield-alert-only", "log-only"}:
        score += 4

    deleted_count = max(0, _safe_int(behavior_context.get("deleted_count"), 0))
    channel_count = max(0, _safe_int(behavior_context.get("channel_count"), 0))
    if deleted_count >= 5:
        score += 8
    if channel_count >= 3:
        score += 6

    extra_reasons = behavior_context.get("reasons")
    if isinstance(extra_reasons, (list, tuple, set)):
        reasons.extend(_safe_text(item) for item in extra_reasons)

    return min(100, score), _dedupe(reasons, 12), _dedupe(flags, 12)


def enrich_member_risk_profile(
    member: Any,
    profile: Mapping[str, Any],
    *,
    join_context: Mapping[str, Any] | None = None,
    behavior_context: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Add calibrated profile and behavior dimensions to a raidguard profile."""

    out: Dict[str, Any] = dict(profile or {})
    join = dict(join_context or {})
    behavior = dict(behavior_context or {})

    if _safe_bool(out.get("is_bot_account"), False) or bool(
        getattr(member, "bot", False)
    ):
        out.update(
            {
                "alt_evidence_tier": "excluded_bot",
                "alt_risk_score": 0,
                "spam_risk_score": 0,
                "spam_risk_level": "low",
                "profile_risk_score": 0,
                "risk_dimensions": {
                    "alt": {"score": 0, "tier": "excluded_bot"},
                    "spam": {"score": 0, "level": "low"},
                    "profile": {"score": 0, "level": "low"},
                },
                "post_join_behavior_flags": [],
                "review_verdict": "OFFICIAL BOT — REVIEW PERMISSIONS",
                "listing_source": _is_listing_source(join),
            }
        )
        return out

    username = _safe_text(
        getattr(member, "name", None)
        or out.get("username")
        or out.get("display_name")
    )
    account_age_days = max(
        0,
        _safe_int(
            out.get("account_age_days"),
            _safe_int(join.get("account_age_days"), 999999),
        ),
    )
    default_avatar = _safe_bool(out.get("default_avatar"), False)

    base_score = max(
        0,
        min(
            100,
            _safe_int(
                out.get("risk_score"),
                _safe_int(out.get("score"), 0),
            ),
        ),
    )
    alt_tier = _safe_text(out.get("evidence_tier") or "clear").lower()
    if alt_tier not in {
        "clear",
        "suspicious",
        "strongly_linked",
        "confirmed_duplicate",
    }:
        alt_tier = "clear"

    profile_score, profile_reasons, profile_flags = _numeric_profile_score(
        username=username,
        account_age_days=account_age_days,
        default_avatar=default_avatar,
    )
    spam_score, spam_reasons, behavior_flags = _spam_behavior_score(behavior)

    listing_source = _is_listing_source(join)
    # Listing traffic is normal acquisition context. It cannot add risk by itself.
    if listing_source and base_score == 0 and profile_score == 0 and spam_score == 0:
        review_verdict = "LOW CONCERN — NORMAL LISTING TRAFFIC"
    elif alt_tier == "confirmed_duplicate":
        review_verdict = "CONFIRMED DUPLICATE IDENTITY"
    elif alt_tier == "strongly_linked":
        review_verdict = "STRONG ALT LINK — STAFF REVIEW"
    elif spam_score >= 70:
        review_verdict = "HIGH-CONFIDENCE SPAM ACCOUNT"
    elif spam_score >= 35:
        review_verdict = "SPAM BEHAVIOR DETECTED"
    elif alt_tier == "suspicious" or profile_score >= 25:
        review_verdict = "REVIEW RECOMMENDED"
    else:
        review_verdict = "LOW CONCERN"

    alt_score = base_score
    overall_score = max(base_score, profile_score, spam_score)
    if spam_score >= 35 and alt_tier in {"suspicious", "strongly_linked"}:
        overall_score = min(100, overall_score + 8)
    if alt_tier == "confirmed_duplicate":
        overall_score = 100
    elif alt_tier == "strongly_linked":
        overall_score = max(65, overall_score)

    reasons = _dedupe(
        list(out.get("reasons") or out.get("risk_reasons") or [])
        + profile_reasons
        + spam_reasons,
        16,
    )
    flags = _dedupe(
        list(out.get("suspicion_flags") or [])
        + profile_flags
        + behavior_flags,
        20,
    )

    overall_level = _risk_level(overall_score)
    spam_level = _risk_level(spam_score)
    profile_level = _risk_level(profile_score)

    out.update(
        {
            "score": overall_score,
            "risk_score": overall_score,
            "level": overall_level,
            "risk_level": overall_level,
            "reasons": reasons,
            "risk_reasons": reasons,
            "suspicion_flags": flags,
            "alt_evidence_tier": alt_tier,
            "alt_risk_score": alt_score,
            "spam_risk_score": spam_score,
            "spam_risk_level": spam_level,
            "profile_risk_score": profile_score,
            "profile_risk_level": profile_level,
            "post_join_behavior_flags": behavior_flags,
            "review_verdict": review_verdict,
            "listing_source": listing_source,
            "risk_dimensions": {
                "alt": {"score": alt_score, "tier": alt_tier},
                "spam": {"score": spam_score, "level": spam_level},
                "profile": {"score": profile_score, "level": profile_level},
            },
            "possible_alt_account": alt_tier
            in {"suspicious", "strongly_linked", "confirmed_duplicate"},
            "possible_spam_account": spam_score >= 35,
        }
    )
    return out


__all__ = ["enrich_member_risk_profile"]
