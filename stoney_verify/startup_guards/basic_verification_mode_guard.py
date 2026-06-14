from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

_READY = False
_ORIGINAL_SYNC = None


def _log(message: str) -> None:
    try:
        print(f"basic_verification_mode_guard: {message}")
    except Exception:
        pass


def _install_basic_button_listener() -> bool:
    try:
        from stoney_verify import interaction_handlers as handlers
        from stoney_verify.verification_new.basic_verify import maybe_handle_basic_verify_interaction
    except Exception:
        return False
    original = getattr(handlers, "handle_component_interaction", None)
    if not callable(original) or getattr(original, "_basic_verify_ready", False):
        return bool(callable(original))

    async def wrapped_component_handler(interaction: discord.Interaction) -> None:
        try:
            if await maybe_handle_basic_verify_interaction(interaction):
                return
        except Exception:
            pass
        return await original(interaction)

    setattr(wrapped_component_handler, "_basic_verify_ready", True)
    handlers.handle_component_interaction = wrapped_component_handler  # type: ignore[assignment]
    return True


def _install_panel_command() -> bool:
    try:
        from stoney_verify.commands_ext import public_verify_basic_panel
        install = getattr(public_verify_basic_panel, "apply", None)
        return bool(install()) if callable(install) else False
    except Exception:
        return False


def _install_sync_hook() -> bool:
    global _ORIGINAL_SYNC
    if getattr(app_commands.CommandTree.sync, "_verify_panel_ready", False):
        return True
    try:
        _ORIGINAL_SYNC = app_commands.CommandTree.sync

        async def sync_with_panel(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
            _install_panel_command()
            return await _ORIGINAL_SYNC(self, *args, **kwargs)  # type: ignore[misc]

        setattr(sync_with_panel, "_verify_panel_ready", True)
        app_commands.CommandTree.sync = sync_with_panel  # type: ignore[assignment]
        return True
    except Exception:
        return False


def _install_legacy_allowlist() -> bool:
    try:
        from stoney_verify.startup_guards import id_verify_allowlist_guard
        install = getattr(id_verify_allowlist_guard, "apply", None)
        return bool(install()) if callable(install) else False
    except Exception:
        return False


def apply() -> bool:
    global _READY
    if _READY:
        _install_panel_command()
        return True
    a = _install_legacy_allowlist()
    b = _install_basic_button_listener()
    c = _install_panel_command()
    d = _install_sync_hook()
    _READY = bool(a or b or c or d)
    if _READY:
        _log(f"active allowlist={a} listener={b} panel={c} sync={d}")
    return _READY


apply()

__all__ = ["apply"]
