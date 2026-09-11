from __future__ import annotations

"""
Process health / crash visibility guard.

This replaces the old root-level runtime_process_health_guard.py.

It logs boot count, host shutdown signals, unhandled sync/async exceptions,
process exit, and periodic memory/task heartbeats. It exits cleanly on host
SIGTERM/SIGINT instead of swallowing shutdown signals.
"""

import atexit
import asyncio
import builtins
import os
import signal
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

_BOOT_TS = time.time()
_BOOT_STATE_PATH = Path(os.getenv("DANK_PROCESS_BOOT_STATE", "/tmp/dank_process_boot_state.txt"))
_HEALTH_INTERVAL_SECONDS = int(os.getenv("DANK_PROCESS_HEALTH_INTERVAL_SECONDS", "120") or "120")
_HEALTH_TASK_STARTED = False
_READY_LISTENER_ATTACHED = False
_PREVIOUS_EXCEPTHOOK = sys.excepthook
_ORIGINAL_IMPORT = builtins.__import__
_INSTALLED = False
_EXTERNAL_WATCHDOG_LAST_OK_AT = 0.0
_EXTERNAL_WATCHDOG_LAST_ERROR = ""
_EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT = 0.0
_EXTERNAL_WATCHDOG_WAS_OK = False


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


def _external_watchdog_url() -> str:
    for name in (
        "DANK_HEALTHCHECKS_PING_URL",
        "HEALTHCHECKS_PING_URL",
        "HEALTHCHECK_PING_URL",
        "HC_PING_URL",
    ):
        try:
            value = str(os.getenv(name, "") or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _external_watchdog_timeout_seconds() -> float:
    try:
        raw = float(str(os.getenv("DANK_HEALTHCHECKS_TIMEOUT_SECONDS", "5") or "5").strip())
        return max(1.0, min(raw, 15.0))
    except Exception:
        return 5.0


def external_watchdog_configured() -> bool:
    return bool(_external_watchdog_url())


def external_watchdog_status() -> tuple[bool, bool, str]:
    configured = external_watchdog_configured()
    if not configured:
        return False, False, "not configured — set `DANK_HEALTHCHECKS_PING_URL`"

    if _EXTERNAL_WATCHDOG_LAST_OK_AT > 0:
        age = max(0, int(time.time() - _EXTERNAL_WATCHDOG_LAST_OK_AT))
        if _EXTERNAL_WATCHDOG_LAST_ERROR:
            return True, False, f"last success {age}s ago; latest ping failed"
        return True, True, f"pinging Healthchecks.io; last success {age}s ago"

    if _EXTERNAL_WATCHDOG_LAST_ERROR:
        return True, False, "configured; ping has not succeeded yet"
    return True, False, "configured; waiting for first successful ping"


def _ping_external_watchdog_sync() -> tuple[bool, str]:
    url = _external_watchdog_url()
    if not url:
        return False, "not_configured"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Dank-Shield/healthcheck"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_external_watchdog_timeout_seconds()) as response:
            status = int(getattr(response, "status", 200) or 200)
            if 200 <= status < 300:
                return True, f"http_{status}"
            return False, f"http_{status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}"


async def _ping_external_watchdog() -> bool:
    global _EXTERNAL_WATCHDOG_LAST_OK_AT
    global _EXTERNAL_WATCHDOG_LAST_ERROR
    global _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT
    global _EXTERNAL_WATCHDOG_WAS_OK

    if not external_watchdog_configured():
        return False

    try:
        ok, detail = await asyncio.wait_for(
            asyncio.to_thread(_ping_external_watchdog_sync),
            timeout=_external_watchdog_timeout_seconds() + 2.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        ok, detail = False, type(exc).__name__

    now = time.time()
    if ok:
        recovered = bool(_EXTERNAL_WATCHDOG_LAST_ERROR)
        _EXTERNAL_WATCHDOG_LAST_OK_AT = now
        _EXTERNAL_WATCHDOG_LAST_ERROR = ""
        if recovered or not _EXTERNAL_WATCHDOG_WAS_OK:
            _log("external watchdog ping healthy")
        _EXTERNAL_WATCHDOG_WAS_OK = True
        return True

    _EXTERNAL_WATCHDOG_LAST_ERROR = str(detail or "ping_failed")
    _EXTERNAL_WATCHDOG_WAS_OK = False
    if now - _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT >= 300:
        _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT = now
        _log(f"external watchdog ping failed error={_EXTERNAL_WATCHDOG_LAST_ERROR}")
    return False


def _proc_status_rss_mb(text: str) -> float | None:
    """Parse current resident memory from Linux /proc/self/status."""

    try:
        for line in str(text or "").splitlines():
            if not line.startswith("VmRSS:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return None
            value = float(parts[1])
            if value <= 0:
                return None
            unit = parts[2].lower() if len(parts) > 2 else "kb"
            if unit == "kb":
                return value / 1024.0
            if unit == "mb":
                return value
            if unit == "gb":
                return value * 1024.0
            return None
    except Exception:
        pass
    return None


def _current_rss_mb() -> float | None:
    """Return current resident memory where the host exposes it.

    Linux/Discloud exposes current RSS through procfs. ``ru_maxrss`` is not used
    here because it is a lifetime high-water mark and therefore cannot show
    memory being released after startup work finishes.
    """

    if sys.platform.startswith("linux"):
        try:
            value = _proc_status_rss_mb(Path("/proc/self/status").read_text(encoding="utf-8"))
            if value is not None:
                return value
        except Exception:
            pass
        try:
            fields = Path("/proc/self/statm").read_text(encoding="utf-8").split()
            resident_pages = int(fields[1]) if len(fields) > 1 else 0
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            if resident_pages > 0 and page_size > 0:
                return (resident_pages * page_size) / (1024.0 * 1024.0)
        except Exception:
            pass
    return None


def _peak_rss_mb() -> float | None:
    """Return the process lifetime RSS high-water mark when available."""

    try:
        import resource

        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss or 0)
        if raw <= 0:
            return None
        if sys.platform == "darwin":
            return raw / (1024.0 * 1024.0)
        return raw / 1024.0
    except Exception:
        return None


def _memory_snapshot() -> str:
    current = _current_rss_mb()
    peak = _peak_rss_mb()
    current_text = "unknown" if current is None else f"{current:.1f}MB"
    peak_text = "unknown" if peak is None else f"{peak:.1f}MB"
    return f"rss_current≈{current_text} rss_peak≈{peak_text}"


def _operation_queue_snapshot() -> str:
    try:
        from ..operation_queue import operation_queue_health_summary

        summary = operation_queue_health_summary()
        totals = dict(summary.get("totals") or {})
        global_state = dict(summary.get("global") or {})

        queues = int(totals.get("queues", 0) or 0)
        queued = int(totals.get("queued", 0) or 0)
        running = int(totals.get("running", 0) or 0)
        failed = int(totals.get("failed", 0) or 0)
        duplicates = int(totals.get("duplicate_hits", 0) or 0)
        busy = int(totals.get("busy_rejected", 0) or 0)

        if queues <= 0 and queued <= 0 and running <= 0 and failed <= 0 and duplicates <= 0 and busy <= 0:
            return ""

        return (
            " opq="
            f"{summary.get('status')}:{running}run/{queued}q "
            f"jobs={global_state.get('jobs_tracked')} "
            f"dup={duplicates} busy={busy} fail={failed}"
        )
    except Exception:
        return ""


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
    _log(f"SIGNAL_RECEIVED signal={name} uptime={uptime:.1f}s {_memory_snapshot()} exiting_cleanly=true")
    raise SystemExit(128 + int(signum))


def _atexit() -> None:
    try:
        uptime = time.time() - _BOOT_TS
        _log(f"PROCESS_EXIT uptime={uptime:.1f}s {_memory_snapshot()}{_operation_queue_snapshot()}")
    except Exception:
        pass


async def _health_loop() -> None:
    interval = max(30, int(_HEALTH_INTERVAL_SECONDS or 120))
    while True:
        try:
            await asyncio.sleep(interval)
            watchdog_ok = await _ping_external_watchdog() if external_watchdog_configured() else False
            uptime = time.time() - _BOOT_TS
            loop = asyncio.get_running_loop()
            task_count = 0
            try:
                task_count = len([t for t in asyncio.all_tasks(loop) if not t.done()])
            except Exception:
                task_count = 0
            watchdog_state = "ok" if watchdog_ok else ("off" if not external_watchdog_configured() else "failed")
            _log(f"heartbeat uptime={uptime:.1f}s tasks={task_count} {_memory_snapshot()}{_operation_queue_snapshot()} external_watchdog={watchdog_state}")
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
            if external_watchdog_configured():
                await _ping_external_watchdog()
            user = getattr(bot, "user", None)
            guilds = len(getattr(bot, "guilds", []) or [])
            uptime = time.time() - _BOOT_TS
            _log(f"on_ready health attached user={user} guilds={guilds} uptime={uptime:.1f}s {_memory_snapshot()}{_operation_queue_snapshot()}")
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
        _log(f"BOOT count={count} seconds_since_previous_boot={since:.1f} pid={os.getpid()} {_memory_snapshot()}")
    else:
        _log(f"BOOT count={count} first_recorded_boot pid={os.getpid()} {_memory_snapshot()}")

    _maybe_attach_loaded_bot()


install()
_log("loaded; crash/restart visibility active")


__all__ = [
    "install",
    "install_loop_exception_handler",
    "start_health_loop",
    "external_watchdog_configured",
    "external_watchdog_status",
]
