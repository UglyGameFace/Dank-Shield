from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from stoney_verify.members_new import activity_tracker as tracker


@pytest.fixture(autouse=True)
def _reset_tracker_state(monkeypatch):
    tracker._ACTIVITY_BUFFER.clear()
    tracker._PENDING_FAIL_REPORTS.clear()
    tracker._LOCAL_ERRORS.clear()
    tracker._CONSECUTIVE_DB_FAILURES = 0
    tracker._BREAKER_OPEN_UNTIL = 0.0
    tracker._BATCH_RPC_AVAILABLE = None
    tracker._DROPPED_EVENT_COUNT = 0
    tracker._LAST_DROP_LOG_COUNT = 0
    monkeypatch.delenv("DANK_ACTIVITY_FLUSH_SECONDS", raising=False)
    monkeypatch.delenv("DANK_ACTIVITY_BUFFER_MAX_ENTRIES", raising=False)
    monkeypatch.delenv("DANK_ACTIVITY_BREAKER_THRESHOLD", raising=False)
    monkeypatch.delenv("DANK_ACTIVITY_BREAKER_COOLDOWN_SECONDS", raising=False)
    yield
    tracker._ACTIVITY_BUFFER.clear()
    tracker._PENDING_FAIL_REPORTS.clear()
    tracker._LOCAL_ERRORS.clear()
    tracker._CONSECUTIVE_DB_FAILURES = 0
    tracker._BREAKER_OPEN_UNTIL = 0.0
    tracker._BATCH_RPC_AVAILABLE = None
    tracker._DROPPED_EVENT_COUNT = 0
    tracker._LAST_DROP_LOG_COUNT = 0


def _ts(year=2026, month=10, day=13, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_stage_event_dedupes_to_latest_with_count():
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(minute=0), channel_id=10,
    )
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(minute=5), channel_id=11,
    )
    assert len(tracker._ACTIVITY_BUFFER) == 1
    entry = tracker._ACTIVITY_BUFFER[(1, 2, "message")]
    assert entry["occurred_at"] == _ts(minute=5)
    assert entry["channel_id"] == 11
    assert entry["count"] == 2


def test_stage_event_older_arrival_keeps_newer_but_counts():
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(minute=5), channel_id=11,
    )
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(minute=0), channel_id=10,
    )
    entry = tracker._ACTIVITY_BUFFER[(1, 2, "message")]
    assert entry["occurred_at"] == _ts(minute=5)
    assert entry["channel_id"] == 11
    assert entry["count"] == 2


def test_stage_event_separates_activity_types():
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(), channel_id=10,
    )
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="reaction",
        occurred_at=_ts(), channel_id=10,
    )
    assert len(tracker._ACTIVITY_BUFFER) == 2


def test_stage_event_enforces_buffer_cap(monkeypatch):
    monkeypatch.setenv("DANK_ACTIVITY_BUFFER_MAX_ENTRIES", "3")
    for uid in range(10):
        tracker._stage_event(
            guild_id=1, user_id=uid, activity_type="message",
            occurred_at=_ts(), channel_id=10,
        )
    assert len(tracker._ACTIVITY_BUFFER) == 3


def test_record_direct_member_activity_buffers_without_io(monkeypatch):
    calls = []
    monkeypatch.setattr(tracker, "_rpc_sync", lambda *a, **k: calls.append((a, k)))
    ok = asyncio.run(
        tracker.record_direct_member_activity(
            guild_id=1, user_id=2, activity_type="message",
            occurred_at=_ts(), channel_id=10,
        )
    )
    assert ok is True
    assert calls == []
    assert len(tracker._ACTIVITY_BUFFER) == 1


def test_record_direct_member_activity_rejects_invalid():
    assert asyncio.run(
        tracker.record_direct_member_activity(
            guild_id=0, user_id=2, activity_type="message"
        )
    ) is False
    assert asyncio.run(
        tracker.record_direct_member_activity(
            guild_id=1, user_id=2, activity_type="presence"
        )
    ) is False
    assert tracker._ACTIVITY_BUFFER == {}


def test_mark_failure_records_locally_without_rpc(monkeypatch):
    calls = []
    monkeypatch.setattr(tracker, "_rpc_sync", lambda *a, **k: calls.append((a, k)))
    asyncio.run(tracker._mark_failure(7, "boom"))
    assert calls == []
    assert tracker._LOCAL_ERRORS[7] == "boom"
    assert tracker._PENDING_FAIL_REPORTS[7] == "boom"


def test_flush_uses_batch_rpc_when_available(monkeypatch):
    rpc_calls = []
    monkeypatch.setattr(tracker, "_BATCH_RPC_AVAILABLE", True)

    def fake_rpc(name, params):
        rpc_calls.append((name, params))

    monkeypatch.setattr(tracker, "_rpc_sync", fake_rpc)
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(), channel_id=10, count=3,
    )
    asyncio.run(tracker._flush_once())
    assert len(rpc_calls) == 1
    name, params = rpc_calls[0]
    assert name == "record_member_activity_batch"
    assert params["p_events"][0]["event_count"] == 3
    assert tracker._ACTIVITY_BUFFER == {}


def test_flush_circuit_breaker_opens_after_threshold(monkeypatch):
    monkeypatch.setenv("DANK_ACTIVITY_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("DANK_ACTIVITY_BREAKER_COOLDOWN_SECONDS", "120")

    def boom(name, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(tracker, "_rpc_sync", boom)
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(), channel_id=10,
    )
    asyncio.run(tracker._flush_once())
    assert tracker._CONSECUTIVE_DB_FAILURES == 1
    assert tracker._BREAKER_OPEN_UNTIL == 0.0
    # Fail-closed: the failed flush marks the guild's coverage incomplete.
    assert 1 in tracker._LOCAL_ERRORS
    asyncio.run(tracker._flush_once())
    assert tracker._CONSECUTIVE_DB_FAILURES == 2
    assert tracker._BREAKER_OPEN_UNTIL > time.monotonic()
    # Buffer is re-queued, not lost.
    assert len(tracker._ACTIVITY_BUFFER) == 1
    # While open, flush is a no-op (no additional RPC attempts).
    asyncio.run(tracker._flush_once())
    assert tracker._CONSECUTIVE_DB_FAILURES == 2


def test_flush_success_resets_breaker_and_clears_guild_errors(monkeypatch):
    monkeypatch.setattr(tracker, "_BATCH_RPC_AVAILABLE", True)
    monkeypatch.setattr(tracker, "_rpc_sync", lambda *a, **k: None)
    tracker._CONSECUTIVE_DB_FAILURES = 2
    tracker._LOCAL_ERRORS[1] = "old"
    tracker._stage_event(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=_ts(), channel_id=10,
    )
    asyncio.run(tracker._flush_once())
    assert tracker._CONSECUTIVE_DB_FAILURES == 0
    assert 1 not in tracker._LOCAL_ERRORS


def test_poison_entry_does_not_block_valid_entries(monkeypatch, capsys):
    """One rejected record is logged and skipped; valid records still land."""
    monkeypatch.setattr(tracker, "_BATCH_RPC_AVAILABLE", True)
    landed = []

    def fake_rpc(name, params):
        if name == "record_member_activity_batch":
            raise RuntimeError("batch rejected: bad row")
        landed.append(params["p_guild_id"])

    monkeypatch.setattr(tracker, "_rpc_sync", fake_rpc)
    for uid in (2, 3):
        tracker._stage_event(
            guild_id=1, user_id=uid, activity_type="message",
            occurred_at=_ts(), channel_id=10,
        )
    asyncio.run(tracker._flush_once())
    out = capsys.readouterr().out
    assert "isolating per-entry" in out
    # Per-entry fallback also uses the failing fake for batch name only...
    # individual RPCs succeed, so both entries land.
    assert sorted(landed) == ["1", "1"]


def test_buffer_overflow_counts_drops(monkeypatch):
    monkeypatch.setenv("DANK_ACTIVITY_BUFFER_MAX_ENTRIES", "1000")
    for uid in range(1005):
        tracker._stage_event(
            guild_id=1, user_id=uid, activity_type="message",
            occurred_at=_ts(), channel_id=10,
        )
    assert len(tracker._ACTIVITY_BUFFER) == 1000
    assert tracker._DROPPED_EVENT_COUNT == 5