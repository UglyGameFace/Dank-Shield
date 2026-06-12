from __future__ import annotations

"""
Event helper queue guard.

This replaces the old root-level runtime_event_safety.py.

It patches expensive event/startup sync helpers so they are queued through
stoney_verify.runtime_jobs instead of running inline inside Discord gateway or
startup paths.

This stays as a startup guard for now because it must load before/while
stoney_verify.events imports. The important cleanup is that it now lives inside
stoney_verify/startup_guards instead of cluttering the repo root.
"""

import asyncio
import builtins
import inspect
import sys
from typing import Any

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_MODULES: set[str] = set()
_RETRY_TASK_STARTED = False
_NOT_READY_LOGGED: set[str] = set()
_TARGET_EVENTS_MODULE = "stoney_verify.events"

_EVENT_HELPERS = (
    "_new_sync_member_safe",
    "_new_mark_member_left_safe",
    "_reconcile_stale_open_verification_tickets",
    "_run_initial_member_sync_once",
    "_maybe_run_initial_member_sync_once",
    "_initial_member_sync_once",
    "_warm_invite_cache_once",
    "_maybe_warm_invite_cache_once",
    "_warm_invite_cache",
    "_run_invite_cache_warmup",
    "_resume_member_wait_timers_once",
)


def _log(message: str) -> None:
    try:
        print(f"🧯 event_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ event_safety {message}")
    except Exception:
        pass


def _guild_id_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | str | None:
    for value in list(args) + list(kwargs.values()):
        try:
            guild = getattr(value, "guild", None)
            gid = getattr(guild, "id", None)
            if gid:
                return gid
        except Exception:
            pass

        try:
            gid = getattr(value, "id", None)
            if gid and hasattr(value, "members") and hasattr(value, "channels"):
                return gid
        except Exception:
            pass

    try:
        events = sys.modules.get(_TARGET_EVENTS_MODULE)
        bot = getattr(events, "bot", None)
        guilds = list(getattr(bot, "guilds", []) or [])
        if guilds:
            return getattr(guilds[0], "id", None)
    except Exception:
        pass

    return "global"


def _member_id_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | str | None:
    for value in list(args) + list(kwargs.values()):
        try:
            if getattr(value, "guild", None) is not None and getattr(value, "id", None):
                return getattr(value, "id", None)
        except Exception:
            pass
    return None


def _patch_member_role_snapshot(module: Any) -> bool:
    """Force events.py member snapshots through native per-guild role truth.

    events.py still has a legacy _member_role_snapshot body while the large
    module is being split apart. Runtime sync payloads must not keep deriving
    verification state from deployment/global role IDs, so redirect the helper to
    stoney_verify.role_truth until the function is physically removed from
    events.py.
    """
    original = getattr(module, "_member_role_snapshot", None)
    if not callable(original):
        return False
    if getattr(original, "_event_safety_role_truth_wrapped", False):
        return False

    def _role_truth_member_snapshot(member: Any) -> dict[str, Any]:
        try:
            from stoney_verify import role_truth

            return role_truth.build_member_role_snapshot(member)
        except Exception as e:
            _warn(f"role_truth snapshot fallback for member={getattr(member, 'id', None)}: {e!r}")
            return original(member)

    try:
        setattr(_role_truth_member_snapshot, "_event_safety_role_truth_wrapped", True)
        setattr(_role_truth_member_snapshot, "_event_safety_original", original)
    except Exception:
        pass

    setattr(module, "_member_role_snapshot", _role_truth_member_snapshot)
    return True


def _patch_join_verification_failure(module: Any) -> bool:
    """Route events.py fail-closed join handling through native removal safety."""
    original = getattr(module, "_handle_join_verification_failure", None)
    if not callable(original):
        return False
    if getattr(original, "_event_safety_fail_closed_wrapped", False):
        return False

    async def _service_join_verification_failure(member: Any, reason: Any) -> Any:
        try:
            from stoney_verify.members_new.join_removal_safety import handle_join_verification_failure

            return await handle_join_verification_failure(member, reason)
        except Exception as e:
            _warn(
                "native fail-closed handler failed; falling back to legacy events handler "
                f"member={getattr(member, 'id', None)} error={e!r}"
            )
            return await original(member, reason)

    try:
        setattr(_service_join_verification_failure, "_event_safety_fail_closed_wrapped", True)
        setattr(_service_join_verification_failure, "_fresh_join_role_recovery_wrapped", True)
        setattr(_service_join_verification_failure, "_event_safety_original", original)
    except Exception:
        pass

    setattr(module, "_handle_join_verification_failure", _service_join_verification_failure)
    return True


def _wrap_async_with_queue(
    module: Any,
    name: str,
    *,
    kind: str,
    timeout: float,
    max_queue: int,
    label_prefix: str,
) -> bool:
    original = getattr(module, name, None)
    if not callable(original):
        return False
    if getattr(original, "_event_safety_wrapped", False) or getattr(original, "_runtime_event_safety_wrapped", False):
        return False
    if not inspect.iscoroutinefunction(original):
        return False

    async def _queued_wrapper(*args: Any, **kwargs: Any) -> Any:
        guild_id = _guild_id_from_args(args, kwargs)
        member_id = _member_id_from_args(args, kwargs)
        label = f"{label_prefix}:{name}"
        if member_id:
            label += f" member={member_id}"

        try:
            from stoney_verify.runtime_jobs import enqueue_runtime_job
        except Exception as e:
            _warn(f"runtime_jobs import failed for {name}; falling back direct with timeout: {e!r}")
            try:
                return await asyncio.wait_for(original(*args, **kwargs), timeout=max(1.0, timeout))
            except asyncio.TimeoutError:
                _warn(f"direct fallback timed out for {name} after {timeout}s")
                return None
            except Exception as inner:
                _warn(f"direct fallback failed for {name}: {inner!r}")
                return None

        async def _job() -> object:
            return await original(*args, **kwargs)

        queued = await enqueue_runtime_job(
            kind=kind,
            guild_id=guild_id,
            label=label,
            factory=_job,
            timeout=timeout,
            max_queue=max_queue,
            dedupe_key=f"{name}:{member_id or 'guild'}",
        )

        if not queued:
            _warn(f"dropped queued event helper kind={kind} guild={guild_id} label={label}")

        return queued

    try:
        setattr(_queued_wrapper, "_event_safety_wrapped", True)
        setattr(_queued_wrapper, "_event_safety_original", original)
    except Exception:
        pass

    setattr(module, name, _queued_wrapper)
    return True


def _patch_events(module: Any, *, final_attempt: bool = False) -> int:
    module_name = str(getattr(module, "__name__", "") or "")
    if module_name != _TARGET_EVENTS_MODULE:
        return 0
    if module_name in _PATCHED_MODULES:
        return 0

    wrapped: list[str] = []

    if _patch_member_role_snapshot(module):
        wrapped.append("_member_role_snapshot->role_truth")

    if _patch_join_verification_failure(module):
        wrapped.append("_handle_join_verification_failure->join_removal_safety")

    for name in ("_new_sync_member_safe", "_new_mark_member_left_safe"):
        if _wrap_async_with_queue(
            module,
            name,
            kind="member_sync",
            timeout=15.0,
            max_queue=1000,
            label_prefix="member_sync",
        ):
            wrapped.append(name)

    for name in (
        "_reconcile_stale_open_verification_tickets",
        "_run_initial_member_sync_once",
        "_maybe_run_initial_member_sync_once",
        "_initial_member_sync_once",
        "_warm_invite_cache_once",
        "_maybe_warm_invite_cache_once",
        "_warm_invite_cache",
        "_run_invite_cache_warmup",
        "_resume_member_wait_timers_once",
    ):
        if _wrap_async_with_queue(
            module,
            name,
            kind="startup_event_maintenance",
            timeout=180.0,
            max_queue=50,
            label_prefix="startup_event",
        ):
            wrapped.append(name)

    if wrapped:
        _PATCHED_MODULES.add(module_name)
        _log(f"patched {module_name} queued helpers: {', '.join(wrapped)}")
        return len(wrapped)

    if final_attempt:
        existing = [name for name in _EVENT_HELPERS if hasattr(module, name)]
        _warn(
            f"final retry found no unwrapped coroutine helpers in {module_name}; "
            f"existing_matching_names={existing or 'none'}"
        )
    elif module_name not in _NOT_READY_LOGGED:
        _NOT_READY_LOGGED.add(module_name)
        _log(f"{module_name} not ready yet; event helper patch will retry")
    return 0


async def _retry_patch_events_later() -> None:
    delays = (0.25, 0.75, 1.5, 3.0, 6.0, 12.0)
    for index, delay in enumerate(delays):
        await asyncio.sleep(delay)
        try:
            events = sys.modules.get(_TARGET_EVENTS_MODULE)
            if events is None:
                continue
            if _patch_events(events, final_attempt=(index == len(delays) - 1)) > 0:
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _warn(f"delayed events patch failed: {e!r}")


def _ensure_retry_task() -> None:
    global _RETRY_TASK_STARTED
    if _RETRY_TASK_STARTED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    except Exception:
        return
    _RETRY_TASK_STARTED = True
    loop.create_task(_retry_patch_events_later(), name="event-safety-retry")


def _maybe_patch_loaded_modules() -> None:
    try:
        events = sys.modules.get(_TARGET_EVENTS_MODULE)
        if events is not None:
            wrapped = _patch_events(events)
            if wrapped <= 0 and _TARGET_EVENTS_MODULE not in _PATCHED_MODULES:
                _ensure_retry_task()
    except Exception as e:
        _warn(f"events patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        if name == _TARGET_EVENTS_MODULE:
            target = sys.modules.get(_TARGET_EVENTS_MODULE)
            if target is not None:
                wrapped = _patch_events(target)
                if wrapped <= 0 and _TARGET_EVENTS_MODULE not in _PATCHED_MODULES:
                    _ensure_retry_task()
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; event helper queue guard active")
