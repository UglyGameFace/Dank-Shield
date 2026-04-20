from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import (
    _staff_check,
    reply_once,
    safe_defer,
    mark_ticket_activity,
)

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

try:
    from ..transcripts import send_tickettool_style_transcript
except Exception:
    async def send_tickettool_style_transcript(*args, **kwargs) -> None:  # type: ignore
        return None

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

try:
    from ..tickets_new.transcript_service import (
        post_transcript_to_channel as transcript_post_to_channel,
    )
except Exception:
    transcript_post_to_channel = None  # type: ignore

try:
    from ..tickets_new.service import (
        reopen_ticket_channel as service_reopen_ticket_channel,
        mark_ticket_closed as service_mark_ticket_closed,
        add_internal_note as service_add_internal_note,
    )
except Exception:
    service_reopen_ticket_channel = None  # type: ignore
    service_mark_ticket_closed = None  # type: ignore
    service_add_internal_note = None  # type: ignore


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status"), "unknown").lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
    except Exception:
        pass
    return "unknown"


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _channel_looks_open(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("ticket-")
    except Exception:
        return False


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


def _is_ticket_channel(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    if isinstance(row, dict):
        return True
    try:
        return bool(is_verification_ticket_channel(channel))
    except Exception:
        return False


async def _send_ephemeral(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=True)
    except Exception:
        pass


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await _send_ephemeral(interaction, "❌ Must be used in a ticket text channel.")
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not _is_ticket_channel(ch, row):
        await _send_ephemeral(
            interaction,
            f"❌ `{ch.name}` is not recognized as a ticket channel.",
        )
        return None, None

    return ch, row


async def _ticket_owner(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> Optional[discord.Member | discord.User]:
    try:
        owner_id = _safe_int((row or {}).get("owner_id") or (row or {}).get("user_id"), 0)
        if owner_id > 0:
            member = channel.guild.get_member(owner_id)
            if member:
                return member
            try:
                return await channel.guild.fetch_member(owner_id)
            except Exception:
                pass
    except Exception:
        pass

    try:
        return await find_ticket_owner_retry(channel)
    except Exception:
        return None


async def _ticket_assignee(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> Optional[discord.Member]:
    try:
        assigned_to = _safe_int((row or {}).get("assigned_to") or (row or {}).get("claimed_by"), 0)
        if assigned_to <= 0:
            return None
        member = channel.guild.get_member(assigned_to)
        if member:
            return member
        try:
            return await channel.guild.fetch_member(assigned_to)
        except Exception:
            return None
    except Exception:
        return None


def _actor_member(guild: Optional[discord.Guild], user: discord.abc.User) -> Optional[discord.Member]:
    if guild is None:
        return None
    try:
        member = guild.get_member(int(user.id))
        if member:
            return member
    except Exception:
        pass
    return None


async def _refresh_ticket_row(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    return await _ticket_row_for_channel(channel)


async def _repair_closed_drift_for_resolve(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
    actor: Optional[discord.Member | discord.User],
) -> Optional[Dict[str, Any]]:
    """
    Older broken flows can leave the channel visibly closed while the DB still
    says open/claimed. Resolve should treat that as already closed.
    """
    status = _ticket_status(row)
    if status == "deleted":
        return row

    if _channel_looks_closed(channel) and status in {"open", "claimed", "unknown"}:
        if service_mark_ticket_closed is None:
            return row
        try:
            await service_mark_ticket_closed(
                channel=channel,
                closed_by=actor,
                reason="State repaired before resolve",
            )
        except Exception:
            return row
        return await _refresh_ticket_row(channel)

    return row


async def _repair_open_drift_for_reopen(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
    actor: Optional[discord.Member | discord.User],
) -> Optional[Dict[str, Any]]:
    """
    Older broken flows can leave the channel visibly open while the DB still
    says closed. Reopen should not pretend this is still closed.
    """
    status = _ticket_status(row)
    if status == "deleted":
        return row

    if _channel_looks_open(channel) and status == "closed":
        if service_reopen_ticket_channel is None:
            return row

        owner = await _ticket_owner(channel, row)
        owner_member = owner if isinstance(owner, discord.Member) else None

        try:
            await service_reopen_ticket_channel(
                channel=channel,
                owner=owner_member,
                actor=actor,
                reason="State repaired before reopen check",
            )
        except Exception:
            return row

        return await _refresh_ticket_row(channel)

    return row


async def _post_ticket_transcript(
    *,
    channel: discord.TextChannel,
    owner: Optional[discord.Member | discord.User],
    actor: Optional[discord.Member | discord.User],
    reason: str,
) -> Tuple[bool, Optional[str]]:
    if callable(transcript_post_to_channel):
        try:
            _msg, jump_url = await transcript_post_to_channel(
                ticket_channel=channel,
                deleted_by=actor,
                reason=reason,
            )
            if jump_url:
                return True, jump_url
            return True, None
        except Exception:
            pass

    try:
        await send_tickettool_style_transcript(
            channel,
            owner if isinstance(owner, discord.Member) else None,
            closed_by=actor,
            decision=reason,
        )
        return True, None
    except Exception:
        return False, None


async def _add_resolution_note(channel_id: int, author: discord.abc.User, note: str) -> None:
    if service_add_internal_note is None:
        return
    try:
        await service_add_internal_note(
            channel_id=channel_id,
            author=author,
            note=note,
            is_pinned=False,
        )
    except Exception:
        pass


async def _cancel_ticket_kick_timer_if_any(channel_id: int) -> None:
    try:
        _cancel_kick_timer(channel_id)
    except Exception:
        pass

    try:
        await kick_timer_persist_delete(int(channel_id))
    except Exception:
        pass


def register_ticket_resolution_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_resolve",
        description="Resolve a ticket with a required reason, transcript, and close.",
    )
    @app_commands.describe(
        reason="Required closure reason",
        channel="Ticket channel to resolve (leave empty to use current channel)",
    )
    async def ticket_resolve(
        interaction: discord.Interaction,
        reason: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await interaction.response.send_message(
                "❌ A close reason is required.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_mark_ticket_closed is None:
            return await interaction.followup.send(
                "❌ Ticket close service is unavailable.",
                ephemeral=True,
            )

        row = await _repair_closed_drift_for_resolve(
            channel=ch,
            row=row,
            actor=interaction.user,
        )
        status = _ticket_status(row)

        if status == "deleted":
            return await interaction.followup.send(
                "❌ This ticket is already marked deleted and cannot be resolved.",
                ephemeral=True,
            )

        if status == "closed" or _channel_looks_closed(ch):
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already closed.",
                ephemeral=True,
            )

        owner = await _ticket_owner(ch, row)
        actor_member = _actor_member(interaction.guild, interaction.user) or interaction.user

        await _cancel_ticket_kick_timer_if_any(ch.id)

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Resolution note: {clean_reason}",
            )
        except Exception:
            pass

        transcript_ok, transcript_url = await _post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor_member,
            reason=clean_reason,
        )

        try:
            ok = await service_mark_ticket_closed(
                channel=ch,
                closed_by=actor_member,
                reason=clean_reason,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed resolving ticket state: `{e}`",
                ephemeral=True,
            )

        if not ok:
            return await interaction.followup.send(
                "❌ Failed to resolve this ticket.",
                ephemeral=True,
            )

        try:
            await ch.send(
                f"✅ Ticket resolved by {interaction.user.mention}.\n"
                f"**Reason:** {clean_reason}\n"
                "Use `/ticket_reopen_reason` if this needs to be reopened."
            )
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        msg = f"✅ Resolved {ch.mention}."
        if transcript_ok and transcript_url:
            msg += f"\n🧾 Transcript: {transcript_url}"
        elif not transcript_ok:
            msg += "\n⚠️ Transcript generation failed."
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="ticket_reopen_reason",
        description="Reopen a ticket with a required reason.",
    )
    @app_commands.describe(
        reason="Required reopen reason",
        channel="Ticket channel to reopen (leave empty to use current channel)",
    )
    async def ticket_reopen_reason(
        interaction: discord.Interaction,
        reason: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await interaction.response.send_message(
                "❌ A reopen reason is required.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_reopen_ticket_channel is None:
            return await interaction.followup.send(
                "❌ Ticket reopen service is unavailable.",
                ephemeral=True,
            )

        row = await _repair_open_drift_for_reopen(
            channel=ch,
            row=row,
            actor=interaction.user,
        )
        status = _ticket_status(row)

        if status == "deleted":
            return await interaction.followup.send(
                "❌ Deleted tickets cannot be reopened.",
                ephemeral=True,
            )

        if status in {"open", "claimed"} and _channel_looks_open(ch):
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already open.",
                ephemeral=True,
            )

        owner = await _ticket_owner(ch, row)
        owner_member = owner if isinstance(owner, discord.Member) else None

        ok = await service_reopen_ticket_channel(
            channel=ch,
            owner=owner_member,
            actor=interaction.user,
            reason=clean_reason,
        )
        if not ok:
            return await interaction.followup.send(
                "❌ Failed to reopen this ticket.",
                ephemeral=True,
            )

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Reopen reason: {clean_reason}",
            )
        except Exception:
            pass

        try:
            await ch.send(
                f"♻️ Ticket reopened by {interaction.user.mention}.\n"
                f"**Reason:** {clean_reason}"
            )
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        await interaction.followup.send(f"✅ Reopened {ch.mention}.", ephemeral=True)

    @tree.command(
        name="ticket_nudge_owner",
        description="Ping the ticket owner with an optional message.",
    )
    @app_commands.describe(
        message="Optional nudge message",
        channel="Ticket channel to use (leave empty to use current channel)",
    )
    async def ticket_nudge_owner(
        interaction: discord.Interaction,
        message: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot nudge the owner of a deleted ticket.", "ephemeral": True},
            )

        owner = await _ticket_owner(ch, row)
        if owner is None:
            return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

        body = _safe_str(message, "Staff is waiting on your response.")
        try:
            await ch.send(f"{getattr(owner, 'mention', '')} ⏰ {body}")
            mark_ticket_activity(ch.id)
        except Exception as e:
            return await reply_once(interaction, {"content": f"❌ Failed nudging owner: {e}", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Nudged the ticket owner in {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_nudge_assignee",
        description="Ping the assigned staff member with an optional message.",
    )
    @app_commands.describe(
        message="Optional nudge message",
        channel="Ticket channel to use (leave empty to use current channel)",
    )
    async def ticket_nudge_assignee(
        interaction: discord.Interaction,
        message: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status in {"deleted", "closed"}:
            return await reply_once(
                interaction,
                {"content": "❌ Only active tickets can nudge an assignee.", "ephemeral": True},
            )

        assignee = await _ticket_assignee(ch, row)
        if assignee is None:
            return await reply_once(interaction, {"content": "❌ This ticket is currently unassigned.", "ephemeral": True})

        body = _safe_str(message, "This ticket needs staff attention.")
        try:
            await ch.send(f"{assignee.mention} 🔔 {body}")
            mark_ticket_activity(ch.id)
        except Exception as e:
            return await reply_once(interaction, {"content": f"❌ Failed nudging assignee: {e}", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Nudged the assigned staff member in {ch.mention}.", "ephemeral": True})
