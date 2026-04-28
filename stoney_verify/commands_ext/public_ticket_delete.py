from __future__ import annotations

from typing import Optional

import discord

from .common import RUNTIME_STATS, _staff_check, mark_ticket_activity, reply_once, safe_defer
from . import ticket_admin as legacy
from .public_ticket_group import ticket_group


# ============================================================
# public_ticket_delete.py
# ------------------------------------------------------------
# Adds /ticket delete to the existing public /ticket group without exposing the
# old legacy top-level command surface.
#
# TicketTool parity goal:
# - close -> transcript -> delete is a first-class staff workflow
#
# Production safety rules:
# - staff only
# - recognized ticket channels only
# - closed/archive tickets delete cleanly
# - open tickets are blocked unless staff explicitly sets force_open_delete
# - transcript is attempted before channel deletion
# - DB state is marked deleted before deleting the Discord channel
# ============================================================


_ATTACHED = False


def _safe_reason(reason: Optional[str], fallback: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return fallback
    return text[:500]


async def _ticket_delete_callback(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
    force_open_delete: bool = False,
) -> None:
    if not _staff_check(interaction):
        return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

    await safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_mark_ticket_deleted is None:
        return await interaction.followup.send("❌ Ticket delete service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    effectively_closed = legacy._ticket_effectively_closed(channel=ch, row=row)

    if status != "deleted" and not effectively_closed and not force_open_delete:
        return await interaction.followup.send(
            (
                "🚫 Delete blocked because this ticket is still open.\n"
                "Close/archive it first with `/ticket close`, then run `/ticket delete`.\n"
                "For emergency cleanup only, rerun with `force_open_delete:true` and include a reason."
            ),
            ephemeral=True,
        )

    if force_open_delete and not _safe_reason(reason, ""):
        return await interaction.followup.send(
            "❌ Forced open-ticket deletion requires a reason.",
            ephemeral=True,
        )

    actor = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    delete_reason = _safe_reason(
        reason,
        "FORCED OPEN TICKET DELETE" if force_open_delete and not effectively_closed else "STAFF DELETED CLOSED TICKET",
    )

    await legacy._cleanup_ticket_timer_state(ch.id)

    owner = await legacy._owner_for_ticket(ch, row)
    transcript_ok = False
    transcript_url: Optional[str] = None
    try:
        transcript_ok, transcript_url = await legacy._post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor,
            reason=delete_reason,
        )
    except Exception:
        transcript_ok = False
        transcript_url = None

    try:
        await legacy.service_mark_ticket_deleted(
            channel_id=ch.id,
            deleted_by=actor,
            reason=delete_reason,
        )
    except Exception as e:
        return await interaction.followup.send(
            f"❌ Delete stopped because DB state could not be marked deleted: `{e}`",
            ephemeral=True,
        )

    channel_name = ch.name
    channel_id = ch.id
    deleted_channel = False
    try:
        await ch.delete(reason=f"Ticket deleted by {interaction.user}: {delete_reason}")
        deleted_channel = True
    except discord.Forbidden:
        return await interaction.followup.send(
            "❌ Ticket was marked deleted in the DB, but Discord blocked channel deletion. Check Manage Channels permission/hierarchy.",
            ephemeral=True,
        )
    except discord.NotFound:
        deleted_channel = True
    except Exception as e:
        return await interaction.followup.send(
            f"⚠️ Ticket was marked deleted in the DB, but channel deletion failed: `{e}`",
            ephemeral=True,
        )

    try:
        mark_ticket_activity(channel_id)
        RUNTIME_STATS["tickets_deleted"] = int(RUNTIME_STATS.get("tickets_deleted", 0) or 0) + 1
    except Exception:
        pass

    lines = [f"✅ Deleted ticket channel `#{channel_name}` (`{channel_id}`)."]
    if transcript_ok and transcript_url:
        lines.append(f"🧾 Transcript: {transcript_url}")
    elif transcript_ok:
        lines.append("🧾 Transcript posted.")
    else:
        lines.append("⚠️ Transcript generation/posting failed before deletion.")
    if force_open_delete and not effectively_closed:
        lines.append("⚠️ This was a forced open-ticket delete.")
    if deleted_channel:
        lines.append(f"Reason: `{delete_reason}`")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = ticket_group.get_command("delete")
    except Exception:
        existing = None

    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="delete",
        description="Delete a closed ticket after posting a transcript.",
        callback=_ticket_delete_callback,
    )

    try:
        command._params["channel"].description = "Ticket channel to delete. Leave empty to use current channel."
        command._params["reason"].description = "Reason for deleting the ticket. Required when force deleting an open ticket."
        command._params["force_open_delete"].description = "Emergency only: allow deleting an open ticket. Requires a reason."
    except Exception:
        pass

    ticket_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_ticket_delete_commands(bot, tree) -> None:
    _ = bot
    _ = tree
    _attach()
    try:
        print("✅ public_ticket_delete: attached /ticket delete command")
    except Exception:
        pass


__all__ = ["register_public_ticket_delete_commands"]
