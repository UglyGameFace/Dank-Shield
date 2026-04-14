from __future__ import annotations

from typing import Any, Dict, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import _staff_check, reply_once

try:
    from ..tickets_new.guardrails import (
        get_ticket_creation_settings,
        get_ticket_blacklist_row,
        upsert_ticket_creation_settings,
        upsert_ticket_blacklist,
        delete_ticket_blacklist,
    )
except Exception:
    get_ticket_creation_settings = None  # type: ignore
    get_ticket_blacklist_row = None  # type: ignore
    upsert_ticket_creation_settings = None  # type: ignore
    upsert_ticket_blacklist = None  # type: ignore
    delete_ticket_blacklist = None  # type: ignore


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


def _settings_embed(title: str, settings: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Cooldown Seconds", value=f"`{_safe_int(settings.get('cooldown_seconds'), 0)}`", inline=True)
    embed.add_field(name="Max Tickets / Window", value=f"`{_safe_int(settings.get('max_tickets_per_window'), 0)}`", inline=True)
    embed.add_field(name="Window Minutes", value=f"`{_safe_int(settings.get('window_minutes'), 0)}`", inline=True)
    return embed


def register_ticket_governance_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_guardrails_status",
        description="Show ticket creation cooldown/limit settings for this server.",
    )
    async def ticket_guardrails_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if get_ticket_creation_settings is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        settings = await get_ticket_creation_settings(guild.id)
        embed = _settings_embed("🛡️ Ticket Guardrails", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_set_cooldown",
        description="Set a global cooldown for ticket creation.",
    )
    @app_commands.describe(seconds="Cooldown in seconds. Set 0 to disable.")
    async def ticket_set_cooldown(interaction: discord.Interaction, seconds: int):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_creation_settings is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        value = max(0, int(seconds))
        ok = await upsert_ticket_creation_settings(guild.id, {"cooldown_seconds": value})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating ticket cooldown.", "ephemeral": True})

        settings = await get_ticket_creation_settings(guild.id) if get_ticket_creation_settings else {"cooldown_seconds": value}
        embed = _settings_embed("✅ Ticket Cooldown Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_set_limit",
        description="Set a rolling ticket creation limit.",
    )
    @app_commands.describe(
        max_tickets="How many tickets a user can create in the window. Set 0 to disable.",
        window_minutes="Window size in minutes. Set 0 to disable.",
    )
    async def ticket_set_limit(
        interaction: discord.Interaction,
        max_tickets: int,
        window_minutes: int,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_creation_settings is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        mt = max(0, int(max_tickets))
        wm = max(0, int(window_minutes))
        ok = await upsert_ticket_creation_settings(guild.id, {"max_tickets_per_window": mt, "window_minutes": wm})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating ticket limit.", "ephemeral": True})

        settings = await get_ticket_creation_settings(guild.id) if get_ticket_creation_settings else {"max_tickets_per_window": mt, "window_minutes": wm}
        embed = _settings_embed("✅ Ticket Limit Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_blacklist_add",
        description="Block a user from creating tickets.",
    )
    @app_commands.describe(
        member="Member to block from ticket creation",
        reason="Reason for the block",
    )
    async def ticket_blacklist_add(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_blacklist is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        ok = await upsert_ticket_blacklist(
            guild.id,
            member.id,
            {
                "is_blocked": True,
                "reason": _safe_str(reason, "Blocked from ticket creation"),
                "blocked_by": str(interaction.user.id),
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed adding ticket blacklist entry.", "ephemeral": True})

        embed = discord.Embed(title="⛔ Ticket Blacklist Added", color=discord.Color.red(), timestamp=now_utc())
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)
        embed.add_field(name="Reason", value=_safe_str(reason, "Blocked from ticket creation"), inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_blacklist_remove",
        description="Remove a user from the ticket blacklist.",
    )
    @app_commands.describe(member="Member to unblock for ticket creation")
    async def ticket_blacklist_remove(interaction: discord.Interaction, member: discord.Member):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if delete_ticket_blacklist is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        ok = await delete_ticket_blacklist(guild.id, member.id)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed removing ticket blacklist entry.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Removed {member.mention} from the ticket blacklist.", "ephemeral": True})

    @tree.command(
        name="ticket_blacklist_check",
        description="Check whether a user is blocked from creating tickets.",
    )
    @app_commands.describe(member="Member to inspect")
    async def ticket_blacklist_check(interaction: discord.Interaction, member: discord.Member):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if get_ticket_blacklist_row is None:
            return await reply_once(interaction, {"content": "❌ Guardrails service is unavailable.", "ephemeral": True})

        row = await get_ticket_blacklist_row(guild.id, member.id)
        embed = discord.Embed(title="🔎 Ticket Blacklist Check", color=discord.Color.blurple(), timestamp=now_utc())
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)
        if not row:
            embed.add_field(name="Status", value="Not blacklisted", inline=False)
        else:
            embed.add_field(name="Status", value="Blocked" if bool(row.get("is_blocked", True)) else "Not blocked", inline=True)
            embed.add_field(name="Reason", value=_safe_str(row.get("reason"), "—"), inline=False)
            embed.add_field(name="Blocked By", value=f"`{_safe_str(row.get('blocked_by'), 'unknown')}`", inline=True)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
