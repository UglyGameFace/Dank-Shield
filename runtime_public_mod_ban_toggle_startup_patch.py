from __future__ import annotations

"""
Startup patch for the clear /ban_unban moderation command.

This wraps commands_ext.moderation.register_moderation_commands so the existing
moderation module can still register /mod_kick, /mod_timeout, and /debug_intents,
then removes the confusing ban command names and registers only /ban_unban.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🪄 runtime_public_mod_ban_toggle_startup {message}")
    except Exception:
        pass


def _patch_moderation_module(module: Any) -> None:
    global _PATCHED
    if _PATCHED:
        return

    original = getattr(module, "register_moderation_commands", None)
    if not callable(original) or getattr(original, "_ban_unban_wrapped", False):
        return

    def register_moderation_commands_patched(bot: Any, tree: Any) -> None:
        original(bot, tree)
        try:
            from stoney_verify.commands_ext.public_ban_unban_patch import register_public_ban_unban_patch

            register_public_ban_unban_patch(bot, tree)
        except Exception as e:
            try:
                print(f"⚠️ runtime_public_mod_ban_toggle_startup failed replacing ban command: {e!r}")
            except Exception:
                pass

    try:
        setattr(register_moderation_commands_patched, "_ban_unban_wrapped", True)
    except Exception:
        pass

    setattr(module, "register_moderation_commands", register_moderation_commands_patched)
    _PATCHED = True
    _log("patched commands_ext.moderation.register_moderation_commands to register /ban_unban after base moderation registration")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.commands_ext.moderation")
        if module is not None:
            _patch_moderation_module(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.moderation" or name.endswith("commands_ext.moderation"):
            target = sys.modules.get("stoney_verify.commands_ext.moderation") or sys.modules.get(name)
            if target is not None:
                _patch_moderation_module(target)
        else:
            _maybe_patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded()
_log("loaded; /ban_unban startup patch active")
