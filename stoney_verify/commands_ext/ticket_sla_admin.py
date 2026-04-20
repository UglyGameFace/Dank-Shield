from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from ..tickets import is_verification_ticket_channel
from .common import _staff_check, reply_once

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
        safe_optional_update_by_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

    async def safe_optional_update_by_channel_id(channel_id: int | str, patch: Dict[str, Any]) -> bool:  # type: ignore
        return False


_MAX_SLA_MINUTES = 10080  # 7 days
_MAX_DUE_SOON_MINUTES = 10080


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


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status"), "unknown").lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
    except Exception:
        pass
    return "unknown"


def _ticket_priority(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("priority"), "medium").lower()
        if raw in {"low", "medium", "high", "urgent"}:
            return raw
    except Exception:
        pass
    return "medium"


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
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        if isinstance(row, dict):
            return dict(row)
    except Exception:
        pass

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


async def _update_ticket_sla(channel_id: int, patch: Dict[str, Any]) -> bool:
    try:
        return await safe_optional_update_by_channel_id(channel_id, patch)
    except Exception:
        return False


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
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _ticket_status(row) not in {"open", "claimed"}:
                continue
            if not _safe_str(row.get("sla_deadline")):
                continue
            out.append(dict(row))
        return out
    except Exception:
        return []


async def _fetch_due_soon(guild_id: int, horizon_minutes: int, limit: int = 15) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_due_soon_sync, guild_id, horizon_minutes, limit)


def _ticket_line(guild: discord.Guild, row: Dict[str, Any]) -> str:
    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    ticket_number = _safe_str(row.get("ticket_number"))
    priority = _ticket_priority(row)
    status = _ticket_status(row)
    assignee = _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0)
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
        status = _ticket_status(row)
        priority = _ticket_priority(row)

        embed = discord.Embed(
            title="⏱️ Ticket SLA Status",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Priority", value=f"`{priority}`", inline=True)
        embed.add_field(name="SLA Deadline", value=_discord_ts(row.get("sla_deadline")), inline=True)
        embed.add_field(name="Created", value=_discord_ts(row.get("created_at")), inline=True)
        embed.add_field(
            name="Assigned",
            value=(f"<@{_safe_int(row.get('assigned_to') or row.get('claimed_by'), 0)}>" if _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0) > 0 else "Unassigned"),
            inline=True,
        )

        if status in {"closed", "deleted"}:
            embed.add_field(
                name="Note",
                value="This ticket is not active. SLA deadlines are informational only until the ticket is reopened.",
                inline=False,
            )

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

        if int(minutes) > _MAX_SLA_MINUTES:
            return await reply_once(
                interaction,
                {"content": f"❌ Minutes cannot exceed `{_MAX_SLA_MINUTES}` (7 days).", "ephemeral": True},
            )

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot set an SLA deadline on a deleted ticket.", "ephemeral": True},
            )

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

        msg = f"✅ Set SLA deadline for {ch.mention} to {_discord_ts(deadline)}."
        if status == "closed":
            msg += "\n⚠️ This ticket is currently closed. The deadline is stored, but it will not matter until the ticket is reopened."
        await reply_once(interaction, {"content": msg, "ephemeral": True})

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

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot clear SLA on a deleted ticket.", "ephemeral": True},
            )

        if not _safe_str((row or {}).get("sla_deadline")):
            return await reply_once(
                interaction,
                {"content": f"ℹ️ {ch.mention} does not currently have an SLA deadline.", "ephemeral": True},
            )

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

        horizon = max(1, min(int(minutes), _MAX_DUE_SOON_MINUTES))
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
        embed.set_footer(text=f"Showing {min(len(rows), 15)} ticket(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
