from __future__ import annotations

"""
Runtime command registration safety guard.

Imported by main.py before stoney_verify.app.

Why this exists:
- Discord has a hard cap of 100 global slash commands.
- One accidental command registration past that limit can crash startup with
  discord.app_commands.errors.CommandLimitReached.
- A public/multi-server bot should keep booting even if an optional command is
  skipped.
- The bot is already at the 100-command ceiling, so we need command-budget logs
  during startup to guide consolidation.

This does not add or rename commands. It only prevents command-limit exceptions
from killing the process and reports command budget pressure.
"""

import asyncio
import time
from typing import Any

_PATCHED = False
_SKIPPED_COMMANDS: list[dict[str, Any]] = []
_LAST_BUDGET_LOG_MONOTONIC = 0.0
_WARNED_AT_COUNTS: set[int] = set()

GLOBAL_COMMAND_LIMIT = 100
WARN_AT = 90


def _log(message: str) -> None:
    try:
        print(f"🧷 runtime_command_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_command_safety {message}")
    except Exception:
        pass


def skipped_command_registrations() -> list[dict[str, Any]]:
    return list(_SKIPPED_COMMANDS)


def _command_name(command: Any) -> str:
    try:
        return str(getattr(command, "name", "") or getattr(command, "qualified_name", "") or repr(command))
    except Exception:
        return "unknown"


def _is_global_scope(kwargs: dict[str, Any]) -> bool:
    try:
        return kwargs.get("guild") is None and not kwargs.get("guilds")
    except Exception:
        return True


def _global_commands(tree: Any) -> list[Any]:
    try:
        commands = tree.get_commands(guild=None)
        return list(commands or [])
    except Exception:
        pass

    try:
        commands = getattr(tree, "_global_commands", {}) or {}
        if isinstance(commands, dict):
            return list(commands.values())
    except Exception:
        pass

    return []


def _guild_command_count(tree: Any) -> int:
    total = 0
    try:
        guild_commands = getattr(tree, "_guild_commands", {}) or {}
        if isinstance(guild_commands, dict):
            for value in guild_commands.values():
                try:
                    total += len(value or {})
                except Exception:
                    pass
    except Exception:
        pass
    return int(total)


def command_budget_snapshot(tree: Any) -> dict[str, Any]:
    global_commands = _global_commands(tree)
    names = [_command_name(command) for command in global_commands]
    count = len(names)
    return {
        "global_count": count,
        "global_limit": GLOBAL_COMMAND_LIMIT,
        "global_remaining": max(0, GLOBAL_COMMAND_LIMIT - count),
        "guild_command_count": _guild_command_count(tree),
        "skipped_count": len(_SKIPPED_COMMANDS),
        "skipped": list(_SKIPPED_COMMANDS),
        "global_names": names,
    }


def _log_command_budget(tree: Any, *, force: bool = False, reason: str = "") -> None:
    global _LAST_BUDGET_LOG_MONOTONIC

    now = time.monotonic()
    if not force and (now - _LAST_BUDGET_LOG_MONOTONIC) < 10.0:
        return

    snapshot = command_budget_snapshot(tree)
    count = int(snapshot.get("global_count", 0) or 0)
    remaining = int(snapshot.get("global_remaining", 0) or 0)
    skipped = int(snapshot.get("skipped_count", 0) or 0)

    if force or count >= WARN_AT or skipped > 0:
        level = "⚠️" if count >= WARN_AT or skipped > 0 else "🧷"
        try:
            print(
                f"{level} runtime_command_safety command budget "
                f"global={count}/{GLOBAL_COMMAND_LIMIT} remaining={remaining} "
                f"guild_registered={snapshot.get('guild_command_count', 0)} "
                f"skipped={skipped} reason={reason or 'budget_check'}"
            )
        except Exception:
            pass
        _LAST_BUDGET_LOG_MONOTONIC = now


def _maybe_warn_after_add(tree: Any, *, command_name: str, global_scope: bool) -> None:
    if not global_scope:
        return

    snapshot = command_budget_snapshot(tree)
    count = int(snapshot.get("global_count", 0) or 0)
    remaining = int(snapshot.get("global_remaining", 0) or 0)

    if count >= WARN_AT and count not in _WARNED_AT_COUNTS:
        _WARNED_AT_COUNTS.add(count)
        _warn(
            f"global command budget high after registering {command_name!r}: "
            f"{count}/{GLOBAL_COMMAND_LIMIT}, remaining={remaining}. "
            "Consolidate commands before adding more."
        )


def _install_budget_logger_on_sync(app_commands: Any) -> None:
    try:
        original_sync = app_commands.CommandTree.sync
    except Exception:
        return

    if getattr(original_sync, "_runtime_command_budget_wrapped", False):
        return

    async def _safe_sync(self: Any, *args: Any, **kwargs: Any) -> Any:
        _log_command_budget(self, force=True, reason="before_sync")
        try:
            return await original_sync(self, *args, **kwargs)
        finally:
            _log_command_budget(self, force=True, reason="after_sync")

    try:
        setattr(_safe_sync, "_runtime_command_budget_wrapped", True)
        setattr(_safe_sync, "_runtime_command_budget_original", original_sync)
    except Exception:
        pass

    app_commands.CommandTree.sync = _safe_sync


def install_command_limit_guard() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        from discord import app_commands
    except Exception as e:
        _warn(f"discord import failed; command guard inactive: {e!r}")
        return

    try:
        original_add_command = app_commands.CommandTree.add_command
    except Exception as e:
        _warn(f"could not access CommandTree.add_command; command guard inactive: {e!r}")
        return

    if getattr(original_add_command, "_runtime_command_safety_wrapped", False):
        _PATCHED = True
        _install_budget_logger_on_sync(app_commands)
        return

    def _safe_add_command(self: Any, command: Any, *args: Any, **kwargs: Any) -> Any:
        name = _command_name(command)
        global_scope = _is_global_scope(kwargs)

        try:
            result = original_add_command(self, command, *args, **kwargs)
            _maybe_warn_after_add(self, command_name=name, global_scope=global_scope)
            return result
        except app_commands.errors.CommandLimitReached as e:
            guild = kwargs.get("guild")
            guilds = kwargs.get("guilds")
            guild_id = None
            try:
                guild_id = getattr(guild, "id", None)
            except Exception:
                guild_id = None

            scope = "global"
            if guild_id:
                scope = f"guild:{guild_id}"
            elif guilds:
                scope = "guilds"

            item = {
                "name": name,
                "scope": scope,
                "error": repr(e),
            }
            _SKIPPED_COMMANDS.append(item)

            _warn(
                f"skipped slash command registration name={name!r} scope={scope} "
                f"reason=CommandLimitReached; bot will continue booting"
            )
            _log_command_budget(self, force=True, reason="command_limit_reached")
            return None

    try:
        setattr(_safe_add_command, "_runtime_command_safety_wrapped", True)
        setattr(_safe_add_command, "_runtime_command_safety_original", original_add_command)
    except Exception:
        pass

    app_commands.CommandTree.add_command = _safe_add_command
    _install_budget_logger_on_sync(app_commands)
    _PATCHED = True
    _log("loaded; CommandLimitReached startup crash guard + command budget logger active")


install_command_limit_guard()


__all__ = [
    "install_command_limit_guard",
    "skipped_command_registrations",
    "command_budget_snapshot",
]
