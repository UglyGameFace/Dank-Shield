from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once, safe_defer, mark_ticket_activity, RUNTIME_STATS

# Reuse the hardened ticket-admin helper/service layer without registering the
# legacy top-level ticket_* commands. Importing these modules is safe; commands
# are only registered when their register_* functions are called.
from . import ticket_admin as legacy
from . import ticket_channel_admin as channel_legacy


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


# ============================================================
# Core ticket lifecycle/actions
# ============================================================

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


# ============================================================
# Channel access/management commands
# ============================================================

@ticket_group.command(
    name="add",
    description="Grant a member access to a ticket.",
)
@app_commands.describe(
    member="Member to add to the ticket.",
    channel="Ticket channel to update. Leave empty to use the current channel.",
)
async def ticket_add(
    interaction: discord.Interaction,
    member: discord.Member,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    status = channel_legacy._ticket_status(row)
    effectively_closed = channel_legacy._ticket_effectively_closed(channel=ch, row=row)

    if status == "deleted":
        return await reply_once(interaction, {"content": "❌ Cannot modify access on a deleted ticket.", "ephemeral": True})

    if member.bot:
        return await reply_once(interaction, {"content": "❌ Adding bots to tickets this way is not supported.", "ephemeral": True})

    if channel_legacy._member_is_ticket_owner(member, row):
        return await reply_once(interaction, {"content": "ℹ️ That member is already the ticket owner.", "ephemeral": True})

    try:
        existing = ch.overwrites_for(member)
        overwrite = channel_legacy._build_member_overwrite(existing, can_view=True, can_send=(not effectively_closed))
        await ch.set_permissions(
            member,
            overwrite=overwrite,
            reason=f"Ticket access granted by {interaction.user}",
        )
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed adding member to ticket: {e}", "ephemeral": True})

    try:
        if effectively_closed:
            await ch.send(
                f"➕ {member.mention} was added to the ticket by {interaction.user.mention}. "
                "They can view it, but the ticket is currently closed."
            )
        else:
            await ch.send(f"➕ {member.mention} was added to the ticket by {interaction.user.mention}.")
    except Exception:
        pass

    await channel_legacy._touch_ticket_channel(ch)
    await reply_once(interaction, {"content": f"✅ Added {member.mention} to {ch.mention}.", "ephemeral": True})


@ticket_group.command(
    name="remove",
    description="Remove a member's access from a ticket.",
)
@app_commands.describe(
    member="Member to remove from the ticket.",
    channel="Ticket channel to update. Leave empty to use the current channel.",
)
async def ticket_remove(
    interaction: discord.Interaction,
    member: discord.Member,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    status = channel_legacy._ticket_status(row)
    if status == "deleted":
        return await reply_once(interaction, {"content": "❌ Cannot modify access on a deleted ticket.", "ephemeral": True})

    if channel_legacy._member_is_ticket_owner(member, row):
        return await reply_once(
            interaction,
            {"content": "❌ You cannot remove the ticket owner. Transfer ownership first if needed.", "ephemeral": True},
        )

    if channel_legacy._member_is_staff_like(member):
        return await reply_once(
            interaction,
            {
                "content": "❌ This member has staff-level access. Remove their staff access separately if that is what you want.",
                "ephemeral": True,
            },
        )

    try:
        await ch.set_permissions(
            member,
            overwrite=None,
            reason=f"Ticket access removed by {interaction.user}",
        )
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed removing member from ticket: {e}", "ephemeral": True})

    try:
        await ch.send(f"➖ {member.mention} was removed from the ticket by {interaction.user.mention}.")
    except Exception:
        pass

    await channel_legacy._touch_ticket_channel(ch)
    await reply_once(interaction, {"content": f"✅ Removed {member.mention} from {ch.mention}.", "ephemeral": True})


@ticket_group.command(
    name="rename",
    description="Rename a non-numbered ticket channel.",
)
@app_commands.describe(
    name="New ticket channel name.",
    channel="Ticket channel to rename. Leave empty to use the current channel.",
)
async def ticket_rename(
    interaction: discord.Interaction,
    name: str,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    ticket_num = channel_legacy._ticket_number(row, ch)
    if ticket_num > 0 or channel_legacy._is_canonical_ticket_name(ch.name):
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ Manual renaming is disabled for numbered tickets.\n"
                    "This bot keeps canonical names like `ticket-0032` / `closed-0032` so close/reopen/delete state stays reliable."
                ),
                "ephemeral": True,
            },
        )

    new_name = channel_legacy._safe_str(name).lower().replace(" ", "-")
    if not new_name:
        return await reply_once(interaction, {"content": "❌ New channel name cannot be empty.", "ephemeral": True})

    try:
        await ch.edit(name=new_name, reason=f"Ticket renamed by {interaction.user}")
        await channel_legacy._persist_channel_name(ch)
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed renaming ticket: {e}", "ephemeral": True})

    try:
        await ch.send(f"✏️ Ticket renamed to `{new_name}` by {interaction.user.mention}.")
    except Exception:
        pass

    await channel_legacy._touch_ticket_channel(ch)
    await reply_once(interaction, {"content": f"✅ Renamed ticket to `{new_name}`.", "ephemeral": True})


@ticket_group.command(
    name="lock",
    description="Lock the ticket so the owner cannot reply.",
)
@app_commands.describe(channel="Ticket channel to lock. Leave empty to use the current channel.")
async def ticket_lock(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    status = channel_legacy._ticket_status(row)
    if status == "deleted":
        return await reply_once(interaction, {"content": "❌ Deleted tickets cannot be locked.", "ephemeral": True})

    if channel_legacy._ticket_effectively_closed(channel=ch, row=row):
        return await reply_once(
            interaction,
            {
                "content": (
                    "ℹ️ This ticket is already closed/archived, so the owner should already be reply-locked.\n"
                    "Use `/ticket reopen` if you want the owner to speak again."
                ),
                "ephemeral": True,
            },
        )

    owner = await channel_legacy._ticket_owner(ch, row)
    if owner is None or not isinstance(owner, discord.Member):
        return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner for this channel.", "ephemeral": True})

    try:
        existing = ch.overwrites_for(owner)
        overwrite = channel_legacy._build_member_overwrite(existing, can_view=True, can_send=False)
        await ch.set_permissions(owner, overwrite=overwrite, reason=f"Ticket locked by {interaction.user}")
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed locking ticket: {e}", "ephemeral": True})

    try:
        await ch.send(f"🔒 Ticket locked by {interaction.user.mention}. {owner.mention} can no longer reply.")
    except Exception:
        pass

    await channel_legacy._touch_ticket_channel(ch)
    await reply_once(interaction, {"content": f"✅ Locked {ch.mention}.", "ephemeral": True})


@ticket_group.command(
    name="unlock",
    description="Unlock the ticket so the owner can reply again.",
)
@app_commands.describe(channel="Ticket channel to unlock. Leave empty to use the current channel.")
async def ticket_unlock(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    status = channel_legacy._ticket_status(row)
    if status == "deleted":
        return await reply_once(interaction, {"content": "❌ Deleted tickets cannot be unlocked.", "ephemeral": True})

    if channel_legacy._ticket_effectively_closed(channel=ch, row=row):
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ Closed/archived tickets should stay reply-locked.\n"
                    "Use `/ticket reopen` if you want the owner to speak again."
                ),
                "ephemeral": True,
            },
        )

    owner = await channel_legacy._ticket_owner(ch, row)
    if owner is None or not isinstance(owner, discord.Member):
        return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner for this channel.", "ephemeral": True})

    try:
        existing = ch.overwrites_for(owner)
        overwrite = channel_legacy._build_member_overwrite(existing, can_view=True, can_send=True)
        await ch.set_permissions(owner, overwrite=overwrite, reason=f"Ticket unlocked by {interaction.user}")
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed unlocking ticket: {e}", "ephemeral": True})

    try:
        await ch.send(f"🔓 Ticket unlocked by {interaction.user.mention}. {owner.mention} can reply again.")
    except Exception:
        pass

    await channel_legacy._touch_ticket_channel(ch)
    await reply_once(interaction, {"content": f"✅ Unlocked {ch.mention}.", "ephemeral": True})


@ticket_group.command(
    name="owner",
    description="Show the owner of a ticket.",
)
@app_commands.describe(channel="Ticket channel to inspect. Leave empty to use the current channel.")
async def ticket_owner(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    owner = await channel_legacy._ticket_owner(ch, row)
    if owner is None:
        return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

    row = row or {}
    embed = discord.Embed(
        title="🎫 Ticket Owner",
        color=discord.Color.blurple(),
        timestamp=channel_legacy.now_utc(),
    )
    embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
    embed.add_field(name="Owner", value=channel_legacy._ticket_owner_value(owner, ch.guild, row), inline=False)
    embed.add_field(name="Status", value=f"`{channel_legacy._safe_str(row.get('status'), 'unknown')}`", inline=True)
    embed.add_field(name="Category", value=f"`{channel_legacy._safe_str(row.get('category'), 'unknown')}`", inline=True)
    embed.add_field(name="Location", value=channel_legacy._ticket_location_label(ch), inline=False)

    ticket_num = channel_legacy._ticket_number(row, ch)
    if ticket_num > 0:
        embed.add_field(name="Ticket Number", value=f"`{ticket_num}`", inline=True)

    matched = channel_legacy._safe_str(row.get("matched_category_name") or row.get("matched_category_slug"))
    if matched:
        embed.add_field(name="Matched Category", value=matched, inline=True)

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_group.command(
    name="access",
    description="Show explicit access overrides on a ticket.",
)
@app_commands.describe(channel="Ticket channel to inspect. Leave empty to use the current channel.")
async def ticket_access(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await channel_legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    row = row or {}
    owner_id = channel_legacy._safe_int(row.get("owner_id") or row.get("user_id"), 0)

    member_lines = []
    role_lines = []

    try:
        for target, overwrite in ch.overwrites.items():
            if isinstance(target, discord.Member):
                bits = []
                if overwrite.view_channel is not None:
                    bits.append(f"view={overwrite.view_channel}")
                if overwrite.send_messages is not None:
                    bits.append(f"send={overwrite.send_messages}")
                if overwrite.attach_files is not None:
                    bits.append(f"files={overwrite.attach_files}")
                if overwrite.embed_links is not None:
                    bits.append(f"embeds={overwrite.embed_links}")

                prefix = "👑 " if int(target.id) == owner_id else "• "
                member_lines.append(f"{prefix}{target.mention} (`{target.id}`) — {', '.join(bits) if bits else 'custom overwrite'}")

            elif isinstance(target, discord.Role):
                bits = []
                if overwrite.view_channel is not None:
                    bits.append(f"view={overwrite.view_channel}")
                if overwrite.send_messages is not None:
                    bits.append(f"send={overwrite.send_messages}")
                if overwrite.attach_files is not None:
                    bits.append(f"files={overwrite.attach_files}")
                if overwrite.embed_links is not None:
                    bits.append(f"embeds={overwrite.embed_links}")

                role_lines.append(f"• @{target.name} (`{target.id}`) — {', '.join(bits) if bits else 'custom overwrite'}")
    except Exception:
        pass

    embed = discord.Embed(
        title="🔐 Ticket Access",
        description=f"{ch.mention}\n{channel_legacy._ticket_location_label(ch)}",
        color=discord.Color.blurple(),
        timestamp=channel_legacy.now_utc(),
    )

    embed.add_field(
        name="Members",
        value=channel_legacy._truncate("\n".join(member_lines), 1024) if member_lines else "No explicit member overwrites found.",
        inline=False,
    )
    embed.add_field(
        name="Roles",
        value=channel_legacy._truncate("\n".join(role_lines), 1024) if role_lines else "No explicit role overwrites found.",
        inline=False,
    )

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


# ============================================================
# Registration
# ============================================================

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
