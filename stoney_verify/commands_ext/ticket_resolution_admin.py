from __future__ import annotations

from typing import Any, Dict, Optional

import asyncio

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import _staff_check, reply_once, mark_ticket_activity

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

try:
    from ..transcripts import (
        send_tickettool_style_transcript,
        post_or_replace_open_ticket_controls,
    )
except Exception:
    async def send_tickettool_style_transcript(*args, **kwargs) -> None:  # type: ignore
        return None

    async def post_or_replace_open_ticket_controls(*args, **kwargs):  # type: ignore
        return None

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


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _ticket_row_sync(channel_id: int) -> Optional[Dict[str, Any]]:
    try:
        sb = get_supabase()
    except Exception:
        sb = None

    if not sb:
        return None

    for field in ("channel_id", "discord_thread_id"):
        try:
            res = (
                sb.table("tickets")
                .select("*")
                .eq(field, str(int(channel_id)))
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if rows:
                return dict(rows[0])
        except Exception:
            continue
    return None


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    return await _run_blocking(_ticket_row_sync, int(channel.id))


def _is_ticket_channel(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    if isinstance(row, dict):
        return True
    try:
        return bool(is_verification_ticket_channel(channel))
    except Exception:
        return False


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await reply_once(interaction, {"content": "❌ Must be used in a ticket text channel.", "ephemeral": True})
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not _is_ticket_channel(ch, row):
        await reply_once(
            interaction,
            {"content": f"❌ `{ch.name}` is not recognized as a ticket channel.", "ephemeral": True},
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
        assigned_to = _safe_int((row or {}).get("assigned_to"), 0)
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


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        return _safe_str((row or {}).get("status"), "unknown").lower()
    except Exception:
        return "unknown"


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
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await reply_once(interaction, {"content": "❌ A close reason is required.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_mark_ticket_closed is None:
            return await reply_once(interaction, {"content": "❌ Ticket close service is unavailable.", "ephemeral": True})

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        status = _ticket_status(row)
        if status == "deleted":
            try:
                await interaction.followup.send(
                    "❌ This ticket is already marked deleted and cannot be resolved.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        if status == "closed":
            try:
                await interaction.followup.send(
                    f"ℹ️ {ch.mention} is already closed.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        owner = await _ticket_owner(ch, row)
        actor_member = _actor_member(interaction.guild, interaction.user)

        try:
            _cancel_kick_timer(ch.id)
        except Exception:
            pass

        try:
            await kick_timer_persist_delete(int(ch.id))
        except Exception:
            pass

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Resolution note: {clean_reason}",
            )
        except Exception:
            pass

        transcript_ok = True
        try:
            await send_tickettool_style_transcript(
                ch,
                owner,
                closed_by=actor_member,
                decision=clean_reason,
            )
        except Exception:
            transcript_ok = False

        try:
            ok = await service_mark_ticket_closed(
                channel=ch,
                closed_by=actor_member,
                reason=clean_reason,
            )
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"❌ Failed resolving ticket state: `{e}`",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        if not ok:
            try:
                await interaction.followup.send(
                    "❌ Failed to resolve this ticket.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

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

        try:
            await interaction.followup.send(
                (
                    f"✅ Resolved {ch.mention}."
                    if transcript_ok
                    else f"✅ Resolved {ch.mention}, but transcript generation failed."
                ),
                ephemeral=True,
            )
        except Exception:
            pass

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
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await reply_once(interaction, {"content": "❌ A reopen reason is required.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_reopen_ticket_channel is None:
            return await reply_once(interaction, {"content": "❌ Ticket reopen service is unavailable.", "ephemeral": True})

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Deleted tickets cannot be reopened.", "ephemeral": True},
            )

        if status in {"open", "claimed"}:
            return await reply_once(
                interaction,
                {"content": f"ℹ️ {ch.mention} is already open.", "ephemeral": True},
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
            return await reply_once(interaction, {"content": "❌ Failed to reopen this ticket.", "ephemeral": True})

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Reopen reason: {clean_reason}",
            )
        except Exception:
            pass

        try:
            await post_or_replace_open_ticket_controls(ch)
        except Exception as e:
            try:
                print(f"⚠️ Failed to restore open ticket controls after reopen for {ch.id}: {e}")
            except Exception:
                pass

        try:
            await ch.send(f"♻️ Ticket reopened by {interaction.user.mention}.\n**Reason:** {clean_reason}")
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        await reply_once(interaction, {"content": f"✅ Reopened {ch.mention}.", "ephemeral": True})

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
