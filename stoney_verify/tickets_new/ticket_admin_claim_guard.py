from __future__ import annotations

from typing import Any, Optional

import discord


_INSTALLED_MARKER = "_dank_ticket_admin_claim_guard_installed"


def _command_name(interaction: discord.Interaction) -> str:
    try:
        command = getattr(interaction, "command", None)
        return str(getattr(command, "name", "") or "").strip().lower()
    except Exception:
        return ""


def _action_for_command(command_name: str) -> str:
    name = str(command_name or "").strip().lower().replace("-", "_")
    mapping = {
        "close_ticket": "close",
        "ticket_claim": "claim",
        "ticket_unclaim": "unclaim",
        "ticket_transfer": "transfer",
        "ticket_reopen": "reopen",
        "ticket_delete": "delete",
        "ticket_transcript": "transcript",
        "ticket_priority": "priority",
        "ticket_info": "view_info",
        "ticket_note_add": "note",
        "ticket_notes": "view_notes",
        "ticket_notes_list": "view_notes",
        "ticket_macro": "macro",
    }
    if name in mapping:
        return mapping[name]
    if name.startswith("ticket_"):
        return name.removeprefix("ticket_") or "interaction"
    return "interaction"


async def _reply_denied(ticket_admin: Any, interaction: discord.Interaction, message: str) -> None:
    try:
        await ticket_admin._send_ephemeral(interaction, f"❌ {message}")
    except Exception:
        pass


def install_ticket_admin_claim_guard(
    ticket_admin: Any,
    *,
    ticket_transcripts: Any,
) -> None:
    """Protect every legacy top-level ticket command and direct helper.

    The canonical `/ticket` group has its own interaction check, but older
    top-level commands remain registered for compatibility. This guard wraps
    their shared context resolver, which runs before the command reaches any
    lifecycle, transcript, internal-note, or Discord channel side effect.
    """
    if bool(getattr(ticket_admin, _INSTALLED_MARKER, False)):
        return

    required_admin = (
        "_ensure_ticket_context",
        "_send_ephemeral",
        "transcript_staff_delete_closed_ticket",
    )
    missing_admin = [name for name in required_admin if not hasattr(ticket_admin, name)]
    if missing_admin:
        raise RuntimeError(
            "Cannot install legacy ticket admin claim guard; missing: "
            + ", ".join(sorted(missing_admin))
        )

    if not hasattr(ticket_transcripts, "authorize_ticket_action"):
        raise RuntimeError("Transcript module does not expose authorize_ticket_action.")
    if not callable(getattr(ticket_transcripts, "send_tickettool_style_transcript", None)):
        raise RuntimeError("Transcript module does not expose guarded transcript sender.")

    original_context = ticket_admin._ensure_ticket_context
    original_delete = ticket_admin.transcript_staff_delete_closed_ticket
    original_direct_transcript = getattr(ticket_admin, "transcript_post_to_channel", None)

    if not callable(original_context):
        raise RuntimeError("Legacy ticket context resolver is unavailable.")
    if not callable(original_delete):
        raise RuntimeError("Legacy ticket delete service is unavailable.")

    async def guarded_context(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> tuple[Optional[discord.TextChannel], Optional[dict[str, Any]]]:
        resolved_channel, row = await original_context(interaction, channel)
        if not isinstance(resolved_channel, discord.TextChannel):
            return resolved_channel, row

        action = _action_for_command(_command_name(interaction))
        actor = interaction.user if isinstance(interaction.user, (discord.Member, discord.User)) else None
        decision = await ticket_transcripts.authorize_ticket_action(
            channel_id=resolved_channel.id,
            actor=actor,
            action=action,
            row=row,
        )
        if decision.allowed:
            return resolved_channel, row

        await _reply_denied(ticket_admin, interaction, decision.message)
        try:
            print(
                f"🚨 ticket_claim_policy blocked legacy command "
                f"guild={getattr(interaction, 'guild_id', None)} "
                f"channel={resolved_channel.id} command={_command_name(interaction)} "
                f"action={action} actor={getattr(actor, 'id', None)} code={decision.code}"
            )
        except Exception:
            pass
        return None, None

    async def guarded_delete(
        *,
        channel: discord.TextChannel,
        staff_member: discord.Member,
        is_ghost: bool = False,
        reason: str = "Deleted by staff",
    ) -> dict[str, Any]:
        decision = await ticket_transcripts.authorize_ticket_action(
            channel_id=channel.id,
            actor=staff_member,
            action="delete",
        )
        if not decision.allowed:
            return {
                "ok": False,
                "deleted": False,
                "reason": decision.message,
                "authorization_code": decision.code,
            }

        result = await original_delete(
            channel=channel,
            staff_member=staff_member,
            is_ghost=is_ghost,
            reason=reason,
        )
        if isinstance(result, dict):
            normalized = dict(result)
            normalized.setdefault(
                "ok",
                bool(normalized.get("deleted") or normalized.get("channel_deleted")),
            )
            return normalized
        return {"ok": bool(result), "deleted": bool(result)}

    async def guarded_direct_transcript(
        *,
        ticket_channel: discord.TextChannel,
        deleted_by: Optional[discord.Member | discord.User] = None,
        reason: Optional[str] = None,
    ) -> tuple[Optional[discord.Message], Optional[str]]:
        decision = await ticket_transcripts.authorize_ticket_action(
            channel_id=ticket_channel.id,
            actor=deleted_by,
            action="transcript",
        )
        if not decision.allowed:
            return None, None
        if not callable(original_direct_transcript):
            return None, None
        return await original_direct_transcript(
            ticket_channel=ticket_channel,
            deleted_by=deleted_by,
            reason=reason,
        )

    ticket_admin._ensure_ticket_context = guarded_context
    ticket_admin.transcript_staff_delete_closed_ticket = guarded_delete
    ticket_admin.transcript_post_to_channel = guarded_direct_transcript
    # ticket_admin imported this symbol before transcript runtime wrapping. Point
    # it at the current guarded function rather than the stale function object.
    ticket_admin.send_tickettool_style_transcript = ticket_transcripts.send_tickettool_style_transcript
    setattr(ticket_admin, _INSTALLED_MARKER, True)


__all__ = [
    "install_ticket_admin_claim_guard",
]
