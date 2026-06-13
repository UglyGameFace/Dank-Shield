from __future__ import annotations

"""Route Spam Guard configuration writes through the shared operation queue.

This prevents duplicate panel clicks, repeated modal submits, and parallel staff
changes from racing guild_security_settings or leaving the panel showing stale
state.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 spam_guard_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ spam_guard_operation_queue_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_patch_keys(patch: Any) -> list[str]:
    try:
        if isinstance(patch, dict):
            return sorted(str(k) for k in patch.keys())[:50]
    except Exception:
        pass
    return []


def _wrap_spam_guard(spam_mod: Any) -> bool:
    original = getattr(spam_mod, "save_spam_settings", None)
    if not callable(original) or getattr(original, "_spam_guard_operation_queue_wrapped", False):
        return False

    async def wrapped(guild_id: int, patch: dict[str, Any], *, updated_by: Any = None) -> tuple[dict[str, Any], bool]:
        gid = _safe_int(guild_id, 0)
        actor_id = _safe_int(getattr(updated_by, "id", 0), 0)
        patch_keys = _safe_patch_keys(patch)
        try:
            from ..operation_queue import run_exclusive

            state, result, _job = await run_exclusive(
                guild_id=gid or "global",
                actor_id=actor_id or None,
                operation_type="spam_guard_update_config",
                risk_level="moderate",
                source="discord_command",
                payload={"patch_keys": patch_keys, "patch": dict(patch or {})},
                concurrency_class="guild_config_write",
                concurrency_key="spam_guard_settings",
                timeout_seconds=120.0,
                reject_if_busy=True,
                factory=lambda: original(guild_id, patch, updated_by=updated_by),
            )
            if state in {"succeeded", "partial", "failed"} and result is not None:
                return result
            if hasattr(spam_mod, "get_spam_settings"):
                current = await spam_mod.get_spam_settings(gid)
            else:
                current = dict(patch or {})
            return current, False
        except Exception as e:
            _warn(f"spam guard settings queue unavailable; running original: {e!r}")
            return await original(guild_id, patch, updated_by=updated_by)

    setattr(wrapped, "_spam_guard_operation_queue_wrapped", True)
    setattr(wrapped, "_spam_guard_operation_queue_original", original)
    setattr(spam_mod, "save_spam_settings", wrapped)
    return True


def _patch_loaded() -> None:
    try:
        spam_mod = sys.modules.get("stoney_verify.spam_guard")
        if spam_mod is not None:
            wrapped = _wrap_spam_guard(spam_mod)
            if wrapped:
                _log("patched Spam Guard settings save")
    except Exception as e:
        _warn(f"patch loaded spam_guard failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.spam_guard" or name.endswith(".spam_guard"):
            target = sys.modules.get("stoney_verify.spam_guard") or sys.modules.get(name)
            if target is not None:
                _wrap_spam_guard(target)
        _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_spam_guard_operation_queue_import_hook", False):
        _patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_spam_guard_operation_queue_import_hook", True)
        _patch_loaded()
        _log("loaded; Spam Guard config queue guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
