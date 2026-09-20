from __future__ import annotations

"""Serialize heavyweight startup recovery work across feature owners.

Discord history/member recovery is intentionally allowed to run in the
background, but multiple authoritative sweeps must not stampede Discord and the
database at the same time during startup or gateway resume.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

_LOCK: asyncio.Lock | None = None
_LOCK_LOOP: asyncio.AbstractEventLoop | None = None
_CURRENT_LABEL = ""
_WAITERS = 0


def _lock_for_current_loop() -> asyncio.Lock:
    global _LOCK
    global _LOCK_LOOP

    loop = asyncio.get_running_loop()
    if _LOCK is None or _LOCK_LOOP is not loop:
        _LOCK = asyncio.Lock()
        _LOCK_LOOP = loop
    return _LOCK


def startup_recovery_snapshot() -> dict[str, object]:
    lock = _LOCK
    return {
        "locked": bool(lock.locked()) if lock is not None else False,
        "current_label": _CURRENT_LABEL,
        "waiters": max(0, int(_WAITERS)),
    }


@asynccontextmanager
async def startup_recovery_slot(label: str) -> AsyncIterator[None]:
    """Run one heavyweight startup recovery section at a time."""

    global _CURRENT_LABEL
    global _WAITERS

    safe_label = str(label or "startup-recovery")[:160]
    lock = _lock_for_current_loop()
    wait_started = time.monotonic()
    _WAITERS += 1
    try:
        await lock.acquire()
    finally:
        _WAITERS = max(0, _WAITERS - 1)

    started = time.monotonic()
    _CURRENT_LABEL = safe_label
    wait_ms = int((started - wait_started) * 1000)
    try:
        print(
            "🧯 startup_recovery slot acquired "
            f"label={safe_label} wait_ms={wait_ms} waiters={_WAITERS}"
        )
    except Exception:
        pass

    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _CURRENT_LABEL = ""
        try:
            lock.release()
        finally:
            try:
                print(
                    "🧯 startup_recovery slot released "
                    f"label={safe_label} elapsed_ms={elapsed_ms}"
                )
            except Exception:
                pass


__all__ = ["startup_recovery_slot", "startup_recovery_snapshot"]
