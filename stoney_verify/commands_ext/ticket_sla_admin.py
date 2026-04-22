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


_MAX_SLA_MINUTES = 10_080  # 7 days
_MAX_DUE_SOON_MINUTES = 10_080


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


def _normalize_ticket_status(value: Any) -> str:
    raw = _safe_str(value, "unknown").lower()
    if raw in {"open", "claimed", "closed", "deleted"}:
        return raw
    if raw in {"active", "reopened"}:
        return "open"
    return "unknown"


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    return _normalize_ticket_status((row or {}).get("status"))


def _ticket_priority(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("priority"), "medium").lower()
        if raw in {"low", "medium", "high", "urgent"}:
            return raw
    except Exception:
        pass
    return "medium"


def _priority_rank(priority: str) -> int:
    return {
        "urgent": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }.get(_safe_str(priority, "medium").lower(), 2)


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        try:
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_active_category_id() -> int:
    try:
        return int(globals().get("TICKET_CATEGORY_ID", 0) or 0)
    except Exception:
        return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).lower()
    if not text:
        return False

    markers = (
        "archive",
        "archived",
        "ticket archive",
        "tickets archive",
        "archived tickets",
        "closed tickets",
    )
    return any(marker in text for marker in markers)


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
    except Exception:
        pass
    return None


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _resolve_active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    active_id = _ticket_active_category_id()
    if active_id > 0:
        active = _resolve_category_by_id(guild, active_id)
        if active is not None:
            return active
    return None


def _channel_is_in_category(
    channel: discord.TextChannel,
    category: Optional[discord.CategoryChannel],
) -> bool:
    try:
        if category is None:
            return False
        return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
    except Exception:
        return False


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category and _channel_is_in_category(channel, archive_category):
        return True
    try:
        if channel.category and _looks_like_archive_category_name(channel.category.name):
            return True
    except Exception:
        pass
    return False


def _channel_is_in_active_category(channel: discord.TextChannel) -> bool:
    active_category = _resolve_active_ticket_category(channel.guild)
    if active_category and _channel_is_in_category(channel, active_category):
        return True
    return False


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _ticket_effectively_closed(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> bool:
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return True
    if _channel_looks_closed(channel):
        return True
    if _channel_is_in_archive_category(channel):
        return True
    return False


def _location_label(channel: discord.TextChannel) -> str:
    archive_category = _resolve_archive_category(channel.guild)
    active_category = _resolve_active_ticket_category(channel.guild)

    if archive_category and _channel_is_in_category(channel, archive_category):
        return f"Archived in **{archive_category.name}**"
    if active_category and _channel_is_in_category(channel, active_category):
        return f"Active in **{active_category.name}**"
    if channel.category:
        return f"In **{channel.category.name}**"
    return "No category"


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


def _fetch_due_soon_sync(guild_id: int, horizon_minutes: int, limit: int = 25) -> List[Dict[str, Any]]:
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
            if _normalize_ticket_status(row.get("status")) not in {"open", "claimed"}:
                continue
            if not _safe_str(row.get("sla_deadline")):
                continue
            out.append(dict(row))

        out.sort(
            key=lambda row: (
                _priority_rank(_ticket_priority(row)),
                _parse_iso(row.get("sla_deadline")) or datetime.max.replace(tzinfo=timezone.utc),
            )
        )
        return out
    except Exception:
        return []


async def _fetch_due_soon(guild_id: int, horizon_minutes: int, limit: int = 25) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_due_soon_sync, guild_id, horizon_minutes, limit)


def _ticket_line(guild: discord.Guild, row: Dict[str, Any]) -> str:
    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    ticket_number = _safe_str(row.get("ticket_number"))
    priority = _ticket_priority(row)
    status = _ticket_status(row)
    assignee = _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0)
    owner_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)

    channel_ref = f"<#{channel_id}>" if channel_id > 0 else f"`{_safe_str(row.get('channel_name') or row.get('title') or 'ticket')}`"
    assignee_ref = f"<@{assignee}>" if assignee > 0 else "Unassigned"
    owner_ref = f"<@{owner_id}>" if owner_id > 0 else "Unknown"
    num = f"#{ticket_number} " if ticket_number else ""

    return (
        f"• {num}{channel_ref}\n"
        f"  Owner: {owner_ref}\n"
        f"  Assignee: {assignee_ref}\n"
        f"  Status: `{status}` • Priority: `{priority}` • Due: {_discord_ts(row.get('sla_deadline'))}"
    )


def _rows_to_chunks(guild: discord.Guild, rows: List[Dict[str, Any]], chunk_limit: int = 3800) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for row in rows:
        line = _ticket_line(guild, row)
        line_len = len(line) + 2

        if current and (current_len + line_len) > chunk_limit:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks or []


async def _send_paginated_embeds(
    interaction: discord.Interaction,
    *,
    title: str,
    description: Optional[str],
    color: discord.Color,
    chunks: List[str],
    footer_prefix: str,
) -> None:
    if not chunks:
        embed = discord.Embed(
            title=title,
            description=description or "No results found.",
            color=color,
            timestamp=now_utc(),
        )
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    embeds: List[discord.Embed] = []
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=title if index == 1 else f"{title} (cont.)",
            description=description if index == 1 and description else None,
            color=color,
            timestamp=now_utc(),
        )
        embed.add_field(name="Tickets", value=chunk[:1024], inline=False) if len(chunk) <= 1024 else None
        if len(chunk) > 1024:
            embed.description = ((embed.description + "\n\n") if embed.description else "") + chunk[:4000]
        embed.set_footer(text=f"{footer_prefix} • Page {index}/{total}")
        embeds.append(embed)

    first = embeds[0]
    rest = embeds[1:]

    await reply_once(interaction, {"embed": first, "ephemeral": True})
    for embed in rest:
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            break


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
        due_at = row.get("sla_deadline")
        effectively_closed = _ticket_effectively_closed(channel=ch, row=row)

        embed = discord.Embed(
            title="⏱️ Ticket SLA Status",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Priority", value=f"`{priority}`", inline=True)
        embed.add_field(name="Location", value=_location_label(ch), inline=False)
        embed.add_field(name="SLA Deadline", value=_discord_ts(due_at), inline=True)
        embed.add_field(name="Created", value=_discord_ts(row.get("created_at")), inline=True)
        embed.add_field(
            name="Assigned",
            value=(
                f"<@{_safe_int(row.get('assigned_to') or row.get('claimed_by'), 0)}>"
                if _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0) > 0
                else "Unassigned"
            ),
            inline=True,
        )

        if not _safe_str(due_at):
            embed.add_field(
                name="Meaning",
                value="No SLA deadline is currently stored for this ticket.",
                inline=False,
            )
        elif effectively_closed:
            embed.add_field(
                name="Note",
                value=(
                    "This ticket is currently closed/archived. "
                    "The stored SLA deadline is informational only until the ticket is reopened."
                ),
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

        msg_lines = [f"✅ Set SLA deadline for {ch.mention} to {_discord_ts(deadline)}."]
        if _ticket_effectively_closed(channel=ch, row=row):
            msg_lines.append(
                "⚠️ This ticket is currently closed/archived. The deadline is stored, but it will only matter again after reopen."
            )
        await reply_once(interaction, {"content": "\n".join(msg_lines), "ephemeral": True})

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
        rows = await _fetch_due_soon(guild.id, horizon, limit=25)

        if not rows:
            embed = discord.Embed(
                title="🚨 Tickets Due Soon",
                description=f"No tickets are due within the next `{horizon}` minute(s).",
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        chunks = _rows_to_chunks(guild, rows)
        await _send_paginated_embeds(
            interaction,
            title="🚨 Tickets Due Soon",
            description=f"Showing tickets due within the next `{horizon}` minute(s), sorted by urgency and due time.",
            color=discord.Color.orange(),
            chunks=chunks,
            footer_prefix=f"Showing {len(rows)} ticket(s)",
        )
