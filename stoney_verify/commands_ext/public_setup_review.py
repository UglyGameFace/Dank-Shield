from __future__ import annotations

import asyncio
from typing import Any, List, Mapping, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _config_embed,
    _config_table_name,
    _field_text,
    _require_setup_permission,
    _safe_str,
    dank_group,
)
from ..globals import get_supabase
from ..guild_config import get_guild_config, guild_config_cache_snapshot


_REVIEW_COMMAND_ATTACHED = False
_DB_CHECK_COMMAND_ATTACHED = False


def _status_line(blockers: List[str], warnings: List[str]) -> str:
    if blockers:
        return "🚫 **Not ready** — run `/dank setup` and fix the blockers before public use."
    if warnings:
        return "⚠️ **Usable with warnings** — safe to test, but review the warnings."
    return "✅ **Ready** — no setup blockers or warnings found."


def _next_steps(blockers: List[str], warnings: List[str]) -> list[str]:
    steps: list[str] = []
    joined = "\n".join(blockers + warnings).lower()

    if "ticket" in joined or "category" in joined or "staff" in joined or "transcript" in joined:
        steps.append("Run `/dank setup` and use the setup buttons to repair ticket categories, staff role, or transcript channel.")
    if "verify" in joined or "role" in joined or "vc" in joined:
        steps.append("Run `/dank setup` and use the setup buttons to repair verification channels or role hierarchy.")
    if "modlog" in joined or "join/exit" in joined or "raid" in joined or "log" in joined:
        steps.append("Run `/dank setup` and use the setup buttons to repair modlog, raid/security log, or join/exit log channels.")
    if "env/default" in joined or "fallback" in joined:
        steps.append("Run `/dank setup` so this server saves its own database-backed config instead of using fallback config.")

    if not steps and blockers:
        steps.append("Fix the blockers listed above, then run `/dank setup` again.")
    if not steps and warnings:
        steps.append("Warnings are allowed, but review them before inviting the bot to public servers.")
    if not steps:
        steps.append("Post or refresh your ticket panel, then test ticket create/close/reopen as a staff member.")

    out: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if step not in seen:
            seen.add(step)
            out.append(step)
    return out


def _setup_review_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")

    embed = discord.Embed(
        title="🧭 Dank Shield Setup Review",
        description=(
            f"{_status_line(blockers, warnings)}\n"
            f"Config source: `{source}`\n"
            f"Guild: `{guild.id}`"
        ),
        color=discord.Color.red() if blockers else discord.Color.gold() if warnings else discord.Color.green(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.add_field(name="Next Steps", value=_field_text(_next_steps(blockers, warnings), empty="✅ No setup actions needed."), inline=False)
    embed.set_footer(text="Read-only review. Use /dank setup for fixes.")
    return embed


def _probe_guild_config_db_sync(guild_id: int) -> dict[str, Any]:
    table = _config_table_name()
    result: dict[str, Any] = {
        "table": table,
        "supabase_available": False,
        "read_ok": False,
        "row_found": False,
        "row_columns": [],
        "error": "",
        "error_kind": "",
    }

    sb = get_supabase()
    if sb is None:
        result["error"] = "Supabase client is not available from get_supabase()."
        result["error_kind"] = "supabase_unavailable"
        return result

    result["supabase_available"] = True

    try:
        response = (
            sb.table(table)
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        result["read_ok"] = True
        result["row_found"] = bool(rows)
        if rows and isinstance(rows[0], Mapping):
            result["row_columns"] = sorted(str(k) for k in rows[0].keys())[:40]
        return result
    except Exception as e:
        text = repr(e)
        lowered = text.lower()
        result["error"] = text[:900]
        if "pgrst205" in lowered or "schema cache" in lowered or "could not find the table" in lowered:
            result["error_kind"] = "table_not_in_rest_schema_cache"
        elif "permission" in lowered or "row-level security" in lowered or "rls" in lowered:
            result["error_kind"] = "permission_or_rls"
        else:
            result["error_kind"] = type(e).__name__
        return result


async def _probe_guild_config_db(guild_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(_probe_guild_config_db_sync, int(guild_id))


def attach_setup_review_commands() -> None:
    global _REVIEW_COMMAND_ATTACHED, _DB_CHECK_COMMAND_ATTACHED

    if not _REVIEW_COMMAND_ATTACHED:
        try:
            @dank_group.command(name="setup-review", description="Read-only review of this server's Dank Shield setup")
            async def setup_review(interaction: discord.Interaction) -> None:
                if interaction.guild is None:
                    return await interaction.response.send_message("❌ Run this inside a server.", ephemeral=True)
                if not await _require_setup_permission(interaction):
                    return
                await safe_defer(interaction, ephemeral=True)
                cfg = await get_guild_config(interaction.guild.id, refresh=True)
                embed = _setup_review_embed(interaction.guild, cfg)
                await interaction.followup.send(embed=embed, ephemeral=True)

            _REVIEW_COMMAND_ATTACHED = True
            print("✅ setup-review command attached.")
        except Exception as e:
            print("⚠️ setup-review command attach failed:", repr(e))

    if not _DB_CHECK_COMMAND_ATTACHED:
        try:
            @dank_group.command(name="db-check", description="Check whether this server's setup row is visible to the bot")
            async def db_check(interaction: discord.Interaction) -> None:
                if interaction.guild is None:
                    return await interaction.response.send_message("❌ Run this inside a server.", ephemeral=True)
                if not await _require_setup_permission(interaction):
                    return
                await safe_defer(interaction, ephemeral=True)
                result = await _probe_guild_config_db(interaction.guild.id)
                snapshot = guild_config_cache_snapshot(interaction.guild.id)
                embed = discord.Embed(
                    title="🗄️ Dank Shield Setup DB Check",
                    description=f"Guild `{interaction.guild.id}` • table `{result.get('table')}`",
                    color=discord.Color.green() if result.get("read_ok") else discord.Color.red(),
                )
                embed.add_field(name="Supabase Client", value="✅ available" if result.get("supabase_available") else "❌ unavailable", inline=True)
                embed.add_field(name="Read OK", value="✅ yes" if result.get("read_ok") else "❌ no", inline=True)
                embed.add_field(name="Setup Row", value="✅ found" if result.get("row_found") else "⚠️ missing", inline=True)
                embed.add_field(name="REST Columns", value=", ".join(result.get("row_columns") or [])[:1024] or "None", inline=False)
                embed.add_field(name="Cache Snapshot", value=f"source=`{snapshot.get('source')}` age=`{snapshot.get('age_seconds')}` keys=`{snapshot.get('cached_keys_count')}`", inline=False)
                if result.get("error"):
                    embed.add_field(name=f"Error ({result.get('error_kind')})", value=str(result.get("error"))[:1024], inline=False)
                embed.set_footer(text="If this fails after migrations, refresh Supabase REST schema cache or check service-role env vars.")
                await interaction.followup.send(embed=embed, ephemeral=True)

            _DB_CHECK_COMMAND_ATTACHED = True
            print("✅ setup db-check command attached.")
        except Exception as e:
            print("⚠️ setup db-check command attach failed:", repr(e))


attach_setup_review_commands()

__all__ = ["attach_setup_review_commands"]
