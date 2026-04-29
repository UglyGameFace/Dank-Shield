from __future__ import annotations

"""
guild_members.role_state compatibility guard.

Some deployed Supabase schemas still have an older CHECK constraint for
`guild_members.role_state` and do not allow the newer `cosmetic_only` value.
The app can still preserve the useful signal through `has_cosmetic_only=true`
and `role_state_reason`, but the stored role_state must remain compatible.

This patch prevents noisy 23514 check-constraint failures without requiring an
immediate database migration.
"""

import builtins
import sys
from typing import Any, Dict

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

_COMPAT_ROLE_STATE_MAP = {
    "cosmetic_only": "missing_unverified",
}


def _log(message: str) -> None:
    try:
        print(f"🧾 runtime_guild_members_role_state_compat {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _sanitize_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    out: Dict[str, Any] = dict(payload)
    state = _safe_str(out.get("role_state"), "")
    mapped = _COMPAT_ROLE_STATE_MAP.get(state)
    if mapped:
        out["role_state"] = mapped
        out["has_cosmetic_only"] = bool(out.get("has_cosmetic_only", True))
        reason = _safe_str(out.get("role_state_reason"), "")
        marker = f"Original computed role_state was `{state}`; stored as `{mapped}` for database compatibility."
        if marker not in reason:
            out["role_state_reason"] = f"{reason} {marker}".strip() if reason else marker
    return out


def _patch_sync_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return
    patch_key = f"{module_name}:role_state_compat"
    if patch_key in _PATCHED_MODULES:
        return

    original_snapshot = getattr(module, "_member_role_snapshot", None)
    if callable(original_snapshot) and not getattr(original_snapshot, "_role_state_compat_wrapped", False):
        def _member_role_snapshot_patched(member: Any) -> Dict[str, Any]:
            snap = original_snapshot(member)
            return _sanitize_payload(snap)

        try:
            setattr(_member_role_snapshot_patched, "_role_state_compat_wrapped", True)
        except Exception:
            pass
        setattr(module, "_member_role_snapshot", _member_role_snapshot_patched)

    original_minimal = getattr(module, "_minimal_member_payload", None)
    if callable(original_minimal) and not getattr(original_minimal, "_role_state_compat_wrapped", False):
        def _minimal_member_payload_patched(member: Any, in_guild: bool = True) -> Dict[str, Any]:
            payload = original_minimal(member, in_guild)
            return _sanitize_payload(payload)

        try:
            setattr(_minimal_member_payload_patched, "_role_state_compat_wrapped", True)
        except Exception:
            pass
        setattr(module, "_minimal_member_payload", _minimal_member_payload_patched)

    original_upsert = getattr(module, "_guild_members_upsert_safe_async", None)
    if callable(original_upsert) and not getattr(original_upsert, "_role_state_compat_wrapped", False):
        async def _guild_members_upsert_safe_async_patched(sb: Any, payload: Dict[str, Any]) -> None:
            return await original_upsert(sb, _sanitize_payload(payload))

        try:
            setattr(_guild_members_upsert_safe_async_patched, "_role_state_compat_wrapped", True)
        except Exception:
            pass
        setattr(module, "_guild_members_upsert_safe_async", _guild_members_upsert_safe_async_patched)

    original_update = getattr(module, "_guild_members_update_safe_async", None)
    if callable(original_update) and not getattr(original_update, "_role_state_compat_wrapped", False):
        async def _guild_members_update_safe_async_patched(sb: Any, guild_id: str, user_id: str, payload: Dict[str, Any]) -> None:
            return await original_update(sb, guild_id, user_id, _sanitize_payload(payload))

        try:
            setattr(_guild_members_update_safe_async_patched, "_role_state_compat_wrapped", True)
        except Exception:
            pass
        setattr(module, "_guild_members_update_safe_async", _guild_members_update_safe_async_patched)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}; cosmetic_only stores as missing_unverified while preserving has_cosmetic_only")


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
_log("loaded; guild_members role_state compatibility guard active")
