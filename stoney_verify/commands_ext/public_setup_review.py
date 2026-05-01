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
    stoney_group,
)
from ..globals import get_supabase
from ..guild_config import get_guild_config, guild_config_cache_snapshot


_REVIEW_COMMAND_ATTACHED = False
_DB_CHECK_COMMAND_ATTACHED = False


def _status_line(blockers: List[str], warnings: List[str]) -> str:
    if blockers:
        return "🚫 **Not ready** — run `/stoney setup` and fix the blockers before beta/public use."
    if warnings:
        return "⚠️ **Usable with warnings** — safe to test, but review the warnings."
    return "✅ **Ready** — no setup blockers or warnings found."


def _next_steps(blockers: List[str], warnings: List[str]) -> list[str]:
    steps: list[str] = []
    joined = "\n".join(blockers + warnings).lower()

    if "ticket" in joined or "category" in joined or "staff" in joined or "transcript" in joined:
        steps.append("Run `/stoney setup` and use the setup buttons to repair ticket categories, staff role, or transcript channel.")
    if "verify" in joined or "role" in joined or "vc" in joined:
        steps.append("Run `/stoney setup` and use the setup buttons to repair verification channels or role hierarchy.")
    if "modlog" in joined or "join/exit" in joined or "raid" in joined or "log" in joined:
        steps.append("Run `/stoney setup` and use the setup buttons to repair modlog, raid/security log, or join/exit log channels.")
    if "env/default" in joined or "fallback" in joined:
        steps.append("Run `/stoney setup` so this server saves its own database-backed config instead of using fallback config.")

    if not steps and blockers:
        steps.append("Fix the blockers listed above, then run `/stoney setup` again.")
    if not steps and warnings:
        steps.append("Warnings are allowed, but review them before inviting the bot to public/beta servers.")
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
        title="🧭 Stoney Setup Review",
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
    embed.set_footer(text="Read-only review. Use /stoney setup for fixes.")
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
        elif "column" in lowered and "guild_id" in lowered:
            result["error_kind"] = "missing_guild_id_column"
        else:
            result["error_kind"] = "read_failed"
        return result


def _db_probe_next_steps(probe: Mapping[str, Any], cfg: Any) -> list[str]:
    steps: list[str] = []
    error_kind = _safe_str(probe.get("error_kind"), "")
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")

    if not probe.get("supabase_available"):
        steps.append("Check `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the bot environment.")
    elif error_kind == "table_not_in_rest_schema_cache":
        steps.append("Confirm the table exists as `public.guild_configs`, not in another schema or with a different name.")
        steps.append("In Supabase, reload/refresh the REST schema cache or wait briefly, then restart the bot.")
        steps.append("Confirm `STONEY_GUILD_CONFIG_TABLE` is unset or exactly `guild_configs`.")
    elif error_kind == "missing_guild_id_column":
        steps.append("Confirm `guild_configs` has a `guild_id` column, ideally text with a unique index.")
    elif error_kind == "permission_or_rls":
        steps.append("Confirm the bot is using the service-role key server-side only. Do not expose it to the dashboard/client.")
    elif probe.get("read_ok") and not probe.get("row_found"):
        steps.append("Run `/stoney setup` to create this guild's config row.")
    elif probe.get("read_ok") and source.startswith("supabase:"):
        steps.append("Database config is visible. If Discord still shows old values, restart once after a clean sync.")
    elif probe.get("read_ok"):
        steps.append("Database is readable, but runtime config is still using fallback. Run `/stoney setup` and check again.")

    if not steps:
        steps.append("Fix the database error shown above, then run `/stoney setup` again.")
    return steps


def _db_check_embed(guild: discord.Guild, cfg: Any, probe: Mapping[str, Any]) -> discord.Embed:
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
    read_ok = bool(probe.get("read_ok"))
    row_found = bool(probe.get("row_found"))
    error = _safe_str(probe.get("error"), "")
    error_kind = _safe_str(probe.get("error_kind"), "")
    table = _safe_str(probe.get("table"), "guild_configs")
    cache = guild_config_cache_snapshot()

    healthy = read_ok and row_found and source.startswith("supabase:")
    embed = discord.Embed(
        title="🧪 Stoney DB Config Check",
        description=(
            "✅ **Guild config DB is visible and runtime is using it.**"
            if healthy
            else "⚠️ **Guild config DB needs attention or this guild is still using fallback config.**"
        ),
        color=discord.Color.green() if healthy else discord.Color.gold(),
    )
    embed.add_field(
        name="Connection",
        value=(
            f"Supabase client: `{'available' if probe.get('supabase_available') else 'missing'}`\n"
            f"Config table: `{table}`\n"
            f"Read query: `{'ok' if read_ok else 'failed'}`\n"
            f"Row for this guild: `{'found' if row_found else 'not found'}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Runtime Config",
        value=(
            f"Runtime source: `{source}`\n"
            f"Cache table: `{cache.get('table')}`\n"
            f"Cached guilds: `{cache.get('cached_guilds')}`"
        ),
        inline=False,
    )
    columns = probe.get("row_columns") or []
    if columns:
        embed.add_field(name="Detected Row Columns", value=_field_text([", ".join(str(x) for x in columns)], empty="None"), inline=False)
    if error:
        embed.add_field(name=f"DB Error ({error_kind or 'unknown'})", value=f"```txt\n{error}\n```", inline=False)
    embed.add_field(name="Next Steps", value=_field_text(_db_probe_next_steps(probe, cfg), empty="✅ None"), inline=False)
    embed.set_footer(text="Read-only diagnostic. Use /stoney setup for setup fixes.")
    return embed


async def _setup_review_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed loading config for setup review: `{e}`", ephemeral=True)

    await interaction.followup.send(
        embeds=[
            _setup_review_embed(guild, cfg),
            _config_embed(guild, cfg, title="📌 Current Saved Config"),
        ],
        ephemeral=True,
    )


async def _db_check_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        cfg = None
        load_error = e
    else:
        load_error = None

    probe = await asyncio.to_thread(_probe_guild_config_db_sync, int(guild.id))

    if cfg is None:
        embed = discord.Embed(
            title="🧪 Stoney DB Config Check",
            description="❌ Runtime config failed to load before DB probe could complete.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Runtime Load Error", value=f"```txt\n{load_error!r}\n```", inline=False)
        probe_error = _safe_str(probe.get("error"), "")
        if probe_error:
            embed.add_field(name="DB Probe Error", value=f"```txt\n{probe_error}\n```", inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    await interaction.followup.send(embed=_db_check_embed(guild, cfg, probe), ephemeral=True)


def _attach_setup_review_command() -> None:
    global _REVIEW_COMMAND_ATTACHED
    if _REVIEW_COMMAND_ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup-review")
    except Exception:
        existing = None
    if existing is not None:
        _REVIEW_COMMAND_ATTACHED = True
        return
    stoney_group.add_command(
        discord.app_commands.Command(
            name="setup-review",
            description="Review this server's Stoney setup without changing anything.",
            callback=_setup_review_callback,
        )
    )
    _REVIEW_COMMAND_ATTACHED = True


def _attach_db_check_command() -> None:
    global _DB_CHECK_COMMAND_ATTACHED
    if _DB_CHECK_COMMAND_ATTACHED:
        return
    try:
        existing = stoney_group.get_command("db-check")
    except Exception:
        existing = None
    if existing is not None:
        _DB_CHECK_COMMAND_ATTACHED = True
        return
    stoney_group.add_command(
        discord.app_commands.Command(
            name="db-check",
            description="Check whether this server's saved config is visible to the bot database client.",
            callback=_db_check_callback,
        )
    )
    _DB_CHECK_COMMAND_ATTACHED = True


_attach_setup_review_command()
_attach_db_check_command()


def register_public_setup_review_commands(bot, tree) -> None:
    _ = bot, tree
    _attach_setup_review_command()
    _attach_db_check_command()
    try:
        print("✅ public_setup_review: attached /stoney setup-review and /stoney db-check commands")
    except Exception:
        pass


__all__ = ["register_public_setup_review_commands"]
