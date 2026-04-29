from __future__ import annotations

"""
Temporary native lifecycle wiring patch.

The clean helpers now live in:
    stoney_verify.tickets_new.lifecycle_categories

This shim wires existing tickets_new.service lifecycle hooks into those helpers
without editing the giant service file in a risky blind way. Once service.py is
refactored in-place, this patch can be deleted.
"""

import builtins
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🎫 runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


async def _move_archive(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import try_move_ticket_to_archive_category

        return bool(await try_move_ticket_to_archive_category(channel))
    except Exception as e:
        _warn(f"archive lifecycle move failed channel={getattr(channel, 'id', None)}: {e!r}")
        return False


async def _move_active(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import try_move_ticket_to_active_category

        return bool(await try_move_ticket_to_active_category(channel))
    except Exception as e:
        _warn(f"active lifecycle move failed channel={getattr(channel, 'id', None)}: {e!r}")
        return False


def _patch_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:native_lifecycle_categories_v1"
    if key in _PATCHED:
        return

    patched_any = False

    original_archive = getattr(module, "_move_ticket_to_archive_if_configured", None)
    if callable(original_archive) and not getattr(original_archive, "_native_lifecycle_wrapped", False):
        async def _move_ticket_to_archive_if_configured_native(channel: discord.TextChannel) -> bool:
            moved = await _move_archive(channel)
            if moved:
                return True
            # Do not silently use the old env/name fallback in public mode unless
            # native resolution failed. This fallback keeps dev/private servers
            # alive while production cleanup continues.
            try:
                return bool(await original_archive(channel))
            except Exception as e:
                _warn(f"legacy archive fallback failed channel={getattr(channel, 'id', None)}: {e!r}")
                return False

        try:
            setattr(_move_ticket_to_archive_if_configured_native, "_native_lifecycle_wrapped", True)
        except Exception:
            pass
        setattr(module, "_move_ticket_to_archive_if_configured", _move_ticket_to_archive_if_configured_native)
        patched_any = True

    original_active = getattr(module, "_move_ticket_to_active_if_configured", None)
    if callable(original_active) and not getattr(original_active, "_native_lifecycle_wrapped", False):
        async def _move_ticket_to_active_if_configured_native(channel: discord.TextChannel) -> bool:
            moved = await _move_active(channel)
            if moved:
                return True
            try:
                return bool(await original_active(channel))
            except Exception as e:
                _warn(f"legacy active fallback failed channel={getattr(channel, 'id', None)}: {e!r}")
                return False

        try:
            setattr(_move_ticket_to_active_if_configured_native, "_native_lifecycle_wrapped", True)
        except Exception:
            pass
        setattr(module, "_move_ticket_to_active_if_configured", _move_ticket_to_active_if_configured_native)
        patched_any = True

    if patched_any:
        _PATCHED.add(key)
        _log(f"patched {module_name}; close/reopen category movement now uses native lifecycle helpers")


def _patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.tickets_new.service")
        if module is not None:
            _patch_service(module)
    except Exception as e:
        _warn(f"loaded service patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_service(target)
        else:
            _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; native ticket lifecycle movement wiring active")
