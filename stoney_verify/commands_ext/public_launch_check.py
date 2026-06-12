from __future__ import annotations

"""
Public launch readiness command.

/dank launch-check is the one-screen production checklist admins should run
before relying on Stoney in a real server. It combines:

- per-guild setup health
- raw DB config completeness checks
- public isolation/config-source checks
- command sync/duplicate-command risk
- structured API security checks
- scaling/sharding reminders

It is read-only and never changes server config.
"""

import asyncio
import os
from typing import Any, Mapping, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _safe_str,
    stoney_group,
)
from ..guild_config import get_guild_config
from ..globals import get_supabase


_LAUNCH_CHECK_ATTACHED = False
_TREE: Any = None

_JSON_CONFIG_KEYS = {"settings", "config", "metadata", "meta"}

_REQUIRED_DB_FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Open ticket category", ("ticket_category_id", "tickets_category_id", "support_category_id"), "/dank setup-tickets"),
    ("Ticket staff role", ("staff_role_id", "support_role_id", "mod_role_id"), "/dank setup-tickets"),
    ("Verify text channel", ("verify_channel_id", "verification_channel_id", "verify_channel"), "/dank setup-verify"),
    ("Unverified role", ("unverified_role_id",), "/dank setup-verify"),
    ("Verified role", ("verified_role_id",), "/dank setup-verify"),
)

_RECOMMENDED_DB_FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "Archive/closed ticket category",
        (
            "ticket_archive_category_id",
            "ticket_archived_category_id",
            "archived_ticket_category_id",
            "archive_ticket_category_id",
            "closed_ticket_category_id",
            "closed_tickets_category_id",
        ),
        "/dank setup-tickets",
    ),
    ("Transcript channel", ("transcripts_channel_id", "transcript_channel_id"), "/dank setup-tickets"),
    ("Modlog channel", ("modlog_channel_id", "mod_log_channel_id"), "/dank setup-logs"),
    (
        "Join/exit log channel",
        (
            "join_log_channel_id",
            "join_log_id",
            "member_log_channel_id",
            "member_join_log_channel_id",
            "member_leave_log_channel_id",
            "welcome_exit_channel_id",
            "welcome_channel_id",
            "leave_log_channel_id",
        ),
        "/dank setup-logs",
    ),
)


def _env_str(name: str, default: str = "") -> str:
    try:
        value = os.getenv(name)
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = _env_str(name, "")
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _masked_secret_state(value: str) -> str:
    if not value:
        return "missing"
    if len(value) < 16:
        return f"present-but-too-short(len={len(value)})"
    return f"present(len={len(value)})"


def _deployment_mode() -> str:
    raw = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_bool("STONEY_PRODUCTION_MODE", False):
        return "production"
    if _env_bool("STONEY_PUBLIC_MODE", False):
        return "public"
    return "development"


def _command_profile() -> str:
    return _env_str("STONEY_COMMAND_PROFILE", "public").lower() or "public"


def _config_table_name() -> str:
    return _env_str("STONEY_GUILD_CONFIG_TABLE", "guild_configs") or "guild_configs"


def _tree_counts_for_guild(guild_id: int) -> tuple[int, int]:
    tree = _TREE
    global_count = 0
    guild_count = 0
    if tree is None:
        return 0, 0

    try:
        global_count = len(list(tree.get_commands(guild=None) or []))
    except Exception:
        global_count = 0

    try:
        guild_obj = discord.Object(id=int(guild_id))
        guild_count = len(list(tree.get_commands(guild=guild_obj) or []))
    except Exception:
        guild_count = 0

    return int(global_count), int(guild_count)


def _raw_value_present(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in {"0", "none", "null", "false"}


def _fetch_raw_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return None
    response = (
        sb.table(_config_table_name())
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return dict(row) if isinstance(row, Mapping) else None


def _merged_raw_config(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        for key, value in row.items():
            if key not in _JSON_CONFIG_KEYS and value is not None:
                merged[str(key)] = value
        for key in ("settings", "config", "metadata", "meta"):
            value = row.get(key)
            if isinstance(value, Mapping):
                for nested_key, nested_value in value.items():
                    if nested_value is not None:
                        merged[str(nested_key)] = nested_value
    except Exception:
        return {}
    return merged


def _has_raw_any(data: Mapping[str, Any], aliases: tuple[str, ...]) -> bool:
    try:
        for key in aliases:
            if _raw_value_present(data.get(key)):
                return True
    except Exception:
        return False
    return False


async def _raw_db_config_checks(guild_id: int) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    try:
        row = await asyncio.to_thread(_fetch_raw_config_row_sync, int(guild_id))
    except Exception as e:
        blockers.append(f"Could not read raw guild config row from `{_config_table_name()}`: `{repr(e)[:220]}`")
        return blockers, warnings, ok

    if not row:
        blockers.append(f"No raw guild config row exists in `{_config_table_name()}` for this server. Run `/dank setup-picker` or `/dank setup-tickets` first.")
        return blockers, warnings, ok

    data = _merged_raw_config(row)
    if not data:
        blockers.append("Raw guild config row exists, but no usable config payload could be read from flat columns/settings/config.")
        return blockers, warnings, ok

    for label, aliases, command_name in _REQUIRED_DB_FIELDS:
        if _has_raw_any(data, aliases):
            ok.append(f"Raw DB config contains {label}.")
        else:
            blockers.append(f"Raw DB config is missing {label}. Run `{command_name}` so production does not depend on old env fallback IDs.")

    for label, aliases, command_name in _RECOMMENDED_DB_FIELDS:
        if _has_raw_any(data, aliases):
            ok.append(f"Raw DB config contains {label}.")
        else:
            warnings.append(f"Raw DB config is missing {label}. Run `{command_name}` before full public rollout.")

    return blockers, warnings, ok


def _runtime_checks(guild: discord.Guild, cfg: Any) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    profile = _command_profile()
    deployment = _deployment_mode()
    source = _safe_str(getattr(cfg, "source", ""), "unknown")

    if profile not in {"public", "minimal"}:
        blockers.append(f"Command profile is `{profile}`. Public launch should use `public` or `minimal`.")
    else:
        ok.append(f"Command profile is public-safe: `{profile}`.")

    if deployment not in {"public", "prod", "production"}:
        warnings.append(f"Deployment mode is `{deployment}`. For public launch use `STONEY_DEPLOYMENT_MODE=public` or `production`.")
    else:
        ok.append(f"Deployment mode is public-safe: `{deployment}`.")

    if not source.startswith("supabase:"):
        blockers.append("This server is not using a Supabase guild_configs row. Run setup before launch so it cannot inherit env fallback values.")
    else:
        ok.append("Server config source is per-guild Supabase config.")

    global_count, guild_count = _tree_counts_for_guild(int(guild.id))
    if global_count:
        ok.append(f"Local global command surface has `{global_count}` command(s).")
    if global_count >= 95:
        warnings.append(f"Global command count is high: `{global_count}/100`.")

    sync_beta = _env_bool("STONEY_SYNC_BETA_GUILD_COMMANDS", False)
    clear_beta = _env_bool("STONEY_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT", True)
    if sync_beta:
        warnings.append("`STONEY_SYNC_BETA_GUILD_COMMANDS=true` can cause duplicate commands beside global commands. Keep it false for public launch.")
    else:
        ok.append("Beta guild command sync is disabled, so public/global commands stay clean.")

    if guild_count > 0 and not sync_beta:
        warnings.append(f"Local tree still has `{guild_count}` guild command(s) for this server. Restart once with stale guild command clearing enabled if duplicates remain.")
    elif guild_count == 0:
        ok.append("No local guild-scoped command copies are registered for this server.")

    if not clear_beta and _env_str("GUILD_ID", ""):
        warnings.append("`STONEY_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT=false`. That is fine after cleanup, but turn it on once if duplicate guild commands return.")

    require_auth = _env_bool("BOT_API_REQUIRE_AUTH", True)
    allow_insecure = _env_bool("BOT_API_ALLOW_INSECURE", False)
    bind_host = _env_str("BOT_API_BIND_HOST", "127.0.0.1")
    shared_secret = _env_str("BOT_API_SHARED_SECRET", "")

    if not require_auth:
        blockers.append("Structured Bot API auth is disabled: `BOT_API_REQUIRE_AUTH=false`.")
    else:
        ok.append("Structured Bot API requires authentication.")

    if allow_insecure:
        blockers.append("`BOT_API_ALLOW_INSECURE=true` is local-dev only. Disable it before public launch.")
    else:
        ok.append("Insecure API bypass is disabled.")

    if bind_host in {"0.0.0.0", "::"} and not require_auth:
        blockers.append("Bot API is public-facing while auth is disabled.")
    else:
        ok.append(f"Bot API bind/auth combination is acceptable: `{bind_host}`.")

    if require_auth and len(shared_secret) < 32:
        warnings.append(f"`BOT_API_SHARED_SECRET` should be at least 32 random characters ({_masked_secret_state(shared_secret)}).")
    elif require_auth:
        ok.append("Bot API shared secret length looks production-safe.")

    expected_guilds = _env_int("STONEY_EXPECTED_PUBLIC_GUILDS", 1)
    auto_shard = _env_bool("DISCORD_AUTO_SHARD", False)
    if expected_guilds >= 100 and not auto_shard:
        warnings.append("Expected public guild count is 100+, but `DISCORD_AUTO_SHARD` is not enabled yet.")
    elif auto_shard:
        ok.append("Auto-sharding is enabled.")
    else:
        ok.append("Current expected guild count does not require auto-sharding yet.")

    if _env_str("GUILD_ID", ""):
        warnings.append("`GUILD_ID` is still set. This is okay for beta, but production behavior must keep using per-guild DB config.")

    return blockers, warnings, ok


def _overall_status(blockers: list[str], warnings: list[str]) -> tuple[str, discord.Color, str]:
    if blockers:
        return "blocked", discord.Color.red(), "🚫 **Not ready for public launch.** Fix blockers first."
    if warnings:
        return "warnings", discord.Color.gold(), "⚠️ **Operational, but not polished enough for public launch yet.** Review warnings."
    return "ready", discord.Color.green(), "✅ **Launch-ready.** No blockers or warnings were found."


def _launch_embed(guild: discord.Guild, cfg: Any, setup_blockers: list[str], setup_warnings: list[str], setup_ok: list[str], runtime_blockers: list[str], runtime_warnings: list[str], runtime_ok: list[str]) -> discord.Embed:
    blockers = list(setup_blockers) + list(runtime_blockers)
    warnings = list(setup_warnings) + list(runtime_warnings)
    ok = list(setup_ok) + list(runtime_ok)
    status, color, description = _overall_status(blockers, warnings)

    embed = discord.Embed(
        title="🚀 Stoney Launch Check",
        description=(
            f"{description}\n\n"
            f"Status: `{status}`\n"
            f"Guild: `{guild.id}`\n"
            f"Config source: `{_safe_str(getattr(cfg, 'source', 'unknown'), 'unknown')}`"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported.", limit=1000), inline=False)
    embed.add_field(
        name="Next step",
        value=(
            "Fix blockers, then run `/dank launch-check` again."
            if blockers
            else "Review warnings, then run a live ticket + modlog test before public invite rollout."
            if warnings
            else "Run one live ticket test and one harmless mod action test, then you are ready for controlled beta/public rollout."
        ),
        inline=False,
    )
    embed.set_footer(text="Read-only launch check. No config was changed.")
    return embed


async def _launch_check_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        setup_blockers, setup_warnings, setup_ok = _build_setup_health(guild, cfg)
        runtime_blockers, runtime_warnings, runtime_ok = _runtime_checks(guild, cfg)
        raw_blockers, raw_warnings, raw_ok = await _raw_db_config_checks(guild.id)
        runtime_blockers.extend(raw_blockers)
        runtime_warnings.extend(raw_warnings)
        runtime_ok.extend(raw_ok)
        await interaction.followup.send(
            embeds=[
                _launch_embed(guild, cfg, setup_blockers, setup_warnings, setup_ok, runtime_blockers, runtime_warnings, runtime_ok),
                _config_embed(guild, cfg, title="📌 Launch Check Config Snapshot"),
            ],
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Launch check failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach_launch_check_command() -> None:
    global _LAUNCH_CHECK_ATTACHED
    if _LAUNCH_CHECK_ATTACHED:
        return

    try:
        existing = stoney_group.get_command("launch-check")
    except Exception:
        existing = None

    if existing is not None:
        _LAUNCH_CHECK_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="launch-check",
        description="Run a production launch checklist for setup, isolation, commands, API, and scaling.",
        callback=_launch_check_callback,
    )
    stoney_group.add_command(command)
    _LAUNCH_CHECK_ATTACHED = True


_attach_launch_check_command()


def register_public_launch_check_commands(bot: Any, tree: Any) -> None:
    global _TREE
    _ = bot
    _TREE = tree
    _attach_launch_check_command()
    try:
        print("✅ public_launch_check: attached /dank launch-check production readiness command")
    except Exception:
        pass


__all__ = ["register_public_launch_check_commands"]
