from __future__ import annotations

"""
Runtime job manager for scale hardening.

This is intentionally lightweight and dependency-free. It gives the bot a bounded,
per-guild background queue for non-critical work so Discord gateway events and
interaction callbacks can return quickly.

Design goals:
- Never block the Discord event loop waiting for optional work.
- Bound memory with max queue size.
- Per-guild keys so one noisy guild does not starve everyone else.
- Timebox every background job.
- Emit simple logs/stats for observability.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

JobFactory = Callable[[], Awaitable[object]]


@dataclass
class RuntimeJobStats:
    enqueued: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    dropped: int = 0
    running: int = 0
    last_error: str = ""
    last_label: str = ""
    last_elapsed_ms: int = 0
    last_updated_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class RuntimeJob:
    label: str
    factory: JobFactory
    timeout: float
    created_monotonic: float = field(default_factory=time.monotonic)


class RuntimeJobManager:
    def __init__(self) -> None:
        self._queues: Dict[str, asyncio.Queue[RuntimeJob]] = {}
        self._workers: Dict[str, asyncio.Task[None]] = {}
        self._stats: Dict[str, RuntimeJobStats] = {}
        self._lock = asyncio.Lock()

    def snapshot(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for key, stats in list(self._stats.items()):
            queue = self._queues.get(key)
            worker = self._workers.get(key)
            out[key] = {
                "queue_size": queue.qsize() if queue else 0,
                "worker_done": bool(worker.done()) if worker else True,
                "enqueued": stats.enqueued,
                "completed": stats.completed,
                "failed": stats.failed,
                "timed_out": stats.timed_out,
                "dropped": stats.dropped,
                "running": stats.running,
                "last_error": stats.last_error,
                "last_label": stats.last_label,
                "last_elapsed_ms": stats.last_elapsed_ms,
            }
        return out

    async def enqueue(
        self,
        *,
        key: str,
        label: str,
        factory: JobFactory,
        timeout: float = 5.0,
        max_queue: int = 100,
    ) -> bool:
        safe_key = str(key or "global")
        safe_label = str(label or "runtime-job")[:160]
        timeout = max(0.25, float(timeout or 5.0))
        max_queue = max(1, int(max_queue or 100))

        async with self._lock:
            queue = self._queues.get(safe_key)
            if queue is None:
                queue = asyncio.Queue(maxsize=max_queue)
                self._queues[safe_key] = queue

            stats = self._stats.setdefault(safe_key, RuntimeJobStats())

            worker = self._workers.get(safe_key)
            if worker is None or worker.done():
                worker = asyncio.create_task(self._worker(safe_key), name=f"runtime-job-worker:{safe_key}")
                self._workers[safe_key] = worker

            if queue.full():
                stats.dropped += 1
                stats.last_label = safe_label
                stats.last_error = "queue full"
                stats.last_updated_monotonic = time.monotonic()
                try:
                    print(f"⚠️ runtime_jobs drop key={safe_key} label={safe_label} reason=queue_full size={queue.qsize()}")
                except Exception:
                    pass
                return False

            queue.put_nowait(RuntimeJob(label=safe_label, factory=factory, timeout=timeout))
            stats.enqueued += 1
            stats.last_label = safe_label
            stats.last_updated_monotonic = time.monotonic()
            return True

    async def _worker(self, key: str) -> None:
        queue = self._queues[key]
        stats = self._stats.setdefault(key, RuntimeJobStats())

        while True:
            job = await queue.get()
            started = time.monotonic()
            stats.running += 1
            stats.last_label = job.label
            stats.last_updated_monotonic = started

            try:
                await asyncio.wait_for(job.factory(), timeout=job.timeout)
                stats.completed += 1
                stats.last_error = ""
            except asyncio.TimeoutError:
                stats.timed_out += 1
                stats.last_error = f"timeout after {job.timeout:.2f}s"
                try:
                    print(f"⚠️ runtime_jobs timeout key={key} label={job.label} timeout={job.timeout:.2f}s")
                except Exception:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stats.failed += 1
                stats.last_error = repr(e)
                try:
                    print(f"⚠️ runtime_jobs failed key={key} label={job.label} error={e!r}")
                except Exception:
                    pass
            finally:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                stats.last_elapsed_ms = elapsed_ms
                stats.running = max(0, stats.running - 1)
                stats.last_updated_monotonic = time.monotonic()
                queue.task_done()


_MANAGER = RuntimeJobManager()


def runtime_job_stats() -> Dict[str, dict]:
    return _MANAGER.snapshot()


async def enqueue_runtime_job(
    *,
    kind: str,
    guild_id: int | str | None,
    label: str,
    factory: JobFactory,
    timeout: float = 5.0,
    max_queue: int = 100,
) -> bool:
    gid = str(guild_id or "global")
    key = f"{kind}:{gid}"
    return await _MANAGER.enqueue(
        key=key,
        label=label,
        factory=factory,
        timeout=timeout,
        max_queue=max_queue,
    )


__all__ = [
    "enqueue_runtime_job",
    "runtime_job_stats",
    "RuntimeJobManager",
]
