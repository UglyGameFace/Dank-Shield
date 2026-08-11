from __future__ import annotations

"""Canonical confirmed inactive-member cleanup service.

The scanner finds conservative candidates; this service revalidates the target
immediately before removal, runs the mutation through the shared operation
queue, records per-target audit evidence, and can persist/post one bulk-run
summary. No startup monkeypatch is required.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import discord

from stoney_verify.operation_queue import run_exclusive, with_retry

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
    from stoney_verify.members_new.activity_service import (
        InactiveScanOptions,
        is_scan_user_locked,
        scan_inactive_members,
    )
except Exception:
    InactiveScanOptions = None  # type: ignore
    is_scan_user_locked = None  # type: ignore
    scan_inactive_members = None  # type: ignore


@dataclass(frozen=True)
class MemberCleanupRequest:
    guild_id: int
    target_user_id: int
    actor_user_id: int
    reason: str = "Confirmed inactive verified/resident cleanup"
    require_scan_unlocked: bool = True
    protect_bots: bool = True
    protect_staff: bool = True
    inactive_days: int = 90
    require_authoritative_inactivity: bool = True


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


@dataclass
class MemberCleanupRunSummary:
    guild_id: int
    actor_user_id: int
    mode: str
    attempted: int
    removed: int
    blocked: int
    failed: int
    skipped: int
    inactive_days: int
    reason: str
    results: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime = field(default_factory=now_utc)
    finished_at: datetime = field(default_factory=now_utc)
    persisted: bool = False
    modlog_posted: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "guild_id": str(self.guild_id),
            "actor_user_id": str(self.actor_user_id),
            "mode": self.mode,
            "attempted": self.attempted,
            "removed": self.removed,
            "blocked": self.blocked,
            "failed": self.failed,
            "skipped": self.skipped,
            "inactive_days": self.inactive_days,
            "reason": self.reason,
            "results": list(self.results[:250]),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
        }


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
        raw_items = value if isinstance(value, (list, tuple, set)) else str(value or "").replace(";", ",").replace(" ", ",").split(",")
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
            "staff_role_id", "vc_staff_role_id", "server_control_role_id", "bot_manager_role_id",
            "inactive_cleanup_protected_role_ids", "protected_role_ids",
        ):
            protected |= _role_ids_from_value(_cfg_value(cfg, key, None))
    except Exception:
        pass
    return protected


async def _load_bot_manager_role_ids(guild_id: int) -> set[int]:
    roles: set[int] = set()
    try:
        if get_guild_config is None:
            return roles
        cfg = await get_guild_config(int(guild_id))  # type: ignore[misc]
        for key in ("bot_manager_role_id", "server_control_role_id", "bot_manager_role_ids"):
            roles |= _role_ids_from_value(_cfg_value(cfg, key, None))
    except Exception:
        pass
    return roles


def _is_staff_like(member: discord.Member, protected_role_ids: set[int]) -> bool:
    try:
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_roles or perms.kick_members or perms.ban_members or perms.moderate_members:
            return True
    except Exception:
        pass
    return bool(_member_role_ids(member).intersection(protected_role_ids))


def _actor_can_confirm(actor: discord.Member) -> tuple[bool, str]:
    try:
        perms = actor.guild_permissions
        if int(actor.id) == int(actor.guild.owner_id) or perms.administrator or perms.manage_guild or perms.kick_members:
            return True, ""
        return False, "You need Administrator, Manage Server, or Kick Members to confirm cleanup."
    except Exception:
        return False, "Could not verify your permissions."


async def actor_can_use_no_confirm(actor: discord.Member) -> tuple[bool, str]:
    """No-confirm is intentionally narrower than ordinary cleanup permission."""
    try:
        if int(actor.id) == int(actor.guild.owner_id):
            return True, "Server owner"
        if bool(actor.guild_permissions.administrator):
            return True, "Administrator"
        manager_roles = await _load_bot_manager_role_ids(int(actor.guild.id))
        if manager_roles and _member_role_ids(actor).intersection(manager_roles):
            return True, "Bot Manager"
        return False, "Only the server owner, an Administrator, or a configured Bot Manager can disable mass-cleanup confirmation."
    except Exception:
        return False, "Could not verify no-confirm authorization."


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
        return MemberCleanupValidation(False, "Member not found", target_id, reasons=["That user is no longer in this server, so there is nothing to clean up."])

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
            warnings.append("The target is above or equal to your top role. Dank Shield still requires its own hierarchy to pass.")
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

    if request.require_authoritative_inactivity:
        if scan_inactive_members is None or InactiveScanOptions is None:
            reasons.append("Authoritative inactivity validation is unavailable. Cleanup stopped instead of guessing.")
        else:
            try:
                fresh_report = await scan_inactive_members(
                    guild,
                    InactiveScanOptions(
                        inactive_days=max(7, min(int(request.inactive_days), 730)),
                        grace_days=1,
                        include_low_confidence=False,
                        include_medium_confidence=True,
                        include_high_confidence=True,
                        max_candidates=10000,
                        verified_resident_focus=True,
                        use_audit_log_fallback=True,
                        skip_locked_users=True,
                    ),
                )
                if not bool(getattr(fresh_report, "actionable", False)):
                    reasons.append("Fresh inactivity proof is not actionable: " + str(getattr(fresh_report, "actionability_reason", "continuous activity coverage is incomplete")))
                else:
                    fresh_candidate = next((candidate for candidate in fresh_report.candidates if int(candidate.user_id) == target_id), None)
                    if fresh_candidate is None:
                        reasons.append("The member is not present in the fresh inactivity candidate set.")
                    elif not bool(getattr(fresh_candidate, "removable", False)):
                        reasons.append("The fresh scan did not mark this member purge-safe.")
            except Exception as exc:
                reasons.append(f"Fresh inactivity validation failed: {type(exc).__name__}: {str(exc)[:180]}. Cleanup stopped instead of guessing.")

    if reasons:
        return MemberCleanupValidation(False, "Blocked by safety checks", target_id, display_name, reasons, warnings, target)
    return MemberCleanupValidation(True, "Ready for confirmed cleanup", target_id, display_name, ["All action-time safety checks passed."], warnings, target)


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
    *, guild_id: int, actor_user_id: int, target_user_id: int, status: str,
    reason: str, metadata: Optional[dict[str, Any]] = None,
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
    return await asyncio.to_thread(_insert_activity_event_sync, payload)


async def record_cleanup_run_summary(summary: MemberCleanupRunSummary) -> tuple[bool, str]:
    payload = {
        "guild_id": str(int(summary.guild_id)),
        "event_type": "member_cleanup_summary",
        "actor_id": str(int(summary.actor_user_id)),
        "target_id": None,
        "message": (
            f"Inactive cleanup {summary.mode}: removed={summary.removed} blocked={summary.blocked} "
            f"failed={summary.failed} skipped={summary.skipped} attempted={summary.attempted}"
        )[:1000],
        "metadata": summary.payload(),
        "meta": summary.payload(),
        "created_at": summary.finished_at.isoformat(),
    }
    ok, note = await asyncio.to_thread(_insert_activity_event_sync, payload)
    summary.persisted = ok
    return ok, note


async def post_cleanup_run_summary(guild: discord.Guild, summary: MemberCleanupRunSummary) -> bool:
    """Post exactly one human-readable Discord summary when a modlog is configured."""
    try:
        if get_guild_config is None:
            return False
        cfg = await get_guild_config(int(guild.id))  # type: ignore[misc]
        channel_id = 0
        for key in ("modlog_channel_id", "mod_log_channel_id", "audit_log_channel_id", "status_channel_id"):
            channel_id = _safe_int(_cfg_value(cfg, key, 0), 0)
            if channel_id:
                break
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False
        embed = discord.Embed(title="🧹 Inactive member cleanup complete", color=discord.Color.blurple(), timestamp=summary.finished_at)
        embed.description = (
            f"**Mode:** {summary.mode}\n"
            f"**Removed:** {summary.removed} • **Blocked:** {summary.blocked} • **Failed:** {summary.failed} • **Skipped:** {summary.skipped}\n"
            f"**Attempted:** {summary.attempted} • **Inactive threshold:** {summary.inactive_days} days\n"
            f"**Started by:** <@{summary.actor_user_id}>"
        )
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        summary.modlog_posted = True
        return True
    except Exception:
        return False


async def _execute_member_cleanup_unqueued(guild: discord.Guild, request: MemberCleanupRequest) -> MemberCleanupResult:
    validation = await validate_member_cleanup(guild, request)
    if not validation.ok or validation.member is None:
        await record_cleanup_event(
            guild_id=int(guild.id), actor_user_id=int(request.actor_user_id), target_user_id=int(request.target_user_id),
            status="blocked", reason=validation.reason_text,
            metadata={"status": validation.status, "warnings": validation.warnings},
        )
        return MemberCleanupResult(False, validation.status, validation.target_user_id, validation.target_display_name, validation.reasons, validation.warnings)

    reason = str(request.reason or "Confirmed inactive verified/resident cleanup")[:450]
    audit_reason = f"Dank Shield confirmed member cleanup by {request.actor_user_id}: {reason}"
    try:
        await with_retry(
            lambda: validation.member.kick(reason=audit_reason[:512]),
            attempts=3,
            concurrency_key=f"member-cleanup:{guild.id}",
        )
        await record_cleanup_event(
            guild_id=int(guild.id), actor_user_id=int(request.actor_user_id), target_user_id=int(request.target_user_id),
            status="removed", reason=reason,
            metadata={"target_display_name": validation.target_display_name, "warnings": validation.warnings, "source": "confirmed_member_cleanup"},
        )
        return MemberCleanupResult(True, "Member removed", validation.target_user_id, validation.target_display_name, ["Member was removed after final safety checks."], validation.warnings)
    except discord.Forbidden:
        reasons = ["Discord rejected the cleanup action. Check Kick Members permission and bot role position."]
    except discord.HTTPException as exc:
        reasons = [f"Discord rejected the cleanup action: {getattr(exc, 'text', '') or type(exc).__name__}."]
    except Exception as exc:
        reasons = [f"Cleanup failed unexpectedly: {type(exc).__name__}."]

    await record_cleanup_event(
        guild_id=int(guild.id), actor_user_id=int(request.actor_user_id), target_user_id=int(request.target_user_id),
        status="failed", reason=" ".join(reasons),
        metadata={"target_display_name": validation.target_display_name, "warnings": validation.warnings},
    )
    return MemberCleanupResult(False, "Cleanup failed", validation.target_user_id, validation.target_display_name, reasons, validation.warnings)


async def execute_member_cleanup(guild: discord.Guild, request: MemberCleanupRequest) -> MemberCleanupResult:
    """Execute one target through the canonical member-scoped operation queue."""
    state, result, job = await run_exclusive(
        guild_id=int(guild.id),
        actor_id=int(request.actor_user_id),
        operation_type="inactive_purge_execute",
        risk_level="dangerous",
        source="discord_command",
        payload={
            "target_user_id": int(request.target_user_id),
            "inactive_days": int(request.inactive_days),
            "reason": str(request.reason or "")[:200],
        },
        concurrency_class="member_role_mutation",
        concurrency_key=f"cleanup:{int(request.target_user_id)}",
        timeout_seconds=180.0,
        reject_if_busy=True,
        factory=lambda: _execute_member_cleanup_unqueued(guild, request),
    )
    if state == "succeeded" and isinstance(result, MemberCleanupResult):
        return result
    if state == "duplicate":
        return MemberCleanupResult(False, "Duplicate cleanup blocked", int(request.target_user_id), reasons=["This cleanup was already submitted recently. Refresh the member list before trying again."])
    if state == "busy":
        return MemberCleanupResult(False, "Cleanup already running", int(request.target_user_id), reasons=["A cleanup action for this member is already running."])
    return MemberCleanupResult(False, "Cleanup failed", int(request.target_user_id), reasons=[str((job or {}).get("error_message") or "The queued cleanup could not finish.")])


async def finalize_cleanup_run(
    guild: discord.Guild,
    *,
    actor_user_id: int,
    mode: str,
    inactive_days: int,
    reason: str,
    results: Sequence[MemberCleanupResult],
    skipped: int = 0,
    started_at: Optional[datetime] = None,
) -> MemberCleanupRunSummary:
    rows: list[dict[str, Any]] = []
    removed = blocked = failed = 0
    for result in results:
        if result.ok:
            removed += 1
        elif "blocked" in result.status.lower() or "not found" in result.status.lower() or "duplicate" in result.status.lower():
            blocked += 1
        else:
            failed += 1
        rows.append({
            "target_user_id": str(result.target_user_id),
            "target_display_name": result.target_display_name,
            "ok": result.ok,
            "status": result.status,
            "reason": result.reason_text[:500],
        })
    summary = MemberCleanupRunSummary(
        guild_id=int(guild.id), actor_user_id=int(actor_user_id), mode=str(mode or "cleanup"),
        attempted=len(results), removed=removed, blocked=blocked, failed=failed, skipped=max(0, int(skipped)),
        inactive_days=max(1, int(inactive_days)), reason=str(reason or "")[:500], results=rows,
        started_at=started_at or now_utc(), finished_at=now_utc(),
    )
    await record_cleanup_run_summary(summary)
    await post_cleanup_run_summary(guild, summary)
    return summary


__all__ = [
    "MemberCleanupRequest", "MemberCleanupResult", "MemberCleanupRunSummary", "MemberCleanupValidation",
    "actor_can_use_no_confirm", "execute_member_cleanup", "finalize_cleanup_run", "post_cleanup_run_summary",
    "record_cleanup_event", "record_cleanup_run_summary", "validate_member_cleanup",
]
