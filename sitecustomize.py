from __future__ import annotations

"""
Runtime safety patch for Stoney Verify.

Why this exists:
- Some sync Supabase/PostgREST lookups still exist inside risk-profile code.
- When sync HTTP calls happen inside Discord's asyncio event loop, the whole bot can freeze.
- Discord then reports heartbeat blocked, interactions lag, tickets create slowly, and the
  gateway session can be invalidated.
- Ticket creation also used DB-heavy ticket-number/counter/event-log paths before and after
  channel creation. Those must be timeboxed so Discord channel creation stays responsive.
- Voice-state modlog must never run dashboard/risk context work long enough to block the bot.

This file is intentionally surgical. main.py force-imports this before stoney_verify.app.
"""

import asyncio
import builtins
import sys
import time
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


def _lightweight_member_context_snapshot(guild: Any, target: Any) -> Dict[str, Any]:
    try:
        roles = []
        try:
            roles = [
                {"id": str(getattr(role, "id", "")), "name": str(getattr(role, "name", ""))}
                for role in (getattr(target, "roles", None) or [])
                if str(getattr(role, "name", "")) != "@everyone"
            ]
        except Exception:
            roles = []

        return {
            "runtime_safety_fallback": True,
            "guild_id": str(getattr(guild, "id", "") or ""),
            "user_id": str(getattr(target, "id", "") or ""),
            "username": str(target) if target is not None else "Unknown",
            "display_name": str(getattr(target, "display_name", "") or ""),
            "bot": bool(getattr(target, "bot", False)),
            "joined_at": str(getattr(target, "joined_at", "") or ""),
            "created_at": str(getattr(target, "created_at", "") or ""),
            "role_count": len(roles),
            "roles": roles[:25],
            "risk_profile": {},
            "identity_context": {},
        }
    except Exception:
        return {"runtime_safety_fallback": True}


def _patch_raidguard(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:raidguard"
    if patch_key in _PATCHED_MODULES:
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

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} hard identity sync lookups")


def _patch_identity_proof_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:identity"
    if patch_key in _PATCHED_MODULES:
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
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} event-loop sync truth lookup")


def _patch_ticket_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:ticket_service_p0"
    if patch_key in _PATCHED_MODULES:
        return

    original_reserve = getattr(module, "_reserve_next_ticket_number", None)
    if callable(original_reserve):
        async def _safe_reserve_next_ticket_number(guild: Any, parent: Any = None, *, max_retries: int = 20) -> int:
            guild_id = int(getattr(guild, "id", 0) or 0)
            lock = None
            acquired = False
            started = time.monotonic()

            try:
                lock_getter = getattr(module, "_ticket_number_lock", None)
                if callable(lock_getter):
                    lock = lock_getter(guild_id)
                    await asyncio.wait_for(lock.acquire(), timeout=2.0)
                    acquired = True
            except Exception:
                _warn(f"ticket number lock timeout/bypass guild={guild_id}; using best-effort scan")

            try:
                channel_max = 0
                db_max = 0

                try:
                    scanner = getattr(module, "_channel_scan_max_ticket_number", None)
                    if callable(scanner):
                        channel_max = int(scanner(guild, parent=parent) or 0)
                except Exception as e:
                    _warn(f"ticket number channel scan failed guild={guild_id}: {repr(e)}")

                try:
                    db_reader = getattr(module, "_db_max_ticket_number", None)
                    if callable(db_reader):
                        db_max = int(
                            await asyncio.wait_for(
                                asyncio.to_thread(db_reader, guild_id),
                                timeout=1.75,
                            )
                            or 0
                        )
                except asyncio.TimeoutError:
                    _warn(f"ticket number DB max timeout guild={guild_id}; using channel scan only")
                except Exception as e:
                    _warn(f"ticket number DB max failed guild={guild_id}: {repr(e)}")

                next_number = max(channel_max, db_max) + 1
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _log(
                    f"reserved ticket number guild={guild_id} number={next_number} "
                    f"channel_max={channel_max} db_max={db_max} elapsed_ms={elapsed_ms}"
                )
                return int(next_number)
            finally:
                if acquired and lock is not None:
                    try:
                        lock.release()
                    except Exception:
                        pass

        setattr(module, "_reserve_next_ticket_number", _safe_reserve_next_ticket_number)

    def _wrap_async_timeout(name: str, timeout: float, default: Any) -> None:
        original = getattr(module, name, None)
        if not callable(original) or getattr(original, "_runtime_safety_wrapped", False):
            return

        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(original(*args, **kwargs), timeout=timeout)
            except asyncio.TimeoutError:
                _warn(f"{module_name}.{name} timed out after {timeout}s; continuing safely")
                return default

        try:
            setattr(_wrapped, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, name, _wrapped)

    for repo_name, default in (
        ("repo_find_open_ticket_for_owner", None),
        ("repo_create_ticket_record", None),
        ("repo_sync_ticket_record_from_channel", None),
        ("repo_safe_optional_update_by_channel_id", None),
    ):
        _wrap_async_timeout(repo_name, 6.0, default)

    for event_name in (
        "log_ticket_created",
        "log_ticket_claimed",
        "log_ticket_unclaimed",
        "log_ticket_transferred",
        "log_ticket_priority_updated",
        "log_ticket_note_added",
        "log_ticket_closed",
        "log_ticket_reopened",
        "log_ticket_deleted",
        "log_ticket_transcript_attached",
    ):
        _wrap_async_timeout(event_name, 3.0, False)

    original_create = getattr(module, "create_ticket_channel", None)
    if callable(original_create) and not getattr(original_create, "_runtime_safety_wrapped", False):
        async def _instrumented_create_ticket_channel(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            guild_id = getattr(kwargs.get("guild"), "id", None)
            owner_id = getattr(kwargs.get("owner"), "id", None)
            try:
                result = await original_create(*args, **kwargs)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                channel_id = getattr(result, "id", None)
                _log(
                    f"create_ticket_channel complete guild={guild_id} owner={owner_id} "
                    f"channel={channel_id} elapsed_ms={elapsed_ms}"
                )
                return result
            except Exception as e:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _warn(
                    f"create_ticket_channel failed guild={guild_id} owner={owner_id} "
                    f"elapsed_ms={elapsed_ms} error={repr(e)}"
                )
                raise

        try:
            setattr(_instrumented_create_ticket_channel, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "create_ticket_channel", _instrumented_create_ticket_channel)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} ticket creation timing/DB guards")


def _patch_modlog(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:modlog_voice_p0"
    if patch_key in _PATCHED_MODULES:
        return

    original_context = getattr(module, "_fetch_member_context_snapshot", None)
    if callable(original_context) and not getattr(original_context, "_runtime_safety_wrapped", False):
        async def _safe_fetch_member_context_snapshot(guild: Any, target: Any) -> Dict[str, Any]:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(original_context(guild, target), timeout=2.75)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > 1500:
                    _warn(
                        f"member context snapshot slow guild={getattr(guild, 'id', None)} "
                        f"target={getattr(target, 'id', None)} elapsed_ms={elapsed_ms}"
                    )
                return result if isinstance(result, dict) else {}
            except asyncio.TimeoutError:
                _warn(
                    f"member context snapshot timeout guild={getattr(guild, 'id', None)} "
                    f"target={getattr(target, 'id', None)}; using lightweight fallback"
                )
                return _lightweight_member_context_snapshot(guild, target)
            except Exception as e:
                _warn(
                    f"member context snapshot failed guild={getattr(guild, 'id', None)} "
                    f"target={getattr(target, 'id', None)} error={repr(e)}"
                )
                return _lightweight_member_context_snapshot(guild, target)

        try:
            setattr(_safe_fetch_member_context_snapshot, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_fetch_member_context_snapshot", _safe_fetch_member_context_snapshot)

    original_dashboard_log = getattr(module, "post_dashboard_mod_action_log", None)
    if callable(original_dashboard_log) and not getattr(original_dashboard_log, "_runtime_safety_wrapped", False):
        async def _safe_post_dashboard_mod_action_log(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(original_dashboard_log(*args, **kwargs), timeout=4.0)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > 2500:
                    _warn(f"dashboard mod action log slow elapsed_ms={elapsed_ms}")
                return result
            except asyncio.TimeoutError:
                _warn("dashboard mod action log timeout; skipped to protect Discord heartbeat")
                return False
            except Exception as e:
                _warn(f"dashboard mod action log failed safely: {repr(e)}")
                return False

        try:
            setattr(_safe_post_dashboard_mod_action_log, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "post_dashboard_mod_action_log", _safe_post_dashboard_mod_action_log)

    original_voice = getattr(module, "maybe_log_voice_state_update", None)
    if callable(original_voice) and not getattr(original_voice, "_runtime_safety_wrapped", False):
        async def _safe_maybe_log_voice_state_update(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(original_voice(*args, **kwargs), timeout=4.5)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > 2500:
                    _warn(f"voice state modlog slow elapsed_ms={elapsed_ms}")
                return result
            except asyncio.TimeoutError:
                _warn("voice state modlog timeout; skipped to protect Discord heartbeat")
                return False
            except Exception as e:
                _warn(f"voice state modlog failed safely: {repr(e)}")
                return False

        try:
            setattr(_safe_maybe_log_voice_state_update, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "maybe_log_voice_state_update", _safe_maybe_log_voice_state_update)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} voice/dashboard modlog timeouts")


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

    try:
        service = sys.modules.get("stoney_verify.tickets_new.service")
        if service is not None:
            _patch_ticket_service(service)
    except Exception as e:
        _warn(f"ticket service patch failed: {repr(e)}")

    try:
        modlog = sys.modules.get("stoney_verify.modlog")
        if modlog is not None:
            _patch_modlog(modlog)
    except Exception as e:
        _warn(f"modlog patch failed: {repr(e)}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        if name == "stoney_verify.raidguard" or name.endswith(".raidguard"):
            target = sys.modules.get("stoney_verify.raidguard") or sys.modules.get(name)
            if target is not None:
                _patch_raidguard(target)

        if name == "stoney_verify.identity_proof_service" or name.endswith(".identity_proof_service"):
            target = sys.modules.get("stoney_verify.identity_proof_service") or sys.modules.get(name)
            if target is not None:
                _patch_identity_proof_service(target)

        if name == "stoney_verify.tickets_new.service" or name.endswith(".tickets_new.service") or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_ticket_service(target)

        if name == "stoney_verify.modlog" or name.endswith(".modlog"):
            target = sys.modules.get("stoney_verify.modlog") or sys.modules.get(name)
            if target is not None:
                _patch_modlog(target)

        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {repr(e)}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("sitecustomize loaded; event-loop DB guard + ticket creation guard + modlog guard active")
