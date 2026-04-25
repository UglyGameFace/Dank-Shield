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

This does not add or rename commands. It only prevents command-limit exceptions
from killing the process.
"""

from typing import Any

_PATCHED = False
_SKIPPED_COMMANDS: list[dict[str, Any]] = []


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


def install_command_limit_guard() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        import discord
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
        return

    def _safe_add_command(self: Any, command: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return original_add_command(self, command, *args, **kwargs)
        except app_commands.errors.CommandLimitReached as e:
            name = _command_name(command)
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
            return None

    try:
        setattr(_safe_add_command, "_runtime_command_safety_wrapped", True)
        setattr(_safe_add_command, "_runtime_command_safety_original", original_add_command)
    except Exception:
        pass

    app_commands.CommandTree.add_command = _safe_add_command
    _PATCHED = True
    _log("loaded; CommandLimitReached startup crash guard active")


install_command_limit_guard()


__all__ = ["install_command_limit_guard", "skipped_command_registrations"]
