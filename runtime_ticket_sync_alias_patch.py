from __future__ import annotations

"""
Runtime startup ticket-sync alias guard.

Why this exists:
- stoney_verify.app imports sync_active_ticket_channels_for_guild into a module-level alias.
- runtime_guild_config_ticket_patch can correctly patch stoney_verify.tickets_new.sync_service.
- without this guard, app.py may keep calling the pre-patch alias during startup maintenance.

This guard keeps the app startup alias pointed at the current sync_service function so
startup ticket sync/backfill receives the same per-guild guild_configs behavior as the
rest of the ticket runtime.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 runtime_ticket_sync_alias_patch {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_sync_alias_patch {message}")
    except Exception:
        pass


def _patch_app_ticket_sync_alias() -> None:
    global _PATCHED

    try:
        app_module: Any = sys.modules.get("stoney_verify.app")
        sync_module: Any = sys.modules.get("stoney_verify.tickets_new.sync_service")

        if app_module is None or sync_module is None:
            return

        current_sync = getattr(sync_module, "sync_active_ticket_channels_for_guild", None)
        if not callable(current_sync):
            return

        existing = getattr(app_module, "_sync_active_ticket_channels_for_guild", None)
        if existing is current_sync and _PATCHED:
            return

        setattr(app_module, "_sync_active_ticket_channels_for_guild", current_sync)
        _PATCHED = True
        _log("patched stoney_verify.app startup ticket sync alias to current sync_service function")
    except Exception as e:
        _warn(f"failed to patch app startup ticket sync alias: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if (
            name == "stoney_verify.app"
            or name.endswith("stoney_verify.app")
            or name == "stoney_verify.tickets_new.sync_service"
            or name.endswith("tickets_new.sync_service")
        ):
            _patch_app_ticket_sync_alias()
        else:
            _patch_app_ticket_sync_alias()
    except Exception as e:
        _warn(f"post-import alias patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_app_ticket_sync_alias()
_log("loaded; startup ticket sync alias guard active")
