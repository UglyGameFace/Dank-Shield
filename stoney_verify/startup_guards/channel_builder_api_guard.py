from __future__ import annotations

"""Temporary compatibility shim for Channel Builder structured API routes.

The real route handlers live in stoney_verify.api_new.channel_builder_routes.
This guard no longer rebuilds/copies api_new.server.start_api. Until server.py
is patched directly, it wraps start_api and injects Channel Builder routes into
the aiohttp app at AppRunner creation time, then restores aiohttp immediately.
"""

import builtins
import sys
from typing import Any

from aiohttp import web

_ORIGINAL_IMPORT = builtins.__import__
_WAITING_LOGGED = False


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_api_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_api_guard {message}")
    except Exception:
        pass


def _waiting(message: str) -> None:
    global _WAITING_LOGGED
    if _WAITING_LOGGED:
        return
    _WAITING_LOGGED = True
    try:
        print(f"🧱 channel_builder_api_guard waiting: {message}")
    except Exception:
        pass


def _direct_registration_present(server: Any) -> bool:
    try:
        start_api = getattr(server, "start_api", None)
        names = getattr(start_api, "__code__", None).co_names if callable(start_api) and getattr(start_api, "__code__", None) else ()
        return "register_channel_builder_routes" in set(names or ())
    except Exception:
        return False


def _patch_server_module(server: Any) -> None:
    if getattr(server, "_CHANNEL_BUILDER_API_GUARD_PATCHED", False):
        return

    if _direct_registration_present(server):
        server._CHANNEL_BUILDER_API_GUARD_PATCHED = True
        _log("direct Channel Builder route registration detected; compatibility shim skipped")
        return
    original_start_api = getattr(server, "start_api", None)
    if not callable(original_start_api):
        _waiting("structured server imported before start_api is attached; route injection will retry")
        return

    async def start_api_with_channel_builder(*args: Any, **kwargs: Any):
        original_app_runner = web.AppRunner

        def app_runner_with_channel_builder(app: web.Application, *runner_args: Any, **runner_kwargs: Any):
            try:
                if not getattr(app, "_dank_shield_channel_builder_routes", False):
                    from stoney_verify.api_new.channel_builder_routes import register_channel_builder_routes

                    register_channel_builder_routes(app, server)
                    setattr(app, "_dank_shield_channel_builder_routes", True)
                    _log("injected Channel Builder routes into structured API app")
            except Exception as e:
                _warn(f"failed to inject Channel Builder routes: {e!r}")
                raise
            return original_app_runner(app, *runner_args, **runner_kwargs)

        web.AppRunner = app_runner_with_channel_builder  # type: ignore[assignment]
        try:
            return await original_start_api(*args, **kwargs)
        finally:
            web.AppRunner = original_app_runner  # type: ignore[assignment]

    setattr(start_api_with_channel_builder, "_channel_builder_api_original", original_start_api)
    server.start_api = start_api_with_channel_builder
    server._CHANNEL_BUILDER_API_GUARD_PATCHED = True
    _log("patched structured API startup with AppRunner route injection shim")


def _maybe_patch_loaded() -> None:
    try:
        server = sys.modules.get("stoney_verify.api_new.server")
        if server is not None:
            _patch_server_module(server)
    except Exception as e:
        _warn(f"patch loaded server failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.api_new.server" or name.endswith(".api_new.server"):
            target = sys.modules.get("stoney_verify.api_new.server") or sys.modules.get(name)
            if target is not None:
                _patch_server_module(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_api_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_api_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; Channel Builder route injection shim active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
