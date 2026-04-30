from __future__ import annotations

"""
Public production readiness audit.

This command is intentionally stricter than /stoney launch-check. Launch-check
answers "can this guild function right now?" Production-audit answers "is this
bot safe, understandable, isolated, and predictable enough to invite into public
servers and eventually sell plans?"

It is read-only and never changes server config.
"""

import os
import sys
from typing import Any, Iterable, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _require_setup_permission,
    _safe_int,
    _safe_str,
    stoney_group,
)
from ..guild_config import get_guild_config, guild_config_cache_snapshot, public_config_isolation_enabled
from ..globals import get_supabase


_ATTACHED = False
_TREE: Any = None

_RUNTIME_PATCH_MODULES: tuple[str, ...] = (
    "runtime_process_health_guard",
    "runtime_command_safety",
    "runtime_raidguard_hard_stop",
    "runtime_raidguard_bot_heuristics_patch",
    "runtime_raidguard_risk_engine_v2_patch",
    "runtime_alt_identity_link_safety_patch",
    "runtime_member_join_kick_safety_patch",
    "runtime_guild_members_role_state_compat_patch",
    "runtime_setup_role_safety_patch",
    "runtime_public_ticket_panel_command_patch",
    "runtime_public_mod_ban_toggle_startup_patch",
    "runtime_guild_config_ticket_patch",
    "runtime_ticket_creation_native_category_patch",
    "runtime_ticket_category_enforcer_patch",
    "runtime_ticket_sync_alias_patch",
    "runtime_public_startup_scope_patch",
    "runtime_api_guild_config_patch",
    "runtime_event_safety",
    "runtime_job_dedupe_safety",
)

_CRITICAL_NATIVE_CLEANUP_TARGETS: tuple[str, ...] = (
    "tickets_new/service.py",
    "tickets_new/panel.py",
    "commands_ext/kick_timers.py",
    "events.py",
    "raidguard.py",
    "api_new/server.py",
    "modlog.py",
)

_REQUIRED_PUBLIC_ENV: tuple[str, ...] = (
    "STONEY_DEPLOYMENT_MODE",
    "STONEY_COMMAND_PROFILE",
    "BOT_API_REQUIRE_AUTH",
    "BOT_API_SHARED_SECRET",
)

_REQUIRED_GLOBAL_COMMANDS: tuple[str, ...] = (
    "stoney",
    "mod",
    "ticket",
    "tickets",
    "ticket-intake",
    "ticket-category",
    "ticket-panel",
)

_REQUIRED_GROUP_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("ticket-intake", "post-panel"),
    ("ticket-intake", "post-actions"),
    ("ticket", "close"),
    ("ticket", "reopen"),
    ("ticket", "claim"),
    ("ticket", "transfer"),
    ("ticket", "transcript"),
    ("ticket", "delete"),
    ("ticket-category", "list"),
    ("ticket-category", "update"),
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
        raw = _env_str(name, "")
        if not raw:
            return bool(default)
        return raw.lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _masked_secret_state(value: str) -> str:
    if not value:
        return "missing"
    if len(value) < 16:
        return f"present-but-too-short(len={len(value)})"
    if len(value) < 32:
        return f"present-but-weak(len={len(value)})"
    return f"present(len={len(value)})"


def _tree_command_counts() -> tuple[int, int]:
    global_count = 0
    guild_count = 0
    tree = _TREE
    if tree is None:
        return 0, 0

    try:
        global_count = len(list(tree.get_commands(guild=None) or []))
    except Exception:
        global_count = 0

    try:
        guild_commands = getattr(tree, "_guild_commands", {}) or {}
        if isinstance(guild_commands, dict):
            for value in guild_commands.values():
                try:
                    guild_count += len(value or {})
                except Exception:
                    pass
    except Exception:
        guild_count = 0

    return int(global_count), int(guild_count)


def _tree_global_command(name: str) -> Any:
    tree = _TREE
    if tree is None:
        return None
    try:
        return tree.get_command(name, guild=None)
    except Exception:
        return None


def _group_has_subcommand(group_name: str, subcommand_name: str) -> bool:
    group = _tree_global_command(group_name)
    if group is None:
        return False
    try:
        return group.get_command(subcommand_name) is not None
    except Exception:
        try:
            return any(getattr(cmd, "name", None) == subcommand_name for cmd in (getattr(group, "commands", None) or []))
        except Exception:
            return False


def _channel_label(guild: discord.Guild, channel_id: int) -> str:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return "not set"
    channel = guild.get_channel(cid)
    if channel is None:
        return f"missing `{cid}`"
    mention = getattr(channel, "mention", None)
    return f"{mention or channel.name} (`{cid}`)"


def _role_label(guild: discord.Guild, role_id: int) -> str:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return "not set"
    role = guild.get_role(rid)
    if role is None:
        return f"missing `{rid}`"
    return f"{role.mention} (`{rid}`)"


def _text_channel_writable(guild: discord.Guild, channel_id: int, *, embeds: bool = True, files: bool = False) -> bool:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return False
    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.read_message_history):
            return False
        if embeds and not perms.embed_links:
            return False
        if files and not perms.attach_files:
            return False
        return True
    except Exception:
        return False


def _category_usable(guild: discord.Guild, category_id: int) -> bool:
    cid = _safe_int(category_id, 0)
    if cid <= 0:
        return False
    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.CategoryChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.manage_channels)
    except Exception:
        return False


def _bot_can_manage_role(guild: discord.Guild, role_id: int) -> bool:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return False
    role = guild.get_role(rid)
    me = guild.me
    if role is None or me is None:
        return False
    try:
        return bool(me.guild_permissions.manage_roles and role < me.top_role)
    except Exception:
        return False


def _append_env_audit(blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    profile = _env_str("STONEY_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    require_auth = _env_bool("BOT_API_REQUIRE_AUTH", True)
    allow_insecure = _env_bool("BOT_API_ALLOW_INSECURE", False)
    shared_secret = _env_str("BOT_API_SHARED_SECRET", "")
    bot_fresh_join_removal = _env_bool("ALLOW_BOT_FRESH_JOIN_REMOVAL", False)

    if profile not in {"public", "minimal"}:
        blockers.append(f"Command profile is `{profile}`. Public production must use `public` or `minimal`.")
    else:
        ok.append(f"Command profile is public-safe: `{profile}`.")

    if deployment not in {"public", "prod", "production"}:
        warnings.append("`STONEY_DEPLOYMENT_MODE` is not public/production. That is okay for dev, not okay for public rollout.")
    else:
        ok.append(f"Deployment mode is public-safe: `{deployment}`.")

    if not require_auth:
        blockers.append("Structured API auth is disabled. `BOT_API_REQUIRE_AUTH` must stay true for public use.")
    else:
        ok.append("Structured API auth is required.")

    if allow_insecure:
        blockers.append("`BOT_API_ALLOW_INSECURE=true` is local-dev only and must be false in production.")
    else:
        ok.append("Insecure API bypass is disabled.")

    if len(shared_secret) < 32:
        blockers.append(f"`BOT_API_SHARED_SECRET` is not production-strength: {_masked_secret_state(shared_secret)}.")
    else:
        ok.append("Bot API shared secret length looks production-safe.")

    if bot_fresh_join_removal:
        blockers.append("`ALLOW_BOT_FRESH_JOIN_REMOVAL=true` allows instant bot kick/ban on fresh joins. Keep this false for public/listing-site traffic.")
    else:
        ok.append("Fresh joins are protected from instant bot kick/ban by default.")

    missing = [name for name in _REQUIRED_PUBLIC_ENV if not _env_str(name, "")]
    if missing:
        warnings.append("Important public env values are missing/unset: " + ", ".join(f"`{x}`" for x in missing) + ".")


def _append_patch_audit(warnings: list[str], ok: list[str]) -> None:
    loaded = [name for name in _RUNTIME_PATCH_MODULES if name in sys.modules]
    if loaded:
        warnings.append(
            f"Runtime patch stack is still active: `{len(loaded)}` patch module(s). This is beta-safe but not clean public architecture."
        )
        warnings.append(
            "Native cleanup still needed: " + ", ".join(f"`{x}`" for x in _CRITICAL_NATIVE_CLEANUP_TARGETS) + "."
        )
    else:
        ok.append("No known runtime patch modules are loaded.")


def _append_command_audit(blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    global_count, guild_count = _tree_command_counts()
    ok.append(f"Command count snapshot: global `{global_count}`, guild-scoped `{guild_count}`.")

    if _TREE is None:
        blockers.append("Production audit cannot inspect the live slash command tree.")
        return

    missing_global = [name for name in _REQUIRED_GLOBAL_COMMANDS if _tree_global_command(name) is None]
    if missing_global:
        blockers.append("Missing required public slash commands: " + ", ".join(f"`/{name}`" for name in missing_global) + ".")
    else:
        ok.append("Required public slash commands are present, including `/ticket-panel`.")

    missing_sub = [(group, sub) for group, sub in _REQUIRED_GROUP_SUBCOMMANDS if not _group_has_subcommand(group, sub)]
    if missing_sub:
        blockers.append(
            "Missing required public subcommands: "
            + ", ".join(f"`/{group} {sub}`" for group, sub in missing_sub)
            + "."
        )
    else:
        ok.append("Required ticket workflow subcommands are present, including `/ticket-intake post-panel` and `/ticket-intake post-actions`.")

    if global_count >= 90:
        warnings.append(f"Global command count is high: `{global_count}/100`. Public bots should keep this lower and cleaner.")
    if guild_count > 0:
        warnings.append(f"Local tree still has `{guild_count}` guild-scoped command(s). Duplicate/stale commands can confuse admins.")


def _append_config_audit(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
    if not source.startswith("supabase:"):
        blockers.append(f"This guild is not using per-guild Supabase config. Current source: `{source}`.")
    else:
        ok.append("This guild is using per-guild Supabase config.")

    if public_config_isolation_enabled():
        ok.append("Public config isolation is enabled, so other servers should not inherit private/beta server IDs.")
    else:
        blockers.append("Public config isolation is disabled. Other servers could inherit fallback env IDs.")

    if _category_usable(guild, getattr(cfg, "ticket_category_id", 0)):
        ok.append(f"Open ticket category is usable: {_channel_label(guild, getattr(cfg, 'ticket_category_id', 0))}.")
    else:
        blockers.append("Open ticket category is missing/unusable. Ticket creation cannot be trusted.")

    if _category_usable(guild, getattr(cfg, "ticket_archive_category_id", 0)):
        ok.append(f"Archive category is usable: {_channel_label(guild, getattr(cfg, 'ticket_archive_category_id', 0))}.")
    else:
        warnings.append("Archive ticket category is missing/unusable. Closed ticket organization is weaker than TicketTool.")

    if _text_channel_writable(guild, getattr(cfg, "transcripts_channel_id", 0), embeds=True, files=True):
        ok.append(f"Transcript channel is writable and supports files: {_channel_label(guild, getattr(cfg, 'transcripts_channel_id', 0))}.")
    else:
        warnings.append("Transcript channel is missing or lacks Send/Embed/Attach permissions.")

    if _text_channel_writable(guild, getattr(cfg, "modlog_channel_id", 0), embeds=True):
        ok.append(f"Staff modlog channel is writable: {_channel_label(guild, getattr(cfg, 'modlog_channel_id', 0))}.")
    else:
        warnings.append("Staff modlog channel is missing/not writable. Staff-only evidence may be lost.")

    join_log_id = getattr(cfg, "join_log_channel_id", 0)
    if _text_channel_writable(guild, join_log_id, embeds=True):
        ok.append(f"Join/exit channel is writable: {_channel_label(guild, join_log_id)}.")
    else:
        warnings.append("Join/exit channel is missing/not writable. Public member lifecycle cards may fail.")

    if _bot_can_manage_role(guild, getattr(cfg, "unverified_role_id", 0)):
        ok.append(f"Bot can manage Unverified role: {_role_label(guild, getattr(cfg, 'unverified_role_id', 0))}.")
    else:
        warnings.append("Bot cannot manage the Unverified role or it is missing.")

    if _bot_can_manage_role(guild, getattr(cfg, "verified_role_id", 0)):
        ok.append(f"Bot can manage Verified role: {_role_label(guild, getattr(cfg, 'verified_role_id', 0))}.")
    else:
        warnings.append("Bot cannot manage the Verified role or it is missing.")

    if _safe_int(getattr(cfg, "staff_role_id", 0), 0) > 0:
        ok.append(f"Ticket staff role is configured: {_role_label(guild, getattr(cfg, 'staff_role_id', 0))}.")
    else:
        blockers.append("Ticket staff role is not configured.")


def _append_bot_permission_audit(guild: discord.Guild, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    me = guild.me
    if me is None:
        blockers.append("Bot member object is unavailable in this guild.")
        return

    perms = me.guild_permissions
    required = {
        "view_audit_log": "View Audit Log",
        "manage_channels": "Manage Channels",
        "manage_roles": "Manage Roles",
        "view_channel": "View Channels",
        "send_messages": "Send Messages",
        "read_message_history": "Read Message History",
        "embed_links": "Embed Links",
        "attach_files": "Attach Files",
    }
    missing_required = [label for attr, label in required.items() if not bool(getattr(perms, attr, False))]
    if missing_required:
        blockers.append("Bot is missing required production permissions: " + ", ".join(missing_required) + ".")
    else:
        ok.append("Bot has required baseline permissions for tickets/logging/setup.")

    recommended = {
        "moderate_members": "Moderate Members",
        "kick_members": "Kick Members",
        "ban_members": "Ban Members",
        "manage_nicknames": "Manage Nicknames",
        "move_members": "Move Members",
        "connect": "Connect",
        "speak": "Speak",
    }
    missing_recommended = [label for attr, label in recommended.items() if not bool(getattr(perms, attr, False))]
    if missing_recommended:
        warnings.append("Missing recommended permissions for full feature coverage: " + ", ".join(missing_recommended) + ".")
    else:
        ok.append("Bot has recommended moderation/VC/nickname permissions.")


def _append_db_audit(blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    try:
        sb = get_supabase()
    except Exception:
        sb = None
    if sb is None:
        blockers.append("Supabase client is unavailable. Public production requires DB-backed per-guild config and ticket state.")
    else:
        ok.append("Supabase client is available.")

    try:
        snap = guild_config_cache_snapshot()
        ok.append(
            f"Guild config cache: table `{snap.get('table')}`, cached guilds `{snap.get('cached_guilds')}`, isolation `{snap.get('public_config_isolation')}`."
        )
    except Exception as e:
        warnings.append(f"Could not read guild config cache snapshot: `{repr(e)[:160]}`.")


def _status(blockers: list[str], warnings: list[str]) -> tuple[str, discord.Color, str]:
    if blockers:
        return "not-public-ready", discord.Color.red(), "🚫 Not ready for public production. Fix blockers first."
    if warnings:
        return "controlled-beta-only", discord.Color.gold(), "⚠️ No hard blockers, but still controlled beta only."
    return "public-ready-check-passed", discord.Color.green(), "✅ This guild passed the automated production audit. Still run live smoke tests."


def _make_embed(guild: discord.Guild, blockers: list[str], warnings: list[str], ok: list[str]) -> discord.Embed:
    status, color, description = _status(blockers, warnings)
    embed = discord.Embed(
        title="🧪 Stoney Public Production Audit",
        description=(
            f"{description}\n\n"
            f"Status: `{status}`\n"
            f"Guild: `{guild.id}`\n"
            f"Brutal mode: `on`"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported.", limit=1000), inline=False)
    embed.add_field(
        name="Required live smoke tests",
        value=(
            "`setup-defaults` / custom setup → post panels → create ticket → claim → transfer → close → transcript → reopen → delete → "
            "fresh join → voluntary leave → staff kick → staff ban → launch-check → tickettool-check."
        ),
        inline=False,
    )
    embed.set_footer(text="Read-only audit. No server config was changed.")
    return embed


async def _production_audit_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        setup_blockers, setup_warnings, setup_ok = _build_setup_health(guild, cfg)
        blockers.extend(setup_blockers)
        warnings.extend(setup_warnings)
        ok.extend(setup_ok)

        _append_config_audit(guild, cfg, blockers, warnings, ok)
        _append_bot_permission_audit(guild, blockers, warnings, ok)
        _append_env_audit(blockers, warnings, ok)
        _append_db_audit(blockers, warnings, ok)
        _append_command_audit(blockers, warnings, ok)
        _append_patch_audit(warnings, ok)

        await interaction.followup.send(embed=_make_embed(guild, blockers, warnings, ok), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Production audit failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = stoney_group.get_command("production-audit")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="production-audit",
        description="Brutally audit whether this server/bot setup is ready for public production.",
        callback=_production_audit_callback,
    )
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_production_audit_commands(bot: Any, tree: Any) -> None:
    global _TREE
    _ = bot
    _TREE = tree
    _attach()
    try:
        print("✅ public_production_audit: attached /stoney production-audit command")
    except Exception:
        pass


__all__ = ["register_public_production_audit_commands"]
