from __future__ import annotations

"""
Shared runtime throttling utilities for Stoney Verify.

Why this exists:
- Public bots cannot let every feature invent its own sleeps/semaphores.
- Discord and Supabase pressure must be bounded globally and per guild.
- Startup jobs must be jittered so 10k guilds do not stampede at once.

This module is intentionally dependency-light and safe to import anywhere.
It does not replace discord.py's internal route buckets; it reduces the bot's
own burst pressure before requests hit discord.py/Supabase.
"""

import asyncio
import os
import random
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Deque, Dict, Hashable, Optional, TypeVar

T = TypeVar("T")


# ============================================================
# Env helpers
# ============================================================

def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return int(default)
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return float(default)
        return float(str(raw).strip())
    except Exception:
        return float(default)


# ============================================================
# Semaphore pools
# ============================================================

@dataclass(frozen=True)
class RuntimeLimitSnapshot:
    global_discord_limit: int
    global_db_limit: int
    per_guild_discord_limit: int
    per_guild_db_limit: int
    active_named_limiters: int
    active_guild_limiters: int


_GLOBAL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}
_GUILD_SEMAPHORES: Dict[tuple[str, str], asyncio.Semaphore] = {}

_SEMAPHORE_LAST_USED: Dict[Hashable, float] = {}
_CLEANUP_INTERVAL_SECONDS = 600.0
_LAST_CLEANUP_AT = 0.0


def _global_discord_limit() -> int:
    return max(1, min(_env_int("STONEY_GLOBAL_DISCORD_CONCURRENCY", 20), 200))


def _global_db_limit() -> int:
    return max(1, min(_env_int("STONEY_GLOBAL_DB_CONCURRENCY", 12), 100))


def _per_guild_discord_limit() -> int:
    return max(1, min(_env_int("STONEY_PER_GUILD_DISCORD_CONCURRENCY", 2), 20))


def _per_guild_db_limit() -> int:
    return max(1, min(_env_int("STONEY_PER_GUILD_DB_CONCURRENCY", 2), 20))


def _cleanup_stale_limiters_if_needed() -> None:
    global _LAST_CLEANUP_AT

    now = time.monotonic()
    if now - _LAST_CLEANUP_AT < _CLEANUP_INTERVAL_SECONDS:
        return
    _LAST_CLEANUP_AT = now

    cutoff = now - _CLEANUP_INTERVAL_SECONDS

    try:
        for key in list(_GUILD_SEMAPHORES.keys()):
            last = _SEMAPHORE_LAST_USED.get(("guild", key), 0.0)
            if last and last < cutoff:
                _GUILD_SEMAPHORES.pop(key, None)
                _SEMAPHORE_LAST_USED.pop(("guild", key), None)
    except Exception:
        pass


def _global_semaphore(name: str, limit: int) -> asyncio.Semaphore:
    _cleanup_stale_limiters_if_needed()
    key = str(name)
    sem = _GLOBAL_SEMAPHORES.get(key)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(limit)))
        _GLOBAL_SEMAPHORES[key] = sem
    _SEMAPHORE_LAST_USED[("global", key)] = time.monotonic()
    return sem


def _guild_semaphore(name: str, guild_id: int | str, limit: int) -> asyncio.Semaphore:
    _cleanup_stale_limiters_if_needed()
    key = (str(name), str(guild_id or "0"))
    sem = _GUILD_SEMAPHORES.get(key)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(limit)))
        _GUILD_SEMAPHORES[key] = sem
    _SEMAPHORE_LAST_USED[("guild", key)] = time.monotonic()
    return sem


@asynccontextmanager
async def discord_global_limit(label: str = "discord") -> AsyncIterator[None]:
    sem = _global_semaphore(f"discord:{label}", _global_discord_limit())
    async with sem:
        yield


@asynccontextmanager
async def discord_guild_limit(guild_id: int | str, label: str = "discord") -> AsyncIterator[None]:
    global_sem = _global_semaphore(f"discord:{label}", _global_discord_limit())
    guild_sem = _guild_semaphore(f"discord:{label}", guild_id, _per_guild_discord_limit())
    async with global_sem:
        async with guild_sem:
            yield


@asynccontextmanager
async def db_global_limit(label: str = "db") -> AsyncIterator[None]:
    sem = _global_semaphore(f"db:{label}", _global_db_limit())
    async with sem:
        yield


@asynccontextmanager
async def db_guild_limit(guild_id: int | str, label: str = "db") -> AsyncIterator[None]:
    global_sem = _global_semaphore(f"db:{label}", _global_db_limit())
    guild_sem = _guild_semaphore(f"db:{label}", guild_id, _per_guild_db_limit())
    async with global_sem:
        async with guild_sem:
            yield


# ============================================================
# Sliding window limiter
# ============================================================

class SlidingWindowLimiter:
    def __init__(self, *, max_calls: int, period_seconds: float, name: str = "limiter") -> None:
        self.max_calls = max(1, int(max_calls))
        self.period_seconds = max(0.1, float(period_seconds))
        self.name = name
        self._events: Deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                cutoff = now - self.period_seconds
                while self._events and self._events[0] <= cutoff:
                    self._events.popleft()

                if len(self._events) < self.max_calls:
                    self._events.append(now)
                    return

                sleep_for = max(0.05, self.period_seconds - (now - self._events[0]))
                await asyncio.sleep(sleep_for)


_WINDOW_LIMITERS: Dict[str, SlidingWindowLimiter] = {}


def get_window_limiter(name: str, *, max_calls: int, period_seconds: float) -> SlidingWindowLimiter:
    key = str(name)
    limiter = _WINDOW_LIMITERS.get(key)
    if limiter is None:
        limiter = SlidingWindowLimiter(max_calls=max_calls, period_seconds=period_seconds, name=key)
        _WINDOW_LIMITERS[key] = limiter
    return limiter


async def wait_window(name: str, *, max_calls: int, period_seconds: float) -> None:
    limiter = get_window_limiter(name, max_calls=max_calls, period_seconds=period_seconds)
    await limiter.wait()


# ============================================================
# Jitter / retries
# ============================================================

async def jitter_sleep(
    *,
    base_seconds: float = 0.0,
    max_jitter_seconds: Optional[float] = None,
    guild_id: Optional[int | str] = None,
) -> None:
    max_jitter = max_jitter_seconds
    if max_jitter is None:
        max_jitter = _env_float("STONEY_STARTUP_JITTER_MAX_SECONDS", 3.0)

    base = max(0.0, float(base_seconds or 0.0))
    jitter = random.uniform(0.0, max(0.0, float(max_jitter or 0.0)))

    # Stable-ish extra spread by guild id so all guilds do not align after restarts.
    if guild_id is not None:
        try:
            gid_int = int(str(guild_id))
            jitter += (gid_int % 1000) / 1000.0
        except Exception:
            pass

    delay = base + jitter
    if delay > 0:
        await asyncio.sleep(delay)


def retry_after_from_exception(exc: BaseException) -> Optional[float]:
    for attr in ("retry_after", "retry_after_seconds"):
        try:
            value = getattr(exc, attr, None)
            if value is not None:
                parsed = float(value)
                if parsed >= 0:
                    return parsed
        except Exception:
            pass

    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value is not None:
            parsed = float(value)
            if parsed >= 0:
                return parsed
    except Exception:
        pass

    return None


def is_retryable_runtime_error(exc: BaseException) -> bool:
    text = repr(exc).lower()
    if retry_after_from_exception(exc) is not None:
        return True
    return any(
        marker in text
        for marker in (
            "429",
            "rate limited",
            "ratelimited",
            "too many requests",
            "temporarily unavailable",
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "server disconnected",
            "remoteprotocolerror",
            "localprotocolerror",
            "broken pipe",
            "readerror",
            "httpx",
            "httpcore",
        )
    )


async def run_with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    label: str,
    attempts: int = 5,
    base_delay_seconds: float = 0.35,
    max_delay_seconds: float = 8.0,
) -> T:
    last_error: Optional[BaseException] = None

    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            return await op()
        except BaseException as exc:
            last_error = exc
            if attempt >= attempts or not is_retryable_runtime_error(exc):
                raise

            retry_after = retry_after_from_exception(exc)
            if retry_after is None:
                retry_after = min(max_delay_seconds, base_delay_seconds * (2 ** max(0, attempt - 1)))
            retry_after = max(0.05, min(float(retry_after), float(max_delay_seconds)))
            await jitter_sleep(base_seconds=retry_after, max_jitter_seconds=0.25)

    if last_error:
        raise last_error
    raise RuntimeError(f"{label} failed without captured exception")


# ============================================================
# Convenience wrappers
# ============================================================

async def limited_discord_call(
    guild_id: int | str,
    op: Callable[[], Awaitable[T]],
    *,
    label: str = "discord_call",
    attempts: int = 4,
) -> T:
    async def _inner() -> T:
        async with discord_guild_limit(guild_id, label=label):
            return await op()

    return await run_with_retries(_inner, label=label, attempts=attempts)


async def limited_db_call(
    guild_id: int | str,
    op: Callable[[], Awaitable[T]],
    *,
    label: str = "db_call",
    attempts: int = 5,
) -> T:
    async def _inner() -> T:
        async with db_guild_limit(guild_id, label=label):
            return await op()

    return await run_with_retries(_inner, label=label, attempts=attempts)


def runtime_limit_snapshot() -> RuntimeLimitSnapshot:
    return RuntimeLimitSnapshot(
        global_discord_limit=_global_discord_limit(),
        global_db_limit=_global_db_limit(),
        per_guild_discord_limit=_per_guild_discord_limit(),
        per_guild_db_limit=_per_guild_db_limit(),
        active_named_limiters=len(_GLOBAL_SEMAPHORES) + len(_WINDOW_LIMITERS),
        active_guild_limiters=len(_GUILD_SEMAPHORES),
    )


__all__ = [
    "RuntimeLimitSnapshot",
    "SlidingWindowLimiter",
    "db_global_limit",
    "db_guild_limit",
    "discord_global_limit",
    "discord_guild_limit",
    "get_window_limiter",
    "is_retryable_runtime_error",
    "jitter_sleep",
    "limited_db_call",
    "limited_discord_call",
    "retry_after_from_exception",
    "run_with_retries",
    "runtime_limit_snapshot",
    "wait_window",
]
