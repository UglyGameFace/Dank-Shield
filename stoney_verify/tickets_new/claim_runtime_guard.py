from __future__ import annotations

from typing import Any

import discord


_INSTALLED_MARKER = "_dank_claim_runtime_guards_installed"


def _interaction_custom_id(interaction: discord.Interaction) -> str:
    try:
        data = getattr(interaction, "data", None) or {}
        return str(data.get("custom_id") or "").strip()
    except Exception:
        return ""


async def _authorize(
    ticket_transcripts: Any,
    *,
    channel: discord.TextChannel,
    actor: discord.abc.User | None,
    action: str,
    allow_requester_cancel: bool = False,
) -> Any:
    return await ticket_transcripts.authorize_ticket_action(
        channel_id=channel.id,
        actor=actor,
        action=action,
        allow_requester_cancel=allow_requester_cancel,
    )


async def _reply_denied(ticket_transcripts: Any, interaction: discord.Interaction, message: str) -> None:
    try:
        await ticket_transcripts._reply_ephemeral(interaction, f"❌ {message}")
    except Exception:
        pass


def install_transcript_claim_runtime_guards(ticket_transcripts: Any) -> None:
    """Claim-gate legacy persistent views before they perform side effects.

    These controls predate the central ticket service and contain direct Discord
    fallbacks. Wrapping their shared helpers prevents transcript posting, role
    changes, control reposts, close cancellation, reopening, and force deletion
    from occurring before the authoritative claim decision.
    """
    if bool(getattr(ticket_transcripts, _INSTALLED_MARKER, False)):
        return

    required = (
        "authorize_ticket_action",
        "_staff_delete_closed_ticket_verified",
        "send_tickettool_style_transcript",
        "_user_can_close_ticket",
        "_user_can_reopen_ticket",
        "VerificationStaffReviewView",
        "StaffClosedTicketView",
    )
    missing = [name for name in required if not hasattr(ticket_transcripts, name)]
    if missing:
        raise RuntimeError(
            "Cannot install claim-first transcript guards; missing: " + ", ".join(sorted(missing))
        )

    original_delete = ticket_transcripts._staff_delete_closed_ticket_verified
    original_transcript = ticket_transcripts.send_tickettool_style_transcript
    original_verification_staff_check = ticket_transcripts.VerificationStaffReviewView._ensure_staff
    original_closed_staff_check = ticket_transcripts.StaffClosedTicketView._ensure_staff

    async def guarded_delete(
        *,
        channel: discord.TextChannel,
        staff_member: discord.Member,
        is_ghost: bool,
        reason: str,
    ) -> dict[str, Any]:
        decision = await _authorize(
            ticket_transcripts,
            channel=channel,
            actor=staff_member,
            action="delete",
        )
        if not decision.allowed:
            return {
                "deleted": False,
                "ok": False,
                "reason": decision.message,
                "authorization_code": decision.code,
            }
        return await original_delete(
            channel=channel,
            staff_member=staff_member,
            is_ghost=is_ghost,
            reason=reason,
        )

    async def guarded_transcript(
        channel: discord.TextChannel,
        owner: discord.Member | None,
        owner_id: int | None = None,
        closed_by: discord.Member | None = None,
        decision: str | None = None,
    ) -> Any:
        if closed_by is not None:
            auth = await _authorize(
                ticket_transcripts,
                channel=channel,
                actor=closed_by,
                action="transcript",
            )
            if not auth.allowed:
                try:
                    print(
                        f"🚨 ticket_claim_policy blocked transcript channel={channel.id} "
                        f"actor={closed_by.id} code={auth.code}"
                    )
                except Exception:
                    pass
                return False
        return await original_transcript(
            channel,
            owner,
            owner_id=owner_id,
            closed_by=closed_by,
            decision=decision,
        )

    async def guarded_can_close(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> bool:
        actor = interaction.user if isinstance(interaction.user, (discord.Member, discord.User)) else None
        auth = await _authorize(
            ticket_transcripts,
            channel=channel,
            actor=actor,
            action="close",
            allow_requester_cancel=True,
        )
        return bool(auth.allowed)

    async def guarded_can_reopen(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> bool:
        actor = interaction.user if isinstance(interaction.user, (discord.Member, discord.User)) else None
        auth = await _authorize(
            ticket_transcripts,
            channel=channel,
            actor=actor,
            action="reopen",
        )
        return bool(auth.allowed)

    async def guarded_verification_staff_check(self: Any, interaction: discord.Interaction) -> bool:
        if not await original_verification_staff_check(self, interaction):
            return False
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await _reply_denied(ticket_transcripts, interaction, "Invalid ticket channel.")
            return False
        auth = await _authorize(
            ticket_transcripts,
            channel=channel,
            actor=interaction.user,
            action="verification_review",
        )
        if auth.allowed:
            return True
        await _reply_denied(ticket_transcripts, interaction, auth.message)
        return False

    async def guarded_closed_staff_check(self: Any, interaction: discord.Interaction) -> bool:
        if not await original_closed_staff_check(self, interaction):
            return False
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await _reply_denied(ticket_transcripts, interaction, "Invalid ticket channel.")
            return False

        custom_id = _interaction_custom_id(interaction)
        action = "transcript" if custom_id == "sv:ticket:transcript" else "delete"
        auth = await _authorize(
            ticket_transcripts,
            channel=channel,
            actor=interaction.user,
            action=action,
        )
        if auth.allowed:
            return True
        await _reply_denied(ticket_transcripts, interaction, auth.message)
        return False

    ticket_transcripts._staff_delete_closed_ticket_verified = guarded_delete
    ticket_transcripts.send_tickettool_style_transcript = guarded_transcript
    ticket_transcripts._user_can_close_ticket = guarded_can_close
    ticket_transcripts._user_can_reopen_ticket = guarded_can_reopen
    ticket_transcripts.VerificationStaffReviewView._ensure_staff = guarded_verification_staff_check
    ticket_transcripts.StaffClosedTicketView._ensure_staff = guarded_closed_staff_check
    setattr(ticket_transcripts, _INSTALLED_MARKER, True)


__all__ = ["install_transcript_claim_runtime_guards"]
