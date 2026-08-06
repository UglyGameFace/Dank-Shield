from __future__ import annotations

"""Confirmed emergency ticket actions reserved for the real Discord guild owner.

Normal controls remain claimant-only. The startup audit gate records a durable
pre-mutation authorization event, while this module performs the requested
lifecycle mutation and emits only the normal lifecycle event for successful
operations. That keeps the audit trail complete without duplicating every owner
action several times in the activity feed.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from weakref import WeakValueDictionary

import discord

from .claim_policy import (
    evaluate_ticket_action,
    ticket_claimed_by_id,
    ticket_has_transcript,
    ticket_owner_id,
)
from .repository import (
    attach_transcript_to_ticket as repo_attach_transcript,
    get_ticket_by_any_channel_id,
    mark_ticket_deleted as repo_mark_deleted,
    transfer_ticket as repo_transfer,
    unclaim_ticket as repo_unclaim,
)

_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_VALID_ACTIONS = frozenset({"transfer", "unclaim", "close", "delete"})


@dataclass(frozen=True)
class OwnerEmergencyResult:
    ok: bool
    action: str
    code: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _clean_reason(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:500]


def _name(user: Any) -> str:
    try:
        return str(user)
    except Exception:
        return "Discord guild owner"


def _guild_owner_id(guild: Any) -> int:
    return _safe_int(getattr(guild, "owner_id", 0), 0) or _safe_int(
        getattr(getattr(guild, "owner", None), "id", 0),
        0,
    )


def is_actual_guild_owner(guild: Any, actor: Any) -> bool:
    """Roles and Administrator never qualify; only Discord's owner ID does."""
    return bool(
        guild is not None
        and actor is not None
        and not bool(getattr(actor, "bot", False))
        and _guild_owner_id(guild) > 0
        and _safe_int(getattr(actor, "id", 0), 0) == _guild_owner_id(guild)
    )


def available_owner_emergency_actions(row: Optional[Dict[str, Any]]) -> tuple[str, ...]:
    status = str((row or {}).get("status") or "unknown").strip().lower()
    if status in {"active", "reopened"}:
        status = "open"
    if status in {"open", "claimed"}:
        return (
            ("transfer", "unclaim", "close")
            if ticket_claimed_by_id(row) > 0
            else ("transfer", "close")
        )
    if status == "closed":
        return ("delete",)
    return ()


def _result(
    ok: bool,
    action: str,
    code: str,
    message: str,
    **metadata: Any,
) -> OwnerEmergencyResult:
    return OwnerEmergencyResult(bool(ok), action, code, message, dict(metadata))


def _lock(channel_id: int) -> asyncio.Lock:
    cid = int(channel_id)
    lock = _LOCKS.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[cid] = lock
    return lock


async def _row(channel_id: int) -> Optional[Dict[str, Any]]:
    try:
        value = await get_ticket_by_any_channel_id(channel_id)
        return dict(value) if isinstance(value, dict) else None
    except Exception:
        return None


def _target_is_staff(member: Any) -> bool:
    if member is None or bool(getattr(member, "bot", False)):
        return False
    if is_actual_guild_owner(getattr(member, "guild", None), member):
        return True
    try:
        from ..commands_ext.public_staff_scope import scoped_is_staff

        return bool(scoped_is_staff(member))
    except Exception:
        return False


async def _sync_claimant_permissions(
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
    previous_claimed_by: int,
) -> None:
    try:
        from . import service

        await service._sync_claimant_permissions_by_channel_id(
            channel.id,
            row=row,
            previous_claimed_by=previous_claimed_by,
            closed=False,
        )
    except Exception as exc:
        print(
            "⚠️ owner emergency claimant permission sync failed "
            f"channel={channel.id} error={exc!r}"
        )


async def _send(channel: discord.TextChannel, content: str) -> None:
    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass


async def _log_assignment_event(
    *,
    channel: discord.TextChannel,
    actor: Any,
    action: str,
    reason: str,
    previous_claimed_by: int,
    row: Optional[Dict[str, Any]],
    target: Any = None,
) -> bool:
    """Emit one normal lifecycle event with emergency context attached."""
    try:
        from . import event_service

        metadata = {
            "owner_emergency_override": True,
            "override_reason": reason,
            "previous_claimed_by": previous_claimed_by or None,
            "allow_duplicate_event": True,
        }
        if action == "transfer":
            return bool(
                await event_service.log_ticket_transferred(
                    guild_id=channel.guild.id,
                    actor_user_id=actor.id,
                    actor_name=_name(actor),
                    target_user_id=target.id,
                    target_name=_name(target),
                    channel_id=channel.id,
                    reason=reason,
                    ticket_row=row,
                    source="tickets_new_owner_emergency_transfer",
                    metadata=metadata,
                )
            )

        return bool(
            await event_service.log_ticket_unclaimed(
                guild_id=channel.guild.id,
                actor_user_id=actor.id,
                actor_name=_name(actor),
                channel_id=channel.id,
                ticket_row=row,
                source="tickets_new_owner_emergency_unclaim",
                metadata=metadata,
            )
        )
    except Exception as exc:
        print(
            "⚠️ owner emergency assignment event failed "
            f"channel={channel.id} action={action} error={exc!r}"
        )
        return False


async def _ensure_transcript(
    channel: discord.TextChannel,
    actor: Any,
    reason: str,
) -> tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
    """Create and persist transcript evidence before Discord channel deletion."""
    current = await _row(channel.id)
    if ticket_has_transcript(current):
        return True, current, {
            "transcript_created": False,
            "transcript_already_existed": True,
        }

    try:
        from . import transcript_service

        posted, url = await transcript_service.post_transcript_to_channel(
            ticket_channel=channel,
            deleted_by=actor,
            reason=f"Owner emergency delete: {reason}",
        )
    except Exception as exc:
        return False, current, {"transcript_error": repr(exc)}

    transcript_url = url
    message_id = None
    transcript_channel_id = None

    if posted is not None:
        transcript_url = url or getattr(posted, "jump_url", None)
        message_id = getattr(posted, "id", None)
        transcript_channel_id = getattr(
            getattr(posted, "channel", None),
            "id",
            None,
        )
        try:
            attached = await repo_attach_transcript(
                channel_id=channel.id,
                transcript_url=transcript_url,
                transcript_message_id=message_id,
                transcript_channel_id=transcript_channel_id,
                actor=actor,
            )
        except Exception:
            attached = False

        if attached:
            try:
                from .event_service import log_ticket_transcript_attached

                await log_ticket_transcript_attached(
                    guild_id=channel.guild.id,
                    actor_user_id=actor.id,
                    actor_name=_name(actor),
                    channel_id=channel.id,
                    transcript_url=transcript_url,
                    transcript_message_id=message_id,
                    transcript_channel_id=transcript_channel_id,
                    source="tickets_new_owner_emergency_transcript",
                    metadata={
                        "owner_emergency_override": True,
                        "allow_duplicate_event": True,
                    },
                )
            except Exception:
                pass

    refreshed = await _row(channel.id)
    metadata = {
        "transcript_created": posted is not None,
        "transcript_url": transcript_url,
        "transcript_message_id": message_id,
        "transcript_channel_id": transcript_channel_id,
    }
    return ticket_has_transcript(refreshed), refreshed, metadata


async def _log_delete_event(
    *,
    channel: discord.TextChannel,
    actor: Any,
    reason: str,
    previous_claimed_by: int,
    row: Optional[Dict[str, Any]],
    transcript_metadata: Dict[str, Any],
) -> bool:
    try:
        from .event_service import log_ticket_deleted

        return bool(
            await log_ticket_deleted(
                guild_id=channel.guild.id,
                actor_user_id=actor.id,
                actor_name=_name(actor),
                channel_id=channel.id,
                reason=reason,
                ticket_row=row,
                source="tickets_new_owner_emergency_delete",
                metadata={
                    "owner_emergency_override": True,
                    "previous_claimed_by": previous_claimed_by or None,
                    "allow_duplicate_event": True,
                    **transcript_metadata,
                },
            )
        )
    except Exception as exc:
        print(
            "⚠️ owner emergency delete event failed "
            f"channel={channel.id} error={exc!r}"
        )
        return False


async def execute_owner_emergency_override(
    *,
    channel: discord.TextChannel,
    actor: discord.Member,
    action: str,
    reason: str,
    target_member: Optional[discord.Member] = None,
) -> OwnerEmergencyResult:
    clean_action = str(action or "").strip().lower().replace("-", "_")
    clean_reason = _clean_reason(reason)

    if clean_action not in _VALID_ACTIONS:
        return _result(
            False,
            clean_action,
            "invalid_action",
            "That emergency action is not supported.",
            mutation_started=False,
        )
    if (
        channel is None
        or getattr(channel, "guild", None) is None
        or _safe_int(getattr(channel, "id", 0), 0) <= 0
    ):
        return _result(
            False,
            clean_action,
            "invalid_channel",
            "Use this inside a registered ticket channel.",
            mutation_started=False,
        )
    if not is_actual_guild_owner(channel.guild, actor):
        return _result(
            False,
            clean_action,
            "guild_owner_required",
            "Only the actual Discord server owner can use Emergency Override.",
            mutation_started=False,
        )
    if len(clean_reason) < 8:
        return _result(
            False,
            clean_action,
            "reason_required",
            "Give a clear emergency reason of at least 8 characters.",
            mutation_started=False,
        )

    lock = _lock(channel.id)
    if lock.locked():
        return _result(
            False,
            clean_action,
            "override_in_progress",
            "Another emergency action is already running for this ticket.",
            mutation_started=False,
        )

    async with lock:
        before = await _row(channel.id)
        if before is None:
            return _result(
                False,
                clean_action,
                "ticket_not_found",
                "This ticket is not registered. Nothing was changed.",
                mutation_started=False,
            )

        previous = ticket_claimed_by_id(before)
        policy_action = (
            "owner_emergency_delete_prepare"
            if clean_action == "delete"
            else f"owner_emergency_{clean_action}"
        )
        decision = evaluate_ticket_action(
            before,
            actor_id=actor.id,
            action=policy_action,
            guild_owner_id=_guild_owner_id(channel.guild),
        )
        if not decision.allowed:
            return _result(
                False,
                clean_action,
                decision.code,
                decision.message,
                mutation_started=False,
                previous_claimed_by=previous,
            )

        if clean_action == "transfer":
            if (
                target_member is None
                or _safe_int(getattr(target_member, "id", 0), 0) <= 0
            ):
                return _result(
                    False,
                    clean_action,
                    "target_required",
                    "Choose a staff member to receive this ticket.",
                    mutation_started=False,
                    previous_claimed_by=previous,
                )
            if _safe_int(
                getattr(getattr(target_member, "guild", None), "id", 0),
                0,
            ) != int(channel.guild.id):
                return _result(
                    False,
                    clean_action,
                    "target_wrong_guild",
                    "That member is not in this server.",
                    mutation_started=False,
                    previous_claimed_by=previous,
                )
            if ticket_owner_id(before) == int(target_member.id):
                return _result(
                    False,
                    clean_action,
                    "target_is_requester",
                    "The requester cannot become the staff claimant.",
                    mutation_started=False,
                    previous_claimed_by=previous,
                )
            if not _target_is_staff(target_member):
                return _result(
                    False,
                    clean_action,
                    "target_not_staff",
                    "Choose configured ticket staff or the actual server owner.",
                    mutation_started=False,
                    previous_claimed_by=previous,
                )
            if previous == int(target_member.id):
                return _result(
                    True,
                    clean_action,
                    "already_assigned",
                    f"Already assigned to {target_member.mention}.",
                    mutation_started=False,
                    previous_claimed_by=previous,
                    target_user_id=target_member.id,
                )

            try:
                await repo_transfer(
                    channel_id=channel.id,
                    to_staff_member=target_member,
                )
            except Exception:
                pass
            after = await _row(channel.id)
            ok = ticket_claimed_by_id(after) == int(target_member.id)
            if ok:
                await _sync_claimant_permissions(channel, after, previous)
                await _log_assignment_event(
                    channel=channel,
                    actor=actor,
                    action=clean_action,
                    reason=clean_reason,
                    previous_claimed_by=previous,
                    row=after,
                    target=target_member,
                )
                await _send(
                    channel,
                    "🚨 Server-owner emergency override transferred this ticket "
                    f"to {target_member.mention}. Reason: {clean_reason}",
                )

            return _result(
                ok,
                clean_action,
                "owner_emergency_transfer_applied" if ok else "transfer_failed",
                (
                    f"Emergency transfer completed to {target_member.mention}."
                    if ok
                    else "The emergency transfer did not persist."
                ),
                mutation_started=True,
                previous_claimed_by=previous,
                target_user_id=target_member.id,
            )

        if clean_action == "unclaim":
            if previous <= 0:
                return _result(
                    True,
                    clean_action,
                    "already_unclaimed",
                    "This ticket is already unclaimed.",
                    mutation_started=False,
                    previous_claimed_by=0,
                )

            try:
                await repo_unclaim(channel_id=channel.id)
            except Exception:
                pass
            after = await _row(channel.id)
            ok = ticket_claimed_by_id(after) <= 0
            if ok:
                await _sync_claimant_permissions(channel, after, previous)
                await _log_assignment_event(
                    channel=channel,
                    actor=actor,
                    action=clean_action,
                    reason=clean_reason,
                    previous_claimed_by=previous,
                    row=after,
                )
                await _send(
                    channel,
                    "🚨 Server-owner emergency override removed the claimant. "
                    f"Reason: {clean_reason}",
                )

            return _result(
                ok,
                clean_action,
                "owner_emergency_unclaim_applied" if ok else "unclaim_failed",
                (
                    "Emergency unclaim completed."
                    if ok
                    else "The emergency unclaim did not persist."
                ),
                mutation_started=True,
                previous_claimed_by=previous,
            )

        if clean_action == "close":
            try:
                from . import service

                await service.mark_ticket_closed(
                    channel=channel,
                    closed_by=actor,
                    reason=f"Owner emergency override: {clean_reason}",
                )
            except Exception:
                pass
            after = await _row(channel.id)
            ok = str((after or {}).get("status") or "").lower() == "closed"
            return _result(
                ok,
                clean_action,
                "owner_emergency_close_applied" if ok else "close_failed",
                (
                    "Emergency close completed; the claimant record remains in audit history."
                    if ok
                    else "The emergency close did not complete."
                ),
                mutation_started=True,
                previous_claimed_by=previous,
            )

        transcript_ok, with_transcript, transcript_metadata = await _ensure_transcript(
            channel,
            actor,
            clean_reason,
        )
        if not transcript_ok:
            return _result(
                False,
                clean_action,
                "transcript_required",
                "Safe delete stopped because a preserved transcript could not be verified.",
                mutation_started=False,
                previous_claimed_by=previous,
                **transcript_metadata,
            )

        final = evaluate_ticket_action(
            with_transcript,
            actor_id=actor.id,
            action="owner_emergency_delete",
            guild_owner_id=_guild_owner_id(channel.guild),
        )
        if not final.allowed:
            return _result(
                False,
                clean_action,
                final.code,
                final.message,
                mutation_started=False,
                previous_claimed_by=previous,
                **transcript_metadata,
            )

        try:
            await channel.delete(
                reason=(
                    f"Owner emergency override by {_name(actor)}: {clean_reason}"
                )[:512]
            )
            discord_deleted = True
        except discord.NotFound:
            discord_deleted = True
        except Exception as exc:
            return _result(
                False,
                clean_action,
                "discord_delete_failed",
                "Discord refused to delete the channel. The closed ticket and transcript were kept.",
                mutation_started=True,
                previous_claimed_by=previous,
                discord_error=repr(exc),
                **transcript_metadata,
            )

        database_deleted = False
        after: Optional[Dict[str, Any]] = None
        for attempt in range(1, 4):
            try:
                await repo_mark_deleted(
                    channel_id=channel.id,
                    deleted_by=actor.id,
                    deleted_by_name=_name(actor),
                    reason=f"Owner emergency override: {clean_reason}",
                )
            except Exception:
                pass
            after = await _row(channel.id)
            if str((after or {}).get("status") or "").lower() == "deleted":
                database_deleted = True
                break
            if attempt < 3:
                await asyncio.sleep(0.35 * attempt)

        if database_deleted:
            await _log_delete_event(
                channel=channel,
                actor=actor,
                reason=clean_reason,
                previous_claimed_by=previous,
                row=after or with_transcript,
                transcript_metadata=transcript_metadata,
            )

        return _result(
            database_deleted,
            clean_action,
            (
                "owner_emergency_delete_applied"
                if database_deleted
                else "discord_deleted_db_update_failed"
            ),
            (
                "Safe emergency delete completed after the transcript was verified."
                if database_deleted
                else "The channel was removed and transcript preserved, but the database status update failed."
            ),
            mutation_started=True,
            previous_claimed_by=previous,
            discord_deleted=discord_deleted,
            db_marked_deleted=database_deleted,
            **transcript_metadata,
        )


__all__ = [
    "OwnerEmergencyResult",
    "available_owner_emergency_actions",
    "execute_owner_emergency_override",
    "is_actual_guild_owner",
]
