from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import (
    _staff_check,
    reply_once,
    safe_defer,
    mark_ticket_activity,
    RUNTIME_STATS,
)

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

try:
    from ..tickets_new.panel import (
        send_ticket_panel,
        send_staff_ghost_ticket_panel,
    )
except Exception:
    send_ticket_panel = None  # type: ignore
    send_staff_ghost_ticket_panel = None  # type: ignore

try:
    from ..events_new.members import (
        run_full_member_sync_for_guild,
        run_departed_reconciliation_for_guild,
    )
except Exception:
    async def run_full_member_sync_for_guild(guild: discord.Guild):  # type: ignore
        return {"processed": 0, "failed": 0, "total_seen": 0}

    async def run_departed_reconciliation_for_guild(guild: discord.Guild):  # type: ignore
        return {"checked": 0, "marked_departed": 0}

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
        staff_delete_closed_ticket as transcript_staff_delete_closed_ticket,
    )
except Exception:
    transcript_post_to_channel = None  # type: ignore
    transcript_staff_delete_closed_ticket = None  # type: ignore

try:
    from ..tickets_new.service import (
        assign_ticket as service_assign_ticket,
        unclaim_ticket as service_unclaim_ticket,
        transfer_ticket as service_transfer_ticket,
        set_ticket_priority as service_set_ticket_priority,
        add_internal_note as service_add_internal_note,
        list_internal_notes as service_list_internal_notes,
        reopen_ticket_channel as service_reopen_ticket_channel,
        mark_ticket_deleted as service_mark_ticket_deleted,
        mark_ticket_closed as service_mark_ticket_closed,
        list_open_ticket_queue as service_list_open_ticket_queue,
        list_unclaimed_tickets as service_list_unclaimed_tickets,
        list_tickets_claimed_by_staff as service_list_tickets_claimed_by_staff,
    )
except Exception:
    service_assign_ticket = None  # type: ignore
    service_unclaim_ticket = None  # type: ignore
    service_transfer_ticket = None  # type: ignore
    service_set_ticket_priority = None  # type: ignore
    service_add_internal_note = None  # type: ignore
    service_list_internal_notes = None  # type: ignore
    service_reopen_ticket_channel = None  # type: ignore
    service_mark_ticket_deleted = None  # type: ignore
    service_mark_ticket_closed = None  # type: ignore
    service_list_open_ticket_queue = None  # type: ignore
    service_list_unclaimed_tickets = None  # type: ignore
    service_list_tickets_claimed_by_staff = None  # type: ignore


_VALID_PRIORITIES = {"low", "medium", "high", "urgent"}


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        return _safe_str((row or {}).get("status"), "unknown").lower()
    except Exception:
        return "unknown"


def _is_ticket_channel(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    if isinstance(row, dict):
        return True
    try:
        return bool(is_verification_ticket_channel(channel))
    except Exception:
        return False


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await reply_once(
            interaction,
            {"content": "❌ Must be used in a ticket text channel.", "ephemeral": True},
        )
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not _is_ticket_channel(ch, row):
        await reply_once(
            interaction,
            {
                "content": f"❌ `{ch.name}` is not recognized as a ticket channel.",
                "ephemeral": True,
            },
        )
        return None, None

    return ch, row


async def _owner_for_ticket(
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> Optional[discord.Member | discord.User]:
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


def _member_label(guild: discord.Guild, user_id: Any, fallback: str = "Unassigned") -> str:
    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return fallback
    member = guild.get_member(uid)
    if member:
        return f"{member.mention} (`{uid}`)"
    return f"`{uid}`"


def _ticket_summary_line(guild: discord.Guild, row: Dict[str, Any]) -> str:
    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    owner_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)
    assigned_to = _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0)
    priority = _safe_str(row.get("priority"), "medium")
    status = _safe_str(row.get("status"), "open")
    number = _safe_str(row.get("ticket_number"))
    title = _safe_str(row.get("title")) or _safe_str(row.get("channel_name")) or "ticket"
    number_prefix = f"#{number} " if number else ""
    channel_ref = f"<#{channel_id}>" if channel_id > 0 else f"`{title}`"
    owner_ref = _member_label(guild, owner_id, "Unknown owner")
    assignee_ref = _member_label(guild, assigned_to, "Unassigned")
    return f"• {number_prefix}{channel_ref} • owner={owner_ref} • assignee={assignee_ref} • `{status}` • `{priority}`"


def _queue_embed(title: str, guild: discord.Guild, rows: List[Dict[str, Any]], empty_text: str) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    if not rows:
        embed.description = empty_text
        return embed

    lines = [_ticket_summary_line(guild, row) for row in rows[:15]]
    embed.description = "\n".join(lines)[:4000]
    embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
    return embed


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


async def _cleanup_ticket_timer_state(channel_id: int) -> None:
    try:
        _cancel_kick_timer(channel_id)
    except Exception:
        pass

    try:
        await kick_timer_persist_delete(int(channel_id))
    except Exception:
        pass


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
            owner,
            closed_by=actor,
            decision=reason,
        )
        return True, None
    except Exception:
        return False, None


def register_ticket_admin_commands(bot, tree) -> None:
    @tree.command(
        name="post_ticket_panel",
        description="Post the public ticket panel in this channel.",
    )
    async def post_ticket_panel_cmd(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Must be used in a text channel.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        try:
            if send_ticket_panel is None:
                return await interaction.followup.send(
                    "❌ Ticket panel sender is unavailable.",
                    ephemeral=True,
                )

            await send_ticket_panel(channel)
            await interaction.followup.send(
                f"✅ Public ticket panel posted in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to post ticket panel: {e}",
                ephemeral=True,
            )

    @tree.command(
        name="post_ghost_ticket_panel",
        description="Post the staff-only ghost ticket panel in this channel.",
    )
    async def post_ghost_ticket_panel_cmd(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Must be used in a text channel.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        try:
            if send_staff_ghost_ticket_panel is None:
                return await interaction.followup.send(
                    "❌ Ghost ticket panel sender is unavailable.",
                    ephemeral=True,
                )

            await send_staff_ghost_ticket_panel(channel)
            await interaction.followup.send(
                f"✅ Staff-only ghost ticket panel posted in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to post ghost ticket panel: {e}",
                ephemeral=True,
            )

    @tree.command(
        name="sync_members_now",
        description="Run a full member sync for this guild.",
    )
    async def sync_members_now(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This command must be run in the server.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        try:
            summary = await run_full_member_sync_for_guild(guild)
            await interaction.followup.send(
                "✅ Member sync complete. "
                f"Processed: {summary.get('processed', 0)} | "
                f"Failed: {summary.get('failed', 0)} | "
                f"Seen: {summary.get('total_seen', 0)}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Member sync failed: {e}",
                ephemeral=True,
            )

    @tree.command(
        name="reconcile_departed_members",
        description="Mark missing users as departed in the dashboard database.",
    )
    async def reconcile_departed_members(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This command must be run in the server.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        try:
            summary = await run_departed_reconciliation_for_guild(guild)
            await interaction.followup.send(
                "✅ Departed reconciliation complete. "
                f"Checked: {summary.get('checked', 0)} | "
                f"Marked departed: {summary.get('marked_departed', 0)}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Departed reconciliation failed: {e}",
                ephemeral=True,
            )

    @tree.command(
        name="close_ticket",
        description="Close a ticket without deleting the channel.",
    )
    @app_commands.describe(
        channel="Ticket channel to close (leave empty to use the current channel)",
        reason="Optional close reason stored in transcript/close metadata",
    )
    async def close_ticket_slash(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_mark_ticket_closed is None:
            return await reply_once(
                interaction,
                {"content": "❌ Ticket close service is unavailable.", "ephemeral": True},
            )

        await safe_defer(interaction, ephemeral=True)

        status = _ticket_status(row)
        if status == "deleted":
            return await interaction.followup.send(
                "❌ This ticket is already marked deleted and cannot be closed.",
                ephemeral=True,
            )

        if status == "closed":
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already closed.",
                ephemeral=True,
            )

        guild = interaction.guild
        owner = await _owner_for_ticket(ch, row)
        actor_member = _actor_member(guild, interaction.user) or interaction.user
        decision = (reason.strip() if reason else "STAFF CLOSED")

        await _cleanup_ticket_timer_state(ch.id)

        try:
            ok = await service_mark_ticket_closed(
                channel=ch,
                closed_by=actor_member,
                reason=decision,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed closing ticket state: `{e}`",
                ephemeral=True,
            )

        if not ok:
            return await interaction.followup.send(
                "❌ Failed to close this ticket.",
                ephemeral=True,
            )

        transcript_ok, transcript_url = await _post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor_member,
            reason=decision,
        )

        try:
            await ch.send(
                f"🔒 Ticket closed by {interaction.user.mention}.\n"
                f"**Reason:** {decision}\n"
                f"Use `/ticket_reopen` if this needs to be reopened."
            )
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        try:
            RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
        except Exception:
            pass

        msg = f"✅ Closed {ch.mention}."
        if transcript_ok and transcript_url:
            msg += f"\n🧾 Transcript: {transcript_url}"
        elif not transcript_ok:
            msg += "\n⚠️ Transcript generation failed."

        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="ticket_claim",
        description="Claim the current ticket for yourself.",
    )
    @app_commands.describe(channel="Ticket channel to claim (leave empty to use current channel)")
    async def ticket_claim(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_assign_ticket is None:
            return await reply_once(interaction, {"content": "❌ Ticket claim service is unavailable.", "ephemeral": True})

        status = _ticket_status(row)
        if status in {"closed", "deleted"}:
            return await reply_once(
                interaction,
                {"content": "❌ You cannot claim a closed or deleted ticket.", "ephemeral": True},
            )

        ok = await service_assign_ticket(channel_id=ch.id, staff_member=interaction.user)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to claim this ticket.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Claimed {ch.mention}.", "ephemeral": True})
        try:
            await ch.send(f"👤 Ticket claimed by {interaction.user.mention}.")
        except Exception:
            pass

    @tree.command(
        name="ticket_unclaim",
        description="Remove the current ticket assignment.",
    )
    @app_commands.describe(channel="Ticket channel to unclaim (leave empty to use current channel)")
    async def ticket_unclaim(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_unclaim_ticket is None:
            return await reply_once(interaction, {"content": "❌ Ticket unclaim service is unavailable.", "ephemeral": True})

        status = _ticket_status(row)
        if status in {"closed", "deleted"}:
            return await reply_once(
                interaction,
                {"content": "❌ You cannot unclaim a closed or deleted ticket.", "ephemeral": True},
            )

        ok = await service_unclaim_ticket(channel_id=ch.id, actor=interaction.user)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to unclaim this ticket.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Unclaimed {ch.mention}.", "ephemeral": True})
        try:
            await ch.send(f"📭 Ticket unclaimed by {interaction.user.mention}.")
        except Exception:
            pass

    @tree.command(
        name="ticket_transfer",
        description="Transfer the current ticket to another staff member.",
    )
    @app_commands.describe(
        member="Staff member to transfer the ticket to",
        channel="Ticket channel to transfer (leave empty to use current channel)",
    )
    async def ticket_transfer(
        interaction: discord.Interaction,
        member: discord.Member,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_transfer_ticket is None:
            return await reply_once(interaction, {"content": "❌ Ticket transfer service is unavailable.", "ephemeral": True})

        status = _ticket_status(row)
        if status in {"closed", "deleted"}:
            return await reply_once(
                interaction,
                {"content": "❌ You cannot transfer a closed or deleted ticket.", "ephemeral": True},
            )

        ok = await service_transfer_ticket(
            channel_id=ch.id,
            to_staff_member=member,
            actor=interaction.user,
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to transfer this ticket.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Transferred {ch.mention} to {member.mention}.", "ephemeral": True})
        try:
            await ch.send(f"🔁 Ticket transferred to {member.mention} by {interaction.user.mention}.")
        except Exception:
            pass

    @tree.command(
        name="ticket_reopen",
        description="Reopen a closed ticket channel.",
    )
    @app_commands.describe(
        channel="Closed ticket channel to reopen (leave empty to use current channel)",
        reason="Optional reopen reason",
    )
    async def ticket_reopen(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_reopen_ticket_channel is None:
            return await reply_once(interaction, {"content": "❌ Ticket reopen service is unavailable.", "ephemeral": True})

        await safe_defer(interaction, ephemeral=True)

        status = _ticket_status(row)
        if status == "deleted":
            return await interaction.followup.send(
                "❌ Deleted tickets cannot be reopened.",
                ephemeral=True,
            )

        if status in {"open", "claimed"}:
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already open.",
                ephemeral=True,
            )

        owner = await _owner_for_ticket(ch, row)
        owner_member = owner if isinstance(owner, discord.Member) else None

        ok = await service_reopen_ticket_channel(
            channel=ch,
            owner=owner_member,
            actor=interaction.user,
            reason=reason,
        )
        if not ok:
            return await interaction.followup.send("❌ Failed to reopen this ticket.", ephemeral=True)

        await interaction.followup.send(f"✅ Reopened {ch.mention}.", ephemeral=True)

        try:
            if reason and reason.strip():
                await ch.send(
                    f"♻️ Ticket reopened by {interaction.user.mention}.\n"
                    f"**Reason:** {reason.strip()}"
                )
            else:
                await ch.send(f"♻️ Ticket reopened by {interaction.user.mention}.")
        except Exception:
            pass

    @tree.command(
        name="ticket_delete",
        description="Permanently delete a closed ticket and post transcript metadata.",
    )
    @app_commands.describe(
        channel="Closed ticket channel to delete (leave empty to use current channel)",
        reason="Optional delete reason",
    )
    async def ticket_delete(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        await safe_defer(interaction, ephemeral=True)

        status = _ticket_status(row)
        if status == "deleted":
            return await interaction.followup.send("ℹ️ This ticket is already deleted.", ephemeral=True)

        if status != "closed":
            return await interaction.followup.send(
                "❌ Ticket must be closed before deletion. Close it first, then delete it.",
                ephemeral=True,
            )

        delete_reason = reason.strip() if reason and reason.strip() else "Deleted by staff"

        await _cleanup_ticket_timer_state(ch.id)

        if callable(transcript_staff_delete_closed_ticket):
            try:
                result = await transcript_staff_delete_closed_ticket(
                    channel=ch,
                    staff_member=interaction.user,
                    is_ghost=bool((row or {}).get("is_ghost")),
                    reason=delete_reason,
                )
            except Exception as e:
                return await interaction.followup.send(
                    f"❌ Delete flow failed: `{e}`",
                    ephemeral=True,
                )

            if not bool((result or {}).get("ok")):
                return await interaction.followup.send(
                    f"❌ {str((result or {}).get('reason') or 'Failed deleting ticket.')}",
                    ephemeral=True,
                )

            msg = "✅ Ticket deleted."
            transcript_url = _safe_str((result or {}).get("transcript_url"))
            if transcript_url:
                msg += f"\n🧾 Transcript: {transcript_url}"
            await interaction.followup.send(msg, ephemeral=True)
            return

        if service_mark_ticket_deleted is None:
            return await interaction.followup.send(
                "❌ Ticket delete service is unavailable.",
                ephemeral=True,
            )

        owner = await _owner_for_ticket(ch, row)
        actor_member = _actor_member(interaction.guild, interaction.user) or interaction.user

        transcript_ok, transcript_url = await _post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor_member,
            reason=delete_reason,
        )

        try:
            ok = await service_mark_ticket_deleted(
                channel_id=ch.id,
                deleted_by=interaction.user,
                reason=delete_reason,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed marking ticket deleted in DB: `{e}`",
                ephemeral=True,
            )

        if not ok:
            return await interaction.followup.send(
                "❌ Failed to mark this ticket deleted.",
                ephemeral=True,
            )

        try:
            await ch.delete(reason=f"{delete_reason} | actor={interaction.user}")
        except discord.Forbidden:
            return await interaction.followup.send(
                "⚠️ Ticket was marked deleted, but I do not have permission to delete the channel.",
                ephemeral=True,
            )
        except discord.NotFound:
            msg = "✅ Ticket was already gone. Delete state was recorded."
            if transcript_ok and transcript_url:
                msg += f"\n🧾 Transcript: {transcript_url}"
            return await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            return await interaction.followup.send(
                f"⚠️ Ticket was marked deleted, but channel deletion failed: `{e}`",
                ephemeral=True,
            )

        msg = "✅ Ticket deleted."
        if transcript_ok and transcript_url:
            msg += f"\n🧾 Transcript: {transcript_url}"
        elif not transcript_ok:
            msg += "\n⚠️ Transcript generation failed."
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="ticket_transcript",
        description="Generate and post a transcript without closing the ticket.",
    )
    @app_commands.describe(
        channel="Ticket channel to transcript (leave empty to use current channel)",
        reason="Optional transcript label",
    )
    async def ticket_transcript(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        await safe_defer(interaction, ephemeral=True)

        owner = await _owner_for_ticket(ch, row)
        actor_member = _actor_member(interaction.guild, interaction.user) or interaction.user
        transcript_reason = reason.strip() if reason and reason.strip() else "STAFF TRANSCRIPT"

        transcript_ok, transcript_url = await _post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor_member,
            reason=transcript_reason,
        )
        if not transcript_ok:
            return await interaction.followup.send(
                "❌ Failed generating transcript.",
                ephemeral=True,
            )

        msg = f"✅ Transcript posted for {ch.mention}."
        if transcript_url:
            msg += f"\n🧾 Transcript: {transcript_url}"
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="ticket_priority",
        description="Set ticket priority.",
    )
    @app_commands.describe(
        priority="Choose: low, medium, high, urgent",
        channel="Ticket channel to update (leave empty to use current channel)",
    )
    async def ticket_priority(
        interaction: discord.Interaction,
        priority: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        clean = _safe_str(priority).lower()
        if clean not in _VALID_PRIORITIES:
            return await reply_once(
                interaction,
                {"content": "❌ Priority must be one of: `low`, `medium`, `high`, `urgent`.", "ephemeral": True},
            )

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_set_ticket_priority is None:
            return await reply_once(interaction, {"content": "❌ Ticket priority service is unavailable.", "ephemeral": True})

        if _ticket_status(row) == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot update priority on a deleted ticket.", "ephemeral": True},
            )

        ok = await service_set_ticket_priority(channel_id=ch.id, priority=clean, actor=interaction.user)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to update ticket priority.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Set {ch.mention} priority to `{clean}`.", "ephemeral": True})
        try:
            await ch.send(f"🚦 Priority updated to `{clean}` by {interaction.user.mention}.")
        except Exception:
            pass

    @tree.command(
        name="ticket_info",
        description="Show ticket details for the current channel.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_info(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        row = row or {}
        owner = await _owner_for_ticket(ch, row)
        notes: List[Dict[str, Any]] = []
        if callable(service_list_internal_notes):
            try:
                notes = await service_list_internal_notes(channel_id=ch.id, limit=5)
            except Exception:
                notes = []

        embed = discord.Embed(
            title="🎫 Ticket Info",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(
            name="Owner",
            value=(
                owner.mention
                if isinstance(owner, discord.Member)
                else _member_label(ch.guild, row.get("owner_id") or row.get("user_id"), "Unknown")
            ),
            inline=True,
        )
        embed.add_field(name="Assigned", value=_member_label(ch.guild, row.get("assigned_to"), "Unassigned"), inline=True)
        embed.add_field(name="Status", value=f"`{_safe_str(row.get('status'), 'unknown')}`", inline=True)
        embed.add_field(name="Priority", value=f"`{_safe_str(row.get('priority'), 'medium')}`", inline=True)
        embed.add_field(name="Category", value=f"`{_safe_str(row.get('category'), 'unknown')}`", inline=True)
        embed.add_field(name="Ticket Number", value=f"`{_safe_str(row.get('ticket_number'), 'n/a')}`", inline=True)
        embed.add_field(name="Source", value=f"`{_safe_str(row.get('source'), 'unknown')}`", inline=True)
        embed.add_field(name="Created At", value=f"`{_safe_str(row.get('created_at'), 'unknown')}`", inline=True)
        embed.add_field(name="Transcript", value=_safe_str(row.get("transcript_url"), "—"), inline=False)

        if notes:
            lines = []
            for note in notes[:3]:
                preview = _safe_str(note.get("note_body"))[:120]
                author = _safe_str(note.get("author_name"), "unknown")
                lines.append(f"• `{author}` — {preview}")
            embed.add_field(name="Recent Notes", value="\n".join(lines)[:1024], inline=False)

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_note_add",
        description="Add an internal staff note to a ticket.",
    )
    @app_commands.describe(
        note="Internal note text",
        pin="Whether to pin this internal note",
        channel="Ticket channel to update (leave empty to use current channel)",
    )
    async def ticket_note_add(
        interaction: discord.Interaction,
        note: str,
        pin: Optional[bool] = False,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_add_internal_note is None:
            return await reply_once(interaction, {"content": "❌ Ticket notes service is unavailable.", "ephemeral": True})

        clean_note = _safe_str(note)
        if not clean_note:
            return await reply_once(interaction, {"content": "❌ Note cannot be empty.", "ephemeral": True})

        ok = await service_add_internal_note(
            channel_id=ch.id,
            author=interaction.user,
            note=clean_note,
            is_pinned=bool(pin),
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed adding internal note.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Internal note added to {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_note_list",
        description="List recent internal staff notes for a ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_note_list(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_list_internal_notes is None:
            return await reply_once(interaction, {"content": "❌ Ticket notes service is unavailable.", "ephemeral": True})

        notes = await service_list_internal_notes(channel_id=ch.id, limit=10)
        if not notes:
            return await reply_once(interaction, {"content": "ℹ️ No internal notes on this ticket yet.", "ephemeral": True})

        embed = discord.Embed(
            title="📝 Ticket Notes",
            description=f"{ch.mention}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        lines = []
        for note_row in notes[:10]:
            author = _safe_str(note_row.get("author_name"), "unknown")
            created_at = _safe_str(note_row.get("created_at"), "unknown")
            body = _safe_str(note_row.get("note_body"))[:180]
            pin_tag = "📌 " if bool(note_row.get("is_pinned")) else ""
            lines.append(f"{pin_tag}`{author}` • `{created_at}`\n{body}")
        embed.add_field(name="Recent Notes", value="\n\n".join(lines)[:1024], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="tickets_mine",
        description="List open/claimed tickets currently assigned to you.",
    )
    async def tickets_mine(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if service_list_tickets_claimed_by_staff is None:
            return await reply_once(
                interaction,
                {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True},
            )

        rows = await service_list_tickets_claimed_by_staff(
            guild_id=guild.id,
            staff_id=interaction.user.id,
        )
        embed = _queue_embed("🎫 My Claimed Tickets", guild, rows, "You have no claimed tickets.")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="tickets_open",
        description="List the current open ticket queue.",
    )
    async def tickets_open(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if service_list_open_ticket_queue is None:
            return await reply_once(
                interaction,
                {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True},
            )

        rows = await service_list_open_ticket_queue(guild_id=guild.id)
        embed = _queue_embed("📂 Open Ticket Queue", guild, rows, "No open or claimed tickets found.")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="tickets_unassigned",
        description="List open tickets with no assignee.",
    )
    async def tickets_unassigned(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if service_list_unclaimed_tickets is None:
            return await reply_once(
                interaction,
                {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True},
            )

        rows = await service_list_unclaimed_tickets(guild_id=guild.id)
        embed = _queue_embed("📭 Unassigned Tickets", guild, rows, "No unassigned open tickets found.")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
