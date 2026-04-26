from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once, safe_defer, mark_ticket_activity, RUNTIME_STATS

# Reuse the hardened ticket-admin helper/service layer without registering the
# legacy top-level ticket_* commands. Importing ticket_admin is safe; commands
# are only registered when register_ticket_admin_commands(...) is called.
from . import ticket_admin as legacy


_PRIORITIES = {
    "low",
    "medium",
    "high",
    "urgent",
}


ticket_group = app_commands.Group(
    name="ticket",
    description="Ticket actions and staff tools.",
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if not _staff_check(interaction):
        await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        return False
    return True


@ticket_group.command(
    name="info",
    description="Show ticket details for the current or selected channel.",
)
@app_commands.describe(channel="Ticket channel to inspect. Leave empty to use the current channel.")
async def ticket_info(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    fresh = await legacy._refresh_ticket_row(ch)
    if fresh:
        row = fresh

    owner = await legacy._owner_for_ticket(ch, row)
    notes = []
    try:
        if legacy.service_list_internal_notes is not None:
            notes = await legacy.service_list_internal_notes(channel_id=ch.id, limit=3)
    except Exception:
        notes = []

    embed = legacy._ticket_info_embed(
        channel=ch,
        row=dict(row or {}),
        owner=owner,
        notes=list(notes or []),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@ticket_group.command(
    name="claim",
    description="Claim the current or selected ticket for yourself.",
)
@app_commands.describe(channel="Ticket channel to claim. Leave empty to use the current channel.")
async def ticket_claim(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_assign_ticket is None:
        return await reply_once(interaction, {"content": "❌ Ticket claim service is unavailable.", "ephemeral": True})

    status = legacy._ticket_status(row)
    if status in {"closed", "deleted"} or legacy._ticket_effectively_closed(channel=ch, row=row):
        return await reply_once(
            interaction,
            {"content": "❌ You cannot claim a closed or deleted ticket.", "ephemeral": True},
        )

    ok = await legacy.service_assign_ticket(channel_id=ch.id, staff_member=interaction.user)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to claim this ticket.", "ephemeral": True})

    await reply_once(interaction, {"content": f"✅ Claimed {ch.mention}.", "ephemeral": True})
    try:
        await ch.send(f"👤 Ticket claimed by {interaction.user.mention}.")
    except Exception:
        pass


@ticket_group.command(
    name="unclaim",
    description="Remove assignment from the current or selected ticket.",
)
@app_commands.describe(channel="Ticket channel to unclaim. Leave empty to use the current channel.")
async def ticket_unclaim(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_unclaim_ticket is None:
        return await reply_once(interaction, {"content": "❌ Ticket unclaim service is unavailable.", "ephemeral": True})

    status = legacy._ticket_status(row)
    if status in {"closed", "deleted"} or legacy._ticket_effectively_closed(channel=ch, row=row):
        return await reply_once(
            interaction,
            {"content": "❌ You cannot unclaim a closed or deleted ticket.", "ephemeral": True},
        )

    ok = await legacy.service_unclaim_ticket(channel_id=ch.id, actor=interaction.user)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to unclaim this ticket.", "ephemeral": True})

    await reply_once(interaction, {"content": f"✅ Unclaimed {ch.mention}.", "ephemeral": True})
    try:
        await ch.send(f"📭 Ticket unclaimed by {interaction.user.mention}.")
    except Exception:
        pass


@ticket_group.command(
    name="transfer",
    description="Transfer a ticket to another staff member.",
)
@app_commands.describe(
    member="Staff member to transfer the ticket to.",
    channel="Ticket channel to transfer. Leave empty to use the current channel.",
)
async def ticket_transfer(
    interaction: discord.Interaction,
    member: discord.Member,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_transfer_ticket is None:
        return await reply_once(interaction, {"content": "❌ Ticket transfer service is unavailable.", "ephemeral": True})

    status = legacy._ticket_status(row)
    if status in {"closed", "deleted"} or legacy._ticket_effectively_closed(channel=ch, row=row):
        return await reply_once(
            interaction,
            {"content": "❌ You cannot transfer a closed or deleted ticket.", "ephemeral": True},
        )

    ok = await legacy.service_transfer_ticket(
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


@ticket_group.command(
    name="priority",
    description="Set ticket priority.",
)
@app_commands.describe(
    priority="Priority to apply.",
    channel="Ticket channel to update. Leave empty to use the current channel.",
)
@app_commands.choices(
    priority=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Urgent", value="urgent"),
    ]
)
async def ticket_priority(
    interaction: discord.Interaction,
    priority: app_commands.Choice[str],
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    value = str(priority.value or "").strip().lower()
    if value not in _PRIORITIES:
        return await reply_once(interaction, {"content": "❌ Invalid priority.", "ephemeral": True})

    if legacy.service_set_ticket_priority is None:
        return await reply_once(interaction, {"content": "❌ Ticket priority service is unavailable.", "ephemeral": True})

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await reply_once(interaction, {"content": "❌ You cannot update a deleted ticket.", "ephemeral": True})

    ok = await legacy.service_set_ticket_priority(
        channel_id=ch.id,
        priority=value,
        actor=interaction.user,
    )
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to update ticket priority.", "ephemeral": True})

    await reply_once(interaction, {"content": f"✅ Set {ch.mention} priority to `{value}`.", "ephemeral": True})
    try:
        await ch.send(f"🚦 Priority set to **{value}** by {interaction.user.mention}.")
    except Exception:
        pass


@ticket_group.command(
    name="close",
    description="Close a ticket without deleting the channel.",
)
@app_commands.describe(
    channel="Ticket channel to close. Leave empty to use the current channel.",
    reason="Optional close reason.",
)
async def ticket_close(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
):
    if not await _staff_only(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_mark_ticket_closed is None:
        return await interaction.followup.send("❌ Ticket close service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is already marked deleted and cannot be closed.", ephemeral=True)

    if legacy._ticket_effectively_closed(channel=ch, row=row) and status == "closed":
        return await interaction.followup.send(f"ℹ️ {ch.mention} is already closed.", ephemeral=True)

    owner = await legacy._owner_for_ticket(ch, row)
    actor_member = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    decision = reason.strip() if reason and reason.strip() else "STAFF CLOSED"

    await legacy._cleanup_ticket_timer_state(ch.id)

    try:
        ok = await legacy.service_mark_ticket_closed(
            channel=ch,
            closed_by=actor_member,
            reason=decision,
        )
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed closing ticket state: `{e}`", ephemeral=True)

    if not ok:
        return await interaction.followup.send("❌ Failed to close this ticket.", ephemeral=True)

    moved_to_archive = await legacy._move_ticket_to_archive_if_configured(ch)
    transcript_ok, transcript_url = await legacy._post_ticket_transcript(
        channel=ch,
        owner=owner,
        actor=actor_member,
        reason=decision,
    )

    try:
        await ch.send(
            f"🔒 Ticket closed by {interaction.user.mention}.\n"
            f"**Reason:** {decision}"
            + (f"\n📦 Moved to archive category: **{ch.category.name}**" if moved_to_archive and ch.category else "")
        )
    except Exception:
        pass

    try:
        mark_ticket_activity(ch.id)
        RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
    except Exception:
        pass

    lines = [f"✅ Closed {ch.mention}."]
    if moved_to_archive and ch.category is not None:
        lines.append(f"📦 Moved to archive category: **{ch.category.name}**")
    if transcript_ok and transcript_url:
        lines.append(f"🧾 Transcript: {transcript_url}")
    elif not transcript_ok:
        lines.append("⚠️ Transcript generation failed.")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@ticket_group.command(
    name="reopen",
    description="Reopen a closed ticket.",
)
@app_commands.describe(
    channel="Ticket channel to reopen. Leave empty to use the current channel.",
    reason="Optional reopen reason.",
)
async def ticket_reopen(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
):
    if not await _staff_only(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_reopen_ticket_channel is None:
        return await interaction.followup.send("❌ Ticket reopen service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is deleted and cannot be reopened.", ephemeral=True)

    if legacy._ticket_effectively_open(channel=ch, row=row) and status in {"open", "claimed"}:
        return await interaction.followup.send(f"ℹ️ {ch.mention} is already open.", ephemeral=True)

    owner = await legacy._owner_for_ticket(ch, row)
    owner_member = owner if isinstance(owner, discord.Member) else None
    actor_member = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    reopen_reason = reason.strip() if reason and reason.strip() else "Reopened from /ticket reopen"

    try:
        ok = await legacy.service_reopen_ticket_channel(
            channel=ch,
            owner=owner_member,
            actor=actor_member,
            reason=reopen_reason,
        )
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed reopening ticket: `{e}`", ephemeral=True)

    if not ok:
        return await interaction.followup.send("❌ Failed to reopen this ticket.", ephemeral=True)

    moved_to_active = await legacy._move_ticket_to_active_if_configured(ch)

    try:
        await ch.send(
            f"🔓 Ticket reopened by {interaction.user.mention}.\n"
            f"**Reason:** {reopen_reason}"
            + (f"\n📂 Moved to active category: **{ch.category.name}**" if moved_to_active and ch.category else "")
        )
    except Exception:
        pass

    await interaction.followup.send(f"✅ Reopened {ch.mention}.", ephemeral=True)


@ticket_group.command(
    name="transcript",
    description="Post a transcript for a ticket.",
)
@app_commands.describe(
    channel="Ticket channel to transcript. Leave empty to use the current channel.",
    reason="Optional transcript reason.",
)
async def ticket_transcript(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
):
    if not await _staff_only(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    owner = await legacy._owner_for_ticket(ch, row)
    actor_member = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    transcript_reason = reason.strip() if reason and reason.strip() else "Manual transcript from /ticket transcript"

    transcript_ok, transcript_url = await legacy._post_ticket_transcript(
        channel=ch,
        owner=owner,
        actor=actor_member,
        reason=transcript_reason,
    )

    if not transcript_ok:
        return await interaction.followup.send("❌ Transcript generation failed.", ephemeral=True)

    lines = [f"✅ Transcript posted for {ch.mention}."]
    if transcript_url:
        lines.append(f"🧾 {transcript_url}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


def register_public_ticket_group_commands(bot, tree) -> None:
    _ = bot
    existing = None
    try:
        existing = tree.get_command("ticket", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        try:
            print("ℹ️ public_ticket_group: /ticket already registered; skipping")
        except Exception:
            pass
        return

    tree.add_command(ticket_group)
    try:
        print("✅ public_ticket_group: registered /ticket grouped command")
    except Exception:
        pass


__all__ = ["register_public_ticket_group_commands", "ticket_group"]
