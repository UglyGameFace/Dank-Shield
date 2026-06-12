from __future__ import annotations

"""Join attribution and join-context persistence service.

This module is the write-side owner for member join context:
- invite / vanity attribution
- entry truth quality
- member_joins rows
- member_events join evidence
- guild_members join-source patching

The read-side context aggregator remains tickets_new/member_context_service.py.
"""

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import get_supabase, now_utc

try:
    from .sync_service import _build_risk_payload_from_profile
except Exception:  # pragma: no cover - defensive fallback for startup import order
    def _build_risk_payload_from_profile(risk_profile: Optional[Dict[str, Any]], *, now_iso: Optional[str] = None) -> Dict[str, Any]:
        return {}


_INVITE_USES_CACHE: Dict[int, Dict[str, int]] = {}
_INVITE_META_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}
_VANITY_USES_CACHE: Dict[int, Optional[int]] = {}


def _log(message: str) -> None:
    try:
        print(f"🧭 join_context_service {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ join_context_service {message}")
    except Exception:
        pass


def _sync_iso_now() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any) -> str:
    try:
        return str(value or "")
    except Exception:
        return ""


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


def _safe_member_avatar_url(member: discord.Member) -> Optional[str]:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return None


async def _run_blocking_db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


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


def join_truth_quality(
    entry_method: str,
    *,
    invite_code: Optional[str] = None,
    invited_by: Optional[str] = None,
) -> Tuple[str, int, str]:
    method = str(entry_method or "").strip().lower()

    if method == "invite" and (invite_code or invited_by):
        return ("confirmed", 95, "Invite usage delta identified a specific invite.")
    if method == "vanity_invite":
        return ("confirmed", 90, "Vanity invite usage increased.")
    if method in {"vouched", "manual_verification", "ticket_verification"}:
        return ("confirmed", 85, "Entry source came from an explicit staff/ticket action.")
    if method == "invite_tracking_unavailable":
        return ("unknown", 15, "Invite tracking was unavailable due to permissions or API failure.")
    if method == "invite_cache_warming":
        return ("partial", 35, "Invite cache was still warming; attribution should not be trusted as exact.")
    if method == "invite_unresolved":
        return ("partial", 45, "Invite cache existed, but the usage delta did not identify one invite.")
    return ("unknown", 20, "Join attribution is unknown.")


def build_join_context(
    *,
    entry_method: str,
    join_source: str,
    verification_source: Optional[str] = None,
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
    quality, confidence, quality_reason = join_truth_quality(
        entry_method,
        invite_code=invite_code,
        invited_by=invited_by,
    )
    return {
        "entry_method": str(entry_method or "").strip() or "unknown_join",
        "join_source": str(join_source or "").strip() or "unknown_join",
        "verification_source": str(verification_source or join_source or "").strip() or "unknown_join",
        "invite_code": str(invite_code or "").strip() or None,
        "invited_by": str(invited_by or "").strip() or None,
        "invited_by_name": str(invited_by_name or "").strip() or None,
        "vouched_by": str(vouched_by or "").strip() or None,
        "vouched_by_name": str(vouched_by_name or "").strip() or None,
        "approved_by": str(approved_by or "").strip() or None,
        "approved_by_name": str(approved_by_name or "").strip() or None,
        "entry_reason": str(entry_reason or "").strip() or None,
        "approval_reason": str(approval_reason or "").strip() or None,
        "join_note": str(join_note or "").strip() or None,
        "channel_id": str(channel_id or "").strip() or None,
        "channel_name": str(channel_name or "").strip() or None,
        "vanity_used": bool(vanity_used),
        "source_ticket_id": str(source_ticket_id or "").strip() or None,
        "entry_truth_quality": quality,
        "entry_confidence": confidence,
        "entry_quality_reason": quality_reason,
        "entry_conflict": False,
    }


def _invite_inviter_id(invite: discord.Invite) -> Optional[str]:
    try:
        inviter = getattr(invite, "inviter", None)
        if inviter and getattr(inviter, "id", None):
            return str(inviter.id)
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


def invite_meta(invite: discord.Invite) -> Dict[str, Any]:
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

    code = _safe_str(getattr(invite, "code", "")).strip()
    return {
        "code": code,
        "uses": uses,
        "max_uses": max_uses,
        "temporary": temporary,
        "inviter_id": _invite_inviter_id(invite),
        "inviter_name": _invite_inviter_name(invite),
        "channel_id": _invite_channel_id(invite),
        "channel_name": _invite_channel_name(invite),
    }


async def warm_invite_cache_for_guild(guild: discord.Guild) -> bool:
    gid = int(getattr(guild, "id", 0) or 0)
    if gid <= 0:
        return False

    current_uses: Dict[str, int] = {}
    current_meta: Dict[str, Dict[str, Any]] = {}
    vanity_uses: Optional[int] = None

    try:
        invites = await guild.invites()
        for invite in invites:
            meta = invite_meta(invite)
            code = str(meta.get("code") or "").strip()
            if not code:
                continue
            current_uses[code] = int(meta.get("uses") or 0)
            current_meta[code] = meta
    except Exception as e:
        _warn(f"invite cache warm failed guild={gid}: {e!r}")
        return False

    try:
        vanity = await guild.vanity_invite()
        if vanity is not None:
            vanity_uses = int(getattr(vanity, "uses", 0) or 0)
    except Exception:
        vanity_uses = None

    _INVITE_USES_CACHE[gid] = current_uses
    _INVITE_META_CACHE[gid] = current_meta
    _VANITY_USES_CACHE[gid] = vanity_uses
    return True


async def detect_join_entry_context(member: discord.Member) -> Dict[str, Any]:
    guild = member.guild
    gid = int(guild.id)

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
            meta = invite_meta(invite)
            code = str(meta.get("code") or "").strip()
            if not code:
                continue
            current_uses[code] = int(meta.get("uses") or 0)
            current_meta[code] = meta
    except discord.Forbidden:
        invites_ok = False
    except Exception as e:
        _warn(f"join detect invite fetch failed guild={gid}: {e!r}")
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
        context = build_join_context(
            entry_method="invite",
            join_source="discord_invite",
            verification_source="invite_join",
            invite_code=best_code,
            invited_by=inviter_id,
            invited_by_name=inviter_name,
            entry_reason=(
                f"Joined using invite `{best_code}`"
                + (f" created by {inviter_name}" if inviter_name else "")
                + (f" in #{channel_name}" if channel_name else "")
                + "."
            ),
            join_note=(
                f"Invite `{best_code}`"
                + (f" • inviter: {inviter_name}" if inviter_name else "")
                + (f" • channel: #{channel_name}" if channel_name else "")
            ),
            channel_id=channel_id,
            channel_name=channel_name,
            vanity_used=False,
        )
        _INVITE_USES_CACHE[gid] = current_uses
        _INVITE_META_CACHE[gid] = current_meta
        _VANITY_USES_CACHE[gid] = vanity_uses
        return context

    if old_vanity_uses is not None and vanity_uses is not None and int(vanity_uses) > int(old_vanity_uses):
        context = build_join_context(
            entry_method="vanity_invite",
            join_source="vanity_invite",
            verification_source="vanity_invite",
            invite_code=vanity_code or "vanity",
            invited_by_name="Vanity URL",
            entry_reason="Joined using the server vanity URL.",
            join_note="Joined through vanity invite tracking.",
            vanity_used=True,
        )
        _INVITE_USES_CACHE[gid] = current_uses
        _INVITE_META_CACHE[gid] = current_meta
        _VANITY_USES_CACHE[gid] = vanity_uses
        return context

    if not invites_ok:
        default_context = build_join_context(
            entry_method="invite_tracking_unavailable",
            join_source="invite_tracking_unavailable",
            verification_source="invite_tracking_unavailable",
            entry_reason="Joined, but the bot could not inspect invite usage. Check Manage Server / invite read permissions.",
            join_note="Invite tracking unavailable for this join.",
            vanity_used=False,
        )
    elif not old_uses and current_uses:
        default_context = build_join_context(
            entry_method="invite_cache_warming",
            join_source="invite_cache_warming",
            verification_source="invite_cache_warming",
            entry_reason="Joined before the bot had a usable invite baseline cache. Future joins should attribute correctly after cache warm-up.",
            join_note="Invite cache was still warming when this member joined.",
            vanity_used=False,
        )
    else:
        default_context = build_join_context(
            entry_method="invite_unresolved",
            join_source="invite_unresolved",
            verification_source="invite_unresolved",
            entry_reason="Joined, but invite attribution could not be resolved from the invite delta.",
            join_note="Invite delta did not clearly resolve this join.",
            vanity_used=False,
        )

    _INVITE_USES_CACHE[gid] = current_uses
    _INVITE_META_CACHE[gid] = current_meta
    _VANITY_USES_CACHE[gid] = vanity_uses
    return default_context


async def persist_member_join_context(
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
        context = await detect_join_entry_context(member)
        risk_payload = _build_risk_payload_from_profile(risk_profile, now_iso=now_iso)

        guild_member_patch = {
            "entry_method": context.get("entry_method"),
            "join_source": context.get("join_source"),
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
            "source_ticket_id": context.get("source_ticket_id"),
            "entry_truth_quality": context.get("entry_truth_quality"),
            "entry_confidence": context.get("entry_confidence"),
            "entry_quality_reason": context.get("entry_quality_reason"),
            "entry_conflict": bool(context.get("entry_conflict", False)),
            "joined_at": joined_at,
            "vanity_used": bool(context.get("vanity_used", False)),
            "synced_at": now_iso,
            "updated_at": now_iso,
            "last_seen_at": now_iso,
            **risk_payload,
        }

        try:
            await _guild_members_update_member_async(sb, guild_id, user_id, guild_member_patch)
        except Exception as e:
            _warn(f"guild_members join-context patch failed guild={guild_id} user={user_id}: {e!r}")

        join_row = {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": _safe_member_username(member),
            "display_name": _safe_member_display_name(member),
            "avatar_url": _safe_member_avatar_url(member),
            "joined_at": joined_at,
            "entry_method": context.get("entry_method"),
            "join_source": context.get("join_source"),
            "verification_source": context.get("verification_source"),
            "invite_code": context.get("invite_code"),
            "invited_by": context.get("invited_by"),
            "invited_by_name": context.get("invited_by_name"),
            "vouched_by": context.get("vouched_by"),
            "vouched_by_name": context.get("vouched_by_name"),
            "approved_by": context.get("approved_by"),
            "approved_by_name": context.get("approved_by_name"),
            "join_note": context.get("join_note"),
            "source_ticket_id": context.get("source_ticket_id"),
            "entry_truth_quality": context.get("entry_truth_quality"),
            "entry_confidence": context.get("entry_confidence"),
            "entry_quality_reason": context.get("entry_quality_reason"),
            "entry_conflict": bool(context.get("entry_conflict", False)),
            "vanity_used": bool(context.get("vanity_used", False)),
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
            _warn(f"member_joins insert failed guild={guild_id} user={user_id}: {e!r}")

        try:
            await _member_events_insert_async(
                sb,
                {
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "actor_id": context.get("invited_by") or context.get("approved_by") or context.get("vouched_by"),
                    "actor_name": context.get("invited_by_name") or context.get("approved_by_name") or context.get("vouched_by_name") or "System",
                    "event_type": "member_joined",
                    "title": "Member Joined",
                    "reason": context.get("entry_reason"),
                    "metadata": {
                        "invite_code": context.get("invite_code"),
                        "entry_method": context.get("entry_method"),
                        "join_source": context.get("join_source"),
                        "verification_source": context.get("verification_source"),
                        "channel_id": context.get("channel_id"),
                        "channel_name": context.get("channel_name"),
                        "joined_at": joined_at,
                        "vanity_used": bool(context.get("vanity_used", False)),
                        "entry_truth_quality": context.get("entry_truth_quality"),
                        "entry_confidence": context.get("entry_confidence"),
                        "entry_quality_reason": context.get("entry_quality_reason"),
                        "entry_conflict": bool(context.get("entry_conflict", False)),
                        "invited_by": context.get("invited_by"),
                        "invited_by_name": context.get("invited_by_name"),
                        "vouched_by": context.get("vouched_by"),
                        "vouched_by_name": context.get("vouched_by_name"),
                        "approved_by": context.get("approved_by"),
                        "approved_by_name": context.get("approved_by_name"),
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
            _warn(f"member_events insert failed guild={guild_id} user={user_id}: {e!r}")
    except Exception as e:
        _warn(f"persist_member_join_context error: {e!r}")
        try:
            traceback.print_exc()
        except Exception:
            pass


__all__ = [
    "build_join_context",
    "detect_join_entry_context",
    "invite_meta",
    "join_truth_quality",
    "persist_member_join_context",
    "warm_invite_cache_for_guild",
]
