from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any, List

import discord

from .globals import *

try:
    from .raidguard import build_member_risk_profile, build_alt_detection_summary
except Exception:
    def build_member_risk_profile(member: discord.Member) -> Dict[str, Any]:
        return {}

    def build_alt_detection_summary(member: discord.Member) -> str:
        return ""

try:
    from .identity_proof_service import get_identity_truth_context
except Exception:
    def get_identity_truth_context(*, guild_id: Any, user_id: Any) -> Dict[str, Any]:
        return {}


# ==========================================================
# Small local helpers
# ==========================================================

def _now_utc() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(str(value).strip())
    except Exception:
        return default


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
        return default
    except Exception:
        return default


def _safe_list(value: Any) -> List[Any]:
    try:
        return list(value) if isinstance(value, list) else []
    except Exception:
        return []


def _safe_string_list(value: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
        elif value is not None:
            text = str(value).strip()
            if text:
                out.append(text)
    except Exception:
        pass
    return out[:max_items]


def _dedupe_list(values: List[str], max_items: int = 20) -> List[str]:
    out: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out[:max_items]


def _truncate(text: Any, max_len: int = 1024) -> str:
    try:
        s = str(text or "")
        if len(s) <= max_len:
            return s
        return s[: max(0, max_len - 1)] + "…"
    except Exception:
        return ""


def _join_nonempty(parts: List[str], sep: str = " • ") -> str:
    clean = [str(p).strip() for p in parts if str(p or "").strip()]
    return sep.join(clean)


def _chunk_lines(lines: List[str], max_len: int = 1000) -> str:
    text = "\n".join([line for line in lines if str(line or "").strip()])
    return _truncate(text, max_len)


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "httpcore",
        "httpx",
        "connection terminated",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 2.5)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_write_with_retry(op_name: str, executor, max_attempts: int = 5) -> bool:
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            executor()
            return True
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue

            print(f"⚠️ {op_name} failed:", repr(e))
            return False

    if last_error is not None:
        print(f"⚠️ {op_name} failed after retries:", repr(last_error))
    return False


def _safe_dt_utc(value: Optional[datetime]) -> Optional[datetime]:
    try:
        if not value:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _json_safe(value: Any):
    try:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            dt = _safe_dt_utc(value)
            return dt.isoformat() if dt else None
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _discord_ts(dt: Optional[datetime]) -> str:
    try:
        dtu = _safe_dt_utc(dt)
        if not dtu:
            return "unknown"
        return f"<t:{int(dtu.timestamp())}:F>"
    except Exception:
        return "unknown"


def _member_display(member: Optional[discord.abc.User]) -> str:
    try:
        if not member:
            return "Unknown"
        display_name = (
            getattr(member, "display_name", None)
            or getattr(member, "global_name", None)
            or getattr(member, "name", None)
            or str(member)
        )
        username = getattr(member, "name", None)
        mid = getattr(member, "id", None)

        if username and display_name and username != display_name:
            base = f"{display_name} / {username}"
        else:
            base = str(display_name or username or "Unknown")

        if mid:
            return f"{base} ({mid})"
        return base
    except Exception:
        return "Unknown"


def _duration_label_from_minutes(minutes: int) -> str:
    m = max(1, int(minutes))
    if m % 1440 == 0:
        days = m // 1440
        return f"{days} day(s)"
    if m % 60 == 0:
        hours = m // 60
        return f"{hours} hour(s)"
    return f"{m} minute(s)"


def _has_default_avatar(user: Optional[discord.abc.User]) -> bool:
    try:
        if user is None:
            return False
        return getattr(user, "avatar", None) is None
    except Exception:
        return False


def _username_for_checks(user: Optional[discord.abc.User]) -> str:
    try:
        if user is None:
            return ""
        return str(
            getattr(user, "name", None)
            or getattr(user, "display_name", None)
            or getattr(user, "global_name", None)
            or ""
        ).strip()
    except Exception:
        return ""


def _digit_ratio(text: str) -> float:
    try:
        raw = str(text or "")
        if not raw:
            return 0.0
        count = sum(1 for ch in raw if ch.isdigit())
        return float(count) / float(max(1, len(raw)))
    except Exception:
        return 0.0


def _max_repeat_run(text: str) -> int:
    try:
        raw = str(text or "")
        if not raw:
            return 0
        best = 1
        cur = 1
        prev = raw[0]
        for ch in raw[1:]:
            if ch.lower() == prev.lower():
                cur += 1
                if cur > best:
                    best = cur
            else:
                cur = 1
                prev = ch
        return best
    except Exception:
        return 0


def _max_digit_run(text: str) -> int:
    try:
        raw = str(text or "")
        best = 0
        cur = 0
        for ch in raw:
            if ch.isdigit():
                cur += 1
                if cur > best:
                    best = cur
            else:
                cur = 0
        return best
    except Exception:
        return 0


def _score_to_level(score: int) -> str:
    s = max(0, int(score or 0))
    if s >= 70:
        return "high"
    if s >= 40:
        return "medium"
    return "low"


def _level_rank(level: str) -> int:
    normalized = str(level or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0


def _humanize_seconds(total_seconds: float) -> str:
    try:
        seconds = max(0, int(total_seconds or 0))

        if seconds < 60:
            return "<1 minute"

        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minute(s)"

        hours = minutes // 60
        rem_minutes = minutes % 60
        if hours < 24:
            if rem_minutes > 0 and hours < 6:
                return f"{hours}h {rem_minutes}m"
            return f"{hours} hour(s)"

        days = hours // 24
        rem_hours = hours % 24
        if days < 30:
            if rem_hours > 0 and days < 7:
                return f"{days}d {rem_hours}h"
            return f"{days} day(s)"

        months = days // 30
        if months < 12:
            return f"{months} month(s)"

        years = days // 365
        return f"{years} year(s)"
    except Exception:
        return "unknown"


def _account_age_human(member_or_user: Optional[discord.abc.User]) -> str:
    try:
        created_at = _safe_dt_utc(getattr(member_or_user, "created_at", None))
        if not created_at:
            return "unknown"
        return _humanize_seconds((_now_utc() - created_at).total_seconds())
    except Exception:
        return "unknown"


def _join_after_creation_delta(member: Optional[discord.abc.User]) -> Tuple[Optional[int], str]:
    try:
        if not isinstance(member, discord.Member):
            return (None, "")

        created_at = _safe_dt_utc(getattr(member, "created_at", None))
        joined_at = _safe_dt_utc(getattr(member, "joined_at", None))
        if not created_at or not joined_at:
            return (None, "")

        delta_seconds = max(0, int((joined_at - created_at).total_seconds()))
        return (delta_seconds, _humanize_seconds(delta_seconds))
    except Exception:
        return (None, "")


def _bot_member_for_guild(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass

    try:
        if getattr(bot, "user", None):
            return guild.get_member(int(bot.user.id))
    except Exception:
        pass

    return None


def _can_act_on_member(actor: discord.Member, target: discord.Member) -> Tuple[bool, str]:
    try:
        if actor.id == target.id:
            return (False, "You cannot moderate yourself.")

        if target.id == actor.guild.owner_id:
            return (False, "You cannot moderate the server owner.")

        if actor.guild.owner_id == actor.id:
            return (True, "")

        if target.top_role >= actor.top_role:
            return (False, "Your role hierarchy is too low for that action.")

        return (True, "")
    except Exception:
        return (False, "Failed to verify permission hierarchy.")


def _bot_can_act_on_member(guild: discord.Guild, target: discord.Member) -> Tuple[bool, str]:
    try:
        me = _bot_member_for_guild(guild)
        if not me:
            return (False, "Bot member could not be resolved.")
        if guild.owner_id == me.id:
            return (True, "")
        if target.id == guild.owner_id:
            return (False, "Bot cannot moderate the server owner.")
        if target.top_role >= me.top_role:
            return (False, "Bot role hierarchy is too low for that action.")
        return (True, "")
    except Exception:
        return (False, "Failed to verify bot hierarchy.")


def _moderator_has_permission(member: discord.Member, perm_name: str) -> bool:
    try:
        return bool(getattr(member.guild_permissions, perm_name, False))
    except Exception:
        return False


def _parse_timeout_minutes(extra: str) -> int:
    try:
        m = re.search(r"(?:^|:)m=(\d+)", str(extra or ""))
        if not m:
            return int(globals().get("MOD_TIMEOUT_MINUTES", 10) or 10)
        minutes = int(m.group(1))
        return max(1, min(minutes, 28 * 24 * 60))
    except Exception:
        return int(globals().get("MOD_TIMEOUT_MINUTES", 10) or 10)


def _quick_mod_default_reason(action: str, moderator: Optional[discord.Member]) -> str:
    actor = _member_display(moderator)
    a = str(action or "").strip().lower()
    if a == "ban":
        return f"Quick mod ban — by {actor}"
    if a == "kick":
        return f"Quick mod kick — by {actor}"
    if a == "timeout":
        return f"Quick mod timeout — by {actor}"
    return f"Quick moderation action — by {actor}"


def _interaction_has_manage_messages(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        return _moderator_has_permission(user, "manage_messages")
    except Exception:
        return False


# ==========================================================
# Compatibility helpers expected elsewhere
# ==========================================================

def _account_age_days(member_or_user: Optional[discord.abc.User]) -> int:
    try:
        created_at = _safe_dt_utc(getattr(member_or_user, "created_at", None))
        if not created_at:
            return 0
        delta = _now_utc() - created_at
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return 0


def _age_bucket(value: Any) -> str:
    try:
        days = value if isinstance(value, int) else _account_age_days(value)
        days = max(0, int(days or 0))
        if days < 1:
            return "<1d"
        if days < 3:
            return "1-2d"
        if days < 7:
            return "3-6d"
        if days < 30:
            return "7-29d"
        if days < 90:
            return "30-89d"
        if days < 180:
            return "90-179d"
        if days < 365:
            return "180-364d"
        return "365d+"
    except Exception:
        return "unknown"


def _behavior_fingerprint(member: Optional[discord.abc.User]) -> str:
    try:
        if not member:
            return ""
        display = (
            getattr(member, "global_name", None)
            or getattr(member, "display_name", None)
            or getattr(member, "name", None)
            or "unknown"
        )
        created_at = _safe_dt_utc(getattr(member, "created_at", None))
        created_day = int(created_at.timestamp() // 86400) if created_at else 0
        avatar_flag = "1" if _has_default_avatar(member) else "0"
        base = f"{str(display).strip().lower()}|{created_day}|{avatar_flag}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:12]
    except Exception:
        return ""


async def _maybe_trigger_raid(*args, **kwargs) -> bool:
    return False


async def _mass_role_strip_if_needed(*args, **kwargs) -> bool:
    return False


async def _post_raidlog(guild: discord.Guild, embed: discord.Embed, view: Optional[discord.ui.View] = None):
    await _post_modlog(guild, embed, view=view)


# ==========================================================
# Audit actor helpers
# ==========================================================

def _format_actor_from_audit(entry: Optional[discord.AuditLogEntry]) -> Tuple[str, str]:
    try:
        if not entry:
            return ("Unknown", "")
        user = getattr(entry, "user", None)
        reason = getattr(entry, "reason", None) or ""
        if user:
            name = (
                getattr(user, "global_name", None)
                or getattr(user, "name", None)
                or str(user)
            )
            actor = f"{name} ({getattr(user, 'id', 'unknown')})"
            return (actor, reason)
        return ("Unknown", reason)
    except Exception:
        return ("Unknown", "")


def _actor_id_from_audit(entry: Optional[discord.AuditLogEntry]) -> Optional[int]:
    try:
        if not entry:
            return None
        user = getattr(entry, "user", None)
        uid = int(getattr(user, "id", 0) or 0)
        return uid or None
    except Exception:
        return None


# ==========================================================
# Context / member intelligence helpers
# ==========================================================

async def _run_blocking_db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _sb_select_guild_member_sync(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if not sb:
            return None

        res = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception as e:
        print("⚠️ _sb_select_guild_member_sync failed:", repr(e))
    return None


def _sb_select_latest_join_sync(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if not sb:
            return None

        res = (
            sb.table("member_joins")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .order("joined_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception as e:
        print("⚠️ _sb_select_latest_join_sync failed:", repr(e))
    return None


def _sb_select_warn_count_sync(guild_id: int, user_id: int) -> int:
    try:
        sb = get_supabase()
        if not sb:
            return 0

        res = (
            sb.table("warns")
            .select("id", count="exact")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .execute()
        )
        return int(getattr(res, "count", 0) or 0)
    except Exception:
        return 0


def _extract_flags_from_profile_like(data: Dict[str, Any]) -> List[str]:
    flags: List[str] = []

    if _safe_bool(data.get("default_avatar")):
        flags.append("default_avatar")
    if _safe_bool(data.get("suspicious_name_pattern")):
        flags.append("suspicious_name")
    if _safe_bool(data.get("repeated_char_pattern")):
        flags.append("repeated_chars")

    for item in _safe_string_list(data.get("suspicion_flags"), 20):
        if item not in flags:
            flags.append(item)

    return flags[:12]


def _pretty_flag_label(flag: Any) -> str:
    raw = _safe_str(flag)
    if not raw:
        return ""

    mapping = {
        "default_avatar": "Default avatar",
        "suspicious_name": "Suspicious name",
        "suspicious_name_pattern": "Suspicious name pattern",
        "repeated_chars": "Repeated characters",
        "repeated_character_pattern": "Repeated characters",
        "very_high_digit_ratio": "Very high digit ratio",
        "elevated_digit_ratio": "Elevated digit ratio",
        "high_digit_ratio": "High digit ratio",
        "long_digit_run": "Long digit run",
        "high_underscore_ratio": "High underscore ratio",
        "synthetic_style_name": "Synthetic-looking name",
        "staff_style_name": "Staff-style name",
        "join_burst": "Join burst",
        "shared_behavior_fingerprint": "Shared fingerprint",
        "similar_recent_username": "Similar recent usernames",
        "age_bucket_cluster": "Age-bucket cluster",
        "extremely_new_account": "Extremely new account",
        "very_new_account": "Very new account",
        "fresh_account": "Fresh account",
        "instant_join_after_creation": "Joined immediately after creation",
        "fast_join_after_creation": "Joined soon after creation",
        "same_day_join_after_creation": "Joined same day as creation",
        "bot_account": "Bot account",
        "cluster_triad": "Multi-signal cluster match",
        "burst_cluster_combo": "Burst + cluster combo",
        "name_cluster_combo": "Name cluster combo",
    }
    if raw in mapping:
        return mapping[raw]
    return raw.replace("_", " ").strip().capitalize()


def _pretty_cluster_reason(reason: Any) -> str:
    raw = _safe_str(reason)
    if not raw:
        return "Linked in recent cluster"
    if raw == "same_fingerprint":
        return "Shared behavioral fingerprint"
    if raw == "same_age_bucket":
        return "Same account-age cluster"
    if raw.startswith("name_similarity:"):
        try:
            pct = float(raw.split(":", 1)[1]) * 100.0
            return f"Very similar username ({pct:.0f}% match)"
        except Exception:
            return "Very similar username"
    return raw.replace("_", " ").strip().capitalize()


def _pretty_truth_link_type(link_type: Any) -> str:
    raw = _safe_str(link_type).lower()
    if raw == "confirmed_duplicate":
        return "Confirmed duplicate"
    if raw == "same_person_likely":
        return "Likely same person"
    if raw == "not_linked":
        return "Not linked"
    return raw.replace("_", " ").strip().capitalize() if raw else "Unknown"


def _truth_context_other_id_label(guild: Optional[discord.Guild], row: Dict[str, Any]) -> str:
    uid = _safe_int(row.get("other_user_id") or row.get("matched_user_id") or row.get("user_id"), 0)
    if uid <= 0:
        return "`unknown`"
    try:
        if guild is not None:
            member = guild.get_member(uid)
            if member is not None:
                return f"{member.mention} (`{uid}`)"
    except Exception:
        pass
    return f"`{uid}`"


def _sb_get_identity_truth_context_sync(guild_id: int, user_id: int) -> Dict[str, Any]:
    try:
        row = get_identity_truth_context(guild_id=str(int(guild_id)), user_id=str(int(user_id)))
        return dict(row) if isinstance(row, dict) else {}
    except Exception:
        return {}


def _context_truth_value(
    guild: Optional[discord.Guild],
    truth_context: Dict[str, Any],
    merged_risk: Optional[Dict[str, Any]] = None,
) -> str:
    truth = dict(truth_context or {})
    merged = dict(merged_risk or {})

    proof_matches = list(truth.get("proof_matches") or [])
    manual_confirmed = list(truth.get("manual_confirmed") or [])
    manual_likely = list(truth.get("manual_likely") or [])
    manual_not_linked = list(truth.get("manual_not_linked") or [])

    proof_count = max(len(proof_matches), _safe_int(merged.get("identity_proof_match_count"), 0))
    confirmed_count = max(len(manual_confirmed), _safe_int(merged.get("manual_confirmed_match_count"), 0))
    likely_count = max(len(manual_likely), _safe_int(merged.get("manual_likely_match_count"), 0))
    not_linked_count = max(len(manual_not_linked), _safe_int(merged.get("manual_not_linked_count"), 0))

    if proof_count <= 0 and confirmed_count <= 0 and likely_count <= 0 and not_linked_count <= 0:
        return ""

    lines: List[str] = []
    header_parts: List[str] = []
    if proof_count > 0:
        header_parts.append(f"proof_matches={proof_count}")
    if confirmed_count > 0:
        header_parts.append(f"manual_confirmed={confirmed_count}")
    if likely_count > 0:
        header_parts.append(f"manual_likely={likely_count}")
    if not_linked_count > 0:
        header_parts.append(f"not_linked={not_linked_count}")
    if header_parts:
        lines.append(" • ".join(header_parts))

    for row in proof_matches[:3]:
        lines.append(
            f"• {_truth_context_other_id_label(guild, row)} — verified identity fingerprint match"
        )

    for row in manual_confirmed[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    for row in manual_likely[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    for row in manual_not_linked[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    return _chunk_lines(lines, 1000)


def _risk_summary_header(source: Dict[str, Any], warn_count: int = 0) -> str:
    if _safe_bool(source.get("is_bot_account"), False):
        return "BOT ACCOUNT • Excluded from alt-risk scoring"

    tier = _safe_str(source.get("evidence_tier"), "clear").replace("_", " ").upper()
    score = _safe_int(source.get("risk_score"), _safe_int(source.get("score"), 0))
    level = _safe_str(source.get("risk_level") or source.get("level"), "low").upper()
    age_human = _safe_str(source.get("account_age_human"))
    age_days = _safe_int(source.get("account_age_days"), 0)
    if not age_human:
        age_human = f"{age_days} day(s)"

    parts = [f"{tier}", f"{level} / {score}/100", f"Account age: {age_human}"]
    if warn_count > 0:
        parts.append(f"Warns: {warn_count}")
    return " • ".join(parts)


def _context_role_state_value(guild_member: Dict[str, Any]) -> str:
    if not guild_member:
        return ""

    parts = [
        _safe_str(guild_member.get("role_state")),
        _safe_str(guild_member.get("role_state_reason")),
    ]
    text = _join_nonempty(parts, sep=" — ")
    return _truncate(text, 400)


def _context_entry_value(guild_member: Dict[str, Any], latest_join: Dict[str, Any]) -> str:
    row = latest_join or guild_member or {}
    if not row:
        return ""

    lines: List[str] = []

    entry_method = _safe_str(row.get("entry_method"))
    verification_source = _safe_str(row.get("verification_source"))
    invite_code = _safe_str(row.get("invite_code"))
    invited_by_name = _safe_str(row.get("invited_by_name"))
    vouched_by_name = _safe_str(row.get("vouched_by_name"))
    approved_by_name = _safe_str(row.get("approved_by_name"))
    join_note = _safe_str(row.get("join_note"))
    entry_reason = _safe_str(guild_member.get("entry_reason") or row.get("entry_reason"))
    approval_reason = _safe_str(guild_member.get("approval_reason") or row.get("approval_reason"))

    header = _join_nonempty(
        [
            f"method={entry_method}" if entry_method else "",
            f"source={verification_source}" if verification_source else "",
            f"invite={invite_code}" if invite_code else "",
        ]
    )
    if header:
        lines.append(header)
    if invited_by_name:
        lines.append(f"invited_by={invited_by_name}")
    if vouched_by_name:
        lines.append(f"vouched_by={vouched_by_name}")
    if approved_by_name:
        lines.append(f"approved_by={approved_by_name}")
    if join_note:
        lines.append(f"note={join_note}")
    if entry_reason:
        lines.append(f"entry_reason={entry_reason}")
    if approval_reason:
        lines.append(f"approval_reason={approval_reason}")

    return _chunk_lines(lines, 1000)


def _value_is_meaningful(value: Any, *, allow_zero: bool = False) -> bool:
    try:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return allow_zero or float(value) != 0.0
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return False
            if allow_zero:
                return True
            return raw not in {"0", "0.0", "unknown", "none", "null", "[]", "{}"}
        if isinstance(value, (list, dict, tuple, set)):
            return len(value) > 0
        return True
    except Exception:
        return False


def _pick_first_meaningful(*values: Any, default: Any = None, allow_zero: bool = False) -> Any:
    for value in values:
        if _value_is_meaningful(value, allow_zero=allow_zero):
            return value
    return default


def _pick_max_int(*values: Any, default: int = 0) -> int:
    found: List[int] = []
    for value in values:
        try:
            iv = int(str(value).strip())
            found.append(iv)
        except Exception:
            continue
    return max(found) if found else int(default)


def _merge_unique_strings(*sources: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []
    for source in sources:
        for item in _safe_string_list(source, max_items=max_items):
            if item not in out:
                out.append(item)
            if len(out) >= max_items:
                return out[:max_items]
    return out[:max_items]


def _merge_unique_dict_rows(*sources: Any, max_items: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, list):
            continue
        for row in source:
            if not isinstance(row, dict):
                continue
            key = _safe_str(row.get("user_id") or row.get("id") or row.get("username") or repr(sorted(row.items())))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(row))
            if len(out) >= max_items:
                return out[:max_items]
    return out[:max_items]


def _normalized_live_profile(target: Optional[discord.abc.User], raw_profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = dict(raw_profile or {})

    age_days = _pick_first_meaningful(
        profile.get("account_age_days"),
        profile.get("age_days"),
        _account_age_days(target),
        default=0,
        allow_zero=True,
    )
    age_days = max(0, _safe_int(age_days, _account_age_days(target)))

    profile["account_age_days"] = age_days
    profile.setdefault("account_age_human", _account_age_human(target))

    if not _safe_str(profile.get("age_bucket")):
        profile["age_bucket"] = _age_bucket(age_days)
    if not _safe_str(profile.get("fingerprint")):
        profile["fingerprint"] = _behavior_fingerprint(target)
    if target is not None:
        profile.setdefault("default_avatar", _has_default_avatar(target))

    return profile


def _local_risk_profile(target: Optional[discord.abc.User]) -> Dict[str, Any]:
    if target is None:
        return {
            "risk_score": 0,
            "score": 0,
            "risk_level": "low",
            "level": "low",
            "evidence_tier": "clear",
            "suspicion_flags": [],
            "risk_reasons": [],
            "same_fingerprint_count": 0,
            "similar_name_count": 0,
            "same_age_bucket_count": 0,
            "burst_join_count": 0,
            "alt_cluster_size": 0,
            "alt_cluster_key": "",
            "cluster_members": [],
            "fingerprint": "",
            "account_age_days": 0,
            "account_age_human": "unknown",
            "age_bucket": "unknown",
            "default_avatar": False,
            "suspicious_name_pattern": False,
            "repeated_char_pattern": False,
            "joined_after_creation_seconds": None,
            "joined_after_creation_human": "",
            "is_bot_account": False,
        }

    if isinstance(target, discord.Member):
        try:
            profile = build_member_risk_profile(target) or {}
            if profile:
                return dict(profile)
        except Exception:
            pass

    age_days = _account_age_days(target)
    default_avatar = _has_default_avatar(target)
    username = _username_for_checks(target)
    lowered = username.lower()
    digit_ratio = _digit_ratio(username)
    repeat_run = _max_repeat_run(username)
    digit_run = _max_digit_run(username)

    if bool(getattr(target, "bot", False)):
        return {
            "risk_score": 0,
            "score": 0,
            "risk_level": "low",
            "level": "low",
            "evidence_tier": "clear",
            "suspicion_flags": ["bot_account"],
            "risk_reasons": ["Discord marks this account as a bot; excluded from alt-risk heuristics."],
            "same_fingerprint_count": 0,
            "similar_name_count": 0,
            "same_age_bucket_count": 0,
            "burst_join_count": 0,
            "alt_cluster_size": 0,
            "alt_cluster_key": "",
            "cluster_members": [],
            "fingerprint": _behavior_fingerprint(target),
            "account_age_days": age_days,
            "account_age_human": _account_age_human(target),
            "age_bucket": _age_bucket(age_days),
            "default_avatar": False,
            "suspicious_name_pattern": False,
            "repeated_char_pattern": False,
            "joined_after_creation_seconds": None,
            "joined_after_creation_human": "",
            "is_bot_account": True,
        }

    weak_points = 0
    flags: List[str] = []
    reasons: List[str] = []
    suspicious_name = False
    repeated_chars = False

    if age_days < 1:
        weak_points += 18
        flags.append("extremely_new_account")
        reasons.append("Account is extremely new.")
    elif age_days < 3:
        weak_points += 12
        flags.append("very_new_account")
        reasons.append("Account is very new.")
    elif age_days < 7:
        weak_points += 6
        flags.append("fresh_account")
        reasons.append("Account is new.")

    if default_avatar:
        weak_points += 6
        flags.append("default_avatar")
        reasons.append("Account is using the default Discord avatar.")

    if digit_ratio >= 0.65 and len(username) >= 6:
        suspicious_name = True
        weak_points += 7
        flags.append("very_high_digit_ratio")
        reasons.append("Username has an extremely high digit ratio.")
    elif digit_ratio >= 0.45 and len(username) >= 6:
        suspicious_name = True
        weak_points += 4
        flags.append("elevated_digit_ratio")
        reasons.append("Username has an elevated digit ratio.")

    if digit_run >= 4:
        suspicious_name = True
        weak_points += 4
        flags.append("long_digit_run")
        reasons.append("Username contains a long run of digits.")

    if repeat_run >= 4:
        repeated_chars = True
        weak_points += 5
        flags.append("repeated_chars")
        reasons.append("Username contains repeated characters.")

    if len(username) >= 8 and re.fullmatch(r"[a-z0-9._-]+", lowered or "") and digit_ratio >= 0.35:
        suspicious_name = True
        weak_points += 5
        flags.append("synthetic_style_name")
        reasons.append("Username pattern looks synthetic.")

    impersonation_terms = ("admin", "mod", "moderator", "staff", "support", "helper", "official", "security")
    if age_days <= 30 and any(term in lowered for term in impersonation_terms):
        suspicious_name = True
        weak_points += 6
        flags.append("staff_style_name")
        reasons.append("New account uses staff-leaning wording in the username.")

    weak_points = min(35, weak_points)
    evidence_tier = "suspicious" if weak_points >= 10 else "clear"
    score = max(20, min(45, weak_points)) if evidence_tier == "suspicious" else min(15, weak_points)

    return {
        "risk_score": score,
        "score": score,
        "risk_level": _score_to_level(score),
        "level": _score_to_level(score),
        "evidence_tier": evidence_tier,
        "suspicion_flags": _dedupe_list(flags, max_items=12),
        "risk_reasons": _dedupe_list(reasons, max_items=8),
        "same_fingerprint_count": 0,
        "similar_name_count": 0,
        "same_age_bucket_count": 0,
        "burst_join_count": 0,
        "alt_cluster_size": 0,
        "alt_cluster_key": "",
        "cluster_members": [],
        "fingerprint": _behavior_fingerprint(target),
        "account_age_days": age_days,
        "account_age_human": _account_age_human(target),
        "age_bucket": _age_bucket(age_days),
        "default_avatar": default_avatar,
        "suspicious_name_pattern": suspicious_name,
        "repeated_char_pattern": repeated_chars,
        "joined_after_creation_seconds": None,
        "joined_after_creation_human": "",
        "is_bot_account": False,
    }


def _build_merged_risk_source(
    *,
    guild_member: Dict[str, Any],
    latest_join: Dict[str, Any],
    live_profile: Dict[str, Any],
    target: Optional[discord.abc.User],
    warn_count: int,
) -> Dict[str, Any]:
    db = dict(guild_member or {})
    join = dict(latest_join or {})
    live = _normalized_live_profile(target, live_profile or {})
    local = _local_risk_profile(target)

    def _has_risk_payload(data: Dict[str, Any]) -> bool:
        return bool(
            _value_is_meaningful(data.get("risk_score"), allow_zero=True)
            or _value_is_meaningful(data.get("risk_level"))
            or _value_is_meaningful(data.get("evidence_tier"))
            or _value_is_meaningful(data.get("risk_reasons"))
            or _value_is_meaningful(data.get("suspicion_flags"))
            or _value_is_meaningful(data.get("alt_cluster_key"))
            or _value_is_meaningful(data.get("same_fingerprint_count"), allow_zero=True)
            or _value_is_meaningful(data.get("similar_name_count"), allow_zero=True)
        )

    authoritative = None
    for candidate in (live, join, db, local):
        if isinstance(candidate, dict) and _has_risk_payload(candidate):
            authoritative = dict(candidate)
            break

    if authoritative is None:
        authoritative = dict(local)

    merged = dict(authoritative)
    computed_age_days = _account_age_days(target)

    merged["risk_score"] = _safe_int(
        _pick_first_meaningful(
            authoritative.get("risk_score"),
            authoritative.get("score"),
            default=0,
            allow_zero=True,
        ),
        0,
    )
    merged["score"] = merged["risk_score"]

    merged["risk_level"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("risk_level"),
            authoritative.get("level"),
            default=_score_to_level(merged["risk_score"]),
        ),
        _score_to_level(merged["risk_score"]),
    )
    merged["level"] = merged["risk_level"]

    merged["evidence_tier"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("evidence_tier"),
            default="clear",
        ),
        "clear",
    )

    merged["account_age_days"] = _safe_int(
        _pick_first_meaningful(
            authoritative.get("account_age_days"),
            authoritative.get("age_days"),
            computed_age_days,
            default=0,
            allow_zero=True,
        ),
        computed_age_days,
    )
    merged["account_age_human"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("account_age_human"),
            _account_age_human(target),
            default=_account_age_human(target),
        ),
        _account_age_human(target),
    )
    merged["age_bucket"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("age_bucket"),
            _age_bucket(merged["account_age_days"]),
            default="unknown",
        ),
        "unknown",
    )

    merged["fingerprint"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("fingerprint"),
            authoritative.get("last_join_fingerprint"),
            _behavior_fingerprint(target),
            default="",
        )
    )
    merged["last_join_fingerprint"] = merged["fingerprint"]

    for key in (
        "alt_cluster_size",
        "burst_join_count",
        "same_fingerprint_count",
        "similar_name_count",
        "same_age_bucket_count",
        "identity_proof_match_count",
        "manual_confirmed_match_count",
        "manual_likely_match_count",
        "manual_not_linked_count",
    ):
        merged[key] = _safe_int(
            _pick_first_meaningful(
                authoritative.get(key),
                default=0,
                allow_zero=True,
            ),
            0,
        )

    merged["matched_identity_fingerprint"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("matched_identity_fingerprint"),
            default="",
        )
    )

    merged["alt_cluster_key"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("alt_cluster_key"),
            default="",
        )
    )
    merged["warn_count"] = max(0, int(warn_count or 0))
    merged["default_avatar"] = _safe_bool(
        _pick_first_meaningful(
            authoritative.get("default_avatar"),
            default=False,
            allow_zero=True,
        ),
        False,
    )
    merged["suspicious_name_pattern"] = _safe_bool(
        _pick_first_meaningful(
            authoritative.get("suspicious_name_pattern"),
            default=False,
            allow_zero=True,
        ),
        False,
    )
    merged["repeated_char_pattern"] = _safe_bool(
        _pick_first_meaningful(
            authoritative.get("repeated_char_pattern"),
            default=False,
            allow_zero=True,
        ),
        False,
    )
    merged["joined_after_creation_seconds"] = _pick_first_meaningful(
        authoritative.get("joined_after_creation_seconds"),
        default=None,
        allow_zero=True,
    )
    merged["joined_after_creation_human"] = _safe_str(
        _pick_first_meaningful(
            authoritative.get("joined_after_creation_human"),
            default="",
        )
    )

    merged["is_bot_account"] = _safe_bool(
        _pick_first_meaningful(
            authoritative.get("is_bot_account"),
            default=bool(getattr(target, "bot", False)) if target else False,
            allow_zero=True,
        ),
        bool(getattr(target, "bot", False)) if target else False,
    )

    merged["suspicion_flags"] = _merge_unique_strings(
        authoritative.get("suspicion_flags"),
        max_items=12,
    )
    merged["risk_reasons"] = _merge_unique_strings(
        authoritative.get("risk_reasons"),
        authoritative.get("reasons"),
        max_items=8,
    )
    merged["cluster_members"] = _merge_unique_dict_rows(
        authoritative.get("cluster_members"),
        max_items=8,
    )
    merged["joined_at"] = _safe_str(_pick_first_meaningful(join.get("joined_at"), db.get("joined_at"), default=""))
    merged["entry_method"] = _safe_str(_pick_first_meaningful(join.get("entry_method"), db.get("entry_method"), default=""))
    merged["verification_source"] = _safe_str(_pick_first_meaningful(join.get("verification_source"), db.get("verification_source"), default=""))

    if merged["is_bot_account"]:
        merged["risk_score"] = 0
        merged["score"] = 0
        merged["risk_level"] = "low"
        merged["level"] = "low"
        merged["evidence_tier"] = "clear"
        merged["alt_cluster_size"] = 0
        merged["burst_join_count"] = 0
        merged["same_fingerprint_count"] = 0
        merged["similar_name_count"] = 0
        merged["same_age_bucket_count"] = 0
        merged["alt_cluster_key"] = ""
        merged["cluster_members"] = []
        merged["identity_proof_match_count"] = 0
        merged["manual_confirmed_match_count"] = 0
        merged["manual_likely_match_count"] = 0
        merged["manual_not_linked_count"] = 0
        merged["matched_identity_fingerprint"] = ""
        merged["risk_reasons"] = ["Discord marks this account as a bot; excluded from alt-risk scoring."]
        merged["suspicion_flags"] = ["bot_account"]

    return merged


def _context_risk_value(
    guild_member: Dict[str, Any],
    live_profile: Dict[str, Any],
    warn_count: int = 0,
    merged_risk: Optional[Dict[str, Any]] = None,
) -> str:
    source = dict(merged_risk or {})
    if not source:
        source = _build_merged_risk_source(
            guild_member=guild_member or {},
            latest_join={},
            live_profile=live_profile or {},
            target=None,
            warn_count=warn_count,
        )
    if not source:
        return ""

    fingerprint = _safe_str(source.get("fingerprint") or source.get("last_join_fingerprint"))
    alt_cluster_key = _safe_str(source.get("alt_cluster_key"))
    alt_cluster_size = _safe_int(source.get("alt_cluster_size"), 0)
    burst_join_count = _safe_int(source.get("burst_join_count"), 0)
    same_fp = _safe_int(source.get("same_fingerprint_count"), 0)
    same_name = _safe_int(source.get("similar_name_count"), 0)
    same_age = _safe_int(source.get("same_age_bucket_count"), 0)
    joined_after_creation = _safe_str(source.get("joined_after_creation_human"))
    flags = _extract_flags_from_profile_like(source)
    if not flags:
        flags = _safe_string_list(source.get("suspicion_flags"), 12)
    reasons = _safe_string_list(source.get("risk_reasons") or source.get("reasons"), 8)

    lines: List[str] = [_risk_summary_header(source, warn_count=warn_count)]

    identity_proof_matches = _safe_int(source.get("identity_proof_match_count"), 0)
    manual_confirmed_matches = _safe_int(source.get("manual_confirmed_match_count"), 0)
    manual_likely_matches = _safe_int(source.get("manual_likely_match_count"), 0)
    manual_not_linked_count = _safe_int(source.get("manual_not_linked_count"), 0)

    signal_parts: List[str] = []
    if identity_proof_matches > 0:
        signal_parts.append(f"Verified identity matches: {identity_proof_matches}")
    if manual_confirmed_matches > 0:
        signal_parts.append(f"Manual confirmed links: {manual_confirmed_matches}")
    if manual_likely_matches > 0:
        signal_parts.append(f"Manual likely links: {manual_likely_matches}")
    if manual_not_linked_count > 0:
        signal_parts.append(f"Not-linked suppressions: {manual_not_linked_count}")
    if burst_join_count > 0:
        signal_parts.append(f"Join burst: {burst_join_count}")
    if same_fp > 0:
        signal_parts.append(f"Shared fingerprint matches: {same_fp}")
    if same_name > 0:
        signal_parts.append(f"Similar recent names: {same_name}")
    if same_age > 0:
        signal_parts.append(f"Same age-bucket joins: {same_age}")
    if alt_cluster_size > 1:
        signal_parts.append(f"Linked cluster size: {alt_cluster_size}")
    if signal_parts:
        lines.append("Signals: " + " • ".join(signal_parts))

    if joined_after_creation:
        lines.append(f"Created → joined: {joined_after_creation}")

    if flags:
        pretty_flags = [label for label in (_pretty_flag_label(x) for x in flags[:6]) if label]
        if pretty_flags:
            lines.append("Flags: " + ", ".join(pretty_flags))

    if reasons:
        lines.append("Why flagged: " + " | ".join(reasons[:3]))

    matched_identity_fingerprint = _safe_str(source.get("matched_identity_fingerprint"))
    if identity_proof_matches > 0 and matched_identity_fingerprint:
        lines.append(f"Verified identity fingerprint: `{matched_identity_fingerprint}`")
    elif same_fp > 0 and fingerprint:
        lines.append(f"Fingerprint group: `{fingerprint}`")
    if alt_cluster_size > 1 and alt_cluster_key:
        lines.append(f"Cluster reference: `{alt_cluster_key}`")

    return _chunk_lines(lines, 1000)


def _context_linked_accounts_value(
    guild_member: Dict[str, Any],
    live_profile: Dict[str, Any],
    merged_risk: Optional[Dict[str, Any]] = None,
) -> str:
    source = dict(merged_risk or {})
    if not source:
        source = _build_merged_risk_source(
            guild_member=guild_member or {},
            latest_join={},
            live_profile=live_profile or {},
            target=None,
            warn_count=0,
        )
    if not source:
        return ""

    cluster_members = source.get("cluster_members")
    if not isinstance(cluster_members, list) or not cluster_members:
        return ""

    lines: List[str] = []
    for row in cluster_members[:6]:
        if not isinstance(row, dict):
            continue
        display_name = _safe_str(row.get("display_name"))
        username = _safe_str(row.get("username"))
        uid = _safe_str(row.get("user_id"))
        label = display_name or username or uid or "Unknown"
        if username and display_name and username != display_name:
            label = f"{display_name} / {username}"
        if uid:
            label = f"{label} (`{uid}`)"
        lines.append(f"• {label} — {_pretty_cluster_reason(row.get('reason'))}")

    return _chunk_lines(lines, 900)


async def _fetch_member_context_snapshot(
    guild: discord.Guild,
    target: Optional[discord.abc.User],
) -> Dict[str, Any]:
    try:
        if guild is None or target is None:
            return {}

        target_id = int(getattr(target, "id", 0) or 0)
        if target_id <= 0:
            return {}

        guild_member_task = _run_blocking_db(_sb_select_guild_member_sync, int(guild.id), target_id)
        latest_join_task = _run_blocking_db(_sb_select_latest_join_sync, int(guild.id), target_id)
        warn_count_task = _run_blocking_db(_sb_select_warn_count_sync, int(guild.id), target_id)
        truth_context_task = _run_blocking_db(_sb_get_identity_truth_context_sync, int(guild.id), target_id)

        guild_member_row, latest_join_row, warn_count, truth_context_row = await asyncio.gather(
            guild_member_task,
            latest_join_task,
            warn_count_task,
            truth_context_task,
            return_exceptions=True,
        )

        guild_member = guild_member_row if isinstance(guild_member_row, dict) else {}
        latest_join = latest_join_row if isinstance(latest_join_row, dict) else {}
        warns = int(warn_count or 0) if not isinstance(warn_count, Exception) else 0
        truth_context = truth_context_row if isinstance(truth_context_row, dict) else {}

        live_profile: Dict[str, Any] = {}
        try:
            if isinstance(target, discord.Member):
                live_profile = build_member_risk_profile(target) or {}
        except Exception:
            live_profile = {}

        normalized_live = _normalized_live_profile(target, live_profile)
        merged_risk = _build_merged_risk_source(
            guild_member=guild_member,
            latest_join=latest_join,
            live_profile=normalized_live,
            target=target,
            warn_count=warns,
        )

        proof_matches = list(truth_context.get("proof_matches") or []) if isinstance(truth_context, dict) else []
        manual_confirmed = list(truth_context.get("manual_confirmed") or []) if isinstance(truth_context, dict) else []
        manual_likely = list(truth_context.get("manual_likely") or []) if isinstance(truth_context, dict) else []
        manual_not_linked = list(truth_context.get("manual_not_linked") or []) if isinstance(truth_context, dict) else []

        merged_risk["identity_proof_match_count"] = max(_safe_int(merged_risk.get("identity_proof_match_count"), 0), len(proof_matches))
        merged_risk["manual_confirmed_match_count"] = max(_safe_int(merged_risk.get("manual_confirmed_match_count"), 0), len(manual_confirmed))
        merged_risk["manual_likely_match_count"] = max(_safe_int(merged_risk.get("manual_likely_match_count"), 0), len(manual_likely))
        merged_risk["manual_not_linked_count"] = max(_safe_int(merged_risk.get("manual_not_linked_count"), 0), len(manual_not_linked))
        if proof_matches and not _safe_str(merged_risk.get("matched_identity_fingerprint")):
            try:
                merged_risk["matched_identity_fingerprint"] = _safe_str(proof_matches[0].get("identity_fingerprint"))
            except Exception:
                pass

        return {
            "guild_member": guild_member,
            "latest_join": latest_join,
            "warn_count": warns,
            "live_profile": normalized_live,
            "truth_context": truth_context,
            "merged_risk": merged_risk,
        }
    except Exception as e:
        print("⚠️ _fetch_member_context_snapshot failed:", repr(e))
        return {}


# ==========================================================
# Quick-mod button IDs
# ==========================================================

def make_mod_id(action: str, user_id: int, extra: str = "") -> str:
    a = (action or "").strip().lower()
    uid = int(user_id)
    ex = (extra or "").strip()
    return f"sv:mod:{a}:{uid}:{ex}" if ex else f"sv:mod:{a}:{uid}"


def _parse_mod_custom_id(custom_id: str) -> Optional[Tuple[str, int, str]]:
    try:
        raw = str(custom_id or "").strip()
        if not raw.startswith("sv:mod:"):
            return None

        parts = raw.split(":", 4)
        if len(parts) < 4:
            return None

        action = str(parts[2]).strip().lower()
        user_id = int(parts[3])
        extra = str(parts[4]).strip() if len(parts) >= 5 else ""
        return (action, user_id, extra)
    except Exception:
        return None


def build_quick_mod_view(user_id: int) -> discord.ui.View:
    timeout_minutes = int(globals().get("MOD_TIMEOUT_MINUTES", 10) or 10)

    v = discord.ui.View(timeout=None)
    v.add_item(
        discord.ui.Button(
            label="🔨 Ban",
            style=discord.ButtonStyle.danger,
            custom_id=make_mod_id("ban", user_id),
        )
    )
    v.add_item(
        discord.ui.Button(
            label="👢 Kick",
            style=discord.ButtonStyle.danger,
            custom_id=make_mod_id("kick", user_id),
        )
    )
    v.add_item(
        discord.ui.Button(
            label="⏳ Timeout",
            style=discord.ButtonStyle.secondary,
            custom_id=make_mod_id("timeout", user_id, f"m={timeout_minutes}"),
        )
    )
    return v


# ==========================================================
# Channels
# ==========================================================

def _get_modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        if MODLOG_CHANNEL_ID and int(MODLOG_CHANNEL_ID) != 0:
            ch = guild.get_channel(int(MODLOG_CHANNEL_ID))
            if isinstance(ch, discord.TextChannel):
                return ch
    except Exception:
        pass
    return None


async def _post_modlog(
    guild: discord.Guild,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
):
    ch = _get_modlog_channel(guild)
    if not ch:
        print(f"⚠️ Modlog channel not found for guild {getattr(guild, 'id', 'unknown')}")
        return

    try:
        await ch.send(embed=embed, view=view)
    except Exception as e:
        print("⚠️ Failed sending modlog message:", repr(e))


# ==========================================================
# Audit log helpers
# ==========================================================

def _audit_entry_is_recent(entry: discord.AuditLogEntry, lookback_seconds: int) -> bool:
    try:
        ts = getattr(entry, "created_at", None)
        if not ts:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_now_utc() - ts.astimezone(timezone.utc)).total_seconds() <= int(lookback_seconds)
    except Exception:
        return False


def _iter_audit_changes(entry: discord.AuditLogEntry) -> List[Any]:
    try:
        changes = getattr(entry, "changes", None)
        if changes is None:
            return []
        try:
            return list(changes)
        except Exception:
            return []
    except Exception:
        return []


def _audit_change_value(change: Any, attr: str, default=None):
    try:
        return getattr(change, attr, default)
    except Exception:
        return default


def _audit_change_key(change: Any) -> str:
    try:
        key = getattr(change, "key", None)
        return str(key or "")
    except Exception:
        return ""


def _role_name_list(items: Any) -> List[str]:
    out: List[str] = []
    try:
        if not items:
            return out
        for item in items:
            name = getattr(item, "name", None)
            if name:
                out.append(str(name))
            elif isinstance(item, dict) and item.get("name"):
                out.append(str(item.get("name")))
            else:
                out.append(str(item))
    except Exception:
        return out
    return out


def _role_id_name_set_from_audit_value(items: Any) -> set[tuple[int, str]]:
    out: set[tuple[int, str]] = set()
    try:
        if not items:
            return out
        for item in items:
            rid = _safe_int(getattr(item, "id", None), _safe_int(item.get("id") if isinstance(item, dict) else None, 0))
            name = _safe_str(getattr(item, "name", None), _safe_str(item.get("name") if isinstance(item, dict) else None))
            if rid > 0:
                out.add((rid, name or str(rid)))
            elif name:
                out.add((0, name))
    except Exception:
        return out
    return out


def _extract_role_delta_from_entry(entry: discord.AuditLogEntry) -> Tuple[List[str], List[str]]:
    added: List[str] = []
    removed: List[str] = []

    try:
        for change in _iter_audit_changes(entry):
            key = _audit_change_key(change).lower()
            before = _audit_change_value(change, "before")
            after = _audit_change_value(change, "after")

            if key == "roles":
                before_set = _role_id_name_set_from_audit_value(before)
                after_set = _role_id_name_set_from_audit_value(after)
                added.extend([name for _, name in sorted(after_set - before_set)])
                removed.extend([name for _, name in sorted(before_set - after_set)])

            elif key == "$add":
                added.extend(_role_name_list(after or before))
            elif key == "$remove":
                removed.extend(_role_name_list(after or before))
    except Exception:
        pass

    def _dedupe(seq: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in seq:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return (_dedupe(added), _dedupe(removed))


def _extract_timeout_from_entry(entry: discord.AuditLogEntry) -> Tuple[Optional[datetime], Optional[datetime]]:
    before_timeout: Optional[datetime] = None
    after_timeout: Optional[datetime] = None

    try:
        for change in _iter_audit_changes(entry):
            key = _audit_change_key(change).lower()
            if key not in {"communication_disabled_until", "timed_out_until"}:
                continue

            before_timeout = _safe_dt_utc(_audit_change_value(change, "before"))
            after_timeout = _safe_dt_utc(_audit_change_value(change, "after"))
            break
    except Exception:
        pass

    return (before_timeout, after_timeout)


def _extract_nick_from_entry(entry: discord.AuditLogEntry) -> Tuple[Optional[str], Optional[str]]:
    try:
        for change in _iter_audit_changes(entry):
            key = _audit_change_key(change).lower()
            if key == "nick":
                return (
                    _audit_change_value(change, "before"),
                    _audit_change_value(change, "after"),
                )
    except Exception:
        pass
    return (None, None)


def _audit_role_delta_match_score(entry: discord.AuditLogEntry, added_roles: List[str], removed_roles: List[str]) -> int:
    try:
        entry_added, entry_removed = _extract_role_delta_from_entry(entry)
        score = 0

        wanted_added = set(str(x).lower() for x in added_roles if str(x).strip())
        wanted_removed = set(str(x).lower() for x in removed_roles if str(x).strip())
        actual_added = set(str(x).lower() for x in entry_added if str(x).strip())
        actual_removed = set(str(x).lower() for x in entry_removed if str(x).strip())

        if wanted_added:
            score += len(wanted_added & actual_added) * 10
        else:
            score += 3 if not actual_added else 0

        if wanted_removed:
            score += len(wanted_removed & actual_removed) * 10
        else:
            score += 3 if not actual_removed else 0

        if wanted_added == actual_added and wanted_removed == actual_removed:
            score += 20

        return score
    except Exception:
        return 0


def _audit_timeout_match_score(
    entry: discord.AuditLogEntry,
    before_timeout: Optional[datetime],
    after_timeout: Optional[datetime],
) -> int:
    try:
        e_before, e_after = _extract_timeout_from_entry(entry)
        score = 0

        if before_timeout is None and after_timeout is not None:
            if e_after is not None:
                score += 25
                if e_before is None:
                    score += 10
        elif before_timeout is not None and after_timeout is None:
            if e_before is not None and e_after is None:
                score += 35
        elif before_timeout != after_timeout:
            if e_before == before_timeout:
                score += 10
            if e_after == after_timeout:
                score += 10

        if after_timeout and e_after:
            diff = abs((after_timeout - e_after).total_seconds())
            if diff <= 120:
                score += 10
        return score
    except Exception:
        return 0


def _audit_nick_match_score(entry: discord.AuditLogEntry, before_nick: Optional[str], after_nick: Optional[str]) -> int:
    try:
        e_before, e_after = _extract_nick_from_entry(entry)
        score = 0
        if before_nick == e_before:
            score += 10
        if after_nick == e_after:
            score += 10
        if before_nick == e_before and after_nick == e_after:
            score += 20
        return score
    except Exception:
        return 0


def _audit_recency_score(entry: discord.AuditLogEntry) -> int:
    try:
        created = _safe_dt_utc(getattr(entry, "created_at", None))
        if not created:
            return 0
        delta = abs((_now_utc() - created).total_seconds())
        if delta <= 5:
            return 25
        if delta <= 15:
            return 18
        if delta <= 30:
            return 12
        if delta <= 60:
            return 8
        if delta <= 90:
            return 4
        return 0
    except Exception:
        return 0


async def _audit_find_recent_kick(guild: discord.Guild, target_id: int) -> Optional[discord.AuditLogEntry]:
    try:
        me = _bot_member_for_guild(guild)
        if not me or not me.guild_permissions.view_audit_log:
            return None

        lookback = int(globals().get("MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 90) or 90)

        best: Optional[discord.AuditLogEntry] = None
        best_score = -1

        async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.kick):
            try:
                if not entry or not getattr(entry, "target", None):
                    continue
                if int(getattr(entry.target, "id", 0) or 0) != int(target_id):
                    continue
                if not _audit_entry_is_recent(entry, lookback):
                    continue

                score = _audit_recency_score(entry)
                if score > best_score:
                    best = entry
                    best_score = score
            except Exception:
                continue
        return best
    except Exception:
        return None


async def _audit_find_recent_ban(guild: discord.Guild, target_id: int) -> Optional[discord.AuditLogEntry]:
    try:
        me = _bot_member_for_guild(guild)
        if not me or not me.guild_permissions.view_audit_log:
            return None

        lookback = int(globals().get("MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 90) or 90)

        best: Optional[discord.AuditLogEntry] = None
        best_score = -1

        async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.ban):
            try:
                if not entry or not getattr(entry, "target", None):
                    continue
                if int(getattr(entry.target, "id", 0) or 0) != int(target_id):
                    continue
                if not _audit_entry_is_recent(entry, lookback):
                    continue

                score = _audit_recency_score(entry)
                if score > best_score:
                    best = entry
                    best_score = score
            except Exception:
                continue
        return best
    except Exception:
        return None


async def _audit_find_recent_member_update(guild: discord.Guild, target_id: int) -> Optional[discord.AuditLogEntry]:
    try:
        me = _bot_member_for_guild(guild)
        if not me or not me.guild_permissions.view_audit_log:
            return None

        lookback = int(globals().get("MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 90) or 90)
        best: Optional[discord.AuditLogEntry] = None
        best_score = -1

        actions: List[discord.AuditLogAction] = []
        try:
            actions.append(discord.AuditLogAction.member_role_update)
        except Exception:
            pass
        try:
            actions.append(discord.AuditLogAction.member_update)
        except Exception:
            pass

        for act in actions:
            async for entry in guild.audit_logs(limit=40, action=act):
                try:
                    tgt = getattr(entry, "target", None)
                    tid = int(getattr(tgt, "id", 0) or 0)
                    if tid != int(target_id):
                        continue
                    if not _audit_entry_is_recent(entry, lookback):
                        continue

                    score = _audit_recency_score(entry)
                    if score > best_score:
                        best = entry
                        best_score = score
                except Exception:
                    continue

        return best
    except Exception:
        return None


async def _audit_find_best_member_update_match(
    guild: discord.Guild,
    target_id: int,
    *,
    added_roles: Optional[List[str]] = None,
    removed_roles: Optional[List[str]] = None,
    before_timeout: Optional[datetime] = None,
    after_timeout: Optional[datetime] = None,
    before_nick: Optional[str] = None,
    after_nick: Optional[str] = None,
) -> Optional[discord.AuditLogEntry]:
    try:
        me = _bot_member_for_guild(guild)
        if not me or not me.guild_permissions.view_audit_log:
            return None

        lookback = int(globals().get("MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 90) or 90)
        best: Optional[discord.AuditLogEntry] = None
        best_score = -1

        actions: List[discord.AuditLogAction] = []
        try:
            actions.append(discord.AuditLogAction.member_role_update)
        except Exception:
            pass
        try:
            actions.append(discord.AuditLogAction.member_update)
        except Exception:
            pass

        wanted_added = added_roles or []
        wanted_removed = removed_roles or []

        for act in actions:
            async for entry in guild.audit_logs(limit=50, action=act):
                try:
                    tgt = getattr(entry, "target", None)
                    tid = int(getattr(tgt, "id", 0) or 0)
                    if tid != int(target_id):
                        continue
                    if not _audit_entry_is_recent(entry, lookback):
                        continue

                    score = _audit_recency_score(entry)

                    if wanted_added or wanted_removed:
                        score += _audit_role_delta_match_score(entry, wanted_added, wanted_removed)

                    if before_timeout != after_timeout:
                        score += _audit_timeout_match_score(entry, before_timeout, after_timeout)

                    if before_nick != after_nick:
                        score += _audit_nick_match_score(entry, before_nick, after_nick)

                    if score > best_score:
                        best = entry
                        best_score = score
                except Exception:
                    continue

        return best
    except Exception:
        return None


async def _audit_find_recent_voice_target_entry(
    guild: discord.Guild,
    target_id: int,
    *,
    lookback_seconds: Optional[int] = None,
) -> Optional[discord.AuditLogEntry]:
    try:
        me = _bot_member_for_guild(guild)
        if not me or not me.guild_permissions.view_audit_log:
            return None

        lookback = int(lookback_seconds or globals().get("MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 90) or 90)

        best: Optional[discord.AuditLogEntry] = None
        best_score = -1

        async for entry in guild.audit_logs(limit=50, action=discord.AuditLogAction.member_update):
            try:
                tgt = getattr(entry, "target", None)
                tid = int(getattr(tgt, "id", 0) or 0)
                if tid != int(target_id):
                    continue
                if not _audit_entry_is_recent(entry, lookback):
                    continue

                score = _audit_recency_score(entry)
                if score > best_score:
                    best = entry
                    best_score = score
            except Exception:
                continue
        return best
    except Exception:
        return None


# ==========================================================
# Staff action ledger / abuse detection
# ==========================================================

def _sb_staff_actions():
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")
    table_name = str(globals().get("STAFF_ACTION_LOG_TABLE", "staff_action_logs") or "staff_action_logs")
    return sb.table(table_name)


def sb_log_staff_action(
    *,
    guild_id: int,
    actor_id: Optional[int],
    actor_display: str,
    target_id: Optional[int],
    target_display: str,
    action: str,
    reason: str = "",
    duration: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    payload: Dict[str, Any] = {
        "guild_id": str(int(guild_id)),
        "actor_id": str(int(actor_id)) if actor_id else None,
        "actor_display": str(actor_display or "Unknown"),
        "target_id": str(int(target_id)) if target_id else None,
        "target_display": str(target_display or "Unknown"),
        "action": str(action or "").strip().lower(),
        "reason": str(reason or "").strip() or None,
        "duration": str(duration or "").strip() or None,
        "metadata": _json_safe(metadata or {}),
        "created_at": _now_utc().isoformat(),
    }

    def _write():
        _sb_staff_actions().insert(payload).execute()

    return _execute_db_write_with_retry("staff action log insert", _write)


def sb_count_staff_actions_since(
    *,
    guild_id: int,
    actor_id: int,
    since_dt: datetime,
    actions: Optional[List[str]] = None,
) -> int:
    try:
        q = (
            _sb_staff_actions()
            .select("id", count="exact")
            .eq("guild_id", str(int(guild_id)))
            .eq("actor_id", str(int(actor_id)))
            .gte("created_at", _safe_dt_utc(since_dt).isoformat())
        )

        if actions:
            actions_clean = [str(a).strip().lower() for a in actions if str(a).strip()]
            if actions_clean:
                q = q.in_("action", actions_clean)

        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception as e:
        print("⚠️ Failed counting staff actions:", repr(e))
        return 0


_STAFF_ALERT_LAST_SENT: Dict[str, datetime] = {}
_QUICK_MOD_ACTION_LOCKS: Dict[str, asyncio.Lock] = {}
_MODLOG_ROUTER_REGISTERED = False
_RECENT_MODLOG_ACTIONS: Dict[str, datetime] = {}
_DUPLICATE_MODLOG_WINDOW_SECONDS = 15


def _quick_mod_lock(guild_id: int, target_id: int, action: str) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(target_id)}:{str(action).strip().lower()}"
    lock = _QUICK_MOD_ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _QUICK_MOD_ACTION_LOCKS[key] = lock
    return lock


def _recent_modlog_key(
    *,
    guild_id: int,
    action: str,
    actor_id: Optional[int],
    target_id: Optional[int],
    duration: Optional[str] = None,
    until: Optional[datetime] = None,
    added_roles: Optional[List[str]] = None,
    removed_roles: Optional[List[str]] = None,
) -> str:
    a = str(action or "").strip().lower()
    actor = int(actor_id or 0)
    target = int(target_id or 0)
    dur = str(duration or "").strip().lower()
    until_key = ""
    try:
        if until:
            until_key = _safe_dt_utc(until).replace(second=0, microsecond=0).isoformat()
    except Exception:
        until_key = ""

    added_key = ",".join(sorted(str(x).strip().lower() for x in (added_roles or []) if str(x).strip()))
    removed_key = ",".join(sorted(str(x).strip().lower() for x in (removed_roles or []) if str(x).strip()))

    return f"{int(guild_id)}:{a}:{target}:{actor}:{dur}:{until_key}:{added_key}:{removed_key}"


def _should_suppress_duplicate_modlog(
    *,
    guild_id: int,
    action: str,
    actor_id: Optional[int],
    target_id: Optional[int],
    duration: Optional[str] = None,
    until: Optional[datetime] = None,
    added_roles: Optional[List[str]] = None,
    removed_roles: Optional[List[str]] = None,
) -> bool:
    action_clean = str(action or "").strip().lower()
    if action_clean in {"member_join", "abuse_alert", "quick_mod_failed"}:
        return False

    now = _now_utc()
    cutoff = timedelta(seconds=_DUPLICATE_MODLOG_WINDOW_SECONDS)

    try:
        for key, ts in list(_RECENT_MODLOG_ACTIONS.items()):
            if (now - ts) > cutoff:
                _RECENT_MODLOG_ACTIONS.pop(key, None)
    except Exception:
        pass

    key = _recent_modlog_key(
        guild_id=guild_id,
        action=action_clean,
        actor_id=actor_id,
        target_id=target_id,
        duration=duration,
        until=until,
        added_roles=added_roles,
        removed_roles=removed_roles,
    )

    last = _RECENT_MODLOG_ACTIONS.get(key)
    if last and (now - last) <= cutoff:
        return True

    _RECENT_MODLOG_ACTIONS[key] = now
    return False


# ==========================================================
# Embed helpers
# ==========================================================

def _extract_reason_duration_from_reason(reason: str) -> Tuple[str, Optional[str]]:
    try:
        raw = str(reason or "").strip()
        if not raw:
            return ("", None)

        duration = None
        lowered = raw.lower()

        m = re.search(r"\bfor\s+(\d+)\s+minute", lowered)
        if m:
            try:
                duration = f"{int(m.group(1))} minute(s)"
            except Exception:
                duration = None

        parts = raw.split("— by ")
        clean_reason = parts[0].strip() if parts else raw
        return (clean_reason, duration)
    except Exception:
        return (str(reason or "").strip(), None)


def _determine_member_update_action(entry: discord.AuditLogEntry) -> Tuple[str, Dict[str, Any]]:
    try:
        before_timeout, after_timeout = _extract_timeout_from_entry(entry)
        added_roles, removed_roles = _extract_role_delta_from_entry(entry)
        before_nick, after_nick = _extract_nick_from_entry(entry)

        if after_timeout and (not before_timeout or after_timeout != before_timeout):
            return ("timeout", {"until": after_timeout, "added_roles": added_roles, "removed_roles": removed_roles})
        if before_timeout and not after_timeout:
            return ("untimeout", {"until": None, "added_roles": added_roles, "removed_roles": removed_roles})
        if added_roles and not removed_roles:
            return ("add_role", {"added_roles": added_roles, "removed_roles": removed_roles})
        if removed_roles and not added_roles:
            return ("remove_role", {"added_roles": added_roles, "removed_roles": removed_roles})
        if before_nick != after_nick:
            return ("nickname_change", {"before_nick": before_nick, "after_nick": after_nick})
        if added_roles or removed_roles:
            return ("member_update", {"added_roles": added_roles, "removed_roles": removed_roles})
    except Exception:
        pass

    return ("member_update", {"added_roles": [], "removed_roles": []})


def _modlog_color_for_action(action: str) -> discord.Color:
    a = str(action or "").lower().strip()
    if a in {"ban"}:
        return discord.Color.red()
    if a in {"kick"}:
        return discord.Color.orange()
    if a in {"timeout"}:
        return discord.Color.gold()
    if a in {"untimeout"}:
        return discord.Color.green()
    if a in {"warn"}:
        return discord.Color.yellow()
    if a in {"add_role", "remove_role"}:
        return discord.Color.blurple()
    if a in {"server_mute", "server_unmute"}:
        return discord.Color.orange()
    if a in {"server_deafen", "server_undeafen"}:
        return discord.Color.dark_orange()
    if a in {"voice_move", "voice_disconnect"}:
        return discord.Color.teal()
    if a in {"nickname_change"}:
        return discord.Color.dark_teal()
    if a in {"member_join"}:
        return discord.Color.green()
    if a in {"abuse_alert"}:
        return discord.Color.red()
    if a in {"quick_mod_failed"}:
        return discord.Color.red()
    return discord.Color.blurple()


def _modlog_title_for_action(action: str) -> str:
    a = str(action or "").lower().strip()
    mapping = {
        "ban": "🔨 Member Banned",
        "kick": "👢 Member Kicked",
        "timeout": "⏳ Member Timed Out",
        "untimeout": "✅ Timeout Removed",
        "warn": "⚠️ Member Warned",
        "add_role": "➕ Role Added",
        "remove_role": "➖ Role Removed",
        "member_update": "🛠️ Member Updated",
        "member_join": "📥 Member Joined",
        "server_mute": "🔇 Server Mute Applied",
        "server_unmute": "🔊 Server Mute Removed",
        "server_deafen": "🙉 Server Deafen Applied",
        "server_undeafen": "🙊 Server Deafen Removed",
        "voice_move": "🔀 Voice Channel Move",
        "voice_disconnect": "📴 Voice Disconnect",
        "nickname_change": "✏️ Nickname Changed",
        "abuse_alert": "🚨 Staff Action Alert",
        "quick_mod_failed": "❌ Quick Mod Failed",
    }
    return mapping.get(a, "🛠️ Moderation Action")


def build_modlog_embed(
    guild: discord.Guild,
    *,
    target: Optional[discord.abc.User],
    action: str,
    actor_display: str,
    reason: str = "",
    duration: Optional[str] = None,
    until: Optional[datetime] = None,
    added_roles: Optional[List[str]] = None,
    removed_roles: Optional[List[str]] = None,
    extra_fields: Optional[List[Tuple[str, str, bool]]] = None,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    added_roles = added_roles or []
    removed_roles = removed_roles or []
    extra_fields = extra_fields or []
    context_snapshot = context_snapshot or {}

    embed = discord.Embed(
        title=_modlog_title_for_action(action),
        color=_modlog_color_for_action(action),
        timestamp=_now_utc(),
    )

    target_id = int(getattr(target, "id", 0) or 0) if target else 0
    mention = getattr(target, "mention", "") if target else ""
    display_name = _safe_str(getattr(target, "display_name", None) if target else "")
    username = _safe_str(getattr(target, "name", None) if target else "")
    user_lines: List[str] = []

    if mention:
        user_lines.append(mention)
    if display_name:
        user_lines.append(f"Display: `{display_name}`")
    if username and username != display_name:
        user_lines.append(f"Username: `{username}`")
    if target_id:
        user_lines.append(f"ID: `{target_id}`")
    if not user_lines:
        user_lines.append("Unknown")

    embed.add_field(name="User", value=_truncate("\n".join(user_lines), 1024), inline=False)
    embed.add_field(name="Action", value=str(action).upper(), inline=True)
    embed.add_field(name="Staff", value=actor_display or "Unknown", inline=True)

    clean_reason, parsed_duration = _extract_reason_duration_from_reason(reason)
    duration_value = duration or parsed_duration

    if duration_value:
        embed.add_field(name="Duration", value=duration_value, inline=True)

    if until:
        embed.add_field(name="Until", value=_discord_ts(until), inline=True)

    if clean_reason:
        embed.add_field(name="Reason", value=_truncate(clean_reason, 1024), inline=False)

    if added_roles:
        embed.add_field(
            name="Added Roles",
            value=_truncate(", ".join([f"`{r}`" for r in added_roles]), 1024),
            inline=False,
        )

    if removed_roles:
        embed.add_field(
            name="Removed Roles",
            value=_truncate(", ".join([f"`{r}`" for r in removed_roles]), 1024),
            inline=False,
        )

    guild_member = context_snapshot.get("guild_member") if isinstance(context_snapshot.get("guild_member"), dict) else {}
    latest_join = context_snapshot.get("latest_join") if isinstance(context_snapshot.get("latest_join"), dict) else {}
    live_profile = context_snapshot.get("live_profile") if isinstance(context_snapshot.get("live_profile"), dict) else {}
    truth_context = context_snapshot.get("truth_context") if isinstance(context_snapshot.get("truth_context"), dict) else {}
    merged_risk = context_snapshot.get("merged_risk") if isinstance(context_snapshot.get("merged_risk"), dict) else {}
    warn_count = _safe_int(context_snapshot.get("warn_count"), 0)

    role_state_value = _context_role_state_value(guild_member)
    if role_state_value:
        embed.add_field(name="Role State", value=role_state_value, inline=False)

    entry_context_value = _context_entry_value(guild_member, latest_join)
    if entry_context_value:
        embed.add_field(name="Join / Entry Context", value=entry_context_value, inline=False)

    risk_value = _context_risk_value(guild_member, live_profile, warn_count=warn_count, merged_risk=merged_risk)
    if risk_value:
        embed.add_field(name="Risk Overview", value=risk_value, inline=False)

    truth_value = _context_truth_value(guild, truth_context, merged_risk=merged_risk)
    if truth_value:
        embed.add_field(name="Identity Truth", value=truth_value, inline=False)

    linked_accounts_value = _context_linked_accounts_value(guild_member, live_profile, merged_risk=merged_risk)
    if linked_accounts_value:
        embed.add_field(name="Potential Linked Accounts", value=linked_accounts_value, inline=False)

    for field_name, field_value, inline in extra_fields:
        try:
            if field_value:
                embed.add_field(
                    name=str(field_name),
                    value=_truncate(field_value, 1024),
                    inline=bool(inline),
                )
        except Exception:
            pass

    try:
        embed.set_footer(text=f"{guild.name} • Stoney Verify")
    except Exception:
        pass

    return embed


def build_member_join_embed(
    guild: discord.Guild,
    *,
    member: discord.Member,
    profile: Optional[Dict[str, Any]] = None,
    entry_context: Optional[Dict[str, Any]] = None,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    profile = profile or {}
    entry_context = entry_context or {}
    context_snapshot = context_snapshot or {}

    merged_risk = context_snapshot.get("merged_risk") if isinstance(context_snapshot.get("merged_risk"), dict) else {}
    normalized_profile = _build_merged_risk_source(
        guild_member=context_snapshot.get("guild_member") if isinstance(context_snapshot.get("guild_member"), dict) else {},
        latest_join=context_snapshot.get("latest_join") if isinstance(context_snapshot.get("latest_join"), dict) else {},
        live_profile=profile or context_snapshot.get("live_profile") or {},
        target=member,
        warn_count=_safe_int(context_snapshot.get("warn_count"), 0),
    )
    if merged_risk:
        normalized_profile.update({k: v for k, v in merged_risk.items() if _value_is_meaningful(v, allow_zero=True)})

    score = _safe_int(normalized_profile.get("risk_score"), 0)
    level = _safe_str(normalized_profile.get("risk_level"), "low").lower()
    tier = _safe_str(normalized_profile.get("evidence_tier"), "clear")
    age_days = _safe_int(normalized_profile.get("account_age_days"), _account_age_days(member))
    age_human = _safe_str(normalized_profile.get("account_age_human"), _account_age_human(member))
    age_bucket = _safe_str(normalized_profile.get("age_bucket"), _age_bucket(age_days))
    fingerprint = _safe_str(normalized_profile.get("fingerprint"))
    join_gap_human = _safe_str(normalized_profile.get("joined_after_creation_human"))
    alt_summary = ""
    try:
        alt_summary = _safe_str(build_alt_detection_summary(member))
    except Exception:
        alt_summary = ""

    extra_fields: List[Tuple[str, str, bool]] = []

    if getattr(member, "created_at", None):
        extra_fields.append(
            (
                "Account Created",
                _truncate(f"{_discord_ts(_safe_dt_utc(member.created_at))}\nAge: {age_human}", 1024),
                True,
            )
        )
    if getattr(member, "joined_at", None):
        extra_fields.append(("Joined At", _discord_ts(_safe_dt_utc(member.joined_at)), True))
    if join_gap_human:
        extra_fields.append(("Created → Joined", join_gap_human, True))

    risk_head = _join_nonempty(
        [
            tier.replace("_", " ").upper(),
            f"{level.upper()} / {score}/100",
            f"Account age: {age_human}",
            f"Bucket: {age_bucket}" if age_bucket else "",
        ]
    )
    if risk_head:
        extra_fields.append(("Join Risk", risk_head, False))

    if fingerprint:
        extra_fields.append(("Fingerprint", fingerprint, False))

    if alt_summary:
        extra_fields.append(("Alt Summary", alt_summary, False))

    if getattr(member, "bot", False):
        extra_fields.append(("Bot Account", "YES", True))

    flags = _extract_flags_from_profile_like(normalized_profile)
    if not flags:
        flags = _safe_string_list(normalized_profile.get("suspicion_flags"), 12)
    if flags:
        extra_fields.append(("Suspicion Flags", ", ".join([_pretty_flag_label(x) for x in flags[:10] if _pretty_flag_label(x)]), False))

    reasons = _safe_string_list(normalized_profile.get("risk_reasons") or normalized_profile.get("reasons"), 8)
    if reasons:
        extra_fields.append(("Why Flagged", " • ".join(reasons[:4]), False))

    invite_line = _join_nonempty(
        [
            f"method={_safe_str(entry_context.get('entry_method') or normalized_profile.get('entry_method'))}" if _safe_str(entry_context.get("entry_method") or normalized_profile.get("entry_method")) else "",
            f"source={_safe_str(entry_context.get('verification_source') or normalized_profile.get('verification_source'))}" if _safe_str(entry_context.get("verification_source") or normalized_profile.get("verification_source")) else "",
            f"invite={_safe_str(entry_context.get('invite_code'))}" if _safe_str(entry_context.get("invite_code")) else "",
            f"inviter={_safe_str(entry_context.get('invited_by_name'))}" if _safe_str(entry_context.get("invited_by_name")) else "",
        ]
    )
    if invite_line:
        extra_fields.append(("Join Path", invite_line.replace("method=", "Method: ").replace("source=", "Source: ").replace("invite=", "Invite: ").replace("inviter=", "Inviter: "), False))

    context_snapshot = dict(context_snapshot)
    context_snapshot["merged_risk"] = normalized_profile

    embed = build_modlog_embed(
        guild,
        target=member,
        action="member_join",
        actor_display="System",
        reason=_safe_str(entry_context.get("entry_reason"), "Member joined the guild."),
        extra_fields=extra_fields,
        context_snapshot=context_snapshot,
    )

    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    return embed


async def _maybe_alert_staff_abuse(
    guild: discord.Guild,
    *,
    actor_id: Optional[int],
    actor_display: str,
    action: str,
) -> None:
    try:
        if not actor_id:
            return

        window_minutes = max(1, int(globals().get("STAFF_ACTION_ALERT_WINDOW_MINUTES", 10) or 10))
        since_dt = _now_utc() - timedelta(minutes=window_minutes)

        action_clean = str(action or "").strip().lower()
        threshold = int(globals().get("STAFF_ACTION_ALERT_THRESHOLD", 12) or 12)
        actions_for_bucket: Optional[List[str]] = None
        label = "all moderation actions"

        if action_clean in {"timeout", "untimeout"}:
            threshold = int(globals().get("STAFF_ACTION_ALERT_TIMEOUT_THRESHOLD", threshold) or threshold)
            actions_for_bucket = ["timeout", "untimeout"]
            label = "timeout actions"
        elif action_clean in {
            "server_mute", "server_unmute", "server_deafen", "server_undeafen",
            "voice_move", "voice_disconnect"
        }:
            threshold = int(globals().get("STAFF_ACTION_ALERT_VOICE_THRESHOLD", threshold) or threshold)
            actions_for_bucket = [
                "server_mute", "server_unmute", "server_deafen", "server_undeafen",
                "voice_move", "voice_disconnect",
            ]
            label = "voice moderation actions"
        elif action_clean in {"add_role", "remove_role"}:
            threshold = int(globals().get("STAFF_ACTION_ALERT_ROLE_THRESHOLD", threshold) or threshold)
            actions_for_bucket = ["add_role", "remove_role"]
            label = "role-change actions"
        elif action_clean in {"ban"}:
            threshold = int(globals().get("STAFF_ACTION_ALERT_BAN_THRESHOLD", threshold) or threshold)
            actions_for_bucket = ["ban"]
            label = "ban actions"

        count = sb_count_staff_actions_since(
            guild_id=int(guild.id),
            actor_id=int(actor_id),
            since_dt=since_dt,
            actions=actions_for_bucket,
        )

        if count < threshold:
            return

        dedupe_key = f"{guild.id}:{actor_id}:{label}"
        last_sent = _STAFF_ALERT_LAST_SENT.get(dedupe_key)
        if last_sent and (_now_utc() - last_sent).total_seconds() < max(120, window_minutes * 60 // 2):
            return

        _STAFF_ALERT_LAST_SENT[dedupe_key] = _now_utc()

        embed = discord.Embed(
            title=_modlog_title_for_action("abuse_alert"),
            color=_modlog_color_for_action("abuse_alert"),
            timestamp=_now_utc(),
            description=(
                f"Possible staff abuse pattern detected.\n\n"
                f"**Staff:** {actor_display}\n"
                f"**Window:** last {window_minutes} minute(s)\n"
                f"**Category:** {label}\n"
                f"**Count:** {count}\n"
                f"**Threshold:** {threshold}"
            ),
        )
        embed.add_field(name="Actor ID", value=str(actor_id), inline=True)
        embed.add_field(name="Latest Action Type", value=action_clean or "unknown", inline=True)

        await _post_modlog(guild, embed)
    except Exception as e:
        print("⚠️ Failed sending staff abuse alert:", repr(e))


async def post_dashboard_mod_action_log(
    guild: discord.Guild,
    *,
    target: Optional[discord.abc.User],
    action: str,
    actor_display: str,
    actor_id: Optional[int] = None,
    reason: str = "",
    duration: Optional[str] = None,
    until: Optional[datetime] = None,
    added_roles: Optional[List[str]] = None,
    removed_roles: Optional[List[str]] = None,
    extra_fields: Optional[List[Tuple[str, str, bool]]] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        target_id = int(getattr(target, "id", 0) or 0) or None

        if _should_suppress_duplicate_modlog(
            guild_id=int(guild.id),
            action=action,
            actor_id=actor_id,
            target_id=target_id,
            duration=duration,
            until=until,
            added_roles=added_roles,
            removed_roles=removed_roles,
        ):
            print(
                f"ℹ️ Suppressed duplicate modlog entry "
                f"guild={guild.id} action={action} target={target_id} actor={actor_id}"
            )
            return

        context_snapshot = await _fetch_member_context_snapshot(guild, target)

        embed = build_modlog_embed(
            guild,
            target=target,
            action=action,
            actor_display=actor_display,
            reason=reason,
            duration=duration,
            until=until,
            added_roles=added_roles,
            removed_roles=removed_roles,
            extra_fields=extra_fields,
            context_snapshot=context_snapshot,
        )
        await _post_modlog(guild, embed, view=view)

        target_display = _member_display(target)

        snapshot_for_metadata = {
            "guild_member": context_snapshot.get("guild_member") or {},
            "latest_join": context_snapshot.get("latest_join") or {},
            "warn_count": _safe_int(context_snapshot.get("warn_count"), 0),
            "live_profile": context_snapshot.get("live_profile") or {},
            "truth_context": context_snapshot.get("truth_context") or {},
            "merged_risk": context_snapshot.get("merged_risk") or {},
        }

        sb_log_staff_action(
            guild_id=int(guild.id),
            actor_id=actor_id,
            actor_display=actor_display,
            target_id=target_id,
            target_display=target_display,
            action=action,
            reason=reason,
            duration=duration,
            metadata={
                "until": _safe_dt_utc(until).isoformat() if until else None,
                "added_roles": added_roles or [],
                "removed_roles": removed_roles or [],
                "extra_fields": extra_fields or [],
                "context_snapshot": snapshot_for_metadata,
            },
        )

        await _maybe_alert_staff_abuse(
            guild,
            actor_id=actor_id,
            actor_display=actor_display,
            action=action,
        )
    except Exception as e:
        print("⚠️ Failed posting dashboard mod action log:", repr(e))


async def post_member_join_risk_log(
    guild: discord.Guild,
    member: discord.Member,
    *,
    entry_context: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        profile = profile or {}
        entry_context = entry_context or {}
        context_snapshot = await _fetch_member_context_snapshot(guild, member)

        embed = build_member_join_embed(
            guild,
            member=member,
            profile=profile,
            entry_context=entry_context,
            context_snapshot=context_snapshot,
        )
        await _post_modlog(guild, embed, view=view)
    except Exception as e:
        print("⚠️ Failed posting member join risk log:", repr(e))


# ==========================================================
# Quick mod execution
# ==========================================================

async def _respond_interaction_ephemeral(
    interaction: discord.Interaction,
    content: str,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass


async def _resolve_target_member_from_interaction(
    interaction: discord.Interaction,
    target_user_id: int,
) -> Tuple[Optional[discord.Guild], Optional[discord.Member]]:
    try:
        guild = interaction.guild
        if guild is None:
            return (None, None)

        member = guild.get_member(int(target_user_id))
        if member is not None:
            return (guild, member)

        try:
            member = await guild.fetch_member(int(target_user_id))
            return (guild, member)
        except Exception:
            return (guild, None)
    except Exception:
        return (None, None)


async def _log_quick_mod_failure(
    guild: discord.Guild,
    *,
    target: Optional[discord.abc.User],
    actor_display: str,
    actor_id: Optional[int],
    reason: str,
    action: str,
) -> None:
    try:
        await post_dashboard_mod_action_log(
            guild,
            target=target,
            action="quick_mod_failed",
            actor_display=actor_display,
            actor_id=actor_id,
            reason=f"{action}: {reason}",
            extra_fields=[("Attempted Action", action.upper(), True)],
            view=build_quick_mod_view(int(getattr(target, "id", 0) or 0)) if target and getattr(target, "id", None) else None,
        )
    except Exception as e:
        print("⚠️ Failed logging quick mod failure:", repr(e))


async def _execute_quick_mod_action(
    interaction: discord.Interaction,
    *,
    action: str,
    target: discord.Member,
    extra: str = "",
) -> None:
    guild = interaction.guild
    if guild is None:
        await _respond_interaction_ephemeral(interaction, "This action can only be used inside a server.")
        return

    moderator = interaction.user if isinstance(interaction.user, discord.Member) else None
    actor_display = _member_display(moderator)
    actor_id = int(moderator.id) if moderator else None

    if moderator is None:
        await _respond_interaction_ephemeral(interaction, "Only guild staff can use this button.")
        return

    lock = _quick_mod_lock(int(guild.id), int(target.id), action)
    async with lock:
        try:
            if not _interaction_has_manage_messages(interaction):
                await _respond_interaction_ephemeral(interaction, "You do not have permission to use quick mod actions.")
                await _log_quick_mod_failure(
                    guild,
                    target=target,
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason="missing manage_messages permission",
                    action=action,
                )
                return

            allowed, why_not = _can_act_on_member(moderator, target)
            if not allowed:
                await _respond_interaction_ephemeral(interaction, why_not)
                await _log_quick_mod_failure(
                    guild,
                    target=target,
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=why_not,
                    action=action,
                )
                return

            bot_allowed, bot_reason = _bot_can_act_on_member(guild, target)
            if not bot_allowed:
                await _respond_interaction_ephemeral(interaction, bot_reason)
                await _log_quick_mod_failure(
                    guild,
                    target=target,
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=bot_reason,
                    action=action,
                )
                return

            act = str(action or "").strip().lower()

            if act == "ban":
                if not _moderator_has_permission(moderator, "ban_members"):
                    await _respond_interaction_ephemeral(interaction, "You need Ban Members for that.")
                    return

                reason = _quick_mod_default_reason("ban", moderator)
                try:
                    await target.ban(reason=reason, delete_message_days=0)
                except TypeError:
                    await guild.ban(target, reason=reason, delete_message_seconds=0)

                await _respond_interaction_ephemeral(
                    interaction,
                    f"🔨 Banned {target.mention}."
                )

                await post_dashboard_mod_action_log(
                    guild,
                    target=target,
                    action="ban",
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=reason,
                    view=build_quick_mod_view(int(target.id)),
                )
                return

            if act == "kick":
                if not _moderator_has_permission(moderator, "kick_members"):
                    await _respond_interaction_ephemeral(interaction, "You need Kick Members for that.")
                    return

                reason = _quick_mod_default_reason("kick", moderator)
                await target.kick(reason=reason)

                await _respond_interaction_ephemeral(
                    interaction,
                    f"👢 Kicked {target.mention}."
                )

                await post_dashboard_mod_action_log(
                    guild,
                    target=target,
                    action="kick",
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=reason,
                    view=build_quick_mod_view(int(target.id)),
                )
                return

            if act == "timeout":
                if not _moderator_has_permission(moderator, "moderate_members"):
                    await _respond_interaction_ephemeral(interaction, "You need Moderate Members for that.")
                    return

                minutes = _parse_timeout_minutes(extra)
                until = _now_utc() + timedelta(minutes=minutes)
                reason = _quick_mod_default_reason("timeout", moderator)

                try:
                    await target.timeout(until, reason=reason)
                except Exception:
                    await target.edit(
                        timed_out_until=until,
                        reason=reason,
                    )

                await _respond_interaction_ephemeral(
                    interaction,
                    f"⏳ Timed out {target.mention} for {_duration_label_from_minutes(minutes)}."
                )

                await post_dashboard_mod_action_log(
                    guild,
                    target=target,
                    action="timeout",
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=reason,
                    duration=_duration_label_from_minutes(minutes),
                    until=until,
                    view=build_quick_mod_view(int(target.id)),
                )
                return

            await _respond_interaction_ephemeral(interaction, "Unknown quick mod action.")
        except discord.Forbidden as e:
            msg = "Discord denied that action. Check permissions and role hierarchy."
            print(f"⚠️ Quick mod forbidden: action={action} target={target.id} error={repr(e)}")
            await _respond_interaction_ephemeral(interaction, msg)
            await _log_quick_mod_failure(
                guild,
                target=target,
                actor_display=actor_display,
                actor_id=actor_id,
                reason=msg,
                action=action,
            )
        except discord.HTTPException as e:
            msg = f"Discord API error while running {action}: {repr(e)}"
            print(f"⚠️ Quick mod HTTPException: {msg}")
            await _respond_interaction_ephemeral(interaction, msg[:1900])
            await _log_quick_mod_failure(
                guild,
                target=target,
                actor_display=actor_display,
                actor_id=actor_id,
                reason=msg,
                action=action,
            )
        except Exception as e:
            print(f"⚠️ Quick mod unexpected error: action={action} target={target.id} error={repr(e)}")
            try:
                traceback.print_exc()
            except Exception:
                pass
            msg = f"Unexpected error while running {action}."
            await _respond_interaction_ephemeral(interaction, msg)
            await _log_quick_mod_failure(
                guild,
                target=target,
                actor_display=actor_display,
                actor_id=actor_id,
                reason=repr(e),
                action=action,
            )


async def handle_quick_mod_interaction(interaction: discord.Interaction) -> bool:
    try:
        if interaction.type != discord.InteractionType.component:
            return False

        data = getattr(interaction, "data", None) or {}
        custom_id = str(data.get("custom_id") or "").strip()
        parsed = _parse_mod_custom_id(custom_id)
        if not parsed:
            return False

        action, user_id, extra = parsed
        guild, target = await _resolve_target_member_from_interaction(interaction, user_id)
        if guild is None:
            await _respond_interaction_ephemeral(interaction, "This action can only be used in a guild.")
            return True

        if target is None:
            await _respond_interaction_ephemeral(
                interaction,
                "Target member is no longer in the server, so this button cannot be used."
            )
            return True

        await _execute_quick_mod_action(
            interaction,
            action=action,
            target=target,
            extra=extra,
        )
        return True
    except Exception as e:
        print("⚠️ handle_quick_mod_interaction failed:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        try:
            await _respond_interaction_ephemeral(interaction, "Quick mod handler crashed.")
        except Exception:
            pass
        return True


def register_modlog_interaction_router() -> None:
    global _MODLOG_ROUTER_REGISTERED
    if _MODLOG_ROUTER_REGISTERED:
        return

    _MODLOG_ROUTER_REGISTERED = True

    @bot.listen("on_interaction")
    async def _modlog_quick_mod_router(interaction: discord.Interaction):
        try:
            await handle_quick_mod_interaction(interaction)
        except Exception as e:
            print("⚠️ modlog interaction router error:", repr(e))


register_modlog_interaction_router()


# ==========================================================
# Event-facing member / voice comparison helpers
# ==========================================================

def _role_delta_from_members(before: discord.Member, after: discord.Member) -> Tuple[List[str], List[str]]:
    try:
        before_roles = {int(r.id): r for r in list(getattr(before, "roles", []) or [])}
        after_roles = {int(r.id): r for r in list(getattr(after, "roles", []) or [])}

        added_ids = [rid for rid in after_roles.keys() if rid not in before_roles]
        removed_ids = [rid for rid in before_roles.keys() if rid not in after_roles]

        added = [after_roles[rid].name for rid in added_ids if not after_roles[rid].is_default()]
        removed = [before_roles[rid].name for rid in removed_ids if not before_roles[rid].is_default()]
        return (added, removed)
    except Exception:
        return ([], [])


def _timeout_delta_from_members(before: discord.Member, after: discord.Member) -> Tuple[Optional[datetime], Optional[datetime]]:
    return (_safe_dt_utc(getattr(before, "timed_out_until", None)), _safe_dt_utc(getattr(after, "timed_out_until", None)))


def _nickname_delta_from_members(before: discord.Member, after: discord.Member) -> Tuple[Optional[str], Optional[str]]:
    try:
        return (getattr(before, "nick", None), getattr(after, "nick", None))
    except Exception:
        return (None, None)


async def maybe_log_member_update_diff(guild: discord.Guild, before: discord.Member, after: discord.Member) -> bool:
    try:
        added_roles, removed_roles = _role_delta_from_members(before, after)
        before_timeout, after_timeout = _timeout_delta_from_members(before, after)
        before_nick, after_nick = _nickname_delta_from_members(before, after)

        entry = await _audit_find_best_member_update_match(
            guild,
            int(after.id),
            added_roles=added_roles,
            removed_roles=removed_roles,
            before_timeout=before_timeout,
            after_timeout=after_timeout,
            before_nick=before_nick,
            after_nick=after_nick,
        )
        actor_display, reason = _format_actor_from_audit(entry)
        actor_id = _actor_id_from_audit(entry)

        did_log = False

        if after_timeout and (not before_timeout or after_timeout != before_timeout):
            duration = None
            try:
                delta = after_timeout - _now_utc()
                minutes = max(1, int(delta.total_seconds() // 60))
                duration = _duration_label_from_minutes(minutes)
            except Exception:
                duration = None

            await post_dashboard_mod_action_log(
                guild,
                target=after,
                action="timeout",
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                duration=duration,
                until=after_timeout,
                view=build_quick_mod_view(int(after.id)),
            )
            did_log = True
        elif before_timeout and not after_timeout:
            await post_dashboard_mod_action_log(
                guild,
                target=after,
                action="untimeout",
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                view=build_quick_mod_view(int(after.id)),
            )
            did_log = True

        if added_roles:
            await post_dashboard_mod_action_log(
                guild,
                target=after,
                action="add_role",
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                added_roles=added_roles,
                view=build_quick_mod_view(int(after.id)),
            )
            did_log = True

        if removed_roles:
            await post_dashboard_mod_action_log(
                guild,
                target=after,
                action="remove_role",
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                removed_roles=removed_roles,
                view=build_quick_mod_view(int(after.id)),
            )
            did_log = True

        if before_nick != after_nick:
            await post_dashboard_mod_action_log(
                guild,
                target=after,
                action="nickname_change",
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                extra_fields=[
                    ("Before", before_nick or "—", True),
                    ("After", after_nick or "—", True),
                ],
                view=build_quick_mod_view(int(after.id)),
            )
            did_log = True

        return did_log
    except Exception as e:
        print("⚠️ maybe_log_member_update_diff failed:", repr(e))
        return False


async def maybe_log_voice_state_update(
    guild: discord.Guild,
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> bool:
    try:
        did_log = False

        entry = await _audit_find_recent_voice_target_entry(guild, int(member.id))
        actor_display, reason = _format_actor_from_audit(entry)
        actor_id = _actor_id_from_audit(entry)

        before_channel = getattr(before, "channel", None)
        after_channel = getattr(after, "channel", None)
        before_channel_name = getattr(before_channel, "name", None) if before_channel else None
        after_channel_name = getattr(after_channel, "name", None) if after_channel else None

        if getattr(before, "mute", None) != getattr(after, "mute", None):
            action = "server_mute" if bool(getattr(after, "mute", False)) else "server_unmute"
            await post_dashboard_mod_action_log(
                guild,
                target=member,
                action=action,
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                extra_fields=[
                    ("Before", "Muted" if bool(getattr(before, "mute", False)) else "Unmuted", True),
                    ("After", "Muted" if bool(getattr(after, "mute", False)) else "Unmuted", True),
                ],
                view=build_quick_mod_view(int(member.id)),
            )
            did_log = True

        if getattr(before, "deaf", None) != getattr(after, "deaf", None):
            action = "server_deafen" if bool(getattr(after, "deaf", False)) else "server_undeafen"
            await post_dashboard_mod_action_log(
                guild,
                target=member,
                action=action,
                actor_display=actor_display,
                actor_id=actor_id,
                reason=reason,
                extra_fields=[
                    ("Before", "Deafened" if bool(getattr(before, "deaf", False)) else "Undeafened", True),
                    ("After", "Deafened" if bool(getattr(after, "deaf", False)) else "Undeafened", True),
                ],
                view=build_quick_mod_view(int(member.id)),
            )
            did_log = True

        before_cid = int(getattr(before_channel, "id", 0) or 0)
        after_cid = int(getattr(after_channel, "id", 0) or 0)

        if before_cid != after_cid:
            if before_channel and after_channel:
                await post_dashboard_mod_action_log(
                    guild,
                    target=member,
                    action="voice_move",
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=reason,
                    extra_fields=[
                        ("From", before_channel_name or f"`{before_cid}`", True),
                        ("To", after_channel_name or f"`{after_cid}`", True),
                    ],
                    view=build_quick_mod_view(int(member.id)),
                )
                did_log = True
            elif before_channel and not after_channel:
                await post_dashboard_mod_action_log(
                    guild,
                    target=member,
                    action="voice_disconnect",
                    actor_display=actor_display,
                    actor_id=actor_id,
                    reason=reason,
                    extra_fields=[("Disconnected From", before_channel_name or f"`{before_cid}`", False)],
                    view=build_quick_mod_view(int(member.id)),
                )
                did_log = True

        return did_log
    except Exception as e:
        print("⚠️ maybe_log_voice_state_update failed:", repr(e))
        return False


# ==========================================================
# Convenience wrappers
# ==========================================================

async def maybe_log_recent_member_update(guild: discord.Guild, target: discord.Member) -> bool:
    try:
        entry = await _audit_find_recent_member_update(guild, int(target.id))
        if not entry:
            return False

        actor_display, reason = _format_actor_from_audit(entry)
        actor_id = _actor_id_from_audit(entry)
        action, details = _determine_member_update_action(entry)

        duration = None
        until = details.get("until")
        if action == "timeout" and until:
            try:
                minutes = max(1, int((until - _now_utc()).total_seconds() // 60))
                duration = _duration_label_from_minutes(minutes)
            except Exception:
                duration = None

        await post_dashboard_mod_action_log(
            guild,
            target=target,
            action=action,
            actor_display=actor_display,
            actor_id=actor_id,
            reason=reason,
            duration=duration,
            until=until,
            added_roles=details.get("added_roles") or [],
            removed_roles=details.get("removed_roles") or [],
            view=build_quick_mod_view(int(target.id)),
        )
        return True
    except Exception as e:
        print("⚠️ maybe_log_recent_member_update failed:", repr(e))
        return False


async def maybe_log_recent_kick(guild: discord.Guild, target_user: discord.abc.User) -> bool:
    try:
        entry = await _audit_find_recent_kick(guild, int(target_user.id))
        if not entry:
            return False

        actor_display, reason = _format_actor_from_audit(entry)
        actor_id = _actor_id_from_audit(entry)
        await post_dashboard_mod_action_log(
            guild,
            target=target_user,
            action="kick",
            actor_display=actor_display,
            actor_id=actor_id,
            reason=reason,
            view=build_quick_mod_view(int(target_user.id)),
        )
        return True
    except Exception as e:
        print("⚠️ maybe_log_recent_kick failed:", repr(e))
        return False


async def maybe_log_recent_ban(guild: discord.Guild, target_user: discord.abc.User) -> bool:
    try:
        entry = await _audit_find_recent_ban(guild, int(target_user.id))
        if not entry:
            return False

        actor_display, reason = _format_actor_from_audit(entry)
        actor_id = _actor_id_from_audit(entry)
        await post_dashboard_mod_action_log(
            guild,
            target=target_user,
            action="ban",
            actor_display=actor_display,
            actor_id=actor_id,
            reason=reason,
            view=build_quick_mod_view(int(target_user.id)),
        )
        return True
    except Exception as e:
        print("⚠️ maybe_log_recent_ban failed:", repr(e))
        return False
