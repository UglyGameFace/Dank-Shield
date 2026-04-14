from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncio
import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from ..tickets import is_verification_ticket_channel

from .common import _staff_check, reply_once, mark_ticket_activity


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


def _truncate(text: Any, limit: int = 300) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


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


def _macros_table():
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_macros")


def _fetch_macros_sync(guild_id: int) -> List[Dict[str, Any]]:
    try:
        res = (
            _macros_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .order("name", desc=False)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return [dict(x) for x in rows if isinstance(x, dict)]
    except Exception:
        return []


async def _fetch_macros(guild_id: int) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_macros_sync, guild_id)


def _fetch_macro_sync(guild_id: int, name: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _macros_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("name", str(name).strip().lower())
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return None
    return None


async def _fetch_macro(guild_id: int, name: str) -> Optional[Dict[str, Any]]:
    return await _run_blocking(_fetch_macro_sync, guild_id, name)


def _upsert_macro_sync(guild_id: int, name: str, content: str) -> bool:
    payload = {
        "guild_id": str(int(guild_id)),
        "name": str(name).strip().lower(),
        "content": str(content).strip(),
        "updated_at": now_utc().isoformat(),
    }
    try:
        _macros_table().upsert(payload, on_conflict="guild_id,name").execute()
        return True
    except Exception:
        return False


async def _upsert_macro(guild_id: int, name: str, content: str) -> bool:
    return await _run_blocking(_upsert_macro_sync, guild_id, name, content)


def _delete_macro_sync(guild_id: int, name: str) -> bool:
    try:
        _macros_table().delete().eq("guild_id", str(int(guild_id))).eq("name", str(name).strip().lower()).execute()
        return True
    except Exception:
        return False


async def _delete_macro(guild_id: int, name: str) -> bool:
    return await _run_blocking(_delete_macro_sync, guild_id, name)


def _render_macro(content: str, interaction: discord.Interaction, channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> str:
    guild = interaction.guild
    user = interaction.user
    owner_id = _safe_int((row or {}).get("owner_id") or (row or {}).get("user_id"), 0)
    assignee_id = _safe_int((row or {}).get("assigned_to"), 0)
    ticket_number = _safe_str((row or {}).get("ticket_number"))
    category = _safe_str((row or {}).get("category"), "support")

    replacements = {
        "{staff_mention}": getattr(user, "mention", ""),
        "{staff_name}": getattr(user, "display_name", getattr(user, "name", "Staff")),
        "{owner_mention}": f"<@{owner_id}>" if owner_id > 0 else "the ticket owner",
        "{owner_id}": str(owner_id) if owner_id > 0 else "unknown",
        "{assignee_mention}": f"<@{assignee_id}>" if assignee_id > 0 else "the assigned staff member",
        "{assignee_id}": str(assignee_id) if assignee_id > 0 else "unknown",
        "{ticket_number}": ticket_number or "unknown",
        "{ticket_channel}": getattr(channel, "mention", f"`{channel.id}`"),
        "{ticket_category}": category or "support",
        "{guild_name}": getattr(guild, "name", "this server") if guild else "this server",
    }

    text = str(content or "")
    for key, value in replacements.items():
        text = text.replace(key, str(value))
    return text.strip()


def register_ticket_macro_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_macro_add",
        description="Create or update a canned reply macro.",
    )
    @app_commands.describe(
        name="Short macro name like welcome or ask-proof",
        content="Macro content. Supports placeholders like {owner_mention}, {staff_mention}, {ticket_number}",
    )
    async def ticket_macro_add(
        interaction: discord.Interaction,
        name: str,
        content: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        macro_name = _safe_str(name).lower().replace(" ", "-")
        macro_content = _safe_str(content)
        if not macro_name:
            return await reply_once(interaction, {"content": "❌ Macro name cannot be empty.", "ephemeral": True})
        if not macro_content:
            return await reply_once(interaction, {"content": "❌ Macro content cannot be empty.", "ephemeral": True})

        ok = await _upsert_macro(guild.id, macro_name, macro_content)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to save macro.", "ephemeral": True})

        embed = discord.Embed(
            title="💬 Ticket Macro Saved",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Name", value=f"`{macro_name}`", inline=True)
        embed.add_field(name="Preview", value=_truncate(macro_content, 900), inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_delete",
        description="Delete a canned reply macro.",
    )
    @app_commands.describe(name="Macro name to delete")
    async def ticket_macro_delete(
        interaction: discord.Interaction,
        name: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        macro_name = _safe_str(name).lower().replace(" ", "-")
        ok = await _delete_macro(guild.id, macro_name)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to delete macro.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Deleted macro `{macro_name}`.", "ephemeral": True})

    @tree.command(
        name="ticket_macro_list",
        description="List all ticket canned reply macros.",
    )
    async def ticket_macro_list(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _fetch_macros(guild.id)
        embed = discord.Embed(
            title="📚 Ticket Macros",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        if not rows:
            embed.description = "No ticket macros saved yet."
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        lines = []
        for row in rows[:20]:
            lines.append(f"• `{_safe_str(row.get('name'), 'unknown')}` — {_truncate(row.get('content'), 90)}")
        embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text=f"Showing {min(len(rows), 20)} macro(s)")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_show",
        description="Show a single ticket macro in full.",
    )
    @app_commands.describe(name="Macro name to inspect")
    async def ticket_macro_show(
        interaction: discord.Interaction,
        name: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        macro_name = _safe_str(name).lower().replace(" ", "-")
        row = await _fetch_macro(guild.id, macro_name)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Macro `{macro_name}` was not found.", "ephemeral": True})

        embed = discord.Embed(
            title="🔎 Ticket Macro",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Name", value=f"`{macro_name}`", inline=False)
        embed.add_field(name="Content", value=_truncate(row.get("content"), 4000), inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_send",
        description="Send a canned reply macro into the current ticket.",
    )
    @app_commands.describe(
        name="Macro name to send",
        channel="Ticket channel to send into (leave empty to use current channel)",
    )
    async def ticket_macro_send(
        interaction: discord.Interaction,
        name: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        macro_name = _safe_str(name).lower().replace(" ", "-")
        macro = await _fetch_macro(guild.id, macro_name)
        if not macro:
            return await reply_once(interaction, {"content": f"❌ Macro `{macro_name}` was not found.", "ephemeral": True})

        content = _render_macro(_safe_str(macro.get("content")), interaction, ch, row)

        try:
            await ch.send(content, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            mark_ticket_activity(ch.id)
        except Exception as e:
            return await reply_once(interaction, {"content": f"❌ Failed sending macro: {e}", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Sent macro `{macro_name}` in {ch.mention}.", "ephemeral": True})
