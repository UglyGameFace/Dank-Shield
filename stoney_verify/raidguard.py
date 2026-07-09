from __future__ import annotations

import re
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import discord

from .globals import *


# ============================================================
# Internal caches
# ============================================================

_RECENT_JOIN_PROFILES: Dict[int, Deque[Dict[str, Any]]] = defaultdict(deque)
_LAST_RAID_ALERT_AT: Dict[int, datetime] = {}
_LAST_CLUSTER_ALERT_AT: Dict[Tuple[int, str], datetime] = {}

_HARD_PROOF_CACHE: Dict[Tuple[int, int], Tuple[datetime, Dict[str, Any]]] = {}
_PROOF_CACHE_TTL_SECONDS = 60

_USERNAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_REPEAT_CHAR_RE = re.compile(r"(.)\1{3,}")
_SUSPICIOUS_NAME_RE = re.compile(
    r"(free[\W_]*nitro|nitro[\W_]*gift|discord[\W_]*gift|steam[\W_]*gift|airdrop)"
    r"|(^|[^a-z0-9])(support|staff|admin|mod|backup|alt|test|temp|burner|real[\W_]*(support|staff|admin|mod|discord))($|[^a-z0-9])",
    re.IGNORECASE,
)


# ============================================================
# Time / config helpers
# ============================================================
def _utcnow() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _cfg_int(name: str, default: int) -> int:
    try:
        value = globals().get(name, default)
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        value = globals().get(name, default)
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _raid_window_seconds() -> int:
    return max(30, _cfg_int("RAID_WINDOW_SECONDS", 180))


def _raid_join_threshold() -> int:
    return max(3, _cfg_int("RAID_JOIN_THRESHOLD", 6))


def _raid_alert_cooldown_seconds() -> int:
    return max(20, _cfg_int("RAID_ALERT_COOLDOWN_SECONDS", 90))


def _cluster_window_minutes() -> int:
    return max(2, _cfg_int("ALT_CLUSTER_WINDOW_MINUTES", 20))


def _cluster_similarity_threshold() -> float:
    return max(0.60, min(0.99, _cfg_float("ALT_SIMILARITY_THRESHOLD", 0.86)))


def _cluster_alert_cooldown_seconds() -> int:
    return max(20, _cfg_int("ALT_CLUSTER_ALERT_COOLDOWN_SECONDS", 300))


def _critical_age_days() -> int:
    return max(0, _cfg_int("CRITICAL_ACCOUNT_AGE_DAYS", 1))


def _very_new_age_days() -> int:
    return max(1, _cfg_int("VERY_NEW_ACCOUNT_AGE_DAYS", 3))


def _suspicious_age_days() -> int:
    return max(_very_new_age_days(), _cfg_int("SUSPICIOUS_ACCOUNT_AGE_DAYS", 7))


# ============================================================
# Generic helpers
# ============================================================
def _normalize_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _normalize_name(value: Any) -> str:
    text = _normalize_text(value).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _default_avatar(member: discord.Member) -> bool:
    try:
        return getattr(member, "avatar", None) is None
    except Exception:
        return False


def _digit_ratio(text: str) -> float:
    if not text:
        return 0.0
    digits = sum(1 for ch in text if ch.isdigit())
    return digits / max(1, len(text))


def _underscore_ratio(text: str) -> float:
    if not text:
        return 0.0
    underscores = sum(1 for ch in text if ch == "_")
    return underscores / max(1, len(text))


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    try:
        return SequenceMatcher(None, a, b).ratio()
    except Exception:
        return 0.0


def _safe_display_name(member: discord.Member) -> str:
    try:
        return str(getattr(member, "display_name", None) or getattr(member, "name", None) or member)
    except Exception:
        return "Unknown"


def _safe_avatar_url(member: discord.Member) -> Optional[str]:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return None


def _humanize_age_days(age_days: int) -> str:
    try:
        days = max(0, int(age_days or 0))
        if days <= 0:
            return "<1 day"
        if days == 1:
            return "1 day"
        if days < 30:
            return f"{days} days"
        if days < 365:
            months = max(1, days // 30)
            return f"{months} month(s)"
        years = max(1, days // 365)
        return f"{years} year(s)"
    except Exception:
        return "unknown"


def _safe_text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    try:
        ch = guild.get_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    return None


def _joined_after_creation_seconds(member: discord.Member) -> Optional[int]:
    try:
        created_at = getattr(member, "created_at", None)
        joined_at = getattr(member, "joined_at", None)
        if not created_at or not joined_at:
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        else:
            joined_at = joined_at.astimezone(timezone.utc)
        return max(0, int((joined_at - created_at).total_seconds()))
    except Exception:
        return None


def _joined_after_creation_human(member: discord.Member) -> str:
    seconds = _joined_after_creation_seconds(member)
    if seconds is None:
        return ""
    if seconds < 60:
        return "<1 minute"
    if seconds < 3600:
        return f"{seconds // 60} minute(s)"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours <= 6 and minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours} hour(s)"
    days = seconds // 86400
    return f"{days} day(s)"


def _tier_to_level(tier: str) -> str:
    normalized = str(tier or "").strip().lower()
    if normalized == "confirmed_duplicate":
        return "critical"
    if normalized == "strongly_linked":
        return "high"
    if normalized == "suspicious":
        return "medium"
    return "low"


def _pretty_signal(code: str) -> str:
    mapping = {
        "bot_account": "Bot account",
        "extremely_new_account": "Extremely new account",
        "very_new_account": "Very new account",
        "fresh_account": "Fresh account",
        "default_avatar": "Default avatar",
        "suspicious_name_pattern": "Suspicious name pattern",
        "repeated_character_pattern": "Repeated character pattern",
        "very_high_digit_ratio": "Very high digit ratio",
        "elevated_digit_ratio": "Elevated digit ratio",
        "high_underscore_ratio": "High underscore ratio",
        "instant_join_after_creation": "Joined immediately after creation",
        "fast_join_after_creation": "Joined soon after creation",
        "same_day_join_after_creation": "Joined same day as creation",
        "join_burst": "Join burst",
        "shared_behavior_fingerprint": "Shared behavioral fingerprint",
        "similar_recent_username": "Similar recent usernames",
        "age_bucket_cluster": "Age bucket cluster",
        "identity_fingerprint_match": "Verified identity fingerprint match",
        "manual_alt_link": "Manual confirmed duplicate link",
        "manual_review_likely_link": "Manual likely-same-person link",
        "cluster_triad": "Multi-signal cluster match",
        "burst_cluster_combo": "Burst + cluster combo",
        "name_cluster_combo": "Name cluster combo",
    }
    return mapping.get(code, code.replace("_", " ").strip().capitalize())


def _dedupe_list(values: List[str], max_items: int = 20) -> List[str]:
    out: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out[:max_items]


# ============================================================
# Core signal helpers
# ============================================================
def _account_age_days(member: discord.Member) -> int:
    try:
        created_at = getattr(member, "created_at", None)
        if not created_at:
            return 999999
        age = _utcnow() - created_at
        return max(0, int(age.total_seconds() // 86400))
    except Exception:
        return 999999


def _age_bucket(age_days: int) -> str:
    if age_days <= 1:
        return "0-1d"
    if age_days <= 3:
        return "2-3d"
    if age_days <= 7:
        return "4-7d"
    if age_days <= 14:
        return "8-14d"
    if age_days <= 30:
        return "15-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def _behavior_fingerprint(member: discord.Member) -> str:
    try:
        username = _normalize_text(getattr(member, "name", "") or "")
        normalized = _normalize_name(username)
        alpha_only = "".join(ch for ch in normalized if ch.isalpha())[:10] or "none"
        digits = sum(1 for ch in username if ch.isdigit())
        underscores = username.count("_")
        default_avatar_flag = "default" if _default_avatar(member) else "custom"
        age_bucket = _age_bucket(_account_age_days(member))
        length_bucket = (
            "short" if len(username) <= 6 else
            "medium" if len(username) <= 12 else
            "long"
        )
        return "|".join(
            [
                age_bucket,
                alpha_only,
                f"d{min(digits, 9)}",
                f"u{min(underscores, 5)}",
                default_avatar_flag,
                length_bucket,
            ]
        )
    except Exception:
        return "unknown"


# ============================================================
# Hard-proof helpers
# ============================================================
def _proof_cache_valid(ts: datetime) -> bool:
    try:
        return (_utcnow() - ts).total_seconds() <= max(5, int(_PROOF_CACHE_TTL_SECONDS))
    except Exception:
        return False


def _query_identity_proof_matches_sync(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if sb is None:
            return []

        res = (
            sb.table("identity_proof_matches")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .limit(25)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return [dict(r) for r in rows if isinstance(r, dict)]
    except Exception:
        return []


def _query_manual_alt_links_sync(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if sb is None:
            return []

        rows: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()

        for field in ("user_a_id", "user_b_id"):
            try:
                res = (
                    sb.table("manual_alt_links")
                    .select("*")
                    .eq("guild_id", str(int(guild_id)))
                    .eq("status", "active")
                    .eq(field, str(int(user_id)))
                    .limit(25)
                    .execute()
                )
                for row in (getattr(res, "data", None) or []):
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get("id") or repr(sorted(row.items())))
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    rows.append(dict(row))
            except Exception:
                continue

        return rows
    except Exception:
        return []


def _load_hard_identity_context(guild_id: int, user_id: int) -> Dict[str, Any]:
    cache_key = (int(guild_id), int(user_id))
    cached = _HARD_PROOF_CACHE.get(cache_key)
    if cached and _proof_cache_valid(cached[0]):
        return dict(cached[1])

    proof_rows = _query_identity_proof_matches_sync(guild_id, user_id)
    manual_rows = _query_manual_alt_links_sync(guild_id, user_id)

    proof_matches: List[Dict[str, Any]] = []
    proof_seen: Set[int] = set()
    matched_fingerprints: List[str] = []

    for row in proof_rows:
        try:
            matched_user_id = int(str(row.get("matched_user_id") or "0") or 0)
            if matched_user_id <= 0 or matched_user_id == int(user_id):
                continue
            if matched_user_id in proof_seen:
                continue

            proof_seen.add(matched_user_id)
            fingerprint = str(row.get("identity_fingerprint") or "").strip()
            if fingerprint and fingerprint not in matched_fingerprints:
                matched_fingerprints.append(fingerprint)

            proof_matches.append(
                {
                    "user_id": matched_user_id,
                    "identity_fingerprint": fingerprint or None,
                    "fingerprint_version": str(row.get("fingerprint_version") or "").strip() or None,
                    "match_confidence": int(row.get("match_confidence") or 100),
                }
            )
        except Exception:
            continue

    manual_confirmed: List[Dict[str, Any]] = []
    manual_likely: List[Dict[str, Any]] = []
    manual_not_linked_ids: Set[int] = set()

    for row in manual_rows:
        try:
            a_id = int(str(row.get("user_a_id") or "0") or 0)
            b_id = int(str(row.get("user_b_id") or "0") or 0)
            if a_id <= 0 or b_id <= 0:
                continue

            other_id = b_id if a_id == int(user_id) else a_id
            if other_id <= 0 or other_id == int(user_id):
                continue

            link_type = str(row.get("link_type") or "").strip().lower()
            record = {
                "user_id": other_id,
                "link_type": link_type,
                "reason": str(row.get("reason") or "").strip() or None,
                "created_by": str(row.get("created_by") or "").strip() or None,
            }

            if link_type == "confirmed_duplicate":
                if all(existing.get("user_id") != other_id for existing in manual_confirmed):
                    manual_confirmed.append(record)
            elif link_type == "same_person_likely":
                if all(existing.get("user_id") != other_id for existing in manual_likely):
                    manual_likely.append(record)
            elif link_type == "not_linked":
                manual_not_linked_ids.add(other_id)
        except Exception:
            continue

    context = {
        "proof_matches": proof_matches[:12],
        "matched_identity_fingerprints": matched_fingerprints[:6],
        "manual_confirmed": manual_confirmed[:12],
        "manual_likely": manual_likely[:12],
        "manual_not_linked_ids": set(manual_not_linked_ids),
    }

    _HARD_PROOF_CACHE[cache_key] = (_utcnow(), dict(context))
    return dict(context)


def _filter_not_linked_matches(
    rows: List[Dict[str, Any]],
    suppressed_user_ids: Set[int],
) -> List[Dict[str, Any]]:
    if not suppressed_user_ids:
        return list(rows)

    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            row_user_id = int(str(row.get("user_id") or "0") or 0)
            if row_user_id in suppressed_user_ids:
                continue
        except Exception:
            pass
        out.append(row)
    return out


def _build_hard_cluster_members(
    proof_matches: List[Dict[str, Any]],
    manual_confirmed: List[Dict[str, Any]],
    manual_likely: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[int] = set()

    for row in proof_matches[:8]:
        try:
            uid = int(row.get("user_id") or 0)
            if uid <= 0 or uid in seen:
                continue
            seen.add(uid)
            out.append(
                {
                    "user_id": uid,
                    "username": None,
                    "display_name": None,
                    "reason": "identity_fingerprint_match",
                }
            )
        except Exception:
            continue

    for row in manual_confirmed[:8]:
        try:
            uid = int(row.get("user_id") or 0)
            if uid <= 0 or uid in seen:
                continue
            seen.add(uid)
            out.append(
                {
                    "user_id": uid,
                    "username": None,
                    "display_name": None,
                    "reason": "manual_alt_link",
                }
            )
        except Exception:
            continue

    for row in manual_likely[:8]:
        try:
            uid = int(row.get("user_id") or 0)
            if uid <= 0 or uid in seen:
                continue
            seen.add(uid)
            out.append(
                {
                    "user_id": uid,
                    "username": None,
                    "display_name": None,
                    "reason": "manual_review_likely_link",
                }
            )
        except Exception:
            continue

    return out[:8]


# ============================================================
# Join profile / cluster engine
# ============================================================
def _prune_recent_join_profiles(guild_id: int) -> None:
    try:
        window = timedelta(minutes=_cluster_window_minutes())
        cutoff = _utcnow() - window
        dq = _RECENT_JOIN_PROFILES[guild_id]

        while dq and dq[0].get("seen_at") and dq[0]["seen_at"] < cutoff:
            dq.popleft()
    except Exception:
        pass


def _recent_profiles(guild_id: int) -> List[Dict[str, Any]]:
    try:
        _prune_recent_join_profiles(guild_id)
        return [
            row for row in list(_RECENT_JOIN_PROFILES[guild_id])
            if not bool(row.get("is_bot_account"))
        ]
    except Exception:
        return []


def _recent_join_burst_count(guild_id: int) -> int:
    try:
        container = globals().get("JOIN_TIMES", {})
        dq = container.get(guild_id) if isinstance(container, dict) else None
        if not dq:
            return 0

        cutoff = _utcnow() - timedelta(seconds=_raid_window_seconds())
        count = 0

        for ts in list(dq):
            try:
                if ts >= cutoff:
                    count += 1
            except Exception:
                continue

        return count
    except Exception:
        return 0


def _build_recent_cluster_matches(
    guild_id: int,
    target_user_id: int,
    username_normalized: str,
    fingerprint: str,
    age_bucket: str,
) -> Dict[str, Any]:
    profiles = _recent_profiles(guild_id)

    similar_name_matches: List[Dict[str, Any]] = []
    same_fp_matches: List[Dict[str, Any]] = []
    same_age_bucket_matches: List[Dict[str, Any]] = []

    threshold = _cluster_similarity_threshold()
    seen_fp_ids: Set[int] = set()
    seen_age_ids: Set[int] = set()
    best_name_match_by_user: Dict[int, Dict[str, Any]] = {}

    for row in profiles:
        try:
            if bool(row.get("is_bot_account")):
                continue

            row_user_id = int(str(row.get("user_id") or "0") or 0)
            if row_user_id <= 0:
                continue

            # Never compare the target account to itself.
            if row_user_id == int(target_user_id):
                continue

            other_name = str(row.get("username_normalized") or "")
            other_fp = str(row.get("fingerprint") or "")
            other_bucket = str(row.get("age_bucket") or "")

            if other_fp and fingerprint and other_fp == fingerprint and row_user_id not in seen_fp_ids:
                seen_fp_ids.add(row_user_id)
                same_fp_matches.append(dict(row))

            if other_bucket and age_bucket and other_bucket == age_bucket and row_user_id not in seen_age_ids:
                seen_age_ids.add(row_user_id)
                same_age_bucket_matches.append(dict(row))

            if username_normalized and other_name:
                sim = _name_similarity(username_normalized, other_name)
                if sim >= threshold:
                    enriched = dict(row)
                    enriched["similarity"] = sim

                    existing = best_name_match_by_user.get(row_user_id)
                    if existing is None or float(existing.get("similarity") or 0.0) < sim:
                        best_name_match_by_user[row_user_id] = enriched
        except Exception:
            continue

    similar_name_matches = list(best_name_match_by_user.values())
    similar_name_matches.sort(
        key=lambda x: float(x.get("similarity") or 0.0),
        reverse=True,
    )

    return {
        "similar_name_matches": similar_name_matches[:8],
        "same_fp_matches": same_fp_matches[:8],
        "same_age_bucket_matches": same_age_bucket_matches[:12],
    }


def _record_join_profile(member: discord.Member, profile: Dict[str, Any]) -> None:
    try:
        if bool(getattr(member, "bot", False)) or bool(profile.get("is_bot_account")):
            return

        gid = int(member.guild.id)
        uid = int(member.id)

        _prune_recent_join_profiles(gid)
        dq = _RECENT_JOIN_PROFILES[gid]

        # Remove any stale copies of the same member first so the cache
        # cannot self-inflate cluster counts later in the join flow.
        kept: Deque[Dict[str, Any]] = deque()
        for row in list(dq):
            try:
                row_uid = int(str(row.get("user_id") or "0") or 0)
                if row_uid == uid:
                    continue
            except Exception:
                pass
            kept.append(row)

        _RECENT_JOIN_PROFILES[gid] = kept
        _RECENT_JOIN_PROFILES[gid].append(dict(profile))
    except Exception:
        pass


# ============================================================
# Evidence-driven scoring
# ============================================================
def build_member_risk_profile(member: discord.Member) -> Dict[str, Any]:
    guild_id = int(member.guild.id)
    user_id = int(member.id)
    username = _normalize_text(getattr(member, "name", "") or "")
    display_name = _normalize_text(getattr(member, "display_name", "") or "")
    username_normalized = _normalize_name(username)
    display_normalized = _normalize_name(display_name)
    age_days = _account_age_days(member)
    age_bucket = _age_bucket(age_days)
    fingerprint = _behavior_fingerprint(member)

    if getattr(member, "bot", False):
        return {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "username_normalized": username_normalized,
            "display_name_normalized": display_normalized,
            "avatar_url": _safe_avatar_url(member),
            "default_avatar": False,
            "account_age_days": age_days,
            "age_bucket": age_bucket,
            "fingerprint": fingerprint,
            "digit_ratio": 0.0,
            "underscore_ratio": 0.0,
            "burst_count": 0,
            "burst_join_count": 0,
            "score": 0,
            "risk_score": 0,
            "level": "low",
            "risk_level": "low",
            "evidence_tier": "clear",
            "evidence_confidence": "excluded",
            "reasons": ["Discord marks this account as a bot; excluded from raid/alt scoring."],
            "risk_reasons": ["Discord marks this account as a bot; excluded from raid/alt scoring."],
            "account_age_human": _humanize_age_days(age_days),
            "same_fingerprint_count": 0,
            "similar_name_count": 0,
            "same_age_bucket_count": 0,
            "identity_proof_match_count": 0,
            "manual_confirmed_match_count": 0,
            "manual_likely_match_count": 0,
            "matched_identity_fingerprint": None,
            "suspicious_name_pattern": False,
            "repeated_char_pattern": False,
            "suspicion_flags": ["bot_account"],
            "weak_signals": [],
            "strong_signals": [],
            "hard_signals": [],
            "alt_cluster_key": None,
            "alt_cluster_size": 0,
            "cluster_members": [],
            "joined_after_creation_seconds": None,
            "joined_after_creation_human": "",
            "seen_at": _utcnow(),
            "is_bot_account": True,
        }

    default_avatar = _default_avatar(member)
    digit_ratio = _digit_ratio(username)
    underscore_ratio = _underscore_ratio(username)
    repeated_char_pattern = bool(_REPEAT_CHAR_RE.search(username))
    suspicious_name_pattern = bool(_SUSPICIOUS_NAME_RE.search(username))
    burst_count = _recent_join_burst_count(guild_id)
    joined_after_creation_seconds = _joined_after_creation_seconds(member)
    joined_after_creation_human = _joined_after_creation_human(member)

    hard_ctx = _load_hard_identity_context(guild_id, user_id)
    proof_matches = list(hard_ctx.get("proof_matches") or [])
    manual_confirmed = list(hard_ctx.get("manual_confirmed") or [])
    manual_likely = list(hard_ctx.get("manual_likely") or [])
    manual_not_linked_ids = set(hard_ctx.get("manual_not_linked_ids") or set())
    matched_identity_fingerprints = list(hard_ctx.get("matched_identity_fingerprints") or [])

    cluster = _build_recent_cluster_matches(
        guild_id,
        user_id,
        username_normalized,
        fingerprint,
        age_bucket,
    )
    similar_name_matches = _filter_not_linked_matches(cluster["similar_name_matches"], manual_not_linked_ids)
    same_fp_matches = _filter_not_linked_matches(cluster["same_fp_matches"], manual_not_linked_ids)
    same_age_bucket_matches = _filter_not_linked_matches(cluster["same_age_bucket_matches"], manual_not_linked_ids)

    weak_signals: List[str] = []
    strong_signals: List[str] = []
    hard_signals: List[str] = []
    reasons: List[str] = []
    weak_points = 0

    # --------------------------------------------------------
    # Hard evidence: only real proof or explicit manual confirmation
    # --------------------------------------------------------
    if proof_matches:
        hard_signals.append("identity_fingerprint_match")
        reasons.append(
            f"Matched an active verified identity fingerprint with {len(proof_matches)} other account(s)."
        )

    if manual_confirmed:
        hard_signals.append("manual_alt_link")
        reasons.append(
            f"Staff manually confirmed duplicate identity linkage with {len(manual_confirmed)} account(s)."
        )

    # --------------------------------------------------------
    # Strong linked evidence: admin-reviewed likely link or multi-signal combos
    # --------------------------------------------------------
    if manual_likely:
        strong_signals.append("manual_review_likely_link")
        reasons.append(
            f"Staff manually marked this account as likely the same person as {len(manual_likely)} other account(s)."
        )

    # --------------------------------------------------------
    # Weak signals: these can raise suspicion, but never prove identity
    # --------------------------------------------------------
    if age_days <= _critical_age_days():
        weak_signals.append("extremely_new_account")
        reasons.append(f"Account is extremely new ({age_days} day(s) old).")
        weak_points += 18
    elif age_days <= _very_new_age_days():
        weak_signals.append("very_new_account")
        reasons.append(f"Account is very new ({age_days} day(s) old).")
        weak_points += 12
    elif age_days <= _suspicious_age_days():
        weak_signals.append("fresh_account")
        reasons.append(f"Account is still fresh ({age_days} day(s) old).")
        weak_points += 6

    if default_avatar:
        weak_signals.append("default_avatar")
        reasons.append("Using Discord default avatar.")
        weak_points += 6

    if suspicious_name_pattern:
        weak_signals.append("suspicious_name_pattern")
        reasons.append("Username contains suspicious burner / impersonation keywords.")
        weak_points += 8

    if repeated_char_pattern:
        weak_signals.append("repeated_character_pattern")
        reasons.append("Username contains heavy repeated-character pattern.")
        weak_points += 5

    if digit_ratio >= 0.45:
        weak_signals.append("very_high_digit_ratio")
        reasons.append("Username has very high digit ratio.")
        weak_points += 7
    elif digit_ratio >= 0.25:
        weak_signals.append("elevated_digit_ratio")
        reasons.append("Username has elevated digit ratio.")
        weak_points += 4

    if underscore_ratio >= 0.18:
        weak_signals.append("high_underscore_ratio")
        reasons.append("Username has unusual underscore density.")
        weak_points += 3

    if joined_after_creation_seconds is not None:
        if joined_after_creation_seconds <= 300:
            weak_signals.append("instant_join_after_creation")
            reasons.append("Joined the server within minutes of account creation.")
            weak_points += 10
        elif joined_after_creation_seconds <= 3600:
            weak_signals.append("fast_join_after_creation")
            reasons.append("Joined the server very soon after account creation.")
            weak_points += 7
        elif joined_after_creation_seconds <= 86400:
            weak_signals.append("same_day_join_after_creation")
            reasons.append("Joined the server the same day the account was created.")
            weak_points += 4

    if burst_count >= _raid_join_threshold():
        weak_signals.append("join_burst")
        reasons.append(
            f"Join happened during burst activity ({burst_count} joins in ~{_raid_window_seconds()}s)."
        )
        weak_points += 8

    if len(same_age_bucket_matches) >= max(3, _raid_join_threshold() // 2):
        weak_signals.append("age_bucket_cluster")
        reasons.append(
            f"Joined inside an age-bucket cluster ({len(same_age_bucket_matches)} recent join(s) in bucket {age_bucket})."
        )
        weak_points += 3

    # --------------------------------------------------------
    # Heuristic strong links: require combinations, not single hints
    # Also suppress pairs staff explicitly marked as not linked.
    # --------------------------------------------------------
    if (
        len(same_fp_matches) >= 1
        and len(similar_name_matches) >= 1
        and joined_after_creation_seconds is not None
        and joined_after_creation_seconds <= 86400
    ):
        strong_signals.append("cluster_triad")
        reasons.append(
            "Matched a recent fingerprint cluster and a similar recent username, and joined soon after account creation."
        )

    if (
        len(same_fp_matches) >= 1
        and burst_count >= _raid_join_threshold()
        and (age_days <= _suspicious_age_days() or default_avatar or suspicious_name_pattern)
    ):
        strong_signals.append("burst_cluster_combo")
        reasons.append(
            "Matched a recent fingerprint cluster during a burst join window while also carrying weak-risk join traits."
        )

    if (
        len(similar_name_matches) >= 2
        and age_days <= _very_new_age_days()
        and burst_count >= _raid_join_threshold()
    ):
        strong_signals.append("name_cluster_combo")
        reasons.append(
            "Very new account closely matches multiple recent usernames during a burst join window."
        )

    if (
        len(same_fp_matches) >= 2
        and (
            age_days <= _suspicious_age_days()
            or (joined_after_creation_seconds is not None and joined_after_creation_seconds <= 86400)
        )
    ):
        strong_signals.append("shared_behavior_fingerprint")
        reasons.append(
            f"Matched the same recent behavioral fingerprint as {len(same_fp_matches)} other recent join(s)."
        )

    weak_points = min(35, weak_points)

    if hard_signals:
        evidence_tier = "confirmed_duplicate"
        score = 100
    elif strong_signals:
        evidence_tier = "strongly_linked"
        score = max(65, min(90, weak_points + 35 + (len(strong_signals) - 1) * 6))
    elif weak_points >= 10 or len(same_fp_matches) > 0 or len(similar_name_matches) > 0:
        evidence_tier = "suspicious"
        score = max(20, min(45, weak_points))
    else:
        evidence_tier = "clear"
        score = min(15, weak_points)

    level = _tier_to_level(evidence_tier)

    cluster_members: List[Dict[str, Any]] = []
    cluster_members.extend(_build_hard_cluster_members(proof_matches, manual_confirmed, manual_likely))

    existing_member_ids = {
        int(row.get("user_id") or 0)
        for row in cluster_members
        if int(row.get("user_id") or 0) > 0 and int(row.get("user_id") or 0) != user_id
    }

    for row in same_fp_matches[:4]:
        try:
            uid = int(row.get("user_id") or 0)
        except Exception:
            uid = 0
        if uid <= 0 or uid == user_id:
            continue
        if uid not in existing_member_ids:
            existing_member_ids.add(uid)
            cluster_members.append(
                {
                    "user_id": row.get("user_id"),
                    "username": row.get("username"),
                    "display_name": row.get("display_name"),
                    "reason": "same_fingerprint",
                }
            )

    for row in similar_name_matches[:4]:
        try:
            uid = int(row.get("user_id") or 0)
        except Exception:
            uid = 0
        if uid <= 0 or uid == user_id:
            continue
        if uid not in existing_member_ids:
            existing_member_ids.add(uid)
            cluster_members.append(
                {
                    "user_id": row.get("user_id"),
                    "username": row.get("username"),
                    "display_name": row.get("display_name"),
                    "reason": f"name_similarity:{float(row.get('similarity') or 0.0):.2f}",
                }
            )

    alt_cluster_key: Optional[str] = None
    alt_cluster_size = 0

    if proof_matches and matched_identity_fingerprints:
        alt_cluster_key = f"idproof:{matched_identity_fingerprints[0]}"
        alt_cluster_size = 1 + len(proof_matches)
    elif manual_confirmed:
        alt_cluster_key = f"manual_confirmed:{guild_id}:{user_id}"
        alt_cluster_size = 1 + len(manual_confirmed)
    elif manual_likely:
        alt_cluster_key = f"manual_likely:{guild_id}:{user_id}"
        alt_cluster_size = 1 + len(manual_likely)
    elif len(same_fp_matches) >= 1 and fingerprint:
        alt_cluster_key = f"fp:{fingerprint}"
        alt_cluster_size = 1 + len(same_fp_matches)
    elif len(similar_name_matches) >= 1 and username_normalized:
        alt_cluster_key = f"name:{username_normalized[:48]}"
        alt_cluster_size = 1 + len(similar_name_matches)
    elif len(same_age_bucket_matches) >= max(3, _raid_join_threshold() // 2):
        alt_cluster_key = f"age:{age_bucket}"
        alt_cluster_size = 1 + len(same_age_bucket_matches)

    flags = _dedupe_list(
        hard_signals + strong_signals + weak_signals,
        max_items=20,
    )

    return {
        "guild_id": guild_id,
        "user_id": user_id,
        "username": username,
        "display_name": display_name,
        "username_normalized": username_normalized,
        "display_name_normalized": display_normalized,
        "avatar_url": _safe_avatar_url(member),
        "default_avatar": default_avatar,
        "account_age_days": age_days,
        "age_bucket": age_bucket,
        "fingerprint": fingerprint,
        "digit_ratio": round(digit_ratio, 3),
        "underscore_ratio": round(underscore_ratio, 3),
        "burst_count": burst_count,
        "burst_join_count": burst_count,
        "score": score,
        "risk_score": score,
        "level": level,
        "risk_level": level,
        "evidence_tier": evidence_tier,
        "evidence_confidence": (
            "hard" if hard_signals else
            "strong" if strong_signals else
            "weak" if evidence_tier == "suspicious" else
            "none"
        ),
        "reasons": reasons[:12],
        "risk_reasons": reasons[:12],
        "account_age_human": _humanize_age_days(age_days),
        "same_fingerprint_count": len(same_fp_matches),
        "similar_name_count": len(similar_name_matches),
        "same_age_bucket_count": len(same_age_bucket_matches),
        "identity_proof_match_count": len(proof_matches),
        "manual_confirmed_match_count": len(manual_confirmed),
        "manual_likely_match_count": len(manual_likely),
        "matched_identity_fingerprint": matched_identity_fingerprints[0] if matched_identity_fingerprints else None,
        "suspicious_name_pattern": suspicious_name_pattern,
        "repeated_char_pattern": repeated_char_pattern,
        "suspicion_flags": flags,
        "weak_signals": weak_signals[:12],
        "strong_signals": strong_signals[:12],
        "hard_signals": hard_signals[:12],
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "cluster_members": cluster_members[:8],
        "joined_after_creation_seconds": joined_after_creation_seconds,
        "joined_after_creation_human": joined_after_creation_human,
        "seen_at": _utcnow(),
        "is_bot_account": False,
    }


def build_alt_detection_summary(member: discord.Member) -> str:
    profile = build_member_risk_profile(member)

    if bool(profile.get("is_bot_account")):
        return "BOT ACCOUNT • excluded from raid/alt scoring"

    score = int(profile.get("score") or 0)
    level = str(profile.get("level") or "low").upper()
    tier = str(profile.get("evidence_tier") or "clear").replace("_", " ").upper()
    # Do not tell staff an account is CLEAR while also showing heuristic flags.
    # Low-confidence flags are not proof, but they are not "clear" either.
    if tier == "CLEAR" and list(profile.get("suspicion_flags") or []):
        tier = "WATCHLIST"
    age_human = _humanize_age_days(int(profile.get("account_age_days") or 0))
    burst = int(profile.get("burst_count") or 0)
    fp_matches = int(profile.get("same_fingerprint_count") or 0)
    name_matches = int(profile.get("similar_name_count") or 0)
    cluster_size = int(profile.get("alt_cluster_size") or 0)
    identity_matches = int(profile.get("identity_proof_match_count") or 0)
    manual_confirmed = int(profile.get("manual_confirmed_match_count") or 0)
    manual_likely = int(profile.get("manual_likely_match_count") or 0)

    parts: List[str] = [
        f"{tier} ({level} / {score}/100)",
        f"Account age: {age_human}",
    ]

    signal_parts: List[str] = []
    if identity_matches > 0:
        signal_parts.append(f"Verified identity matches: {identity_matches}")
    if manual_confirmed > 0:
        signal_parts.append(f"Manual confirmed links: {manual_confirmed}")
    if manual_likely > 0:
        signal_parts.append(f"Manual likely links: {manual_likely}")
    if cluster_size > 1:
        signal_parts.append(f"Linked cluster size: {cluster_size}")
    if fp_matches > 0:
        signal_parts.append(f"Shared fingerprint matches: {fp_matches}")
    if name_matches > 0:
        signal_parts.append(f"Similar usernames: {name_matches}")
    if burst > 0:
        signal_parts.append(f"Join burst: {burst}")
    if profile.get("default_avatar"):
        signal_parts.append("Default avatar")

    if signal_parts:
        parts.append("Signals: " + " • ".join(signal_parts))
    else:
        parts.append("Signals: no strong recent link evidence")

    flags = [_pretty_signal(x) for x in list(profile.get("suspicion_flags") or [])[:5]]
    if flags:
        parts.append("Flags: " + " • ".join(flags))

    return "\n".join(parts)


# ============================================================
# Raid / alert actions
# ============================================================
async def _post_raidlog(guild: discord.Guild, message: str) -> None:
    try:
        target: Optional[discord.TextChannel] = None

        try:
            raid_log_id = int(globals().get("RAID_LOG_CHANNEL_ID") or 0)
            if raid_log_id > 0:
                target = _safe_text_channel(guild, raid_log_id)
        except Exception:
            target = None

        if target is None:
            try:
                modlog_id = int(globals().get("MODLOG_CHANNEL_ID") or 0)
                if modlog_id > 0:
                    target = _safe_text_channel(guild, modlog_id)
            except Exception:
                target = None

        if target is None:
            try:
                from .modlog import _get_modlog_channel
                target = _get_modlog_channel(guild)
            except Exception:
                target = None

        if target is None:
            return

        text = _normalize_text(message)
        if not text:
            return

        if len(text) <= 1900:
            await target.send(text)
            return

        embed = discord.Embed(
            title="🚨 Raid / Alt Detection",
            description=text[:4000],
            color=discord.Color.red(),
            timestamp=_utcnow(),
        )
        await target.send(embed=embed)

    except Exception as e:
        print("⚠️ _post_raidlog error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _maybe_trigger_raid(guild: discord.Guild) -> Tuple[bool, str]:
    try:
        gid = int(guild.id)
        now = _utcnow()

        recent_join_count = _recent_join_burst_count(gid)
        profiles = _recent_profiles(gid)

        recent_high = [p for p in profiles if str(p.get("level")) in {"high", "critical"}]
        recent_critical = [p for p in profiles if str(p.get("level")) == "critical"]

        same_fp_groups: Dict[str, int] = defaultdict(int)
        for row in profiles:
            fp = str(row.get("fingerprint") or "")
            if fp:
                same_fp_groups[fp] += 1

        hottest_fp_size = max(same_fp_groups.values()) if same_fp_groups else 0
        recent_confirmed = [p for p in profiles if str(p.get("evidence_tier")) == "confirmed_duplicate"]

        should_alert = False
        reasons: List[str] = []

        if recent_join_count >= _raid_join_threshold():
            should_alert = True
            reasons.append(f"{recent_join_count} joins in ~{_raid_window_seconds()}s")

        if len(recent_confirmed) >= 1:
            should_alert = True
            reasons.append(f"{len(recent_confirmed)} hard-proof duplicate join(s) detected")

        if len(recent_critical) >= 2:
            should_alert = True
            reasons.append(f"{len(recent_critical)} confirmed / critical recent joins")

        if len(recent_high) >= max(3, _raid_join_threshold() - 1):
            should_alert = True
            reasons.append(f"{len(recent_high)} high/critical recent joins")

        if hottest_fp_size >= max(3, _raid_join_threshold() // 2):
            should_alert = True
            reasons.append(f"behavioral fingerprint cluster size {hottest_fp_size}")

        if not should_alert:
            return False, ""

        last_alert = _LAST_RAID_ALERT_AT.get(gid)
        if last_alert and (now - last_alert).total_seconds() < _raid_alert_cooldown_seconds():
            return False, ""

        _LAST_RAID_ALERT_AT[gid] = now

        sample_lines: List[str] = []
        for row in sorted(profiles, key=lambda x: int(x.get("score") or 0), reverse=True)[:6]:
            tier = str(row.get("evidence_tier") or "clear")
            sample_lines.append(
                f"`{row.get('username') or row.get('display_name') or row.get('user_id')}`"
                f" score={row.get('score')}/100"
                f" tier={tier}"
                f" proof={row.get('identity_proof_match_count', 0)}"
                f" manual={row.get('manual_confirmed_match_count', 0)}"
                f" fp={row.get('same_fingerprint_count')}"
                f" names={row.get('similar_name_count')}"
            )

        message = (
            "🚨 **Raid / Alt Wave Detected**\n"
            f"Guild: `{guild.name}` (`{guild.id}`)\n"
            f"Signals: {' • '.join(reasons)}\n"
        )

        if sample_lines:
            message += "Recent risky joins:\n" + "\n".join(f"- {line}" for line in sample_lines)

        try:
            RUNTIME_STATS["raid_alerts_triggered"] = int(RUNTIME_STATS.get("raid_alerts_triggered", 0)) + 1
        except Exception:
            pass

        return True, message

    except Exception as e:
        print("⚠️ _maybe_trigger_raid error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False, ""


async def _mass_role_strip_if_needed(member: discord.Member) -> Optional[str]:
    try:
        if getattr(member, "bot", False):
            return None

        guild = member.guild
        gid = int(guild.id)
        join_count = _recent_join_burst_count(gid)

        trigger_threshold = max(_raid_join_threshold() + 1, 7)
        if join_count < trigger_threshold:
            return None

        profile = build_member_risk_profile(member)
        _record_join_profile(member, profile)

        if str(profile.get("level")) not in {"high", "critical"}:
            return None

        protected_ids: set[int] = set()
        for raw in (
            globals().get("UNVERIFIED_ROLE_ID"),
            globals().get("VERIFIED_ROLE_ID"),
            globals().get("RESIDENT_ROLE_ID"),
            globals().get("STAFF_ROLE_ID"),
        ):
            try:
                rid = int(raw or 0)
                if rid > 0:
                    protected_ids.add(rid)
            except Exception:
                continue

        try:
            me = guild.me or await guild.fetch_member(bot.user.id)  # type: ignore[arg-type]
        except Exception:
            me = None

        if me is None or not getattr(me.guild_permissions, "manage_roles", False):
            return None

        removable: List[discord.Role] = []

        for role in list(member.roles or []):
            try:
                if role.is_default():
                    continue
                if int(role.id) in protected_ids:
                    continue
                if role >= me.top_role:
                    continue
                removable.append(role)
            except Exception:
                continue

        if not removable:
            return (
                f"🛡️ **High-risk join observed**: `{member}` (`{member.id}`) "
                f"score={profile['score']}/100 tier={profile.get('evidence_tier')} but no removable roles were present."
            )

        try:
            await member.remove_roles(
                *removable,
                reason=(
                    f"Raidguard protective strip: score={profile['score']}/100 "
                    f"tier={profile.get('evidence_tier')} burst={join_count}"
                ),
            )
        except discord.Forbidden:
            return (
                f"⚠️ **Raidguard could not strip roles** from `{member}` (`{member.id}`) "
                f"due to role hierarchy / permissions."
            )
        except Exception as e:
            return (
                f"⚠️ **Raidguard role strip error** for `{member}` (`{member.id}`): `{repr(e)[:400]}`"
            )

        removed_names = ", ".join(f"`{r.name}`" for r in removable[:8])
        return (
            f"🛡️ **Protective role strip applied** to `{member}` (`{member.id}`)\n"
            f"Score: `{profile['score']}/100` ({profile.get('evidence_tier')}) • joins_in_window=`{join_count}`\n"
            f"Removed: {removed_names if removed_names else '`none`'}"
        )

    except Exception as e:
        print("⚠️ _mass_role_strip_if_needed error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None


# ============================================================
# Public helper to be called by join event after logging
# ============================================================
def track_member_join_risk(member: discord.Member) -> Dict[str, Any]:
    profile = build_member_risk_profile(member)

    if getattr(member, "bot", False):
        return profile

    _record_join_profile(member, profile)

    try:
        gid = int(member.guild.id)
        username_key = profile.get("username_normalized") or profile.get("display_name_normalized") or ""
        if username_key and int(profile.get("similar_name_count") or 0) >= 2:
            cache_key = (gid, str(username_key))
            last_alert = _LAST_CLUSTER_ALERT_AT.get(cache_key)
            now = _utcnow()
            if last_alert is None or (now - last_alert).total_seconds() >= _cluster_alert_cooldown_seconds():
                _LAST_CLUSTER_ALERT_AT[cache_key] = now
    except Exception:
        pass

    return profile
