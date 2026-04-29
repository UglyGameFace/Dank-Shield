from __future__ import annotations

"""
Process health / crash visibility guard.

This does not pretend to magically prevent host-level restarts. It makes the
next shutdown obvious by logging:
- boot counter and time since last boot
- unhandled sync exceptions
- unhandled asyncio task exceptions
- SIGTERM/SIGINT received from the host
- process exit through atexit
- periodic memory/heartbeat snapshots

If Discloud kills the process without Python cleanup, you will see no atexit or
signal line, which usually means host/container kill, OOM, or deployment restart.
"""

import atexit
import asyncio
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
_PREVIOUS_EXCEPTHOOK = sys.excepthook


def _log(message: str) -> None:
    try:
        print(f"🫀 runtime_process_health {message}", flush=True)
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
        # Linux reports KB; macOS reports bytes. Discloud is Linux, but keep a
        # defensive readable fallback.
        if rss_kb > 10_000_000:
            mb = rss_kb / (1024 * 1024)
        else:
            mb = rss_kb / 1024
        return f"rss≈{mb:.1f}MB"
    except Exception:
        return "rss=unknown"


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
    _log(f"SIGNAL_RECEIVED signal={name} uptime={uptime:.1f}s {_memory_snapshot()}")


def _atexit() -> None:
    try:
        uptime = time.time() - _BOOT_TS
        _log(f"PROCESS_EXIT uptime={uptime:.1f}s {_memory_snapshot()}")
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
            _log(f"heartbeat uptime={uptime:.1f}s tasks={task_count} {_memory_snapshot()}")
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
        loop.create_task(_health_loop(), name="runtime_process_health_loop")
        _log("heartbeat loop started")
    except RuntimeError:
        # No running loop yet. main.py/on_ready can call this again later.
        _HEALTH_TASK_STARTED = False
    except Exception as e:
        _HEALTH_TASK_STARTED = False
        _log(f"failed starting heartbeat loop: {e!r}")


def install() -> None:
    sys.excepthook = _sync_excepthook

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
        _log(f"BOOT count={count} seconds_since_previous_boot={since:.1f} pid={os.getpid()} {_memory_snapshot()}")
    else:
        _log(f"BOOT count={count} first_recorded_boot pid={os.getpid()} {_memory_snapshot()}")


install()
_log("loaded; crash/restart visibility active")
