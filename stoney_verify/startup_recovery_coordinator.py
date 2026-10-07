from __future__ import annotations

"""Bound heavyweight startup/recovery work across guilds.

The coordinator prevents one guild from running overlapping Discord/database
recovery paths while still allowing a small number of different guilds to make
progress concurrently. State is loop-local and per-guild lock entries are
released after the final waiter exits.
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


_MAX_CONCURRENT = _env_int(
    "DANK_STARTUP_RECOVERY_MAX_CONCURRENT",
    1,
    minimum=1,
    maximum=16,
)

# Keep Discord-facing recovery dormant long enough for the Gateway, slash
# commands, persistent components, website, and health checks to prove stable.
# Recovery is catch-up work, not a prerequisite for serving live interactions.
_PROCESS_STARTED_AT = time.monotonic()
_STARTUP_RECOVERY_QUIET_SECONDS = _env_int(
    "DANK_STARTUP_RECOVERY_QUIET_SECONDS",
    120,
    minimum=30,
    maximum=600,
)
_QUIET_WAIT_LOGGED = False


@dataclass
class _GuildSlot:
    lock: asyncio.Lock
    users: int = 0


_LOOP: asyncio.AbstractEventLoop | None = None
_GLOBAL_SEMAPHORE: asyncio.Semaphore | None = None
_GUILD_SLOTS: dict[int, _GuildSlot] = {}
_CURRENT: dict[int, str] = {}
_GLOBAL_RUNNING = 0
_GLOBAL_WAITING = 0


def startup_recovery_quiet_remaining_seconds() -> float:
    elapsed = max(0.0, time.monotonic() - _PROCESS_STARTED_AT)
    return max(0.0, float(_STARTUP_RECOVERY_QUIET_SECONDS) - elapsed)


async def wait_for_startup_recovery_quiet_period() -> None:
    global _QUIET_WAIT_LOGGED

    remaining = startup_recovery_quiet_remaining_seconds()
    if remaining <= 0:
        return

    if not _QUIET_WAIT_LOGGED:
        _QUIET_WAIT_LOGGED = True
        try:
            print(
                "🧯 startup_recovery quiet window active "
                f"remaining={remaining:.1f}s "
                f"configured={_STARTUP_RECOVERY_QUIET_SECONDS}s"
            )
        except Exception:
            pass

    await asyncio.sleep(remaining)


def _ensure_loop_state() -> asyncio.Semaphore:
    global _LOOP
    global _GLOBAL_SEMAPHORE
    global _GLOBAL_RUNNING
    global _GLOBAL_WAITING

    loop = asyncio.get_running_loop()
    if _LOOP is not loop:
        _LOOP = loop
        _GLOBAL_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)
        _GUILD_SLOTS.clear()
        _CURRENT.clear()
        _GLOBAL_RUNNING = 0
        _GLOBAL_WAITING = 0
    assert _GLOBAL_SEMAPHORE is not None
    return _GLOBAL_SEMAPHORE


def startup_recovery_snapshot() -> dict[str, object]:
    return {
        "max_concurrent": _MAX_CONCURRENT,
        "quiet_seconds": _STARTUP_RECOVERY_QUIET_SECONDS,
        "quiet_remaining_seconds": round(startup_recovery_quiet_remaining_seconds(), 3),
        "running": max(0, int(_GLOBAL_RUNNING)),
        "waiting": max(0, int(_GLOBAL_WAITING)),
        "guild_slots": len(_GUILD_SLOTS),
        "current": dict(_CURRENT),
    }


@asynccontextmanager
async def startup_recovery_slot(guild_id: int, label: str) -> AsyncIterator[None]:
    """Acquire one per-guild slot plus the bounded process-wide recovery pool."""

    global _GLOBAL_RUNNING
    global _GLOBAL_WAITING

    await wait_for_startup_recovery_quiet_period()

    semaphore = _ensure_loop_state()
    gid = int(guild_id)
    safe_label = str(label or "startup-recovery")[:160]

    slot = _GUILD_SLOTS.get(gid)
    if slot is None:
        slot = _GuildSlot(lock=asyncio.Lock())
        _GUILD_SLOTS[gid] = slot
    slot.users += 1

    wait_started = time.monotonic()
    acquired_guild = False
    acquired_global = False
    try:
        await slot.lock.acquire()
        acquired_guild = True

        _GLOBAL_WAITING += 1
        try:
            await semaphore.acquire()
            acquired_global = True
        finally:
            _GLOBAL_WAITING = max(0, _GLOBAL_WAITING - 1)

        started = time.monotonic()
        _GLOBAL_RUNNING += 1
        _CURRENT[gid] = safe_label
        wait_ms = int((started - wait_started) * 1000)
        try:
            print(
                "🧯 startup_recovery slot acquired "
                f"guild={gid} label={safe_label} wait_ms={wait_ms} "
                f"running={_GLOBAL_RUNNING}/{_MAX_CONCURRENT}"
            )
        except Exception:
            pass

        try:
            yield
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _CURRENT.pop(gid, None)
            _GLOBAL_RUNNING = max(0, _GLOBAL_RUNNING - 1)
            try:
                print(
                    "🧯 startup_recovery slot released "
                    f"guild={gid} label={safe_label} elapsed_ms={elapsed_ms} "
                    f"running={_GLOBAL_RUNNING}/{_MAX_CONCURRENT}"
                )
            except Exception:
                pass
    finally:
        if acquired_global:
            semaphore.release()
        if acquired_guild and slot.lock.locked():
            slot.lock.release()

        slot.users = max(0, int(slot.users) - 1)
        if slot.users <= 0 and not slot.lock.locked():
            current = _GUILD_SLOTS.get(gid)
            if current is slot:
                _GUILD_SLOTS.pop(gid, None)


__all__ = [
    "startup_recovery_quiet_remaining_seconds",
    "startup_recovery_slot",
    "startup_recovery_snapshot",
    "wait_for_startup_recovery_quiet_period",
]
