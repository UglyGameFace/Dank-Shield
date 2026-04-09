from __future__ import annotations

import asyncio
import os
import traceback
from collections import deque
from datetime import timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

from .globals import *

# Split-out admin slash commands (guarded against duplicate registration)
from . import verify_admin_commands  # noqa: F401

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
    from . import vc_sessions
except Exception:
    vc_sessions = None  # type: ignore

try:
    from .tickets_new.service import find_open_ticket_for_owner
except Exception:
    find_open_ticket_for_owner = None  # type: ignore

try:
    from .channel_cleanup import ensure_channel_cleanup_worker_started
except Exception:
    async def ensure_channel_cleanup_worker_started() -> bool:
        return False

# NEW: timer helpers from commands.py
# app.py imports commands BEFORE events, so this is safe in your current load order.
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


# ============================================================
# ✅ Internal helpers (defensive shape repair)
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
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _member_has_any_safe_access_role(member: discord.Member, *, include_unverified: bool = True) -> bool:
    try:
        if VERIFIED_ROLE_ID and _member_has_role_id(member, int(VERIFIED_ROLE_ID)):
            return True
    except Exception:
        pass

    try:
        if RESIDENT_ROLE_ID and _member_has_role_id(member, int(RESIDENT_ROLE_ID)):
            return True
    except Exception:
        pass

    try:
        if STAFF_ROLE_ID and _member_has_role_id(member, int(STAFF_ROLE_ID)):
            return True
    except Exception:
        pass

    if include_unverified:
        try:
            if UNVERIFIED_ROLE_ID and _member_has_role_id(member, int(UNVERIFIED_ROLE_ID)):
                return True
        except Exception:
            pass

    return False


def _member_is_pending_verification(member: discord.Member) -> bool:
    try:
        if getattr(member, "bot", False):
            return False

        uv_id = int(UNVERIFIED_ROLE_ID or 0)
        verified_id = int(VERIFIED_ROLE_ID or 0)
        resident_id = int(RESIDENT_ROLE_ID or 0) if RESIDENT_ROLE_ID else 0
        staff_id = int(STAFF_ROLE_ID or 0) if STAFF_ROLE_ID else 0

        has_unverified = _member_has_role_id(member, uv_id) if uv_id else False
        has_verified = _member_has_role_id(member, verified_id) if verified_id else False
        has_resident = _member_has_role_id(member, resident_id) if resident_id else False
        has_staff = _member_has_role_id(member, staff_id) if staff_id else False

        return bool(has_unverified and not has_verified and not has_resident and not has_staff)
    except Exception:
        return False


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
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


async def _resolve_unverified_chat_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Best-effort fallback channel for verification timer notices when no ticket exists yet.
    Priority:
    1) explicit configured IDs
    2) exact name match: unverified-chat
    3) looser unverified/verify waiting-room style names
    """
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


# ============================================================
# ✅ Async wrappers for blocking Supabase/PostgREST work
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


def _member_joins_insert_sync(sb: Any, payload: Dict[str, Any]):
    return sb.table("member_joins").insert(payload).execute()


async def _member_joins_insert_async(sb: Any, payload: Dict[str, Any]):
    return await _run_blocking_db(_member_joins_insert_sync, sb, payload)


def _member_events_insert_sync(sb: Any, payload: Dict[str, Any]):
    return sb.table("member_events").insert(payload).execute()


async def _member_events_insert_async(sb: Any, payload: Dict[str, Any]):
    return await _run_blocking_db(_member_events_insert_sync, sb, payload)


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
# ✅ Verification ticket cleanup helpers (Option A)
# ============================================================

async def _auto_close_verification_ticket_for_departed_member(
    member: discord.Member,
    *,
    leave_reason: str,
) -> None:
    try:
        if not find_open_ticket_for_owner:
            return

        row = await find_open_ticket_for_owner(
            guild_id=member.guild.id,
            owner_id=member.id,
            category="verification_issue",
        )
        if not isinstance(row, dict):
            return

        channel_id = int(
            str(
                row.get("channel_id")
                or row.get("discord_thread_id")
                or "0"
            ) or 0
        )
        if channel_id <= 0:
            return

        channel = member.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await member.guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        try:
            from .transcripts import send_tickettool_style_transcript
        except Exception:
            send_tickettool_style_transcript = None  # type: ignore

        try:
            from .tickets_new.service import mark_ticket_closed, mark_ticket_deleted
        except Exception:
            mark_ticket_closed = None  # type: ignore
            mark_ticket_deleted = None  # type: ignore

        if isinstance(channel, discord.TextChannel):
            print(
                f"🧹 Auto-closing verification ticket for departed member "
                f"member={member.id} channel={channel.id} reason={leave_reason}"
            )

            try:
                if send_tickettool_style_transcript:
                    await send_tickettool_style_transcript(
                        channel,
                        member,
                        owner_id=int(member.id),
                        closed_by=None,
                        decision=leave_reason,
                    )
            except Exception as e:
                print(f"⚠️ Transcript post failed for departed member ticket {channel.id}: {repr(e)}")

            deleted_ok = False
            try:
                if mark_ticket_deleted:
                    deleted_ok = await mark_ticket_deleted(
                        channel_id=channel.id,
                        deleted_by=None,
                        reason=leave_reason,
                    )
            except Exception as e:
                print(f"⚠️ mark_ticket_deleted failed for departed member ticket {channel.id}: {repr(e)}")

            if not deleted_ok:
                try:
                    if mark_ticket_closed:
                        await mark_ticket_closed(
                            channel=channel,
                            closed_by=None,
                            reason=leave_reason,
                        )
                except Exception as e:
                    print(f"⚠️ mark_ticket_closed fallback failed for departed member ticket {channel.id}: {repr(e)}")

            try:
                await channel.delete(reason=leave_reason)
            except discord.Forbidden:
                print(f"⚠️ Missing permission to delete departed member ticket channel={channel.id}")
            except Exception as e:
                print(f"⚠️ Failed deleting departed member ticket channel={channel.id}: {repr(e)}")
            return

        try:
            if mark_ticket_deleted:
                await mark_ticket_deleted(
                    channel_id=channel_id,
                    deleted_by=None,
                    reason=leave_reason,
                )
                print(
                    f"🧹 Repaired stale verification ticket row for departed member "
                    f"member={member.id} channel_id={channel_id}"
                )
                return
        except Exception as e:
            print(f"⚠️ Failed repairing stale departed ticket row channel_id={channel_id}: {repr(e)}")

    except Exception as e:
        print("⚠️ _auto_close_verification_ticket_for_departed_member error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _reconcile_stale_open_verification_tickets() -> None:
    try:
        sb = get_supabase()
        if not sb:
            return

        guilds = list(getattr(bot, "guilds", []) or [])
        if not guilds:
            return

        repaired = 0

        for guild in guilds:
            try:
                res = await _tickets_select_open_verification_async(sb, str(guild.id))
                rows = getattr(res, "data", None) or []
            except Exception as e:
                print(f"⚠️ stale verification ticket query failed for guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
                continue

            for row in rows:
                try:
                    owner_id = int(str(row.get("user_id") or "0") or 0)
                except Exception:
                    owner_id = 0
                if owner_id <= 0:
                    continue

                try:
                    member = guild.get_member(owner_id)
                    if member is None:
                        member = await guild.fetch_member(owner_id)
                except Exception:
                    member = None

                if isinstance(member, discord.Member):
                    continue

                channel_id = int(str(row.get("channel_id") or row.get("discord_thread_id") or "0") or 0)
                if channel_id <= 0:
                    continue

                channel = guild.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await guild.fetch_channel(channel_id)
                    except Exception:
                        channel = None

                try:
                    from .transcripts import send_tickettool_style_transcript
                except Exception:
                    send_tickettool_style_transcript = None  # type: ignore

                try:
                    from .tickets_new.service import mark_ticket_closed, mark_ticket_deleted
                except Exception:
                    mark_ticket_closed = None  # type: ignore
                    mark_ticket_deleted = None  # type: ignore

                if isinstance(channel, discord.TextChannel):
                    try:
                        if send_tickettool_style_transcript:
                            await send_tickettool_style_transcript(
                                channel,
                                None,
                                owner_id=owner_id,
                                closed_by=None,
                                decision="AUTO CLOSED: user already left server",
                            )
                    except Exception as e:
                        print(f"⚠️ Startup transcript repair failed channel={channel.id}: {repr(e)}")

                    deleted_ok = False
                    try:
                        if mark_ticket_deleted:
                            deleted_ok = await mark_ticket_deleted(
                                channel_id=channel.id,
                                deleted_by=None,
                                reason="AUTO CLOSED: user already left server",
                            )
                    except Exception as e:
                        print(f"⚠️ Startup mark_ticket_deleted failed channel={channel.id}: {repr(e)}")

                    if not deleted_ok:
                        try:
                            if mark_ticket_closed:
                                await mark_ticket_closed(
                                    channel=channel,
                                    closed_by=None,
                                    reason="AUTO CLOSED: user already left server",
                                )
                        except Exception as e:
                            print(f"⚠️ Startup mark_ticket_closed fallback failed channel={channel.id}: {repr(e)}")

                    try:
                        await channel.delete(reason="Verification ticket cleanup for departed user")
                    except Exception as e:
                        print(f"⚠️ Startup ticket delete failed channel={channel.id}: {repr(e)}")
                else:
                    try:
                        if mark_ticket_deleted:
                            await mark_ticket_deleted(
                                channel_id=channel_id,
                                deleted_by=None,
                                reason="AUTO CLOSED: user already left server",
                            )
                    except Exception as e:
                        print(f"⚠️ Startup stale row repair failed channel_id={channel_id}: {repr(e)}")

                repaired += 1

        print(f"🧹 Stale verification ticket reconciliation complete: repaired={repaired}")

    except Exception as e:
        print("⚠️ _reconcile_stale_open_verification_tickets error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


# ============================================================
# ✅ Dashboard / Supabase member sync helpers
# ============================================================

def _sync_iso_now() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


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
    role_ids: List[str] = []
    role_names: List[str] = []
    roles_json: List[Dict[str, Any]] = []

    try:
        sorted_roles = sorted(
            [r for r in (member.roles or []) if not r.is_default()],
            key=lambda r: int(getattr(r, "position", 0)),
            reverse=True,
        )
    except Exception:
        sorted_roles = []

    for r in sorted_roles:
        try:
            rid = str(r.id)
            rname = str(r.name)
            rpos = int(getattr(r, "position", 0))
            role_ids.append(rid)
            role_names.append(rname)
            roles_json.append({
                "id": rid,
                "name": rname,
                "position": rpos,
            })
        except Exception:
            continue

    top_role = role_names[0] if role_names else None
    highest_role_id = role_ids[0] if role_ids else None
    highest_role_name = role_names[0] if role_names else None

    has_unverified = bool(UNVERIFIED_ROLE_ID and _member_has_role_id(member, int(UNVERIFIED_ROLE_ID)))
    has_verified_role = bool(
        (VERIFIED_ROLE_ID and _member_has_role_id(member, int(VERIFIED_ROLE_ID))) or
        (RESIDENT_ROLE_ID and _member_has_role_id(member, int(RESIDENT_ROLE_ID)))
    )
    has_staff_role = bool(STAFF_ROLE_ID and _member_has_role_id(member, int(STAFF_ROLE_ID)))
    has_secondary_verified_role = bool(
        (RESIDENT_ROLE_ID and _member_has_role_id(member, int(RESIDENT_ROLE_ID)))
    )

    try:
        uv_id = int(UNVERIFIED_ROLE_ID or 0)
    except Exception:
        uv_id = 0

    has_any_real_roles = any(
        int(rid) not in ({uv_id} if uv_id else set())
        for rid in [int(x) for x in role_ids if str(x).isdigit()]
    )

    is_bot_like = False
    try:
        is_bot_like = bool(getattr(member, "bot", False) or getattr(getattr(member, "public_flags", None), "verified_bot", False))
    except Exception:
        is_bot_like = bool(getattr(member, "bot", False))

    role_state = "unknown"
    role_state_reason = ""

    try:
        if is_bot_like:
            role_state = "bot_ok"
            role_state_reason = "Member is a bot/app and should not be treated as unverified."
        elif not role_ids:
            role_state = "unknown"
            role_state_reason = "No tracked roles found."
        elif has_staff_role and has_unverified:
            role_state = "staff_conflict"
            role_state_reason = "Member has both Staff and Unverified."
        elif has_staff_role:
            role_state = "staff_ok"
            role_state_reason = "Member has staff role."
        elif has_verified_role and has_unverified:
            role_state = "verified_conflict"
            role_state_reason = "Member has both verified role and Unverified."
        elif has_verified_role:
            role_state = "verified_ok"
            role_state_reason = "Member has verified role and no Unverified."
        elif has_unverified:
            role_state = "unverified_only"
            role_state_reason = "Member has Unverified and is pending verification."
        else:
            role_state = "missing_unverified"
            role_state_reason = "Member has no verified role and no Unverified."
    except Exception:
        role_state = "unknown"
        role_state_reason = "Role state evaluation failed."

    return {
        "role_ids": role_ids,
        "role_names": role_names,
        "roles": roles_json,
        "top_role": top_role,
        "highest_role_id": highest_role_id,
        "highest_role_name": highest_role_name,
        "has_any_role": has_any_real_roles,
        "has_unverified": has_unverified,
        "has_verified_role": has_verified_role,
        "has_staff_role": has_staff_role,
        "has_secondary_verified_role": has_secondary_verified_role,
        "has_cosmetic_only": False,
        "role_state": role_state,
        "role_state_reason": role_state_reason,
        "data_health": "ok",
    }


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
        sb = get_supabase()
        if not sb:
            return

        guild_id = str(member.guild.id)
        user_id = str(member.id)
        existing = await _sync_get_existing_member_row_async(sb, guild_id, user_id) or {}

        username = _safe_member_username(member)
        display_name = _safe_member_display_name(member)
        nickname = _safe_member_nickname(member)
        avatar_url = _safe_member_avatar_url(member)
        now_iso = _sync_iso_now()

        snap = _member_role_snapshot(member)
        voice = _member_voice_snapshot(member)

        previous_usernames = _append_unique_history(
            existing.get("previous_usernames"),
            str(existing.get("last_seen_username") or existing.get("username") or "")
        )
        previous_display_names = _append_unique_history(
            existing.get("previous_display_names"),
            str(existing.get("last_seen_display_name") or existing.get("display_name") or "")
        )
        previous_nicknames = _append_unique_history(
            existing.get("previous_nicknames"),
            str(existing.get("last_seen_nickname") or existing.get("nickname") or "")
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

        merged_risk_payload = (
            _build_risk_payload_from_profile(risk_profile, now_iso=now_iso)
            if isinstance(risk_profile, dict)
            else _extract_existing_risk_payload(existing)
        )

        full_payload = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "nickname": nickname,
            "avatar_url": avatar_url or existing.get("avatar_url") or None,
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
            "joined_at": member.joined_at.isoformat() if member.joined_at else existing.get("joined_at"),
            "synced_at": now_iso,
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
            **merged_risk_payload,
        }

        try:
            await _guild_members_upsert_async(sb, full_payload, on_conflict=True)
            return
        except TypeError:
            try:
                await _guild_members_upsert_async(sb, full_payload, on_conflict=False)
                return
            except Exception as e:
                if not _is_missing_column_error(e, "in_voice"):
                    raise
        except Exception as e:
            if not _is_missing_column_error(e, "in_voice"):
                raise

        fallback_payload = _strip_voice_fields(full_payload)

        try:
            await _guild_members_upsert_async(sb, fallback_payload, on_conflict=True)
            return
        except TypeError:
            try:
                await _guild_members_upsert_async(sb, fallback_payload, on_conflict=False)
                return
            except Exception:
                pass
        except Exception:
            pass

        minimal = _minimal_member_payload(
            member,
            in_guild=in_guild,
            risk_payload=merged_risk_payload,
        )
        minimal = _strip_voice_fields(minimal)

        try:
            await _guild_members_upsert_async(sb, minimal, on_conflict=True)
        except TypeError:
            await _guild_members_upsert_async(sb, minimal, on_conflict=False)

    except Exception as e:
        print("⚠️ _sync_member_to_supabase error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _mark_member_left(member: discord.Member) -> None:
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

        previous_usernames = _append_unique_history(
            existing.get("previous_usernames"),
            str(existing.get("last_seen_username") or existing.get("username") or "")
        )
        previous_display_names = _append_unique_history(
            existing.get("previous_display_names"),
            str(existing.get("last_seen_display_name") or existing.get("display_name") or "")
        )
        previous_nicknames = _append_unique_history(
            existing.get("previous_nicknames"),
            str(existing.get("last_seen_nickname") or existing.get("nickname") or "")
        )

        if existing.get("username") and str(existing.get("username")).strip() != username:
            previous_usernames = _append_unique_history(previous_usernames, str(existing.get("username")).strip())
        if existing.get("display_name") and str(existing.get("display_name")).strip() != display_name:
            previous_display_names = _append_unique_history(previous_display_names, str(existing.get("display_name")).strip())
        if existing.get("nickname") and str(existing.get("nickname")).strip() != nickname:
            previous_nicknames = _append_unique_history(previous_nicknames, str(existing.get("nickname")).strip())

        times_left = int(existing.get("times_left") or 0) + 1
        times_joined = int(existing.get("times_joined") or 0) or 1
        existing_risk_payload = _extract_existing_risk_payload(existing)

        full_payload = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username or existing.get("username") or "",
            "display_name": display_name or existing.get("display_name") or "",
            "nickname": nickname or existing.get("nickname") or "",
            "avatar_url": avatar_url or existing.get("avatar_url") or None,
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
            **existing_risk_payload,
        }

        try:
            await _guild_members_upsert_async(sb, full_payload, on_conflict=True)
            return
        except TypeError:
            try:
                await _guild_members_upsert_async(sb, full_payload, on_conflict=False)
                return
            except Exception as e:
                if not _is_missing_column_error(e, "in_voice"):
                    raise
        except Exception as e:
            if not _is_missing_column_error(e, "in_voice"):
                raise

        fallback_payload = _strip_voice_fields(full_payload)

        try:
            await _guild_members_upsert_async(sb, fallback_payload, on_conflict=True)
            return
        except TypeError:
            try:
                await _guild_members_upsert_async(sb, fallback_payload, on_conflict=False)
                return
            except Exception:
                pass
        except Exception:
            pass

        try:
            await _guild_members_update_member_async(
                sb,
                guild_id,
                user_id,
                {
                    "in_guild": False,
                    "data_health": "left_guild",
                    "synced_at": now_iso,
                    "updated_at": now_iso,
                },
            )
        except Exception as e2:
            print("⚠️ _mark_member_left fallback error:", e2)

    except Exception as e:
        print("⚠️ _mark_member_left error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _initial_member_sync_sweep() -> None:
    """
    Full startup sweep:
    - sync every current guild member into guild_members
    - mark any tracked row not currently present as in_guild = false
    """
    try:
        sb = get_supabase()
        if not sb:
            return

        guilds = list(getattr(bot, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            active_ids: set[str] = set()

            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception:
                members = list(getattr(guild, "members", []) or [])

            for idx, member in enumerate(members, start=1):
                try:
                    active_ids.add(str(member.id))
                    await _sync_member_to_supabase(member, in_guild=True)

                    if idx % 10 == 0:
                        await asyncio.sleep(0)
                except Exception:
                    continue

            sb = get_supabase()
            if not sb:
                continue

            marked_departed = await _bulk_mark_departed_members_async(sb, str(guild.id), active_ids)

            print(
                f"✅ Initial member sync complete for guild {guild.id}: "
                f"{len(active_ids)} active members, marked_departed={marked_departed}"
            )
        except Exception as e:
            print(f"⚠️ Initial member sync failed for guild {getattr(guild, 'id', 'unknown')}: {e}")


# ============================================================
# ✅ Invite cache + entry-path persistence helpers
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


async def _refresh_guild_invite_cache(guild: discord.Guild) -> bool:
    gid = int(guild.id)

    uses_map: Dict[str, int] = {}
    meta_map: Dict[str, Dict[str, Any]] = {}
    vanity_uses: Optional[int] = None

    try:
        invites = await guild.invites()
        for invite in invites:
            meta = _invite_meta(invite)
            code = str(meta.get("code") or "").strip()
            if not code:
                continue
            uses_map[code] = int(meta.get("uses") or 0)
            meta_map[code] = meta
    except discord.Forbidden:
        print(f"⚠️ [INVITES] Missing permission to read invites for guild={gid}")
        return False
    except Exception as e:
        print(f"⚠️ [INVITES] Failed reading invites for guild={gid}: {repr(e)}")
        return False

    try:
        vanity = await guild.vanity_invite()
        if vanity is not None:
            vanity_uses = int(getattr(vanity, "uses", 0) or 0)
    except discord.Forbidden:
        pass
    except Exception:
        vanity_uses = _VANITY_USES_CACHE.get(gid)

    _INVITE_USES_CACHE[gid] = uses_map
    _INVITE_META_CACHE[gid] = meta_map
    _VANITY_USES_CACHE[gid] = vanity_uses

    return True


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
    guild = member.guild
    gid = int(guild.id)

    default_context: Dict[str, Any] = {
        "entry_method": "unknown_join",
        "verification_source": "join_observer_unresolved",
        "invite_code": None,
        "invited_by": None,
        "invited_by_name": None,
        "vouched_by": None,
        "vouched_by_name": None,
        "approved_by": None,
        "approved_by_name": None,
        "entry_reason": "Joined, but invite attribution was unavailable.",
        "approval_reason": None,
        "join_note": "Invite attribution unavailable at join time.",
        "channel_id": None,
        "channel_name": None,
    }

    old_uses = dict(_INVITE_USES_CACHE.get(gid) or {})
    old_meta = dict(_INVITE_META_CACHE.get(gid) or {})
    old_vanity_uses = _VANITY_USES_CACHE.get(gid)

    invites_ok = False
    current_uses: Dict[str, int] = {}
    current_meta: Dict[str, Dict[str, Any]] = {}

    try:
        invites = await guild.invites()
        invites_ok = True
        for invite in invites:
            meta = _invite_meta(invite)
            code = str(meta.get("code") or "").strip()
            if not code:
                continue
            current_uses[code] = int(meta.get("uses") or 0)
            current_meta[code] = meta
    except discord.Forbidden:
        invites_ok = False
    except Exception as e:
        print(f"⚠️ [INVITES] join detect invite fetch failed guild={gid}: {repr(e)}")
        invites_ok = False

    vanity_uses: Optional[int] = old_vanity_uses
    vanity_code: Optional[str] = None
    try:
        vanity = await guild.vanity_invite()
        if vanity is not None:
            vanity_uses = int(getattr(vanity, "uses", 0) or 0)
            vanity_code = str(getattr(vanity, "code", "") or "").strip() or None
    except Exception:
        pass

    best_code: Optional[str] = None
    best_delta = 0

    for code, new_uses in current_uses.items():
        old_use_count = int(old_uses.get(code, 0) or 0)
        delta = int(new_uses or 0) - old_use_count
        if delta > best_delta:
            best_delta = delta
            best_code = code

    if best_code and best_delta > 0:
        meta = current_meta.get(best_code) or old_meta.get(best_code) or {}
        inviter_name = str(meta.get("inviter_name") or "").strip() or None
        inviter_id = str(meta.get("inviter_id") or "").strip() or None
        channel_name = str(meta.get("channel_name") or "").strip() or None
        channel_id = str(meta.get("channel_id") or "").strip() or None

        context = {
            "entry_method": "discord_invite",
            "verification_source": "discord_invite",
            "invite_code": best_code,
            "invited_by": inviter_id,
            "invited_by_name": inviter_name,
            "vouched_by": None,
            "vouched_by_name": None,
            "approved_by": None,
            "approved_by_name": None,
            "entry_reason": (
                f"Joined using invite `{best_code}`"
                + (f" created by {inviter_name}" if inviter_name else "")
                + (f" in #{channel_name}" if channel_name else "")
                + "."
            ),
            "approval_reason": None,
            "join_note": (
                f"Invite `{best_code}`"
                + (f" • inviter: {inviter_name}" if inviter_name else "")
                + (f" • channel: #{channel_name}" if channel_name else "")
            ),
            "channel_id": channel_id,
            "channel_name": channel_name,
        }

        _INVITE_USES_CACHE[gid] = current_uses
        _INVITE_META_CACHE[gid] = current_meta
        _VANITY_USES_CACHE[gid] = vanity_uses
        return context

    if (
        old_vanity_uses is not None
        and vanity_uses is not None
        and int(vanity_uses) > int(old_vanity_uses)
    ):
        context = {
            "entry_method": "vanity_invite",
            "verification_source": "discord_vanity",
            "invite_code": vanity_code or "vanity",
            "invited_by": None,
            "invited_by_name": "Vanity URL",
            "vouched_by": None,
            "vouched_by_name": None,
            "approved_by": None,
            "approved_by_name": None,
            "entry_reason": "Joined using the server vanity URL.",
            "approval_reason": None,
            "join_note": "Joined through vanity invite tracking.",
            "channel_id": None,
            "channel_name": None,
        }

        _INVITE_USES_CACHE[gid] = current_uses
        _INVITE_META_CACHE[gid] = current_meta
        _VANITY_USES_CACHE[gid] = vanity_uses
        return context

    if not invites_ok:
        default_context["verification_source"] = "invite_tracking_unavailable"
        default_context["entry_reason"] = (
            "Joined, but the bot could not inspect invite usage. "
            "Check Manage Server / invite read permissions."
        )
        default_context["join_note"] = "Invite tracking unavailable for this join."
    elif not old_uses and current_uses:
        default_context["verification_source"] = "invite_cache_warming"
        default_context["entry_reason"] = (
            "Joined before the bot had a usable invite baseline cache. "
            "Future joins should attribute correctly after cache warm-up."
        )
        default_context["join_note"] = "Invite cache was still warming when this member joined."
    else:
        default_context["verification_source"] = "invite_unresolved"
        default_context["entry_reason"] = (
            "Joined, but invite attribution could not be resolved from the invite delta."
        )
        default_context["join_note"] = "Invite delta did not clearly resolve this join."

    _INVITE_USES_CACHE[gid] = current_uses
    _INVITE_META_CACHE[gid] = current_meta
    _VANITY_USES_CACHE[gid] = vanity_uses

    return default_context


async def _persist_member_join_context(
    member: discord.Member,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        sb = get_supabase()
        if not sb:
            return

        guild_id = str(member.guild.id)
        user_id = str(member.id)
        now_iso = _sync_iso_now()
        joined_at = member.joined_at.isoformat() if member.joined_at else now_iso

        context = await _detect_join_entry_context(member)
        risk_payload = _build_risk_payload_from_profile(risk_profile, now_iso=now_iso)

        guild_member_patch = {
            "entry_method": context.get("entry_method"),
            "verification_source": context.get("verification_source"),
            "invite_code": context.get("invite_code"),
            "invited_by": context.get("invited_by"),
            "invited_by_name": context.get("invited_by_name"),
            "vouched_by": context.get("vouched_by"),
            "vouched_by_name": context.get("vouched_by_name"),
            "approved_by": context.get("approved_by"),
            "approved_by_name": context.get("approved_by_name"),
            "entry_reason": context.get("entry_reason"),
            "approval_reason": context.get("approval_reason"),
            "joined_at": joined_at,
            "synced_at": now_iso,
            "updated_at": now_iso,
            "last_seen_at": now_iso,
            **risk_payload,
        }

        try:
            await _guild_members_update_member_async(sb, guild_id, user_id, guild_member_patch)
        except Exception as e:
            print(f"⚠️ [JOIN-CONTEXT] guild_members patch failed for user={user_id}: {repr(e)}")

        join_row = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": _safe_member_username(member),
            "display_name": _safe_member_display_name(member),
            "avatar_url": _safe_member_avatar_url(member),
            "joined_at": joined_at,
            "entry_method": context.get("entry_method"),
            "verification_source": context.get("verification_source"),
            "invite_code": context.get("invite_code"),
            "invited_by": context.get("invited_by"),
            "invited_by_name": context.get("invited_by_name"),
            "vouched_by": context.get("vouched_by"),
            "vouched_by_name": context.get("vouched_by_name"),
            "approved_by": context.get("approved_by"),
            "approved_by_name": context.get("approved_by_name"),
            "join_note": context.get("join_note"),
            "source_ticket_id": None,
            "risk_score": risk_payload.get("risk_score", 0),
            "risk_level": risk_payload.get("risk_level", "low"),
            "risk_reasons": risk_payload.get("risk_reasons", []),
            "fingerprint": risk_payload.get("fingerprint"),
            "alt_cluster_key": risk_payload.get("alt_cluster_key"),
            "alt_cluster_size": risk_payload.get("alt_cluster_size", 0),
            "burst_join_count": risk_payload.get("burst_join_count", 0),
            "same_fingerprint_count": risk_payload.get("same_fingerprint_count", 0),
            "similar_name_count": risk_payload.get("similar_name_count", 0),
            "same_age_bucket_count": risk_payload.get("same_age_bucket_count", 0),
            "suspicious_name_pattern": risk_payload.get("suspicious_name_pattern", False),
            "repeated_char_pattern": risk_payload.get("repeated_char_pattern", False),
            "default_avatar": risk_payload.get("default_avatar", False),
            "account_age_days": risk_payload.get("account_age_days"),
            "age_bucket": risk_payload.get("age_bucket"),
            "digit_ratio": risk_payload.get("digit_ratio", 0.0),
            "underscore_ratio": risk_payload.get("underscore_ratio", 0.0),
            "cluster_members": risk_payload.get("cluster_members", []),
            "suspicion_flags": risk_payload.get("suspicion_flags", []),
            "risk_evaluated_at": now_iso,
            "join_fingerprint": risk_payload.get("fingerprint"),
        }

        try:
            await _member_joins_insert_async(sb, join_row)
        except Exception as e:
            print(f"⚠️ [JOIN-CONTEXT] member_joins insert failed for user={user_id}: {repr(e)}")

        try:
            await _member_events_insert_async(
                sb,
                {
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "actor_id": context.get("invited_by"),
                    "actor_name": context.get("invited_by_name") or "System",
                    "event_type": "member_joined",
                    "title": "Member Joined",
                    "reason": context.get("entry_reason"),
                    "metadata": {
                        "invite_code": context.get("invite_code"),
                        "entry_method": context.get("entry_method"),
                        "verification_source": context.get("verification_source"),
                        "channel_id": context.get("channel_id"),
                        "channel_name": context.get("channel_name"),
                        "joined_at": joined_at,
                        "risk_score": risk_payload.get("risk_score", 0),
                        "risk_level": risk_payload.get("risk_level", "low"),
                        "fingerprint": risk_payload.get("fingerprint"),
                        "alt_cluster_key": risk_payload.get("alt_cluster_key"),
                        "alt_cluster_size": risk_payload.get("alt_cluster_size", 0),
                        "same_fingerprint_count": risk_payload.get("same_fingerprint_count", 0),
                        "similar_name_count": risk_payload.get("similar_name_count", 0),
                        "suspicion_flags": risk_payload.get("suspicion_flags", []),
                    },
                    "created_at": now_iso,
                },
            )
        except Exception as e:
            print(f"⚠️ [JOIN-CONTEXT] member_events insert failed for user={user_id}: {repr(e)}")

    except Exception as e:
        print("⚠️ _persist_member_join_context error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


# ============================================================
# ✅ VC session helpers (owner + assigned staff only / relock)
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
    ]

    out: List[int] = []
    seen: set[int] = set()

    for value in raw_values:
        rid = _as_int(value, 0)
        if rid > 0 and rid not in seen:
            seen.add(rid)
            out.append(rid)

    return out


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

        if not await _vc_channel_is_empty(verify_ch):
            return

        rows = await _fetch_active_vc_session_rows(guild, verify_vc_id)
        if not rows:
            return

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

    except Exception as e:
        print("⚠️ _maybe_finish_vc_sessions_after_voice_change error:", repr(e))


# Guard to avoid double-modlog spam when we auto-remove Unverified
_AUTO_UV_REMOVAL_TS: Dict[Tuple[int, int], Any] = {}

# Guard to avoid duplicate watchdog tasks for the same member
_JOIN_VERIFY_TASKS: Dict[Tuple[int, int], asyncio.Task] = {}


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


async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:
    try:
        if getattr(member, "bot", False):
            return

        guild = member.guild

        try:
            fresh_member = guild.get_member(member.id)
            if fresh_member is None:
                fresh_member = await guild.fetch_member(member.id)
            member = fresh_member
        except Exception:
            pass

        if _member_has_any_safe_access_role(member, include_unverified=True):
            print(f"ℹ️ [VERIFY] Fail-closed skipped for {member.id}; member already has a safe role state.")
            return

        embed = discord.Embed(
            title="🚨 Verification Safety Fail-Closed",
            description="A joining member could not be placed into a safe verification state.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"{member.mention} (`{member.id}`)\n`{member}`",
            inline=False,
        )
        embed.add_field(
            name="Reason",
            value=f"```{str(reason or 'Unknown reason')[:900]}```",
            inline=False,
        )
        embed.add_field(
            name="Action",
            value="Member will be kicked to avoid remaining in a neutral verification state.",
            inline=False,
        )

        try:
            await _post_modlog(guild, embed, view=build_quick_mod_view(member.id))
        except Exception as e:
            print(f"⚠️ [VERIFY] Failed posting fail-closed modlog for {member.id}: {repr(e)}")

        try:
            await member.kick(reason=f"Verification fail-closed: {str(reason)[:400]}")
            print(f"🚨 [VERIFY] Kicked member {member.id} due to failed safe-state assignment.")
        except discord.Forbidden as e:
            print(f"❌ [VERIFY] Could not kick member {member.id}; missing permission. error={repr(e)}")
        except discord.HTTPException as e:
            print(f"⚠️ [VERIFY] HTTPException kicking member {member.id} during fail-closed. error={repr(e)}")
        except Exception as e:
            print(f"⚠️ [VERIFY] Unexpected error kicking member {member.id} during fail-closed. error={repr(e)}")

    except Exception as e:
        print(f"⚠️ _handle_join_verification_failure error: {repr(e)}")
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _ensure_unverified_on_join(member: discord.Member) -> bool:
    try:
        if getattr(member, "bot", False):
            return False

        guild = member.guild
        uv_id = int(UNVERIFIED_ROLE_ID or 0)
        v_id = int(VERIFIED_ROLE_ID or 0)
        resident_id = int(RESIDENT_ROLE_ID or 0) if RESIDENT_ROLE_ID else 0
        staff_id = int(STAFF_ROLE_ID or 0) if STAFF_ROLE_ID else 0

        if not uv_id:
            print("⚠️ [VERIFY] UNVERIFIED_ROLE_ID missing or invalid.")
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


async def _join_verification_watchdog(guild_id: int, member_id: int) -> None:
    key = (int(guild_id), int(member_id))

    try:
        total_attempts = 5

        for attempt in range(1, total_attempts + 1):
            try:
                guild = bot.get_guild(int(guild_id))
                if guild is None:
                    try:
                        await asyncio.sleep(2.0)
                    except Exception:
                        pass
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

                has_verified = False
                has_unverified = False
                has_staff = False
                has_resident = False

                try:
                    if VERIFIED_ROLE_ID:
                        has_verified = _member_has_role_id(member, int(VERIFIED_ROLE_ID))
                except Exception:
                    pass

                try:
                    if STAFF_ROLE_ID:
                        has_staff = _member_has_role_id(member, int(STAFF_ROLE_ID))
                except Exception:
                    pass

                try:
                    if RESIDENT_ROLE_ID:
                        has_resident = _member_has_role_id(member, int(RESIDENT_ROLE_ID))
                except Exception:
                    pass

                try:
                    if UNVERIFIED_ROLE_ID:
                        has_unverified = _member_has_role_id(member, int(UNVERIFIED_ROLE_ID))
                except Exception:
                    pass

                if not has_verified and not has_staff and not has_resident and not has_unverified:
                    try:
                        added = await _ensure_unverified_on_join(member)
                        if added:
                            has_unverified = True
                            try:
                                fallback_channel = await _resolve_unverified_chat_channel(guild)
                                started = await start_join_grace_then_kick_timer_for_member(
                                    member,
                                    source_channel=fallback_channel,
                                )
                                print(
                                    f"⏳ [VERIFY-WATCHDOG] join grace timer start "
                                    f"guild={guild_id} member={member_id} started={started} "
                                    f"channel={getattr(fallback_channel, 'id', None)}"
                                )
                            except Exception as e:
                                print(
                                    f"⚠️ [VERIFY-WATCHDOG] failed to start join-grace timer "
                                    f"guild={guild_id} member={member_id} attempt={attempt} error={repr(e)}"
                                )
                    except Exception as e:
                        print(
                            f"⚠️ [VERIFY-WATCHDOG] _ensure_unverified_on_join failed "
                            f"guild={guild_id} member={member_id} attempt={attempt} error={repr(e)}"
                        )

                try:
                    await _sync_member_to_supabase(member, in_guild=True)
                except Exception:
                    pass

                if has_verified or has_staff or has_resident or has_unverified:
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
# ✅ MEMBER LOGS / RAID DETECTION / ALT CLUSTERING
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

        JOIN_TIMES[gid].append(now_utc())
        RAID_RECENT_JOINERS[gid][int(member.id)] = now_utc()

        age_days = _account_age_days(member)
        fp = _behavior_fingerprint(member)

        try:
            risk_profile = track_member_join_risk(member)
        except Exception:
            risk_profile = {
                "score": 0,
                "level": "low",
                "reasons": [],
                "same_fingerprint_count": 0,
                "similar_name_count": 0,
                "same_age_bucket_count": 0,
                "burst_count": 0,
                "fingerprint": fp,
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
                f"(`{risk_profile.get('level', 'low')}`)\n"
                f"fp matches: `{risk_profile.get('same_fingerprint_count', 0)}` • "
                f"name matches: `{risk_profile.get('similar_name_count', 0)}` • "
                f"age bucket matches: `{risk_profile.get('same_age_bucket_count', 0)}` • "
                f"burst: `{risk_profile.get('burst_count', 0)}`"
            ),
            inline=False,
        )
        reasons = list(risk_profile.get("reasons") or [])
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
        except Exception:
            pass

        try:
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        except Exception:
            pass

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
            await _ensure_unverified_on_join(member)
        except Exception as e:
            print("⚠️ _ensure_unverified_on_join wrapper error:", e)
            try:
                traceback.print_exc()
            except Exception:
                pass

        try:
            if not getattr(member, "bot", False):
                _schedule_join_verification_watchdog(member)
        except Exception as e:
            print(f"⚠️ Failed to schedule join verification watchdog for {member.id}: {repr(e)}")

        try:
            await _sync_member_to_supabase(
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

        try:
            if not getattr(member, "bot", False):
                fresh_member = guild.get_member(member.id)
                if fresh_member is None:
                    try:
                        fresh_member = await guild.fetch_member(member.id)
                    except Exception:
                        fresh_member = member

                if isinstance(fresh_member, discord.Member) and _member_is_pending_verification(fresh_member):
                    fallback_channel = await _resolve_unverified_chat_channel(guild)
                    started = await start_join_grace_then_kick_timer_for_member(
                        fresh_member,
                        source_channel=fallback_channel,
                    )
                    print(
                        f"⏳ [VERIFY] join grace timer start "
                        f"guild={gid} member={fresh_member.id} started={started} "
                        f"fallback_channel={getattr(fallback_channel, 'id', None)}"
                    )
        except Exception as e:
            print(f"⚠️ Failed to start join grace timer for {member.id}: {repr(e)}")

        if (
            not getattr(member, "bot", False)
            and not _member_has_any_safe_access_role(member, include_unverified=True)
        ):
            await _handle_join_verification_failure(
                member,
                "Unverified could not be assigned during on_member_join and member has no safe access role.",
            )
            return

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
                await _mark_member_left(member)
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

        ban_logged = False
        try:
            ban_logged = await maybe_log_recent_ban(guild, member)
        except Exception:
            ban_logged = False

        if ban_logged:
            try:
                RUNTIME_STATS["member_bans_detected"] += 1
            except Exception:
                pass

            try:
                await _mark_member_left(member)
            except Exception:
                pass

            try:
                await _auto_close_verification_ticket_for_departed_member(
                    member,
                    leave_reason="AUTO CLOSED: user was banned during verification",
                )
            except Exception:
                pass
            return

        try:
            RUNTIME_STATS["member_leaves_detected"] += 1
        except Exception:
            pass

        embed = discord.Embed(title="📤 Member Left", color=discord.Color.blurple())
        embed.add_field(name="User", value=f"`{member}` (`{member.id}`)", inline=False)
        await _post_modlog(guild, embed, view=None)

        try:
            await _mark_member_left(member)
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

        if logged:
            return

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
                            await _sync_member_to_supabase(after, in_guild=True)
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

                    if uv_id:
                        has_unverified = _member_has_role_id(after, uv_id)
                        has_verified = _member_has_role_id(after, v_id) if v_id else False
                        has_resident = _member_has_role_id(after, resident_id) if resident_id else False
                        has_staff = _member_has_role_id(after, staff_id) if staff_id else False

                        non_default_roles = [r for r in (after.roles or []) if not r.is_default()]
                        has_no_real_roles = len(non_default_roles) == 0

                        if (
                            has_no_real_roles
                            and not has_unverified
                            and not has_verified
                            and not has_resident
                            and not has_staff
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
            await _sync_member_to_supabase(after, in_guild=True)
        except Exception:
            pass

        try:
            if not getattr(after, "bot", False):
                if _member_has_any_safe_access_role(after, include_unverified=False):
                    await cancel_verification_wait_timers_for_member(gid, int(after.id))
        except Exception as e:
            print(f"⚠️ Failed cancelling verification wait timers on member update for {after.id}: {repr(e)}")

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
            await _sync_member_to_supabase(member, in_guild=True)
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
# ✅ Startup wiring (runs once)
# ============================================================

@bot.event
async def on_ready():
    try:
        if getattr(bot, "_vc_sweeper_task", None) and not bot._vc_sweeper_task.done():  # type: ignore[attr-defined]
            try:
                if not getattr(bot, "_invite_cache_warm_started", False):  # type: ignore[attr-defined]
                    try:
                        bot._invite_cache_warm_started = True  # type: ignore[attr-defined]
                    except Exception:
                        pass

                    async def _run_invite_cache_warm():
                        try:
                            await _warm_all_guild_invite_caches()
                        except Exception as e:
                            print("⚠️ background invite cache warm error:", e)
                            try:
                                traceback.print_exc()
                            except Exception:
                                pass

                    asyncio.create_task(_run_invite_cache_warm())

                if not getattr(bot, "_initial_member_sync_started", False):  # type: ignore[attr-defined]
                    try:
                        bot._initial_member_sync_started = True  # type: ignore[attr-defined]
                    except Exception:
                        pass

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

                    asyncio.create_task(_run_startup_member_sync())

                if not getattr(bot, "_stale_verification_reconcile_started", False):  # type: ignore[attr-defined]
                    try:
                        bot._stale_verification_reconcile_started = True  # type: ignore[attr-defined]
                    except Exception:
                        pass

                    async def _run_stale_verification_reconcile():
                        try:
                            await _reconcile_stale_open_verification_tickets()
                        except Exception as e:
                            print("⚠️ background stale verification reconciliation error:", e)
                            try:
                                traceback.print_exc()
                            except Exception:
                                pass

                    asyncio.create_task(_run_stale_verification_reconcile())

                try:
                    started = await ensure_channel_cleanup_worker_started()
                    print(f"🧹 Channel cleanup worker status: started={started}")
                except Exception as e:
                    print(f"⚠️ Failed starting channel cleanup worker: {repr(e)}")
            except Exception:
                pass
            return
    except Exception:
        pass

    try:
        task = asyncio.create_task(vc_sweeper_loop(bot))
        try:
            bot._vc_sweeper_task = task  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        pass

    try:
        if not getattr(bot, "_invite_cache_warm_started", False):  # type: ignore[attr-defined]
            try:
                bot._invite_cache_warm_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            async def _run_invite_cache_warm():
                try:
                    await _warm_all_guild_invite_caches()
                except Exception as e:
                    print("⚠️ on_ready invite cache warm error:", e)
                    try:
                        traceback.print_exc()
                    except Exception:
                        pass

            asyncio.create_task(_run_invite_cache_warm())
    except Exception as e:
        print("⚠️ on_ready invite cache warm scheduling error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass

    try:
        if not getattr(bot, "_initial_member_sync_started", False):  # type: ignore[attr-defined]
            try:
                bot._initial_member_sync_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            async def _run_startup_member_sync():
                try:
                    await _initial_member_sync_sweep()
                    try:
                        bot._initial_member_sync_done = True  # type: ignore[attr-defined]
                    except Exception:
                        pass
                except Exception as e:
                    print("⚠️ on_ready background member sync sweep error:", e)
                    try:
                        traceback.print_exc()
                    except Exception:
                        pass

            asyncio.create_task(_run_startup_member_sync())
    except Exception as e:
        print("⚠️ on_ready member sync scheduling error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass

    try:
        if not getattr(bot, "_stale_verification_reconcile_started", False):  # type: ignore[attr-defined]
            try:
                bot._stale_verification_reconcile_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            async def _run_stale_verification_reconcile():
                try:
                    await _reconcile_stale_open_verification_tickets()
                except Exception as e:
                    print("⚠️ on_ready stale verification reconciliation error:", e)
                    try:
                        traceback.print_exc()
                    except Exception:
                        pass

            asyncio.create_task(_run_stale_verification_reconcile())
    except Exception as e:
        print("⚠️ on_ready stale verification reconciliation scheduling error:", e)
        try:
            traceback.print_exc()
        except Exception:
            pass

    try:
        started = await ensure_channel_cleanup_worker_started()
        print(f"🧹 Channel cleanup worker status: started={started}")
    except Exception as e:
        print(f"⚠️ Failed starting channel cleanup worker: {repr(e)}")