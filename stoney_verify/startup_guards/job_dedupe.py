from __future__ import annotations

"""
Runtime job dedupe guard.

This replaces the old root-level runtime_job_dedupe_safety.py.

It patches stoney_verify.runtime_jobs.enqueue_runtime_job so queued work gets a
sensible dedupe key even when the caller forgets to pass one.
"""

import builtins
import re
import sys
from typing import Any

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_MODULES: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧬 job_dedupe {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ job_dedupe {message}")
    except Exception:
        pass


def _normalize_label(label: Any) -> str:
    text = str(label or "runtime-job").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9:_=.#@ -]+", "", text)
    return text[:180] or "runtime-job"


def _infer_dedupe_key(*, kind: Any, guild_id: Any, label: Any) -> str:
    safe_kind = str(kind or "job").strip().lower()[:80] or "job"
    safe_guild = str(guild_id or "global").strip()[:80] or "global"
    safe_label = _normalize_label(label)
    return f"auto:{safe_kind}:{safe_guild}:{safe_label}"


def _patch_runtime_jobs(module: Any) -> None:
    module_name = str(getattr(module, "__name__", "") or "")
    if module_name in _PATCHED_MODULES:
        return

    original = getattr(module, "enqueue_runtime_job", None)
    if not callable(original):
        return

    if getattr(original, "_job_dedupe_wrapped", False) or getattr(original, "_runtime_job_dedupe_safety_wrapped", False):
        _PATCHED_MODULES.add(module_name)
        return

    async def _deduping_enqueue_runtime_job(*args: Any, **kwargs: Any) -> Any:
        try:
            dedupe_key = str(kwargs.get("dedupe_key", "") or "").strip()
            if not dedupe_key:
                kwargs["dedupe_key"] = _infer_dedupe_key(
                    kind=kwargs.get("kind", "job"),
                    guild_id=kwargs.get("guild_id", "global"),
                    label=kwargs.get("label", "runtime-job"),
                )
        except Exception as e:
            _warn(f"failed to infer dedupe key; continuing without auto-dedupe: {e!r}")

        return await original(*args, **kwargs)

    try:
        setattr(_deduping_enqueue_runtime_job, "_job_dedupe_wrapped", True)
        setattr(_deduping_enqueue_runtime_job, "_job_dedupe_original", original)
    except Exception:
        pass

    setattr(module, "enqueue_runtime_job", _deduping_enqueue_runtime_job)
    _PATCHED_MODULES.add(module_name)
    _log(f"patched {module_name}.enqueue_runtime_job with auto-dedupe")


def _maybe_patch_loaded_modules() -> None:
    try:
        module = sys.modules.get("stoney_verify.runtime_jobs")
        if module is not None:
            _patch_runtime_jobs(module)
    except Exception as e:
        _warn(f"runtime_jobs patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        if name == "stoney_verify.runtime_jobs" or name.endswith(".runtime_jobs"):
            target = sys.modules.get("stoney_verify.runtime_jobs") or sys.modules.get(name)
            if target is not None:
                _patch_runtime_jobs(target)
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; automatic runtime job dedupe guard active")
