from __future__ import annotations

"""
Process health / crash visibility guard.

This replaces the old root-level runtime_process_health_guard.py.

It logs boot count, host shutdown signals, unhandled sync/async exceptions,
process exit, periodic memory/task heartbeats, and runtime throttle settings.
It exits cleanly on host SIGTERM/SIGINT instead of swallowing shutdown signals.
"""

import atexit
import asyncio
import builtins
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_BOOT_TS = time.time()
_BOOT_STATE_PATH = Path(os.getenv("STONEY_PROCESS_BOOT_STATE", "/tmp/stoney_process_boot_state.txt"))
_HEALTH_INTERVAL_SECONDS = int(os.getenv("STONEY_PROCESS_HEALTH_INTERVAL_SECONDS", "120") or "120")
_HEALTH_TASK_STARTED = False
_READY_LISTENER_ATTACHED = False
_PREVIOUS_EXCEPTHOOK = sys.excepthook
_ORIGINAL_IMPORT = builtins.__import__
_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"🫀 process_health {message}", flush=True)
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _read_boot_state() -> tuple[int, float]:
    try:
        raw = _BOOT_STATE_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return 0, 0.0
        parts = raw.split(",", 1)
        return _safe_int(parts[0], 0), float(parts[1]) if len(parts) > 1 else 0.0
    except Exception:
        return 0, 0.0


def _write_boot_state(count: int, ts: float) -> None:
    try:
        _BOOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BOOT_STATE_PATH.write_text(f"{int(count)},{float(ts)}", encoding="utf-8")
    except Exception:
        pass


def _memory_snapshot() -> str:
    try:
        import resource

        rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss or 0)
        if rss_kb > 10_000_000:
            mb = rss_kb / (1024 * 1024)
        else:
            mb = rss_kb / 1024
        return f"rss≈{mb:.1f}MB"
    except Exception:
        return "rss=unknown"


def _runtime_limit_snapshot_text() -> str:
    try:
        from ..runtime_limits import runtime_limit_snapshot

        snap = runtime_limit_snapshot()
        return (
            "limits="
            f"discord_global:{snap.global_discord_limit},"
            f"discord_guild:{snap.per_guild_discord_limit},"
            f"db_global:{snap.global_db_limit},"
            f"db_guild:{snap.per_guild_db_limit},"
            f"active_global:{snap.active_named_limiters},"
            f"active_guild:{snap.active_guild_limiters}"
        )
    except Exception as e:
        return f"limits=unavailable:{type(e).__name__}"


def _sync_excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
    try:
        _log(f"UNHANDLED_SYNC_EXCEPTION type={getattr(exc_type, '__name__', exc_type)} error={exc!r}")
        traceback.print_exception(exc_type, exc, tb)
    except Exception:
        pass
    try:
        _PREVIOUS_EXCEPTHOOK(exc_type, exc, tb)
    except Exception:
        pass


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    try:
        message = context.get("message") or "Unhandled asyncio exception"
        exc = context.get("exception")
        task = context.get("task") or context.get("future")
        _log(f"ASYNCIO_EXCEPTION message={message!r} task={task!r} exception={exc!r}")
        if exc is not None:
            traceback.print_exception(type(exc), exc, getattr(exc, "__traceback__", None))
    except Exception:
        pass
    try:
        loop.default_exception_handler(context)
    except Exception:
        pass


def install_loop_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    try:
        loop = loop or asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
        _log("asyncio exception handler installed")
    except Exception as e:
        _log(f"failed installing asyncio exception handler: {e!r}")


def _signal_handler(signum: int, frame: Any) -> None:
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    uptime = time.time() - _BOOT_TS
    _log(
        f"SIGNAL_RECEIVED signal={name} uptime={uptime:.1f}s "
        f"{_memory_snapshot()} {_runtime_limit_snapshot_text()} exiting_cleanly=true"
    )
    raise SystemExit(128 + int(signum))


def _atexit() -> None:
    try:
        uptime = time.time() - _BOOT_TS
        _log(f"PROCESS_EXIT uptime={uptime:.1f}s {_memory_snapshot()} {_runtime_limit_snapshot_text()}")
    except Exception:
        pass


async def _health_loop() -> None:
    interval = max(30, int(_HEALTH_INTERVAL_SECONDS or 120))
    while True:
        try:
            await asyncio.sleep(interval)
            uptime = time.time() - _BOOT_TS
            loop = asyncio.get_running_loop()
            task_count = 0
            try:
                task_count = len([t for t in asyncio.all_tasks(loop) if not t.done()])
            except Exception:
                task_count = 0
            _log(
                f"heartbeat uptime={uptime:.1f}s tasks={task_count} "
                f"{_memory_snapshot()} {_runtime_limit_snapshot_text()}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log(f"health loop error: {e!r}")


def start_health_loop() -> None:
    global _HEALTH_TASK_STARTED
    if _HEALTH_TASK_STARTED:
        return
    _HEALTH_TASK_STARTED = True
    try:
        loop = asyncio.get_running_loop()
        install_loop_exception_handler(loop)
        loop.create_task(_health_loop(), name="process_health_loop")
        _log("heartbeat loop started")
    except RuntimeError:
        _HEALTH_TASK_STARTED = False
    except Exception as e:
        _HEALTH_TASK_STARTED = False
        _log(f"failed starting heartbeat loop: {e!r}")


def _attach_ready_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return
    _READY_LISTENER_ATTACHED = True

    async def _process_health_on_ready() -> None:
        try:
            start_health_loop()
            user = getattr(bot, "user", None)
            guilds = len(getattr(bot, "guilds", []) or [])
            uptime = time.time() - _BOOT_TS
            _log(
                f"on_ready health attached user={user} guilds={guilds} "
                f"uptime={uptime:.1f}s {_memory_snapshot()} {_runtime_limit_snapshot_text()}"
            )
        except Exception as e:
            _log(f"on_ready health attach failed: {e!r}")

    try:
        bot.add_listener(_process_health_on_ready, "on_ready")
        _log("on_ready heartbeat listener attached")
    except Exception as e:
        _READY_LISTENER_ATTACHED = False
        _log(f"failed attaching on_ready heartbeat listener: {e!r}")


def _maybe_attach_loaded_bot() -> None:
    try:
        for module_name in ("stoney_verify.app", "stoney_verify.globals"):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            bot = getattr(module, "bot", None)
            if bot is not None:
                _attach_ready_listener(bot)
                return
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        _maybe_attach_loaded_bot()
    except Exception:
        pass
    return module


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    sys.excepthook = _sync_excepthook
    builtins.__import__ = _safe_import

    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            signum = getattr(signal, sig_name)
            signal.signal(signum, _signal_handler)
        except Exception:
            pass

    try:
        atexit.register(_atexit)
    except Exception:
        pass

    count, last_ts = _read_boot_state()
    now = time.time()
    since = now - last_ts if last_ts else 0.0
    count += 1
    _write_boot_state(count, now)
    if last_ts:
        _log(
            f"BOOT count={count} seconds_since_previous_boot={since:.1f} "
            f"pid={os.getpid()} {_memory_snapshot()} {_runtime_limit_snapshot_text()}"
        )
    else:
        _log(
            f"BOOT count={count} first_recorded_boot pid={os.getpid()} "
            f"{_memory_snapshot()} {_runtime_limit_snapshot_text()}"
        )

    _maybe_attach_loaded_bot()


install()
_log("loaded; crash/restart visibility active")


__all__ = ["install", "install_loop_exception_handler", "start_health_loop"]
