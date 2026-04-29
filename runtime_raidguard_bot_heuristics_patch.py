from __future__ import annotations

"""
Runtime raidguard bot-pattern heuristic hardening.

This patch is intentionally conservative:
- It improves review signals for disposable/bot-farm-looking accounts.
- It avoids calling ordinary first-name + birth-year usernames suspicious.
- It separates heuristic suspicion from hard proof.
- It adjusts scores with calibrated floors instead of blindly inflating numbers.

This does NOT auto-ban by itself. It makes staff-facing logs more honest.
"""

import builtins
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

# alpha prefix + numeric suffix, for examples like suzette01723 / maria48291.
_NUMERIC_SUFFIX_RE = re.compile(r"^([a-z][a-z]{2,24})(\d{3,10})$", re.IGNORECASE)
_LOW_ENTROPY_NAME_RE = re.compile(r"^[a-z]{4,14}$", re.IGNORECASE)
_REPEATING_DIGITS_RE = re.compile(r"^(\d)\1{2,}$")
_SEQUENTIAL_DIGITS = {
    "0123", "1234", "2345", "3456", "4567", "5678", "6789", "7890",
    "9876", "8765", "7654", "6543", "5432", "4321", "3210",
}

_PRETTY_SIGNAL_LABELS = {
    "numeric_suffix_username": "Human-name + numeric suffix username",
    "long_numeric_suffix_username": "Long numeric suffix username",
    "birth_year_like_suffix": "Birth-year-like numeric suffix",
    "young_numeric_suffix_account": "Young account with numeric suffix",
    "fresh_numeric_suffix_account": "Fresh account with numeric suffix",
    "display_username_suffix_match": "Display name matches username prefix",
    "default_avatar_numeric_suffix": "Default avatar + numeric suffix",
    "high_digit_density_numeric_suffix": "High digit density numeric suffix",
    "randomish_numeric_suffix": "Random-looking numeric suffix",
    "disposable_bot_name_pattern": "Disposable/bot-farm name pattern",
    "bot_pattern_review_needed": "Bot-pattern review recommended",
}


def _log(message: str) -> None:
    try:
        print(f"🧪 runtime_raidguard_bot_heuristics {message}")
    except Exception:
        pass


def _normalize_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _normalize_name(value: Any) -> str:
    text = _normalize_text(value).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _alpha_only(value: Any) -> str:
    text = _normalize_text(value).lower()
    return "".join(ch for ch in text if ch.isalpha())


def _append_unique(target: Any, values: Iterable[str]) -> List[str]:
    if isinstance(target, list):
        out = [str(item) for item in target if str(item or "").strip()]
    else:
        out = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _ranked_level(current: str, desired: str) -> str:
    ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    current_norm = str(current or "low").strip().lower()
    desired_norm = str(desired or "low").strip().lower()
    return desired_norm if ranks.get(desired_norm, 0) > ranks.get(current_norm, 0) else current_norm


def _ranked_tier(current: str, desired: str) -> str:
    ranks = {"clear": 0, "suspicious": 1, "strongly_linked": 2, "confirmed_duplicate": 3}
    current_norm = str(current or "clear").strip().lower()
    desired_norm = str(desired or "clear").strip().lower()
    return desired_norm if ranks.get(desired_norm, 0) > ranks.get(current_norm, 0) else current_norm


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _default_avatar(member: Any) -> bool:
    try:
        return getattr(member, "avatar", None) is None
    except Exception:
        return False


def _current_year() -> int:
    try:
        return datetime.now(timezone.utc).year
    except Exception:
        return 2026


def _birth_year_like(suffix: str) -> bool:
    try:
        if len(suffix) != 4 or not suffix.isdigit():
            return False
        year = int(suffix)
        return 1900 <= year <= _current_year()
    except Exception:
        return False


def _randomish_suffix(suffix: str) -> bool:
    try:
        if not suffix or not suffix.isdigit():
            return False
        if _birth_year_like(suffix):
            return False
        if len(suffix) >= 5:
            return True
        if _REPEATING_DIGITS_RE.match(suffix):
            return True
        return suffix in _SEQUENTIAL_DIGITS
    except Exception:
        return False


def _set_score_floor(profile: Dict[str, Any], floor: int) -> None:
    current = max(_safe_int(profile.get("risk_score"), 0), _safe_int(profile.get("score"), 0))
    score = max(current, int(floor))
    profile["risk_score"] = min(100, score)
    profile["score"] = min(100, score)


def _add_score_points(profile: Dict[str, Any], points: int, *, cap: int) -> None:
    current = max(_safe_int(profile.get("risk_score"), 0), _safe_int(profile.get("score"), 0))
    score = min(int(cap), max(current, current + max(0, int(points))))
    profile["risk_score"] = max(0, min(100, score))
    profile["score"] = max(0, min(100, score))


def _confidence_from_score(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "medium"
    if score >= 30:
        return "low"
    return "trace"


def _bot_pattern_analysis(
    *,
    alpha_prefix: str,
    digit_suffix: str,
    display_alpha: str,
    age_days: int,
    digit_ratio: float,
    default_avatar: bool,
) -> tuple[int, list[str], list[str], dict[str, Any]]:
    suffix_len = len(digit_suffix)
    birth_year = _birth_year_like(digit_suffix)
    randomish = _randomish_suffix(digit_suffix)
    display_matches_prefix = bool(display_alpha and display_alpha == alpha_prefix)
    low_entropy_prefix = bool(_LOW_ENTROPY_NAME_RE.match(alpha_prefix or ""))

    signals: list[str] = []
    reasons: list[str] = []
    score = 0

    # Base pattern: common enough that it should not be scary by itself.
    if low_entropy_prefix and suffix_len >= 3:
        signals.append("numeric_suffix_username")
        if birth_year:
            signals.append("birth_year_like_suffix")
            reasons.append("Username has a human-name prefix plus a birth-year-like suffix; this is common, so it is weighted lightly.")
            score += 4
        else:
            reasons.append(f"Username has a human-name prefix plus a numeric suffix (`{alpha_prefix}` + {suffix_len} digits).")
            score += 10

    # Longer non-birth-year suffixes are more bot-farm/disposable shaped.
    if suffix_len >= 5 and not birth_year:
        signals.append("long_numeric_suffix_username")
        reasons.append("Numeric suffix is long enough to look generated rather than a normal short tag or birth year.")
        score += 14

    if randomish:
        signals.append("randomish_numeric_suffix")
        reasons.append("Numeric suffix looks random/generated rather than a normal birth year.")
        score += 10

    # Age matters, but do not punish older normal accounts hard.
    if age_days <= 14:
        signals.append("fresh_numeric_suffix_account")
        reasons.append("Account is fresh and uses a numeric-suffix username.")
        score += 18
    elif age_days <= 90:
        signals.append("young_numeric_suffix_account")
        reasons.append("Account is under 90 days old and uses a numeric-suffix username.")
        score += 12
    elif age_days <= 180 and randomish and suffix_len >= 5:
        signals.append("young_numeric_suffix_account")
        reasons.append("Account is under 180 days old and uses a long random-looking numeric suffix.")
        score += 6

    # This is a useful corroborating hint, but also common for real people.
    if display_matches_prefix and suffix_len >= 4:
        signals.append("display_username_suffix_match")
        reasons.append("Display name matches the username prefix while the username adds numeric digits.")
        score += 6 if not birth_year else 2

    if default_avatar and suffix_len >= 4:
        signals.append("default_avatar_numeric_suffix")
        reasons.append("Default avatar plus numeric-suffix username can indicate a disposable/bot-farm account.")
        score += 12

    if digit_ratio >= 0.35 and age_days <= 180 and not birth_year:
        signals.append("high_digit_density_numeric_suffix")
        reasons.append("Username has high digit density for a young numeric-suffix account.")
        score += 8
    elif digit_ratio >= 0.25 and age_days <= 90 and not birth_year:
        signals.append("high_digit_density_numeric_suffix")
        reasons.append("Username has elevated digit density for a young numeric-suffix account.")
        score += 4

    # Prevent common real-user shapes from being overstated.
    suppress_medium = False
    if birth_year and not default_avatar and age_days > 30:
        suppress_medium = True
        score = min(score, 24)
    elif birth_year and not default_avatar:
        score = min(score, 34)
    elif suffix_len == 4 and not default_avatar and age_days > 90:
        suppress_medium = True
        score = min(score, 28)

    # Require multiple corroborating conditions for a real review-level flag.
    corroborators = 0
    if suffix_len >= 5 and not birth_year:
        corroborators += 1
    if randomish:
        corroborators += 1
    if age_days <= 90:
        corroborators += 1
    if display_matches_prefix:
        corroborators += 1
    if default_avatar:
        corroborators += 1
    if digit_ratio >= 0.35 and not birth_year:
        corroborators += 1

    if score >= 55 and corroborators >= 3 and not suppress_medium:
        signals.append("disposable_bot_name_pattern")
        signals.append("bot_pattern_review_needed")
        reasons.append("Combined username shape, account age, and profile signals are consistent with disposable/bot-farm accounts.")
    else:
        # Keep the evidence visible, but label it as weak/low-confidence.
        score = min(score, 44)

    details = {
        "alpha_prefix": alpha_prefix,
        "digit_suffix_length": suffix_len,
        "birth_year_like_suffix": birth_year,
        "randomish_suffix": randomish,
        "display_matches_prefix": display_matches_prefix,
        "default_avatar": default_avatar,
        "age_days": age_days,
        "corroborators": corroborators,
        "suppressed_medium": suppress_medium,
    }
    return max(0, min(100, int(score))), signals, reasons, details


def _enhance_profile(member: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        return profile

    try:
        if bool(getattr(member, "bot", False)) or bool(profile.get("is_bot_account")):
            return profile

        username = _normalize_text(getattr(member, "name", "") or profile.get("username") or "")
        display_name = _normalize_text(getattr(member, "display_name", "") or profile.get("display_name") or "")
        username_norm = _normalize_name(username)
        display_alpha = _alpha_only(display_name)
        age_days = _safe_int(profile.get("account_age_days"), 999999)
        digit_ratio = _safe_float(profile.get("digit_ratio"), 0.0)
        default_avatar = bool(profile.get("default_avatar")) or _default_avatar(member)

        suffix_match = _NUMERIC_SUFFIX_RE.match(username_norm)
        if not suffix_match:
            return profile

        alpha_prefix = suffix_match.group(1).lower()
        digit_suffix = suffix_match.group(2)

        bot_score, added_signals, added_reasons, details = _bot_pattern_analysis(
            alpha_prefix=alpha_prefix,
            digit_suffix=digit_suffix,
            display_alpha=display_alpha,
            age_days=age_days,
            digit_ratio=digit_ratio,
            default_avatar=default_avatar,
        )

        if bot_score <= 0 or not added_signals:
            return profile

        profile["weak_signals"] = _append_unique(profile.get("weak_signals"), added_signals)
        profile["suspicion_flags"] = _append_unique(profile.get("suspicion_flags"), added_signals)
        profile["reasons"] = _append_unique(profile.get("reasons"), added_reasons)
        profile["risk_reasons"] = _append_unique(profile.get("risk_reasons"), added_reasons)
        profile["bot_pattern_score"] = int(bot_score)
        profile["bot_pattern_confidence"] = _confidence_from_score(bot_score)
        profile["bot_pattern_details"] = details
        profile["possible_bot_pattern"] = bool(bot_score >= 30)
        profile["bot_pattern_reason"] = "numeric_suffix_disposable_name_shape"

        # Calibrated scoring:
        # - low-confidence patterns stay LOW but no longer show as almost zero.
        # - medium/high-confidence patterns become SUSPICIOUS/MEDIUM for review.
        # - never override hard/manual higher evidence.
        if bot_score >= 65:
            _set_score_floor(profile, 34)
            _add_score_points(profile, min(18, bot_score // 4), cap=54)
            profile["level"] = _ranked_level(str(profile.get("level") or profile.get("risk_level") or "low"), "medium")
            profile["risk_level"] = _ranked_level(str(profile.get("risk_level") or profile.get("level") or "low"), "medium")
            profile["evidence_tier"] = _ranked_tier(str(profile.get("evidence_tier") or "clear"), "suspicious")
            profile["evidence_confidence"] = "heuristic_medium_confidence_bot_pattern"
        elif bot_score >= 45:
            _set_score_floor(profile, 24)
            _add_score_points(profile, min(10, bot_score // 6), cap=39)
            # Keep as LOW unless the base scorer already had medium/high evidence.
            profile["evidence_confidence"] = str(profile.get("evidence_confidence") or "heuristic_low_confidence_bot_pattern")
        else:
            _set_score_floor(profile, 12)
            _add_score_points(profile, min(6, bot_score // 8), cap=29)

        return profile
    except Exception:
        return profile


def _patch_pretty_signal(module: Any) -> None:
    original = getattr(module, "_pretty_signal", None)
    if not callable(original) or getattr(original, "_bot_heuristics_wrapped", False):
        return

    def _pretty_signal_patched(code: str) -> str:
        key = str(code or "").strip()
        if key in _PRETTY_SIGNAL_LABELS:
            return _PRETTY_SIGNAL_LABELS[key]
        return original(code)

    try:
        setattr(_pretty_signal_patched, "_bot_heuristics_wrapped", True)
    except Exception:
        pass
    setattr(module, "_pretty_signal", _pretty_signal_patched)


def _patch_raidguard(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return
    patch_key = f"{module_name}:bot_heuristics_v2"
    if patch_key in _PATCHED_MODULES:
        return

    original = getattr(module, "build_member_risk_profile", None)
    if not callable(original) or getattr(original, "_bot_heuristics_v2_wrapped", False):
        return

    def _build_member_risk_profile_patched(member: Any) -> Dict[str, Any]:
        profile = original(member)
        return _enhance_profile(member, profile)

    try:
        setattr(_build_member_risk_profile_patched, "_bot_heuristics_v2_wrapped", True)
    except Exception:
        pass

    setattr(module, "build_member_risk_profile", _build_member_risk_profile_patched)
    _patch_pretty_signal(module)
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}.build_member_risk_profile with calibrated numeric-suffix bot-pattern scoring v2")


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
_log("loaded; calibrated disposable/bot-pattern scorer active")
