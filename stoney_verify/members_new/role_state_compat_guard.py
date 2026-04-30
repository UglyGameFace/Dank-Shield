from __future__ import annotations

"""
guild_members.role_state compatibility guard.

This replaces the old root-level runtime_guild_members_role_state_compat_patch.py.

Best permanent fix:
Run the migration that relaxes guild_members_role_state_check to allow short
snake_case role-state labels. After that, future states like `cosmetic_only` do
not need more Supabase edits.

Runtime fallback:
If an older database still rejects a new role_state with SQLSTATE 23514, retry
that single write with a backward-compatible state while preserving the original
state in role_state_raw / role_state_reason. This avoids noisy startup failures
without hiding what the bot actually computed.
"""

import builtins
import sys
from typing import Any, Dict

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_MODULES: set[str] = set()

_COMPAT_ROLE_STATE_MAP = {
    "cosmetic_only": "missing_unverified",
}


def _log(message: str) -> None:
    try:
        print(f"🧾 role_state_compat_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _is_role_state_constraint_error(exc: BaseException) -> bool:
    blob = ""
    try:
        blob = f"{type(exc).__name__} {exc!r} {str(exc)}"
    except Exception:
        blob = ""
    blob_l = blob.lower()
    return "23514" in blob_l and "guild_members_role_state_check" in blob_l


def _compat_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    out: Dict[str, Any] = dict(payload)
    state = _safe_str(out.get("role_state"), "")
    mapped = _COMPAT_ROLE_STATE_MAP.get(state)
    if mapped:
        out["role_state_raw"] = state
        out["role_state"] = mapped
        if state == "cosmetic_only":
            out["has_cosmetic_only"] = bool(out.get("has_cosmetic_only", True))
        reason = _safe_str(out.get("role_state_reason"), "")
        marker = f"Original computed role_state was `{state}`; retried as `{mapped}` for database compatibility."
        if marker not in reason:
            out["role_state_reason"] = f"{reason} {marker}".strip() if reason else marker
    return out


def _patch_sync_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return
    patch_key = f"{module_name}:role_state_compat_retry"
    if patch_key in _PATCHED_MODULES:
        return

    original_upsert = getattr(module, "_guild_members_upsert_safe_async", None)
    if callable(original_upsert) and not getattr(original_upsert, "_role_state_compat_retry_wrapped", False):
        async def _guild_members_upsert_safe_async_patched(sb: Any, payload: Dict[str, Any]) -> None:
            try:
                return await original_upsert(sb, payload)
            except Exception as e:
                if not _is_role_state_constraint_error(e):
                    raise
                compat = _compat_payload(payload)
                if compat == payload:
                    raise
                _log(f"role_state={payload.get('role_state')!r} rejected by old DB constraint; retrying compat write")
                return await original_upsert(sb, compat)

        try:
            setattr(_guild_members_upsert_safe_async_patched, "_role_state_compat_retry_wrapped", True)
        except Exception:
            pass
        setattr(module, "_guild_members_upsert_safe_async", _guild_members_upsert_safe_async_patched)

    original_update = getattr(module, "_guild_members_update_safe_async", None)
    if callable(original_update) and not getattr(original_update, "_role_state_compat_retry_wrapped", False):
        async def _guild_members_update_safe_async_patched(sb: Any, guild_id: str, user_id: str, payload: Dict[str, Any]) -> None:
            try:
                return await original_update(sb, guild_id, user_id, payload)
            except Exception as e:
                if not _is_role_state_constraint_error(e):
                    raise
                compat = _compat_payload(payload)
                if compat == payload:
                    raise
                _log(f"role_state={payload.get('role_state')!r} rejected by old DB constraint during update; retrying compat write")
                return await original_update(sb, guild_id, user_id, compat)

        try:
            setattr(_guild_members_update_safe_async_patched, "_role_state_compat_retry_wrapped", True)
        except Exception:
            pass
        setattr(module, "_guild_members_update_safe_async", _guild_members_update_safe_async_patched)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}; future role_state values pass through, old DBs retry compat on 23514")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.members_new.sync_service")
        if module is not None:
            _patch_sync_service(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.members_new.sync_service" or name.endswith("members_new.sync_service"):
            target = sys.modules.get("stoney_verify.members_new.sync_service") or sys.modules.get(name)
            if target is not None:
                _patch_sync_service(target)
        else:
            _maybe_patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded()
_log("loaded; guild_members role_state compatibility retry guard active")


__all__ = []
