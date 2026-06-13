from __future__ import annotations

"""Bridge existing Channel Builder API guard to the first-class runtime service."""

import builtins
import sys
from typing import Any

from aiohttp import web

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_runtime_service_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_runtime_service_guard {message}")
    except Exception:
        pass


async def _service_list_channel_builder_channels(server: Any, request: web.Request):
    from ..services.channel_builder_runtime import list_channels_payload

    data = await server._merged_request_data(request) if hasattr(server, "_merged_request_data") else dict(request.query)
    payload, err = await list_channels_payload(server=server, guild_id=data.get("guild_id"))
    if err is not None:
        return err
    return server._json_ok(**(payload or {}))


def _patch_api_guard(api_guard: Any) -> bool:
    if getattr(api_guard, "_CHANNEL_BUILDER_RUNTIME_SERVICE_PATCHED", False):
        return False
    from ..services import channel_builder_runtime as service

    api_guard._normalize_items = service.normalize_channel_builder_items
    api_guard._validate_items = service.validate_channel_builder_items
    api_guard._channel_kind = service.channel_kind
    api_guard._channel_payload = service.channel_payload
    api_guard._snapshot_channel = service.snapshot_channel
    api_guard._sort_channels = service.sort_channels
    api_guard._find_category = service.find_category
    api_guard._ensure_category = service.ensure_category
    api_guard._find_channel = service.find_channel
    api_guard._create_channel = service.create_channel
    api_guard._rename_channel = service.rename_channel
    api_guard._execute_channel_builder_plan = service.execute_channel_builder_plan
    api_guard.list_channel_builder_channels = _service_list_channel_builder_channels
    api_guard._CHANNEL_BUILDER_RUNTIME_SERVICE_PATCHED = True
    _log("existing API guard now delegates Channel Builder runtime behavior to service module")
    return True


def _maybe_patch_loaded() -> None:
    try:
        api_guard = sys.modules.get("stoney_verify.startup_guards.channel_builder_api_guard")
        if api_guard is None:
            from . import channel_builder_api_guard as api_guard  # type: ignore
        _patch_api_guard(api_guard)
    except Exception as e:
        _warn(f"patch loaded channel_builder_api_guard failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.startup_guards.channel_builder_api_guard" or name.endswith(".channel_builder_api_guard"):
            target = sys.modules.get("stoney_verify.startup_guards.channel_builder_api_guard") or sys.modules.get(name)
            if target is not None:
                _patch_api_guard(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_runtime_service_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_runtime_service_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; service bridge active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
