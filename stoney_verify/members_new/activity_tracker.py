from __future__ import annotations

"""Authoritative, fail-closed member activity tracking.

Cleanup-authorizing activity is limited to durable direct evidence:
- retained guild messages authored by the member
- Discord interactions submitted while Dank Shield is online
- ticket messages explicitly authored by the member

Reactions are stored only as supplemental context. They never authorize
cleanup because their complete history cannot be reconstructed during bot
downtime.

Presence and voice-state changes are intentionally excluded from authoritative
inactivity proof because they can be inaccurate or caused by staff actions.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import time
from typing import Any, Mapping, Optional
import uuid

import discord

from stoney_verify.globals import get_supabase
from stoney_verify.startup_recovery_coordinator import startup_recovery_slot
from stoney_verify.guild_sweep import run_bounded_guild_sweep
from stoney_verify.members_new.activity_reconciliation import (
    audit_guild_activity_scope,
    max_reconcile_gap_seconds,
    reconcile_restart_gap,
    reconcile_start_delay_seconds,
    reconcile_timeout_seconds,
)


LEDGER_TABLE = "member_activity_ledger"
TRACKER_STATE_TABLE = "member_activity_tracker_state"

_PROCESS_ID = uuid.uuid4().hex
_HEARTBEAT_INTERVAL_SECONDS = 60
_MAX_HEARTBEAT_GAP_SECONDS = 180

_INSTALLED = False
_HEARTBEAT_TASK: Optional[asyncio.Task] = None
_STARTED_GUILDS: set[int] = set()
_STARTUP_TASKS: dict[int, asyncio.Task] = {}
# Discord history reconciliation is intentionally single-flight. PR #280
# replaced the old lock with a shared two-guild startup pool, which allowed two
# REST-heavy history/thread scans to overlap and exceed Discloud's process-wide
# 300 requests / 30 seconds ceiling. The shared coordinator still bounds all
# startup work; this lock restores the narrower history-specific invariant.
_STARTUP_RECONCILE_LOCK = asyncio.Lock()
_STARTUP_RECOVERY_HEARTBEATS: dict[int, datetime] = {}
_LOCAL_ERRORS: dict[int, str] = {}
_LOCAL_SCOPE_ERRORS: dict[int, str] = {}

# --- Write batching (scales the per-event ledger RPC) ---------------------
# One RPC per message/interaction/reaction does not scale: at 1k guilds x
# 2 msg/s the ledger absorbs 2000+ writes/sec, and every failed write used
# to fire a second failing RPC (fail storm -> thread-pool saturation ->
# missed heartbeats -> gateway disconnects). Events are now deduplicated in
# memory and flushed periodically. This reduces Supabase ROUND-TRIPS, not
# PostgreSQL work: the batch RPC still performs one upsert per deduplicated
# entry server-side. The ledger upsert is greatest()-based, so flushing only
# the latest event per (guild, user, type) converges to the identical row
# state; per-window counts are preserved via p_event_count. Exact flush cost
# is ceil(deduped_entries / 500) RPCs per window — measure, don't assume.
_DEFAULT_FLUSH_SECONDS = 15.0
_DEFAULT_BUFFER_MAX_ENTRIES = 50000
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN_SECONDS = 120.0

# (guild_id, user_id, activity_type) -> {"occurred_at", "channel_id", "count"}
_ACTIVITY_BUFFER: dict[tuple[int, int, str], dict[str, Any]] = {}
_FLUSH_TASK: Optional[asyncio.Task] = None
# In-flight snapshot is immutable until PostgreSQL acknowledges its batch ID.
_PENDING_BATCH: Optional[dict[str, Any]] = None
_PENDING_SPLITS: list[dict[str, Any]] = []
_FLUSH_LOCK = asyncio.Lock()
_ACTIVITY_SEQUENCE = 0
_LOST_COVERAGE_GUILDS: set[int] = set()
_ACKED_BATCH_IDS: list[str] = []
# Consecutive DB-RPC failures across flush AND heartbeat paths. When this
# reaches the threshold, activity flush and heartbeat/scope writes pause.
_CONSECUTIVE_DB_FAILURES = 0
_BREAKER_OPEN_UNTIL = 0.0  # time.monotonic()
# guild_id -> latest failure message awaiting its (single, per-cycle) report.
_PENDING_FAIL_REPORTS: dict[int, str] = {}
# Explicit loss accounting: events dropped when the bounded buffer overflows
# during a prolonged outage. Counts cannot be reconstructed later, so drops
# are counted and logged, never silent.
_DROPPED_EVENT_COUNT = 0
_LAST_DROP_LOG_COUNT = 0
# Max entries per batch RPC call (PostgREST payload limits).
_BATCH_CHUNK_SIZE = 500


def _flush_seconds() -> float:
    try:
        raw = float(
            str(
                os.getenv(
                    "DANK_ACTIVITY_FLUSH_SECONDS",
                    str(_DEFAULT_FLUSH_SECONDS),
                )
                or _DEFAULT_FLUSH_SECONDS
            ).strip()
        )
    except Exception:
        raw = _DEFAULT_FLUSH_SECONDS
    return max(5.0, min(raw, 120.0))


def _buffer_max_entries() -> int:
    return max(
        1000,
        _safe_int(
            os.getenv(
                "DANK_ACTIVITY_BUFFER_MAX_ENTRIES",
                str(_DEFAULT_BUFFER_MAX_ENTRIES),
            ),
            _DEFAULT_BUFFER_MAX_ENTRIES,
        ),
    )


def _breaker_threshold() -> int:
    return max(
        1,
        _safe_int(
            os.getenv(
                "DANK_ACTIVITY_BREAKER_THRESHOLD",
                str(_DEFAULT_BREAKER_THRESHOLD),
            ),
            _DEFAULT_BREAKER_THRESHOLD,
        ),
    )


def _breaker_cooldown_seconds() -> float:
    try:
        raw = float(
            str(
                os.getenv(
                    "DANK_ACTIVITY_BREAKER_COOLDOWN_SECONDS",
                    str(_DEFAULT_BREAKER_COOLDOWN_SECONDS),
                )
                or _DEFAULT_BREAKER_COOLDOWN_SECONDS
            ).strip()
        )
    except Exception:
        raw = _DEFAULT_BREAKER_COOLDOWN_SECONDS
    return max(10.0, min(raw, 900.0))


def _db_breaker_open() -> bool:
    return time.monotonic() < _BREAKER_OPEN_UNTIL


def _record_db_success() -> None:
    global _CONSECUTIVE_DB_FAILURES
    _CONSECUTIVE_DB_FAILURES = 0


def _record_db_failure() -> None:
    """Feed any DB-RPC failure (flush, heartbeat, scope sync) into the shared
    breaker. Consecutive failures mean the DB is down, not one bad guild:
    successes interleave and reset the count."""
    global _CONSECUTIVE_DB_FAILURES, _BREAKER_OPEN_UNTIL
    _CONSECUTIVE_DB_FAILURES += 1
    if (_CONSECUTIVE_DB_FAILURES >= _breaker_threshold()
            and not _db_breaker_open()):
        _BREAKER_OPEN_UNTIL = time.monotonic() + _breaker_cooldown_seconds()
        print(
            "⛔ activity DB circuit breaker OPEN "
            f"consecutive_failures={_CONSECUTIVE_DB_FAILURES} "
            f"cooldown={_breaker_cooldown_seconds():.0f}s"
        )


@dataclass(frozen=True)
class ActivityCoverageStatus:
    guild_id: int
    actionable: bool
    reason: str
    continuous_since: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    observed_days: int = 0
    required_days: int = 0
    process_id: str = ""
    event_writes_failed: int = 0
    last_error: str = ""
    storage_ready: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    try:
        parsed = datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _combined_local_error(guild_id: int) -> str:
    gid = int(guild_id)
    parts = [
        str(_LOCAL_ERRORS.get(gid, "") or "").strip(),
        str(_LOCAL_SCOPE_ERRORS.get(gid, "") or "").strip(),
    ]
    if gid in _PENDING_FAIL_REPORTS:
        parts.append("Unreported activity-write failure; coverage is unverified.")
    if gid in _LOST_COVERAGE_GUILDS:
        parts.append("Activity events were dropped; a new proof window is required.")
    if _db_breaker_open():
        parts.append("Activity database circuit breaker is open; coverage unverified.")
    return " | ".join(part for part in parts if part)


def _rpc_sync(name: str, params: Mapping[str, Any]) -> None:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    sb.rpc(name, dict(params)).execute()


def _select_tracker_state_sync(guild_id: int) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    response = (
        sb.table(TRACKER_STATE_TABLE)
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )

    rows = getattr(response, "data", None) or []

    if not rows or not isinstance(rows[0], Mapping):
        return None

    return dict(rows[0])


async def persisted_last_heartbeat_at(guild_id: int) -> Optional[datetime]:
    """Return this process's immutable pre-restart heartbeat for a guild."""

    gid = int(guild_id)
    cached = _STARTUP_RECOVERY_HEARTBEATS.get(gid)
    if cached is not None:
        return cached

    try:
        row = await asyncio.to_thread(_select_tracker_state_sync, gid)
    except Exception:
        return None
    if not row:
        return None

    heartbeat = _safe_dt(row.get("last_heartbeat_at"))
    stored_process = str(row.get("process_id") or "").strip()
    if heartbeat is None:
        return None

    # Once this process has resumed the tracker, last_heartbeat_at advances.
    # Without a cached pre-restart value that new heartbeat must never be used as
    # a historical recovery boundary because it could hide the actual gap.
    if stored_process == _PROCESS_ID:
        return None

    _STARTUP_RECOVERY_HEARTBEATS[gid] = heartbeat
    return heartbeat


def evaluate_coverage_state(
    row: Optional[Mapping[str, Any]],
    *,
    guild_id: int,
    now: datetime,
    required_days: int,
    expected_process_id: str,
    max_gap_seconds: int = _MAX_HEARTBEAT_GAP_SECONDS,
    local_error: str = "",
) -> ActivityCoverageStatus:
    safe_days = max(1, int(required_days or 1))
    current = _safe_dt(now) or _utcnow()

    if local_error:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker entered fail-closed mode after a local write error: "
                + str(local_error)[:300]
            ),
            required_days=safe_days,
            process_id=str(expected_process_id),
            last_error=str(local_error)[:500],
            storage_ready=False,
        )

    if not row:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Authoritative activity tables are missing, unreadable, "
                "or have not started tracking this server yet."
            ),
            required_days=safe_days,
            process_id=str(expected_process_id),
            storage_ready=False,
        )

    stored_process = str(row.get("process_id") or "").strip()
    continuous_since = _safe_dt(row.get("continuous_since"))
    last_heartbeat = _safe_dt(row.get("last_heartbeat_at"))
    failures = _safe_int(row.get("event_writes_failed"), 0)
    last_error = str(row.get("last_error") or "").strip()

    if stored_process != str(expected_process_id):
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker process changed. Restart reconciliation has not "
                "completed or could not be verified, so cleanup remains "
                "review-only."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    if continuous_since is None or last_heartbeat is None:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason="Tracker state is incomplete, so cleanup remains review-only.",
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    heartbeat_age = (current - last_heartbeat).total_seconds()

    if heartbeat_age < -30:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason="Tracker heartbeat is in the future; check system time.",
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    if heartbeat_age > max(60, int(max_gap_seconds)):
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker heartbeat is stale. Continuous proof has a gap, "
                "so cleanup remains review-only."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    observed_seconds = max(
        0.0,
        (current - continuous_since).total_seconds(),
    )
    observed_days = max(
        0,
        int(math.floor(observed_seconds / 86400.0)),
    )

    required_seconds = safe_days * 86400

    if observed_seconds < required_seconds:
        remaining_days = max(
            1,
            int(
                math.ceil(
                    (required_seconds - observed_seconds) / 86400.0
                )
            ),
        )

        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                f"Authoritative tracker has {observed_days}/{safe_days} "
                f"continuous day(s). Approximately {remaining_days} more "
                "day(s) are required."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            observed_days=observed_days,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    return ActivityCoverageStatus(
        guild_id=int(guild_id),
        actionable=True,
        reason=(
            f"Authoritative tracker has continuously observed this server "
            f"for {observed_days} day(s), meeting the {safe_days}-day "
            "requirement."
        ),
        continuous_since=continuous_since,
        last_heartbeat_at=last_heartbeat,
        observed_days=observed_days,
        required_days=safe_days,
        process_id=stored_process,
        event_writes_failed=failures,
        last_error=last_error,
        storage_ready=True,
    )


async def get_activity_coverage_status(
    guild_id: int,
    *,
    required_days: int,
) -> ActivityCoverageStatus:
    gid = int(guild_id)

    try:
        row = await asyncio.to_thread(
            _select_tracker_state_sync,
            gid,
        )
    except Exception as exc:
        return ActivityCoverageStatus(
            guild_id=gid,
            actionable=False,
            reason=(
                "Could not read authoritative tracker state: "
                f"{type(exc).__name__}: {str(exc)[:220]}"
            ),
            required_days=max(1, int(required_days or 1)),
            process_id=_PROCESS_ID,
            last_error=str(exc)[:500],
            storage_ready=False,
        )

    return evaluate_coverage_state(
        row,
        guild_id=gid,
        now=_utcnow(),
        required_days=required_days,
        expected_process_id=_PROCESS_ID,
        local_error=_combined_local_error(gid),
    )


def _start_tracker_sync(guild_id: int) -> None:
    now = _utcnow().isoformat()

    _rpc_sync(
        "start_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_started_at": now,
        },
    )


def _resume_tracker_sync(
    *,
    guild_id: int,
    previous_process_id: str,
    previous_heartbeat_at: datetime,
    resumed_at: datetime,
) -> bool:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    response = sb.rpc(
        "resume_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_previous_process_id": str(
                previous_process_id
            ),
            "p_new_process_id": _PROCESS_ID,
            "p_previous_heartbeat_at": (
                previous_heartbeat_at.isoformat()
            ),
            "p_resumed_at": resumed_at.isoformat(),
        },
    ).execute()

    data = getattr(response, "data", None)

    if isinstance(data, bool):
        return data

    if isinstance(data, list) and data:
        return bool(data[0])

    if isinstance(data, Mapping):
        for value in data.values():
            if isinstance(value, bool):
                return value

    return bool(data)


def _heartbeat_sync(guild_id: int) -> None:
    _rpc_sync(
        "heartbeat_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_heartbeat_at": _utcnow().isoformat(),
            "p_max_gap_seconds": _MAX_HEARTBEAT_GAP_SECONDS,
        },
    )


def _fail_tracker_sync(guild_id: int, error: str) -> None:
    _rpc_sync(
        "fail_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_failed_at": _utcnow().isoformat(),
            "p_error": str(error or "unknown tracker error")[:1000],
        },
    )


def _record_activity_sync(
    *,
    guild_id: int,
    user_id: int,
    activity_type: str,
    occurred_at: datetime,
    channel_id: Optional[int],
) -> None:
    _rpc_sync(
        "record_member_activity",
        {
            "p_guild_id": str(int(guild_id)),
            "p_user_id": str(int(user_id)),
            "p_activity_type": str(activity_type),
            "p_occurred_at": occurred_at.isoformat(),
            "p_channel_id": (
                str(int(channel_id))
                if channel_id is not None
                else ""
            ),
            "p_process_id": _PROCESS_ID,
        },
    )


async def _mark_failure(guild_id: int, error: str) -> None:
    gid = int(guild_id)
    message = str(error or "unknown tracker error")[:500]
    _LOCAL_ERRORS[gid] = message
    # Fail-storm guard: NEVER fire an RPC per failure. The old code called
    # fail_member_activity_tracker here, so one failing RPC produced a second
    # failing RPC per message — saturating the thread pool during an outage.
    # Failures are now reported at most once per guild per flush cycle, and
    # the circuit breaker can suppress even those while the DB is down.
    _PENDING_FAIL_REPORTS[gid] = message
    # Every _mark_failure call site follows a failed DB RPC: feed the shared
    # breaker so heartbeat/scope failures also pause write attempts.
    _record_db_failure()

    print(
        "⚠️ authoritative activity tracker fail-closed "
        f"guild={gid} error={message}"
    )


def _stage_event(
    *,
    guild_id: int,
    user_id: int,
    activity_type: str,
    occurred_at: datetime,
    channel_id: Optional[int],
    count: int = 1,
) -> None:
    """Buffer one event. Latest timestamp wins per (guild, user, type);
    counts accumulate. The ledger upsert is greatest()-based; the batch
    payload also carries first-observed timestamps and stable retry IDs."""
    global _ACTIVITY_SEQUENCE
    key = (int(guild_id), int(user_id), activity_type)
    existing = _ACTIVITY_BUFFER.get(key)
    _ACTIVITY_SEQUENCE += 1
    if existing is not None and existing["occurred_at"] > occurred_at:
        existing["count"] += int(count)
        existing["first_occurred_at"] = min(
            existing["first_occurred_at"], occurred_at
        )
        # Refresh insertion recency in O(1) so oldest-drop really evicts
        # the least recently staged key, not a frequently used older key.
        _ACTIVITY_BUFFER.pop(key)
        _ACTIVITY_BUFFER[key] = existing
        return
    if existing is not None:
        _ACTIVITY_BUFFER.pop(key)
    _ACTIVITY_BUFFER[key] = {
        "occurred_at": occurred_at,
        "first_occurred_at": min(existing["first_occurred_at"], occurred_at)
            if existing else occurred_at,
        "channel_id": int(channel_id) if channel_id else None,
        "count": int(existing["count"]) + int(count) if existing else int(count),
        "order": _ACTIVITY_SEQUENCE,
    }
    limit = _buffer_max_entries()
    if len(_ACTIVITY_BUFFER) > limit:
        # Bounded memory during a prolonged outage. Drops are counted and
        # reported (see _flush_once); event counts dropped here cannot be
        # reconstructed later, so this must stay visible, never silent.
        global _DROPPED_EVENT_COUNT
        while len(_ACTIVITY_BUFFER) > limit:
            _oldest_key = next(iter(_ACTIVITY_BUFFER))
            _dropped = _ACTIVITY_BUFFER.pop(_oldest_key)
            _DROPPED_EVENT_COUNT += int(_dropped.get("count", 1))
            lost_guild = int(_oldest_key[0])
            _LOST_COVERAGE_GUILDS.add(lost_guild)
            _LOCAL_ERRORS[lost_guild] = (
                "Activity buffer overflow: records were lost; "
                "continuous coverage must restart before cleanup."
            )
        # Surface a loss even while the breaker is open. Rate-limit logs so
        # a long DB outage does not become a separate console-output storm.
        global _LAST_DROP_LOG_COUNT
        if (_LAST_DROP_LOG_COUNT == 0
                or _DROPPED_EVENT_COUNT - _LAST_DROP_LOG_COUNT >= 1000):
            print(
                "⚠️ activity buffer overflow during outage: "
                f"total_dropped_events={_DROPPED_EVENT_COUNT}"
            )
            _LAST_DROP_LOG_COUNT = _DROPPED_EVENT_COUNT


async def record_direct_member_activity(
    *,
    guild_id: int,
    user_id: int,
    activity_type: str,
    occurred_at: Optional[datetime] = None,
    channel_id: Optional[int] = None,
) -> bool:
    if int(guild_id) <= 0 or int(user_id) <= 0:
        return False

    if activity_type not in {
        "message",
        "reaction",
        "interaction",
        "ticket_message",
    }:
        return False

    timestamp = _safe_dt(occurred_at) or _utcnow()
    # No I/O here: stage in memory; the flush loop persists periodically.
    _stage_event(
        guild_id=int(guild_id),
        user_id=int(user_id),
        activity_type=activity_type,
        occurred_at=timestamp,
        channel_id=channel_id,
    )
    return True


def _flush_batch_sync(batch: dict[str, Any]) -> None:
    """Retry the same immutable batch ID; SQL dedupes acknowledged commits."""
    events = [
        {
            "guild_id": str(gid),
            "user_id": str(uid),
            "activity_type": atype,
            "occurred_at": entry["occurred_at"].isoformat(),
            "first_occurred_at": entry["first_occurred_at"].isoformat(),
            "channel_id": str(entry["channel_id"]) if entry.get("channel_id") else "",
            "process_id": _PROCESS_ID,
            "event_count": int(entry["count"]),
        }
        for (gid, uid, atype), entry in batch["snapshot"].items()
    ]
    _rpc_sync("record_member_activity_batch", {
        "p_batch_id": batch["id"],
        "p_events": events,
    })


def _ack_batch_receipts_sync(batch_ids: list[str]) -> None:
    _rpc_sync("ack_member_activity_batches", {"p_batch_ids": batch_ids})


def _is_deterministic_batch_rejection(exc: Exception) -> bool:
    """Only known SQL validation failures may trigger batch bisection.

    Network, authentication, timeout and unknown PostgreSQL failures must
    preserve the original batch ID for exactly-once retry.
    """
    code = str(getattr(exc, "code", "") or "").upper()
    if code in {"22P02", "22007", "22003", "23514", "P0001"}:
        return True
    detail = str(exc).lower()
    return any(part in detail for part in (
        "unsupported direct activity type",
        "invalid input syntax for type timestamp",
        "invalid input syntax for type bigint",
    ))


async def _flush_once() -> bool:
    """Commit one chunk; on failure keep its ID and all entries for retry."""
    global _PENDING_BATCH, _LAST_DROP_LOG_COUNT, _DROPPED_EVENT_COUNT
    async with _FLUSH_LOCK:
        if _db_breaker_open():
            return False
        if _DROPPED_EVENT_COUNT != _LAST_DROP_LOG_COUNT:
            print(
                "⚠️ activity buffer overflow: "
                f"{_DROPPED_EVENT_COUNT - _LAST_DROP_LOG_COUNT} newly dropped events "
                f"(total={_DROPPED_EVENT_COUNT}); guild coverage fail-closed"
            )
            _LAST_DROP_LOG_COUNT = _DROPPED_EVENT_COUNT
        completed_guilds: set[int] = set()
        progress = False
        if _PENDING_BATCH is None and _PENDING_SPLITS:
            _PENDING_BATCH = _PENDING_SPLITS.pop(0)
        if _PENDING_BATCH is None and _ACTIVITY_BUFFER:
            items = sorted(
                _ACTIVITY_BUFFER.items(), key=lambda pair: pair[1]["order"]
            )[:_BATCH_CHUNK_SIZE]
            snapshot = dict(items)
            for key, _ in items:
                _ACTIVITY_BUFFER.pop(key, None)
            _PENDING_BATCH = {"id": str(uuid.uuid4()), "snapshot": snapshot}
        if _PENDING_BATCH is not None:
            batch = _PENDING_BATCH
            try:
                await asyncio.to_thread(_flush_batch_sync, batch)
            except Exception as exc:
                if _is_deterministic_batch_rejection(exc):
                    entries = list(batch["snapshot"].items())
                    if len(entries) > 1:
                        midpoint = len(entries) // 2
                        # The rejected SQL transaction committed neither its
                        # receipt nor any ledger entries; fresh child IDs are safe.
                        _PENDING_BATCH = {
                            "id": str(uuid.uuid4()),
                            "snapshot": dict(entries[:midpoint]),
                        }
                        _PENDING_SPLITS.insert(0, {
                            "id": str(uuid.uuid4()),
                            "snapshot": dict(entries[midpoint:]),
                        })
                        return True
                    (gid, _uid, atype), invalid = entries[0]
                    # Explicitly quarantine irreparable data. Never silently
                    # treat its missing count as a successful write.
                    _DROPPED_EVENT_COUNT += int(invalid["count"])
                    _LOST_COVERAGE_GUILDS.add(gid)
                    _LOCAL_ERRORS[gid] = (
                        "Invalid activity record quarantined; "
                        "coverage must restart before inactivity cleanup."
                    )
                    _PENDING_FAIL_REPORTS[gid] = _LOCAL_ERRORS[gid]
                    _PENDING_BATCH = None
                    print(
                        "⚠️ activity entry quarantined after SQL validation "
                        f"guild={gid} type={atype} error={type(exc).__name__}"
                    )
                    return True
                _record_db_failure()
                error = (
                    f"Activity batch pending retry: {type(exc).__name__}: "
                    f"{str(exc)[:180]}"
                )
                for gid, _uid, _atype in batch["snapshot"]:
                    _LOCAL_ERRORS[gid] = error
                    _PENDING_FAIL_REPORTS[gid] = error
                return False
            completed_guilds.update(k[0] for k in batch["snapshot"])
            _PENDING_BATCH = None
            _ACKED_BATCH_IDS.append(batch["id"])
            _record_db_success()
            progress = True

        # Deletion of acknowledged receipts is optional for correctness but
        # critical to bound long-term Supabase storage. Retry without ever
        # treating a receipt-maintenance failure as a failed activity write.
        if _ACKED_BATCH_IDS and (len(_ACKED_BATCH_IDS) >= 64
                                 or not _ACTIVITY_BUFFER):
            ack_ids = list(_ACKED_BATCH_IDS[:128])
            try:
                await asyncio.to_thread(_ack_batch_receipts_sync, ack_ids)
            except Exception as exc:
                print(f"⚠️ activity receipt cleanup pending: {type(exc).__name__}")
            else:
                del _ACKED_BATCH_IDS[:len(ack_ids)]

        # A recovery report restarts the durable coverage clock even when
        # buffered events replay. Never clear a failure on heartbeat alone.
        for gid, message in list(_PENDING_FAIL_REPORTS.items())[:10]:
            try:
                await asyncio.to_thread(_fail_tracker_sync, gid, message)
            except Exception:
                _record_db_failure()
                return progress
            _PENDING_FAIL_REPORTS.pop(gid, None)
            completed_guilds.add(gid)
            _record_db_success()
            progress = True
        # Records dropped under memory pressure cannot be reconstructed.
        for gid in list(_LOST_COVERAGE_GUILDS)[:10]:
            # Reset immediately after DB recovery. Continuously active guilds
            # must not be starved just because more events are being staged.
            try:
                await asyncio.to_thread(_start_tracker_sync, gid)
            except Exception:
                _record_db_failure()
                return progress
            _LOST_COVERAGE_GUILDS.discard(gid)
            completed_guilds.add(gid)
            _record_db_success()
            progress = True
        for gid in completed_guilds:
            if (gid not in _LOST_COVERAGE_GUILDS
                    and gid not in _PENDING_FAIL_REPORTS):
                _LOCAL_ERRORS.pop(gid, None)
        return progress


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(_flush_seconds())
        for _ in range(128):
            try:
                if not await _flush_once():
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ activity flush loop error: {type(exc).__name__}")
                break


async def flush_activity_buffer() -> None:
    """Best-effort bounded shutdown drain (SIGKILL/OOM still loses RAM)."""
    for _ in range(128):
        if not await _flush_once():
            break
    if _PENDING_BATCH or _PENDING_SPLITS or _ACTIVITY_BUFFER:
        print(
            "⚠️ activity shutdown unflushed "
            f"pending_batch={bool(_PENDING_BATCH)} "
            f"pending_split_chunks={len(_PENDING_SPLITS)} "
            f"buffered_keys={len(_ACTIVITY_BUFFER)}"
        )


async def _refresh_scope_status(
    guild: discord.Guild,
) -> str:
    gid = int(guild.id)
    previous = str(
        _LOCAL_SCOPE_ERRORS.get(gid, "") or ""
    ).strip()
    current = str(
        audit_guild_activity_scope(guild) or ""
    ).strip()

    if current:
        _LOCAL_SCOPE_ERRORS[gid] = current

        if gid in _STARTED_GUILDS and not previous:
            try:
                await asyncio.to_thread(
                    _start_tracker_sync,
                    gid,
                )
                print(
                    "⚠️ authoritative activity coverage reset "
                    f"guild={gid} reason=scope_became_incomplete"
                )
            except Exception as exc:
                await _mark_failure(
                    gid,
                    "scope reset failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )

        return current

    if previous:
        _LOCAL_SCOPE_ERRORS.pop(gid, None)

        if gid in _STARTED_GUILDS:
            try:
                await asyncio.to_thread(
                    _start_tracker_sync,
                    gid,
                )
                _LOCAL_ERRORS.pop(gid, None)
                print(
                    "📡 authoritative activity coverage restarted "
                    f"guild={gid} reason=scope_permissions_restored"
                )
            except Exception as exc:
                await _mark_failure(
                    gid,
                    "scope recovery failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )

    return ""


async def _start_guild_tracking(
    guild: discord.Guild,
) -> bool:
    gid = int(guild.id)

    if gid in _STARTED_GUILDS:
        return True

    try:
        scope_error = await _refresh_scope_status(guild)
        row = await asyncio.to_thread(
            _select_tracker_state_sync,
            gid,
        )
        now = _utcnow()

        previous_process = str(
            (row or {}).get("process_id") or ""
        ).strip()
        previous_heartbeat = _safe_dt(
            (row or {}).get("last_heartbeat_at")
        )
        previous_continuous = _safe_dt(
            (row or {}).get("continuous_since")
        )
        previous_failures = _safe_int(
            (row or {}).get("event_writes_failed"),
            0,
        )
        previous_error = str(
            (row or {}).get("last_error") or ""
        ).strip()

        if (
            previous_heartbeat is not None
            and previous_process != _PROCESS_ID
        ):
            # Legacy tracker rows may predate process_id. A durable heartbeat is
            # still a valid pre-restart boundary and must be pinned before this
            # process advances the stored state.
            _STARTUP_RECOVERY_HEARTBEATS.setdefault(
                gid,
                previous_heartbeat,
            )

        if (
            row
            and previous_process == _PROCESS_ID
            and previous_heartbeat is not None
        ):
            await asyncio.to_thread(
                _heartbeat_sync,
                gid,
            )
            _STARTED_GUILDS.add(gid)
            _LOCAL_ERRORS.pop(gid, None)
            return True

        reset_reason = "no_previous_tracker_state"

        if row:
            reset_reason = "restart_gap_not_reconciled"

        can_reconcile = bool(
            row
            and not scope_error
            and previous_process
            and previous_heartbeat is not None
            and previous_continuous is not None
            and previous_failures <= 0
            and not previous_error
        )

        if can_reconcile:
            gap_seconds = (
                now - previous_heartbeat
            ).total_seconds()

            if gap_seconds < 0:
                can_reconcile = False
                reset_reason = "previous_heartbeat_in_future"
            elif gap_seconds > max_reconcile_gap_seconds():
                can_reconcile = False
                reset_reason = (
                    "restart_gap_exceeded_safe_limit"
                )

        if can_reconcile:
            try:
                result = await reconcile_restart_gap(
                    guild,
                    after=previous_heartbeat,
                    before=now,
                )

                for user_id, (
                    occurred_at,
                    channel_id,
                ) in sorted(
                    result.latest_by_user.items()
                ):
                    await asyncio.to_thread(
                        _record_activity_sync,
                        guild_id=gid,
                        user_id=int(user_id),
                        activity_type="message",
                        occurred_at=occurred_at,
                        channel_id=channel_id or None,
                    )

                resumed = await asyncio.to_thread(
                    _resume_tracker_sync,
                    guild_id=gid,
                    previous_process_id=previous_process,
                    previous_heartbeat_at=previous_heartbeat,
                    resumed_at=now,
                )

                if not resumed:
                    raise RuntimeError(
                        "Tracker state changed while restart "
                        "reconciliation was running."
                    )

                _STARTED_GUILDS.add(gid)
                _LOCAL_ERRORS.pop(gid, None)

                print(
                    "📡 authoritative activity continuity resumed "
                    f"guild={gid} "
                    f"channels={result.scanned_channels} "
                    f"messages={result.scanned_messages} "
                    f"members={result.replayed_members} "
                    f"gap_seconds={int(gap_seconds)}"
                )
                return True

            except Exception as exc:
                reset_reason = (
                    "restart_reconciliation_failed: "
                    f"{type(exc).__name__}: {str(exc)[:250]}"
                )

        elif scope_error:
            reset_reason = (
                "activity_scope_incomplete: "
                + scope_error[:250]
            )
        elif previous_failures > 0 or previous_error:
            reset_reason = "previous_tracker_failure"

        await asyncio.to_thread(
            _start_tracker_sync,
            gid,
        )

        _STARTED_GUILDS.add(gid)
        _LOCAL_ERRORS.pop(gid, None)

        print(
            "📡 authoritative activity tracking started "
            f"guild={gid} process={_PROCESS_ID[:8]} "
            f"new_proof_window=True reason={reset_reason}"
        )
        return True

    except Exception as exc:
        await _mark_failure(
            gid,
            "tracker start failed: "
            f"{type(exc).__name__}: {str(exc)[:350]}",
        )
        return False


async def _force_new_window_after_startup_failure(
    guild: discord.Guild,
    *,
    reason: str,
) -> None:
    """Fail closed when background reconciliation cannot finish safely."""
    gid = int(guild.id)

    try:
        await asyncio.to_thread(
            _start_tracker_sync,
            gid,
        )

        _STARTED_GUILDS.add(gid)
        _LOCAL_ERRORS.pop(gid, None)

        print(
            "⚠️ authoritative activity continuity reset "
            f"guild={gid} reason={str(reason)[:300]}"
        )
    except Exception as exc:
        await _mark_failure(
            gid,
            "startup reset failed: "
            f"{type(exc).__name__}: {str(exc)[:300]}",
        )


async def _run_scheduled_guild_tracking(
    guild: discord.Guild,
    *,
    delay_seconds: int,
) -> None:
    gid = int(guild.id)

    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        # Acquire the activity-history lane before the shared startup slot so
        # another activity task waiting for history capacity does not occupy one
        # of the process-wide startup slots.
        async with _STARTUP_RECONCILE_LOCK:
            async with startup_recovery_slot(
                gid,
                "activity_restart_reconcile",
            ):
                # Keep the REST lane serialized while giving large guilds
                # enough wall-clock time to obey the shared pacing budget.
                timeout_seconds = reconcile_timeout_seconds(guild)
                try:
                    completed = await asyncio.wait_for(
                        _start_guild_tracking(guild),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    await _force_new_window_after_startup_failure(
                        guild,
                        reason=(
                            "restart reconciliation exceeded "
                            f"{timeout_seconds} seconds"
                        ),
                    )
                    return

        if not completed:
            await _force_new_window_after_startup_failure(
                guild,
                reason=(
                    "restart reconciliation returned without "
                    "starting the tracker"
                ),
            )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _force_new_window_after_startup_failure(
            guild,
            reason=(
                "background restart reconciliation crashed: "
                f"{type(exc).__name__}: {str(exc)[:250]}"
            ),
        )
    finally:
        current = _STARTUP_TASKS.get(gid)

        if current is asyncio.current_task():
            _STARTUP_TASKS.pop(gid, None)


def _schedule_guild_tracking(
    guild: discord.Guild,
    *,
    delay_seconds: int,
) -> Optional[asyncio.Task]:
    gid = int(guild.id)

    if gid in _STARTED_GUILDS:
        return None

    existing = _STARTUP_TASKS.get(gid)

    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(
        _run_scheduled_guild_tracking(
            guild,
            delay_seconds=max(0, int(delay_seconds)),
        ),
        name=f"activity_restart_reconcile_{gid}",
    )

    _STARTUP_TASKS[gid] = task

    print(
        "📡 authoritative activity reconciliation queued "
        f"guild={gid} delay={max(0, int(delay_seconds))}s"
    )

    return task


def _heartbeat_concurrency() -> int:
    """Bound Supabase fan-out, even if a deployment env is misconfigured."""
    return max(1, min(_safe_int(os.getenv("DANK_HEARTBEAT_GUILD_CONCURRENCY"), 8), 20))


async def _heartbeat_one_guild(guild: discord.Guild) -> bool:
    gid = int(guild.id)
    pending = _STARTUP_TASKS.get(gid)
    if pending is not None and not pending.done():
        return True
    if gid not in _STARTED_GUILDS:
        _schedule_guild_tracking(guild, delay_seconds=0)
        return True
    if _db_breaker_open():
        return False

    # Scope changes can require an additional durable reset RPC. Check the
    # shared activity breaker again before sending a heartbeat.
    try:
        await _refresh_scope_status(guild)
        if _db_breaker_open():
            return False
        await asyncio.to_thread(_heartbeat_sync, gid)
        _record_db_success()
        if (gid not in _LOST_COVERAGE_GUILDS
                and gid not in _PENDING_FAIL_REPORTS):
            _LOCAL_ERRORS.pop(gid, None)
        return True
    except Exception as exc:
        await _mark_failure(
            gid,
            "heartbeat failed: "
            f"{type(exc).__name__}: {str(exc)[:350]}",
        )
        return False


async def _heartbeat_loop(bot: discord.Client) -> None:
    """One bounded cycle per process; shards share the same tracker state."""
    await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
    while not bot.is_closed():
        result = await run_bounded_guild_sweep(
            list(getattr(bot, "guilds", []) or []),
            _heartbeat_one_guild,
            concurrency=_heartbeat_concurrency(),
            on_error=lambda guild, exc: print(
                "⚠️ activity heartbeat guild exception "
                f"guild={getattr(guild, 'id', '?')} error={type(exc).__name__}"
            ),
        )
        print(
            "📡 activity heartbeat cycle "
            f"guilds={result.total} ok={result.succeeded} "
            f"failed_or_breaker={result.failed} "
            f"concurrency={result.concurrency} elapsed_s={result.seconds:.2f} "
            f"target_s={_HEARTBEAT_INTERVAL_SECONDS} "
            f"overrun={result.seconds > _HEARTBEAT_INTERVAL_SECONDS}"
        )
        # Target a 60s start-to-start cadence. Always leave a short
        # recovery gap after an overrun instead of saturating Supabase.
        await asyncio.sleep(
            max(5.0, _HEARTBEAT_INTERVAL_SECONDS - result.seconds)
        )


async def _on_ready(
    bot: discord.Client,
) -> None:
    global _HEARTBEAT_TASK
    global _FLUSH_TASK

    # Never await full Discord history repair inside on_ready. Queue it after
    # Discord is responsive so slash commands and persistent panels can answer.
    base_delay = reconcile_start_delay_seconds()

    for index, guild in enumerate(
        list(getattr(bot, "guilds", []) or [])
    ):
        _schedule_guild_tracking(
            guild,
            delay_seconds=base_delay + min(index * 2, 30),
        )

    if (
        _HEARTBEAT_TASK is None
        or _HEARTBEAT_TASK.done()
    ):
        _HEARTBEAT_TASK = asyncio.create_task(
            _heartbeat_loop(bot),
            name=(
                "authoritative_member_activity_heartbeat"
            ),
        )

    if _FLUSH_TASK is None or _FLUSH_TASK.done():
        _FLUSH_TASK = asyncio.create_task(
            _flush_loop(),
            name="authoritative_member_activity_flush",
        )


async def _on_guild_join(
    guild: discord.Guild,
) -> None:
    _schedule_guild_tracking(
        guild,
        delay_seconds=reconcile_start_delay_seconds(),
    )


async def _on_message(message: discord.Message) -> None:
    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)

    if guild is None or author is None:
        return
    if bool(getattr(author, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(guild.id),
        user_id=int(author.id),
        activity_type="message",
        occurred_at=getattr(message, "created_at", None),
        channel_id=getattr(
            getattr(message, "channel", None),
            "id",
            None,
        ),
    )


async def _on_raw_reaction_add(
    bot: discord.Client,
    payload: discord.RawReactionActionEvent,
) -> None:
    if payload.guild_id is None:
        return

    if getattr(bot.user, "id", None) == payload.user_id:
        return

    member = getattr(payload, "member", None)

    if member is None:
        guild = bot.get_guild(int(payload.guild_id))
        member = (
            guild.get_member(int(payload.user_id))
            if guild is not None
            else None
        )

    if bool(getattr(member, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(payload.guild_id),
        user_id=int(payload.user_id),
        activity_type="reaction",
        occurred_at=_utcnow(),
        channel_id=int(payload.channel_id),
    )


async def _on_interaction(
    interaction: discord.Interaction,
) -> None:
    if interaction.guild_id is None:
        return

    user = getattr(interaction, "user", None)

    if user is None or bool(getattr(user, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(interaction.guild_id),
        user_id=int(user.id),
        activity_type="interaction",
        occurred_at=getattr(interaction, "created_at", None),
        channel_id=interaction.channel_id,
    )


def install_activity_tracker(bot: discord.Client) -> bool:
    global _INSTALLED

    if _INSTALLED:
        return True

    marker = "_dank_authoritative_activity_tracker_installed"

    if bool(getattr(bot, marker, False)):
        _INSTALLED = True
        return True

    async def ready_listener() -> None:
        await _on_ready(bot)

    async def raw_reaction_listener(
        payload: discord.RawReactionActionEvent,
    ) -> None:
        await _on_raw_reaction_add(bot, payload)

    bot.add_listener(ready_listener, "on_ready")
    bot.add_listener(_on_guild_join, "on_guild_join")
    bot.add_listener(_on_message, "on_message")
    bot.add_listener(
        raw_reaction_listener,
        "on_raw_reaction_add",
    )
    bot.add_listener(_on_interaction, "on_interaction")

    # Best-effort shutdown flush: discord.py closes the gateway on SIGTERM /
    # SIGINT via bot.close(). Flushing first (bounded 10s) means a graceful
    # restart loses at most the in-flight window, not the whole buffer.
    # SIGKILL / OOM kills cannot be caught: those lose the buffer, and the
    # drop accounting in _flush_once keeps that visible.
    _original_close = bot.close

    async def _close_with_activity_flush(*args: Any, **kwargs: Any) -> None:
        try:
            await asyncio.wait_for(flush_activity_buffer(), timeout=10)
        except Exception:
            pass
        await _original_close(*args, **kwargs)

    if getattr(bot.close, "__name__", "") != "_close_with_activity_flush":
        bot.close = _close_with_activity_flush  # type: ignore[assignment]

    setattr(bot, marker, True)
    _INSTALLED = True

    print(
        "📡 authoritative member activity tracker installed; "
        "messages/interactions are cleanup evidence; "
        "reactions are supplemental only"
    )

    return True


__all__ = [
    "ActivityCoverageStatus",
    "evaluate_coverage_state",
    "flush_activity_buffer",
    "get_activity_coverage_status",
    "persisted_last_heartbeat_at",
    "install_activity_tracker",
    "record_direct_member_activity",
]
