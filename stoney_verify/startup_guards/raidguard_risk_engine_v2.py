from __future__ import annotations

"""
RaidGuard Risk Engine v2.

This replaces the old root-level runtime_raidguard_risk_engine_v2_patch.py.

This layer keeps the bot realistic for servers that intentionally get public
traffic from listing sites such as Disboard, Discodus, Discordfy, and Discadia.

Important rule:
A public/listing source is NOT suspicious by itself. Public listing traffic is
expected for growth servers. The engine only escalates when independent signals
agree: young account + generated-looking name + burst/cluster + spam/behavior or
hard identity evidence.

The output adds category buckets to the existing risk profile without removing
legacy fields that older modlog/dashboard code already uses.
"""

import builtins
import os
import sys
from typing import Any, Dict, Iterable, List, Mapping, Tuple

# Chain whatever import hook is already active. Do not bypass earlier startup guards.
_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

LISTING_SOURCE_KEYWORDS = {
    "disboard",
    "discodus",
    "discordfy",
    "discadia",
}

_RISK_LEVELS = ("low", "medium", "high", "critical")
_EVIDENCE_TIERS = ("clear", "suspicious", "strongly_linked", "confirmed_duplicate")


def _log(message: str) -> None:
    try:
        print(f"🧠 raidguard_risk_engine_v2 {message}")
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except Exception:
        try:
            return float(str(value).strip())
        except Exception:
            return float(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
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


def _cap(score: Any) -> int:
    return max(0, min(100, _safe_int(score, 0)))


def _level_from_score(score: int) -> str:
    s = _cap(score)
    if s >= 85:
        return "critical"
    if s >= 65:
        return "high"
    if s >= 38:
        return "medium"
    return "low"


def _ranked(current: str, desired: str, order: Tuple[str, ...]) -> str:
    c = _safe_str(current, order[0]).lower()
    d = _safe_str(desired, order[0]).lower()
    try:
        return d if order.index(d) > order.index(c) else c
    except Exception:
        return d if d in order else c if c in order else order[0]


def _env_keywords() -> set[str]:
    raw = _safe_str(os.getenv("DANK_LISTING_SOURCE_KEYWORDS"))
    if not raw:
        return set(LISTING_SOURCE_KEYWORDS)
    values = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return values or set(LISTING_SOURCE_KEYWORDS)


def _source_texts(profile: Mapping[str, Any]) -> List[str]:
    keys = (
        "traffic_source",
        "entry_source",
        "verification_source",
        "invite_source",
        "invite_label",
        "invite_name",
        "invite_code",
        "entry_method",
        "join_note",
        "source",
        "referrer",
        "referral_source",
    )
    texts: List[str] = []
    for key in keys:
        value = profile.get(key)
        if isinstance(value, Mapping):
            for sub_key, sub_value in value.items():
                texts.append(f"{sub_key}:{sub_value}")
        elif isinstance(value, (list, tuple)):
            for item in value:
                texts.append(_safe_str(item))
        else:
            texts.append(_safe_str(value))
    return [t for t in texts if t]


def _detect_listing_source(profile: Mapping[str, Any]) -> Tuple[bool, str]:
    keywords = _env_keywords()
    blob = " ".join(_source_texts(profile)).lower()
    for keyword in sorted(keywords):
        if keyword and keyword in blob:
            return True, keyword
    return False, ""


def _bucket(score: int, label: str, signals: Iterable[str], notes: Iterable[str]) -> Dict[str, Any]:
    s = _cap(score)
    return {
        "score": s,
        "level": _level_from_score(s),
        "label": label,
        "signals": _append_unique([], signals, max_items=20),
        "notes": _append_unique([], notes, max_items=12),
    }


def _build_buckets(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    flags = _append_unique(
        profile.get("suspicion_flags"),
        _string_list(profile.get("weak_signals")) + _string_list(profile.get("strong_signals")) + _string_list(profile.get("hard_signals")),
        max_items=50,
    )
    hard = set(_string_list(profile.get("hard_signals"), 25))
    strong = set(_string_list(profile.get("strong_signals"), 25))

    account_age_days = _safe_int(profile.get("account_age_days"), 999999)
    burst_count = max(_safe_int(profile.get("burst_count"), 0), _safe_int(profile.get("burst_join_count"), 0))
    same_fp = _safe_int(profile.get("same_fingerprint_count"), 0)
    similar_name = _safe_int(profile.get("similar_name_count"), 0)
    same_age_bucket = _safe_int(profile.get("same_age_bucket_count"), 0)
    bot_pattern_score = _safe_int(profile.get("bot_pattern_score"), 0)
    digit_ratio = _safe_float(profile.get("digit_ratio"), 0.0)
    default_avatar = _safe_bool(profile.get("default_avatar"), False)
    joined_after_creation = _safe_int(profile.get("joined_after_creation_seconds"), -1)
    listing_source, listing_label = _detect_listing_source(profile)

    identity_score = 0
    identity_signals: List[str] = []
    identity_notes: List[str] = []
    if profile.get("identity_proof_match_count") or "identity_fingerprint_match" in hard:
        identity_score += 95
        identity_signals.append("identity_fingerprint_match")
        identity_notes.append("Verified identity fingerprint matched another known account.")
    if profile.get("manual_confirmed_match_count") or "manual_alt_link" in hard:
        identity_score += 95
        identity_signals.append("manual_alt_link")
        identity_notes.append("Staff manually confirmed this account link.")
    if profile.get("manual_likely_match_count") or "manual_review_likely_link" in strong:
        identity_score += 58
        identity_signals.append("manual_review_likely_link")
        identity_notes.append("Staff previously marked this account as likely linked.")

    bot_pattern_signals = [f for f in flags if any(part in f for part in ("numeric_suffix", "bot_pattern", "disposable", "digit", "default_avatar"))]
    bot_pattern_score_final = min(100, max(bot_pattern_score, 0))
    if bot_pattern_score_final <= 0:
        if digit_ratio >= 0.45:
            bot_pattern_score_final += 22
        elif digit_ratio >= 0.25:
            bot_pattern_score_final += 10
        if default_avatar:
            bot_pattern_score_final += 12
    if account_age_days > 365 and not default_avatar:
        bot_pattern_score_final = min(bot_pattern_score_final, 24)

    age_score = 0
    age_signals: List[str] = []
    age_notes: List[str] = []
    if account_age_days <= 1:
        age_score = 75
        age_signals.append("extremely_new_account")
        age_notes.append("Account is 0–1 days old.")
    elif account_age_days <= 3:
        age_score = 58
        age_signals.append("very_new_account")
        age_notes.append("Account is under 3 days old.")
    elif account_age_days <= 7:
        age_score = 38
        age_signals.append("fresh_account")
        age_notes.append("Account is under 7 days old.")
    elif account_age_days <= 30:
        age_score = 18
        age_signals.append("young_account")
        age_notes.append("Account is under 30 days old.")

    join_timing_score = 0
    join_timing_signals: List[str] = []
    join_timing_notes: List[str] = []
    if joined_after_creation >= 0:
        if joined_after_creation <= 300:
            join_timing_score = 70
            join_timing_signals.append("instant_join_after_creation")
            join_timing_notes.append("Joined within minutes of account creation.")
        elif joined_after_creation <= 3600:
            join_timing_score = 50
            join_timing_signals.append("fast_join_after_creation")
            join_timing_notes.append("Joined within an hour of account creation.")
        elif joined_after_creation <= 86400:
            join_timing_score = 28
            join_timing_signals.append("same_day_join_after_creation")
            join_timing_notes.append("Joined the same day the account was created.")

    raid_score = 0
    raid_signals: List[str] = []
    raid_notes: List[str] = []
    if burst_count >= 10:
        raid_score += 75
    elif burst_count >= 6:
        raid_score += 55
    elif burst_count >= 3:
        raid_score += 25
    if burst_count > 0:
        raid_signals.append("join_burst")
        raid_notes.append(f"Join happened during a burst of about {burst_count} recent join(s).")
    if same_age_bucket >= 4:
        raid_score += 14
        raid_signals.append("age_bucket_cluster")
        raid_notes.append("Recent joins share a similar account-age bucket.")

    cluster_score = 0
    cluster_signals: List[str] = []
    cluster_notes: List[str] = []
    if same_fp > 0:
        cluster_score += min(50, 30 + same_fp * 8)
        cluster_signals.append("shared_behavior_fingerprint")
        cluster_notes.append("Recent account has a similar behavioral fingerprint to another join.")
    if similar_name > 0:
        cluster_score += min(35, 18 + similar_name * 5)
        cluster_signals.append("similar_recent_username")
        cluster_notes.append("Recent join had a very similar username.")
    if "cluster_triad" in strong:
        cluster_score += 55
        cluster_signals.append("cluster_triad")
        cluster_notes.append("Multiple cluster signals agreed at once.")

    invite_score = 0
    invite_signals: List[str] = []
    invite_notes: List[str] = []
    if listing_source:
        invite_signals.append(f"listing_source:{listing_label}")
        invite_notes.append(f"Traffic source appears to be `{listing_label}`. Public listing traffic is expected and is not penalized by itself.")
    elif _source_texts(profile):
        invite_signals.append("known_invite_context")
        invite_notes.append("Invite/source context exists, but no penalty is applied without behavior or cluster evidence.")
    else:
        invite_signals.append("invite_source_unknown")
        invite_notes.append("Invite/listing source was not available to the risk engine; no source penalty applied.")

    verification_score = 0
    verification_signals: List[str] = []
    verification_notes: List[str] = []
    role_state = _safe_str(profile.get("role_state") or profile.get("verification_state") or profile.get("entry_method")).lower()
    if "failed" in role_state:
        verification_score = 55
        verification_signals.append("verification_failed")
        verification_notes.append("Verification state indicates a failed check.")
    elif "pending" in role_state or "unverified" in role_state:
        verification_score = 12
        verification_signals.append("verification_pending")
        verification_notes.append("Member is still pending verification.")

    behavior_score = 0
    behavior_signals: List[str] = []
    behavior_notes: List[str] = []
    for key, score, note in (
        ("posted_link_immediately", 45, "Posted a link immediately after joining."),
        ("mention_burst", 60, "Mentioned multiple users/roles shortly after joining."),
        ("spam_message_burst", 70, "Sent a burst of repeated/rapid messages."),
        ("opened_ticket_immediately", 20, "Opened a ticket immediately after joining."),
        ("verification_timeout", 25, "Did not complete verification in the expected window."),
    ):
        if _safe_bool(profile.get(key), False):
            behavior_score += score
            behavior_signals.append(key)
            behavior_notes.append(note)

    return {
        "identity": _bucket(identity_score, "Identity / alt proof", identity_signals, identity_notes),
        "bot_pattern": _bucket(bot_pattern_score_final, "Bot-pattern heuristics", bot_pattern_signals, ["Username/avatar/account-shape heuristics only; not hard proof."] if bot_pattern_signals else []),
        "account_age": _bucket(age_score, "Account age", age_signals, age_notes),
        "join_timing": _bucket(join_timing_score, "Created-to-joined timing", join_timing_signals, join_timing_notes),
        "raid_burst": _bucket(raid_score, "Join burst / raid context", raid_signals, raid_notes),
        "cluster": _bucket(cluster_score, "Similar-account cluster", cluster_signals, cluster_notes),
        "invite_source": _bucket(invite_score, "Invite/listing source", invite_signals, invite_notes),
        "verification": _bucket(verification_score, "Verification state", verification_signals, verification_notes),
        "post_join_behavior": _bucket(behavior_score, "Post-join behavior", behavior_signals, behavior_notes),
    }


def _combine_scores(profile: Dict[str, Any], buckets: Mapping[str, Mapping[str, Any]]) -> Tuple[int, str, str, List[str]]:
    legacy_score = _cap(max(_safe_int(profile.get("risk_score"), 0), _safe_int(profile.get("score"), 0)))
    identity = _cap((buckets.get("identity") or {}).get("score", 0))
    behavior = _cap((buckets.get("post_join_behavior") or {}).get("score", 0))
    raid = _cap((buckets.get("raid_burst") or {}).get("score", 0))
    cluster = _cap((buckets.get("cluster") or {}).get("score", 0))
    bot_pattern = _cap((buckets.get("bot_pattern") or {}).get("score", 0))
    age = _cap((buckets.get("account_age") or {}).get("score", 0))
    timing = _cap((buckets.get("join_timing") or {}).get("score", 0))
    verification = _cap((buckets.get("verification") or {}).get("score", 0))

    if identity >= 90:
        score = max(legacy_score, 92)
        tier = "confirmed_duplicate"
        level = "critical"
        action = "hard_evidence_review_or_enforce"
    elif identity >= 55:
        score = max(legacy_score, 72)
        tier = "strongly_linked"
        level = "high"
        action = "staff_review_strong_identity_signal"
    else:
        combined = legacy_score
        combined = max(combined, min(84, behavior))
        combined = max(combined, min(78, raid + min(18, cluster // 3)))
        combined = max(combined, min(68, cluster + min(12, raid // 4)))

        corroborated_shape = bot_pattern >= 45 and (
            age >= 18 or timing >= 28 or raid >= 25 or cluster >= 25 or verification >= 25 or behavior >= 20
        )
        strong_shape = bot_pattern >= 65 and (
            (age >= 38 and cluster >= 25) or (age >= 38 and raid >= 25) or behavior >= 35 or timing >= 50
        )

        if strong_shape:
            combined = max(combined, min(64, 38 + bot_pattern // 5 + max(age, timing, raid, cluster) // 6))
        elif corroborated_shape:
            combined = max(combined, min(45, 24 + bot_pattern // 7 + max(age, timing, raid, cluster, verification) // 10))
        else:
            combined = max(combined, min(29, bot_pattern // 3 + age // 8 + timing // 10))

        score = _cap(combined)
        level = _level_from_score(score)
        tier = "suspicious" if level in {"medium", "high", "critical"} else "clear"
        if level == "low":
            action = "log_only"
        elif level == "medium":
            action = "verify_or_staff_review"
        elif level == "high":
            action = "restrict_and_review"
        else:
            action = "urgent_review"

    reasons: List[str] = []
    sorted_buckets = sorted(buckets.items(), key=lambda kv: _cap(kv[1].get("score", 0)), reverse=True)
    for key, bucket in sorted_buckets:
        bscore = _cap(bucket.get("score", 0))
        if bscore <= 0:
            continue
        label = _safe_str(bucket.get("label"), key)
        reasons.append(f"{label}: {bscore}/100")
        if len(reasons) >= 5:
            break

    return score, level, tier, [action] + reasons


def _summarize_buckets(buckets: Mapping[str, Mapping[str, Any]]) -> str:
    parts: List[str] = []
    for key, bucket in sorted(buckets.items(), key=lambda kv: _cap(kv[1].get("score", 0)), reverse=True):
        score = _cap(bucket.get("score", 0))
        if score <= 0 and key != "invite_source":
            continue
        label = _safe_str(bucket.get("label"), key)
        parts.append(f"{label}: {score}/100")
        if len(parts) >= 6:
            break
    return " • ".join(parts)


def _apply_engine_v2(profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, dict) or _safe_bool(profile.get("is_bot_account"), False):
        return profile

    buckets = _build_buckets(profile)
    score, level, tier, action_and_reasons = _combine_scores(profile, buckets)
    action = action_and_reasons[0] if action_and_reasons else "log_only"
    category_reasons = action_and_reasons[1:]

    current_score = _cap(max(profile.get("risk_score", 0), profile.get("score", 0)))
    final_score = max(current_score, score)

    profile["risk_engine_version"] = "v2_listing_source_aware"
    profile["risk_buckets"] = dict(buckets)
    profile["risk_bucket_summary"] = _summarize_buckets(buckets)
    profile["review_recommendation"] = action
    profile["recommended_action"] = action
    profile["risk_score"] = final_score
    profile["score"] = final_score
    profile["level"] = _ranked(_safe_str(profile.get("level") or profile.get("risk_level"), "low"), level, _RISK_LEVELS)
    profile["risk_level"] = _ranked(_safe_str(profile.get("risk_level") or profile.get("level"), "low"), level, _RISK_LEVELS)
    profile["evidence_tier"] = _ranked(_safe_str(profile.get("evidence_tier"), "clear"), tier, _EVIDENCE_TIERS)

    listing_detected, listing_label = _detect_listing_source(profile)
    profile["listing_source_detected"] = bool(listing_detected)
    profile["listing_source_label"] = listing_label or None
    if listing_detected:
        profile["traffic_source_policy"] = "listing_source_not_penalized_without_behavior_or_cluster"

    added_reasons = [f"Risk categories: {item}" for item in category_reasons]
    if listing_detected:
        added_reasons.append(f"Traffic source `{listing_label}` is treated as expected public listing traffic, not suspicious by itself.")
    else:
        added_reasons.append("Invite/listing source is not penalized without independent behavior, burst, cluster, or identity signals.")

    profile["reasons"] = _append_unique(profile.get("reasons"), added_reasons, max_items=20)
    profile["risk_reasons"] = _append_unique(profile.get("risk_reasons"), added_reasons, max_items=20)

    return profile


def _patch_raidguard(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return
    patch_key = f"{module_name}:risk_engine_v2"
    if patch_key in _PATCHED_MODULES:
        return

    original = getattr(module, "build_member_risk_profile", None)
    if not callable(original) or getattr(original, "_risk_engine_v2_wrapped", False):
        return

    def _build_member_risk_profile_v2(member: Any) -> Dict[str, Any]:
        profile = original(member)
        return _apply_engine_v2(profile)

    try:
        setattr(_build_member_risk_profile_v2, "_risk_engine_v2_wrapped", True)
    except Exception:
        pass

    setattr(module, "build_member_risk_profile", _build_member_risk_profile_v2)
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}.build_member_risk_profile with listing-source-aware Risk Engine v2")


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
_log("loaded; listing-source-aware Risk Engine v2 active")


__all__ = []
