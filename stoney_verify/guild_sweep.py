"""Bounded, single-process fan-out for periodic guild maintenance.

Unlike creating one asyncio task per guild, this keeps only `concurrency`
workers live, consumes each guild exactly once, and lets cancellation propagate.
It does not replace the startup recovery coordinator or Discord REST limiter.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable, Iterable


@dataclass(frozen=True)
class GuildSweepResult:
    total: int
    succeeded: int
    failed: int
    seconds: float
    concurrency: int


async def run_bounded_guild_sweep(
    guilds: Iterable[Any],
    handler: Callable[[Any], Awaitable[bool | None]],
    *,
    concurrency: int,
    on_error: Callable[[Any, Exception], None] | None = None,
) -> GuildSweepResult:
    """Process a snapshot using at most `concurrency` async workers.

    A handler returning False counts as a failure. Exceptions are isolated per
    guild, except CancelledError, which always cancels the entire sweep.
    """
    pending = deque(guilds)
    total = len(pending)
    if not total:
        return GuildSweepResult(0, 0, 0, 0.0, 0)

    workers = min(total, max(1, int(concurrency)))
    succeeded = 0
    failed = 0
    started = time.monotonic()

    async def work() -> None:
        nonlocal succeeded, failed
        while pending:
            guild = pending.popleft()
            try:
                result = await handler(guild)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed += 1
                if on_error is not None:
                    try:
                        on_error(guild, exc)
                    except Exception:
                        pass
            else:
                if result is False:
                    failed += 1
                else:
                    succeeded += 1

    tasks = [asyncio.create_task(work()) for _ in range(workers)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return GuildSweepResult(
        total=total,
        succeeded=succeeded,
        failed=failed,
        seconds=time.monotonic() - started,
        concurrency=workers,
    )
