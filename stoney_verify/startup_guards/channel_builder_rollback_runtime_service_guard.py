from __future__ import annotations

"""Bridge existing Channel Builder rollback API guard to the rollback service."""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_rollback_runtime_service_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_rollback_runtime_service_guard {message}")
    except Exception:
        pass


def _patch_rollback_guard(rollback_guard: Any) -> bool:
    if getattr(rollback_guard, "_CHANNEL_BUILDER_ROLLBACK_RUNTIME_SERVICE_PATCHED", False):
        return False
    from ..services import channel_builder_rollback_runtime as service

    rollback_guard._source_job_rollback_plan = service.source_job_rollback_plan
    rollback_guard._category_by_id = service.category_by_id
    rollback_guard._rollback_delete_created = service.rollback_delete_created
    rollback_guard._rollback_rename = service.rollback_rename
    rollback_guard._execute_rollback_plan = service.execute_rollback_plan
    rollback_guard.submit_channel_builder_rollback_job = service.submit_rollback_job
    rollback_guard._CHANNEL_BUILDER_ROLLBACK_RUNTIME_SERVICE_PATCHED = True
    _log("existing rollback guard now delegates rollback behavior to service module")
    return True


def _maybe_patch_loaded() -> None:
    try:
        rollback_guard = sys.modules.get("stoney_verify.startup_guards.channel_builder_rollback_api_guard")
        if rollback_guard is None:
            from . import channel_builder_rollback_api_guard as rollback_guard  # type: ignore
        _patch_rollback_guard(rollback_guard)
    except Exception as e:
        _warn(f"patch loaded channel_builder_rollback_api_guard failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.startup_guards.channel_builder_rollback_api_guard" or name.endswith(".channel_builder_rollback_api_guard"):
            target = sys.modules.get("stoney_verify.startup_guards.channel_builder_rollback_api_guard") or sys.modules.get(name)
            if target is not None:
                _patch_rollback_guard(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_rollback_runtime_service_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_rollback_runtime_service_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; rollback service bridge active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
