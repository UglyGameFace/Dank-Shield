from __future__ import annotations

"""
Runtime raidguard bot-pattern heuristic hardening.

Why this exists:
The base raidguard scorer is intentionally conservative because weak signals
should not prove an alt or malicious user. That is correct for identity claims,
but it under-flags obvious disposable/bot-farm account patterns such as:

- display name is a normal first name
- username is the same first name plus a long numeric suffix
- account is young-ish but not brand new
- no identity proof exists yet

This patch does NOT auto-ban by itself. It raises those accounts into a clearer
medium/suspicious review tier so staff logs and moderation buttons make sense.
"""

import builtins
import re
import sys
from typing import Any, Dict, Iterable, List

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

_NUMERIC_SUFFIX_RE = re.compile(r"^([a-z][a-z]{2,24})(\d{4,10})$", re.IGNORECASE)
_LOW_ENTROPY_NAME_RE = re.compile(r"^[a-z]{4,14}$", re.IGNORECASE)

_PRETTY_SIGNAL_LABELS = {
    "numeric_suffix_username": "Human-name + numeric suffix username",
    "young_numeric_suffix_account": "Young account with numeric suffix",
    "display_username_suffix_match": "Display name matches username prefix",
    "bot_farm_name_shape": "Bot-farm style name shape",
    "possible_disposable_bot_pattern": "Possible disposable/bot pattern",
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


def _risk_floor(profile: Dict[str, Any], floor: int) -> None:
    current = max(_safe_int(profile.get("risk_score"), 0), _safe_int(profile.get("score"), 0))
    score = max(current, int(floor))
    profile["risk_score"] = score
    profile["score"] = score


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
        suffix_len = len(digit_suffix)

        added_signals: list[str] = []
        added_reasons: list[str] = []
        extra_points = 0

        if suffix_len >= 4 and _LOW_ENTROPY_NAME_RE.match(alpha_prefix):
            added_signals.append("numeric_suffix_username")
            added_reasons.append(
                f"Username looks like a human-name prefix plus a long numeric suffix (`{alpha_prefix}` + {suffix_len} digits)."
            )
            extra_points += 12

        if suffix_len >= 4 and age_days <= 90:
            added_signals.append("young_numeric_suffix_account")
            added_reasons.append("Account is under 90 days old and uses a long numeric username suffix.")
            extra_points += 10

        if display_alpha and display_alpha == alpha_prefix and suffix_len >= 4:
            added_signals.append("display_username_suffix_match")
            added_reasons.append("Display name matches the username prefix while the username adds a long digit suffix.")
            extra_points += 6

        if digit_ratio >= 0.25 and age_days <= 90:
            added_signals.append("bot_farm_name_shape")
            added_reasons.append("Young account has a bot-farm style name shape with elevated digit density.")
            extra_points += 8

        if default_avatar and suffix_len >= 4:
            added_signals.append("possible_disposable_bot_pattern")
            added_reasons.append("Default avatar plus numeric-suffix username looks like a disposable/bot-farm account pattern.")
            extra_points += 8

        if extra_points <= 0:
            return profile

        # Keep this as review-oriented suspicion, not hard proof. Do not downgrade
        # hard/manual evidence if another layer already marked the user higher.
        profile["weak_signals"] = _append_unique(profile.get("weak_signals"), added_signals)
        profile["suspicion_flags"] = _append_unique(profile.get("suspicion_flags"), added_signals)
        profile["reasons"] = _append_unique(profile.get("reasons"), added_reasons)
        profile["risk_reasons"] = _append_unique(profile.get("risk_reasons"), added_reasons)
        profile["bot_pattern_score"] = int(extra_points)
        profile["possible_bot_pattern"] = True
        profile["bot_pattern_reason"] = "numeric_suffix_disposable_name_shape"

        if extra_points >= 18:
            _risk_floor(profile, 28)
            profile["level"] = _ranked_level(str(profile.get("level") or profile.get("risk_level") or "low"), "medium")
            profile["risk_level"] = _ranked_level(str(profile.get("risk_level") or profile.get("level") or "low"), "medium")
            profile["evidence_tier"] = _ranked_tier(str(profile.get("evidence_tier") or "clear"), "suspicious")
            profile["evidence_confidence"] = str(profile.get("evidence_confidence") or "heuristic_bot_pattern")
        else:
            _risk_floor(profile, 16)

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
    patch_key = f"{module_name}:bot_heuristics"
    if patch_key in _PATCHED_MODULES:
        return

    original = getattr(module, "build_member_risk_profile", None)
    if not callable(original) or getattr(original, "_bot_heuristics_wrapped", False):
        return

    def _build_member_risk_profile_patched(member: Any) -> Dict[str, Any]:
        profile = original(member)
        return _enhance_profile(member, profile)

    try:
        setattr(_build_member_risk_profile_patched, "_bot_heuristics_wrapped", True)
    except Exception:
        pass

    setattr(module, "build_member_risk_profile", _build_member_risk_profile_patched)
    _patch_pretty_signal(module)
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}.build_member_risk_profile with numeric-suffix bot-pattern scoring")


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
_log("loaded; disposable/bot-pattern scorer active")
