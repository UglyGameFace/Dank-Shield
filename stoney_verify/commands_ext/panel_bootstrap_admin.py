from __future__ import annotations

from typing import Any, Dict, List

import discord

from ..globals import now_utc
from ..tickets_new.panel_bootstrap import (
    bootstrap_panel_system_for_bot,
    bootstrap_panel_system_for_guild,
    panel_bootstrap_status,
    start_panel_bootstrap_once,
    start_panel_bootstrap_worker,
    stop_panel_bootstrap_worker,
)
from .common import _staff_check, reply_once, safe_defer


# ============================================================
# commands_ext/panel_bootstrap_admin.py
# ------------------------------------------------------------
# Safe admin command layer for panel bootstrap/self-heal.
# This file is intentionally compact and stable because command
# registration must not fail during public startup.
# ============================================================


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
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


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _guild_result_line(row: Dict[str, Any]) -> str:
    ok = "✅" if _safe_bool(row.get("ok"), False) else "❌"
    guild_id = _safe_str(row.get("guild_id"), "unknown")
    guild_name = _safe_str(row.get("guild_name"), "Unknown Guild")
    parts: List[str] = [f"{ok} `{guild_id}` — **{guild_name}**"]
    if _safe_bool(row.get("saved_discovery"), False):
        parts.append("discovery saved")
    if _safe_bool(row.get("default_preset_ready"), False):
        parts.append("preset ready")
    if _safe_bool(row.get("default_panel_created"), False):
        parts.append("default panel created")
    repaired = _safe_int(row.get("rules_repaired"), 0)
    if repaired > 0:
        parts.append(f"rules repaired={repaired}")
    error = _safe_str(row.get("error"))
    if error:
        parts.append(f"error={_truncate(error, 160)}")
    return " • ".join(parts)


def _single_guild_embed(result: Dict[str, Any]) -> discord.Embed:
    ok = _safe_bool(result.get("ok"), False)
    embed = discord.Embed(
        title="🧩 Panel Bootstrap Result",
        color=discord.Color.green() if ok else discord.Color.red(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Guild",
        value=f"{_safe_str(result.get('guild_name'), 'Unknown Guild')}\n`{_safe_str(result.get('guild_id'), 'unknown')}`",
        inline=False,
    )
    embed.add_field(name="OK", value=f"`{ok}`", inline=True)
    embed.add_field(name="Discovery Saved", value=f"`{_safe_bool(result.get('saved_discovery'), False)}`", inline=True)
    embed.add_field(name="Default Preset Ready", value=f"`{_safe_bool(result.get('default_preset_ready'), False)}`", inline=True)
    embed.add_field(name="Default Panel Created", value=f"`{_safe_bool(result.get('default_panel_created'), False)}`", inline=True)
    embed.add_field(name="Rules Repaired", value=f"`{_safe_int(result.get('rules_repaired'), 0)}`", inline=True)
    if _safe_str(result.get("error")):
        embed.add_field(name="Error", value=_truncate(result.get("error"), 1024), inline=False)
    return embed


def _summary_embed(summary: Dict[str, Any]) -> discord.Embed:
    ok = _safe_bool(summary.get("ok"), False)
    embed = discord.Embed(
        title="🧩 Panel Bootstrap Summary",
        color=discord.Color.green() if ok else discord.Color.orange(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Guilds Seen", value=f"`{_safe_int(summary.get('guilds_seen'), 0)}`", inline=True)
    embed.add_field(name="Guilds OK", value=f"`{_safe_int(summary.get('guilds_ok'), 0)}`", inline=True)
    embed.add_field(name="Guilds Failed", value=f"`{_safe_int(summary.get('guilds_failed'), 0)}`", inline=True)
    embed.add_field(name="Rules Repaired", value=f"`{_safe_int(summary.get('rules_repaired'), 0)}`", inline=True)
    embed.add_field(name="Default Panels Created", value=f"`{_safe_int(summary.get('default_panels_created'), 0)}`", inline=True)
    results = summary.get("results")
    if isinstance(results, list) and results:
        lines = [_guild_result_line(row) for row in results[:15] if isinstance(row, dict)]
        embed.add_field(name="Results", value=_truncate("\n".join(lines), 1024) or "No details.", inline=False)
    return embed


def _status_embed(status: Dict[str, Any]) -> discord.Embed:
    state = _safe_str(status.get("task_state"), "unknown")
    color = discord.Color.green() if state == "running" else discord.Color.orange()
    embed = discord.Embed(
        title="🧩 Panel Bootstrap Worker Status",
        color=color,
        timestamp=now_utc(),
    )
    embed.add_field(name="Task State", value=f"`{state}`", inline=True)
    embed.add_field(name="Interval Seconds", value=f"`{_safe_int(status.get('interval_seconds'), 0)}`", inline=True)
    embed.add_field(name="Guild Concurrency", value=f"`{_safe_int(status.get('guild_concurrency'), 0)}`", inline=True)
    last_run = status.get("last_run")
    if isinstance(last_run, dict) and last_run:
        lines = [f"`{gid}` → `{ran_at}`" for gid, ran_at in list(last_run.items())[:15]]
        embed.add_field(name="Last Run", value=_truncate("\n".join(lines), 1024), inline=False)
    else:
        embed.add_field(name="Last Run", value="No guild bootstrap run recorded yet.", inline=False)
    last_error = status.get("last_error")
    if isinstance(last_error, dict) and last_error:
        lines = [f"`{gid}` → {_truncate(err, 140)}" for gid, err in list(last_error.items())[:10]]
        embed.add_field(name="Last Errors", value=_truncate("\n".join(lines), 1024), inline=False)
    return embed


def register_panel_bootstrap_admin_commands(bot, tree) -> None:
    @tree.command(name="ticket_panel_bootstrap_status", description="Show panel bootstrap worker status.")
    async def ticket_panel_bootstrap_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        await reply_once(interaction, {"embed": _status_embed(panel_bootstrap_status()), "ephemeral": True})

    @tree.command(name="ticket_panel_bootstrap_run", description="Run panel bootstrap/self-heal for this server now.")
    async def ticket_panel_bootstrap_run(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        await safe_defer(interaction, ephemeral=True)
        result = await bootstrap_panel_system_for_guild(guild, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        await interaction.followup.send(embed=_single_guild_embed(result), ephemeral=True)

    @tree.command(name="ticket_panel_bootstrap_all", description="Run panel bootstrap/self-heal for every attached guild.")
    async def ticket_panel_bootstrap_all(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        await safe_defer(interaction, ephemeral=True)
        summary = await bootstrap_panel_system_for_bot(bot, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        await interaction.followup.send(embed=_summary_embed(summary), ephemeral=True)

    @tree.command(name="ticket_panel_bootstrap_start", description="Start the recurring panel bootstrap/self-heal worker.")
    async def ticket_panel_bootstrap_start(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        task = start_panel_bootstrap_worker(bot, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        content = "✅ Panel bootstrap worker started." if task is not None else "⚠️ Panel bootstrap worker did not start."
        await reply_once(interaction, {"content": content, "embed": _status_embed(panel_bootstrap_status()), "ephemeral": True})

    @tree.command(name="ticket_panel_bootstrap_once", description="Schedule one background panel bootstrap pass.")
    async def ticket_panel_bootstrap_once(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        task = start_panel_bootstrap_once(bot, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        content = "✅ One-shot panel bootstrap scheduled." if task is not None else "⚠️ One-shot panel bootstrap was not scheduled."
        await reply_once(interaction, {"content": content, "embed": _status_embed(panel_bootstrap_status()), "ephemeral": True})

    @tree.command(name="ticket_panel_bootstrap_stop", description="Stop the recurring panel bootstrap/self-heal worker.")
    async def ticket_panel_bootstrap_stop(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        await safe_defer(interaction, ephemeral=True)
        await stop_panel_bootstrap_worker()
        await interaction.followup.send(content="✅ Panel bootstrap worker stopped.", embed=_status_embed(panel_bootstrap_status()), ephemeral=True)
