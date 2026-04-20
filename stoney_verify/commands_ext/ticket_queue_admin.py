from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import _staff_check, reply_once

try:
    from ..tickets_new.repository import (
        get_ticket_by_number,
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
        list_tickets_for_owner,
        list_ticket_activity_events,
    )
except Exception:
    get_ticket_by_number = None  # type: ignore
    repo_get_ticket_by_any_channel_id = None  # type: ignore
    list_tickets_for_owner = None  # type: ignore
    list_ticket_activity_events = None  # type: ignore


# ============================================================
# ticket_queue_admin.py
# ------------------------------------------------------------
# Hardening goals:
# - make queue/history surfaces reflect real repository state
# - avoid raw channel-only lookups drifting from thread-aware rows
# - normalize recent-closed / overdue reads
# - prevent deleted tickets from polluting active/admin views
# - make owner history / activity views consistent with schema lock
# ============================================================


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


def _truncate(text: Any, limit: int = 250) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        raw = _safe_str(value)
        if not raw:
            return None
        raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _discord_ts(value: Any) -> str:
    dt = _parse_iso(value)
    if not dt:
        return "unknown"
    try:
        return f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        return _safe_str(value, "unknown")


def _normalize_ticket_status(value: Any) -> str:
    raw = _safe_str(value, "unknown").lower()
    if raw in {"open", "claimed", "closed", "deleted"}:
        return raw
    if raw in {"reopened", "active"}:
        return "open"
    return "unknown"


def _normalize_ticket_priority(value: Any) -> str:
    raw = _safe_str(value, "medium").lower()
    if raw in {"low", "medium", "high", "urgent"}:
        return raw
    return "medium"


def _normalize_ticket_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    out["status"] = _normalize_ticket_status(out.get("status"))
    out["priority"] = _normalize_ticket_priority(out.get("priority"))

    user_id = _safe_str(out.get("user_id"))
    out["owner_id"] = _safe_str(out.get("owner_id") or user_id)
    out["requester_id"] = _safe_str(out.get("requester_id") or user_id)

    if out["status"] == "open":
        if _safe_int(out.get("assigned_to") or out.get("claimed_by"), 0) > 0:
            out["status"] = "claimed"

    return out


def _member_ref(guild: discord.Guild, user_id: Any, fallback: str = "Unknown") -> str:
    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return fallback
    member = guild.get_member(uid)
    if member:
        return f"{member.mention} (`{uid}`)"
    return f"`{uid}`"


def _ticket_line(guild: discord.Guild, row: Dict[str, Any]) -> str:
    row = _normalize_ticket_row(row)

    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    owner_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)
    assigned_to = _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0)
    priority = _safe_str(row.get("priority"), "medium")
    status = _safe_str(row.get("status"), "unknown")
    ticket_number = _safe_str(row.get("ticket_number"))
    channel_name = _safe_str(row.get("channel_name") or row.get("title") or "ticket")

    channel_ref = f"<#{channel_id}>" if channel_id > 0 else f"`{channel_name}`"
    num = f"#{ticket_number} " if ticket_number else ""

    return (
        f"• {num}{channel_ref} • owner={_member_ref(guild, owner_id)} "
        f"• assignee={_member_ref(guild, assigned_to, 'Unassigned')} "
        f"• `{status}` • `{priority}`"
    )


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    if repo_get_ticket_by_any_channel_id is None:
        return None

    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        if isinstance(row, dict):
            return _normalize_ticket_row(row)
    except Exception:
        pass
    return None


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
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
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


def _fetch_recent_closed_sync(guild_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return []

    try:
        res = (
            sb.table("tickets")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("status", "closed")
            .order("closed_at", desc=True)
            .limit(int(limit))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            norm = _normalize_ticket_row(row)
            if norm.get("status") != "closed":
                continue
            out.append(norm)
        return out
    except Exception:
        return []


def _fetch_overdue_sync(guild_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return []

    now_iso = now_utc().isoformat()

    try:
        res = (
            sb.table("tickets")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .in_("status", ["open", "claimed"])
            .lt("sla_deadline", now_iso)
            .order("sla_deadline", desc=False)
            .limit(int(limit))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            norm = _normalize_ticket_row(row)
            if norm.get("status") not in {"open", "claimed"}:
                continue
            if not _safe_str(norm.get("sla_deadline")):
                continue
            out.append(norm)
        return out
    except Exception:
        return []


def _history_embed(
    *,
    title: str,
    description: Optional[str],
    color: discord.Color,
    lines: List[str],
    footer: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now_utc(),
    )
    if lines:
        embed.add_field(name="Results", value="\n".join(lines)[:1024], inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def register_ticket_queue_admin_commands(bot, tree) -> None:
    @tree.command(
        name="tickets_recent_closed",
        description="List the most recently closed tickets.",
    )
    async def tickets_recent_closed(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _run_blocking(_fetch_recent_closed_sync, guild.id, 15)

        embed = discord.Embed(
            title="🗃️ Recently Closed Tickets",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.description = "No recently closed tickets found."
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = []
        for row in rows[:15]:
            base = _ticket_line(guild, row)
            closed_at = _discord_ts(row.get("closed_at"))
            transcript = _safe_str(row.get("transcript_url"))
            line = f"{base} • closed={closed_at}"
            if transcript:
                line += " • has transcript"
            lines.append(line)

        embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text=f"Showing {min(len(rows), 15)} closed ticket(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="tickets_overdue",
        description="List tickets whose SLA deadline has passed.",
    )
    async def tickets_overdue(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _run_blocking(_fetch_overdue_sync, guild.id, 15)

        embed = discord.Embed(
            title="⏰ Overdue Tickets",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.description = "No overdue tickets found."
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = []
        for row in rows[:15]:
            base = _ticket_line(guild, row)
            deadline = _discord_ts(row.get("sla_deadline"))
            last_activity = _discord_ts(row.get("last_activity_at"))
            lines.append(f"{base} • sla={deadline} • last_activity={last_activity}")

        embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text=f"Showing {min(len(rows), 15)} overdue ticket(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_find_number",
        description="Find a ticket by its ticket number.",
    )
    @app_commands.describe(ticket_number="The ticket number to look up")
    async def ticket_find_number(interaction: discord.Interaction, ticket_number: int):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if get_ticket_by_number is None:
            return await reply_once(interaction, {"content": "❌ Ticket lookup service is unavailable.", "ephemeral": True})

        row = await get_ticket_by_number(guild_id=guild.id, ticket_number=int(ticket_number))
        if not row:
            return await reply_once(interaction, {"content": f"❌ No ticket found for `#{ticket_number}`.", "ephemeral": True})

        row = _normalize_ticket_row(row)

        embed = discord.Embed(
            title=f"🎫 Ticket #{ticket_number}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.description = _ticket_line(guild, row)
        embed.add_field(name="Created", value=_discord_ts(row.get("created_at")), inline=True)
        embed.add_field(name="Closed", value=_discord_ts(row.get("closed_at")), inline=True)
        embed.add_field(name="SLA", value=_discord_ts(row.get("sla_deadline")), inline=True)
        embed.add_field(name="Transcript", value=_safe_str(row.get("transcript_url"), "—"), inline=False)
        embed.add_field(name="Decision", value=_safe_str(row.get("decision"), "—"), inline=True)
        embed.add_field(name="Category", value=f"`{_safe_str(row.get('category'), 'unknown')}`", inline=True)
        embed.add_field(name="Priority", value=f"`{_safe_str(row.get('priority'), 'medium')}`", inline=True)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="tickets_for_user",
        description="List recent tickets for a specific user.",
    )
    @app_commands.describe(member="User whose ticket history you want to view")
    async def tickets_for_user(interaction: discord.Interaction, member: discord.Member):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        if list_tickets_for_owner is None:
            return await reply_once(interaction, {"content": "❌ Ticket history service is unavailable.", "ephemeral": True})

        rows = await list_tickets_for_owner(guild_id=guild.id, owner_id=member.id, limit=15)
        rows = [_normalize_ticket_row(row) for row in rows if isinstance(row, dict)]

        embed = discord.Embed(
            title="👤 User Ticket History",
            description=f"{member.mention}\n`{member.id}`",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.add_field(name="History", value="No tickets found for this user.", inline=False)
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = [_ticket_line(guild, row) for row in rows[:15]]
        embed.add_field(name="Tickets", value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_history",
        description="Show prior tickets for the owner of the current ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_history(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        if list_tickets_for_owner is None:
            return await reply_once(interaction, {"content": "❌ Ticket history service is unavailable.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        owner = await _ticket_owner(ch, row)
        owner_id = int(getattr(owner, "id", 0) or 0)
        if owner_id <= 0:
            return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

        rows = await list_tickets_for_owner(guild_id=ch.guild.id, owner_id=owner_id, limit=15)
        rows = [_normalize_ticket_row(item) for item in rows if isinstance(item, dict)]

        embed = discord.Embed(
            title="🧾 Ticket Owner History",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Owner",
            value=(owner.mention if isinstance(owner, discord.Member) else f"`{owner_id}`"),
            inline=False,
        )

        if not rows:
            embed.add_field(name="History", value="No tickets found for this owner.", inline=False)
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = [_ticket_line(ch.guild, item) for item in rows[:15]]
        embed.add_field(name="Tickets", value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_activity",
        description="Show recent activity-feed events for the current ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_activity(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        if list_ticket_activity_events is None:
            return await reply_once(interaction, {"content": "❌ Ticket activity service is unavailable.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        ticket_id = _safe_str((row or {}).get("id"))
        events: List[Dict[str, Any]] = []

        try:
            # Prefer ticket_id when available to avoid channel/thread drift.
            if ticket_id:
                events = await list_ticket_activity_events(guild_id=ch.guild.id, ticket_id=ticket_id, limit=10)
            if not events:
                events = await list_ticket_activity_events(guild_id=ch.guild.id, channel_id=ch.id, limit=10)
        except Exception:
            events = []

        embed = discord.Embed(
            title="📜 Ticket Activity",
            description=f"{ch.mention}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not events:
            embed.add_field(name="Activity", value="No recent activity-feed events found for this ticket.", inline=False)
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = []
        for event in events[:10]:
            title = _safe_str(event.get("title") or event.get("event_type") or "event")
            actor = _safe_str(event.get("actor_name") or event.get("actor_user_id") or "unknown")
            created = _discord_ts(event.get("created_at"))
            desc = _truncate(event.get("description") or event.get("reason") or "", 120)
            line = f"• **{title}** — `{actor}` — {created}"
            if desc:
                line += f"\n  {desc}"
            lines.append(line)

        embed.add_field(name="Recent Events", value="\n".join(lines)[:1024], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
