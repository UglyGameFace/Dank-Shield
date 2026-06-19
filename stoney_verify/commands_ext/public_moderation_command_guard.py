from __future__ import annotations

"""
Clean public moderation command guard.

This replaces the old root-level runtime_public_mod_ban_toggle_startup_patch.py.

Purpose:
- keep public moderation command names obvious
- remove confusing old local registrations like /mod_ban, /mod_kick, /mod_timeout
- ensure the public Ban/Unban toggle command is registered from the real commands_ext package
"""

import builtins
import sys
from typing import Any

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🪄 public_moderation_command_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_moderation_command_guard {message}")
    except Exception:
        pass


def _apply_ban_target_resolver() -> None:
    try:
        from stoney_verify.startup_guards import ban_target_resolution_guard

        ban_target_resolution_guard.apply()
    except Exception:
        pass


def _remove_existing_global_command(tree: Any, name: str) -> None:
    try:
        tree.remove_command(name, guild=None)
    except Exception:
        try:
            commands = getattr(tree, "_global_commands", None)
            if isinstance(commands, dict):
                commands.pop(name, None)
        except Exception:
            pass


def _patch_moderation_module(module: Any) -> None:
    global _PATCHED
    if _PATCHED:
        _apply_ban_target_resolver()
        return

    original = getattr(module, "register_moderation_commands", None)
    if not callable(original) or getattr(original, "_clean_public_moderation_wrapped", False):
        _apply_ban_target_resolver()
        return

    def register_moderation_commands_patched(bot: Any, tree: Any) -> None:
        original(bot, tree)

        # Remove old confusing local registrations after base moderation registers.
        for stale_name in ("mod_ban", "mod_ban_toggle", "mod_kick", "mod_timeout"):
            _remove_existing_global_command(tree, stale_name)

        try:
            from stoney_verify.commands_ext.public_ban_unban_patch import register_public_ban_unban_patch

            register_public_ban_unban_patch(bot, tree)
            _apply_ban_target_resolver()
        except Exception as e:
            _warn(f"failed replacing ban command: {e!r}")

    try:
        setattr(register_moderation_commands_patched, "_clean_public_moderation_wrapped", True)
    except Exception:
        pass

    setattr(module, "register_moderation_commands", register_moderation_commands_patched)
    _PATCHED = True
    _apply_ban_target_resolver()
    _log("patched commands_ext.moderation.register_moderation_commands for clean public moderation commands")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.commands_ext.moderation")
        if module is not None:
            _patch_moderation_module(module)
        _apply_ban_target_resolver()
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
_log("loaded; clean public moderation command guard active")


__all__ = []
