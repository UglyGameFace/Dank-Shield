from __future__ import annotations

"""Normalize worker starter return values for production startup logs.

Some workers correctly create their background asyncio task but historically
returned None. app.py used that return value for startup logging, so production
logs could say a worker "was not started" and then the worker would announce it
started a moment later.

This guard keeps the worker behavior the same and only makes the starter return
its live task when one was created.
"""

import asyncio
from typing import Any

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧩 worker_start_return_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ worker_start_return_guard {message}")
    except Exception:
        pass


def _live_task(module: Any, attr_name: str) -> asyncio.Task | None:
    try:
        task = getattr(module, attr_name, None)
        if isinstance(task, asyncio.Task) and not task.done():
            return task
    except Exception:
        pass
    return None


def _wrap_starter(module: Any, func_name: str, task_attr: str) -> bool:
    original = getattr(module, func_name, None)
    if not callable(original):
        return False
    if getattr(original, "_worker_start_return_guard_wrapped", False):
        return True

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if result is not None:
            return result
        return _live_task(module, task_attr)

    try:
        setattr(wrapped, "_worker_start_return_guard_wrapped", True)
        setattr(module, func_name, wrapped)
        return True
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    patched: list[str] = []
    try:
        from stoney_verify.workers import metrics_sync_worker

        if _wrap_starter(metrics_sync_worker, "start_metrics_worker", "_METRICS_TASK"):
            patched.append("metrics_sync_worker.start_metrics_worker")
    except Exception as e:
        _warn(f"metrics worker return patch failed: {e!r}")

    try:
        from stoney_verify.workers import ticket_automation_worker

        if _wrap_starter(ticket_automation_worker, "start_ticket_automation_worker", "_TICKET_AUTOMATION_TASK"):
            patched.append("ticket_automation_worker.start_ticket_automation_worker")
    except Exception as e:
        _warn(f"ticket automation worker return patch failed: {e!r}")

    _PATCHED = True
    if patched:
        _log("patched starters=" + ", ".join(patched))
    else:
        _warn("no worker starters were patched")
    return True


apply()

__all__ = ["apply"]
