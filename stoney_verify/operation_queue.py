from __future__ import annotations

"""
Bot-wide guild operation queue.

This module is the shared safety layer for dangerous/mutating bot work:
channel creation/restyles, setup repair, ticket mutations, verification
decisions, role writes, purge execution, and future dashboard jobs.

It deliberately has two execution paths:

1. submit_operation(...): queued background jobs for dashboard/API flows that can
   poll progress by job ID.
2. run_interaction_exclusive(...): click-safe, same-interaction execution for
   Discord component callbacks that must answer immediately and should not sit
   behind a long queue.

Both paths share idempotency, operation metadata, guild/concurrency scoping,
health metrics, and optional Supabase persistence.
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

JobFactory = Callable[[], Awaitable[Any]]

_VALID_STATUSES = {
    "queued",
    "running",
    "waiting_rate_limit",
    "partial",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
}

_DEFAULT_TIMEOUT_SECONDS = 120.0
_RECENT_IDEMPOTENCY_SECONDS = 60.0


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value)
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        pass

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        return str(value)
    except Exception:
        return repr(value)


def _stable_payload(value: Any) -> str:
    try:
        return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return _safe_str(value)


def payload_hash(value: Any) -> str:
    raw = _stable_payload(value).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def make_idempotency_key(
    *,
    guild_id: int | str | None,
    actor_id: int | str | None,
    operation_type: str,
    payload: Any = None,
    custom_key: str = "",
    time_bucket_seconds: int = 10,
) -> str:
    """Build a stable key for duplicate click/submission protection."""

    supplied = _safe_str(custom_key).strip()
    if supplied:
        return supplied[:240]

    bucket_seconds = max(1, int(time_bucket_seconds or 10))
    bucket = int(time.time() // bucket_seconds)
    return (
        f"auto:{_safe_str(guild_id, 'global')}:{_safe_str(actor_id, 'system')}:"
        f"{_safe_str(operation_type, 'operation')}:{payload_hash(payload)[:24]}:{bucket}"
    )[:240]


def _concurrency_key(
    *,
    guild_id: int | str | None,
    operation_type: str,
    concurrency_class: str,
    concurrency_key: str = "",
) -> str:
    gid = _safe_str(guild_id, "global")
    op_type = _safe_str(operation_type, "operation")
    cls = _safe_str(concurrency_class, "guild")
    explicit = _safe_str(concurrency_key).strip()
    if explicit:
        return f"{gid}:{cls}:{explicit}"[:240]
    if cls in {"guild", "guild_wide", "guild_config_write", "channel_mutation"}:
        return f"{gid}:{cls}"[:240]
    return f"{gid}:{cls}:{op_type}"[:240]


@dataclass
class OperationJob:
    id: str
    guild_id: str
    actor_id: str
    operation_type: str
    risk_level: str
    source: str
    idempotency_key: str
    payload_hash: str
    concurrency_class: str
    concurrency_key: str
    status: str = "queued"
    progress_current: int = 0
    progress_total: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str = ""
    finished_at: str = ""
    last_updated_monotonic: float = field(default_factory=time.monotonic)
    factory: Optional[JobFactory] = field(default=None, repr=False, compare=False)
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 0

    def public_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "guild_id": self.guild_id,
            "actor_id": self.actor_id or None,
            "operation_type": self.operation_type,
            "risk_level": self.risk_level,
            "source": self.source,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "concurrency_class": self.concurrency_class,
            "concurrency_key": self.concurrency_key,
            "status": self.status,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "result": _jsonable(self.result or {}),
            "error_code": self.error_code or None,
            "error_message": self.error_message or None,
            "created_at": self.created_at,
            "started_at": self.started_at or None,
            "finished_at": self.finished_at or None,
        }


@dataclass
class OperationQueueStats:
    submitted: int = 0
    started: int = 0
    succeeded: int = 0
    failed: int = 0
    partial: int = 0
    cancelled: int = 0
    expired: int = 0
    duplicate_hits: int = 0
    busy_rejected: int = 0
    queued: int = 0
    running: int = 0
    waiting_global_slot: int = 0
    rate_limit_waits: int = 0
    last_operation_type: str = ""
    last_error: str = ""
    last_updated_monotonic: float = field(default_factory=time.monotonic)


class _OperationPersistence:
    """Best-effort Supabase persistence.

    The queue still works without the table. If the REST table is unavailable we
    silently degrade to in-memory operation tracking and print one clear warning.
    """

    def __init__(self) -> None:
        self._disabled_reason = ""
        self._warned = False

    @property
    def enabled(self) -> bool:
        return not bool(self._disabled_reason)

    def _disable(self, reason: str) -> None:
        self._disabled_reason = _safe_str(reason, "unknown")
        if not self._warned:
            self._warned = True
            try:
                print(
                    "⚠️ operation_queue persistence disabled "
                    f"reason={self._disabled_reason}; in-memory queue still active"
                )
            except Exception:
                pass

    def _client(self) -> Any:
        try:
            from .globals import get_supabase

            return get_supabase()
        except Exception as e:
            self._disable(f"get_supabase_failed:{type(e).__name__}")
            return None

    async def upsert_job(self, job: OperationJob) -> None:
        if not self.enabled:
            return

        payload = {
            "id": job.id,
            "guild_id": job.guild_id,
            "actor_id": job.actor_id or None,
            "operation_type": job.operation_type,
            "risk_level": job.risk_level,
            "source": job.source,
            "idempotency_key": job.idempotency_key,
            "payload_hash": job.payload_hash,
            "status": job.status,
            "progress_current": int(job.progress_current or 0),
            "progress_total": int(job.progress_total or 0),
            "result_json": _jsonable(job.result or {}),
            "error_code": job.error_code or None,
            "error_message": job.error_message or None,
            "locked_by": "bot-runtime" if job.status == "running" else None,
            "created_at": job.created_at,
            "started_at": job.started_at or None,
            "finished_at": job.finished_at or None,
        }

        def _sync() -> None:
            client = self._client()
            if client is None:
                return
            client.table("bot_operation_jobs").upsert(payload, on_conflict="guild_id,idempotency_key").execute()

        try:
            await asyncio.to_thread(_sync)
        except Exception as e:
            self._disable(f"upsert_failed:{type(e).__name__}:{str(e)[:120]}")

    async def update_job(self, job: OperationJob) -> None:
        if not self.enabled:
            return

        payload = {
            "status": job.status,
            "progress_current": int(job.progress_current or 0),
            "progress_total": int(job.progress_total or 0),
            "result_json": _jsonable(job.result or {}),
            "error_code": job.error_code or None,
            "error_message": job.error_message or None,
            "locked_by": "bot-runtime" if job.status == "running" else None,
            "started_at": job.started_at or None,
            "finished_at": job.finished_at or None,
        }

        def _sync() -> None:
            client = self._client()
            if client is None:
                return
            client.table("bot_operation_jobs").update(payload).eq("id", job.id).execute()

        try:
            await asyncio.to_thread(_sync)
        except Exception as e:
            self._disable(f"update_failed:{type(e).__name__}:{str(e)[:120]}")


class GuildOperationQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, OperationJob] = {}
        self._dedupe: dict[str, str] = {}
        self._dedupe_expires: dict[str, float] = {}
        self._queues: dict[str, asyncio.Queue[OperationJob]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._stats: dict[str, OperationQueueStats] = {}
        self._lock = asyncio.Lock()
        self._persistence = _OperationPersistence()

        self._max_global = _env_int("DANK_OPERATION_QUEUE_MAX_GLOBAL", 16, minimum=1, maximum=256)
        self._max_queue_per_key = _env_int("DANK_OPERATION_QUEUE_MAX_PER_KEY", 100, minimum=1, maximum=5000)
        self._summary_interval = _env_int("DANK_OPERATION_QUEUE_SUMMARY_SECONDS", 300, minimum=30, maximum=3600)
        self._global_semaphore: asyncio.Semaphore | None = None
        self._global_loop: asyncio.AbstractEventLoop | None = None
        self._global_running = 0
        self._global_waiting = 0
        self._summary_task: asyncio.Task[None] | None = None

    def _global(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._global_semaphore is None or self._global_loop is not loop:
            self._global_semaphore = asyncio.Semaphore(self._max_global)
            self._global_loop = loop
            self._global_running = 0
            self._global_waiting = 0
            try:
                print(
                    "🧱 operation_queue global cap active "
                    f"max_global={self._max_global} env=DANK_OPERATION_QUEUE_MAX_GLOBAL"
                )
            except Exception:
                pass
        return self._global_semaphore

    def _stats_for(self, key: str) -> OperationQueueStats:
        return self._stats.setdefault(key, OperationQueueStats())

    def _prune_dedupe(self) -> None:
        now = time.monotonic()
        expired = [key for key, until in self._dedupe_expires.items() if until <= now]
        for key in expired[:500]:
            self._dedupe_expires.pop(key, None)
            self._dedupe.pop(key, None)

    def _start_summary_logger(self) -> None:
        if self._summary_task is not None and not self._summary_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except Exception:
            return
        self._summary_task = loop.create_task(self._summary_logger(), name="operation-queue-summary")

    async def submit(
        self,
        *,
        guild_id: int | str | None,
        operation_type: str,
        factory: JobFactory,
        actor_id: int | str | None = None,
        risk_level: str = "moderate",
        source: str = "system",
        payload: Any = None,
        idempotency_key: str = "",
        concurrency_class: str = "guild",
        concurrency_key: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        progress_total: int = 0,
    ) -> OperationJob:
        self._start_summary_logger()

        gid = _safe_str(guild_id, "global")
        op_type = _safe_str(operation_type, "operation")[:120]
        actor = _safe_str(actor_id, "")
        risk = _safe_str(risk_level, "moderate")
        src = _safe_str(source, "system")
        p_hash = payload_hash(payload)
        dedupe = make_idempotency_key(
            guild_id=gid,
            actor_id=actor or "system",
            operation_type=op_type,
            payload=payload,
            custom_key=idempotency_key,
            time_bucket_seconds=30,
        )
        c_key = _concurrency_key(
            guild_id=gid,
            operation_type=op_type,
            concurrency_class=concurrency_class,
            concurrency_key=concurrency_key,
        )

        async with self._lock:
            self._prune_dedupe()
            existing_id = self._dedupe.get(f"{gid}:{dedupe}")
            if existing_id and existing_id in self._jobs:
                job = self._jobs[existing_id]
                self._stats_for(c_key).duplicate_hits += 1
                self._stats_for(c_key).last_updated_monotonic = time.monotonic()
                return job

            queue = self._queues.get(c_key)
            if queue is None:
                queue = asyncio.Queue(maxsize=self._max_queue_per_key)
                self._queues[c_key] = queue

            stats = self._stats_for(c_key)
            if queue.full():
                stats.busy_rejected += 1
                stats.last_operation_type = op_type
                stats.last_error = "queue full"
                stats.last_updated_monotonic = time.monotonic()
                raise RuntimeError(f"operation queue full for {c_key}")

            job = OperationJob(
                id=str(uuid.uuid4()),
                guild_id=gid,
                actor_id=actor,
                operation_type=op_type,
                risk_level=risk,
                source=src,
                idempotency_key=dedupe,
                payload_hash=p_hash,
                concurrency_class=_safe_str(concurrency_class, "guild"),
                concurrency_key=c_key,
                factory=factory,
                timeout_seconds=max(1.0, float(timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)),
                progress_total=max(0, int(progress_total or 0)),
            )

            self._jobs[job.id] = job
            self._dedupe[f"{gid}:{dedupe}"] = job.id
            self._dedupe_expires[f"{gid}:{dedupe}"] = time.monotonic() + _RECENT_IDEMPOTENCY_SECONDS
            queue.put_nowait(job)

            stats.submitted += 1
            stats.queued += 1
            stats.last_operation_type = op_type
            stats.last_error = ""
            stats.last_updated_monotonic = time.monotonic()

            worker = self._workers.get(c_key)
            if worker is None or worker.done():
                worker = asyncio.create_task(self._worker(c_key), name=f"operation-queue:{c_key}")
                self._workers[c_key] = worker

        await self._persistence.upsert_job(job)
        return job

    async def run_exclusive(
        self,
        *,
        guild_id: int | str | None,
        operation_type: str,
        factory: JobFactory,
        actor_id: int | str | None = None,
        risk_level: str = "dangerous",
        source: str = "discord_command",
        payload: Any = None,
        idempotency_key: str = "",
        concurrency_class: str = "guild_config_write",
        concurrency_key: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        reject_if_busy: bool = True,
    ) -> tuple[str, Any, OperationJob | None]:
        """Run now behind the same guild/concurrency lock.

        Returns (state, result, job):
        - duplicate: a recent matching operation already exists
        - busy: another operation is currently holding this lock
        - succeeded/failed/partial: operation completed
        """

        self._start_summary_logger()

        gid = _safe_str(guild_id, "global")
        op_type = _safe_str(operation_type, "operation")[:120]
        actor = _safe_str(actor_id, "")
        p_hash = payload_hash(payload)
        dedupe = make_idempotency_key(
            guild_id=gid,
            actor_id=actor or "system",
            operation_type=op_type,
            payload=payload,
            custom_key=idempotency_key,
            time_bucket_seconds=10,
        )
        c_key = _concurrency_key(
            guild_id=gid,
            operation_type=op_type,
            concurrency_class=concurrency_class,
            concurrency_key=concurrency_key,
        )

        async with self._lock:
            self._prune_dedupe()
            stats = self._stats_for(c_key)
            existing_id = self._dedupe.get(f"{gid}:{dedupe}")
            if existing_id and existing_id in self._jobs:
                stats.duplicate_hits += 1
                stats.last_operation_type = op_type
                stats.last_updated_monotonic = time.monotonic()
                return "duplicate", None, self._jobs.get(existing_id)

            lock = self._locks.get(c_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[c_key] = lock

            if reject_if_busy and lock.locked():
                stats.busy_rejected += 1
                stats.last_operation_type = op_type
                stats.last_error = "busy"
                stats.last_updated_monotonic = time.monotonic()
                return "busy", None, None

            job = OperationJob(
                id=str(uuid.uuid4()),
                guild_id=gid,
                actor_id=actor,
                operation_type=op_type,
                risk_level=_safe_str(risk_level, "dangerous"),
                source=_safe_str(source, "discord_command"),
                idempotency_key=dedupe,
                payload_hash=p_hash,
                concurrency_class=_safe_str(concurrency_class, "guild_config_write"),
                concurrency_key=c_key,
                status="queued",
                factory=factory,
                timeout_seconds=max(1.0, float(timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)),
            )
            self._jobs[job.id] = job
            self._dedupe[f"{gid}:{dedupe}"] = job.id
            self._dedupe_expires[f"{gid}:{dedupe}"] = time.monotonic() + _RECENT_IDEMPOTENCY_SECONDS
            stats.submitted += 1
            stats.last_operation_type = op_type
            stats.last_error = ""
            stats.last_updated_monotonic = time.monotonic()

        await self._persistence.upsert_job(job)

        async with lock:
            return await self._run_job_now(job)

    async def _run_job_now(self, job: OperationJob) -> tuple[str, Any, OperationJob]:
        stats = self._stats_for(job.concurrency_key)
        sem = self._global()
        wait_started = time.monotonic()
        acquired = False

        try:
            stats.waiting_global_slot += 1
            self._global_waiting += 1
            await sem.acquire()
            acquired = True
        finally:
            stats.waiting_global_slot = max(0, stats.waiting_global_slot - 1)
            self._global_waiting = max(0, self._global_waiting - 1)

        job.status = "running"
        job.started_at = _utc_now_iso()
        job.last_updated_monotonic = time.monotonic()
        stats.started += 1
        stats.running += 1
        self._global_running += 1
        await self._persistence.update_job(job)

        result: Any = None
        try:
            if job.factory is None:
                raise RuntimeError("operation job has no factory")
            result = await asyncio.wait_for(job.factory(), timeout=float(job.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS))
            job.result = _jsonable(result if isinstance(result, dict) else {"value": result})
            job.status = _safe_str(job.result.get("status"), "succeeded") if isinstance(job.result, dict) else "succeeded"
            if job.status not in _VALID_STATUSES or job.status in {"queued", "running", "waiting_rate_limit"}:
                job.status = "succeeded"
            if job.status == "partial":
                stats.partial += 1
            else:
                stats.succeeded += 1
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error_message = "Operation was cancelled"
            stats.cancelled += 1
            raise
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error_code = "timeout"
            job.error_message = f"Operation timed out after {job.timeout_seconds:.1f}s"
            stats.failed += 1
        except Exception as e:
            job.status = "failed"
            job.error_code = type(e).__name__
            job.error_message = repr(e)
            stats.failed += 1
        finally:
            job.finished_at = _utc_now_iso()
            job.last_updated_monotonic = time.monotonic()
            stats.running = max(0, stats.running - 1)
            self._global_running = max(0, self._global_running - 1)
            stats.last_operation_type = job.operation_type
            stats.last_error = job.error_message
            stats.last_updated_monotonic = time.monotonic()
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass
            await self._persistence.update_job(job)

        return job.status, result, job

    async def _worker(self, c_key: str) -> None:
        queue = self._queues[c_key]
        lock = self._locks.setdefault(c_key, asyncio.Lock())
        while True:
            job = await queue.get()
            stats = self._stats_for(c_key)
            stats.queued = max(0, stats.queued - 1)
            try:
                async with lock:
                    await self._run_job_now(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stats.failed += 1
                stats.last_error = repr(e)
                stats.last_updated_monotonic = time.monotonic()
            finally:
                queue.task_done()

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        job = self._jobs.get(_safe_str(job_id))
        return job.public_payload() if job else None

    def health_summary(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        totals = {
            "queues": 0,
            "queued": 0,
            "running": 0,
            "waiting_global_slot": 0,
            "submitted": 0,
            "started": 0,
            "succeeded": 0,
            "failed": 0,
            "partial": 0,
            "cancelled": 0,
            "expired": 0,
            "duplicate_hits": 0,
            "busy_rejected": 0,
            "rate_limit_waits": 0,
        }

        for key, stats in list(self._stats.items()):
            queue = self._queues.get(key)
            row = {
                "queue_size": queue.qsize() if queue else 0,
                "submitted": stats.submitted,
                "started": stats.started,
                "succeeded": stats.succeeded,
                "failed": stats.failed,
                "partial": stats.partial,
                "cancelled": stats.cancelled,
                "expired": stats.expired,
                "duplicate_hits": stats.duplicate_hits,
                "busy_rejected": stats.busy_rejected,
                "running": stats.running,
                "waiting_global_slot": stats.waiting_global_slot,
                "rate_limit_waits": stats.rate_limit_waits,
                "last_operation_type": stats.last_operation_type,
                "last_error": stats.last_error,
                "last_updated_seconds_ago": max(0, int(time.monotonic() - stats.last_updated_monotonic)),
            }
            snapshot[key] = row

            totals["queues"] += 1
            totals["queued"] += int(row["queue_size"] or 0)
            for name in (
                "running",
                "waiting_global_slot",
                "submitted",
                "started",
                "succeeded",
                "failed",
                "partial",
                "cancelled",
                "expired",
                "duplicate_hits",
                "busy_rejected",
                "rate_limit_waits",
            ):
                totals[name] += int(row.get(name, 0) or 0)

        if totals["failed"] or totals["expired"]:
            status = "warning"
        elif totals["queued"] or totals["running"] or totals["waiting_global_slot"]:
            status = "busy"
        else:
            status = "ok"

        hot = sorted(
            (
                {"key": key, **row}
                for key, row in snapshot.items()
                if int(row.get("queue_size", 0) or 0)
                or int(row.get("running", 0) or 0)
                or int(row.get("failed", 0) or 0)
                or int(row.get("busy_rejected", 0) or 0)
            ),
            key=lambda row: (
                int(row.get("failed", 0) or 0) * 100
                + int(row.get("busy_rejected", 0) or 0) * 10
                + int(row.get("queue_size", 0) or 0)
                + int(row.get("running", 0) or 0)
            ),
            reverse=True,
        )[:10]

        return {
            "status": status,
            "totals": totals,
            "global": {
                "max_global": self._max_global,
                "running": self._global_running,
                "waiting": self._global_waiting,
                "jobs_tracked": len(self._jobs),
                "dedupe_keys": len(self._dedupe),
                "persistence": "enabled" if self._persistence.enabled else "memory_only",
            },
            "hot_queues": hot,
        }

    async def _summary_logger(self) -> None:
        while True:
            await asyncio.sleep(float(self._summary_interval))
            try:
                summary = self.health_summary()
                totals = dict(summary.get("totals") or {})
                global_state = dict(summary.get("global") or {})
                if not totals.get("queues"):
                    continue
                if (
                    int(totals.get("queued", 0) or 0) <= 0
                    and int(totals.get("running", 0) or 0) <= 0
                    and int(totals.get("waiting_global_slot", 0) or 0) <= 0
                    and int(totals.get("failed", 0) or 0) <= 0
                    and int(totals.get("busy_rejected", 0) or 0) <= 0
                ):
                    continue
                hot_bits = []
                for row in list(summary.get("hot_queues") or [])[:3]:
                    hot_bits.append(
                        f"{row.get('key')} q={row.get('queue_size')} run={row.get('running')} "
                        f"fail={row.get('failed')} dup={row.get('duplicate_hits')} busy={row.get('busy_rejected')}"
                    )
                print(
                    "📊 operation_queue summary "
                    f"status={summary.get('status')} queues={totals.get('queues')} "
                    f"queued={totals.get('queued')} running={totals.get('running')} "
                    f"global={global_state.get('running')}/{global_state.get('max_global')} "
                    f"submitted={totals.get('submitted')} ok={totals.get('succeeded')} "
                    f"failed={totals.get('failed')} partial={totals.get('partial')} "
                    f"duplicates={totals.get('duplicate_hits')} busy={totals.get('busy_rejected')} "
                    f"persistence={global_state.get('persistence')} hot={' | '.join(hot_bits) if hot_bits else 'none'}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                try:
                    print(f"⚠️ operation_queue summary failed: {e!r}")
                except Exception:
                    pass


_MANAGER = GuildOperationQueue()


async def submit_operation(
    *,
    guild_id: int | str | None,
    operation_type: str,
    factory: JobFactory,
    actor_id: int | str | None = None,
    risk_level: str = "moderate",
    source: str = "system",
    payload: Any = None,
    idempotency_key: str = "",
    concurrency_class: str = "guild",
    concurrency_key: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    progress_total: int = 0,
) -> dict[str, Any]:
    job = await _MANAGER.submit(
        guild_id=guild_id,
        operation_type=operation_type,
        factory=factory,
        actor_id=actor_id,
        risk_level=risk_level,
        source=source,
        payload=payload,
        idempotency_key=idempotency_key,
        concurrency_class=concurrency_class,
        concurrency_key=concurrency_key,
        timeout_seconds=timeout_seconds,
        progress_total=progress_total,
    )
    return job.public_payload()


async def run_exclusive(
    *,
    guild_id: int | str | None,
    operation_type: str,
    factory: JobFactory,
    actor_id: int | str | None = None,
    risk_level: str = "dangerous",
    source: str = "discord_command",
    payload: Any = None,
    idempotency_key: str = "",
    concurrency_class: str = "guild_config_write",
    concurrency_key: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    reject_if_busy: bool = True,
) -> tuple[str, Any, dict[str, Any] | None]:
    state, result, job = await _MANAGER.run_exclusive(
        guild_id=guild_id,
        operation_type=operation_type,
        factory=factory,
        actor_id=actor_id,
        risk_level=risk_level,
        source=source,
        payload=payload,
        idempotency_key=idempotency_key,
        concurrency_class=concurrency_class,
        concurrency_key=concurrency_key,
        timeout_seconds=timeout_seconds,
        reject_if_busy=reject_if_busy,
    )
    return state, result, job.public_payload() if job else None


async def _send_interaction_message(interaction: Any, content: str) -> None:
    try:
        allowed_mentions = None
        try:
            import discord

            allowed_mentions = discord.AllowedMentions.none()
        except Exception:
            allowed_mentions = None

        response = getattr(interaction, "response", None)
        is_done = bool(response.is_done()) if response is not None and hasattr(response, "is_done") else True
        if not is_done and response is not None:
            await response.send_message(content, ephemeral=True, allowed_mentions=allowed_mentions)
            return

        followup = getattr(interaction, "followup", None)
        if followup is not None:
            await followup.send(content, ephemeral=True, allowed_mentions=allowed_mentions)
    except Exception:
        pass


async def run_interaction_exclusive(
    *,
    interaction: Any,
    operation_type: str,
    action_label: str,
    factory: JobFactory,
    fingerprint: Any = None,
    risk_level: str = "dangerous",
    source: str = "discord_command",
    concurrency_class: str = "guild_config_write",
    concurrency_key: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    actor_id = _safe_int(getattr(user, "id", 0), 0)

    state, result, _job = await run_exclusive(
        guild_id=guild_id,
        actor_id=actor_id,
        operation_type=operation_type,
        risk_level=risk_level,
        source=source,
        payload=fingerprint,
        concurrency_class=concurrency_class,
        concurrency_key=concurrency_key,
        timeout_seconds=timeout_seconds,
        reject_if_busy=True,
        factory=factory,
    )

    if state == "duplicate":
        await _send_interaction_message(
            interaction,
            f"✅ That **{action_label}** click was already handled. Blocked the duplicate tap.",
        )
        return None

    if state == "busy":
        await _send_interaction_message(
            interaction,
            f"⏳ **{action_label}** is already running for this server. Wait a moment, then refresh before pressing it again.",
        )
        return None

    if state == "failed":
        # The wrapped factory usually sends its own error. This fallback prevents
        # silent failures when a callback crashes before responding.
        try:
            if not getattr(getattr(interaction, "response", None), "is_done", lambda: True)():
                await _send_interaction_message(
                    interaction,
                    f"⚠️ **{action_label}** failed before it could finish. Check the bot logs, then try again.",
                )
        except Exception:
            pass
        return None

    return result


def get_operation_job(job_id: str) -> dict[str, Any] | None:
    return _MANAGER.get_job(job_id)


def operation_queue_health_summary() -> dict[str, Any]:
    return _MANAGER.health_summary()


async def with_retry(
    factory: JobFactory,
    *,
    attempts: int = 3,
    base_delay: float = 0.75,
    max_delay: float = 8.0,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Any:
    """Small retry helper for future Discord/API mutation jobs."""

    total = max(1, int(attempts or 1))
    delay = max(0.05, float(base_delay or 0.75))
    max_delay = max(delay, float(max_delay or 8.0))

    for index in range(total):
        try:
            return await factory()
        except retry_exceptions:
            if index >= total - 1:
                raise
            await asyncio.sleep(min(max_delay, delay))
            delay = min(max_delay, delay * 2)


__all__ = [
    "GuildOperationQueue",
    "OperationJob",
    "make_idempotency_key",
    "payload_hash",
    "submit_operation",
    "run_exclusive",
    "run_interaction_exclusive",
    "get_operation_job",
    "operation_queue_health_summary",
    "with_retry",
]
