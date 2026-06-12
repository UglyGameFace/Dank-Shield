# ============================================================
# File: stoney_verify/events.py
# ============================================================

from __future__ import annotations

import asyncio
import os
import traceback
from collections import deque
from datetime import timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

from .globals import *
from . import role_truth

# Split-out admin slash commands
from . import verify_admin_commands  # noqa: F401
from . import spam_guard  # noqa: F401

from .raidguard import (
    _account_age_days,
    _behavior_fingerprint,
    _age_bucket,
    _maybe_trigger_raid,
    _mass_role_strip_if_needed,
    _post_raidlog,
    track_member_join_risk,
)

from .modlog import (
    _post_modlog,
    _get_modlog_channel,
    build_quick_mod_view,
    _audit_find_recent_ban,
    maybe_log_member_update_diff,
    maybe_log_recent_ban,
    maybe_log_recent_kick,
    maybe_log_voice_state_update,
)

from .vc_verify import _can_manage_channel, _get_vc_channel, vc_sweeper_loop

try:
    from .guild_config import get_guild_config, public_config_isolation_enabled
except Exception:
    get_guild_config = None  # type: ignore

    def public_config_isolation_enabled() -> bool:  # type: ignore
        return True

try:
    from . import vc_sessions
except Exception:
    vc_sessions = None  # type: ignore

try:
    from .tickets_new.service import (
        find_open_ticket_for_owner,
        mark_ticket_closed as tickets_mark_ticket_closed,
        mark_ticket_deleted as tickets_mark_ticket_deleted,
    )
except Exception:
    find_open_ticket_for_owner = None  # type: ignore
    tickets_mark_ticket_closed = None  # type: ignore
    tickets_mark_ticket_deleted = None  # type: ignore

try:
    from .channel_cleanup import ensure_channel_cleanup_worker_started
except Exception:
    async def ensure_channel_cleanup_worker_started() -> bool:
        return False

# Timer helpers from commands.py
try:
    from .commands import (
        start_join_grace_then_kick_timer_for_member,
        cancel_verification_wait_timers_for_member,
    )
except Exception:
    async def start_join_grace_then_kick_timer_for_member(
        member: discord.Member,
        source_channel: Optional[discord.TextChannel] = None,
        grace_minutes: Optional[int] = None,
    ) -> bool:
        return False

    async def cancel_verification_wait_timers_for_member(guild_id: int, user_id: int) -> bool:
        return False

try:
    from .members_new.sync_service import (
        sync_member_to_supabase as new_sync_member_to_supabase,
        mark_member_left as new_mark_member_left,
        run_full_member_sync_for_guild as new_run_full_member_sync_for_guild,
        run_departed_reconciliation_for_guild as new_run_departed_reconciliation_for_guild,
    )
except Exception:
    new_sync_member_to_supabase = None  # type: ignore
    new_mark_member_left = None  # type: ignore
    new_run_full_member_sync_for_guild = None  # type: ignore
    new_run_departed_reconciliation_for_guild = None  # type: ignore


# ============================================================
# Internal helpers
# ============================================================

def _ensure_gid_dict_of_lists(container: Any, gid: int) -> None:
    try:
        if gid not in container or container.get(gid) is None:
            container[gid] = {}
        if not isinstance(container[gid], dict):
            container[gid] = {}
    except Exception:
        try:
            container[gid] = {}
        except Exception:
            pass


def _ensure_bucket_list(container: Any, gid: int, bucket: str) -> None:
    try:
        _ensure_gid_dict_of_lists(container, gid)
        if bucket not in container[gid] or container[gid].get(bucket) is None:
            container[gid][bucket] = []
        if not isinstance(container[gid][bucket], list):
            container[gid][bucket] = []
    except Exception:
        pass


def _ensure_gid_dict(container: Any, gid: int) -> None:
    try:
        if gid not in container or container.get(gid) is None:
            container[gid] = {}
        if not isinstance(container[gid], dict):
            container[gid] = {}
    except Exception:
        try:
            container[gid] = {}
        except Exception:
            pass


def _ensure_gid_join_deque(container: Any, gid: int) -> None:
    try:
        if gid not in container or container.get(gid) is None:
            container[gid] = deque()
            return

        current = container[gid]
        if isinstance(current, deque):
            return

        if isinstance(current, list):
            container[gid] = deque(current)
            return

        container[gid] = deque()
    except Exception:
        try:
            container[gid] = deque()
        except Exception:
            pass


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    return role_truth.member_has_role_id(member, role_id)


def _member_has_any_safe_access_role(member: discord.Member, *, include_unverified: bool = True) -> bool:
    return bool(
        role_truth.member_has_any_safe_access_role(
            member,
            include_unverified=include_unverified,
        )
    )


def _member_is_pending_verification(member: discord.Member) -> bool:
    return bool(role_truth.member_is_pending_verification(member))


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or isinstance(v, bool):
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(str(v).strip())
    except Exception:
        return default


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
        if _as_int(profile.get("same_age_bucket_count"), 0) >= 3 and age_bucket:
            return f"age:{age_bucket}"
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


def _sync_iso_now() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


def _build_risk_payload_from_profile(
    risk_profile: Optional[Dict[str, Any]],
    *,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(risk_profile, dict):
        return {}

    now_value = now_iso or _sync_iso_now()

    score = max(0, min(100, _as_int(risk_profile.get("score"), 0)))
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
        alt_cluster_size = 1 + max(
            same_fingerprint_count,
            similar_name_count,
            same_age_bucket_count,
        )

    reasons = _safe_string_list(risk_profile.get("reasons"), 12)
    suspicion_flags = _derive_suspicion_flags_from_profile(risk_profile)
    cluster_members = _safe_json_object_list(risk_profile.get("cluster_members"), 8)

    digit_ratio = round(_as_float(risk_profile.get("digit_ratio"), 0.0), 3)
    underscore_ratio = round(_as_float(risk_profile.get("underscore_ratio"), 0.0), 3)
    account_age_days = _as_int(risk_profile.get("account_age_days"), 0)
    age_bucket = str(risk_profile.get("age_bucket") or "").strip() or None

    suspicious_name_pattern = bool(risk_profile.get("suspicious_name_pattern"))
    repeated_char_pattern = bool(risk_profile.get("repeated_char_pattern"))
    default_avatar = bool(risk_profile.get("default_avatar"))

    return {
        "risk_score": score,
        "risk_level": level,
        "risk_reasons": reasons,
        "fingerprint": fingerprint,
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "burst_join_count": burst_join_count,
        "same_fingerprint_count": same_fingerprint_count,
        "similar_name_count": similar_name_count,
        "same_age_bucket_count": same_age_bucket_count,
        "suspicious_name_pattern": suspicious_name_pattern,
        "repeated_char_pattern": repeated_char_pattern,
        "default_avatar": default_avatar,
        "account_age_days": account_age_days,
        "age_bucket": age_bucket,
        "digit_ratio": digit_ratio,
        "underscore_ratio": underscore_ratio,
        "cluster_members": cluster_members,
        "suspicion_flags": suspicion_flags,
        "risk_last_evaluated_at": now_value,
        "last_join_risk_score": score,
        "last_join_risk_level": level,
        "last_join_fingerprint": fingerprint,
    }


def _extract_existing_risk_payload(existing: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(existing, dict):
        return {}

    out: Dict[str, Any] = {}
    for key in (
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
    ):
        if key in existing:
            out[key] = existing.get(key)
    return out


def _startup_task_running(attr_name: str) -> bool:
    try:
        task = getattr(bot, attr_name, None)
        if task and hasattr(task, "done") and not task.done():
            return True
    except Exception:
        pass
    return False


def _assign_startup_task(attr_name: str, coro) -> None:
    try:
        task = asyncio.create_task(coro)
        setattr(bot, attr_name, task)
    except Exception:
        pass


async def _new_sync_member_safe(
    member: discord.Member,
    *,
    in_guild: bool,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        if callable(new_sync_member_to_supabase):
            try:
                await new_sync_member_to_supabase(
                    member,
                    in_guild=in_guild,
                    risk_profile=risk_profile,
                )
            except TypeError:
                await new_sync_member_to_supabase(member, in_guild=in_guild)
            return
        print("⚠️ new_sync_member_to_supabase unavailable; member sync skipped")
    except Exception as e:
        print("⚠️ new_sync_member_to_supabase failed:", repr(e))


async def _new_mark_member_left_safe(member: discord.Member) -> None:
    try:
        if callable(new_mark_member_left):
            await new_mark_member_left(member)
            return
        print("⚠️ new_mark_member_left unavailable; member-left sync skipped")
    except Exception as e:
        print("⚠️ new_mark_member_left failed:", repr(e))


# ============================================================
# Async wrappers for blocking Supabase work
# ============================================================

async def _run_blocking_db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _tickets_select_open_verification_sync(sb: Any, guild_id: str):
    return (
        sb.table("tickets")
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("status", "open")
        .eq("category", "verification_issue")
        .execute()
    )


async def _tickets_select_open_verification_async(sb: Any, guild_id: str):
    return await _run_blocking_db(_tickets_select_open_verification_sync, sb, guild_id)


def _vc_sessions_select_active_sync(sb: Any, guild_id: int, vc_channel_id: int, statuses: List[str]):
    return (
        sb.table("vc_verify_sessions")
        .select("*")
        .eq("guild_id", int(guild_id))
        .eq("vc_channel_id", int(vc_channel_id))
        .in_("status", statuses)
        .limit(50)
        .execute()
    )


async def _vc_sessions_select_active_async(sb: Any, guild_id: int, vc_channel_id: int, statuses: List[str]):
    return await _run_blocking_db(_vc_sessions_select_active_sync, sb, guild_id, vc_channel_id, statuses)


def _guild_members_select_existing_sync(sb: Any, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            sb.table("guild_members")
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


async def _sync_get_existing_member_row_async(sb: Any, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    return await _run_blocking_db(_guild_members_select_existing_sync, sb, guild_id, user_id)


def _guild_members_upsert_sync(sb: Any, payload: Dict[str, Any], on_conflict: bool = True):
    if on_conflict:
        return sb.table("guild_members").upsert(payload, on_conflict="guild_id,user_id").execute()
    return sb.table("guild_members").upsert(payload).execute()


async def _guild_members_upsert_async(sb: Any, payload: Dict[str, Any], on_conflict: bool = True):
    return await _run_blocking_db(_guild_members_upsert_sync, sb, payload, on_conflict)


def _guild_members_update_member_sync(sb: Any, guild_id: str, user_id: str, payload: Dict[str, Any]):
    return (
        sb.table("guild_members")
        .update(payload)
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
        .execute()
    )


async def _guild_members_update_member_async(sb: Any, guild_id: str, user_id: str, payload: Dict[str, Any]):
    return await _run_blocking_db(_guild_members_update_member_sync, sb, guild_id, user_id, payload)


def _guild_members_select_guild_rows_sync(sb: Any, guild_id: str):
    return (
        sb.table("guild_members")
        .select("guild_id,user_id,in_guild")
        .eq("guild_id", str(guild_id))
        .execute()
    )


async def _guild_members_select_guild_rows_async(sb: Any, guild_id: str):
    return await _run_blocking_db(_guild_members_select_guild_rows_sync, sb, guild_id)


async def _bulk_mark_departed_members_async(sb: Any, guild_id: str, active_ids: set[str]) -> int:
    try:
        res = await _guild_members_select_guild_rows_async(sb, guild_id)
        rows = getattr(res, "data", None) or []
    except Exception:
        rows = []

    marked = 0

    for row in rows:
        try:
            uid = str(row.get("user_id") or "")
            if not uid or uid in active_ids:
                continue

            payload = {
                "in_guild": False,
                "data_health": "left_guild",
                "synced_at": _sync_iso_now(),
                "updated_at": _sync_iso_now(),
                "role_state": "left_guild",
                "role_state_reason": "Member left or was removed from guild.",
            }
            try:
                await _guild_members_update_member_async(sb, str(guild_id), uid, payload)
                marked += 1
            except Exception:
                continue
        except Exception:
            continue

    return marked


# ============================================================
# Verification ticket cleanup helpers
# ============================================================

async def _auto_close_verification_ticket_for_departed_member(
    member: discord.Member,
    *,
    leave_reason: str,
) -> None:
    from .tickets_new.departed_member_cleanup_service import close_verification_ticket_for_departed_member

    await close_verification_ticket_for_departed_member(member, leave_reason=leave_reason)


async def _reconcile_stale_open_verification_tickets() -> None:
    from .tickets_new.departed_member_cleanup_service import reconcile_stale_open_verification_tickets

    guilds = list(getattr(bot, "guilds", []) or [])
    await reconcile_stale_open_verification_tickets(guilds)


# ============================================================
# Dashboard / Supabase member sync helpers
# ============================================================

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


def _is_missing_column_error(exc: Exception, column_name: str) -> bool:
    try:
        text = repr(exc)
        return (
            "PGRST204" in text
            and "schema cache" in text
            and f"'{column_name}' column" in text
        )
    except Exception:
        return False


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


def _member_role_snapshot(member: discord.Member) -> Dict[str, Any]:
    return role_truth.build_member_role_snapshot(member)


def _minimal_member_payload(
    member: discord.Member,
    in_guild: bool = True,
    risk_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now_iso = _sync_iso_now()
    snap = _member_role_snapshot(member)
    voice = _member_voice_snapshot(member)

    payload = {
        "guild_id": str(member.guild.id),
        "user_id": str(member.id),
        "username": _safe_member_username(member),
        "display_name": _safe_member_display_name(member),
        "nickname": _safe_member_nickname(member),
        "avatar_url": _safe_member_avatar_url(member),
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
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "synced_at": now_iso,
        "updated_at": now_iso,
    }

    if isinstance(risk_payload, dict) and risk_payload:
        payload.update(risk_payload)

    return payload


async def _sync_member_to_supabase(
    member: discord.Member,
    in_guild: bool = True,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        if callable(new_sync_member_to_supabase):
            try:
                await new_sync_member_to_supabase(
                    member,
                    in_guild=in_guild,
                    risk_profile=risk_profile,
                )
            except TypeError:
                await new_sync_member_to_supabase(member, in_guild=in_guild)
            return
        print("⚠️ member sync service unavailable; member sync skipped")
    except Exception as e:
        print("⚠️ _sync_member_to_supabase service delegate failed:", repr(e))


async def _mark_member_left(member: discord.Member) -> None:
    try:
        if callable(new_mark_member_left):
            await new_mark_member_left(member)
            return
        print("⚠️ member-left sync service unavailable; member-left sync skipped")
    except Exception as e:
        print("⚠️ _mark_member_left service delegate failed:", repr(e))


async def _initial_member_sync_sweep() -> None:
    try:
        guilds = list(getattr(bot, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            if callable(new_run_full_member_sync_for_guild):
                try:
                    summary = await new_run_full_member_sync_for_guild(guild)
                    print(
                        f"✅ Initial member sync complete for guild {guild.id}: "
                        f"active={int(summary.get('active_members_synced') or 0)} "
                        f"marked_departed={int(summary.get('marked_departed') or 0)} "
                        f"errors={int(summary.get('errors') or 0)}"
                    )
                    continue
                except Exception as e:
                    print(f"⚠️ new full member sync failed for guild {guild.id}: {repr(e)}")

            sb = get_supabase()
            if not sb:
                continue

            active_ids: set[str] = set()

            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception:
                members = list(getattr(guild, "members", []) or [])

            for idx, member in enumerate(members, start=1):
                try:
                    active_ids.add(str(member.id))
                    await _new_sync_member_safe(member, in_guild=True)

                    if idx % 10 == 0:
                        await asyncio.sleep(0)
                except Exception:
                    continue

            marked_departed = await _bulk_mark_departed_members_async(sb, str(guild.id), active_ids)

            print(
                f"✅ Initial member sync complete for guild {guild.id}: "
                f"{len(active_ids)} active members, marked_departed={marked_departed}"
            )
        except Exception as e:
            print(f"⚠️ Initial member sync failed for guild {getattr(guild, 'id', 'unknown')}: {e}")


# ============================================================
# Invite cache + entry-path persistence helpers
# ============================================================

_INVITE_USES_CACHE: Dict[int, Dict[str, int]] = {}
_INVITE_META_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}
_VANITY_USES_CACHE: Dict[int, Optional[int]] = {}


def _invite_inviter_id(invite: discord.Invite) -> Optional[str]:
    try:
        inviter = getattr(invite, "inviter", None)
        if inviter and getattr(invite.inviter, "id", None):
            return str(invite.inviter.id)
    except Exception:
        pass
    return None


def _invite_inviter_name(invite: discord.Invite) -> Optional[str]:
    try:
        inviter = getattr(invite, "inviter", None)
        if inviter is not None:
            return str(inviter)
    except Exception:
        pass
    return None


def _invite_channel_id(invite: discord.Invite) -> Optional[str]:
    try:
        ch = getattr(invite, "channel", None)
        if ch and getattr(ch, "id", None):
            return str(ch.id)
    except Exception:
        pass
    return None


def _invite_channel_name(invite: discord.Invite) -> Optional[str]:
    try:
        ch = getattr(invite, "channel", None)
        if ch is not None:
            return str(getattr(ch, "name", "") or "")
    except Exception:
        pass
    return None


def _invite_meta(invite: discord.Invite) -> Dict[str, Any]:
    try:
        uses_raw = getattr(invite, "uses", 0)
        uses = int(uses_raw or 0)
    except Exception:
        uses = 0

    try:
        max_uses_raw = getattr(invite, "max_uses", 0)
        max_uses = int(max_uses_raw or 0)
    except Exception:
        max_uses = 0

    try:
        temporary = bool(getattr(invite, "temporary", False))
    except Exception:
        temporary = False

    return {
        "code": str(getattr(invite, "code", "") or "").strip(),
        "uses": uses,
        "max_uses": max_uses,
        "temporary": temporary,
        "inviter_id": _invite_inviter_id(invite),
        "inviter_name": _invite_inviter_name(invite),
        "channel_id": _invite_channel_id(invite),
        "channel_name": _invite_channel_name(invite),
    }


def _join_truth_quality(entry_method: str, *, invite_code: Optional[str] = None, invited_by: Optional[str] = None) -> Tuple[str, int, str]:
    from .members_new.join_context_service import join_truth_quality

    return join_truth_quality(entry_method, invite_code=invite_code, invited_by=invited_by)


def _build_join_context(
    *,
    entry_method: str,
    join_source: str,
    verification_source: str,
    invite_code: Optional[str] = None,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    entry_reason: Optional[str] = None,
    approval_reason: Optional[str] = None,
    join_note: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    vanity_used: bool = False,
    source_ticket_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .members_new.join_context_service import build_join_context

    return build_join_context(
        entry_method=entry_method,
        join_source=join_source,
        verification_source=verification_source,
        invite_code=invite_code,
        invited_by=invited_by,
        invited_by_name=invited_by_name,
        vouched_by=vouched_by,
        vouched_by_name=vouched_by_name,
        approved_by=approved_by,
        approved_by_name=approved_by_name,
        entry_reason=entry_reason,
        approval_reason=approval_reason,
        join_note=join_note,
        channel_id=channel_id,
        channel_name=channel_name,
        vanity_used=vanity_used,
        source_ticket_id=source_ticket_id,
    )


async def _refresh_guild_invite_cache(guild: discord.Guild) -> bool:
    from .members_new.join_context_service import warm_invite_cache_for_guild

    return await warm_invite_cache_for_guild(guild)


async def _warm_all_guild_invite_caches() -> None:
    try:
        guilds = list(getattr(bot, "guilds", []) or [])
    except Exception:
        guilds = []

    warmed = 0
    for guild in guilds:
        try:
            ok = await _refresh_guild_invite_cache(guild)
            if ok:
                warmed += 1
        except Exception as e:
            print(f"⚠️ [INVITES] warm cache failed guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")

    print(f"📨 Invite cache warm complete: guilds={len(guilds)} warmed={warmed}")


async def _detect_join_entry_context(member: discord.Member) -> Dict[str, Any]:
    from .members_new.join_context_service import detect_join_entry_context

    return await detect_join_entry_context(member)


async def _persist_member_join_context(
    member: discord.Member,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    from .members_new.join_context_service import persist_member_join_context

    await persist_member_join_context(member, risk_profile=risk_profile)


# ============================================================
# VC session helpers
# ============================================================

def _vc_meta_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        meta = row.get("meta") or {}
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _vc_owner_id_from_row(row: Dict[str, Any]) -> int:
    return _as_int(
        row.get("owner_id")
        or row.get("requester_id")
        or _vc_meta_dict(row).get("owner_id")
        or _vc_meta_dict(row).get("requester_id"),
        0,
    )


def _vc_staff_ids_from_row(row: Dict[str, Any]) -> List[int]:
    meta = _vc_meta_dict(row)
    raw_values = [
        row.get("staff_id"),
        row.get("accepted_by"),
        row.get("started_by"),
        row.get("claimed_by"),
        row.get("current_staff_id"),
        meta.get("staff_id"),
        meta.get("accepted_by"),
        meta.get("started_by"),
        meta.get("claimed_by"),
        meta.get("current_staff_id"),
        meta.get("takeover_staff_id"),
        meta.get("restart_staff_id"),
        meta.get("assigned_staff_id"),
    ]

    out: List[int] = []
    seen: set[int] = set()

    for value in raw_values:
        rid = _as_int(value, 0)
        if rid > 0 and rid not in seen:
            seen.add(rid)
            out.append(rid)

    return out


def _vc_row_token(row: Dict[str, Any]) -> str:
    try:
        return str(row.get("token") or "").strip()
    except Exception:
        return ""


def _vc_row_status(row: Dict[str, Any]) -> str:
    try:
        return str(row.get("status") or "").upper().strip()
    except Exception:
        return ""


def _member_in_target_voice(member: Optional[discord.Member], channel_id: int) -> bool:
    try:
        if member is None or channel_id <= 0:
            return False
        state = getattr(member, "voice", None)
        ch = getattr(state, "channel", None)
        return bool(ch and int(getattr(ch, "id", 0) or 0) == int(channel_id))
    except Exception:
        return False


async def _resolve_vc_verify_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    try:
        ch = _get_vc_channel(guild)
        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return ch
    except Exception:
        pass

    try:
        vc_id = _as_int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or globals().get("VC_VERIFY_VC_ID", 0), 0)
        if vc_id <= 0:
            return None
        fetched = await guild.fetch_channel(vc_id)
        if isinstance(fetched, (discord.VoiceChannel, discord.StageChannel)):
            return fetched
    except Exception:
        pass

    return None


async def _fetch_active_vc_session_rows(
    guild: discord.Guild,
    vc_channel_id: int,
) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return []

    statuses = [
        "PENDING",
        "STAFF_ACCEPTED",
        "OWNER_CONFIRMED",
        "READY",
        "STARTED",
        "IN_VC",
        "TAKEN_OVER",
        "RESTARTED",
    ]

    try:
        res = await _vc_sessions_select_active_async(sb, int(guild.id), int(vc_channel_id), statuses)
        rows = getattr(res, "data", None) or []
        return [row for row in rows if isinstance(row, dict)]
    except Exception as e:
        print("⚠️ _fetch_active_vc_session_rows failed:", repr(e))
        return []


async def _vc_channel_is_empty(channel: discord.abc.GuildChannel) -> bool:
    try:
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return len(channel.members) == 0
    except Exception:
        pass
    return False


async def _vc_relock_session_channel(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str = "vc session ended",
) -> bool:
    try:
        ch = await _resolve_vc_verify_channel(guild)
        if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return False

        me = guild.me
        if me is None:
            print("⚠️ VC relock skipped: bot member missing.")
            return False

        try:
            can_manage = _can_manage_channel(me, ch)
            if isinstance(can_manage, tuple):
                ok, why = bool(can_manage[0]), str(can_manage[1] if len(can_manage) > 1 else "")
            else:
                ok, why = bool(can_manage), ""
        except Exception as e:
            ok, why = False, repr(e)

        if not ok:
            print(f"⚠️ VC relock skipped: bot cannot manage VC verify channel. reason={why}")
            return False

        owner_id = _vc_owner_id_from_row(row)
        staff_ids = _vc_staff_ids_from_row(row)

        touched = False

        if owner_id > 0:
            try:
                owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
            except Exception:
                owner = None

            if owner:
                try:
                    await ch.set_permissions(owner, overwrite=None, reason=reason)
                    touched = True
                except Exception as e:
                    print(f"⚠️ Failed clearing VC overwrite for owner {owner_id}: {repr(e)}")

        for sid in staff_ids:
            try:
                staff_member = guild.get_member(sid) or await guild.fetch_member(sid)
            except Exception:
                staff_member = None

            if staff_member is None:
                continue

            try:
                await ch.set_permissions(staff_member, overwrite=None, reason=reason)
                touched = True
            except Exception as e:
                print(f"⚠️ Failed clearing VC overwrite for staff {sid}: {repr(e)}")

        return touched
    except Exception as e:
        print("⚠️ _vc_relock_session_channel failed:", repr(e))
        return False


async def _vc_mark_session_completed(
    guild: discord.Guild,
    row: Dict[str, Any],
) -> None:
    token = str(row.get("token") or "").strip()
    if not token or vc_sessions is None:
        return

    try:
        await vc_sessions.end_session(
            guild_id=int(guild.id),
            token=token,
            status="COMPLETED",
            staff_id=0,
        )
        return
    except Exception:
        pass

    try:
        vc_sessions.transition(
            token=token,
            new_status="COMPLETED",
            staff_id=0,
        )
    except Exception:
        pass


async def _vc_touch_session_activity(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str,
) -> None:
    token = _vc_row_token(row)
    if not token or vc_sessions is None:
        return

    try:
        primary_staff_id = 0
        staff_ids = _vc_staff_ids_from_row(row)
        if staff_ids:
            primary_staff_id = int(staff_ids[0])

        if hasattr(vc_sessions, "extend_expiry"):
            vc_sessions.extend_expiry(
                token=token,
                minutes=_as_int(row.get("access_minutes"), 0),
                reason=reason,
                by_staff_id=primary_staff_id,
            )
    except Exception:
        pass

    try:
        if hasattr(vc_sessions, "touch_watchdog"):
            vc_sessions.touch_watchdog(token)
    except Exception:
        pass


async def _vc_mark_owner_confirmed_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    verify_vc_id: int,
) -> None:
    try:
        if owner is None or vc_sessions is None or not hasattr(vc_sessions, "set_owner_confirmed"):
            return
        if not _member_in_target_voice(owner, verify_vc_id):
            return
        token = _vc_row_token(row)
        if not token:
            return
        meta = _vc_meta_dict(row)
        if bool(meta.get("owner_confirmed")):
            return
        vc_sessions.set_owner_confirmed(
            token=token,
            owner_id=int(owner.id),
        )
    except Exception:
        pass


async def _vc_mark_started_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
) -> None:
    try:
        if vc_sessions is None or not hasattr(vc_sessions, "mark_started"):
            return

        token = _vc_row_token(row)
        if not token:
            return

        status = _vc_row_status(row)
        if status in {"STARTED", "IN_VC", "COMPLETED", "CANCELED", "EXPIRED"}:
            return

        owner_in = _member_in_target_voice(owner, verify_vc_id)
        staff_in_members = [m for m in staff_members if _member_in_target_voice(m, verify_vc_id)]
        if not owner_in or not staff_in_members:
            return

        vc_sessions.mark_started(
            token=token,
            by_staff_id=int(staff_in_members[0].id),
        )
    except Exception:
        pass


async def _vc_sync_runtime_request_state(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
) -> None:
    try:
        token = _vc_row_token(row)
        if not token:
            return

        req = VC_REQUESTS.get(token) or {}
        owner_in = _member_in_target_voice(owner, verify_vc_id)
        staff_in = any(_member_in_target_voice(m, verify_vc_id) for m in staff_members)

        if owner_in and staff_in:
            req["status"] = "IN_VC"
        elif staff_in:
            req["status"] = "STARTED"
        elif owner_in:
            req["status"] = "READY"
        else:
            req.setdefault("status", _vc_row_status(row) or "PENDING")

        req["owner_id"] = int(owner.id) if isinstance(owner, discord.Member) else _vc_owner_id_from_row(row)
        req["ticket_channel_id"] = _as_int(row.get("ticket_channel_id"), 0)
        req["vc_channel_id"] = int(verify_vc_id)
        req["guild_id"] = int(row.get("guild_id") or 0)
        if staff_members:
            req["assigned_staff_id"] = int(staff_members[0].id)
            req["accepted_staff_id"] = int(staff_members[0].id)
        VC_REQUESTS[token] = req
    except Exception:
        pass


async def _maybe_finish_vc_sessions_after_voice_change(
    guild: discord.Guild,
    changed_channel_ids: set[int],
) -> None:
    try:
        verify_ch = await _resolve_vc_verify_channel(guild)
        if not isinstance(verify_ch, (discord.VoiceChannel, discord.StageChannel)):
            return

        verify_vc_id = int(verify_ch.id)
        if verify_vc_id not in changed_channel_ids:
            return

        rows = await _fetch_active_vc_session_rows(guild, verify_vc_id)
        if not rows:
            return

        if await _vc_channel_is_empty(verify_ch):
            for row in rows:
                try:
                    await _vc_relock_session_channel(
                        guild,
                        row,
                        reason="VC verify session ended and channel emptied",
                    )
                    await _vc_mark_session_completed(guild, row)

                    ticket_channel_id = _as_int(row.get("ticket_channel_id"), 0)
                    if ticket_channel_id > 0:
                        try:
                            ticket_ch = guild.get_channel(ticket_channel_id)
                            if ticket_ch is None:
                                ticket_ch = await guild.fetch_channel(ticket_channel_id)
                            if isinstance(ticket_ch, discord.TextChannel):
                                await ticket_ch.send(
                                    "🔒 VC verify session ended. The ID Verify VC has been locked again."
                                )
                        except Exception:
                            pass
                except Exception as e:
                    print("⚠️ VC session finalize loop error:", repr(e))
            return

        for row in rows:
            try:
                owner_id = _vc_owner_id_from_row(row)
                owner = None
                if owner_id > 0:
                    try:
                        owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
                    except Exception:
                        owner = None

                staff_members: List[discord.Member] = []
                for sid in _vc_staff_ids_from_row(row):
                    try:
                        member = guild.get_member(sid) or await guild.fetch_member(sid)
                        if isinstance(member, discord.Member):
                            staff_members.append(member)
                    except Exception:
                        continue

                await _vc_touch_session_activity(
                    guild,
                    row,
                    reason="verify vc still has active users",
                )
                await _vc_mark_owner_confirmed_if_needed(row, owner, verify_vc_id)
                await _vc_mark_started_if_needed(row, owner, staff_members, verify_vc_id)
                await _vc_sync_runtime_request_state(row, owner, staff_members, verify_vc_id)
            except Exception as e:
                print("⚠️ VC session live-state reconcile error:", repr(e))

    except Exception as e:
        print("⚠️ _maybe_finish_vc_sessions_after_voice_change error:", repr(e))


# Guards
_AUTO_UV_REMOVAL_TS: Dict[Tuple[int, int], Any] = {}
_JOIN_VERIFY_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}
_JOIN_PROCESS_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}


def _member_runtime_key(guild_id: Any, member_id: Any) -> Tuple[int, int]:
    return (_as_int(guild_id, 0), _as_int(member_id, 0))


def _get_member_processing_lock(guild_id: Any, member_id: Any) -> asyncio.Lock:
    key = _member_runtime_key(guild_id, member_id)
    lock = _JOIN_PROCESS_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _JOIN_PROCESS_LOCKS[key] = lock
    return lock


async def _refetch_live_member(guild: discord.Guild, member_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(member_id))
        if isinstance(member, discord.Member):
            return member
    except Exception:
        pass

    try:
        fetched = await guild.fetch_member(int(member_id))
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    return None


async def _sync_verification_wait_timer_for_member(
    member: discord.Member,
    *,
    source: str,
) -> None:
    try:
        if getattr(member, "bot", False):
            return

        gid = int(member.guild.id)
        uid = int(member.id)

        if _member_has_any_safe_access_role(member, include_unverified=False):
            try:
                cancelled = await cancel_verification_wait_timers_for_member(gid, uid)
                print(
                    f"🛑 [VERIFY-TIMER] cancel "
                    f"guild={gid} member={uid} cancelled={cancelled} source={source}"
                )
            except Exception as e:
                print(
                    f"⚠️ [VERIFY-TIMER] cancel failed "
                    f"guild={gid} member={uid} source={source} error={repr(e)}"
                )
            return

        if _member_is_pending_verification(member):
            try:
                fallback_channel = await _resolve_unverified_chat_channel(member.guild)
                started = await start_join_grace_then_kick_timer_for_member(
                    member,
                    source_channel=fallback_channel,
                )
                print(
                    f"⏳ [VERIFY-TIMER] start "
                    f"guild={gid} member={uid} started={started} "
                    f"channel={getattr(fallback_channel, 'id', None)} source={source}"
                )
            except Exception as e:
                print(
                    f"⚠️ [VERIFY-TIMER] start failed "
                    f"guild={gid} member={uid} source={source} error={repr(e)}"
                )
            return

        try:
            cancelled = await cancel_verification_wait_timers_for_member(gid, uid)
            print(
                f"🛑 [VERIFY-TIMER] cleanup "
                f"guild={gid} member={uid} cancelled={cancelled} source={source}"
            )
        except Exception as e:
            print(
                f"⚠️ [VERIFY-TIMER] cleanup failed "
                f"guild={gid} member={uid} source={source} error={repr(e)}"
            )
    except Exception as e:
        print(
            f"⚠️ _sync_verification_wait_timer_for_member error "
            f"member={getattr(member, 'id', 'unknown')} source={source} error={repr(e)}"
        )


async def _ensure_member_verification_safe_state(
    member: discord.Member,
    *,
    source: str,
    risk_profile: Optional[Dict[str, Any]] = None,
    fail_closed: bool = True,
) -> bool:
    try:
        live_member = await _refetch_live_member(member.guild, int(member.id)) or member

        if getattr(live_member, "bot", False):
            return True

        if _member_has_any_safe_access_role(live_member, include_unverified=False):
            try:
                await _new_sync_member_safe(
                    live_member,
                    in_guild=True,
                    risk_profile=risk_profile,
                )
            except Exception:
                pass

            await _sync_verification_wait_timer_for_member(
                live_member,
                source=f"{source}:already-safe",
            )
            return True

        if _member_is_pending_verification(live_member):
            try:
                await _new_sync_member_safe(
                    live_member,
                    in_guild=True,
                    risk_profile=risk_profile,
                )
            except Exception:
                pass

            await _sync_verification_wait_timer_for_member(
                live_member,
                source=f"{source}:pending-verification",
            )
            return True

        ensured_unverified = False
        try:
            ensured_unverified = await _ensure_unverified_on_join(live_member)
        except Exception as e:
            print(
                f"⚠️ [VERIFY] _ensure_unverified_on_join failed "
                f"member={live_member.id} source={source} error={repr(e)}"
            )

        live_member = await _refetch_live_member(member.guild, int(member.id)) or live_member

        try:
            await _new_sync_member_safe(
                live_member,
                in_guild=True,
                risk_profile=risk_profile,
            )
        except Exception:
            pass

        if _member_has_any_safe_access_role(live_member, include_unverified=False):
            await _sync_verification_wait_timer_for_member(
                live_member,
                source=f"{source}:gained-safe-role",
            )
            return True

        if _member_is_pending_verification(live_member):
            await _sync_verification_wait_timer_for_member(
                live_member,
                source=f"{source}:confirmed-unverified",
            )
            return True

        if fail_closed:
            await _handle_join_verification_failure(
                live_member,
                (
                    f"{source}: member has no safe verification role state after recovery. "
                    f"ensured_unverified={ensured_unverified}"
                ),
            )

        return False
    except Exception as e:
        print(
            f"⚠️ _ensure_member_verification_safe_state error "
            f"member={getattr(member, 'id', 'unknown')} source={source} error={repr(e)}"
        )
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


async def _resolve_bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass

    try:
        if getattr(bot, "user", None):
            fetched = await guild.fetch_member(bot.user.id)  # type: ignore[arg-type]
            if isinstance(fetched, discord.Member):
                return fetched
    except Exception:
        pass

    return None


async def _verification_role_ids_for_guild(guild: discord.Guild) -> Dict[str, int]:
    """Resolve verification role IDs for this guild without leaking home-guild globals."""

    try:
        if callable(get_guild_config):
            cfg = await get_guild_config(guild.id, force_refresh=False)  # type: ignore[misc]
            return {
                "unverified": _as_int(cfg.get("unverified_role_id"), 0),
                "verified": _as_int(cfg.get("verified_role_id"), 0),
                "resident": _as_int(cfg.get("resident_role_id"), 0),
                "staff": _as_int(cfg.get("staff_role_id"), 0),
                "stoner": _as_int(cfg.get("stoner_role_id"), 0),
                "drunken": _as_int(cfg.get("drunken_role_id"), 0),
            }
    except Exception as e:
        print(f"⚠️ [VERIFY] per-guild role config lookup failed guild={getattr(guild, 'id', 'unknown')} error={repr(e)}")

    allow_global = True
    try:
        if public_config_isolation_enabled():
            home_gid = _as_int(globals().get("GUILD_ID", 0), 0)
            guild_id = _as_int(getattr(guild, "id", 0), 0)
            allow_global = bool(home_gid > 0 and guild_id == home_gid)
    except Exception:
        allow_global = False

    if not allow_global:
        return {
            "unverified": 0,
            "verified": 0,
            "resident": 0,
            "staff": 0,
            "stoner": 0,
            "drunken": 0,
        }

    return {
        "unverified": _as_int(globals().get("UNVERIFIED_ROLE_ID", 0), 0),
        "verified": _as_int(globals().get("VERIFIED_ROLE_ID", 0), 0),
        "resident": _as_int(globals().get("RESIDENT_ROLE_ID", 0), 0),
        "staff": _as_int(globals().get("STAFF_ROLE_ID", 0), 0),
        "stoner": _as_int(globals().get("STONER_ROLE_ID", 0), 0),
        "drunken": _as_int(globals().get("DRUNKEN_ROLE_ID", 0), 0),
    }


async def _verification_config_ready_for_guild(guild: discord.Guild) -> Tuple[bool, str]:
    role_ids = await _verification_role_ids_for_guild(guild)
    uv_id = int(role_ids.get("unverified") or 0)
    if uv_id <= 0:
        return False, "No per-guild Unverified role configured. Setup must finish before join enforcement."

    try:
        role = guild.get_role(uv_id)
        if role is None:
            return False, f"Configured Unverified role {uv_id} does not exist in this guild."
    except Exception:
        return False, "Could not validate this guild's Unverified role."

    return True, "Verification config ready."


async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:
    from .members_new.join_removal_safety import handle_join_verification_failure

    await handle_join_verification_failure(member, reason)


async def _ensure_unverified_on_join(member: discord.Member) -> bool:
    try:
        if getattr(member, "bot", False):
            return False

        guild = member.guild
        role_ids = await _verification_role_ids_for_guild(guild)
        uv_id = int(role_ids.get("unverified") or 0)
        v_id = int(role_ids.get("verified") or 0)
        resident_id = int(role_ids.get("resident") or 0)
        staff_id = int(role_ids.get("staff") or 0)
        stoner_id = int(role_ids.get("stoner") or 0)
        drunken_id = int(role_ids.get("drunken") or 0)

        if not uv_id:
            print(f"⚠️ [VERIFY] Unverified role missing for guild={guild.id}; setup required before join enforcement.")
            return False

        role = guild.get_role(uv_id)
        if not role:
            print(f"⚠️ [VERIFY] UNVERIFIED_ROLE_ID not found in guild: {uv_id}")
            return False

        bot_member = await _resolve_bot_member(guild)
        if not bot_member:
            print("⚠️ [VERIFY] Could not resolve bot member in guild.")
            return False

        try:
            if not bot_member.guild_permissions.manage_roles:
                print("⚠️ [VERIFY] Bot is missing Manage Roles permission.")
                return False
        except Exception:
            print("⚠️ [VERIFY] Could not confirm Manage Roles permission.")
            return False

        try:
            if role.position >= bot_member.top_role.position:
                print(
                    f"⚠️ [VERIFY] Cannot assign Unverified because role hierarchy blocks it. "
                    f"unverified_role={role.name}({role.id}) bot_top={bot_member.top_role.name}({bot_member.top_role.id})"
                )
                return False
        except Exception:
            print("⚠️ [VERIFY] Failed hierarchy check for Unverified assignment.")
            return False

        last_error: Optional[Exception] = None

        for attempt in range(1, 4):
            try:
                if attempt == 1:
                    await asyncio.sleep(1.5)
                else:
                    await asyncio.sleep(1.0)

                try:
                    fresh_member = await guild.fetch_member(member.id)
                except Exception:
                    fresh_member = member

                if getattr(fresh_member, "bot", False):
                    return False

                if v_id and _member_has_role_id(fresh_member, v_id):
                    print(f"ℹ️ [VERIFY] Skip Unverified for {fresh_member.id}; already has Verified.")
                    return False

                if resident_id and _member_has_role_id(fresh_member, resident_id):
                    print(f"ℹ️ [VERIFY] Skip Unverified for {fresh_member.id}; already has Resident.")
                    return False

                if staff_id and _member_has_role_id(fresh_member, staff_id):
                    print(f"ℹ️ [VERIFY] Skip Unverified for {fresh_member.id}; already has Staff.")
                    return False

                if stoner_id and _member_has_role_id(fresh_member, stoner_id):
                    print(f"ℹ️ [VERIFY] Skip Unverified for {fresh_member.id}; already has Stoner.")
                    return False

                if drunken_id and _member_has_role_id(fresh_member, drunken_id):
                    print(f"ℹ️ [VERIFY] Skip Unverified for {fresh_member.id}; already has Drunken.")
                    return False

                if _member_has_role_id(fresh_member, uv_id):
                    print(f"ℹ️ [VERIFY] Member {fresh_member.id} already has Unverified.")
                    return True

                await fresh_member.add_roles(
                    role,
                    reason="Auto-assign Unverified on join (not Verified)",
                )

                try:
                    confirm_member = await guild.fetch_member(member.id)
                except Exception:
                    confirm_member = fresh_member

                if _member_has_role_id(confirm_member, uv_id):
                    print(
                        f"✅ [VERIFY] Assigned Unverified to {confirm_member} ({confirm_member.id}) "
                        f"on attempt {attempt}"
                    )
                    return True

            except discord.Forbidden as e:
                last_error = e
                print(
                    f"❌ [VERIFY] Forbidden assigning Unverified to {member.id}. "
                    f"Check role hierarchy + Manage Roles. attempt={attempt} error={repr(e)}"
                )
                break

            except discord.HTTPException as e:
                last_error = e
                print(
                    f"⚠️ [VERIFY] HTTPException assigning Unverified to {member.id}. "
                    f"attempt={attempt} error={repr(e)}"
                )

            except Exception as e:
                last_error = e
                print(
                    f"⚠️ [VERIFY] Unexpected error assigning Unverified to {member.id}. "
                    f"attempt={attempt} error={repr(e)}"
                )

        print(
            f"❌ [VERIFY] Failed to assign Unverified to {member.id}. "
            f"last_error={repr(last_error)}"
        )
        return False

    except Exception as e:
        print("⚠️ _ensure_unverified_on_join fatal error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


async def _resolve_unverified_chat_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    candidate_ids: List[int] = []

    for key in (
        "UNVERIFIED_CHAT_CHANNEL_ID",
        "UNVERIFIED_ONLY_CHAT_CHANNEL_ID",
        "VERIFY_WAIT_CHANNEL_ID",
        "VERIFY_HELP_CHANNEL_ID",
        "UNVERIFIED_CHANNEL_ID",
    ):
        try:
            raw = globals().get(key)
            cid = _as_int(raw, 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass

    for key in (
        "UNVERIFIED_CHAT_CHANNEL_ID",
        "UNVERIFIED_ONLY_CHAT_CHANNEL_ID",
        "VERIFY_WAIT_CHANNEL_ID",
        "VERIFY_HELP_CHANNEL_ID",
        "UNVERIFIED_CHANNEL_ID",
    ):
        try:
            raw = os.getenv(key, "")
            cid = _as_int(raw, 0)
            if cid > 0 and cid not in candidate_ids:
                candidate_ids.append(cid)
        except Exception:
            pass

    for cid in candidate_ids:
        try:
            ch = guild.get_channel(int(cid))
            if isinstance(ch, discord.TextChannel):
                return ch
        except Exception:
            pass

        try:
            fetched = await guild.fetch_channel(int(cid))
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except Exception:
            pass

    exact_names = {
        "unverified-chat",
        "unverified",
        "verify-chat",
        "verification-chat",
    }

    fuzzy_terms = (
        "unverified",
        "verify",
        "verification",
    )

    try:
        for ch in list(guild.text_channels):
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if name in exact_names:
                return ch
    except Exception:
        pass

    try:
        for ch in list(guild.text_channels):
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if any(term in name for term in fuzzy_terms):
                return ch
    except Exception:
        pass

    return None


async def _join_verification_watchdog(guild_id: int, member_id: int) -> None:
    key = (int(guild_id), int(member_id))

    try:
        total_attempts = 5

        for attempt in range(1, total_attempts + 1):
            try:
                guild = bot.get_guild(int(guild_id))
                if guild is None:
                    await asyncio.sleep(2.0)
                    continue

                try:
                    member = guild.get_member(int(member_id))
                    if member is None:
                        member = await guild.fetch_member(int(member_id))
                except Exception:
                    member = None

                if member is None:
                    print(
                        f"⚠️ [VERIFY-WATCHDOG] Member not found yet "
                        f"guild={guild_id} member={member_id} attempt={attempt}"
                    )
                    await asyncio.sleep(2.0)
                    continue

                if getattr(member, "bot", False):
                    return

                lock = _get_member_processing_lock(guild_id, member_id)
                async with lock:
                    ok = await _ensure_member_verification_safe_state(
                        member,
                        source=f"watchdog-attempt-{attempt}",
                        fail_closed=False,
                    )
                    if ok:
                        return

                await asyncio.sleep(3.0)

            except Exception as e:
                print(
                    f"⚠️ [VERIFY-WATCHDOG] loop error guild={guild_id} member={member_id} "
                    f"attempt={attempt} error={repr(e)}"
                )
                try:
                    traceback.print_exc()
                except Exception:
                    pass
                await asyncio.sleep(2.0)

        try:
            guild = bot.get_guild(int(guild_id))
            if guild is not None:
                member = guild.get_member(int(member_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(member_id))
                    except Exception:
                        member = None

                if (
                    member is not None
                    and not getattr(member, "bot", False)
                    and not _member_has_any_safe_access_role(member, include_unverified=True)
                ):
                    await _handle_join_verification_failure(
                        member,
                        "Join verification watchdog exhausted retries and member still has no safe verification role.",
                    )
        except Exception as e:
            print(
                f"⚠️ [VERIFY-WATCHDOG] final fail-closed action failed "
                f"guild={guild_id} member={member_id} error={repr(e)}"
            )

    finally:
        try:
            _JOIN_VERIFY_TASKS.pop(key, None)
        except Exception:
            pass


def _schedule_join_verification_watchdog(member: discord.Member) -> None:
    try:
        key = (int(member.guild.id), int(member.id))
        if key in _JOIN_VERIFY_TASKS:
            existing = _JOIN_VERIFY_TASKS[key]
            if not existing.done():
                return

        task = asyncio.create_task(_join_verification_watchdog(member.guild.id, member.id))
        _JOIN_VERIFY_TASKS[key] = task
    except Exception as e:
        print(f"⚠️ [VERIFY-WATCHDOG] failed to schedule task for member={getattr(member, 'id', 'unknown')}: {e}")


# ============================================================
# MEMBER LOGS / RAID DETECTION / ALT CLUSTERING
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):
    try:
        guild = member.guild
        gid = int(guild.id)

        _ensure_gid_join_deque(JOIN_TIMES, gid)
        _ensure_gid_dict(RAID_RECENT_JOINERS, gid)
        _ensure_gid_dict_of_lists(ALT_JOIN_BUCKETS, gid)
        _ensure_gid_dict(ALT_JOIN_BUCKET_TS, gid)

        lock = _get_member_processing_lock(gid, int(member.id))
        async with lock:
            try:
                RUNTIME_STATS["member_joins"] += 1
            except Exception:
                pass

            if not getattr(member, "bot", False):
                JOIN_TIMES[gid].append(now_utc())
                RAID_RECENT_JOINERS[gid][int(member.id)] = now_utc()

            age_days = _account_age_days(member)
            fp = _behavior_fingerprint(member)

            try:
                if getattr(member, "bot", False):
                    risk_profile = {
                        "score": 0,
                        "risk_score": 0,
                        "level": "low",
                        "risk_level": "low",
                        "evidence_tier": "clear",
                        "reasons": ["Discord marks this account as a bot; excluded from raid/alt scoring."],
                        "risk_reasons": ["Discord marks this account as a bot; excluded from raid/alt scoring."],
                        "same_fingerprint_count": 0,
                        "similar_name_count": 0,
                        "same_age_bucket_count": 0,
                        "burst_count": 0,
                        "burst_join_count": 0,
                        "fingerprint": fp,
                        "suspicion_flags": ["bot_account"],
                        "is_bot_account": True,
                    }
                else:
                    risk_profile = track_member_join_risk(member)
            except Exception:
                risk_profile = {
                    "score": 0,
                    "risk_score": 0,
                    "level": "low",
                    "risk_level": "low",
                    "evidence_tier": "clear",
                    "reasons": [],
                    "risk_reasons": [],
                    "same_fingerprint_count": 0,
                    "similar_name_count": 0,
                    "same_age_bucket_count": 0,
                    "burst_count": 0,
                    "burst_join_count": 0,
                    "fingerprint": fp,
                    "suspicion_flags": ["bot_account"] if getattr(member, "bot", False) else [],
                    "is_bot_account": bool(getattr(member, "bot", False)),
                }

            embed = discord.Embed(
                title="📥 Member Joined",
                color=discord.Color.green(),
                timestamp=now_utc(),
            )
            embed.add_field(
                name="User",
                value=f"{member.mention} (`{member.id}`)\n`{member}`",
                inline=False,
            )
            embed.add_field(
                name="Account Age",
                value=f"`{age_days} days` (created `{member.created_at}`)",
                inline=False,
            )
            embed.add_field(name="Fingerprint", value=f"`{fp}`", inline=False)
            embed.add_field(
                name="Alt Risk",
                value=(
                    f"`{risk_profile.get('score', 0)}/100` "
                    f"(`{risk_profile.get('evidence_tier', 'clear')}` / `{risk_profile.get('level', 'low')}`)\n"
                    f"fp matches: `{risk_profile.get('same_fingerprint_count', 0)}` • "
                    f"name matches: `{risk_profile.get('similar_name_count', 0)}` • "
                    f"age bucket matches: `{risk_profile.get('same_age_bucket_count', 0)}` • "
                    f"burst: `{risk_profile.get('burst_count', 0)}`"
                ),
                inline=False,
            )
            reasons = list(risk_profile.get("reasons") or risk_profile.get("risk_reasons") or [])
            if reasons:
                embed.add_field(
                    name="Risk Reasons",
                    value="\n".join(f"• {str(x)[:180]}" for x in reasons[:5]),
                    inline=False,
                )
            if member.joined_at:
                embed.add_field(name="Joined At", value=f"`{member.joined_at}`", inline=False)

            try:
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            except Exception:
                pass

            if not getattr(member, "bot", False):
                bucket = f"{_age_bucket(age_days)}"
                _ensure_bucket_list(ALT_JOIN_BUCKETS, gid, bucket)
                ALT_JOIN_BUCKETS[gid][bucket].append(int(member.id))
                ALT_JOIN_BUCKET_TS[gid][bucket] = now_utc()

                triggered, msg = await _maybe_trigger_raid(guild)
                if triggered:
                    await _post_raidlog(guild, msg)

                strip_msg = await _mass_role_strip_if_needed(member)
                if strip_msg:
                    await _post_raidlog(guild, strip_msg)

            target_ch: Optional[discord.TextChannel] = None
            try:
                if JOIN_LOG_CHANNEL_ID and int(JOIN_LOG_CHANNEL_ID) != 0:
                    ch = guild.get_channel(int(JOIN_LOG_CHANNEL_ID))
                    if isinstance(ch, discord.TextChannel):
                        target_ch = ch
            except Exception:
                target_ch = None

            if not target_ch:
                target_ch = _get_modlog_channel(guild)

            if target_ch:
                await target_ch.send(embed=embed, view=build_quick_mod_view(member.id))

            try:
                await _new_sync_member_safe(
                    member,
                    in_guild=True,
                    risk_profile=risk_profile,
                )
            except Exception:
                pass

            try:
                if not getattr(member, "bot", False):
                    await _persist_member_join_context(
                        member,
                        risk_profile=risk_profile,
                    )
            except Exception as e:
                print(f"⚠️ Failed persisting join entry context for {member.id}: {repr(e)}")

            safe_state_ok = await _ensure_member_verification_safe_state(
                member,
                source="on_member_join",
                risk_profile=risk_profile,
                fail_closed=True,
            )
            if not safe_state_ok:
                return

            if not getattr(member, "bot", False):
                try:
                    _schedule_join_verification_watchdog(member)
                except Exception as e:
                    print(f"⚠️ Failed to schedule join verification watchdog for {member.id}: {repr(e)}")

            if not getattr(member, "bot", False):
                try:
                    cutoff = now_utc() - timedelta(minutes=max(5, int(ALT_CLUSTER_WINDOW_MINUTES)))
                    for b, ts in list((ALT_JOIN_BUCKET_TS.get(gid) or {}).items()):
                        if ts < cutoff:
                            ALT_JOIN_BUCKET_TS[gid].pop(b, None)
                            ALT_JOIN_BUCKETS[gid].pop(b, None)

                    for b, ids in list((ALT_JOIN_BUCKETS.get(gid) or {}).items()):
                        if len(ids) >= int(ALT_CLUSTER_MIN_GROUP):
                            await _post_raidlog(
                                guild,
                                f"🧩 **Alt/Cluster Flag**: `{len(ids)}` joins in `{b}` bucket within ~{ALT_CLUSTER_WINDOW_MINUTES}m. "
                                + "IDs: "
                                + ", ".join([f"`{x}`" for x in ids[-10:]]),
                            )
                except Exception:
                    pass

    except Exception as e:
        print("⚠️ on_member_join error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


@bot.event
async def on_member_remove(member: discord.Member):
    try:
        guild = member.guild
        gid = int(guild.id)

        try:
            _ensure_gid_dict(RAID_RECENT_JOINERS, gid)
            RAID_RECENT_JOINERS[gid].pop(int(member.id), None)
        except Exception:
            pass

        try:
            key = (gid, int(member.id))
            task = _JOIN_VERIFY_TASKS.pop(key, None)
            if task and not task.done():
                task.cancel()
        except Exception:
            pass

        try:
            await cancel_verification_wait_timers_for_member(gid, int(member.id))
        except Exception as e:
            print(f"⚠️ Failed cancelling verification wait timers for departed member {member.id}: {repr(e)}")

        kick_logged = False
        try:
            kick_logged = await maybe_log_recent_kick(guild, member)
        except Exception:
            kick_logged = False

        if kick_logged:
            try:
                RUNTIME_STATS["member_kicks_detected"] += 1
            except Exception:
                pass

            try:
                await _new_mark_member_left_safe(member)
            except Exception:
                pass

            try:
                await _auto_close_verification_ticket_for_departed_member(
                    member,
                    leave_reason="AUTO CLOSED: user was kicked during verification",
                )
            except Exception:
                pass
            return

        suppress_leave_for_ban = False
        try:
            entry = await _audit_find_recent_ban(guild, int(member.id))
            suppress_leave_for_ban = bool(entry)
        except Exception:
            suppress_leave_for_ban = False

        if not suppress_leave_for_ban:
            try:
                await asyncio.sleep(1.5)
            except Exception:
                pass

            try:
                entry = await _audit_find_recent_ban(guild, int(member.id))
                suppress_leave_for_ban = bool(entry)
            except Exception:
                suppress_leave_for_ban = False

        if suppress_leave_for_ban:
            try:
                await _new_mark_member_left_safe(member)
            except Exception:
                pass

            print(
                f"ℹ️ Suppressed generic leave log for member={member.id} "
                f"because a recent ban audit entry exists; on_member_ban will handle logging."
            )
            return

        try:
            RUNTIME_STATS["member_leaves_detected"] += 1
        except Exception:
            pass

        embed = discord.Embed(title="📤 Member Left", color=discord.Color.blurple())
        embed.add_field(name="User", value=f"`{member}` (`{member.id}`)", inline=False)
        await _post_modlog(guild, embed, view=None)

        try:
            await _new_mark_member_left_safe(member)
        except Exception:
            pass

        try:
            await _auto_close_verification_ticket_for_departed_member(
                member,
                leave_reason="AUTO CLOSED: user left during verification",
            )
        except Exception:
            pass

    except Exception as e:
        print("⚠️ on_member_remove error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    try:
        try:
            RUNTIME_STATS["member_bans_detected"] += 1
        except Exception:
            pass

        logged = False
        try:
            logged = await maybe_log_recent_ban(guild, user)
        except Exception:
            logged = False

        if not logged:
            entry = await _audit_find_recent_ban(guild, int(user.id))
            mod = entry.user if entry else None
            reason = getattr(entry, "reason", None) if entry else None

            embed = discord.Embed(title="🔨 Ban Event", color=discord.Color.red())
            embed.add_field(name="User", value=f"<@{user.id}> (`{user}` | `{user.id}`)", inline=False)
            embed.add_field(
                name="By",
                value=f"{mod.mention if mod else 'Unknown'} (`{getattr(mod,'id',0)}`)",
                inline=False,
            )
            embed.add_field(name="Reason", value=f"`{reason or '—'}`", inline=False)
            await _post_modlog(guild, embed)

        try:
            member_like = guild.get_member(int(user.id))
            if member_like is None:
                try:
                    member_like = await guild.fetch_member(int(user.id))
                except Exception:
                    member_like = None

            if isinstance(member_like, discord.Member):
                try:
                    await _new_mark_member_left_safe(member_like)
                except Exception:
                    pass

                try:
                    await _auto_close_verification_ticket_for_departed_member(
                        member_like,
                        leave_reason="AUTO CLOSED: user was banned during verification",
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ on_member_ban post-ban cleanup error for user={getattr(user, 'id', 'unknown')}: {repr(e)}")

    except Exception as e:
        print("⚠️ on_member_ban error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    try:
        guild = after.guild
        gid = int(guild.id)

        b_roles = set([r.id for r in (before.roles or [])])
        a_roles = set([r.id for r in (after.roles or [])])

        removed_unverified = False
        if b_roles != a_roles:
            added_ids = (a_roles - b_roles)
            removed_ids = (b_roles - a_roles)

            try:
                uv_id = int(UNVERIFIED_ROLE_ID or 0)
                key = (gid, int(after.id))
                if uv_id and not added_ids and removed_ids == {uv_id}:
                    ts = _AUTO_UV_REMOVAL_TS.get(key)
                    if ts and (now_utc() - ts).total_seconds() <= 15:
                        try:
                            await _new_sync_member_safe(after, in_guild=True)
                        except Exception:
                            pass
                        return
            except Exception:
                pass

            try:
                uv_id = int(UNVERIFIED_ROLE_ID or 0)
                v_id = int(VERIFIED_ROLE_ID or 0)

                if uv_id and v_id and (v_id in added_ids):
                    uv_role = guild.get_role(uv_id)
                    if uv_role and _member_has_role_id(after, uv_id):
                        await after.remove_roles(uv_role, reason="Auto-remove Unverified when Verified is granted")
                        removed_unverified = True
                        _AUTO_UV_REMOVAL_TS[(gid, int(after.id))] = now_utc()
            except Exception:
                removed_unverified = False

            try:
                if not getattr(after, "bot", False):
                    uv_id = int(UNVERIFIED_ROLE_ID or 0)
                    v_id = int(VERIFIED_ROLE_ID or 0)
                    resident_id = int(RESIDENT_ROLE_ID or 0) if RESIDENT_ROLE_ID else 0
                    staff_id = int(STAFF_ROLE_ID or 0) if STAFF_ROLE_ID else 0
                    stoner_id = int(STONER_ROLE_ID or 0) if STONER_ROLE_ID else 0
                    drunken_id = int(DRUNKEN_ROLE_ID or 0) if DRUNKEN_ROLE_ID else 0

                    if uv_id:
                        has_unverified = _member_has_role_id(after, uv_id)
                        has_verified = _member_has_role_id(after, v_id) if v_id else False
                        has_resident = _member_has_role_id(after, resident_id) if resident_id else False
                        has_staff = _member_has_role_id(after, staff_id) if staff_id else False
                        has_stoner = _member_has_role_id(after, stoner_id) if stoner_id else False
                        has_drunken = _member_has_role_id(after, drunken_id) if drunken_id else False

                        non_default_roles = [r for r in (after.roles or []) if not r.is_default()]
                        has_no_real_roles = len(non_default_roles) == 0

                        if (
                            has_no_real_roles
                            and not has_unverified
                            and not has_verified
                            and not has_resident
                            and not has_staff
                            and not has_stoner
                            and not has_drunken
                        ):
                            uv_role = guild.get_role(uv_id)
                            if uv_role is not None:
                                try:
                                    await after.add_roles(
                                        uv_role,
                                        reason="Auto-restore Unverified after member became roleless",
                                    )
                                    print(
                                        f"✅ [ROLE-HEAL] Restored Unverified to member {after.id} "
                                        f"after all roles were removed."
                                    )

                                    try:
                                        refreshed = guild.get_member(after.id) or await guild.fetch_member(after.id)
                                    except Exception:
                                        refreshed = after

                                    if isinstance(refreshed, discord.Member) and _member_is_pending_verification(refreshed):
                                        fallback_channel = await _resolve_unverified_chat_channel(guild)
                                        started = await start_join_grace_then_kick_timer_for_member(
                                            refreshed,
                                            source_channel=fallback_channel,
                                        )
                                        print(
                                            f"⏳ [ROLE-HEAL] join grace timer start "
                                            f"guild={gid} member={refreshed.id} started={started} "
                                            f"fallback_channel={getattr(fallback_channel, 'id', None)}"
                                        )
                                except discord.Forbidden as e:
                                    print(
                                        f"❌ [ROLE-HEAL] Missing permission to restore Unverified "
                                        f"to {after.id}: {repr(e)}"
                                    )
                                except discord.HTTPException as e:
                                    print(
                                        f"⚠️ [ROLE-HEAL] HTTPException restoring Unverified "
                                        f"to {after.id}: {repr(e)}"
                                    )
            except Exception as e:
                print(
                    f"⚠️ roleless auto-heal block error for member "
                    f"{getattr(after, 'id', 'unknown')}: {repr(e)}"
                )

            try:
                strip_msg = await _mass_role_strip_if_needed(after)
                if strip_msg:
                    await _post_raidlog(guild, strip_msg)
            except Exception:
                pass

        try:
            logged = await maybe_log_member_update_diff(guild, before, after)
            if removed_unverified and not logged:
                embed = discord.Embed(title="🎭 Roles Updated", color=discord.Color.teal())
                embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
                embed.add_field(
                    name="Auto",
                    value="Removed **Unverified** because **Verified** was granted. (Option A)",
                    inline=False,
                )
                await _post_modlog(guild, embed)
        except Exception as e:
            print("⚠️ maybe_log_member_update_diff error:", repr(e))

        try:
            await _new_sync_member_safe(after, in_guild=True)
        except Exception:
            pass

        if b_roles != a_roles:
            try:
                if not getattr(after, "bot", False):
                    await _sync_verification_wait_timer_for_member(
                        after,
                        source="on_member_update",
                    )

                    if not _member_has_any_safe_access_role(after, include_unverified=True):
                        _schedule_join_verification_watchdog(after)
            except Exception as e:
                print(
                    f"⚠️ verification timer/watchdog sync failed on member update "
                    f"member={after.id} error={repr(e)}"
                )

    except Exception as e:
        print("⚠️ on_member_update error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


@bot.event
async def on_invite_create(invite: discord.Invite):
    try:
        guild = getattr(invite, "guild", None)
        if guild is None:
            return
        await _refresh_guild_invite_cache(guild)
    except Exception as e:
        print(f"⚠️ on_invite_create cache refresh error: {repr(e)}")


@bot.event
async def on_invite_delete(invite: discord.Invite):
    try:
        guild = getattr(invite, "guild", None)
        if guild is None:
            return
        await _refresh_guild_invite_cache(guild)
    except Exception as e:
        print(f"⚠️ on_invite_delete cache refresh error: {repr(e)}")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    try:
        if getattr(member, "bot", False):
            return

        try:
            await maybe_log_voice_state_update(member.guild, member, before, after)
        except Exception as e:
            print("⚠️ maybe_log_voice_state_update error:", repr(e))

        try:
            await _new_sync_member_safe(member, in_guild=True)
        except Exception as e:
            print("⚠️ voice sync to supabase failed:", repr(e))

        try:
            changed_ids = {
                _as_int(getattr(getattr(before, "channel", None), "id", 0), 0),
                _as_int(getattr(getattr(after, "channel", None), "id", 0), 0),
            }
            changed_ids.discard(0)
            if changed_ids:
                await _maybe_finish_vc_sessions_after_voice_change(member.guild, changed_ids)
        except Exception as e:
            print("⚠️ VC session voice cleanup failed:", repr(e))

    except Exception as e:
        print("⚠️ on_voice_state_update error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


# ============================================================
# Startup wiring
# ============================================================

async def _run_startup_once_flags() -> None:
    try:
        if not getattr(bot, "_invite_cache_warm_started", False):  # type: ignore[attr-defined]
            try:
                bot._invite_cache_warm_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            if not _startup_task_running("_invite_cache_warm_task"):
                async def _run_invite_cache_warm():
                    try:
                        await _warm_all_guild_invite_caches()
                    except Exception as e:
                        print("⚠️ invite cache warm error:", e)
                        try:
                            traceback.print_exc()
                        except Exception:
                            pass

                _assign_startup_task("_invite_cache_warm_task", _run_invite_cache_warm())

        if not getattr(bot, "_initial_member_sync_started", False):  # type: ignore[attr-defined]
            try:
                bot._initial_member_sync_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            if not _startup_task_running("_initial_member_sync_task"):
                async def _run_startup_member_sync():
                    try:
                        await _initial_member_sync_sweep()
                        try:
                            bot._initial_member_sync_done = True  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    except Exception as e:
                        print("⚠️ background initial member sync error:", e)
                        try:
                            traceback.print_exc()
                        except Exception:
                            pass

                _assign_startup_task("_initial_member_sync_task", _run_startup_member_sync())

        if not getattr(bot, "_stale_verification_reconcile_started", False):  # type: ignore[attr-defined]
            try:
                bot._stale_verification_reconcile_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            if not _startup_task_running("_stale_verification_reconcile_task"):
                async def _run_stale_verification_reconcile():
                    try:
                        await _reconcile_stale_open_verification_tickets()
                    except Exception as e:
                        print("⚠️ stale verification reconciliation error:", e)
                        try:
                            traceback.print_exc()
                        except Exception:
                            pass

                _assign_startup_task("_stale_verification_reconcile_task", _run_stale_verification_reconcile())

        try:
            if callable(new_run_departed_reconciliation_for_guild):
                for guild in list(getattr(bot, "guilds", []) or []):
                    try:
                        await new_run_departed_reconciliation_for_guild(guild)
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            started = await ensure_channel_cleanup_worker_started()
            print(f"🧹 Channel cleanup worker status: started={started}")
        except Exception as e:
            print(f"⚠️ Failed starting channel cleanup worker: {repr(e)}")
    except Exception:
        pass


@bot.event
async def on_ready():
    try:
        if not _startup_task_running("_vc_sweeper_task"):
            try:
                task = asyncio.create_task(vc_sweeper_loop(bot))
                try:
                    bot._vc_sweeper_task = task  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception:
                pass

        await _run_startup_once_flags()
    except Exception as e:
        print("⚠️ on_ready error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
