from __future__ import annotations

"""Slash command cleanup guard.

Why this exists:
- During the TicketTool-style command consolidation, old top-level commands like
  /spam_guard, /grant_vr, /ticket_panel_rules_set, etc. were replaced by grouped
  commands such as /stoney spam, /verify grant-vr, /ticket-panel rules set.
- Discord keeps previously synced global commands until the next successful sync.
- This guard strips stale aliases from the local CommandTree right before sync so
  the next successful sync removes them from Discord too.
- It also prevents the old CLEAR_GLOBAL_COMMANDS_ON_BOOT env from accidentally
  wiping the full public command surface. A dangerous emergency wipe still exists
  behind STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=true.
"""

import os
from typing import Any, Optional

from discord import app_commands


_PATCHED = False
_ORIGINAL_SYNC = None
_ORIGINAL_CLEAR_COMMANDS = None

STALE_TOP_LEVEL_COMMANDS = {
    "spam_guard",
    "spam_guard_status",
    "fix_unverified",
    "set_verified",
    "set_resident",
    "grant_vr",
    "verify_diagnose",
    "fix_unverified_member",
    "verify_status",
    "repair_verify_ui",
    "recompute_member_risk",
    "recompute_all_member_risk",
    "channel_cleanup_status",
    "run_channel_cleanup",
    "purge_channel_messages",
    "ticket_setup_status",
    "ticket_setup_discover",
    "ticket_setup_save_discovered",
    "ticket_setup_set_channel",
    "ticket_setup_set_role",
    "ticket_panel_list",
    "ticket_panel_show",
    "ticket_panel_bind_categories",
    "ticket_panel_rules",
    "ticket_panel_rules_set",
    "ticket_panel_runtime",
    "ticket_panel_bootstrap_status",
    "ticket_panel_bootstrap_run",
    "ticket_panel_bootstrap_all",
    "ticket_panel_bootstrap_start",
    "ticket_panel_bootstrap_once",
    "ticket_panel_bootstrap_stop",
}

# Public users should not see a wall of setup/debug/audit commands when they
# type /stoney. The guided setup flow is /stoney setup. Everything else here is
# an internal/admin helper that should not be part of the default public surface.
# These are removed right before global sync, so the next successful sync clears
# them from Discord's command picker.
CONFUSING_STONEY_CHILDREN = {
    "archive-backfill",
    "cache",
    "config",
    "db-check",
    "health",
    "launch-check",
    "modlog-check",
    "permission-check",
    "production-audit",
    "refresh-config",
    "setup-access",
    "setup-assistant",
    "setup-defaults",
    "setup-find",
    "setup-logs",
    "setup-picker",
    "setup-review",
    "setup-status",
    "setup-tickets",
    "setup-verify",
    "setup-verify-ids",
    "tickettool-check",
}

ALLOWED_STONEY_CHILDREN = {
    "setup",
    "help",
    "commands",
    "spam",
    "cleanup",
}


def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if not raw:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _public_scope_enabled() -> bool:
    profile = _env_str("STONEY_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_true("STONEY_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_true("STONEY_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _guild_from_sync_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[Any]:
    try:
        if "guild" in kwargs:
            return kwargs.get("guild")
        if args:
            return args[0]
    except Exception:
        pass
    return None


def _safe_command_names(tree: app_commands.CommandTree[Any]) -> list[str]:
    try:
        return [str(cmd.name) for cmd in tree.get_commands(guild=None)]
    except Exception:
        try:
            return [str(cmd.name) for cmd in tree.get_commands()]
        except Exception:
            return []


def _safe_child_names(group: Any) -> list[str]:
    try:
        return sorted(
            str(getattr(cmd, "name", ""))
            for cmd in list(getattr(group, "commands", []) or [])
            if str(getattr(cmd, "name", "")).strip()
        )
    except Exception:
        return []


def remove_stale_top_level_commands(tree: app_commands.CommandTree[Any], *, reason: str = "manual") -> list[str]:
    removed: list[str] = []

    for name in sorted(STALE_TOP_LEVEL_COMMANDS):
        try:
            existing = tree.get_command(name, guild=None)
        except Exception:
            existing = None

        if existing is None:
            continue

        try:
            tree.remove_command(name, guild=None)
            removed.append(name)
        except Exception:
            continue

    if removed:
        try:
            print(f"🧹 slash_command_cleanup removed stale top-level commands reason={reason}: {removed}")
        except Exception:
            pass

    return removed


def prune_public_stoney_children(tree: app_commands.CommandTree[Any], *, reason: str = "manual") -> list[str]:
    if not _public_scope_enabled():
        return []

    try:
        stoney = tree.get_command("stoney", guild=None)
    except Exception:
        stoney = None

    if stoney is None or not hasattr(stoney, "remove_command"):
        return []

    before = _safe_child_names(stoney)
    removed: list[str] = []

    for name in sorted(CONFUSING_STONEY_CHILDREN):
        try:
            existing = stoney.get_command(name)
        except Exception:
            existing = None
        if existing is None:
            continue
        try:
            stoney.remove_command(name)
            removed.append(name)
        except Exception:
            continue

    after = _safe_child_names(stoney)
    unexpected = [name for name in after if name not in ALLOWED_STONEY_CHILDREN]

    try:
        print(
            "🧹 slash_command_cleanup pruned /stoney public surface "
            f"reason={reason} before={before} after={after} removed={removed} unexpected_remaining={unexpected}"
        )
    except Exception:
        pass

    return removed


def _should_block_global_clear(guild: Optional[Any]) -> bool:
    if guild is not None:
        return False
    if not _public_scope_enabled():
        return False
    if not _env_true("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False):
        return False
    if _env_true("STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT", False):
        return False
    return True


def install_slash_command_cleanup_guard() -> None:
    global _PATCHED, _ORIGINAL_SYNC, _ORIGINAL_CLEAR_COMMANDS

    if _PATCHED:
        return

    _ORIGINAL_SYNC = app_commands.CommandTree.sync
    _ORIGINAL_CLEAR_COMMANDS = app_commands.CommandTree.clear_commands

    async def _patched_sync(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
        guild = _guild_from_sync_args(args, kwargs)
        if guild is None:
            remove_stale_top_level_commands(self, reason="pre_global_sync")
            prune_public_stoney_children(self, reason="pre_global_sync")
            try:
                names = _safe_command_names(self)
                print(f"🧹 slash_command_cleanup pre-sync global command count={len(names)} names={names}")
            except Exception:
                pass
        return await _ORIGINAL_SYNC(self, *args, **kwargs)  # type: ignore[misc]

    def _patched_clear_commands(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
        guild = kwargs.get("guild", None)
        if _should_block_global_clear(guild):
            try:
                print(
                    "🛑 slash_command_cleanup blocked CLEAR_GLOBAL_COMMANDS_ON_BOOT in public scope. "
                    "Use STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=true for an intentional one-time wipe."
                )
            except Exception:
                pass
            return None
        return _ORIGINAL_CLEAR_COMMANDS(self, *args, **kwargs)  # type: ignore[misc]

    app_commands.CommandTree.sync = _patched_sync  # type: ignore[assignment]
    app_commands.CommandTree.clear_commands = _patched_clear_commands  # type: ignore[assignment]

    _PATCHED = True
    try:
        print("🧹 slash_command_cleanup loaded; stale alias cleanup + public /stoney surface pruning active")
    except Exception:
        pass


install_slash_command_cleanup_guard()


__all__ = [
    "ALLOWED_STONEY_CHILDREN",
    "CONFUSING_STONEY_CHILDREN",
    "STALE_TOP_LEVEL_COMMANDS",
    "install_slash_command_cleanup_guard",
    "prune_public_stoney_children",
    "remove_stale_top_level_commands",
]
