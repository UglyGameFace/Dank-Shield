from __future__ import annotations

"""
Startup ticket-sync alias guard.

This replaces the old root-level runtime_ticket_sync_alias_patch.py.

Why this still exists:
- stoney_verify.app imports sync_active_ticket_channels_for_guild into a module-level alias.
- ticket sync guards can wrap stoney_verify.tickets_new.sync_service after that alias was created.
- this keeps app.py pointed at the current sync_service function without rewriting the large app startup file in a risky pass.

The long-term cleaner version is to update app.py to resolve the sync function at
runtime inside _maybe_run_ticket_sync_once(). Until then, this package-local guard
keeps the behavior stable and removes another root patch from startup.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 ticket_sync_alias_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_sync_alias_guard {message}")
    except Exception:
        pass


def patch_app_ticket_sync_alias() -> None:
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
        text = str(name)
        if (
            text == "stoney_verify.app"
            or text.endswith("stoney_verify.app")
            or text == "stoney_verify.tickets_new.sync_service"
            or text.endswith("tickets_new.sync_service")
        ):
            patch_app_ticket_sync_alias()
    except Exception as e:
        _warn(f"post-import alias guard failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
patch_app_ticket_sync_alias()
_log("loaded; startup ticket sync alias guard active")


__all__ = ["patch_app_ticket_sync_alias"]
