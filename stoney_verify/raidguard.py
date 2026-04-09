from __future__ import annotations

import re
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

from .globals import *


# ============================================================
# ✅ Internal caches
# ============================================================

_RECENT_JOIN_PROFILES: Dict[int, Deque[Dict[str, Any]]] = defaultdict(deque)
_LAST_RAID_ALERT_AT: Dict[int, datetime] = {}
_LAST_CLUSTER_ALERT_AT: Dict[Tuple[int, str], datetime] = {}

_USERNAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_REPEAT_CHAR_RE = re.compile(r"(.)\1{3,}")
_SUSPICIOUS_NAME_RE = re.compile(
    r"(free|nitro|gift|airdrop|support|mod|staff|admin|real|backup|alt|test|temp|burner)",
    re.IGNORECASE,
)


# ============================================================
# ✅ Time / config helpers
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
# ✅ Generic helpers
# ============================================================

def _normalize_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _normalize_name(value: Any) -> str:
    text = _normalize_text(value).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _tokenize_name(value: Any) -> List[str]:
    text = _normalize_text(value).lower()
    return [m.group(0) for m in _USERNAME_TOKEN_RE.finditer(text)]


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


# ============================================================
# ✅ Core signal helpers
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
# ✅ Join profile / cluster engine
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
        return list(_RECENT_JOIN_PROFILES[guild_id])
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
    username_normalized: str,
    fingerprint: str,
    age_bucket: str,
) -> Dict[str, Any]:
    profiles = _recent_profiles(guild_id)

    similar_name_matches: List[Dict[str, Any]] = []
    same_fp_matches: List[Dict[str, Any]] = []
    same_age_bucket_matches: List[Dict[str, Any]] = []

    threshold = _cluster_similarity_threshold()

    for row in profiles:
        try:
            other_name = str(row.get("username_normalized") or "")
            other_fp = str(row.get("fingerprint") or "")
            other_bucket = str(row.get("age_bucket") or "")

            if other_fp and other_fp == fingerprint:
                same_fp_matches.append(row)

            if other_bucket and other_bucket == age_bucket:
                same_age_bucket_matches.append(row)

            if username_normalized and other_name:
                sim = _name_similarity(username_normalized, other_name)
                if sim >= threshold:
                    enriched = dict(row)
                    enriched["similarity"] = sim
                    similar_name_matches.append(enriched)
        except Exception:
            continue

    similar_name_matches.sort(key=lambda x: float(x.get("similarity") or 0.0), reverse=True)

    return {
        "similar_name_matches": similar_name_matches[:8],
        "same_fp_matches": same_fp_matches[:8],
        "same_age_bucket_matches": same_age_bucket_matches[:12],
    }


def _record_join_profile(member: discord.Member, profile: Dict[str, Any]) -> None:
    try:
        gid = int(member.guild.id)
        _prune_recent_join_profiles(gid)
        _RECENT_JOIN_PROFILES[gid].append(profile)
    except Exception:
        pass


# ============================================================
# ✅ Risk scoring
# ============================================================

def build_member_risk_profile(member: discord.Member) -> Dict[str, Any]:
    guild_id = int(member.guild.id)
    username = _normalize_text(getattr(member, "name", "") or "")
    display_name = _normalize_text(getattr(member, "display_name", "") or "")
    username_normalized = _normalize_name(username)
    display_normalized = _normalize_name(display_name)

    age_days = _account_age_days(member)
    age_bucket = _age_bucket(age_days)
    fingerprint = _behavior_fingerprint(member)
    default_avatar = _default_avatar(member)
    digit_ratio = _digit_ratio(username)
    underscore_ratio = _underscore_ratio(username)
    repeated_char_pattern = bool(_REPEAT_CHAR_RE.search(username))
    suspicious_name_pattern = bool(_SUSPICIOUS_NAME_RE.search(username))

    burst_count = _recent_join_burst_count(guild_id)
    cluster = _build_recent_cluster_matches(guild_id, username_normalized, fingerprint, age_bucket)

    similar_name_matches = cluster["similar_name_matches"]
    same_fp_matches = cluster["same_fp_matches"]
    same_age_bucket_matches = cluster["same_age_bucket_matches"]

    score = 0
    reasons: List[str] = []

    if age_days <= _critical_age_days():
        score += 36
        reasons.append(f"Account is extremely new ({age_days} day(s) old).")
    elif age_days <= _very_new_age_days():
        score += 26
        reasons.append(f"Account is very new ({age_days} day(s) old).")
    elif age_days <= _suspicious_age_days():
        score += 14
        reasons.append(f"Account is still fresh ({age_days} day(s) old).")

    if default_avatar:
        score += 8
        reasons.append("Using Discord default avatar.")

    if suspicious_name_pattern:
        score += 12
        reasons.append("Username contains suspicious burner / impersonation keywords.")

    if repeated_char_pattern:
        score += 8
        reasons.append("Username contains heavy repeated-character pattern.")

    if digit_ratio >= 0.45:
        score += 10
        reasons.append("Username has very high digit ratio.")
    elif digit_ratio >= 0.25:
        score += 5
        reasons.append("Username has elevated digit ratio.")

    if underscore_ratio >= 0.18:
        score += 4
        reasons.append("Username has unusual underscore density.")

    if burst_count >= _raid_join_threshold():
        score += min(24, (burst_count - _raid_join_threshold() + 1) * 4)
        reasons.append(
            f"Join happened during burst activity ({burst_count} joins in ~{_raid_window_seconds()}s)."
        )

    if len(same_fp_matches) >= 1:
        score += min(22, len(same_fp_matches) * 8)
        reasons.append(
            f"Matched recent behavioral fingerprint with {len(same_fp_matches)} other recent join(s)."
        )

    if len(similar_name_matches) >= 1:
        best_similarity = float(similar_name_matches[0].get("similarity") or 0.0)
        score += min(18, len(similar_name_matches) * 6)
        reasons.append(
            f"Username closely matches {len(similar_name_matches)} recent join(s) "
            f"(best similarity {best_similarity:.2f})."
        )

    if len(same_age_bucket_matches) >= max(3, _raid_join_threshold() // 2):
        score += 8
        reasons.append(
            f"Joined inside an age-bucket cluster ({len(same_age_bucket_matches)} recent join(s) in bucket {age_bucket})."
        )

    score = max(0, min(100, score))

    if score >= 75:
        level = "critical"
    elif score >= 50:
        level = "high"
    elif score >= 25:
        level = "medium"
    else:
        level = "low"

    suspicion_flags: List[str] = []

    if age_days <= _critical_age_days():
        suspicion_flags.append("extremely_new_account")
    elif age_days <= _very_new_age_days():
        suspicion_flags.append("very_new_account")
    elif age_days <= _suspicious_age_days():
        suspicion_flags.append("fresh_account")

    if default_avatar:
        suspicion_flags.append("default_avatar")
    if suspicious_name_pattern:
        suspicion_flags.append("suspicious_name_pattern")
    if repeated_char_pattern:
        suspicion_flags.append("repeated_character_pattern")
    if digit_ratio >= 0.45:
        suspicion_flags.append("very_high_digit_ratio")
    elif digit_ratio >= 0.25:
        suspicion_flags.append("elevated_digit_ratio")
    if underscore_ratio >= 0.18:
        suspicion_flags.append("high_underscore_ratio")
    if burst_count >= _raid_join_threshold():
        suspicion_flags.append("join_burst")
    if len(same_fp_matches) >= 1:
        suspicion_flags.append("shared_behavior_fingerprint")
    if len(similar_name_matches) >= 1:
        suspicion_flags.append("similar_recent_username")
    if len(same_age_bucket_matches) >= max(3, _raid_join_threshold() // 2):
        suspicion_flags.append("age_bucket_cluster")

    cluster_members: List[Dict[str, Any]] = []

    for row in same_fp_matches[:4]:
        cluster_members.append(
            {
                "user_id": row.get("user_id"),
                "username": row.get("username"),
                "display_name": row.get("display_name"),
                "reason": "same_fingerprint",
            }
        )

    for row in similar_name_matches[:4]:
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

    if len(same_fp_matches) >= 1 and fingerprint:
        alt_cluster_key = f"fp:{fingerprint}"
        alt_cluster_size = 1 + len(same_fp_matches)
    elif len(similar_name_matches) >= 1 and username_normalized:
        alt_cluster_key = f"name:{username_normalized[:48]}"
        alt_cluster_size = 1 + len(similar_name_matches)
    elif len(same_age_bucket_matches) >= max(3, _raid_join_threshold() // 2):
        alt_cluster_key = f"age:{age_bucket}"
        alt_cluster_size = 1 + len(same_age_bucket_matches)

    profile = {
        "guild_id": guild_id,
        "user_id": int(member.id),
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
        "reasons": reasons[:12],
        "risk_reasons": reasons[:12],
        "account_age_human": _humanize_age_days(age_days),
        "same_fingerprint_count": len(same_fp_matches),
        "similar_name_count": len(similar_name_matches),
        "same_age_bucket_count": len(same_age_bucket_matches),
        "suspicious_name_pattern": suspicious_name_pattern,
        "repeated_char_pattern": repeated_char_pattern,
        "suspicion_flags": suspicion_flags[:20],
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "cluster_members": cluster_members[:8],
        "seen_at": _utcnow(),
    }

    return profile


def build_alt_detection_summary(member: discord.Member) -> str:
    profile = build_member_risk_profile(member)

    score = int(profile.get("score") or 0)
    level = str(profile.get("level") or "low").upper()
    age_human = _humanize_age_days(int(profile.get("account_age_days") or 0))
    burst = int(profile.get("burst_count") or 0)
    fp_matches = int(profile.get("same_fingerprint_count") or 0)
    name_matches = int(profile.get("similar_name_count") or 0)
    cluster_size = int(profile.get("alt_cluster_size") or 0)

    parts: List[str] = [f"{level} risk ({score}/100)", f"Account age: {age_human}"]

    signal_parts: List[str] = []
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
        parts.append("Signals: no strong recent alt-cluster links detected")

    return "\n".join(parts)


# ============================================================
# ✅ Raid / alert actions
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

        should_alert = False
        reasons: List[str] = []

        if recent_join_count >= _raid_join_threshold():
            should_alert = True
            reasons.append(
                f"{recent_join_count} joins in ~{_raid_window_seconds()}s"
            )

        if len(recent_critical) >= 2:
            should_alert = True
            reasons.append(
                f"{len(recent_critical)} critical-risk recent joins"
            )

        if len(recent_high) >= max(3, _raid_join_threshold() - 1):
            should_alert = True
            reasons.append(
                f"{len(recent_high)} high/critical-risk recent joins"
            )

        if hottest_fp_size >= max(3, _raid_join_threshold() // 2):
            should_alert = True
            reasons.append(
                f"behavioral fingerprint cluster size {hottest_fp_size}"
            )

        if not should_alert:
            return False, ""

        last_alert = _LAST_RAID_ALERT_AT.get(gid)
        if last_alert and (now - last_alert).total_seconds() < _raid_alert_cooldown_seconds():
            return False, ""

        _LAST_RAID_ALERT_AT[gid] = now

        sample_lines: List[str] = []
        for row in sorted(profiles, key=lambda x: int(x.get("score") or 0), reverse=True)[:6]:
            sample_lines.append(
                f"`{row.get('username') or row.get('display_name') or row.get('user_id')}`"
                f" risk={row.get('score')}/100"
                f" age={row.get('account_age_days')}d"
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
                f"risk={profile['score']}/100 but no removable roles were present."
            )

        try:
            await member.remove_roles(
                *removable,
                reason=(
                    f"Raidguard protective strip: risk={profile['score']}/100 "
                    f"level={profile['level']} burst={join_count}"
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
            f"Risk: `{profile['score']}/100` ({profile['level']}) • joins_in_window=`{join_count}`\n"
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
# ✅ Public helper to be called by join event after logging
# ============================================================

def track_member_join_risk(member: discord.Member) -> Dict[str, Any]:
    profile = build_member_risk_profile(member)
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