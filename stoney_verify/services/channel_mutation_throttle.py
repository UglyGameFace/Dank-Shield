from __future__ import annotations

"""Shared pacing helpers for Discord channel mutations.

Discord channel edits can trigger long route-level 429s when many PATCH /channels
requests are fired in a burst. This service gives batch jobs a common paced lane
so setup tools, Channel Builder, permission repairs, and font renames do not
hammer Discord.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

DEFAULT_BATCH_SIZE = max(1, min(10, int(os.getenv("DANK_CHANNEL_MUTATION_BATCH_SIZE", "3") or "3")))
DEFAULT_DELAY_SECONDS = max(0.0, min(30.0, float(os.getenv("DANK_CHANNEL_MUTATION_DELAY_SECONDS", "2") or "2")))
DEFAULT_TIMEOUT_SECONDS = max(8.0, min(120.0, float(os.getenv("DANK_CHANNEL_MUTATION_TIMEOUT_SECONDS", "45") or "45")))
DEFAULT_MAX_ITEMS = max(1, min(500, int(os.getenv("DANK_CHANNEL_MUTATION_MAX_ITEMS", "150") or "150")))

_GUILD_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_EDIT_AT: dict[str, float] = {}


@dataclass
class ChannelMutationResult:
    attempted: int = 0
    changed: int = 0
    already: int = 0
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
    failures: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "changed": self.changed,
            "already": self.already,
            "skipped": self.skipped,
            "failed": self.failed,
            "remaining": self.remaining,
            "failures": list(self.failures),
            "changes": list(self.changes),
        }


def guild_lock(guild_id: int | str) -> asyncio.Lock:
    key = str(guild_id)
    lock = _GUILD_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GUILD_LOCKS[key] = lock
    return lock


def is_guild_busy(guild_id: int | str) -> bool:
    return guild_lock(guild_id).locked()


async def _pace(guild_id: int | str, *, delay_seconds: float = DEFAULT_DELAY_SECONDS) -> None:
    key = str(guild_id)
    now = time.monotonic()
    last = _LAST_EDIT_AT.get(key, 0.0)
    wait = float(delay_seconds) - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST_EDIT_AT[key] = time.monotonic()


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


async def run_paced_channel_mutations(
    *,
    guild_id: int | str,
    items: Iterable[Any],
    mutate_one: Callable[[Any], Awaitable[dict[str, Any]]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> ChannelMutationResult:
    """Run a bounded, paced set of channel mutation coroutines.

    mutate_one should return a dict with one of these statuses:
    - changed
    - already
    - skipped
    - failed

    If Discord/client code blocks on a long route-level retry, wait_for converts
    that item into a timeout failure so the job can report progress instead of
    looking frozen forever.
    """

    result = ChannelMutationResult()
    queue = list(items)[:max_items]
    result.remaining = max(0, len(list(items)) - len(queue)) if not isinstance(items, list) else max(0, len(items) - len(queue))
    batch_size = max(1, min(int(batch_size or 1), max_items))

    for item in queue[:batch_size]:
        result.attempted += 1
        await _pace(guild_id, delay_seconds=delay_seconds)
        try:
            payload = await asyncio.wait_for(mutate_one(item), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            result.failed += 1
            result.failures.append("Timed out waiting for Discord channel edit; try continuing later.")
            result.remaining += max(0, len(queue) - result.attempted)
            break
        except Exception as exc:
            result.failed += 1
            result.failures.append(f"{type(exc).__name__}: {_safe_str(exc)[:120]}")
            continue

        status = _safe_str(payload.get("status") if isinstance(payload, dict) else None, "failed")
        if status == "changed":
            result.changed += 1
            if isinstance(payload, dict):
                result.changes.append(dict(payload))
        elif status == "already":
            result.already += 1
        elif status == "skipped":
            result.skipped += 1
        else:
            result.failed += 1
            if isinstance(payload, dict) and payload.get("error"):
                result.failures.append(_safe_str(payload.get("error"))[:180])

    processed = min(result.attempted, len(queue))
    result.remaining += max(0, len(queue) - processed)
    return result


__all__ = [
    "ChannelMutationResult",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_DELAY_SECONDS",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_TIMEOUT_SECONDS",
    "guild_lock",
    "is_guild_busy",
    "run_paced_channel_mutations",
]
