from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import _staff_check, reply_once

try:
    from ..workers.ticket_automation_worker import (
        get_ticket_automation_settings,
        upsert_ticket_automation_settings,
        run_ticket_automation_pass,
        get_ticket_automation_runtime_status,
    )
except Exception:
    get_ticket_automation_settings = None  # type: ignore
    upsert_ticket_automation_settings = None  # type: ignore
    run_ticket_automation_pass = None  # type: ignore
    get_ticket_automation_runtime_status = None  # type: ignore


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _settings_embed(title: str, settings: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Enabled", value="Yes" if _safe_bool(settings.get("enabled"), False) else "No", inline=True)
    embed.add_field(name="SLA Breach Alerts", value="Yes" if _safe_bool(settings.get("sla_breach_alerts_enabled"), True) else "No", inline=True)
    embed.add_field(name="Inactivity Reminders", value="Yes" if _safe_bool(settings.get("inactivity_reminders_enabled"), True) else "No", inline=True)
    embed.add_field(name="Auto Close", value="Yes" if _safe_bool(settings.get("auto_close_enabled"), False) else "No", inline=True)
    embed.add_field(name="Reminder Minutes", value=f"`{_safe_int(settings.get('inactivity_reminder_minutes'), 240)}`", inline=True)
    embed.add_field(name="Auto Close Minutes", value=f"`{_safe_int(settings.get('auto_close_minutes'), 1440)}`", inline=True)
    alert_channel = _safe_int(settings.get("staff_alert_channel_id"), 0)
    embed.add_field(name="Staff Alert Channel", value=(f"<#{alert_channel}>" if alert_channel > 0 else "Not set"), inline=False)
    return embed


def register_ticket_automation_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_automation_status",
        description="Show ticket automation worker status and settings.",
    )
    async def ticket_automation_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if get_ticket_automation_settings is None or get_ticket_automation_runtime_status is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        runtime = get_ticket_automation_runtime_status() or {}

        embed = _settings_embed("🤖 Ticket Automation Status", settings or {})
        embed.add_field(name="Worker Running", value="Yes" if bool(runtime.get("task_running")) else "No", inline=True)
        embed.add_field(name="Poll Seconds", value=f"`{_safe_int(runtime.get('poll_seconds'), 0)}`", inline=True)
        embed.add_field(name="Last Run", value=f"`{runtime.get('last_run_at') or 'never'}`", inline=False)

        guild_summary = (runtime.get("guild_summaries") or {}).get(int(guild.id)) or {}
        if guild_summary:
            embed.add_field(
                name="Last Guild Summary",
                value=(
                    f"Checked: `{_safe_int(guild_summary.get('tickets_checked'), 0)}`\n"
                    f"SLA Alerts: `{_safe_int(guild_summary.get('sla_breach_alerts'), 0)}`\n"
                    f"Reminders: `{_safe_int(guild_summary.get('inactivity_reminders'), 0)}`\n"
                    f"Auto Closed: `{_safe_int(guild_summary.get('auto_closed'), 0)}`"
                ),
                inline=False,
            )

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_enable",
        description="Enable or disable the ticket automation worker for this guild.",
    )
    @app_commands.describe(enabled="Turn automation on or off for this guild")
    async def ticket_automation_enable(interaction: discord.Interaction, enabled: bool):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_automation_settings is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        ok = await upsert_ticket_automation_settings(guild.id, {"enabled": bool(enabled)})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating automation enable flag.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        embed = _settings_embed("✅ Ticket Automation Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_sla",
        description="Enable or disable SLA breach alerts.",
    )
    @app_commands.describe(enabled="Turn SLA breach alerts on or off")
    async def ticket_automation_sla(interaction: discord.Interaction, enabled: bool):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_automation_settings is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        ok = await upsert_ticket_automation_settings(guild.id, {"sla_breach_alerts_enabled": bool(enabled)})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating SLA alert settings.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        embed = _settings_embed("✅ SLA Alert Settings Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_inactivity",
        description="Configure inactivity reminder behavior.",
    )
    @app_commands.describe(
        enabled="Turn inactivity reminders on or off",
        minutes="Minutes of inactivity before reminding",
    )
    async def ticket_automation_inactivity(
        interaction: discord.Interaction,
        enabled: bool,
        minutes: int,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_automation_settings is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        value = max(1, int(minutes))
        ok = await upsert_ticket_automation_settings(
            guild.id,
            {
                "inactivity_reminders_enabled": bool(enabled),
                "inactivity_reminder_minutes": value,
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating inactivity reminder settings.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        embed = _settings_embed("✅ Inactivity Reminder Settings Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_autoclose",
        description="Configure automatic ticket closure after inactivity.",
    )
    @app_commands.describe(
        enabled="Turn auto-close on or off",
        minutes="Minutes of inactivity before closing the ticket",
    )
    async def ticket_automation_autoclose(
        interaction: discord.Interaction,
        enabled: bool,
        minutes: int,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_automation_settings is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        value = max(5, int(minutes))
        ok = await upsert_ticket_automation_settings(
            guild.id,
            {
                "auto_close_enabled": bool(enabled),
                "auto_close_minutes": value,
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating auto-close settings.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        embed = _settings_embed("✅ Auto-Close Settings Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_alert_channel",
        description="Set or clear the staff alert channel for ticket automation notices.",
    )
    @app_commands.describe(channel="Alert channel. Leave empty to clear it.")
    async def ticket_automation_alert_channel(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if upsert_ticket_automation_settings is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        ok = await upsert_ticket_automation_settings(
            guild.id,
            {"staff_alert_channel_id": str(channel.id) if channel is not None else None},
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating alert channel.", "ephemeral": True})

        settings = await get_ticket_automation_settings(guild.id)
        embed = _settings_embed("✅ Alert Channel Updated", settings or {})
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_automation_run_now",
        description="Run one ticket automation pass right now for this guild.",
    )
    async def ticket_automation_run_now(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        if run_ticket_automation_pass is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        await interaction.response.defer(ephemeral=True)
        summary = await run_ticket_automation_pass(guild_id=guild.id)

        embed = discord.Embed(
            title="⚙️ Ticket Automation Run Complete",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Guilds Checked", value=f"`{_safe_int(summary.get('guilds_checked'), 0)}`", inline=True)
        embed.add_field(name="Tickets Checked", value=f"`{_safe_int(summary.get('tickets_checked'), 0)}`", inline=True)
        embed.add_field(name="SLA Alerts", value=f"`{_safe_int(summary.get('sla_breach_alerts'), 0)}`", inline=True)
        embed.add_field(name="Reminders", value=f"`{_safe_int(summary.get('inactivity_reminders'), 0)}`", inline=True)
        embed.add_field(name="Auto Closed", value=f"`{_safe_int(summary.get('auto_closed'), 0)}`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
