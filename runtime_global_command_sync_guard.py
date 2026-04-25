from __future__ import annotations

"""
Runtime global command sync guard.

Imported before stoney_verify.app.

This guard prevents accidentally syncing a huge slash-command surface globally.
For a 500-1000+ server public bot, global commands must be intentionally small
and stable. Admin-heavy commands should be consolidated or guild-scoped.

Behavior:
- Guild syncs are allowed.
- Global sync of zero commands is allowed, so CLEAR_GLOBAL_COMMANDS_ON_BOOT can
  still clear globals.
- Global sync above STONEY_GLOBAL_COMMAND_SYNC_LIMIT is blocked unless
  STONEY_ALLOW_LARGE_GLOBAL_COMMAND_SYNC=true.

This does not add slash commands.
"""

import os
from typing import Any

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🌍 runtime_global_command_sync_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_global_command_sync_guard {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = str(os.getenv(name, "") or "").strip().lower()
        if not raw:
            return bool(default)
        return raw in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _global_command_count(tree: Any) -> int:
    try:
        return len(list(tree.get_commands(guild=None) or []))
    except Exception:
        pass

    try:
        commands = getattr(tree, "_global_commands", {}) or {}
        if isinstance(commands, dict):
            return len(commands)
    except Exception:
        pass

    return 0


def _sync_is_global(kwargs: dict[str, Any]) -> bool:
    try:
        return kwargs.get("guild") is None
    except Exception:
        return True


def install_global_command_sync_guard() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        from discord import app_commands
    except Exception as e:
        _warn(f"discord import failed; global sync guard inactive: {e!r}")
        return

    try:
        original_sync = app_commands.CommandTree.sync
    except Exception as e:
        _warn(f"could not access CommandTree.sync; global sync guard inactive: {e!r}")
        return

    if getattr(original_sync, "_runtime_global_command_sync_guard_wrapped", False):
        _PATCHED = True
        return

    async def _guarded_sync(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _sync_is_global(kwargs):
            count = _global_command_count(self)
            limit = max(1, _env_int("STONEY_GLOBAL_COMMAND_SYNC_LIMIT", 25))
            allow_large = _env_bool("STONEY_ALLOW_LARGE_GLOBAL_COMMAND_SYNC", False)

            if count > limit and not allow_large:
                _warn(
                    "blocked large global slash sync "
                    f"commands={count} limit={limit}. "
                    "Set STONEY_ALLOW_LARGE_GLOBAL_COMMAND_SYNC=true only after command consolidation."
                )
                return []

            _log(f"allowing global slash sync commands={count} limit={limit} allow_large={allow_large}")

        return await original_sync(self, *args, **kwargs)

    try:
        setattr(_guarded_sync, "_runtime_global_command_sync_guard_wrapped", True)
        setattr(_guarded_sync, "_runtime_global_command_sync_guard_original", original_sync)
    except Exception:
        pass

    app_commands.CommandTree.sync = _guarded_sync
    _PATCHED = True
    _log("loaded; large global slash sync guard active")


install_global_command_sync_guard()


__all__ = ["install_global_command_sync_guard"]
