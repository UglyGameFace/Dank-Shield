from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from stoney_verify.members_new import activity_tracker as t


@pytest.fixture(autouse=True)
def reset_tracker(monkeypatch):
    t._ACTIVITY_BUFFER.clear()
    t._PENDING_BATCH = None
    t._PENDING_SPLITS.clear()
    t._PENDING_FAIL_REPORTS.clear()
    t._ACKED_BATCH_IDS.clear()
    t._LOCAL_ERRORS.clear()
    t._LOST_COVERAGE_GUILDS.clear()
    t._CONSECUTIVE_DB_FAILURES = 0
    t._BREAKER_OPEN_UNTIL = 0.0
    t._DROPPED_EVENT_COUNT = 0
    t._LAST_DROP_LOG_COUNT = 0
    t._ACTIVITY_SEQUENCE = 0
    monkeypatch.delenv("DANK_ACTIVITY_BREAKER_THRESHOLD", raising=False)
    monkeypatch.delenv("DANK_ACTIVITY_BUFFER_MAX_ENTRIES", raising=False)
    yield
    t._ACTIVITY_BUFFER.clear()
    t._PENDING_BATCH = None
    t._PENDING_SPLITS.clear()
    t._PENDING_FAIL_REPORTS.clear()
    t._ACKED_BATCH_IDS.clear()
    t._LOCAL_ERRORS.clear()
    t._LOST_COVERAGE_GUILDS.clear()


def ts(minute=0):
    return datetime(2026, 10, 13, 12, minute, tzinfo=timezone.utc)


def add(user=2, minute=0, typ="message", count=1, channel=10):
    t._stage_event(
        guild_id=1, user_id=user, activity_type=typ,
        occurred_at=ts(minute), channel_id=channel, count=count,
    )


def test_stage_tracks_oldest_newest_and_counts():
    add(minute=5, channel=11)
    add(minute=0, channel=10)
    add(minute=5, channel=12)
    e = t._ACTIVITY_BUFFER[(1, 2, "message")]
    assert e["count"] == 3
    assert e["first_occurred_at"] == ts(0)
    assert e["occurred_at"] == ts(5)
    assert e["channel_id"] == 12


def test_stage_separates_types_and_records_last_arrival_order():
    add(typ="message")
    add(typ="reaction")
    add(typ="message", channel=20)
    a = t._ACTIVITY_BUFFER
    assert len(a) == 2
    assert a[(1, 2, "message")]["order"] > a[(1, 2, "reaction")]["order"]


def test_record_direct_stages_without_rpc(monkeypatch):
    calls = []
    monkeypatch.setattr(t, "_rpc_sync", lambda *a, **kw: calls.append(a))
    ok = asyncio.run(t.record_direct_member_activity(
        guild_id=1, user_id=2, activity_type="message",
        occurred_at=ts(), channel_id=10,
    ))
    assert ok and calls == [] and len(t._ACTIVITY_BUFFER) == 1


def test_invalid_direct_event_rejected():
    assert not asyncio.run(t.record_direct_member_activity(
        guild_id=0, user_id=2, activity_type="message",
    ))
    assert not asyncio.run(t.record_direct_member_activity(
        guild_id=1, user_id=2, activity_type="presence",
    ))


def test_batch_payload_counts_and_id(monkeypatch):
    requests = []
    monkeypatch.setattr(t, "_rpc_sync", lambda name, args: requests.append((name, args)))
    add(count=5)
    assert asyncio.run(t._flush_once())
    batches = [(name, args) for name, args in requests
               if name == "record_member_activity_batch"]
    assert len(batches) == 1
    name, args = batches[0]
    assert name == "record_member_activity_batch"
    assert args["p_batch_id"]
    assert args["p_events"][0]["event_count"] == 5
    assert args["p_events"][0]["first_occurred_at"] == ts().isoformat()
    assert t._PENDING_BATCH is None and not t._ACTIVITY_BUFFER


def test_uncertain_ack_retries_same_batch_not_old_single_rpc(monkeypatch):
    requests, applied = [], set()
    def rpc(name, args):
        if name == "ack_member_activity_batches":
            return
        requests.append((name, args["p_batch_id"]))
        if args["p_batch_id"] not in applied:
            applied.add(args["p_batch_id"])
            raise TimeoutError("commit succeeded, HTTP reply lost")
    monkeypatch.setattr(t, "_rpc_sync", rpc)
    monkeypatch.setattr(t, "_fail_tracker_sync", lambda gid, message: None)
    add(count=9)
    assert not asyncio.run(t._flush_once())
    assert t._PENDING_BATCH is not None
    assert 1 in t._LOCAL_ERRORS
    assert asyncio.run(t._flush_once())
    assert requests[0][1] == requests[1][1]
    assert all(name == "record_member_activity_batch" for name, _ in requests)
    assert len(applied) == 1
    assert t._PENDING_BATCH is None
    assert 1 not in t._LOCAL_ERRORS


def test_db_outage_does_not_fallback_to_per_event_storm(monkeypatch):
    seen = []
    def rpc(name, args):
        seen.append(name)
        raise RuntimeError("supabase unavailable")
    monkeypatch.setattr(t, "_rpc_sync", rpc)
    monkeypatch.setenv("DANK_ACTIVITY_BREAKER_THRESHOLD", "2")
    add(count=400)
    assert not asyncio.run(t._flush_once())
    assert not asyncio.run(t._flush_once())
    assert t._BREAKER_OPEN_UNTIL > time.monotonic()
    assert t._PENDING_BATCH is not None
    assert seen == ["record_member_activity_batch"] * 2
    assert not asyncio.run(t._flush_once())
    assert len(seen) == 2


def test_chunk_cap_and_sequential_drain(monkeypatch):
    calls = []
    monkeypatch.setattr(
        t, "_rpc_sync",
        lambda name, args: calls.append(args)
        if name == "record_member_activity_batch" else None,
    )
    for uid in range(1, 503):
        add(user=uid)
    assert asyncio.run(t._flush_once())
    assert asyncio.run(t._flush_once())
    assert [len(x["p_events"]) for x in calls] == [500, 2]
    assert all(len(x["p_events"]) <= 500 for x in calls)


def test_report_failure_keeps_cleanup_blocked(monkeypatch):
    monkeypatch.setattr(t, "_rpc_sync", lambda name, args: None)
    def fail(*args):
        raise RuntimeError("tracker state cannot be persisted")
    monkeypatch.setattr(t, "_fail_tracker_sync", fail)
    add()
    t._PENDING_FAIL_REPORTS[1] = "failed activity previously"
    t._LOCAL_ERRORS[1] = "failed activity previously"
    assert asyncio.run(t._flush_once())
    assert t._LOCAL_ERRORS[1]
    assert 1 in t._PENDING_FAIL_REPORTS


def test_overflow_fails_closed_and_resets_durable_window(monkeypatch):
    monkeypatch.setenv("DANK_ACTIVITY_BUFFER_MAX_ENTRIES", "1000")
    for uid in range(1, 1003):
        add(user=uid)
    assert t._DROPPED_EVENT_COUNT == 2
    assert t._LOST_COVERAGE_GUILDS == {1}
    assert 1 in t._LOCAL_ERRORS
    resets = []
    monkeypatch.setattr(t, "_rpc_sync", lambda name, args: None)
    monkeypatch.setattr(t, "_start_tracker_sync", lambda gid: resets.append(gid))
    # Proof resets immediately after DB recovery, even for a busy guild.
    assert asyncio.run(t._flush_once())
    assert resets == [1]
    assert asyncio.run(t._flush_once())
    assert resets == [1]
    assert 1 not in t._LOST_COVERAGE_GUILDS
    assert 1 not in t._LOCAL_ERRORS


def test_fail_reports_are_bounded_per_flush(monkeypatch):
    monkeypatch.setattr(t, "_fail_tracker_sync", lambda gid, message: None)
    for gid in range(25):
        t._PENDING_FAIL_REPORTS[gid] = "error"
        t._LOCAL_ERRORS[gid] = "error"
    assert asyncio.run(t._flush_once())
    assert len(t._PENDING_FAIL_REPORTS) == 15
    assert asyncio.run(t._flush_once())
    assert len(t._PENDING_FAIL_REPORTS) == 5


def test_sql_poison_isolated_without_replaying_valid_events(monkeypatch):
    class InvalidActivity(Exception):
        code = "P0001"
    persisted = []
    def rpc(name, args):
        if name == "ack_member_activity_batches":
            return
        assert name == "record_member_activity_batch"
        if any(e["user_id"] == "2" for e in args["p_events"]):
            raise InvalidActivity("unsupported direct activity type")
        persisted.extend(e["user_id"] for e in args["p_events"])
    resets = []
    monkeypatch.setattr(t, "_rpc_sync", rpc)
    monkeypatch.setattr(t, "_fail_tracker_sync", lambda *a: None)
    monkeypatch.setattr(t, "_start_tracker_sync", lambda gid: resets.append(gid))
    add(user=2)
    add(user=3)
    assert asyncio.run(t._flush_once())  # rejects parent, bisects
    assert asyncio.run(t._flush_once())  # quarantines bad singleton
    assert asyncio.run(t._flush_once())  # commits good singleton
    assert persisted == ["3"]
    assert t._DROPPED_EVENT_COUNT == 1
    assert resets == [1]
    assert t._PENDING_BATCH is None and not t._PENDING_SPLITS
