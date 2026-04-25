from __future__ import annotations

"""
Runtime event safety patch for scale hardening.

Imported by main.py before stoney_verify.app.

This does not add slash commands. It only patches expensive event/startup sync
helpers so they are queued through stoney_verify.runtime_jobs instead of running
inline inside Discord gateway/startup paths.
"""

import asyncio
import builtins
import inspect
import sys
from typing import Any, Callable

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧯 runtime_event_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_event_safety {message}")
    except Exception:
        pass


def _guild_id_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | str | None:
    # Member-style calls: first arg is usually discord.Member.
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
            # Avoid accidentally treating a user/member id as a guild id unless the
            # object at least looks guild-like.
            if gid and hasattr(value, "members") and hasattr(value, "channels"):
                return gid
        except Exception:
            pass

    try:
        events = sys.modules.get("stoney_verify.events")
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
            # Member-like object has guild + id.
            if getattr(value, "guild", None) is not None and getattr(value, "id", None):
                return getattr(value, "id", None)
        except Exception:
            pass
    return None


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
    if getattr(original, "_runtime_event_safety_wrapped", False):
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
        )

        if not queued:
            _warn(f"dropped queued event helper kind={kind} guild={guild_id} label={label}")

        # These helpers are side-effect helpers; callers generally do not use return values.
        return queued

    try:
        setattr(_queued_wrapper, "_runtime_event_safety_wrapped", True)
        setattr(_queued_wrapper, "_runtime_event_safety_original", original)
    except Exception:
        pass

    setattr(module, name, _queued_wrapper)
    return True


def _patch_events(module: Any) -> None:
    module_name = str(getattr(module, "__name__", "") or "")
    if module_name in _PATCHED_MODULES:
        return

    wrapped: list[str] = []

    # Per-member DB sync helpers. These can fire from member join/leave/update paths.
    for name in (
        "_new_sync_member_safe",
        "_new_mark_member_left_safe",
    ):
        if _wrap_async_with_queue(
            module,
            name,
            kind="member_sync",
            timeout=15.0,
            max_queue=1000,
            label_prefix="member_sync",
        ):
            wrapped.append(name)

    # Startup / maintenance helpers that can get expensive across many guilds.
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

    _PATCHED_MODULES.add(module_name)
    if wrapped:
        _log(f"patched {module_name} queued helpers: {', '.join(wrapped)}")
    else:
        _log(f"patched {module_name}; no matching event helpers found yet")


def _maybe_patch_loaded_modules() -> None:
    try:
        events = sys.modules.get("stoney_verify.events")
        if events is not None:
            _patch_events(events)
    except Exception as e:
        _warn(f"events patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        if name == "stoney_verify.events" or name.endswith(".events"):
            target = sys.modules.get("stoney_verify.events") or sys.modules.get(name)
            if target is not None:
                _patch_events(target)
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; event helper queue guard active")
