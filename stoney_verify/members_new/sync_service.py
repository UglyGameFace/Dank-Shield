# stoney_verify/members_new/sync_service.py
from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import discord

from ..globals import *  # noqa: F401,F403
from .membership_authority import collect_membership_snapshot, departure_reconciliation_allowed


# ============================================================
# guild_members sync service
# ------------------------------------------------------------
# Goals:
# - keep guild_members as the main member truth table
# - preserve entry/source metadata used by dashboard
# - preserve name history / leave-rejoin history
# - avoid breaking on mixed / evolving schemas
# - avoid repeated schema-cache failures for optional columns
# - keep member truth deterministic after verification actions
# ============================================================

GUILD_MEMBERS_TABLE = "guild_members"
MEMBER_JOINS_TABLE = "member_joins"
TICKETS_TABLE = "tickets"

_OPTIONAL_GUILD_MEMBER_COLUMNS = {
    "in_voice",
    "voice_channel_id",
    "voice_channel_name",
    "voice_muted",
    "voice_deafened",
    "voice_self_muted",
    "voice_self_deafened",
    "voice_streaming",
    "voice_video",
    "voice_suppressed",
    "nickname",
    "roles",
    "top_role",
    "highest_role_id",
    "highest_role_name",
    "previous_usernames",
    "previous_display_names",
    "previous_nicknames",
    "last_seen_username",
    "last_seen_display_name",
    "last_seen_nickname",
    "first_seen_at",
    "last_seen_at",
    "left_at",
    "rejoined_at",
    "times_joined",
    "times_left",
    "is_bot",
    "avatar_hash",
    "invited_by",
    "invited_by_name",
    "invite_code",
    "vouched_by",
    "vouched_by_name",
    "approved_by",
    "approved_by_name",
    "verification_ticket_id",
    "source_ticket_id",
    "entry_method",
    "verification_source",
    "join_source",
    "vanity_used",
    "entry_reason",
    "approval_reason",
    "entry_truth_quality",
    "entry_confidence",
    "entry_quality_reason",
    "entry_conflict",
    "has_any_role",
    "has_unverified",
    "has_verified_role",
    "has_staff_role",
    "has_secondary_verified_role",
    "has_cosmetic_only",
    "role_state",
    "role_state_reason",
    "risk_score",
    "risk_level",
    "risk_reasons",
    "fingerprint",
    "alt_cluster_key",
    "alt_cluster_size",
    "burst_join_count",
    "same_fingerprint_count",
    "similar_name_count",
    "same_age_bucket_count",
    "suspicious_name_pattern",
    "repeated_char_pattern",
    "default_avatar",
    "account_age_days",
    "age_bucket",
    "digit_ratio",
    "underscore_ratio",
    "cluster_members",
    "suspicion_flags",
    "risk_last_evaluated_at",
    "last_join_risk_score",
    "last_join_risk_level",
    "last_join_fingerprint",
    "alt_notes",
    "data_health",
}
_OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT: Dict[str, Optional[bool]] = {
    col: None for col in _OPTIONAL_GUILD_MEMBER_COLUMNS
}


# ============================================================
# Generic helpers
# ============================================================

def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or isinstance(v, bool):
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _safe_str(v: Any) -> str:
    try:
        return str(v or "")
    except Exception:
        return ""


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        text = str(v).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(v)
    except Exception:
        return default




def _safe_string_list(value: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
        else:
            text = str(value or "").strip()
            if text:
                out.append(text)
    except Exception:
        pass
    return out[:max_items]


def _safe_json_object_list(value: Any, max_items: int = 10) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append(dict(item))
    except Exception:
        pass
    return out[:max_items]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(str(value).strip())
    except Exception:
        return float(default)


def _derive_alt_cluster_key_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    try:
        explicit = str(profile.get("alt_cluster_key") or "").strip()
        if explicit:
            return explicit

        fingerprint = str(profile.get("fingerprint") or "").strip()
        username_key = str(
            profile.get("username_normalized")
            or profile.get("display_name_normalized")
            or ""
        ).strip()
        age_bucket = str(profile.get("age_bucket") or "").strip()

        if _as_int(profile.get("same_fingerprint_count"), 0) > 0 and fingerprint:
            return f"fp:{fingerprint}"
        if _as_int(profile.get("similar_name_count"), 0) > 0 and username_key:
            return f"name:{username_key[:48]}"
    except Exception:
        pass
    return None


def _derive_suspicion_flags_from_profile(profile: Dict[str, Any]) -> List[str]:
    flags = _safe_string_list(profile.get("suspicion_flags"), 20)
    try:
        if flags:
            return flags
        if _as_int(profile.get("account_age_days"), 999999) <= 1:
            flags.append("extremely_new_account")
        elif _as_int(profile.get("account_age_days"), 999999) <= 3:
            flags.append("very_new_account")
        elif _as_int(profile.get("account_age_days"), 999999) <= 7:
            flags.append("fresh_account")
        if bool(profile.get("default_avatar")):
            flags.append("default_avatar")
        if bool(profile.get("suspicious_name_pattern")):
            flags.append("suspicious_name_pattern")
        if bool(profile.get("repeated_char_pattern")):
            flags.append("repeated_character_pattern")
        if _as_float(profile.get("digit_ratio"), 0.0) >= 0.45:
            flags.append("very_high_digit_ratio")
        elif _as_float(profile.get("digit_ratio"), 0.0) >= 0.25:
            flags.append("elevated_digit_ratio")
        if _as_float(profile.get("underscore_ratio"), 0.0) >= 0.18:
            flags.append("high_underscore_ratio")
        if _as_int(profile.get("burst_count"), 0) > 0:
            flags.append("join_burst")
        if _as_int(profile.get("same_fingerprint_count"), 0) > 0:
            flags.append("shared_behavior_fingerprint")
        if _as_int(profile.get("similar_name_count"), 0) > 0:
            flags.append("similar_recent_username")
        if _as_int(profile.get("same_age_bucket_count"), 0) > 0:
            flags.append("age_bucket_cluster")
    except Exception:
        pass
    return flags[:20]


def _build_risk_payload_from_profile(
    risk_profile: Optional[Dict[str, Any]],
    *,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(risk_profile, dict):
        return {}

    now_value = now_iso or _sync_iso_now()
    score = max(
        0,
        min(
            100,
            _as_int(
                risk_profile.get("risk_score"),
                _as_int(risk_profile.get("score"), 0),
            ),
        ),
    )
    level_raw = str(risk_profile.get("level") or "low").strip().lower()
    level = level_raw if level_raw in {"low", "medium", "high", "critical"} else "low"
    fingerprint = str(risk_profile.get("fingerprint") or "").strip() or None
    alt_cluster_key = _derive_alt_cluster_key_from_profile(risk_profile)
    same_fingerprint_count = max(0, _as_int(risk_profile.get("same_fingerprint_count"), 0))
    similar_name_count = max(0, _as_int(risk_profile.get("similar_name_count"), 0))
    same_age_bucket_count = max(0, _as_int(risk_profile.get("same_age_bucket_count"), 0))
    burst_join_count = max(0, _as_int(risk_profile.get("burst_count"), 0))
    alt_cluster_size = max(0, _as_int(risk_profile.get("alt_cluster_size"), 0))
    if alt_cluster_size <= 0 and alt_cluster_key:
        alt_cluster_size = 1 + max(same_fingerprint_count, similar_name_count, same_age_bucket_count)

    return {
        "risk_score": score,
        "risk_level": level,
        "risk_reasons": _safe_string_list(risk_profile.get("reasons"), 12),
        "fingerprint": fingerprint,
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "burst_join_count": burst_join_count,
        "same_fingerprint_count": same_fingerprint_count,
        "similar_name_count": similar_name_count,
        "same_age_bucket_count": same_age_bucket_count,
        "suspicious_name_pattern": bool(risk_profile.get("suspicious_name_pattern")),
        "repeated_char_pattern": bool(risk_profile.get("repeated_char_pattern")),
        "default_avatar": bool(risk_profile.get("default_avatar")),
        "account_age_days": _as_int(risk_profile.get("account_age_days"), 0),
        "age_bucket": str(risk_profile.get("age_bucket") or "").strip() or None,
        "digit_ratio": round(_as_float(risk_profile.get("digit_ratio"), 0.0), 3),
        "underscore_ratio": round(_as_float(risk_profile.get("underscore_ratio"), 0.0), 3),
        "cluster_members": _safe_json_object_list(risk_profile.get("cluster_members"), 8),
        "suspicion_flags": _derive_suspicion_flags_from_profile(risk_profile),
        "risk_last_evaluated_at": now_value,
        "last_join_risk_score": score,
        "last_join_risk_level": level,
        "last_join_fingerprint": fingerprint,
    }


def _sync_iso_now() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


async def _run_blocking_db(
    fn,
    *args,
    retries: int = 1,
    retry_delay: float = 0.35,
    **kwargs,
):
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt >= retries:
                raise
            try:
                await asyncio.sleep(retry_delay * (attempt + 1))
            except Exception:
                pass

    if last_exc:
        raise last_exc


def _safe_member_username(member: discord.Member) -> str:
    try:
        return str(getattr(member, "name", None) or getattr(member, "user", None) or "")
    except Exception:
        return ""


def _safe_member_display_name(member: discord.Member) -> str:
    try:
        return str(getattr(member, "display_name", None) or getattr(member, "name", None) or "")
    except Exception:
        return ""


def _safe_member_nickname(member: discord.Member) -> str:
    try:
        return str(getattr(member, "nick", None) or "")
    except Exception:
        return ""


def _safe_member_avatar_url(member: discord.Member) -> Optional[str]:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return None


def _safe_member_avatar_hash(member: discord.Member) -> Optional[str]:
    try:
        avatar = getattr(member, "avatar", None)
        if avatar is None:
            return None
        key = getattr(avatar, "key", None)
        if key:
            return str(key)
    except Exception:
        pass

    try:
        display_avatar = getattr(member, "display_avatar", None)
        key = getattr(display_avatar, "key", None)
        if key:
            return str(key)
    except Exception:
        pass

    return None


def _append_unique_history(existing: Any, value: str, max_items: int = 15) -> List[str]:
    out: List[str] = []

    try:
        if isinstance(existing, list):
            for item in existing:
                if item is None:
                    continue
                text = str(item).strip()
                if text and text not in out:
                    out.append(text)
    except Exception:
        out = []

    v = str(value or "").strip()
    if v and v not in out:
        out.append(v)

    if len(out) > max_items:
        out = out[-max_items:]

    return out


def _coalesce_str(*values: Any) -> Optional[str]:
    for value in values:
        text = _safe_str(value).strip()
        if text and text.lower() not in {"none", "null"}:
            return text
    return None


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _member_voice_snapshot(member: discord.Member) -> Dict[str, Any]:
    try:
        vs = getattr(member, "voice", None)
        ch = getattr(vs, "channel", None) if vs else None

        return {
            "in_voice": bool(ch),
            "voice_channel_id": str(getattr(ch, "id", "")) if ch else None,
            "voice_channel_name": str(getattr(ch, "name", "")) if ch else None,
            "voice_muted": bool(getattr(vs, "mute", False)) if vs else False,
            "voice_deafened": bool(getattr(vs, "deaf", False)) if vs else False,
            "voice_self_muted": bool(getattr(vs, "self_mute", False)) if vs else False,
            "voice_self_deafened": bool(getattr(vs, "self_deaf", False)) if vs else False,
            "voice_streaming": bool(getattr(vs, "self_stream", False)) if vs else False,
            "voice_video": bool(getattr(vs, "self_video", False)) if vs else False,
            "voice_suppressed": bool(getattr(vs, "suppress", False)) if vs else False,
        }
    except Exception:
        return {
            "in_voice": False,
            "voice_channel_id": None,
            "voice_channel_name": None,
            "voice_muted": False,
            "voice_deafened": False,
            "voice_self_muted": False,
            "voice_self_deafened": False,
            "voice_streaming": False,
            "voice_video": False,
            "voice_suppressed": False,
        }

def _member_role_snapshot(member: discord.Member) -> Dict[str, Any]:
    from stoney_verify import role_truth

    return role_truth.build_member_role_snapshot(member)

def _minimal_member_payload(member: discord.Member, in_guild: bool = True) -> Dict[str, Any]:
    now_iso = _sync_iso_now()
    snap = _member_role_snapshot(member)

    return {
        "guild_id": str(member.guild.id),
        "user_id": str(member.id),
        "username": _safe_member_username(member),
        "display_name": _safe_member_display_name(member),
        "nickname": _safe_member_nickname(member),
        "avatar_url": _safe_member_avatar_url(member),
        "avatar_hash": _safe_member_avatar_hash(member),
        "role_ids": snap["role_ids"],
        "role_names": snap["role_names"],
        "roles": snap["roles"],
        "top_role": snap["top_role"],
        "highest_role_id": snap["highest_role_id"],
        "highest_role_name": snap["highest_role_name"],
        "has_any_role": snap["has_any_role"],
        "has_unverified": snap["has_unverified"],
        "has_verified_role": snap["has_verified_role"],
        "has_staff_role": snap["has_staff_role"],
        "has_secondary_verified_role": snap["has_secondary_verified_role"],
        "has_cosmetic_only": snap["has_cosmetic_only"],
        "role_state": snap["role_state"],
        "role_state_reason": snap["role_state_reason"],
        "data_health": "ok" if in_guild else "left_guild",
        "in_guild": bool(in_guild),
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "synced_at": now_iso,
        "updated_at": now_iso,
        "is_bot": bool(getattr(member, "bot", False)),
    }


def _is_missing_column_error(exc: Exception, column_name: str) -> bool:
    try:
        text = repr(exc)
        text_l = text.lower()
        col_l = str(column_name).lower()
        return (
            col_l in text_l
            and (
                "pgrst204" in text_l
                or "schema cache" in text_l
                or "column" in text_l
                or "does not exist" in text_l
            )
        )
    except Exception:
        return False


def _strip_optional_unsupported_columns(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for col, supported in _OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT.items():
        if supported is False:
            out.pop(col, None)
    return out


def _strip_optional_columns(payload: Dict[str, Any], columns: Sequence[str]) -> Dict[str, Any]:
    out = dict(payload or {})
    for col in columns:
        out.pop(col, None)
    return out


def _strip_voice_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for key in (
        "in_voice",
        "voice_channel_id",
        "voice_channel_name",
        "voice_muted",
        "voice_deafened",
        "voice_self_muted",
        "voice_self_deafened",
        "voice_streaming",
        "voice_video",
        "voice_suppressed",
    ):
        out.pop(key, None)
    return out


def _detect_unsupported_optional_columns(exc: Exception, payload: Dict[str, Any]) -> List[str]:
    removed: List[str] = []
    for col in list(payload.keys()):
        if col not in _OPTIONAL_GUILD_MEMBER_COLUMNS:
            continue
        if _is_missing_column_error(exc, col):
            _OPTIONAL_GUILD_MEMBER_COLUMN_SUPPORT[col] = False
            removed.append(col)
    return removed


# ============================================================
# Supabase / PostgREST wrappers
# ============================================================

def _guild_members_select_existing_sync(
    sb: Any,
    guild_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    try:
        res = (
            sb.table(GUILD_MEMBERS_TABLE)
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return None
    return None


async def _sync_get_existing_member_row_async(
    sb: Any,
    guild_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    return await _run_blocking_db(
        _guild_members_select_existing_sync,
        sb,
        guild_id,
        user_id,
    )


def _guild_members_upsert_sync(
    sb: Any,
    payload: Dict[str, Any],
    on_conflict: bool = True,
):
    if on_conflict:
        return sb.table(GUILD_MEMBERS_TABLE).upsert(
            payload,
            on_conflict="guild_id,user_id",
        ).execute()
    return sb.table(GUILD_MEMBERS_TABLE).upsert(payload).execute()


async def _guild_members_upsert_async(
    sb: Any,
    payload: Dict[str, Any],
    on_conflict: bool = True,
):
    return await _run_blocking_db(
        _guild_members_upsert_sync,
        sb,
        payload,
        on_conflict,
    )


def _guild_members_update_member_sync(
    sb: Any,
    guild_id: str,
    user_id: str,
    payload: Dict[str, Any],
):
    return (
        sb.table(GUILD_MEMBERS_TABLE)
        .update(payload)
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
        .execute()
    )


async def _guild_members_update_member_async(
    sb: Any,
    guild_id: str,
    user_id: str,
    payload: Dict[str, Any],
):
    return await _run_blocking_db(
        _guild_members_update_member_sync,
        sb,
        guild_id,
        user_id,
        payload,
    )


def _guild_members_select_guild_rows_sync(sb: Any, guild_id: str):
    return (
        sb.table(GUILD_MEMBERS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .execute()
    )


async def _guild_members_select_guild_rows_async(sb: Any, guild_id: str):
    return await _run_blocking_db(
        _guild_members_select_guild_rows_sync,
        sb,
        guild_id,
    )


def _member_joins_select_latest_sync(
    sb: Any,
    guild_id: str,
    user_id: str,
):
    return (
        sb.table(MEMBER_JOINS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
        .order("joined_at", desc=True)
        .limit(1)
        .execute()
    )


async def _member_joins_select_latest_async(
    sb: Any,
    guild_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    try:
        res = await _run_blocking_db(
            _member_joins_select_latest_sync,
            sb,
            guild_id,
            user_id,
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return None
    return None


def _member_joins_update_row_sync(
    sb: Any,
    join_id: Any,
    payload: Dict[str, Any],
):
    return (
        sb.table(MEMBER_JOINS_TABLE)
        .update(payload)
        .eq("id", join_id)
        .execute()
    )


async def _member_joins_update_row_async(
    sb: Any,
    join_id: Any,
    payload: Dict[str, Any],
):
    return await _run_blocking_db(
        _member_joins_update_row_sync,
        sb,
        join_id,
        payload,
    )


def _tickets_select_latest_for_member_sync(
    sb: Any,
    guild_id: str,
    user_id: str,
):
    return (
        sb.table(TICKETS_TABLE)
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )


async def _tickets_select_latest_for_member_async(
    sb: Any,
    guild_id: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    try:
        res = await _run_blocking_db(
            _tickets_select_latest_for_member_sync,
            sb,
            guild_id,
            user_id,
        )
        rows = getattr(res, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(dict(row))
        return out
    except Exception:
        return []


# ============================================================
# Entry / source enrichment
# ============================================================

def _ticket_looks_like_verification(ticket_row: Dict[str, Any]) -> bool:
    try:
        category = _safe_str(ticket_row.get("category")).strip().lower()
        title = _safe_str(ticket_row.get("title")).strip().lower()
        source = _safe_str(ticket_row.get("source")).strip().lower()

        verification_markers = {
            "verification",
            "verification_issue",
            "verification-issue",
            "verify",
            "id_verify",
            "id-verify",
        }

        if category in verification_markers:
            return True
        if "verification" in title or "verify" in title:
            return True
        if "verification" in source or "verify" in source:
            return True
    except Exception:
        pass
    return False


def _pick_latest_verification_ticket_id(ticket_rows: List[Dict[str, Any]]) -> Optional[str]:
    for row in ticket_rows:
        try:
            if _ticket_looks_like_verification(row):
                ticket_id = _coalesce_str(row.get("id"), row.get("channel_id"), row.get("discord_thread_id"))
                if ticket_id:
                    return ticket_id
        except Exception:
            continue
    return None


def _pick_latest_source_ticket_id(ticket_rows: List[Dict[str, Any]]) -> Optional[str]:
    for row in ticket_rows:
        try:
            ticket_id = _coalesce_str(row.get("id"), row.get("channel_id"), row.get("discord_thread_id"))
            if ticket_id:
                return ticket_id
        except Exception:
            continue
    return None


def _infer_entry_method(
    *,
    latest_join: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
    latest_ticket_rows: List[Dict[str, Any]],
) -> Optional[str]:
    explicit = _coalesce_str(
        (latest_join or {}).get("entry_method"),
        existing.get("entry_method"),
        (latest_join or {}).get("join_source"),
        (latest_join or {}).get("verification_source"),
        existing.get("join_source"),
        existing.get("verification_source"),
    )
    if explicit:
        return explicit

    invite_code = _coalesce_str((latest_join or {}).get("invite_code"), existing.get("invite_code"))
    invited_by = _coalesce_str((latest_join or {}).get("invited_by"), existing.get("invited_by"))
    vouched_by = _coalesce_str((latest_join or {}).get("vouched_by"), existing.get("vouched_by"))

    if vouched_by:
        return "vouched"
    if invite_code or invited_by:
        if _safe_bool((latest_join or {}).get("vanity_used"), False):
            return "vanity_invite"
        return "invite"
    if _pick_latest_verification_ticket_id(latest_ticket_rows):
        return "manual_verification"

    return None


def _infer_verification_source(
    *,
    latest_join: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
    latest_ticket_rows: List[Dict[str, Any]],
    entry_method: Optional[str],
) -> Optional[str]:
    explicit = _coalesce_str(
        (latest_join or {}).get("verification_source"),
        existing.get("verification_source"),
    )
    if explicit:
        return explicit

    if _pick_latest_verification_ticket_id(latest_ticket_rows):
        return "ticket_verification"
    if entry_method == "vouched":
        return "vouch_join"
    if entry_method == "vanity_invite":
        return "vanity_invite"
    if entry_method == "invite":
        return "invite_join"
    return None


def _infer_join_source(
    *,
    latest_join: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
    entry_method: Optional[str],
    verification_source: Optional[str],
) -> Optional[str]:
    return _coalesce_str(
        (latest_join or {}).get("join_source"),
        existing.get("join_source"),
        verification_source,
        entry_method,
    )


def _entry_truth_quality_from_meta(
    *,
    entry_method: Optional[str],
    invite_code: Optional[str],
    invited_by: Optional[str],
    latest_join: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
) -> Dict[str, Any]:
    explicit_quality = _coalesce_str(
        (latest_join or {}).get("entry_truth_quality"),
        existing.get("entry_truth_quality"),
    )
    explicit_reason = _coalesce_str(
        (latest_join or {}).get("entry_quality_reason"),
        existing.get("entry_quality_reason"),
    )

    try:
        explicit_confidence = (latest_join or {}).get("entry_confidence")
        if explicit_confidence is None:
            explicit_confidence = existing.get("entry_confidence")
        if explicit_confidence is not None:
            explicit_confidence_int = max(0, min(100, int(explicit_confidence)))
        else:
            explicit_confidence_int = None
    except Exception:
        explicit_confidence_int = None

    if explicit_quality:
        return {
            "entry_truth_quality": explicit_quality,
            "entry_confidence": explicit_confidence_int if explicit_confidence_int is not None else 50,
            "entry_quality_reason": explicit_reason or "Entry truth quality was already recorded.",
            "entry_conflict": bool((latest_join or {}).get("entry_conflict") or existing.get("entry_conflict") or False),
        }

    method = _safe_str(entry_method).strip().lower()
    if method == "invite" and (invite_code or invited_by):
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 95,
            "entry_quality_reason": "Invite usage delta identified a specific invite.",
            "entry_conflict": False,
        }
    if method == "vanity_invite":
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 90,
            "entry_quality_reason": "Vanity invite usage increased.",
            "entry_conflict": False,
        }
    if method in {"vouched", "manual_verification", "ticket_verification"}:
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 85,
            "entry_quality_reason": "Entry source came from an explicit staff/ticket action.",
            "entry_conflict": False,
        }
    if method == "invite_tracking_unavailable":
        return {
            "entry_truth_quality": "unknown",
            "entry_confidence": 15,
            "entry_quality_reason": "Invite tracking was unavailable due to permissions or API failure.",
            "entry_conflict": False,
        }
    if method == "invite_cache_warming":
        return {
            "entry_truth_quality": "partial",
            "entry_confidence": 35,
            "entry_quality_reason": "Invite cache was still warming; attribution should not be trusted as exact.",
            "entry_conflict": False,
        }
    if method == "invite_unresolved":
        return {
            "entry_truth_quality": "partial",
            "entry_confidence": 45,
            "entry_quality_reason": "Invite cache existed, but the usage delta did not identify one invite.",
            "entry_conflict": False,
        }

    return {
        "entry_truth_quality": "unknown",
        "entry_confidence": 20,
        "entry_quality_reason": "Join attribution is unknown.",
        "entry_conflict": False,
    }


def _entry_metadata_from_existing_join_and_tickets(
    *,
    existing: Dict[str, Any],
    latest_join: Optional[Dict[str, Any]],
    latest_ticket_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    entry_method = _infer_entry_method(
        latest_join=latest_join,
        existing=existing,
        latest_ticket_rows=latest_ticket_rows,
    )
    verification_source = _infer_verification_source(
        latest_join=latest_join,
        existing=existing,
        latest_ticket_rows=latest_ticket_rows,
        entry_method=entry_method,
    )
    join_source = _infer_join_source(
        latest_join=latest_join,
        existing=existing,
        entry_method=entry_method,
        verification_source=verification_source,
    )

    verification_ticket_id = _coalesce_str(
        (latest_join or {}).get("verification_ticket_id"),
        existing.get("verification_ticket_id"),
        _pick_latest_verification_ticket_id(latest_ticket_rows),
        (latest_join or {}).get("source_ticket_id"),
    )

    source_ticket_id = _coalesce_str(
        (latest_join or {}).get("source_ticket_id"),
        existing.get("source_ticket_id"),
        _pick_latest_source_ticket_id(latest_ticket_rows),
    )

    approval_reason = _coalesce_str(
        existing.get("approval_reason"),
        (latest_join or {}).get("approval_reason"),
        (latest_join or {}).get("join_note") if _coalesce_str((latest_join or {}).get("approved_by")) else None,
    )

    entry_reason = _coalesce_str(
        (latest_join or {}).get("join_note"),
        existing.get("entry_reason"),
    )

    invited_by = _coalesce_str(
        (latest_join or {}).get("invited_by"),
        existing.get("invited_by"),
    )
    invite_code = _coalesce_str(
        (latest_join or {}).get("invite_code"),
        existing.get("invite_code"),
    )
    truth_meta = _entry_truth_quality_from_meta(
        entry_method=entry_method,
        invite_code=invite_code,
        invited_by=invited_by,
        latest_join=latest_join,
        existing=existing,
    )

    return {
        "invited_by": invited_by,
        "invited_by_name": _coalesce_str(
            (latest_join or {}).get("invited_by_name"),
            existing.get("invited_by_name"),
        ),
        "invite_code": invite_code,
        "vouched_by": _coalesce_str(
            (latest_join or {}).get("vouched_by"),
            existing.get("vouched_by"),
        ),
        "vouched_by_name": _coalesce_str(
            (latest_join or {}).get("vouched_by_name"),
            existing.get("vouched_by_name"),
        ),
        "approved_by": _coalesce_str(
            (latest_join or {}).get("approved_by"),
            existing.get("approved_by"),
        ),
        "approved_by_name": _coalesce_str(
            (latest_join or {}).get("approved_by_name"),
            existing.get("approved_by_name"),
        ),
        "verification_ticket_id": verification_ticket_id,
        "source_ticket_id": source_ticket_id,
        "entry_method": entry_method,
        "verification_source": verification_source,
        "join_source": join_source,
        "vanity_used": _safe_bool(
            (latest_join or {}).get("vanity_used"),
            _safe_bool(existing.get("vanity_used"), False),
        ),
        "entry_reason": entry_reason,
        "approval_reason": approval_reason,
        **truth_meta,
    }


async def _best_effort_update_latest_join_row(
    sb: Any,
    latest_join: Optional[Dict[str, Any]],
    entry_meta: Dict[str, Any],
    now_iso: str,
) -> None:
    try:
        if not isinstance(latest_join, dict):
            return
        join_id = latest_join.get("id")
        if join_id is None:
            return

        payload: Dict[str, Any] = {
            "updated_at": now_iso,
        }

        for key in (
            "invited_by",
            "invited_by_name",
            "invite_code",
            "vouched_by",
            "vouched_by_name",
            "approved_by",
            "approved_by_name",
            "source_ticket_id",
            "entry_method",
            "verification_source",
            "join_source",
            "entry_truth_quality",
            "entry_confidence",
            "entry_quality_reason",
            "entry_conflict",
        ):
            value = entry_meta.get(key)
            if value is not None:
                payload[key] = value

        if "vanity_used" in entry_meta:
            payload["vanity_used"] = bool(entry_meta.get("vanity_used"))

        if entry_meta.get("approval_reason") and not _coalesce_str(latest_join.get("join_note")):
            payload["join_note"] = entry_meta.get("approval_reason")

        await _member_joins_update_row_async(sb, join_id, payload)
    except Exception:
        pass


# ============================================================
# Safe write helpers with schema fallback
# ============================================================

async def _guild_members_upsert_safe_async(
    sb: Any,
    payload: Dict[str, Any],
) -> None:
    current = _strip_optional_unsupported_columns(payload)

    try:
        try:
            await _guild_members_upsert_async(sb, current, on_conflict=True)
            return
        except TypeError:
            await _guild_members_upsert_async(sb, current, on_conflict=False)
            return
    except Exception as e:
        removed = _detect_unsupported_optional_columns(e, current)
        if not removed:
            raise

    retry_payload = _strip_optional_columns(current, removed)
    try:
        try:
            await _guild_members_upsert_async(sb, retry_payload, on_conflict=True)
            return
        except TypeError:
            await _guild_members_upsert_async(sb, retry_payload, on_conflict=False)
            return
    except Exception:
        raise


async def _guild_members_update_safe_async(
    sb: Any,
    guild_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> None:
    current = _strip_optional_unsupported_columns(payload)

    try:
        await _guild_members_update_member_async(sb, guild_id, user_id, current)
        return
    except Exception as e:
        removed = _detect_unsupported_optional_columns(e, current)
        if not removed:
            raise

    retry_payload = _strip_optional_columns(current, removed)
    await _guild_members_update_member_async(sb, guild_id, user_id, retry_payload)


# ============================================================
# Member sync persistence
# ============================================================

async def sync_member_to_supabase(
    member: discord.Member,
    in_guild: bool = True,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        sb = get_supabase()
        if not sb:
            return

        guild_id = str(member.guild.id)
        user_id = str(member.id)

        existing = await _sync_get_existing_member_row_async(sb, guild_id, user_id) or {}
        latest_join = await _member_joins_select_latest_async(sb, guild_id, user_id)
        latest_ticket_rows = await _tickets_select_latest_for_member_async(sb, guild_id, user_id)

        username = _safe_member_username(member)
        display_name = _safe_member_display_name(member)
        nickname = _safe_member_nickname(member)
        avatar_url = _safe_member_avatar_url(member)
        avatar_hash = _safe_member_avatar_hash(member)
        now_iso = _sync_iso_now()

        snap = _member_role_snapshot(member)
        voice = _member_voice_snapshot(member)

        previous_usernames = _append_unique_history(
            existing.get("previous_usernames"),
            str(existing.get("last_seen_username") or existing.get("username") or ""),
        )
        previous_display_names = _append_unique_history(
            existing.get("previous_display_names"),
            str(existing.get("last_seen_display_name") or existing.get("display_name") or ""),
        )
        previous_nicknames = _append_unique_history(
            existing.get("previous_nicknames"),
            str(existing.get("last_seen_nickname") or existing.get("nickname") or ""),
        )

        old_username = str(existing.get("username") or "").strip()
        old_display = str(existing.get("display_name") or "").strip()
        old_nick = str(existing.get("nickname") or "").strip()

        if old_username and old_username != username:
            previous_usernames = _append_unique_history(previous_usernames, old_username)
        if old_display and old_display != display_name:
            previous_display_names = _append_unique_history(previous_display_names, old_display)
        if old_nick and old_nick != nickname:
            previous_nicknames = _append_unique_history(previous_nicknames, old_nick)

        times_joined = int(existing.get("times_joined") or 0)
        times_left = int(existing.get("times_left") or 0)
        rejoined_at = existing.get("rejoined_at")
        left_at = existing.get("left_at")
        was_in_guild = existing.get("in_guild")

        if existing:
            if was_in_guild is False and in_guild:
                times_joined = max(1, times_joined) + 1
                rejoined_at = now_iso
                left_at = None
            elif times_joined <= 0:
                times_joined = 1
        else:
            times_joined = 1

        joined_at_value = (
            _utc_iso(member.joined_at)
            or _coalesce_str((latest_join or {}).get("joined_at"))
            or _coalesce_str(existing.get("joined_at"))
        )

        entry_meta = _entry_metadata_from_existing_join_and_tickets(
            existing=existing,
            latest_join=latest_join,
            latest_ticket_rows=latest_ticket_rows,
        )

        merged_risk_payload = (
            _build_risk_payload_from_profile(risk_profile, now_iso=now_iso)
            if isinstance(risk_profile, dict)
            else {key: existing.get(key) for key in (
                "risk_score",
                "risk_level",
                "risk_reasons",
                "fingerprint",
                "alt_cluster_key",
                "alt_cluster_size",
                "burst_join_count",
                "same_fingerprint_count",
                "similar_name_count",
                "same_age_bucket_count",
                "suspicious_name_pattern",
                "repeated_char_pattern",
                "default_avatar",
                "account_age_days",
                "age_bucket",
                "digit_ratio",
                "underscore_ratio",
                "cluster_members",
                "suspicion_flags",
                "risk_last_evaluated_at",
                "last_join_risk_score",
                "last_join_risk_level",
                "last_join_fingerprint",
                "alt_notes",
            ) if key in existing}
        )

        await _best_effort_update_latest_join_row(
            sb,
            latest_join,
            entry_meta,
            now_iso,
        )

        full_payload = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "nickname": nickname,
            "avatar_url": avatar_url or existing.get("avatar_url") or None,
            "avatar_hash": avatar_hash or existing.get("avatar_hash") or None,
            "role_ids": snap["role_ids"],
            "role_names": snap["role_names"],
            "roles": snap["roles"],
            "top_role": snap["top_role"],
            "highest_role_id": snap["highest_role_id"],
            "highest_role_name": snap["highest_role_name"],
            "has_any_role": snap["has_any_role"],
            "has_unverified": snap["has_unverified"],
            "has_verified_role": snap["has_verified_role"],
            "has_staff_role": snap["has_staff_role"],
            "has_secondary_verified_role": snap["has_secondary_verified_role"],
            "has_cosmetic_only": snap["has_cosmetic_only"],
            "role_state": snap["role_state"],
            "role_state_reason": snap["role_state_reason"],
            "in_voice": voice["in_voice"],
            "voice_channel_id": voice["voice_channel_id"],
            "voice_channel_name": voice["voice_channel_name"],
            "voice_muted": voice["voice_muted"],
            "voice_deafened": voice["voice_deafened"],
            "voice_self_muted": voice["voice_self_muted"],
            "voice_self_deafened": voice["voice_self_deafened"],
            "voice_streaming": voice["voice_streaming"],
            "voice_video": voice["voice_video"],
            "voice_suppressed": voice["voice_suppressed"],
            "data_health": "ok" if in_guild else "left_guild",
            "in_guild": bool(in_guild),
            "joined_at": joined_at_value,
            "synced_at": now_iso,
            "created_at": existing.get("created_at") or now_iso,
            "updated_at": now_iso,
            "first_seen_at": existing.get("first_seen_at") or now_iso,
            "last_seen_at": now_iso,
            "left_at": left_at,
            "rejoined_at": rejoined_at,
            "times_joined": times_joined,
            "times_left": times_left,
            "last_seen_username": username,
            "last_seen_display_name": display_name,
            "last_seen_nickname": nickname,
            "previous_usernames": previous_usernames,
            "previous_display_names": previous_display_names,
            "previous_nicknames": previous_nicknames,
            "is_bot": bool(getattr(member, "bot", False)),
            "invited_by": entry_meta["invited_by"],
            "invited_by_name": entry_meta["invited_by_name"],
            "invite_code": entry_meta["invite_code"],
            "vouched_by": entry_meta["vouched_by"],
            "vouched_by_name": entry_meta["vouched_by_name"],
            "approved_by": entry_meta["approved_by"],
            "approved_by_name": entry_meta["approved_by_name"],
            "verification_ticket_id": entry_meta["verification_ticket_id"],
            "source_ticket_id": entry_meta["source_ticket_id"],
            "entry_method": entry_meta["entry_method"],
            "verification_source": entry_meta["verification_source"],
            "join_source": entry_meta["join_source"],
            "vanity_used": bool(entry_meta["vanity_used"]),
            "entry_reason": entry_meta["entry_reason"],
            "approval_reason": entry_meta["approval_reason"],
            "entry_truth_quality": entry_meta.get("entry_truth_quality"),
            "entry_confidence": entry_meta.get("entry_confidence"),
            "entry_quality_reason": entry_meta.get("entry_quality_reason"),
            "entry_conflict": bool(entry_meta.get("entry_conflict", False)),
            **merged_risk_payload,
        }

        try:
            await _guild_members_upsert_safe_async(sb, full_payload)
            return
        except Exception:
            pass

        fallback_payload = _strip_voice_fields(full_payload)

        try:
            await _guild_members_upsert_safe_async(sb, fallback_payload)
            return
        except Exception:
            pass

        minimal = _minimal_member_payload(member, in_guild=in_guild)
        minimal = _strip_voice_fields(minimal)

        try:
            await _guild_members_upsert_safe_async(sb, minimal)
        except Exception as e:
            print("⚠️ members_new.sync_service.sync_member_to_supabase final fallback error:", repr(e))

    except Exception as e:
        print("⚠️ members_new.sync_service.sync_member_to_supabase error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


async def mark_member_left(member: discord.Member) -> None:
    try:
        sb = get_supabase()
        if not sb:
            return

        guild_id = str(member.guild.id)
        user_id = str(member.id)
        existing = await _sync_get_existing_member_row_async(sb, guild_id, user_id) or {}

        now_iso = _sync_iso_now()

        username = _safe_member_username(member)
        display_name = _safe_member_display_name(member)
        nickname = _safe_member_nickname(member)
        avatar_url = _safe_member_avatar_url(member)
        avatar_hash = _safe_member_avatar_hash(member)

        previous_usernames = _append_unique_history(
            existing.get("previous_usernames"),
            str(existing.get("last_seen_username") or existing.get("username") or ""),
        )
        previous_display_names = _append_unique_history(
            existing.get("previous_display_names"),
            str(existing.get("last_seen_display_name") or existing.get("display_name") or ""),
        )
        previous_nicknames = _append_unique_history(
            existing.get("previous_nicknames"),
            str(existing.get("last_seen_nickname") or existing.get("nickname") or ""),
        )

        if existing.get("username") and str(existing.get("username")).strip() != username:
            previous_usernames = _append_unique_history(previous_usernames, str(existing.get("username")).strip())
        if existing.get("display_name") and str(existing.get("display_name")).strip() != display_name:
            previous_display_names = _append_unique_history(previous_display_names, str(existing.get("display_name")).strip())
        if existing.get("nickname") and str(existing.get("nickname")).strip() != nickname:
            previous_nicknames = _append_unique_history(previous_nicknames, str(existing.get("nickname")).strip())

        times_joined = int(existing.get("times_joined") or 0) or 1
        times_left = int(existing.get("times_left") or 0)
        if existing.get("in_guild") is not False:
            times_left += 1

        full_payload = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username or existing.get("username") or "",
            "display_name": display_name or existing.get("display_name") or "",
            "nickname": nickname or existing.get("nickname") or "",
            "avatar_url": avatar_url or existing.get("avatar_url") or None,
            "avatar_hash": avatar_hash or existing.get("avatar_hash") or None,
            "in_guild": False,
            "data_health": "left_guild",
            "synced_at": now_iso,
            "updated_at": now_iso,
            "last_seen_at": now_iso,
            "left_at": now_iso,
            "times_joined": times_joined,
            "times_left": times_left,
            "last_seen_username": username or existing.get("last_seen_username") or existing.get("username") or "",
            "last_seen_display_name": display_name or existing.get("last_seen_display_name") or existing.get("display_name") or "",
            "last_seen_nickname": nickname or existing.get("last_seen_nickname") or existing.get("nickname") or "",
            "previous_usernames": previous_usernames,
            "previous_display_names": previous_display_names,
            "previous_nicknames": previous_nicknames,
            "role_ids": existing.get("role_ids") or [],
            "role_names": existing.get("role_names") or [],
            "roles": existing.get("roles") or [],
            "top_role": existing.get("top_role"),
            "highest_role_id": existing.get("highest_role_id"),
            "highest_role_name": existing.get("highest_role_name"),
            "has_any_role": existing.get("has_any_role") or False,
            "has_unverified": existing.get("has_unverified") or False,
            "has_verified_role": existing.get("has_verified_role") or False,
            "has_staff_role": existing.get("has_staff_role") or False,
            "has_secondary_verified_role": existing.get("has_secondary_verified_role") or False,
            "has_cosmetic_only": existing.get("has_cosmetic_only") or False,
            "in_voice": False,
            "voice_channel_id": None,
            "voice_channel_name": None,
            "voice_muted": False,
            "voice_deafened": False,
            "voice_self_muted": False,
            "voice_self_deafened": False,
            "voice_streaming": False,
            "voice_video": False,
            "voice_suppressed": False,
            "role_state": "left_guild",
            "role_state_reason": "Member left or was removed from guild.",
            "is_bot": bool(getattr(member, "bot", False)),
            "invited_by": existing.get("invited_by"),
            "invited_by_name": existing.get("invited_by_name"),
            "invite_code": existing.get("invite_code"),
            "vouched_by": existing.get("vouched_by"),
            "vouched_by_name": existing.get("vouched_by_name"),
            "approved_by": existing.get("approved_by"),
            "approved_by_name": existing.get("approved_by_name"),
            "verification_ticket_id": existing.get("verification_ticket_id"),
            "source_ticket_id": existing.get("source_ticket_id"),
            "entry_method": existing.get("entry_method"),
            "verification_source": existing.get("verification_source"),
            "join_source": existing.get("join_source"),
            "vanity_used": _safe_bool(existing.get("vanity_used"), False),
            "entry_reason": existing.get("entry_reason"),
            "approval_reason": existing.get("approval_reason"),
        }

        try:
            await _guild_members_upsert_safe_async(sb, full_payload)
            return
        except Exception:
            pass

        fallback_payload = _strip_voice_fields(full_payload)

        try:
            await _guild_members_upsert_safe_async(sb, fallback_payload)
            return
        except Exception:
            pass

        try:
            await _guild_members_update_safe_async(
                sb,
                guild_id,
                user_id,
                {
                    "in_guild": False,
                    "data_health": "left_guild",
                    "synced_at": now_iso,
                    "updated_at": now_iso,
                    "left_at": now_iso,
                    "times_left": times_left,
                    "role_state": "left_guild",
                    "role_state_reason": "Member left or was removed from guild.",
                    "join_source": existing.get("join_source"),
                    "vanity_used": _safe_bool(existing.get("vanity_used"), False),
                    "in_voice": False,
                    "voice_channel_id": None,
                    "voice_channel_name": None,
                    "voice_muted": False,
                    "voice_deafened": False,
                    "voice_self_muted": False,
                    "voice_self_deafened": False,
                    "voice_streaming": False,
                    "voice_video": False,
                    "voice_suppressed": False,
                },
            )
        except Exception as e2:
            print("⚠️ members_new.sync_service.mark_member_left fallback error:", repr(e2))

    except Exception as e:
        print("⚠️ members_new.sync_service.mark_member_left error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


# ============================================================
# Full sync / reconciliation entrypoints
# ============================================================

async def _bulk_mark_departed_members_async(
    sb: Any,
    guild_id: str,
    active_ids: set[str],
) -> int:
    try:
        res = await _guild_members_select_guild_rows_async(sb, guild_id)
        rows = getattr(res, "data", None) or []
    except Exception:
        rows = []

    marked = 0
    now_iso = _sync_iso_now()

    for row in rows:
        try:
            if not isinstance(row, dict):
                continue

            uid = str(row.get("user_id") or "")
            if not uid or uid in active_ids:
                continue

            payload = {
                "in_guild": False,
                "data_health": "left_guild",
                "synced_at": now_iso,
                "updated_at": now_iso,
                "role_state": "left_guild",
                "role_state_reason": "Member left or was removed from guild.",
                "in_voice": False,
                "voice_channel_id": None,
                "voice_channel_name": None,
                "voice_muted": False,
                "voice_deafened": False,
                "voice_self_muted": False,
                "voice_self_deafened": False,
                "voice_streaming": False,
                "voice_video": False,
                "voice_suppressed": False,
            }

            if row.get("in_guild") is not False:
                payload["left_at"] = now_iso
                payload["times_left"] = int(row.get("times_left") or 0) + 1

            try:
                await _guild_members_update_safe_async(sb, str(guild_id), uid, payload)
                marked += 1
            except Exception:
                continue
        except Exception:
            continue

    return marked


async def run_full_member_sync_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        members = list(snapshot.members)
        summary["membership_source"] = snapshot.source
        summary["membership_authoritative"] = bool(snapshot.authoritative)
        if snapshot.error:
            summary["member_fetch_error"] = snapshot.error
        summary["checked"] = len(members)

        active_ids: set[str] = set()
        for idx, member in enumerate(members, start=1):
            try:
                member_id = int(getattr(member, "id", 0) or 0)
                if member_id <= 0:
                    summary["errors"] += 1
                    continue
                active_ids.add(str(member_id))
                await sync_member_to_supabase(member, in_guild=True)
                summary["active_members_synced"] += 1

                if idx % 10 == 0:
                    await asyncio.sleep(0)
            except Exception:
                summary["errors"] += 1
                continue

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] += 1
            summary["departure_reconciliation_skipped"] = True
            summary["departure_skip_reason"] = "authoritative_member_fetch_failed"
            print(
                "⚠️ Member departure reconciliation skipped during full sync: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        try:
            summary["marked_departed"] = await _bulk_mark_departed_members_async(
                sb,
                str(guild.id),
                active_ids,
            )
        except Exception as e:
            summary["errors"] += 1
            summary["departure_reconciliation_error"] = f"{type(e).__name__}: {str(e)[:350]}"

        return summary

    except Exception as e:
        summary["error"] = repr(e)
        summary["errors"] = max(1, int(summary.get("errors") or 0))
        print("⚠️ members_new.sync_service.run_full_member_sync_for_guild error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return summary


async def run_departed_reconciliation_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        active_ids = {str(user_id) for user_id in snapshot.active_user_ids}
        summary["membership_source"] = snapshot.source
        summary["membership_authoritative"] = bool(snapshot.authoritative)
        if snapshot.error:
            summary["member_fetch_error"] = snapshot.error
        summary["checked"] = len(active_ids)

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] = 1
            summary["departure_reconciliation_skipped"] = True
            summary["departure_skip_reason"] = "authoritative_member_fetch_failed"
            print(
                "⚠️ Departed-member reconciliation skipped: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        summary["marked_departed"] = await _bulk_mark_departed_members_async(
            sb,
            str(guild.id),
            active_ids,
        )
        return summary

    except Exception as e:
        summary["error"] = repr(e)
        summary["errors"] = 1
        print("⚠️ members_new.sync_service.run_departed_reconciliation_for_guild error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return summary


async def run_full_member_sync_for_all_guilds(bot_instance=bot) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "guilds": 0,
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
        "rows": [],
    }

    try:
        guilds = list(getattr(bot_instance, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            summary = await run_full_member_sync_for_guild(guild)
            out["guilds"] += 1
            out["checked"] += int(summary.get("checked") or 0)
            out["active_members_synced"] += int(summary.get("active_members_synced") or 0)
            out["marked_departed"] += int(summary.get("marked_departed") or 0)
            out["errors"] += int(summary.get("errors") or 0)
            out["rows"].append(summary)
        except Exception:
            out["errors"] += 1

    return out


async def run_departed_reconciliation_for_all_guilds(bot_instance=bot) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "guilds": 0,
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
        "rows": [],
    }

    try:
        guilds = list(getattr(bot_instance, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            summary = await run_departed_reconciliation_for_guild(guild)
            out["guilds"] += 1
            out["checked"] += int(summary.get("checked") or 0)
            out["marked_departed"] += int(summary.get("marked_departed") or 0)
            out["errors"] += int(summary.get("errors") or 0)
            out["rows"].append(summary)
        except Exception:
            out["errors"] += 1

    return out


__all__ = [
    "sync_member_to_supabase",
    "mark_member_left",
    "run_full_member_sync_for_guild",
    "run_departed_reconciliation_for_guild",
    "run_full_member_sync_for_all_guilds",
    "run_departed_reconciliation_for_all_guilds",
]
