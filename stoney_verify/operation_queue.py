from __future__ import annotations

"""Canonical bot-wide operation queue for mutating Dank Shield work.

The queue is intentionally shared by Discord interactions and the structured API.
It provides guild/concurrency scoping, idempotency, persistent job status, restart
reconciliation, cancellation, global/guild/type backpressure, metrics, audit
records, and retry helpers for individual Discord/API calls.

Do not retry an entire multi-step mutation after partial success. Use ``with_retry``
around the individual idempotent Discord/API call that is safe to retry.
"""

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

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
_TERMINAL_STATUSES = {"partial", "succeeded", "failed", "cancelled", "expired"}
_ACTIVE_STATUSES = {"queued", "running", "waiting_rate_limit"}
_DEFAULT_TIMEOUT_SECONDS = 120.0
_RECENT_IDEMPOTENCY_SECONDS = 60.0
_DEFAULT_LOCK_TTL_SECONDS = 900
_SCHEMA_CACHE_RETRY_SECONDS = 90.0


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _future_iso(seconds: float) -> str:
    return (_utc_now() + timedelta(seconds=max(1.0, float(seconds)))).isoformat()


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
    return hashlib.sha256(_stable_payload(value).encode("utf-8", errors="replace")).hexdigest()


def make_idempotency_key(
    *,
    guild_id: int | str | None,
    actor_id: int | str | None,
    operation_type: str,
    payload: Any = None,
    custom_key: str = "",
    time_bucket_seconds: int = 10,
) -> str:
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


def _is_explicitly_scoped_concurrency(concurrency_class: str) -> bool:
    return _safe_str(concurrency_class).strip().lower() in {
        "ticket_channel_mutation",
        "member_role_mutation",
        "scan_readonly",
        "member_sync",
    }


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
    lock_expires_at: str = ""
    factory: Optional[JobFactory] = field(default=None, repr=False, compare=False)
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    supports_cancellation: bool = False
    cancellation_requested: bool = False
    last_updated_monotonic: float = field(default_factory=time.monotonic)

    def public_payload(self) -> dict[str, Any]:
        result = dict(self.result or {})
        if self.cancellation_requested:
            result.setdefault("cancellation_requested", True)
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
            "result": _jsonable(result),
            "error_code": self.error_code or None,
            "error_message": self.error_message or None,
            "created_at": self.created_at,
            "started_at": self.started_at or None,
            "finished_at": self.finished_at or None,
            "lock_expires_at": self.lock_expires_at or None,
            "supports_cancellation": bool(self.supports_cancellation),
        }

    @classmethod
    def from_persisted(cls, row: dict[str, Any]) -> "OperationJob":
        result = row.get("result_json") if isinstance(row.get("result_json"), dict) else {}
        return cls(
            id=_safe_str(row.get("id"), str(uuid.uuid4())),
            guild_id=_safe_str(row.get("guild_id"), "global"),
            actor_id=_safe_str(row.get("actor_id"), ""),
            operation_type=_safe_str(row.get("operation_type"), "operation"),
            risk_level=_safe_str(row.get("risk_level"), "moderate"),
            source=_safe_str(row.get("source"), "system"),
            idempotency_key=_safe_str(row.get("idempotency_key"), ""),
            payload_hash=_safe_str(row.get("payload_hash"), ""),
            concurrency_class=_safe_str(result.get("concurrency_class"), "persisted"),
            concurrency_key=_safe_str(result.get("concurrency_key"), "persisted"),
            status=_safe_str(row.get("status"), "failed"),
            progress_current=_safe_int(row.get("progress_current"), 0),
            progress_total=_safe_int(row.get("progress_total"), 0),
            result=dict(result),
            error_code=_safe_str(row.get("error_code"), ""),
            error_message=_safe_str(row.get("error_message"), ""),
            created_at=_safe_str(row.get("created_at"), _utc_now_iso()),
            started_at=_safe_str(row.get("started_at"), ""),
            finished_at=_safe_str(row.get("finished_at"), ""),
            lock_expires_at=_safe_str(row.get("lock_expires_at"), ""),
            factory=None,
        )


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
    rate_limit_waits: int = 0
    retry_count: int = 0
    stale_recoveries: int = 0
    queued: int = 0
    running: int = 0
    waiting_global_slot: int = 0
    duration_total_seconds: float = 0.0
    duration_samples: int = 0
    last_operation_type: str = ""
    last_error: str = ""
    last_updated_monotonic: float = field(default_factory=time.monotonic)


class _OperationPersistence:
    def __init__(self) -> None:
        self._disabled_reason = ""
        self._retry_after_monotonic = 0.0
        self._warned = False

    def _client(self) -> Any:
        try:
            from .globals import get_supabase
            return get_supabase()
        except Exception:
            return None

    def _schema_cache_lag(self, reason: Any) -> bool:
        text = _safe_str(reason).lower()
        return "pgrst205" in text or "schema cache" in text or ("bot_operation_jobs" in text and "could not find" in text)

    def _disable(self, reason: str) -> None:
        self._disabled_reason = _safe_str(reason, "unknown")
        if self._schema_cache_lag(reason):
            self._retry_after_monotonic = time.monotonic() + _SCHEMA_CACHE_RETRY_SECONDS
        if not self._warned:
            self._warned = True
            print(f"⚠️ operation_queue persistence temporarily unavailable reason={self._disabled_reason}; in-memory safety remains active")

    def _ready(self) -> bool:
        if not self._disabled_reason:
            return True
        if self._retry_after_monotonic and time.monotonic() >= self._retry_after_monotonic:
            self._disabled_reason = ""
            self._retry_after_monotonic = 0.0
            self._warned = False
            return True
        return False

    @property
    def enabled(self) -> bool:
        return self._ready()

    def _row(self, job: OperationJob) -> dict[str, Any]:
        result = dict(job.result or {})
        result.setdefault("concurrency_class", job.concurrency_class)
        result.setdefault("concurrency_key", job.concurrency_key)
        if job.cancellation_requested:
            result["cancellation_requested"] = True
        return {
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
            "result_json": _jsonable(result),
            "error_code": job.error_code or None,
            "error_message": job.error_message or None,
            "locked_by": "bot-runtime" if job.status == "running" else None,
            "lock_expires_at": job.lock_expires_at or None,
            "created_at": job.created_at,
            "started_at": job.started_at or None,
            "finished_at": job.finished_at or None,
        }

    async def upsert_job(self, job: OperationJob) -> None:
        if not self._ready():
            return
        def sync() -> None:
            client = self._client()
            if client is None:
                raise RuntimeError("Supabase unavailable")
            client.table("bot_operation_jobs").upsert(self._row(job), on_conflict="guild_id,idempotency_key").execute()
        try:
            await asyncio.to_thread(sync)
        except Exception as exc:
            self._disable(f"upsert_failed:{type(exc).__name__}:{str(exc)[:160]}")

    async def update_job(self, job: OperationJob) -> None:
        if not self._ready():
            return
        payload = self._row(job)
        payload.pop("id", None)
        payload.pop("guild_id", None)
        payload.pop("idempotency_key", None)
        payload.pop("created_at", None)
        def sync() -> None:
            client = self._client()
            if client is None:
                raise RuntimeError("Supabase unavailable")
            client.table("bot_operation_jobs").update(payload).eq("id", job.id).execute()
        try:
            await asyncio.to_thread(sync)
        except Exception as exc:
            self._disable(f"update_failed:{type(exc).__name__}:{str(exc)[:160]}")

    async def fetch_job(self, job_id: str) -> dict[str, Any] | None:
        if not self._ready():
            return None
        def sync() -> dict[str, Any] | None:
            client = self._client()
            if client is None:
                return None
            response = client.table("bot_operation_jobs").select("*").eq("id", _safe_str(job_id)).limit(1).execute()
            rows = getattr(response, "data", None) or []
            return dict(rows[0]) if rows else None
        try:
            return await asyncio.to_thread(sync)
        except Exception as exc:
            self._disable(f"fetch_failed:{type(exc).__name__}:{str(exc)[:160]}")
            return None

    async def fetch_duplicate(self, guild_id: str, idempotency_key: str) -> dict[str, Any] | None:
        if not self._ready():
            return None
        def sync() -> dict[str, Any] | None:
            client = self._client()
            if client is None:
                return None
            response = (
                client.table("bot_operation_jobs").select("*")
                .eq("guild_id", guild_id).eq("idempotency_key", idempotency_key).limit(1).execute()
            )
            rows = getattr(response, "data", None) or []
            return dict(rows[0]) if rows else None
        try:
            return await asyncio.to_thread(sync)
        except Exception as exc:
            self._disable(f"dedupe_fetch_failed:{type(exc).__name__}:{str(exc)[:160]}")
            return None

    async def reconcile_stale(self) -> list[dict[str, Any]]:
        """Expire jobs a previous process left active.

        Factories are process memory and cannot be safely resumed after restart.
        Marking them expired is truthful and lets the dashboard submit a fresh,
        idempotent operation instead of showing a forever-running job.
        """
        if not self._ready():
            return []
        now_iso = _utc_now_iso()
        def sync() -> list[dict[str, Any]]:
            client = self._client()
            if client is None:
                return []
            response = client.table("bot_operation_jobs").select("*").in_("status", list(_ACTIVE_STATUSES)).execute()
            rows = [dict(row) for row in (getattr(response, "data", None) or []) if isinstance(row, dict)]
            expired: list[dict[str, Any]] = []
            for row in rows:
                lock_exp = _safe_str(row.get("lock_expires_at"), "")
                created = _safe_str(row.get("created_at"), "")
                should_expire = not lock_exp
                if lock_exp:
                    try:
                        should_expire = datetime.fromisoformat(lock_exp.replace("Z", "+00:00")) <= _utc_now()
                    except Exception:
                        should_expire = True
                if not should_expire and _safe_str(row.get("status")) == "queued":
                    try:
                        should_expire = datetime.fromisoformat(created.replace("Z", "+00:00")) <= _utc_now() - timedelta(minutes=15)
                    except Exception:
                        pass
                if not should_expire:
                    continue
                result = row.get("result_json") if isinstance(row.get("result_json"), dict) else {}
                result = {**result, "restart_reconciled": True}
                client.table("bot_operation_jobs").update({
                    "status": "expired",
                    "error_code": "process_restart",
                    "error_message": "Operation was left active by a previous bot process and cannot be safely resumed.",
                    "result_json": result,
                    "locked_by": None,
                    "lock_expires_at": None,
                    "finished_at": now_iso,
                }).eq("id", row.get("id")).execute()
                row.update({"status": "expired", "result_json": result, "finished_at": now_iso})
                expired.append(row)
            return expired
        try:
            return await asyncio.to_thread(sync)
        except Exception as exc:
            self._disable(f"reconcile_failed:{type(exc).__name__}:{str(exc)[:160]}")
            return []

    async def audit_job(self, job: OperationJob) -> None:
        if job.risk_level != "dangerous" or not self._ready():
            return
        payload = {
            "guild_id": job.guild_id,
            "event_type": "operation_job",
            "actor_id": job.actor_id or None,
            "target_id": job.id,
            "message": f"{job.operation_type}: {job.status}"[:1000],
            "metadata": job.public_payload(),
            "meta": job.public_payload(),
            "created_at": _utc_now_iso(),
        }
        def sync() -> None:
            client = self._client()
            if client is None:
                return
            client.table("activity_feed_events").insert(payload).execute()
        try:
            await asyncio.to_thread(sync)
        except Exception:
            # Audit persistence is best effort and must not alter the operation result.
            return


class GuildOperationQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, OperationJob] = {}
        self._dedupe: dict[str, str] = {}
        self._dedupe_expires: dict[str, float] = {}
        self._queues: dict[str, asyncio.Queue[OperationJob]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._stats: dict[str, OperationQueueStats] = {}
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._persistence = _OperationPersistence()

        self._max_global = _env_int("DANK_OPERATION_QUEUE_MAX_GLOBAL", 16, minimum=1, maximum=256)
        self._max_per_guild = _env_int("DANK_OPERATION_QUEUE_MAX_PER_GUILD", 4, minimum=1, maximum=32)
        self._max_per_type = _env_int("DANK_OPERATION_QUEUE_MAX_PER_TYPE", 8, minimum=1, maximum=64)
        self._max_queue_per_key = _env_int("DANK_OPERATION_QUEUE_MAX_PER_KEY", 100, minimum=1, maximum=5000)
        self._summary_interval = _env_int("DANK_OPERATION_QUEUE_SUMMARY_SECONDS", 300, minimum=30, maximum=3600)
        self._lock_ttl_seconds = _env_int("DANK_OPERATION_QUEUE_LOCK_TTL_SECONDS", _DEFAULT_LOCK_TTL_SECONDS, minimum=60, maximum=86400)

        self._global_semaphore: asyncio.Semaphore | None = None
        self._global_loop: asyncio.AbstractEventLoop | None = None
        self._guild_semaphores: dict[str, asyncio.Semaphore] = {}
        self._type_semaphores: dict[str, asyncio.Semaphore] = {}
        self._global_running = 0
        self._global_waiting = 0
        self._summary_task: asyncio.Task[None] | None = None
        self._startup_reconciled = False

    def _global(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._global_semaphore is None or self._global_loop is not loop:
            self._global_semaphore = asyncio.Semaphore(self._max_global)
            self._global_loop = loop
            self._guild_semaphores = {}
            self._type_semaphores = {}
            self._global_running = 0
            self._global_waiting = 0
        return self._global_semaphore

    def _guild_sem(self, job: OperationJob) -> asyncio.Semaphore:
        # Broad/dangerous classes serialize the guild. Explicitly scoped classes
        # may use the bounded per-guild pool so unrelated tickets/members can work.
        cap = self._max_per_guild if _is_explicitly_scoped_concurrency(job.concurrency_class) else 1
        key = f"{job.guild_id}:{cap}"
        return self._guild_semaphores.setdefault(key, asyncio.Semaphore(cap))

    def _type_sem(self, operation_type: str) -> asyncio.Semaphore:
        key = _safe_str(operation_type, "operation")
        return self._type_semaphores.setdefault(key, asyncio.Semaphore(self._max_per_type))

    def _stats_for(self, key: str) -> OperationQueueStats:
        return self._stats.setdefault(key, OperationQueueStats())

    def _prune_dedupe(self) -> None:
        now = time.monotonic()
        for key in [k for k, until in self._dedupe_expires.items() if until <= now][:1000]:
            self._dedupe_expires.pop(key, None)
            self._dedupe.pop(key, None)

    def _remember(self, job: OperationJob) -> None:
        self._jobs[job.id] = job
        key = f"{job.guild_id}:{job.idempotency_key}"
        self._dedupe[key] = job.id
        self._dedupe_expires[key] = time.monotonic() + _RECENT_IDEMPOTENCY_SECONDS

    def _start_summary_logger(self) -> None:
        if self._summary_task is not None and not self._summary_task.done():
            return
        try:
            self._summary_task = asyncio.create_task(self._summary_logger(), name="operation-queue-summary")
        except Exception:
            pass

    async def reconcile_startup(self) -> int:
        if self._startup_reconciled:
            return 0
        self._startup_reconciled = True
        rows = await self._persistence.reconcile_stale()
        count = 0
        for row in rows:
            try:
                job = OperationJob.from_persisted(row)
                self._jobs[job.id] = job
                self._stats_for("startup:reconcile").stale_recoveries += 1
                self._stats_for("startup:reconcile").expired += 1
                count += 1
            except Exception:
                continue
        if count:
            print(f"🧱 operation_queue restart reconciliation expired={count}")
        return count

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
        supports_cancellation: bool = False,
    ) -> OperationJob:
        self._start_summary_logger()
        await self.reconcile_startup()
        gid = _safe_str(guild_id, "global")
        op_type = _safe_str(operation_type, "operation")[:120]
        actor = _safe_str(actor_id, "")
        p_hash = payload_hash(payload)
        dedupe = make_idempotency_key(
            guild_id=gid, actor_id=actor or "system", operation_type=op_type,
            payload=payload, custom_key=idempotency_key, time_bucket_seconds=30,
        )
        c_key = _concurrency_key(
            guild_id=gid, operation_type=op_type, concurrency_class=concurrency_class,
            concurrency_key=concurrency_key,
        )

        persisted = await self._persistence.fetch_duplicate(gid, dedupe)
        if persisted:
            persisted_job = OperationJob.from_persisted(persisted)
            if persisted_job.status in _ACTIVE_STATUSES or persisted_job.status in _TERMINAL_STATUSES:
                async with self._lock:
                    self._remember(persisted_job)
                    self._stats_for(c_key).duplicate_hits += 1
                return persisted_job

        async with self._lock:
            self._prune_dedupe()
            existing_id = self._dedupe.get(f"{gid}:{dedupe}")
            if existing_id and existing_id in self._jobs:
                self._stats_for(c_key).duplicate_hits += 1
                return self._jobs[existing_id]
            queue = self._queues.setdefault(c_key, asyncio.Queue(maxsize=self._max_queue_per_key))
            stats = self._stats_for(c_key)
            if queue.full():
                stats.busy_rejected += 1
                stats.last_error = "queue full"
                raise RuntimeError(f"operation queue full for {c_key}")
            job = OperationJob(
                id=str(uuid.uuid4()), guild_id=gid, actor_id=actor, operation_type=op_type,
                risk_level=_safe_str(risk_level, "moderate"), source=_safe_str(source, "system"),
                idempotency_key=dedupe, payload_hash=p_hash,
                concurrency_class=_safe_str(concurrency_class, "guild"), concurrency_key=c_key,
                factory=factory, timeout_seconds=max(1.0, float(timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)),
                progress_total=max(0, int(progress_total or 0)), supports_cancellation=bool(supports_cancellation),
            )
            self._remember(job)
            queue.put_nowait(job)
            stats.submitted += 1
            stats.queued += 1
            stats.last_operation_type = op_type
            worker = self._workers.get(c_key)
            if worker is None or worker.done():
                self._workers[c_key] = asyncio.create_task(self._worker(c_key), name=f"operation-queue:{c_key}")
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
        supports_cancellation: bool = False,
    ) -> tuple[str, Any, OperationJob | None]:
        self._start_summary_logger()
        await self.reconcile_startup()
        gid = _safe_str(guild_id, "global")
        op_type = _safe_str(operation_type, "operation")[:120]
        actor = _safe_str(actor_id, "")
        dedupe = make_idempotency_key(
            guild_id=gid, actor_id=actor or "system", operation_type=op_type,
            payload=payload, custom_key=idempotency_key, time_bucket_seconds=10,
        )
        c_key = _concurrency_key(
            guild_id=gid, operation_type=op_type, concurrency_class=concurrency_class,
            concurrency_key=concurrency_key,
        )
        persisted = await self._persistence.fetch_duplicate(gid, dedupe)
        if persisted:
            persisted_job = OperationJob.from_persisted(persisted)
            if persisted_job.status in _ACTIVE_STATUSES or persisted_job.status in _TERMINAL_STATUSES:
                async with self._lock:
                    self._remember(persisted_job)
                    self._stats_for(c_key).duplicate_hits += 1
                return "duplicate", None, persisted_job

        async with self._lock:
            self._prune_dedupe()
            stats = self._stats_for(c_key)
            existing_id = self._dedupe.get(f"{gid}:{dedupe}")
            if existing_id and existing_id in self._jobs:
                stats.duplicate_hits += 1
                return "duplicate", None, self._jobs[existing_id]
            lock = self._locks.setdefault(c_key, asyncio.Lock())
            if reject_if_busy and lock.locked():
                stats.busy_rejected += 1
                return "busy", None, None
            job = OperationJob(
                id=str(uuid.uuid4()), guild_id=gid, actor_id=actor, operation_type=op_type,
                risk_level=_safe_str(risk_level, "dangerous"), source=_safe_str(source, "discord_command"),
                idempotency_key=dedupe, payload_hash=payload_hash(payload),
                concurrency_class=_safe_str(concurrency_class, "guild_config_write"), concurrency_key=c_key,
                factory=factory, timeout_seconds=max(1.0, float(timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)),
                supports_cancellation=bool(supports_cancellation),
            )
            self._remember(job)
            stats.submitted += 1
            stats.last_operation_type = op_type
        await self._persistence.upsert_job(job)
        async with lock:
            return await self._run_job_now(job)

    async def _run_job_now(self, job: OperationJob) -> tuple[str, Any, OperationJob]:
        stats = self._stats_for(job.concurrency_key)
        global_sem = self._global()
        guild_sem = self._guild_sem(job)
        type_sem = self._type_sem(job.operation_type)
        acquired_global = acquired_guild = acquired_type = False
        started_monotonic = time.monotonic()
        try:
            stats.waiting_global_slot += 1
            self._global_waiting += 1
            await global_sem.acquire(); acquired_global = True
            await guild_sem.acquire(); acquired_guild = True
            await type_sem.acquire(); acquired_type = True
        finally:
            stats.waiting_global_slot = max(0, stats.waiting_global_slot - 1)
            self._global_waiting = max(0, self._global_waiting - 1)

        if job.cancellation_requested:
            job.status = "cancelled"
            job.finished_at = _utc_now_iso()
            stats.cancelled += 1
            for sem, acquired in ((type_sem, acquired_type), (guild_sem, acquired_guild), (global_sem, acquired_global)):
                if acquired:
                    sem.release()
            await self._persistence.update_job(job)
            return job.status, None, job

        job.status = "running"
        job.started_at = _utc_now_iso()
        job.lock_expires_at = _future_iso(max(self._lock_ttl_seconds, job.timeout_seconds + 60.0))
        stats.started += 1
        stats.running += 1
        self._global_running += 1
        await self._persistence.update_job(job)
        result: Any = None
        try:
            if job.factory is None:
                raise RuntimeError("operation job has no factory")
            task = asyncio.create_task(job.factory(), name=f"operation-job:{job.id}")
            self._running_tasks[job.id] = task
            result = await asyncio.wait_for(task, timeout=float(job.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS))
            job.result = _jsonable(result if isinstance(result, dict) else {"value": result})
            requested_status = _safe_str(job.result.get("status"), "succeeded") if isinstance(job.result, dict) else "succeeded"
            job.status = requested_status if requested_status in _TERMINAL_STATUSES else "succeeded"
            if job.status == "partial":
                stats.partial += 1
            elif job.status == "cancelled":
                stats.cancelled += 1
            elif job.status == "expired":
                stats.expired += 1
            elif job.status == "failed":
                stats.failed += 1
            else:
                stats.succeeded += 1
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error_message = "Operation was cancelled"
            stats.cancelled += 1
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error_code = "timeout"
            job.error_message = f"Operation timed out after {job.timeout_seconds:.1f}s"
            stats.failed += 1
        except Exception as exc:
            job.status = "failed"
            job.error_code = type(exc).__name__
            job.error_message = repr(exc)[:1000]
            stats.failed += 1
        finally:
            self._running_tasks.pop(job.id, None)
            job.finished_at = _utc_now_iso()
            job.lock_expires_at = ""
            job.last_updated_monotonic = time.monotonic()
            elapsed = max(0.0, time.monotonic() - started_monotonic)
            stats.duration_total_seconds += elapsed
            stats.duration_samples += 1
            stats.running = max(0, stats.running - 1)
            self._global_running = max(0, self._global_running - 1)
            stats.last_operation_type = job.operation_type
            stats.last_error = job.error_message
            stats.last_updated_monotonic = time.monotonic()
            for sem, acquired in ((type_sem, acquired_type), (guild_sem, acquired_guild), (global_sem, acquired_global)):
                if acquired:
                    try:
                        sem.release()
                    except Exception:
                        pass
            await self._persistence.update_job(job)
            await self._persistence.audit_job(job)
        return job.status, result, job

    async def _worker(self, c_key: str) -> None:
        queue = self._queues[c_key]
        lock = self._locks.setdefault(c_key, asyncio.Lock())
        while True:
            try:
                job = await asyncio.wait_for(queue.get(), timeout=300.0)
            except asyncio.TimeoutError:
                if queue.empty():
                    return
                continue
            stats = self._stats_for(c_key)
            stats.queued = max(0, stats.queued - 1)
            try:
                if job.cancellation_requested or job.status == "cancelled":
                    job.status = "cancelled"
                    job.finished_at = _utc_now_iso()
                    stats.cancelled += 1
                    await self._persistence.update_job(job)
                else:
                    async with lock:
                        await self._run_job_now(job)
            finally:
                queue.task_done()

    async def get_job_persistent(self, job_id: str) -> dict[str, Any] | None:
        local = self.get_job(job_id)
        if local:
            return local
        row = await self._persistence.fetch_job(job_id)
        if not row:
            return None
        job = OperationJob.from_persisted(row)
        self._jobs[job.id] = job
        return job.public_payload()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(_safe_str(job_id))
        return job.public_payload() if job else None

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(_safe_str(job_id))
        if job is None:
            row = await self._persistence.fetch_job(job_id)
            if not row:
                return None
            job = OperationJob.from_persisted(row)
            self._jobs[job.id] = job
        if job.status in _TERMINAL_STATUSES:
            return job.public_payload()
        if job.status == "queued":
            job.cancellation_requested = True
            job.status = "cancelled"
            job.error_code = "cancelled_before_start"
            job.error_message = "Operation was cancelled before execution."
            job.finished_at = _utc_now_iso()
            await self._persistence.update_job(job)
            return job.public_payload()
        if job.status in {"running", "waiting_rate_limit"}:
            if not job.supports_cancellation:
                job.result = {**dict(job.result or {}), "cancellation_rejected": "operation_does_not_support_safe_midflight_cancellation"}
                await self._persistence.update_job(job)
                return job.public_payload()
            job.cancellation_requested = True
            task = self._running_tasks.get(job.id)
            if task is not None and not task.done():
                task.cancel()
            await self._persistence.update_job(job)
        return job.public_payload()

    def note_retry(self, *, concurrency_key: str = "retry", rate_limit: bool = False) -> None:
        stats = self._stats_for(concurrency_key)
        stats.retry_count += 1
        if rate_limit:
            stats.rate_limit_waits += 1
        stats.last_updated_monotonic = time.monotonic()

    def health_summary(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        totals = {name: 0 for name in (
            "queues", "queued", "running", "waiting_global_slot", "submitted", "started", "succeeded",
            "failed", "partial", "cancelled", "expired", "duplicate_hits", "busy_rejected",
            "rate_limit_waits", "retry_count", "stale_recoveries", "duration_samples",
        )}
        duration_total = 0.0
        for key, stats in list(self._stats.items()):
            queue = self._queues.get(key)
            avg_duration = stats.duration_total_seconds / stats.duration_samples if stats.duration_samples else 0.0
            row = {
                "queue_size": queue.qsize() if queue else 0,
                "submitted": stats.submitted, "started": stats.started, "succeeded": stats.succeeded,
                "failed": stats.failed, "partial": stats.partial, "cancelled": stats.cancelled,
                "expired": stats.expired, "duplicate_hits": stats.duplicate_hits,
                "busy_rejected": stats.busy_rejected, "running": stats.running,
                "waiting_global_slot": stats.waiting_global_slot, "rate_limit_waits": stats.rate_limit_waits,
                "retry_count": stats.retry_count, "stale_recoveries": stats.stale_recoveries,
                "average_duration_seconds": round(avg_duration, 3),
                "last_operation_type": stats.last_operation_type, "last_error": stats.last_error,
                "last_updated_seconds_ago": max(0, int(time.monotonic() - stats.last_updated_monotonic)),
            }
            snapshot[key] = row
            totals["queues"] += 1
            totals["queued"] += int(row["queue_size"] or 0)
            for name in totals:
                if name in {"queues", "queued", "duration_samples"}:
                    continue
                totals[name] += int(row.get(name, 0) or 0)
            totals["duration_samples"] += stats.duration_samples
            duration_total += stats.duration_total_seconds
        finished = totals["succeeded"] + totals["failed"] + totals["partial"] + totals["cancelled"] + totals["expired"]
        failure_rate = (totals["failed"] / finished) if finished else 0.0
        average_duration = duration_total / totals["duration_samples"] if totals["duration_samples"] else 0.0
        status = "warning" if totals["failed"] or totals["expired"] else ("busy" if totals["queued"] or totals["running"] else "ok")
        hot = sorted(
            ({"key": key, **row} for key, row in snapshot.items() if row["queue_size"] or row["running"] or row["failed"] or row["busy_rejected"]),
            key=lambda row: int(row.get("failed", 0)) * 100 + int(row.get("busy_rejected", 0)) * 10 + int(row.get("queue_size", 0)) + int(row.get("running", 0)),
            reverse=True,
        )[:10]
        return {
            "status": status,
            "totals": totals,
            "rates": {"failure_rate": round(failure_rate, 4), "average_duration_seconds": round(average_duration, 3)},
            "global": {
                "max_global": self._max_global, "max_per_guild": self._max_per_guild,
                "max_per_type": self._max_per_type, "running": self._global_running,
                "waiting": self._global_waiting, "jobs_tracked": len(self._jobs),
                "dedupe_keys": len(self._dedupe), "persistence": "enabled" if self._persistence.enabled else "memory_only",
                "startup_reconciled": self._startup_reconciled,
            },
            "hot_queues": hot,
        }

    async def _summary_logger(self) -> None:
        while True:
            await asyncio.sleep(float(self._summary_interval))
            try:
                summary = self.health_summary()
                totals = summary["totals"]
                if not totals["queues"]:
                    continue
                print(
                    "📊 operation_queue summary "
                    f"status={summary['status']} queued={totals['queued']} running={totals['running']} "
                    f"ok={totals['succeeded']} failed={totals['failed']} partial={totals['partial']} "
                    f"dup={totals['duplicate_hits']} retries={totals['retry_count']} rate_waits={totals['rate_limit_waits']} "
                    f"stale={totals['stale_recoveries']} avg_s={summary['rates']['average_duration_seconds']}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ operation_queue summary failed: {exc!r}")


_MANAGER = GuildOperationQueue()


async def ensure_operation_queue_started() -> int:
    _MANAGER._start_summary_logger()
    return await _MANAGER.reconcile_startup()


def ensure_operation_queue_started_background() -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(ensure_operation_queue_started(), name="operation-queue-startup-reconcile")
    except Exception:
        pass


async def submit_operation(
    *, guild_id: int | str | None, operation_type: str, factory: JobFactory,
    actor_id: int | str | None = None, risk_level: str = "moderate", source: str = "system",
    payload: Any = None, idempotency_key: str = "", concurrency_class: str = "guild",
    concurrency_key: str = "", timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    progress_total: int = 0, supports_cancellation: bool = False,
) -> dict[str, Any]:
    job = await _MANAGER.submit(
        guild_id=guild_id, operation_type=operation_type, factory=factory, actor_id=actor_id,
        risk_level=risk_level, source=source, payload=payload, idempotency_key=idempotency_key,
        concurrency_class=concurrency_class, concurrency_key=concurrency_key,
        timeout_seconds=timeout_seconds, progress_total=progress_total,
        supports_cancellation=supports_cancellation,
    )
    return job.public_payload()


async def run_exclusive(
    *, guild_id: int | str | None, operation_type: str, factory: JobFactory,
    actor_id: int | str | None = None, risk_level: str = "dangerous", source: str = "discord_command",
    payload: Any = None, idempotency_key: str = "", concurrency_class: str = "guild_config_write",
    concurrency_key: str = "", timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    reject_if_busy: bool = True, supports_cancellation: bool = False,
) -> tuple[str, Any, dict[str, Any] | None]:
    state, result, job = await _MANAGER.run_exclusive(
        guild_id=guild_id, operation_type=operation_type, factory=factory, actor_id=actor_id,
        risk_level=risk_level, source=source, payload=payload, idempotency_key=idempotency_key,
        concurrency_class=concurrency_class, concurrency_key=concurrency_key,
        timeout_seconds=timeout_seconds, reject_if_busy=reject_if_busy,
        supports_cancellation=supports_cancellation,
    )
    return state, result, job.public_payload() if job else None


async def _send_interaction_message(interaction: Any, content: str) -> None:
    try:
        import discord
        allowed_mentions = discord.AllowedMentions.none()
    except Exception:
        allowed_mentions = None
    try:
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
    *, interaction: Any, operation_type: str, action_label: str, factory: JobFactory,
    fingerprint: Any = None, risk_level: str = "dangerous", source: str = "discord_command",
    concurrency_class: str = "guild_config_write", concurrency_key: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS, supports_cancellation: bool = False,
) -> Any:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    state, result, _job = await run_exclusive(
        guild_id=_safe_int(getattr(guild, "id", 0), 0), actor_id=_safe_int(getattr(user, "id", 0), 0),
        operation_type=operation_type, risk_level=risk_level, source=source, payload=fingerprint,
        concurrency_class=concurrency_class, concurrency_key=concurrency_key, timeout_seconds=timeout_seconds,
        reject_if_busy=True, supports_cancellation=supports_cancellation, factory=factory,
    )
    if state == "duplicate":
        await _send_interaction_message(interaction, f"✅ That **{action_label}** action was already submitted. Blocked the duplicate.")
        return None
    if state == "busy":
        await _send_interaction_message(interaction, f"⏳ **{action_label}** is already running in this scope. Wait a moment, then refresh.")
        return None
    if state == "failed":
        await _send_interaction_message(interaction, f"⚠️ **{action_label}** failed before it could finish. Check the bot logs, then try again.")
        return None
    return result


def get_operation_job(job_id: str) -> dict[str, Any] | None:
    return _MANAGER.get_job(job_id)


async def get_operation_job_persistent(job_id: str) -> dict[str, Any] | None:
    return await _MANAGER.get_job_persistent(job_id)


async def cancel_operation_job(job_id: str) -> dict[str, Any] | None:
    return await _MANAGER.cancel(job_id)


def operation_queue_health_summary() -> dict[str, Any]:
    return _MANAGER.health_summary()


async def with_retry(
    factory: JobFactory,
    *,
    attempts: int = 3,
    base_delay: float = 0.75,
    max_delay: float = 8.0,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
    concurrency_key: str = "retry",
) -> Any:
    """Retry one safe/idempotent API call with jitter and Discord-aware rules."""
    total = max(1, int(attempts or 1))
    delay = max(0.05, float(base_delay or 0.75))
    ceiling = max(delay, float(max_delay or 8.0))
    for index in range(total):
        try:
            return await factory()
        except retry_exceptions as exc:
            # Never retry hard permission/auth failures.
            try:
                import discord
                if isinstance(exc, discord.Forbidden):
                    raise
            except ImportError:
                pass
            status = _safe_int(getattr(exc, "status", 0), 0)
            if status in {401, 403, 404}:
                raise
            if index >= total - 1:
                raise
            retry_after = getattr(exc, "retry_after", None)
            rate_limited = status == 429 or retry_after is not None
            try:
                wait = float(retry_after) if retry_after is not None else delay
            except Exception:
                wait = delay
            wait = min(ceiling, max(0.05, wait))
            wait += random.uniform(0.0, min(0.5, wait * 0.25))
            _MANAGER.note_retry(concurrency_key=concurrency_key, rate_limit=rate_limited)
            await asyncio.sleep(wait)
            delay = min(ceiling, delay * 2.0)


__all__ = [
    "GuildOperationQueue", "OperationJob", "make_idempotency_key", "payload_hash",
    "submit_operation", "run_exclusive", "run_interaction_exclusive", "get_operation_job",
    "get_operation_job_persistent", "cancel_operation_job", "operation_queue_health_summary",
    "ensure_operation_queue_started", "ensure_operation_queue_started_background", "with_retry",
]
