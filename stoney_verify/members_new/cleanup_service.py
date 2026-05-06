from __future__ import annotations

"""Confirmed member cleanup service for Dank Shield.

This module owns actual Discord member removal. The activity scanner only finds
review candidates; this service re-validates everything immediately before a
member is removed.

Safety rules:
- No blind mass purge.
- No action without an explicit confirmation flow.
- Never remove the guild owner, the bot itself, bots by default, staff/admin, or
  protected roles.
- Never remove users locked/skipped from scans unless a future admin-only audit
  workflow explicitly opts into that.
- Re-check guild membership, bot permissions, and role hierarchy at action time.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

try:
    from stoney_verify.guild_config import get_guild_config
except Exception:
    get_guild_config = None  # type: ignore

try:
    from stoney_verify.globals import get_supabase, now_utc
except Exception:
    get_supabase = None  # type: ignore

    def now_utc() -> datetime:  # type: ignore
        return datetime.now(timezone.utc)

try:
    from stoney_verify.members_new.activity_service import is_scan_user_locked
except Exception:
    is_scan_user_locked = None  # type: ignore


@dataclass(frozen=True)
class MemberCleanupRequest:
    guild_id: int
    target_user_id: int
    actor_user_id: int
    reason: str = "Confirmed inactive verified/resident cleanup"
    require_scan_unlocked: bool = True
    protect_bots: bool = True
    protect_staff: bool = True


@dataclass
class MemberCleanupValidation:
    ok: bool
    status: str
    target_user_id: int
    target_display_name: str = "Unknown member"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    member: Optional[discord.Member] = None

    @property
    def reason_text(self) -> str:
        return " ".join(self.reasons).strip() or self.status


@dataclass
class MemberCleanupResult:
    ok: bool
    status: str
    target_user_id: int
    target_display_name: str = "Unknown member"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action_at: datetime = field(default_factory=now_utc)

    @property
    def reason_text(self) -> str:
        return " ".join(self.reasons).strip() or self.status


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass
    return default


def _role_ids_from_value(value: Any) -> set[int]:
    out: set[int] = set()
    try:
        if value is None:
            return out
        if isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            raw = str(value).replace(";", ",").replace(" ", ",")
            raw_items = raw.split(",")
        for item in raw_items:
            rid = _safe_int(item, 0)
            if rid > 0:
                out.add(rid)
    except Exception:
        pass
    return out


def _member_role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(role.id) for role in member.roles or [] if int(role.id) != int(member.guild.default_role.id)}
    except Exception:
        return set()


async def _load_cleanup_protected_role_ids(guild_id: int) -> set[int]:
    protected: set[int] = set()
    try:
        if get_guild_config is None:
            return protected
        cfg = await get_guild_config(int(guild_id))  # type: ignore[misc]
        for key in (
            "staff_role_id",
            "vc_staff_role_id",
            "server_control_role_id",
            "bot_manager_role_id",
            "inactive_cleanup_protected_role_ids",
            "protected_role_ids",
        ):
            protected |= _role_ids_from_value(_cfg_value(cfg, key, None))
    except Exception:
        pass
    return protected


def _is_staff_like(member: discord.Member, protected_role_ids: set[int]) -> bool:
    try:
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_roles or perms.kick_members or perms.ban_members or perms.moderate_members:
            return True
    except Exception:
        pass
    try:
        return bool(_member_role_ids(member).intersection(protected_role_ids))
    except Exception:
        return False


def _actor_can_confirm(actor: discord.Member) -> tuple[bool, str]:
    try:
        perms = actor.guild_permissions
        if perms.administrator or perms.manage_guild or perms.kick_members:
            return True, ""
        return False, "You need Administrator, Manage Server, or Kick Members to confirm cleanup."
    except Exception:
        return False, "Could not verify your permissions."


async def validate_member_cleanup(guild: discord.Guild, request: MemberCleanupRequest) -> MemberCleanupValidation:
    target_id = int(request.target_user_id)
    reasons: list[str] = []
    warnings: list[str] = []

    target = guild.get_member(target_id)
    if target is None:
        try:
            target = await guild.fetch_member(target_id)
        except Exception:
            target = None
    if target is None:
        return MemberCleanupValidation(
            ok=False,
            status="Member not found",
            target_user_id=target_id,
            reasons=["That user is no longer in this server, so there is nothing to clean up."],
        )

    display_name = str(getattr(target, "display_name", None) or getattr(target, "name", None) or target_id)

    actor = guild.get_member(int(request.actor_user_id))
    if actor is None:
        try:
            actor = await guild.fetch_member(int(request.actor_user_id))
        except Exception:
            actor = None
    if actor is None:
        return MemberCleanupValidation(False, "Actor not found", target_id, display_name, ["Could not verify the staff member confirming this cleanup."], member=target)
    actor_ok, actor_reason = _actor_can_confirm(actor)
    if not actor_ok:
        return MemberCleanupValidation(False, "Missing staff permission", target_id, display_name, [actor_reason], member=target)

    me = guild.me
    if me is None:
        return MemberCleanupValidation(False, "Bot member missing", target_id, display_name, ["Could not resolve Dank Shield inside this server."], member=target)

    if target_id == int(guild.owner_id):
        reasons.append("Server owner is protected and can never be removed by cleanup.")
    if target_id == int(me.id):
        reasons.append("Dank Shield will never remove itself.")
    if getattr(target, "bot", False) and request.protect_bots:
        reasons.append("Bot accounts are protected by default.")

    try:
        if not me.guild_permissions.kick_members:
            reasons.append("Dank Shield is missing Kick Members permission.")
    except Exception:
        reasons.append("Could not verify Dank Shield's Kick Members permission.")

    try:
        if guild.owner_id != me.id and target.top_role >= me.top_role:
            reasons.append("Target member is above or equal to Dank Shield's top role.")
    except Exception:
        reasons.append("Could not verify Discord role hierarchy for the target member.")

    try:
        if target.top_role >= actor.top_role and int(actor.id) != int(guild.owner_id):
            warnings.append("The target is above or equal to your top role. Dank Shield will still require its own role hierarchy to pass.")
    except Exception:
        pass

    protected_role_ids = await _load_cleanup_protected_role_ids(int(guild.id))
    if request.protect_staff and _is_staff_like(target, protected_role_ids):
        reasons.append("Target appears to be staff/admin/protected by role or permissions.")

    if request.require_scan_unlocked and is_scan_user_locked is not None:
        try:
            if await is_scan_user_locked(int(guild.id), target_id):
                reasons.append("Target is locked/skipped from cleanup scans. Unlock them first if this is intentional.")
        except Exception:
            warnings.append("Could not verify scan-lock status; continuing with other safety checks.")

    if reasons:
        return MemberCleanupValidation(False, "Blocked by safety checks", target_id, display_name, reasons, warnings, target)

    return MemberCleanupValidation(
        ok=True,
        status="Ready for confirmed cleanup",
        target_user_id=target_id,
        target_display_name=display_name,
        reasons=["All action-time safety checks passed."],
        warnings=warnings,
        member=target,
    )


def _insert_activity_event_sync(payload: dict[str, Any]) -> tuple[bool, str]:
    if get_supabase is None:
        return False, "Supabase unavailable; cleanup event was not saved."
    sb = get_supabase()
    if sb is None:
        return False, "Supabase unavailable; cleanup event was not saved."
    try:
        sb.table("activity_feed_events").insert(payload).execute()
        return True, "Saved cleanup event."
    except Exception:
        return False, "Could not save cleanup event to activity feed."


async def record_cleanup_event(
    *,
    guild_id: int,
    actor_user_id: int,
    target_user_id: int,
    status: str,
    reason: str,
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    payload = {
        "guild_id": str(int(guild_id)),
        "event_type": "member_cleanup",
        "actor_id": str(int(actor_user_id)),
        "target_id": str(int(target_user_id)),
        "message": str(reason or status)[:1000],
        "metadata": dict(metadata or {}),
        "meta": dict(metadata or {}),
        "created_at": now_utc().isoformat(),
    }
    return await __import__("asyncio").to_thread(_insert_activity_event_sync, payload)


async def execute_member_cleanup(guild: discord.Guild, request: MemberCleanupRequest) -> MemberCleanupResult:
    validation = await validate_member_cleanup(guild, request)
    if not validation.ok or validation.member is None:
        await record_cleanup_event(
            guild_id=int(guild.id),
            actor_user_id=int(request.actor_user_id),
            target_user_id=int(request.target_user_id),
            status="blocked",
            reason=validation.reason_text,
            metadata={"status": validation.status, "warnings": validation.warnings},
        )
        return MemberCleanupResult(False, validation.status, validation.target_user_id, validation.target_display_name, validation.reasons, validation.warnings)

    reason = str(request.reason or "Confirmed inactive verified/resident cleanup")[:450]
    audit_reason = f"Dank Shield confirmed member cleanup by {request.actor_user_id}: {reason}"
    try:
        await validation.member.kick(reason=audit_reason[:512])
        await record_cleanup_event(
            guild_id=int(guild.id),
            actor_user_id=int(request.actor_user_id),
            target_user_id=int(request.target_user_id),
            status="removed",
            reason=reason,
            metadata={
                "target_display_name": validation.target_display_name,
                "warnings": validation.warnings,
                "source": "confirmed_member_cleanup",
            },
        )
        return MemberCleanupResult(
            True,
            "Member removed",
            validation.target_user_id,
            validation.target_display_name,
            ["Member was removed after explicit confirmation and final safety checks."],
            validation.warnings,
        )
    except discord.Forbidden:
        reasons = ["Discord rejected the cleanup action. Check Kick Members permission and bot role position."]
    except discord.HTTPException as e:
        reasons = [f"Discord rejected the cleanup action: {getattr(e, 'text', '') or type(e).__name__}."]
    except Exception as e:
        reasons = [f"Cleanup failed unexpectedly: {type(e).__name__}."]

    await record_cleanup_event(
        guild_id=int(guild.id),
        actor_user_id=int(request.actor_user_id),
        target_user_id=int(request.target_user_id),
        status="failed",
        reason=" ".join(reasons),
        metadata={"target_display_name": validation.target_display_name, "warnings": validation.warnings},
    )
    return MemberCleanupResult(False, "Cleanup failed", validation.target_user_id, validation.target_display_name, reasons, validation.warnings)


__all__ = [
    "MemberCleanupRequest",
    "MemberCleanupResult",
    "MemberCleanupValidation",
    "execute_member_cleanup",
    "record_cleanup_event",
    "validate_member_cleanup",
]
