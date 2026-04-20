from __future__ import annotations

from typing import Any, Dict, Optional

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


_DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "sla_breach_alerts_enabled": True,
    "inactivity_reminders_enabled": True,
    "auto_close_enabled": False,
    "inactivity_reminder_minutes": 240,
    "auto_close_minutes": 1440,
    "staff_alert_channel_id": None,
}

_MIN_INACTIVITY_MINUTES = 1
_MIN_AUTOCLOSE_MINUTES = 5
_MAX_AUTOMATION_MINUTES = 10080  # 7 days


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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _normalize_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    src = dict(settings or {})
    out = dict(_DEFAULT_SETTINGS)

    out["enabled"] = _safe_bool(src.get("enabled"), _DEFAULT_SETTINGS["enabled"])
    out["sla_breach_alerts_enabled"] = _safe_bool(
        src.get("sla_breach_alerts_enabled"),
        _DEFAULT_SETTINGS["sla_breach_alerts_enabled"],
    )
    out["inactivity_reminders_enabled"] = _safe_bool(
        src.get("inactivity_reminders_enabled"),
        _DEFAULT_SETTINGS["inactivity_reminders_enabled"],
    )
    out["auto_close_enabled"] = _safe_bool(
        src.get("auto_close_enabled"),
        _DEFAULT_SETTINGS["auto_close_enabled"],
    )
    out["inactivity_reminder_minutes"] = max(
        _MIN_INACTIVITY_MINUTES,
        _safe_int(src.get("inactivity_reminder_minutes"), _DEFAULT_SETTINGS["inactivity_reminder_minutes"]),
    )
    out["auto_close_minutes"] = max(
        _MIN_AUTOCLOSE_MINUTES,
        _safe_int(src.get("auto_close_minutes"), _DEFAULT_SETTINGS["auto_close_minutes"]),
    )
    out["staff_alert_channel_id"] = _safe_str(src.get("staff_alert_channel_id")) or None

    return out


def _settings_warnings(settings: Dict[str, Any], guild: Optional[discord.Guild] = None) -> list[str]:
    warnings: list[str] = []

    enabled = _safe_bool(settings.get("enabled"), False)
    sla_enabled = _safe_bool(settings.get("sla_breach_alerts_enabled"), True)
    reminders_enabled = _safe_bool(settings.get("inactivity_reminders_enabled"), True)
    autoclose_enabled = _safe_bool(settings.get("auto_close_enabled"), False)

    reminder_minutes = max(_MIN_INACTIVITY_MINUTES, _safe_int(settings.get("inactivity_reminder_minutes"), 240))
    autoclose_minutes = max(_MIN_AUTOCLOSE_MINUTES, _safe_int(settings.get("auto_close_minutes"), 1440))
    alert_channel_id = _safe_int(settings.get("staff_alert_channel_id"), 0)

    if not enabled and (sla_enabled or reminders_enabled or autoclose_enabled):
        warnings.append("Automation is disabled, so the feature toggles below will not run until automation is enabled.")

    if autoclose_enabled and autoclose_minutes <= reminder_minutes:
        warnings.append(
            "Auto-close is set at or before the inactivity reminder threshold. "
            "The worker will still force auto-close later, but this configuration is misleading."
        )

    if alert_channel_id <= 0:
        warnings.append("No staff alert channel is set. Alerts/reminders can still post in the ticket, but staff-side visibility is weaker.")
    elif guild is not None and guild.get_channel(alert_channel_id) is None:
        warnings.append("The configured staff alert channel is missing or inaccessible.")

    return warnings


def _settings_embed(
    title: str,
    settings: Dict[str, Any],
    *,
    guild: Optional[discord.Guild] = None,
    runtime: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    normalized = _normalize_settings(settings)
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())

    embed.add_field(
        name="Enabled",
        value="Yes" if _safe_bool(normalized.get("enabled"), False) else "No",
        inline=True,
    )
    embed.add_field(
        name="SLA Breach Alerts",
        value="Yes" if _safe_bool(normalized.get("sla_breach_alerts_enabled"), True) else "No",
        inline=True,
    )
    embed.add_field(
        name="Inactivity Reminders",
        value="Yes" if _safe_bool(normalized.get("inactivity_reminders_enabled"), True) else "No",
        inline=True,
    )
    embed.add_field(
        name="Auto Close",
        value="Yes" if _safe_bool(normalized.get("auto_close_enabled"), False) else "No",
        inline=True,
    )
    embed.add_field(
        name="Reminder Minutes",
        value=f"`{_safe_int(normalized.get('inactivity_reminder_minutes'), 240)}`",
        inline=True,
    )
    embed.add_field(
        name="Auto Close Minutes",
        value=f"`{_safe_int(normalized.get('auto_close_minutes'), 1440)}`",
        inline=True,
    )

    alert_channel = _safe_int(normalized.get("staff_alert_channel_id"), 0)
    embed.add_field(
        name="Staff Alert Channel",
        value=(f"<#{alert_channel}>" if alert_channel > 0 else "Not set"),
        inline=False,
    )

    warnings = _settings_warnings(normalized, guild)
    if warnings:
        embed.add_field(
            name="Warnings",
            value="\n".join([f"• {w}" for w in warnings])[:1024],
            inline=False,
        )

    if runtime:
        embed.add_field(
            name="Worker Running",
            value="Yes" if bool(runtime.get("task_running")) else "No",
            inline=True,
        )
        embed.add_field(
            name="Poll Seconds",
            value=f"`{_safe_int(runtime.get('poll_seconds'), 0)}`",
            inline=True,
        )
        embed.add_field(
            name="Last Run",
            value=f"`{runtime.get('last_run_at') or 'never'}`",
            inline=False,
        )

        if guild is not None:
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

    return embed


async def _load_settings_or_default(guild_id: int) -> Dict[str, Any]:
    if get_ticket_automation_settings is None:
        return dict(_DEFAULT_SETTINGS)

    try:
        settings = await get_ticket_automation_settings(guild_id)
        if isinstance(settings, dict):
            return _normalize_settings(settings)
    except Exception:
        pass

    return dict(_DEFAULT_SETTINGS)


def _validate_inactivity_minutes(value: int) -> Optional[str]:
    if value < _MIN_INACTIVITY_MINUTES:
        return f"Inactivity reminder minutes must be at least `{_MIN_INACTIVITY_MINUTES}`."
    if value > _MAX_AUTOMATION_MINUTES:
        return f"Inactivity reminder minutes cannot exceed `{_MAX_AUTOMATION_MINUTES}`."
    return None


def _validate_autoclose_minutes(value: int, current_reminder_minutes: int) -> Optional[str]:
    if value < _MIN_AUTOCLOSE_MINUTES:
        return f"Auto-close minutes must be at least `{_MIN_AUTOCLOSE_MINUTES}`."
    if value > _MAX_AUTOMATION_MINUTES:
        return f"Auto-close minutes cannot exceed `{_MAX_AUTOMATION_MINUTES}`."
    if value <= current_reminder_minutes:
        return (
            "Auto-close minutes must be greater than the inactivity reminder minutes, "
            f"which are currently `{current_reminder_minutes}`."
        )
    return None


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

        settings = await _load_settings_or_default(guild.id)
        runtime = get_ticket_automation_runtime_status() or {}

        embed = _settings_embed(
            "🤖 Ticket Automation Status",
            settings,
            guild=guild,
            runtime=runtime,
        )

        embed.add_field(
            name="Behavior",
            value=(
                "SLA alerts fire when a live ticket crosses its deadline.\n"
                "Inactivity reminders nudge the owner/assignee.\n"
                "Auto-close can close stale tickets after inactivity."
            )[:1024],
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

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Ticket Automation Updated", settings, guild=guild)
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

        ok = await upsert_ticket_automation_settings(
            guild.id,
            {"sla_breach_alerts_enabled": bool(enabled)},
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating SLA alert settings.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ SLA Alert Settings Updated", settings, guild=guild)
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

        value = int(minutes)
        err = _validate_inactivity_minutes(value)
        if err:
            return await reply_once(interaction, {"content": f"❌ {err}", "ephemeral": True})

        current = await _load_settings_or_default(guild.id)
        if _safe_bool(current.get("auto_close_enabled"), False):
            current_autoclose = _safe_int(current.get("auto_close_minutes"), 1440)
            if current_autoclose <= value:
                return await reply_once(
                    interaction,
                    {
                        "content": (
                            "❌ Inactivity reminder minutes must stay below the current auto-close minutes.\n"
                            f"Current auto-close minutes: `{current_autoclose}`"
                        ),
                        "ephemeral": True,
                    },
                )

        ok = await upsert_ticket_automation_settings(
            guild.id,
            {
                "inactivity_reminders_enabled": bool(enabled),
                "inactivity_reminder_minutes": value,
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating inactivity reminder settings.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Inactivity Reminder Settings Updated", settings, guild=guild)
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

        current = await _load_settings_or_default(guild.id)
        reminder_minutes = _safe_int(current.get("inactivity_reminder_minutes"), 240)

        value = int(minutes)
        err = _validate_autoclose_minutes(value, reminder_minutes)
        if err:
            return await reply_once(interaction, {"content": f"❌ {err}", "ephemeral": True})

        ok = await upsert_ticket_automation_settings(
            guild.id,
            {
                "auto_close_enabled": bool(enabled),
                "auto_close_minutes": value,
            },
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating auto-close settings.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Auto-Close Settings Updated", settings, guild=guild)
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

        if channel is not None:
            perms = channel.permissions_for(guild.me) if guild.me else None
            if perms is not None and (not perms.send_messages or not perms.view_channel):
                return await reply_once(
                    interaction,
                    {
                        "content": (
                            "❌ I do not have permission to view/send messages in that alert channel."
                        ),
                        "ephemeral": True,
                    },
                )

        ok = await upsert_ticket_automation_settings(
            guild.id,
            {"staff_alert_channel_id": str(channel.id) if channel is not None else None},
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed updating alert channel.", "ephemeral": True})

        settings = await _load_settings_or_default(guild.id)
        embed = _settings_embed("✅ Alert Channel Updated", settings, guild=guild)
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

        if run_ticket_automation_pass is None or get_ticket_automation_settings is None:
            return await reply_once(interaction, {"content": "❌ Ticket automation worker is unavailable.", "ephemeral": True})

        await interaction.response.defer(ephemeral=True)

        settings = await _load_settings_or_default(guild.id)
        summary = await run_ticket_automation_pass(guild_id=guild.id)

        if not _safe_bool(settings.get("enabled"), False):
            embed = _settings_embed("⚠️ Ticket Automation Run Skipped", settings, guild=guild)
            embed.add_field(
                name="Reason",
                value="Automation is disabled for this guild, so the worker skipped the run.",
                inline=False,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

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
        embed.add_field(
            name="Alert Channel",
            value=(
                f"<#{_safe_int(settings.get('staff_alert_channel_id'), 0)}>"
                if _safe_int(settings.get("staff_alert_channel_id"), 0) > 0
                else "Not set"
            ),
            inline=True,
        )

        warnings = _settings_warnings(settings, guild)
        if warnings:
            embed.add_field(
                name="Warnings",
                value="\n".join([f"• {w}" for w in warnings])[:1024],
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)
