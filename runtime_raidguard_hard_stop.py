from __future__ import annotations

"""
Hard stop for raidguard sync database lookups on the Discord event loop.

The heartbeat trace showed voice-state modlog calling:
  modlog -> raidguard.build_member_risk_profile -> _load_hard_identity_context

That path used the sync Supabase/PostgREST client and blocked the Discord gateway
thread inside httpcore. This guard patches raidguard before app.py imports events.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧯 runtime_raidguard_hard_stop {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_raidguard_hard_stop {message}")
    except Exception:
        pass


def _in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def _empty_context() -> Dict[str, Any]:
    return {
        "proof_matches": [],
        "matched_identity_fingerprints": [],
        "manual_confirmed": [],
        "manual_likely": [],
        "manual_not_linked_ids": set(),
    }


def _cache_get(module: Any, guild_id: int, user_id: int) -> Dict[str, Any] | None:
    try:
        cache = getattr(module, "_HARD_PROOF_CACHE", None)
        valid = getattr(module, "_proof_cache_valid", None)
        if not isinstance(cache, dict) or not callable(valid):
            return None
        cached = cache.get((int(guild_id), int(user_id)))
        if not cached:
            return None
        ts, value = cached
        if valid(ts):
            return dict(value)
    except Exception:
        return None
    return None


def _cache_put(module: Any, guild_id: int, user_id: int, value: Dict[str, Any]) -> None:
    try:
        cache = getattr(module, "_HARD_PROOF_CACHE", None)
        if isinstance(cache, dict):
            cache[(int(guild_id), int(user_id))] = (datetime.now(timezone.utc), dict(value))
    except Exception:
        pass


def patch_now() -> bool:
    global _PATCHED

    if _PATCHED:
        return True

    try:
        from stoney_verify import raidguard
    except Exception as e:
        _warn(f"raidguard import not ready: {e!r}")
        return False

    original_proof = getattr(raidguard, "_query_identity_proof_matches_sync", None)
    original_manual = getattr(raidguard, "_query_manual_alt_links_sync", None)
    original_load = getattr(raidguard, "_load_hard_identity_context", None)

    def safe_proof(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        if _in_event_loop():
            _warn(f"blocked event-loop identity proof lookup guild={guild_id} user={user_id}")
            return []
        if callable(original_proof):
            return original_proof(guild_id, user_id)
        return []

    def safe_manual(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        if _in_event_loop():
            _warn(f"blocked event-loop manual alt lookup guild={guild_id} user={user_id}")
            return []
        if callable(original_manual):
            return original_manual(guild_id, user_id)
        return []

    def safe_load(guild_id: int, user_id: int) -> Dict[str, Any]:
        cached = _cache_get(raidguard, int(guild_id), int(user_id))
        if cached is not None:
            return cached

        if _in_event_loop():
            context = _empty_context()
            _cache_put(raidguard, int(guild_id), int(user_id), context)
            _warn(f"skipped event-loop hard identity context load guild={guild_id} user={user_id}")
            return context

        if callable(original_load):
            return original_load(guild_id, user_id)
        return _empty_context()

    try:
        raidguard._query_identity_proof_matches_sync = safe_proof  # type: ignore[attr-defined]
        raidguard._query_manual_alt_links_sync = safe_manual  # type: ignore[attr-defined]
        raidguard._load_hard_identity_context = safe_load  # type: ignore[attr-defined]
        _PATCHED = True
        _log("patched raidguard hard identity lookups before app import")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


patch_now()
