from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncio
from datetime import timedelta, timezone

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from ..tickets import is_verification_ticket_channel
from .common import _staff_check, reply_once


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


def _truncate(text: Any, limit: int = 200) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _discord_ts(value: Any) -> str:
    try:
        dt = value
        if isinstance(value, str):
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt is None:
            return "unknown"
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        return _safe_str(value, "unknown")


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _ticket_row_sync(channel_id: int) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
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


def _update_ticket_sla_sync(channel_id: int, patch: Dict[str, Any]) -> bool:
    sb = get_supabase()
    if not sb:
        return False

    for field in ("channel_id", "discord_thread_id"):
        try:
            sb.table("tickets").update(patch).eq(field, str(int(channel_id))).execute()
            return True
        except Exception:
            continue
    return False


async def _update_ticket_sla(channel_id: int, patch: Dict[str, Any]) -> bool:
    return await _run_blocking(_update_ticket_sla_sync, channel_id, patch)


def _fetch_due_soon_sync(guild_id: int, horizon_minutes: int, limit: int = 15) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return []

    now = now_utc()
    end = now + timedelta(minutes=max(1, int(horizon_minutes)))

    try:
        res = (
            sb.table("tickets")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .in_("status", ["open", "claimed"])
            .gte("sla_deadline", now.isoformat())
            .lte("sla_deadline", end.isoformat())
            .order("sla_deadline", desc=False)
            .limit(int(limit))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return [dict(x) for x in rows if isinstance(x, dict)]
    except Exception:
        return []


async def _fetch_due_soon(guild_id: int, horizon_minutes: int, limit: int = 15) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_due_soon_sync, guild_id, horizon_minutes, limit)


def _ticket_line(guild: discord.Guild, row: Dict[str, Any]) -> str:
    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    ticket_number = _safe_str(row.get("ticket_number"))
    priority = _safe_str(row.get("priority"), "medium")
    status = _safe_str(row.get("status"), "unknown")
    assignee = _safe_int(row.get("assigned_to"), 0)
    channel_ref = f"<#{channel_id}>" if channel_id > 0 else f"`{_safe_str(row.get('channel_name') or row.get('title') or 'ticket')}`"
    assignee_ref = f"<@{assignee}>" if assignee > 0 else "Unassigned"
    num = f"#{ticket_number} " if ticket_number else ""
    return f"• {num}{channel_ref} • assignee={assignee_ref} • `{status}` • `{priority}` • due={_discord_ts(row.get('sla_deadline'))}"


def register_ticket_sla_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_sla_status",
        description="Show the SLA deadline for the current ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_sla_status(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        row = row or {}
        embed = discord.Embed(
            title="⏱️ Ticket SLA Status",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(name="Status", value=f"`{_safe_str(row.get('status'), 'unknown')}`", inline=True)
        embed.add_field(name="Priority", value=f"`{_safe_str(row.get('priority'), 'medium')}`", inline=True)
        embed.add_field(name="SLA Deadline", value=_discord_ts(row.get("sla_deadline")), inline=True)
        embed.add_field(name="Created", value=_discord_ts(row.get("created_at")), inline=True)
        embed.add_field(name="Assigned", value=(f"<@{_safe_int(row.get('assigned_to'), 0)}>" if _safe_int(row.get("assigned_to"), 0) > 0 else "Unassigned"), inline=True)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_sla_set",
        description="Set the SLA deadline for a ticket in minutes from now.",
    )
    @app_commands.describe(
        minutes="Minutes from now until the ticket is due",
        channel="Ticket channel to update (leave empty to use current channel)",
    )
    async def ticket_sla_set(
        interaction: discord.Interaction,
        minutes: int,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        if int(minutes) <= 0:
            return await reply_once(interaction, {"content": "❌ Minutes must be greater than 0.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        deadline = now_utc() + timedelta(minutes=int(minutes))
        ok = await _update_ticket_sla(
            ch.id,
            {
                "sla_deadline": deadline.isoformat(),
                "updated_at": now_utc().isoformat(),
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to update SLA deadline.", "ephemeral": True})

        try:
            await ch.send(f"⏱️ SLA deadline set to {_discord_ts(deadline)} by {interaction.user.mention}.")
        except Exception:
            pass

        await reply_once(
            interaction,
            {"content": f"✅ Set SLA deadline for {ch.mention} to {_discord_ts(deadline)}.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_sla_clear",
        description="Clear the SLA deadline for a ticket.",
    )
    @app_commands.describe(channel="Ticket channel to update (leave empty to use current channel)")
    async def ticket_sla_clear(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        ok = await _update_ticket_sla(
            ch.id,
            {
                "sla_deadline": None,
                "updated_at": now_utc().isoformat(),
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to clear SLA deadline.", "ephemeral": True})

        try:
            await ch.send(f"🧹 SLA deadline cleared by {interaction.user.mention}.")
        except Exception:
            pass

        await reply_once(interaction, {"content": f"✅ Cleared SLA deadline for {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="tickets_due_soon",
        description="List tickets whose SLA deadline is approaching.",
    )
    @app_commands.describe(minutes="Show tickets due within this many minutes")
    async def tickets_due_soon(
        interaction: discord.Interaction,
        minutes: int = 60,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        horizon = max(1, min(int(minutes), 10080))
        rows = await _fetch_due_soon(guild.id, horizon, limit=15)

        embed = discord.Embed(
            title="🚨 Tickets Due Soon",
            color=discord.Color.orange(),
            timestamp=now_utc(),
            description=f"Showing tickets due within the next `{horizon}` minute(s).",
        )

        if not rows:
            embed.add_field(name="Queue", value="No tickets due soon.", inline=False)
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        embed.add_field(
            name="Upcoming SLA Deadlines",
            value="\n".join([_ticket_line(guild, row) for row in rows[:15]])[:1024],
            inline=False,
        )
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
