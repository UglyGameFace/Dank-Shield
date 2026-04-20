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


_MAX_COOLDOWN_SECONDS = 86_400
_MAX_WINDOW_MINUTES = 10_080  # 7 days
_MAX_TICKETS_PER_WINDOW = 100


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


def _settings_status_text(settings: Dict[str, Any]) -> str:
    cooldown = _safe_int(settings.get("cooldown_seconds"), 0)
    max_tickets = _safe_int(settings.get("max_tickets_per_window"), 0)
    window_minutes = _safe_int(settings.get("window_minutes"), 0)

    lines = []

    if cooldown > 0:
        lines.append(f"• Cooldown enabled: `{cooldown}` second(s)")
    else:
        lines.append("• Cooldown disabled")

    if max_tickets > 0 and window_minutes > 0:
        lines.append(f"• Rolling limit enabled: `{max_tickets}` ticket(s) per `{window_minutes}` minute(s)")
    else:
        lines.append("• Rolling limit disabled")

    warnings = _settings_warnings(settings)
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend([f"• {w}" for w in warnings])

    return "\n".join(lines)


def _settings_warnings(settings: Dict[str, Any]) -> list[str]:
    cooldown = _safe_int(settings.get("cooldown_seconds"), 0)
    max_tickets = _safe_int(settings.get("max_tickets_per_window"), 0)
    window_minutes = _safe_int(settings.get("window_minutes"), 0)

    warnings: list[str] = []

    if cooldown < 0:
        warnings.append("Cooldown is below zero.")
    if max_tickets < 0:
        warnings.append("Max tickets per window is below zero.")
    if window_minutes < 0:
        warnings.append("Window minutes is below zero.")

    if (max_tickets == 0) ^ (window_minutes == 0):
        warnings.append("Rolling limit is half-configured. Both max tickets and window minutes should be 0 or both positive.")

    if cooldown > _MAX_COOLDOWN_SECONDS:
        warnings.append(f"Cooldown exceeds recommended maximum of `{_MAX_COOLDOWN_SECONDS}` seconds.")
    if max_tickets > _MAX_TICKETS_PER_WINDOW:
        warnings.append(f"Max tickets per window exceeds recommended maximum of `{_MAX_TICKETS_PER_WINDOW}`.")
    if window_minutes > _MAX_WINDOW_MINUTES:
        warnings.append(f"Window minutes exceeds recommended maximum of `{_MAX_WINDOW_MINUTES}`.")

    return warnings


def _settings_embed(title: str, settings: Dict[str, Any]) -> discord.Embed:
    cooldown = _safe_int(settings.get("cooldown_seconds"), 0)
    max_tickets = _safe_int(settings.get("max_tickets_per_window"), 0)
    window_minutes = _safe_int(settings.get("window_minutes"), 0)

    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Cooldown Seconds", value=f"`{cooldown}`", inline=True)
    embed.add_field(name="Max Tickets / Window", value=f"`{max_tickets}`", inline=True)
    embed.add_field(name="Window Minutes", value=f"`{window_minutes}`", inline=True)
    embed.add_field(name="Guardrail Status", value=_settings_status_text(settings)[:1024], inline=False)
    return embed


def _validate_cooldown(seconds: int) -> Optional[str]:
    if seconds < 0:
        return "Cooldown cannot be negative."
    if seconds > _MAX_COOLDOWN_SECONDS:
        return f"Cooldown cannot exceed `{_MAX_COOLDOWN_SECONDS}` seconds."
    return None


def _validate_limit(max_tickets: int, window_minutes: int) -> Optional[str]:
    if max_tickets < 0 or window_minutes < 0:
        return "Limit values cannot be negative."

    if max_tickets == 0 and window_minutes == 0:
        return None

    if max_tickets == 0 or window_minutes == 0:
        return "To disable rolling limits, set both values to `0`. Otherwise both values must be positive."

    if max_tickets > _MAX_TICKETS_PER_WINDOW:
        return f"Max tickets per window cannot exceed `{_MAX_TICKETS_PER_WINDOW}`."

    if window_minutes > _MAX_WINDOW_MINUTES:
        return f"Window minutes cannot exceed `{_MAX_WINDOW_MINUTES}`."

    return None


def _member_is_staff_like(member: discord.Member) -> bool:
    try:
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_guild:
            return True
        if member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass

    try:
        staff_role_id = int(str(globals().get("STAFF_ROLE_ID") or "0"))
        if staff_role_id > 0:
            return any(int(role.id) == staff_role_id for role in member.roles)
    except Exception:
        pass

    return False


def _blacklist_embed(
    title: str,
    member: discord.Member,
    row: Optional[Dict[str, Any]] = None,
    *,
    fallback_reason: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)

    if not row:
        embed.add_field(name="Status", value="Not blacklisted", inline=False)
        return embed

    is_blocked = bool(row.get("is_blocked", True))
    embed.add_field(name="Status", value="Blocked" if is_blocked else "Not blocked", inline=True)
    embed.add_field(name="Blocked By", value=f"`{_safe_str(row.get('blocked_by'), 'unknown')}`", inline=True)
    embed.add_field(
        name="Reason",
        value=_safe_str(row.get("reason"), fallback_reason or "—")[:1024],
        inline=False,
    )

    created_at = _safe_str(row.get("created_at"))
    updated_at = _safe_str(row.get("updated_at"))
    if created_at or updated_at:
        embed.add_field(
            name="Timestamps",
            value=f"Created: `{created_at or 'unknown'}`\nUpdated: `{updated_at or 'unknown'}`",
            inline=False,
        )

    return embed


async def _load_settings_or_default(guild_id: int) -> Dict[str, Any]:
    if get_ticket_creation_settings is None:
        return {
            "cooldown_seconds": 0,
            "max_tickets_per_window": 0,
            "window_minutes": 0,
        }

    try:
        settings = await get_ticket_creation_settings(guild_id)
        if isinstance(settings, dict):
            return {
                "cooldown_seconds": _safe_int(settings.get("cooldown_seconds"), 0),
                "max_tickets_per_window": _safe_int(settings.get("max_tickets_per_window"), 0),
                "window_minutes": _safe_int(settings.get("window_minutes"), 0),
            }
    except Exception:
        pass

    return {
        "cooldown_seconds": 0,
        "max_tickets_per_window": 0,
        "window_minutes": 0,
    }


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

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("🛡️ Ticket Guardrails", settings)

        embed.add_field(
            name="Behavior",
            value=(
                "Cooldown slows repeat ticket creation.\n"
                "Rolling limits cap how many tickets one user can create within a time window."
            )[:1024],
            inline=False,
        )

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

        value = int(seconds)
        err = _validate_cooldown(value)
        if err:
            return await reply_once(interaction, {"content": f"❌ {err}", "ephemeral": True})

        ok = await upsert_ticket_creation_settings(guild.id, {"cooldown_seconds": value})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating ticket cooldown.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Ticket Cooldown Updated", settings)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_set_limit",
        description="Set a rolling ticket creation limit.",
    )
    @app_commands.describe(
        max_tickets="How many tickets a user can create in the window. Set 0 with window 0 to disable.",
        window_minutes="Window size in minutes. Set 0 with max 0 to disable.",
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

        mt = int(max_tickets)
        wm = int(window_minutes)

        err = _validate_limit(mt, wm)
        if err:
            return await reply_once(interaction, {"content": f"❌ {err}", "ephemeral": True})

        ok = await upsert_ticket_creation_settings(
            guild.id,
            {
                "max_tickets_per_window": mt,
                "window_minutes": wm,
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating ticket limit.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Ticket Limit Updated", settings)
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

        if member.bot:
            return await reply_once(
                interaction,
                {"content": "❌ Do not blacklist bot accounts from ticket creation using this command.", "ephemeral": True},
            )

        if int(member.id) == int(interaction.user.id):
            return await reply_once(
                interaction,
                {"content": "❌ You cannot blacklist yourself from ticket creation.", "ephemeral": True},
            )

        if _member_is_staff_like(member):
            return await reply_once(
                interaction,
                {"content": "❌ Refusing to blacklist a staff-level member from ticket creation through this command.", "ephemeral": True},
            )

        reason_clean = _safe_str(reason, "Blocked from ticket creation")[:500]

        ok = await upsert_ticket_blacklist(
            guild.id,
            member.id,
            {
                "is_blocked": True,
                "reason": reason_clean,
                "blocked_by": str(interaction.user.id),
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed adding ticket blacklist entry.", "ephemeral": True})

        row = None
        if get_ticket_blacklist_row is not None:
            try:
                row = await get_ticket_blacklist_row(guild.id, member.id)
            except Exception:
                row = None

        embed = _blacklist_embed("⛔ Ticket Blacklist Added", member, row, fallback_reason=reason_clean)
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

        existing = None
        if get_ticket_blacklist_row is not None:
            try:
                existing = await get_ticket_blacklist_row(guild.id, member.id)
            except Exception:
                existing = None

        if not existing:
            return await reply_once(
                interaction,
                {"content": f"ℹ️ {member.mention} is not currently blacklisted.", "ephemeral": True},
            )

        ok = await delete_ticket_blacklist(guild.id, member.id)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed removing ticket blacklist entry.", "ephemeral": True})

        embed = discord.Embed(title="✅ Ticket Blacklist Removed", color=discord.Color.green(), timestamp=now_utc())
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)
        embed.add_field(name="Previous Reason", value=_safe_str(existing.get("reason"), "—")[:1024], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

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
        embed = _blacklist_embed("🔎 Ticket Blacklist Check", member, row)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
