from __future__ import annotations

"""
Runtime safety patch for Stoney Verify.

Why this exists:
- The bot currently has some sync Supabase/PostgREST lookups inside risk-profile code.
- When those sync HTTP calls happen inside Discord's asyncio event loop, the whole bot can freeze.
- Discord then reports heartbeat blocked, interactions lag, tickets appear to create extremely slowly,
  and the gateway session can be invalidated.

This file is intentionally small and surgical. Python automatically imports `sitecustomize`
when it is on sys.path, which is true when the bot starts from the repo root.

The patch does NOT change normal background/thread behavior. It only prevents the
hard identity proof lookups from doing sync network I/O while the current thread is
already running an asyncio event loop.
"""

import asyncio
import builtins
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🩹 runtime_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_safety {message}")
    except Exception:
        pass


def _running_event_loop_in_this_thread() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def _empty_hard_identity_context() -> Dict[str, Any]:
    return {
        "proof_matches": [],
        "matched_identity_fingerprints": [],
        "manual_confirmed": [],
        "manual_likely": [],
        "manual_not_linked_ids": set(),
    }


def _cache_get_raidguard_context(module: Any, guild_id: int, user_id: int) -> Dict[str, Any] | None:
    try:
        cache = getattr(module, "_HARD_PROOF_CACHE", {})
        validator = getattr(module, "_proof_cache_valid", None)
        key = (int(guild_id), int(user_id))
        cached = cache.get(key) if isinstance(cache, dict) else None
        if cached and callable(validator):
            ts, value = cached
            if validator(ts):
                return dict(value)
    except Exception:
        pass
    return None


def _cache_store_raidguard_context(module: Any, guild_id: int, user_id: int, value: Dict[str, Any]) -> None:
    try:
        cache = getattr(module, "_HARD_PROOF_CACHE", None)
        if isinstance(cache, dict):
            now = None
            try:
                now_fn = getattr(module, "_utcnow", None)
                if callable(now_fn):
                    now = now_fn()
            except Exception:
                now = None
            if now is None:
                now = datetime.now(timezone.utc)
            cache[(int(guild_id), int(user_id))] = (now, dict(value))
    except Exception:
        pass


def _patch_raidguard(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if module_name in _PATCHED_MODULES:
        return

    original_query_proof = getattr(module, "_query_identity_proof_matches_sync", None)
    original_query_manual = getattr(module, "_query_manual_alt_links_sync", None)
    original_load_context = getattr(module, "_load_hard_identity_context", None)

    if callable(original_query_proof):
        def _safe_query_identity_proof_matches_sync(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
            if _running_event_loop_in_this_thread():
                _warn(
                    "blocked sync identity_proof_matches lookup on event loop; "
                    f"guild={guild_id} user={user_id}"
                )
                return []
            return original_query_proof(guild_id, user_id)

        setattr(module, "_query_identity_proof_matches_sync", _safe_query_identity_proof_matches_sync)

    if callable(original_query_manual):
        def _safe_query_manual_alt_links_sync(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
            if _running_event_loop_in_this_thread():
                _warn(
                    "blocked sync manual_alt_links lookup on event loop; "
                    f"guild={guild_id} user={user_id}"
                )
                return []
            return original_query_manual(guild_id, user_id)

        setattr(module, "_query_manual_alt_links_sync", _safe_query_manual_alt_links_sync)

    if callable(original_load_context):
        def _safe_load_hard_identity_context(guild_id: int, user_id: int) -> Dict[str, Any]:
            cached = _cache_get_raidguard_context(module, int(guild_id), int(user_id))
            if cached is not None:
                return cached

            if _running_event_loop_in_this_thread():
                context = _empty_hard_identity_context()
                _cache_store_raidguard_context(module, int(guild_id), int(user_id), context)
                _warn(
                    "skipped hard identity context sync DB load on event loop; "
                    f"guild={guild_id} user={user_id}"
                )
                return context

            return original_load_context(guild_id, user_id)

        setattr(module, "_load_hard_identity_context", _safe_load_hard_identity_context)

    _PATCHED_MODULES.add(module_name)
    _log(f"patched {module_name} hard identity sync lookups")


def _patch_identity_proof_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    if module_name in _PATCHED_MODULES:
        return

    original_get_truth = getattr(module, "get_identity_truth_context", None)
    if not callable(original_get_truth):
        return

    def _safe_get_identity_truth_context(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        if _running_event_loop_in_this_thread():
            _warn("blocked sync identity truth context lookup on event loop")
            return {}
        return original_get_truth(*args, **kwargs)

    setattr(module, "get_identity_truth_context", _safe_get_identity_truth_context)
    _PATCHED_MODULES.add(module_name)
    _log(f"patched {module_name} event-loop sync truth lookup")


def _maybe_patch_loaded_modules() -> None:
    try:
        raidguard = sys.modules.get("stoney_verify.raidguard")
        if raidguard is not None:
            _patch_raidguard(raidguard)
    except Exception as e:
        _warn(f"raidguard patch failed: {repr(e)}")

    try:
        identity = sys.modules.get("stoney_verify.identity_proof_service")
        if identity is not None:
            _patch_identity_proof_service(identity)
    except Exception as e:
        _warn(f"identity_proof_service patch failed: {repr(e)}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        # Patch after imports so `from .raidguard import build_member_risk_profile`
        # receives functions whose globals point at the patched helpers.
        if name == "stoney_verify.raidguard" or name.endswith(".raidguard"):
            target = sys.modules.get("stoney_verify.raidguard") or sys.modules.get(name)
            if target is not None:
                _patch_raidguard(target)

        if name == "stoney_verify.identity_proof_service" or name.endswith(".identity_proof_service"):
            target = sys.modules.get("stoney_verify.identity_proof_service") or sys.modules.get(name)
            if target is not None:
                _patch_identity_proof_service(target)

        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {repr(e)}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("sitecustomize loaded; event-loop blocking DB guard active")
