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
- Cap global job concurrency so 100+ guild queues cannot stampede Supabase/Discord.
- Coalesce duplicate queued/running work when a dedupe key is supplied.
- Emit simple logs/stats for observability.
- Provide health summaries without needing another slash command.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

JobFactory = Callable[[], Awaitable[object]]


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 128) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


@dataclass
class RuntimeJobStats:
    enqueued: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    dropped: int = 0
    coalesced: int = 0
    running: int = 0
    waiting_global_slot: int = 0
    last_error: str = ""
    last_label: str = ""
    last_dedupe_key: str = ""
    last_elapsed_ms: int = 0
    last_wait_ms: int = 0
    last_updated_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class RuntimeJob:
    label: str
    factory: JobFactory
    timeout: float
    dedupe_key: str = ""
    created_monotonic: float = field(default_factory=time.monotonic)


class RuntimeJobManager:
    def __init__(self) -> None:
        self._queues: Dict[str, asyncio.Queue[RuntimeJob]] = {}
        self._workers: Dict[str, asyncio.Task[None]] = {}
        self._stats: Dict[str, RuntimeJobStats] = {}
        self._active_dedupe: set[str] = set()
        self._lock = asyncio.Lock()
        self._summary_task: Optional[asyncio.Task[None]] = None
        self._last_summary_monotonic: float = 0.0

        # Global backpressure. Per-guild queues prevent one server from starving
        # another; this cap prevents 100+ guild workers from stampeding upstreams.
        self._max_concurrent_jobs = _env_int(
            "STONEY_RUNTIME_JOBS_MAX_CONCURRENT",
            8,
            minimum=1,
            maximum=64,
        )
        self._global_semaphore: Optional[asyncio.Semaphore] = None
        self._global_semaphore_loop: Optional[asyncio.AbstractEventLoop] = None
        self._global_running: int = 0
        self._global_waiting: int = 0

    def _get_global_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._global_semaphore is None or self._global_semaphore_loop is not loop:
            self._global_semaphore = asyncio.Semaphore(self._max_concurrent_jobs)
            self._global_semaphore_loop = loop
            self._global_running = 0
            self._global_waiting = 0
            try:
                print(
                    "📊 runtime_jobs global concurrency cap active "
                    f"max_concurrent={self._max_concurrent_jobs} "
                    "env=STONEY_RUNTIME_JOBS_MAX_CONCURRENT"
                )
            except Exception:
                pass
        return self._global_semaphore

    def snapshot(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for key, stats in list(self._stats.items()):
            queue = self._queues.get(key)
            worker = self._workers.get(key)
            out[key] = {
                "queue_size": queue.qsize() if queue else 0,
                "queue_max_size": getattr(queue, "maxsize", 0) if queue else 0,
                "worker_done": bool(worker.done()) if worker else True,
                "enqueued": stats.enqueued,
                "completed": stats.completed,
                "failed": stats.failed,
                "timed_out": stats.timed_out,
                "dropped": stats.dropped,
                "coalesced": stats.coalesced,
                "running": stats.running,
                "waiting_global_slot": stats.waiting_global_slot,
                "last_error": stats.last_error,
                "last_label": stats.last_label,
                "last_dedupe_key": stats.last_dedupe_key,
                "last_elapsed_ms": stats.last_elapsed_ms,
                "last_wait_ms": stats.last_wait_ms,
                "last_updated_seconds_ago": max(0, int(time.monotonic() - float(stats.last_updated_monotonic or 0))),
            }
        return out

    def health_summary(self) -> Dict[str, object]:
        snapshot = self.snapshot()
        totals = {
            "queues": len(snapshot),
            "queued": 0,
            "running": 0,
            "waiting_global_slot": 0,
            "enqueued": 0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
            "dropped": 0,
            "coalesced": 0,
        }

        hot: list[dict] = []
        for key, stats in snapshot.items():
            for total_key, stat_key in (
                ("queued", "queue_size"),
                ("running", "running"),
                ("waiting_global_slot", "waiting_global_slot"),
                ("enqueued", "enqueued"),
                ("completed", "completed"),
                ("failed", "failed"),
                ("timed_out", "timed_out"),
                ("dropped", "dropped"),
                ("coalesced", "coalesced"),
            ):
                try:
                    totals[total_key] += int(stats.get(stat_key, 0) or 0)
                except Exception:
                    pass

            try:
                risk_score = (
                    int(stats.get("dropped", 0) or 0) * 1000
                    + int(stats.get("timed_out", 0) or 0) * 100
                    + int(stats.get("failed", 0) or 0) * 50
                    + int(stats.get("waiting_global_slot", 0) or 0) * 10
                    + int(stats.get("queue_size", 0) or 0)
                )
            except Exception:
                risk_score = 0

            if risk_score > 0:
                hot.append({"key": key, "risk_score": risk_score, **stats})

        hot.sort(key=lambda row: int(row.get("risk_score", 0) or 0), reverse=True)

        if totals["dropped"] > 0:
            status = "degraded"
        elif totals["timed_out"] > 0 or totals["failed"] > 0:
            status = "warning"
        elif totals["waiting_global_slot"] > 0:
            status = "busy"
        else:
            status = "ok"

        return {
            "status": status,
            "totals": totals,
            "global": {
                "max_concurrent_jobs": self._max_concurrent_jobs,
                "running": self._global_running,
                "waiting": self._global_waiting,
                "active_dedupe_keys": len(self._active_dedupe),
            },
            "hot_queues": hot[:10],
        }

    def maybe_start_summary_logger(self, *, interval_seconds: int = 300) -> None:
        if self._summary_task is not None and not self._summary_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        except Exception:
            return
        self._summary_task = loop.create_task(
            self._summary_logger(max(30, int(interval_seconds or 300))),
            name="runtime-jobs-summary-logger",
        )

    async def enqueue(
        self,
        *,
        key: str,
        label: str,
        factory: JobFactory,
        timeout: float = 5.0,
        max_queue: int = 100,
        dedupe_key: str = "",
    ) -> bool:
        safe_key = str(key or "global")
        safe_label = str(label or "runtime-job")[:160]
        safe_dedupe_key = str(dedupe_key or "").strip()[:220]
        timeout = max(0.25, float(timeout or 5.0))
        max_queue = max(1, int(max_queue or 100))

        self.maybe_start_summary_logger()

        async with self._lock:
            queue = self._queues.get(safe_key)
            if queue is None:
                queue = asyncio.Queue(maxsize=max_queue)
                self._queues[safe_key] = queue

            stats = self._stats.setdefault(safe_key, RuntimeJobStats())

            if safe_dedupe_key and safe_dedupe_key in self._active_dedupe:
                stats.coalesced += 1
                stats.last_label = safe_label
                stats.last_dedupe_key = safe_dedupe_key
                stats.last_error = "coalesced duplicate job"
                stats.last_updated_monotonic = time.monotonic()
                return True

            worker = self._workers.get(safe_key)
            if worker is None or worker.done():
                worker = asyncio.create_task(self._worker(safe_key), name=f"runtime-job-worker:{safe_key}")
                self._workers[safe_key] = worker

            if queue.full():
                stats.dropped += 1
                stats.last_label = safe_label
                stats.last_dedupe_key = safe_dedupe_key
                stats.last_error = "queue full"
                stats.last_updated_monotonic = time.monotonic()
                try:
                    print(f"⚠️ runtime_jobs drop key={safe_key} label={safe_label} reason=queue_full size={queue.qsize()}")
                except Exception:
                    pass
                return False

            if safe_dedupe_key:
                self._active_dedupe.add(safe_dedupe_key)

            queue.put_nowait(RuntimeJob(label=safe_label, factory=factory, timeout=timeout, dedupe_key=safe_dedupe_key))
            stats.enqueued += 1
            stats.last_label = safe_label
            stats.last_dedupe_key = safe_dedupe_key
            stats.last_updated_monotonic = time.monotonic()
            return True

    async def _worker(self, key: str) -> None:
        queue = self._queues[key]
        stats = self._stats.setdefault(key, RuntimeJobStats())

        while True:
            job = await queue.get()
            sem = self._get_global_semaphore()
            wait_started = time.monotonic()
            acquired = False

            stats.last_label = job.label
            stats.last_dedupe_key = job.dedupe_key
            stats.last_updated_monotonic = wait_started

            try:
                stats.waiting_global_slot += 1
                self._global_waiting += 1
                await sem.acquire()
                acquired = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stats.failed += 1
                stats.last_error = f"global semaphore acquire failed: {e!r}"
                try:
                    print(f"⚠️ runtime_jobs failed acquiring global slot key={key} label={job.label} error={e!r}")
                except Exception:
                    pass
                if job.dedupe_key:
                    self._active_dedupe.discard(job.dedupe_key)
                queue.task_done()
                continue
            finally:
                stats.waiting_global_slot = max(0, stats.waiting_global_slot - 1)
                self._global_waiting = max(0, self._global_waiting - 1)

            started = time.monotonic()
            wait_ms = int((started - wait_started) * 1000)
            stats.last_wait_ms = wait_ms
            stats.running += 1
            self._global_running += 1
            stats.last_updated_monotonic = started

            if wait_ms > 1000:
                try:
                    print(
                        f"📊 runtime_jobs waited_for_global_slot key={key} "
                        f"label={job.label} wait_ms={wait_ms} "
                        f"running={self._global_running}/{self._max_concurrent_jobs}"
                    )
                except Exception:
                    pass

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
                self._global_running = max(0, self._global_running - 1)
                stats.last_updated_monotonic = time.monotonic()
                if job.dedupe_key:
                    self._active_dedupe.discard(job.dedupe_key)
                if acquired:
                    try:
                        sem.release()
                    except Exception:
                        pass
                queue.task_done()

    async def _summary_logger(self, interval_seconds: int) -> None:
        while True:
            await asyncio.sleep(float(interval_seconds))
            try:
                summary = self.health_summary()
                totals = dict(summary.get("totals") or {})
                global_state = dict(summary.get("global") or {})
                hot = list(summary.get("hot_queues") or [])

                if not totals.get("queues"):
                    continue

                # Avoid noisy logs when everything is completely idle.
                if (
                    int(totals.get("queued", 0) or 0) <= 0
                    and int(totals.get("running", 0) or 0) <= 0
                    and int(totals.get("waiting_global_slot", 0) or 0) <= 0
                    and int(totals.get("failed", 0) or 0) <= 0
                    and int(totals.get("timed_out", 0) or 0) <= 0
                    and int(totals.get("dropped", 0) or 0) <= 0
                ):
                    continue

                hot_bits = []
                for row in hot[:3]:
                    hot_bits.append(
                        f"{row.get('key')} q={row.get('queue_size')} "
                        f"wait={row.get('waiting_global_slot')} "
                        f"to={row.get('timed_out')} fail={row.get('failed')} drop={row.get('dropped')} coal={row.get('coalesced')}"
                    )

                print(
                    "📊 runtime_jobs summary "
                    f"status={summary.get('status')} "
                    f"queues={totals.get('queues')} queued={totals.get('queued')} "
                    f"running={totals.get('running')} waiting={totals.get('waiting_global_slot')} "
                    f"global={global_state.get('running')}/{global_state.get('max_concurrent_jobs')} "
                    f"active_dedupe={global_state.get('active_dedupe_keys')} "
                    f"done={totals.get('completed')} timeout={totals.get('timed_out')} "
                    f"failed={totals.get('failed')} dropped={totals.get('dropped')} coalesced={totals.get('coalesced')} "
                    f"hot={' | '.join(hot_bits) if hot_bits else 'none'}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                try:
                    print(f"⚠️ runtime_jobs summary logger failed: {e!r}")
                except Exception:
                    pass


_MANAGER = RuntimeJobManager()


def runtime_job_stats() -> Dict[str, dict]:
    return _MANAGER.snapshot()


def runtime_job_health_summary() -> Dict[str, object]:
    return _MANAGER.health_summary()


def start_runtime_job_summary_logger(*, interval_seconds: int = 300) -> None:
    _MANAGER.maybe_start_summary_logger(interval_seconds=interval_seconds)


async def enqueue_runtime_job(
    *,
    kind: str,
    guild_id: int | str | None,
    label: str,
    factory: JobFactory,
    timeout: float = 5.0,
    max_queue: int = 100,
    dedupe_key: str = "",
) -> bool:
    gid = str(guild_id or "global")
    key = f"{kind}:{gid}"
    safe_dedupe_key = str(dedupe_key or "").strip()
    if safe_dedupe_key:
        full_dedupe_key = f"{key}:{safe_dedupe_key}"
    else:
        full_dedupe_key = ""

    return await _MANAGER.enqueue(
        key=key,
        label=label,
        factory=factory,
        timeout=timeout,
        max_queue=max_queue,
        dedupe_key=full_dedupe_key,
    )


__all__ = [
    "enqueue_runtime_job",
    "runtime_job_stats",
    "runtime_job_health_summary",
    "start_runtime_job_summary_logger",
    "RuntimeJobManager",
]
