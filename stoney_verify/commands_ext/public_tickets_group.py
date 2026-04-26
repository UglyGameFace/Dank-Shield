from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once

# Reuse the hardened queue/history helpers without registering the legacy
# top-level tickets_* commands. Importing ticket_queue_admin is safe; commands
# are only registered when register_ticket_queue_admin_commands(...) is called.
from . import ticket_queue_admin as legacy


tickets_group = app_commands.Group(
    name="tickets",
    description="Ticket queues, history, and lookup tools.",
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if not _staff_check(interaction):
        await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        return False
    return True


async def _guild_only(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        return None
    return guild


@tickets_group.command(
    name="open",
    description="List active tickets in queue order.",
)
async def tickets_open(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    if legacy.list_open_ticket_queue is None:
        return await reply_once(interaction, {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True})

    rows = await legacy._queue_rows_for_guild(guild.id)
    rows = [row for row in rows if legacy._normalize_ticket_status(row.get("status")) in {"open", "claimed"}]

    chunks = legacy._rows_to_chunks(guild, rows[:40], queue_style=True)
    await legacy._send_paginated_embeds(
        interaction,
        title="🎫 Active Ticket Queue",
        color=discord.Color.blurple(),
        chunks=chunks,
        footer_prefix=f"Showing {min(len(rows), 40)} active ticket(s)",
    )


@tickets_group.command(
    name="unassigned",
    description="List unclaimed tickets waiting for staff.",
)
async def tickets_unassigned(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    if legacy.list_unclaimed_tickets is None:
        return await reply_once(interaction, {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True})

    rows = await legacy._unclaimed_rows_for_guild(guild.id)
    rows = [row for row in rows if bool(row.get("is_unclaimed"))]

    chunks = legacy._rows_to_chunks(guild, rows[:40], queue_style=True)
    await legacy._send_paginated_embeds(
        interaction,
        title="📭 Unassigned Tickets",
        color=discord.Color.orange(),
        chunks=chunks,
        footer_prefix=f"Showing {min(len(rows), 40)} unassigned ticket(s)",
    )


@tickets_group.command(
    name="mine",
    description="List tickets currently claimed by you.",
)
async def tickets_mine(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return

    if legacy.list_tickets_claimed_by_staff is None:
        return await reply_once(interaction, {"content": "❌ Ticket queue service is unavailable.", "ephemeral": True})

    rows = await legacy._claimed_rows_for_staff(guild.id, member.id)
    rows = [row for row in rows if int(row.get("claimed_by_id") or 0) == int(member.id)]

    chunks = legacy._rows_to_chunks(guild, rows[:40], queue_style=True)
    await legacy._send_paginated_embeds(
        interaction,
        title="🧑‍💼 Tickets Claimed By You",
        color=discord.Color.green(),
        chunks=chunks,
        footer_prefix=f"Showing {min(len(rows), 40)} claimed ticket(s)",
    )


@tickets_group.command(
    name="recent-closed",
    description="List the most recently closed tickets.",
)
async def tickets_recent_closed(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._run_blocking(legacy._fetch_recent_closed_sync, guild.id, 15)

    embed = discord.Embed(
        title="🗃️ Recently Closed Tickets",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )

    if not rows:
        embed.description = "No recently closed tickets found."
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    lines = []
    for row in rows[:15]:
        base = legacy._ticket_line(guild, row)
        closed_at = legacy._discord_ts(row.get("closed_at"))
        transcript = legacy._safe_str(row.get("transcript_url"))
        line = f"{base} • closed={closed_at}"
        if transcript:
            line += " • has transcript"
        lines.append(line)

    embed.description = "\n".join(lines)[:4000]
    embed.set_footer(text=f"Showing {min(len(rows), 15)} closed ticket(s)")
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@tickets_group.command(
    name="overdue",
    description="List tickets whose SLA deadline has passed.",
)
async def tickets_overdue(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._run_blocking(legacy._fetch_overdue_sync, guild.id, 15)

    embed = discord.Embed(
        title="⏰ Overdue Tickets",
        color=discord.Color.orange(),
        timestamp=legacy.now_utc(),
    )

    if not rows:
        embed.description = "No overdue tickets found."
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    lines = []
    for row in rows[:15]:
        base = legacy._ticket_line(guild, row)
        deadline = legacy._discord_ts(row.get("sla_deadline"))
        last_activity = legacy._discord_ts(row.get("last_activity_at"))
        lines.append(f"{base} • sla={deadline} • last_activity={last_activity}")

    embed.description = "\n".join(lines)[:4000]
    embed.set_footer(text=f"Showing {min(len(rows), 15)} overdue ticket(s)")
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@tickets_group.command(
    name="find",
    description="Find a ticket by its ticket number.",
)
@app_commands.describe(ticket_number="The ticket number to look up")
async def tickets_find(interaction: discord.Interaction, ticket_number: int):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    if legacy.get_ticket_by_number is None:
        return await reply_once(interaction, {"content": "❌ Ticket lookup service is unavailable.", "ephemeral": True})

    row = await legacy.get_ticket_by_number(guild_id=guild.id, ticket_number=int(ticket_number))
    if not row:
        return await reply_once(interaction, {"content": f"❌ No ticket found for `#{ticket_number}`.", "ephemeral": True})

    row = legacy._normalize_ticket_row(row)

    embed = discord.Embed(
        title=f"🎫 Ticket #{ticket_number}",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )
    embed.description = legacy._ticket_line(guild, row)
    embed.add_field(name="Created", value=legacy._discord_ts(row.get("created_at")), inline=True)
    embed.add_field(name="Closed", value=legacy._discord_ts(row.get("closed_at")), inline=True)
    embed.add_field(name="SLA", value=legacy._discord_ts(row.get("sla_deadline")), inline=True)
    embed.add_field(name="Transcript", value=legacy._safe_str(row.get("transcript_url"), "—"), inline=False)
    embed.add_field(name="Decision", value=legacy._safe_str(row.get("decision"), "—"), inline=True)
    embed.add_field(name="Category", value=f"`{legacy._safe_str(row.get('category'), 'unknown')}`", inline=True)
    embed.add_field(name="Priority", value=f"`{legacy._safe_str(row.get('priority'), 'medium')}`", inline=True)
    embed.add_field(
        name="Matched Category",
        value=legacy._safe_str(row.get("matched_category_name") or row.get("matched_category_slug"), "—"),
        inline=False,
    )
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@tickets_group.command(
    name="for-user",
    description="List recent tickets for a specific user.",
)
@app_commands.describe(member="User whose ticket history you want to view")
async def tickets_for_user(interaction: discord.Interaction, member: discord.Member):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    if legacy.list_tickets_for_owner is None:
        return await reply_once(interaction, {"content": "❌ Ticket history service is unavailable.", "ephemeral": True})

    rows = await legacy.list_tickets_for_owner(guild_id=guild.id, owner_id=member.id, limit=15)
    rows = [legacy._normalize_ticket_row(row) for row in rows if isinstance(row, dict)]

    embed = discord.Embed(
        title="👤 User Ticket History",
        description=f"{member.mention}\n`{member.id}`",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )

    if not rows:
        embed.add_field(name="History", value="No tickets found for this user.", inline=False)
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    lines = [legacy._ticket_line(guild, row) for row in rows[:15]]
    embed.add_field(name="Tickets", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@tickets_group.command(
    name="history",
    description="Show prior tickets for the owner of the current ticket.",
)
@app_commands.describe(channel="Ticket channel to inspect. Leave empty to use the current channel.")
async def tickets_history(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    if legacy.list_tickets_for_owner is None:
        return await reply_once(interaction, {"content": "❌ Ticket history service is unavailable.", "ephemeral": True})

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    owner = await legacy._ticket_owner(ch, row)
    owner_id = int(getattr(owner, "id", 0) or 0)
    if owner_id <= 0:
        return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

    rows = await legacy.list_tickets_for_owner(guild_id=ch.guild.id, owner_id=owner_id, limit=15)
    rows = [legacy._normalize_ticket_row(item) for item in rows if isinstance(item, dict)]

    embed = discord.Embed(
        title="🧾 Ticket Owner History",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )
    embed.add_field(
        name="Owner",
        value=(owner.mention if isinstance(owner, discord.Member) else f"`{owner_id}`"),
        inline=False,
    )

    if not rows:
        embed.add_field(name="History", value="No tickets found for this owner.", inline=False)
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    lines = [legacy._ticket_line(ch.guild, item) for item in rows[:15]]
    embed.add_field(name="Tickets", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@tickets_group.command(
    name="activity",
    description="Show recent activity-feed events for the current ticket.",
)
@app_commands.describe(channel="Ticket channel to inspect. Leave empty to use the current channel.")
async def tickets_activity(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    if legacy.list_ticket_activity_events is None:
        return await reply_once(interaction, {"content": "❌ Ticket activity service is unavailable.", "ephemeral": True})

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    ticket_id = legacy._safe_str((row or {}).get("id"))
    events: List[Dict[str, Any]] = []

    try:
        if ticket_id:
            events = await legacy.list_ticket_activity_events(guild_id=ch.guild.id, ticket_id=ticket_id, limit=10)
        if not events:
            events = await legacy.list_ticket_activity_events(guild_id=ch.guild.id, channel_id=ch.id, limit=10)
    except Exception:
        events = []

    embed = discord.Embed(
        title="📜 Ticket Activity",
        description=f"{ch.mention}",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )

    if not events:
        embed.add_field(name="Activity", value="No recent activity-feed events found for this ticket.", inline=False)
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    lines = []
    for event in events[:10]:
        title = legacy._safe_str(event.get("title") or event.get("event_type") or "event")
        actor = legacy._safe_str(event.get("actor_name") or event.get("actor_user_id") or "unknown")
        created = legacy._discord_ts(event.get("created_at"))
        desc = legacy._truncate(event.get("description") or event.get("reason") or "", 140)
        line = f"• **{title}** — `{actor}` — {created}"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)

    embed.add_field(name="Recent Events", value="\n".join(lines)[:1024], inline=False)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


def register_public_tickets_group_commands(bot, tree) -> None:
    _ = bot
    existing = None
    try:
        existing = tree.get_command("tickets", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        try:
            print("ℹ️ public_tickets_group: /tickets already registered; skipping")
        except Exception:
            pass
        return

    tree.add_command(tickets_group)
    try:
        print("✅ public_tickets_group: registered /tickets grouped command")
    except Exception:
        pass


__all__ = ["register_public_tickets_group_commands", "tickets_group"]
