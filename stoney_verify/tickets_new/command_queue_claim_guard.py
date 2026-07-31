from __future__ import annotations

from typing import Any, Optional

import discord

from .service import (
    assign_ticket,
    authorize_ticket_action,
    mark_ticket_closed,
    reopen_ticket,
)
from .transcript_service import delete_ticket_with_optional_transcript


_TASKS_MARKER = "_dank_tasks_queue_claim_guard_installed"
_WORKER_MARKER = "_dank_bot_worker_claim_guard_installed"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


async def _resolve_member(guild: discord.Guild, user_id: Any) -> Optional[discord.Member]:
    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return None
    member = guild.get_member(uid)
    if member is not None:
        return member
    try:
        fetched = await guild.fetch_member(uid)
        return fetched if isinstance(fetched, discord.Member) else None
    except Exception:
        return None


async def _resolve_text_channel(bot: discord.Client, channel_id: Any) -> Optional[discord.TextChannel]:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return None
    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


async def _require_claimant(
    *,
    channel: discord.TextChannel,
    actor: Optional[discord.Member],
    action: str,
) -> None:
    if actor is None:
        raise RuntimeError(f"staff actor required for dashboard ticket action: {action}")
    decision = await authorize_ticket_action(
        channel_id=channel.id,
        actor=actor,
        action=action,
    )
    if not decision.allowed:
        raise RuntimeError(f"{decision.code}: {decision.message}")


async def _delete_with_claimant(
    *,
    channel: discord.TextChannel,
    actor: discord.Member,
    payload: dict[str, Any],
) -> dict[str, Any]:
    await _require_claimant(channel=channel, actor=actor, action="delete")
    result = await delete_ticket_with_optional_transcript(
        channel=channel,
        deleted_by=actor,
        is_ghost=bool(payload.get("ghost", False)),
        force_transcript_for_ghost=bool(payload.get("force_transcript", False)),
        reason=_safe_str(payload.get("reason")) or "Deleted from dashboard",
    )
    normalized = dict(result or {})
    normalized.setdefault(
        "ok",
        bool(normalized.get("deleted") or normalized.get("channel_deleted")),
    )
    if not normalized.get("ok"):
        raise RuntimeError(_safe_str(normalized.get("reason")) or "Failed to delete ticket")
    return normalized


def install_tasks_command_queue_claim_guard(tasks_queue: Any) -> None:
    if bool(getattr(tasks_queue, _TASKS_MARKER, False)):
        return
    if not callable(getattr(tasks_queue, "_execute_command", None)):
        raise RuntimeError("Legacy tasks command queue execute function is unavailable.")

    original_execute = tasks_queue._execute_command

    async def guarded_execute(command: dict[str, Any]) -> dict[str, Any]:
        action = _safe_str(command.get("action"))
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}

        if action not in {"close_ticket", "delete_ticket", "reopen_ticket", "assign_ticket"}:
            return await original_execute(command)

        channel = await tasks_queue._get_text_channel(payload.get("channel_id"))
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Ticket channel not found")

        if action == "assign_ticket":
            staff = await tasks_queue._get_member(channel.guild, payload.get("staff_id"))
            if staff is None:
                raise RuntimeError("Staff member not found")
            ok = await assign_ticket(channel_id=channel.id, staff_member=staff)
            if not ok:
                raise RuntimeError("Failed to claim ticket")
            return {"assigned": True, "channel_id": str(channel.id), "staff_id": str(staff.id)}

        actor_id = payload.get("actor_id") or payload.get("staff_id") or command.get("requested_by")
        actor = await tasks_queue._get_member(channel.guild, actor_id)
        if actor is None:
            raise RuntimeError("staff_id or actor_id required for dashboard ticket action")

        if action == "close_ticket":
            ok = await mark_ticket_closed(
                channel=channel,
                closed_by=actor,
                reason=_safe_str(payload.get("reason")) or None,
            )
            if not ok:
                raise RuntimeError("Failed to close ticket")
            return {"closed": True, "channel_id": str(channel.id), "closed_by": str(actor.id)}

        if action == "reopen_ticket":
            ok = await reopen_ticket(
                channel_id=channel.id,
                actor=actor,
                reason=_safe_str(payload.get("reason")) or "Reopened from dashboard",
            )
            if not ok:
                raise RuntimeError("Failed to reopen ticket")
            return {"reopened": True, "channel_id": str(channel.id), "actor_id": str(actor.id)}

        return await _delete_with_claimant(channel=channel, actor=actor, payload=payload)

    tasks_queue._execute_command = guarded_execute
    setattr(tasks_queue, _TASKS_MARKER, True)


def install_bot_command_worker_claim_guard(worker: Any) -> None:
    if bool(getattr(worker, _WORKER_MARKER, False)):
        return
    if not callable(getattr(worker, "execute_command", None)):
        raise RuntimeError("Bot command worker execute function is unavailable.")

    original_execute = worker.execute_command

    async def guarded_execute(command: dict[str, Any]) -> dict[str, Any]:
        action = _safe_str(command.get("action"))
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}

        protected = {
            "close_ticket": "close",
            "delete_ticket": "delete",
            "reopen_ticket": "reopen",
            "portal_ticket_reply": "message",
            "approve_verification": "verification_review",
            "deny_verification": "verification_review",
        }

        if action == "assign_ticket":
            guild = worker.bot.get_guild(_safe_int(command.get("guild_id"), 0))
            if guild is None:
                raise RuntimeError("Guild not found")
            channel = await _resolve_text_channel(worker.bot, payload.get("channel_id"))
            staff = await _resolve_member(guild, payload.get("staff_id"))
            if channel is None or staff is None:
                raise RuntimeError("Ticket channel or staff member not found")
            ok = await assign_ticket(channel_id=channel.id, staff_member=staff)
            if not ok:
                raise RuntimeError("Failed to claim ticket")
            return {"assigned": True, "channel_id": str(channel.id), "staff_id": str(staff.id)}

        policy_action = protected.get(action)
        if policy_action is None:
            return await original_execute(command)

        guild = worker.bot.get_guild(_safe_int(command.get("guild_id"), 0))
        if guild is None:
            raise RuntimeError("Guild not found")
        channel = await _resolve_text_channel(worker.bot, payload.get("channel_id"))
        if channel is None:
            raise RuntimeError("Ticket channel not found")

        actor_id = payload.get("actor_id") or payload.get("staff_id") or command.get("requested_by")
        actor = await _resolve_member(guild, actor_id)
        await _require_claimant(channel=channel, actor=actor, action=policy_action)
        assert actor is not None

        if action == "close_ticket":
            ok = await mark_ticket_closed(
                channel=channel,
                closed_by=actor,
                reason=_safe_str(payload.get("reason")) or "Resolved",
            )
            if not ok:
                raise RuntimeError("Failed to close ticket")
            return {"closed": True, "channel_id": str(channel.id), "closed_by": str(actor.id)}

        if action == "delete_ticket":
            return await _delete_with_claimant(channel=channel, actor=actor, payload=payload)

        if action == "reopen_ticket":
            ok = await reopen_ticket(
                channel_id=channel.id,
                actor=actor,
                reason=_safe_str(payload.get("reason")) or "Reopened from dashboard",
            )
            if not ok:
                raise RuntimeError("Failed to reopen ticket")
            return {"reopened": True, "channel_id": str(channel.id), "actor_id": str(actor.id)}

        if action == "portal_ticket_reply":
            content = _safe_str(payload.get("content"))
            if not content:
                raise RuntimeError("Reply content required")
            author_label = _safe_str(payload.get("staff_name")) or getattr(actor, "display_name", None) or str(actor)
            sent = await channel.send(f"💬 **Dashboard Reply** from **{author_label}**\n\n{content}")
            return {
                "mirrored": True,
                "ticket_id": _safe_str(payload.get("ticket_id")) or None,
                "channel_id": str(channel.id),
                "portal_message_id": _safe_str(payload.get("message_id")) or None,
                "discord_message_id": str(sent.id),
            }

        # Verification worker paths retain their existing role/audit behavior,
        # but only after current-claimant authorization succeeds above.
        return await original_execute(command)

    worker.execute_command = guarded_execute
    setattr(worker, _WORKER_MARKER, True)


__all__ = [
    "install_bot_command_worker_claim_guard",
    "install_tasks_command_queue_claim_guard",
]
