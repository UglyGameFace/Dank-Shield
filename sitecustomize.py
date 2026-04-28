from __future__ import annotations

"""
Runtime safety patch for Stoney Verify.

This file is force-imported by main.py before stoney_verify.app.

Purpose:
- prevent sync Supabase/PostgREST risk lookups from blocking Discord's event loop
- keep ticket creation responsive when DB/event-log paths are slow
- route noisy voice/dashboard modlog work through a bounded per-guild queue
- route startup maintenance/backfill work through a bounded per-guild queue

This is a production safety layer while the underlying modules are being refactored
into permanent async/queued code.
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
                _warn(f"blocked sync identity_proof_matches lookup on event loop; guild={guild_id} user={user_id}")
                return []
            return original_query_proof(guild_id, user_id)

        setattr(module, "_query_identity_proof_matches_sync", _safe_query_identity_proof_matches_sync)

    if callable(original_query_manual):
        def _safe_query_manual_alt_links_sync(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
            if _running_event_loop_in_this_thread():
                _warn(f"blocked sync manual_alt_links lookup on event loop; guild={guild_id} user={user_id}")
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
                _warn(f"skipped hard identity context sync DB load on event loop; guild={guild_id} user={user_id}")
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
                        db_max = int(await asyncio.wait_for(asyncio.to_thread(db_reader, guild_id), timeout=1.75) or 0)
                except asyncio.TimeoutError:
                    _warn(f"ticket number DB max timeout guild={guild_id}; using channel scan only")
                except Exception as e:
                    _warn(f"ticket number DB max failed guild={guild_id}: {repr(e)}")

                next_number = max(channel_max, db_max) + 1
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _log(f"reserved ticket number guild={guild_id} number={next_number} channel_max={channel_max} db_max={db_max} elapsed_ms={elapsed_ms}")
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
                _log(f"create_ticket_channel complete guild={guild_id} owner={owner_id} channel={channel_id} elapsed_ms={elapsed_ms}")
                return result
            except Exception as e:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _warn(f"create_ticket_channel failed guild={guild_id} owner={owner_id} elapsed_ms={elapsed_ms} error={repr(e)}")
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
    patch_key = f"{module_name}:modlog_voice_queue_p0"
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
                    _warn(f"member context snapshot slow guild={getattr(guild, 'id', None)} target={getattr(target, 'id', None)} elapsed_ms={elapsed_ms}")
                return result if isinstance(result, dict) else {}
            except asyncio.TimeoutError:
                _warn(f"member context snapshot timeout guild={getattr(guild, 'id', None)} target={getattr(target, 'id', None)}; using lightweight fallback")
                return _lightweight_member_context_snapshot(guild, target)
            except Exception as e:
                _warn(f"member context snapshot failed guild={getattr(guild, 'id', None)} target={getattr(target, 'id', None)} error={repr(e)}")
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
        async def _queued_maybe_log_voice_state_update(*args: Any, **kwargs: Any) -> Any:
            guild = args[0] if len(args) >= 1 else kwargs.get("guild")
            member = args[1] if len(args) >= 2 else kwargs.get("member")
            guild_id = getattr(guild, "id", None)
            member_id = getattr(member, "id", None)

            try:
                from stoney_verify.runtime_jobs import enqueue_runtime_job
            except Exception as e:
                _warn(f"runtime_jobs import failed; falling back to direct voice modlog timeout: {e!r}")
                try:
                    return await asyncio.wait_for(original_voice(*args, **kwargs), timeout=4.5)
                except Exception as inner:
                    _warn(f"voice state modlog fallback failed safely: {inner!r}")
                    return False

            async def _job() -> object:
                return await original_voice(*args, **kwargs)

            queued = await enqueue_runtime_job(
                kind="voice_modlog",
                guild_id=guild_id,
                label=f"voice_state member={member_id}",
                factory=_job,
                timeout=5.0,
                max_queue=250,
            )
            if not queued:
                _warn(f"voice state modlog dropped guild={guild_id} member={member_id}; queue full")
            return queued

        try:
            setattr(_queued_maybe_log_voice_state_update, "_runtime_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "maybe_log_voice_state_update", _queued_maybe_log_voice_state_update)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} voice/dashboard modlog queue + timeouts")


def _patch_app_startup(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:startup_queue_p0"
    if patch_key in _PATCHED_MODULES:
        return

    original_runner = getattr(module, "_startup_background_runner", None)
    if not callable(original_runner) or getattr(original_runner, "_runtime_safety_wrapped", False):
        return

    async def _queued_startup_background_runner() -> None:
        try:
            await asyncio.sleep(5.0)

            try:
                from stoney_verify.runtime_jobs import (
                    enqueue_runtime_job,
                    start_runtime_job_summary_logger,
                )
                start_runtime_job_summary_logger(interval_seconds=300)
            except Exception as e:
                _warn(f"runtime_jobs import failed for startup queue; using original startup runner: {e!r}")
                try:
                    await asyncio.wait_for(original_runner(), timeout=180.0)
                except asyncio.TimeoutError:
                    _warn("original startup background runner timed out after 180s")
                return

            guild_id = None
            try:
                guild = await asyncio.wait_for(module._resolve_runtime_guild(), timeout=8.0)
                guild_id = getattr(guild, "id", None)
            except Exception:
                guild = None

            async def _departed_job() -> object:
                return await module._maybe_run_departed_reconcile_once()

            async def _ticket_sync_job() -> object:
                await asyncio.sleep(2.0)
                return await module._maybe_run_ticket_sync_once()

            queued_departed = await enqueue_runtime_job(
                kind="startup_maintenance",
                guild_id=guild_id,
                label="departed_member_reconciliation",
                factory=_departed_job,
                timeout=120.0,
                max_queue=20,
            )

            queued_ticket_sync = await enqueue_runtime_job(
                kind="startup_maintenance",
                guild_id=guild_id,
                label="startup_ticket_sync_backfill",
                factory=_ticket_sync_job,
                timeout=120.0,
                max_queue=20,
            )

            _log(
                f"queued startup maintenance guild={guild_id} "
                f"departed={queued_departed} ticket_sync={queued_ticket_sync}"
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            _warn(f"queued startup background runner failed safely: {e!r}")

    try:
        setattr(_queued_startup_background_runner, "_runtime_safety_wrapped", True)
    except Exception:
        pass

    setattr(module, "_startup_background_runner", _queued_startup_background_runner)
    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name} startup maintenance queue")


def _maybe_patch_loaded_modules() -> None:
    for module_name, patcher in (
        ("stoney_verify.raidguard", _patch_raidguard),
        ("stoney_verify.identity_proof_service", _patch_identity_proof_service),
        ("stoney_verify.tickets_new.service", _patch_ticket_service),
        ("stoney_verify.modlog", _patch_modlog),
        ("stoney_verify.app", _patch_app_startup),
    ):
        try:
            module = sys.modules.get(module_name)
            if module is not None:
                patcher(module)
        except Exception as e:
            _warn(f"{module_name} patch failed: {repr(e)}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        target_map = {
            "stoney_verify.raidguard": _patch_raidguard,
            "stoney_verify.identity_proof_service": _patch_identity_proof_service,
            "stoney_verify.tickets_new.service": _patch_ticket_service,
            "stoney_verify.modlog": _patch_modlog,
            "stoney_verify.app": _patch_app_startup,
        }
        for module_name, patcher in target_map.items():
            if name == module_name or name.endswith(module_name.split("stoney_verify", 1)[-1]):
                target = sys.modules.get(module_name) or sys.modules.get(name)
                if target is not None:
                    patcher(target)
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {repr(e)}")

    return module


def _load_public_startup_scope_guard() -> None:
    """
    Secondary load path for the public startup-scope guard.

    main.py imports this guard too, but keeping it here prevents stale host
    launchers or alternate entrypoints from silently falling back to env-only
    single-guild startup behavior.
    """
    try:
        _ORIGINAL_IMPORT("runtime_public_startup_scope_patch")
        _log("verified public startup scope guard import")
    except Exception as e:
        _warn(f"public startup scope guard import failed: {e!r}")


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_load_public_startup_scope_guard()
_log("sitecustomize loaded; event-loop DB guard + ticket creation guard + queued modlog/startup guard active")
