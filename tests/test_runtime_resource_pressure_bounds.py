"""Resource-pressure regressions: no growing join/identity caches or runaway work."""
from __future__ import annotations

import gc
import weakref
from collections import deque
from datetime import timedelta
from types import SimpleNamespace

from stoney_verify import events, raidguard
from stoney_verify.globals import JOIN_TIMES
from stoney_verify.members_new import join_context_service as join_context
from stoney_verify import profile_card_runtime as cards
from stoney_verify import share_router_media_resolver as media
from stoney_verify.workers import metrics_sync_worker as metrics


def test_join_burst_prunes_expired_timestamps_and_limits_new_joins():
    gid = 998000001
    now = raidguard._utcnow()
    JOIN_TIMES[gid] = deque([now - timedelta(hours=1), now])
    try:
        assert raidguard._recent_join_burst_count(gid) == 1
        assert len(JOIN_TIMES[gid]) == 1
        events._ensure_gid_join_deque(JOIN_TIMES, gid)
        for _ in range(events._MAX_RAID_JOIN_TIMESTAMPS + 100):
            JOIN_TIMES[gid].append(now)
        assert len(JOIN_TIMES[gid]) == events._MAX_RAID_JOIN_TIMESTAMPS
    finally:
        JOIN_TIMES.pop(gid, None)


def test_member_processing_lock_drops_without_live_references():
    gid, uid = 998000002, 998000003
    lock = events._get_member_processing_lock(gid, uid)
    assert events._get_member_processing_lock(gid, uid) is lock
    ref = weakref.ref(lock)
    del lock
    gc.collect()
    assert ref() is None
    assert (gid, uid) not in events._JOIN_PROCESS_LOCKS


def test_recent_join_context_is_bounded_and_expires_without_key_lookup(monkeypatch):
    fake_clock = [1000.0]
    monkeypatch.setattr(join_context.time, "monotonic", lambda: fake_clock[0])
    join_context._RECENT_JOIN_CONTEXT.clear()
    try:
        for uid in range(join_context._RECENT_JOIN_CONTEXT_MAX_ENTRIES + 40):
            join_context._remember_recent_join_context(998000005, uid, {"invited_by": uid})
        assert len(join_context._RECENT_JOIN_CONTEXT) == join_context._RECENT_JOIN_CONTEXT_MAX_ENTRIES
        fake_clock[0] += 181
        join_context._remember_recent_join_context(998000005, 999999, {"ok": True})
        assert len(join_context._RECENT_JOIN_CONTEXT) == 1
        join_context.forget_recent_join_context(998000005, 999999)
        assert not join_context._RECENT_JOIN_CONTEXT
    finally:
        join_context._RECENT_JOIN_CONTEXT.clear()


def test_identity_proof_cache_expires_and_is_bounded():
    cache = raidguard._HARD_PROOF_CACHE
    cache.clear()
    try:
        old = raidguard._utcnow() - timedelta(minutes=5)
        for uid in range(raidguard._PROOF_CACHE_MAX_ENTRIES + 30):
            cache[(998000006, uid)] = (old, {"x": uid})
        raidguard._prune_hard_proof_cache()
        assert len(cache) == 0
        for uid in range(raidguard._PROOF_CACHE_MAX_ENTRIES + 30):
            cache[(998000006, uid)] = (raidguard._utcnow(), {"x": uid})
        raidguard._prune_hard_proof_cache()
        assert len(cache) <= raidguard._PROOF_CACHE_MAX_ENTRIES
    finally:
        cache.clear()


def test_profile_png_cache_has_total_byte_budget():
    cards._SIGNATURE_CACHE.clear()
    try:
        payload = b"x" * (1024 * 1024)
        for index in range(40):
            cards._signature_cache_put((index,), payload)
        assert sum(len(data) for _time, data in cards._SIGNATURE_CACHE.values()) <= cards._SIGNATURE_CACHE_MAX_BYTES
        cards._signature_cache_put(("oversized",), b"x" * (cards._SIGNATURE_CACHE_MAX_BYTES + 1))
        assert ("oversized",) not in cards._SIGNATURE_CACHE
    finally:
        cards._SIGNATURE_CACHE.clear()


def test_role_metrics_counts_member_roles_once(monkeypatch):
    class Role:
        id = 22
        name = "Member"
        position = 1
        @property
        def members(self):
            raise AssertionError("must not rescan guild for every role")
    role = Role()
    member = SimpleNamespace(roles=[role])
    guild = SimpleNamespace(id=998000007, roles=[role], members=[member, member])
    class DB:
        def table(self, name):
            assert name == "guild_roles"
            return self
        def upsert(self, rows, **kwargs):
            assert rows[0]["member_count"] == 2
            return self
        def execute(self):
            return SimpleNamespace(data=[])
    monkeypatch.setattr(metrics, "get_supabase", lambda **kwargs: DB())
    metrics._LAST_ROLE_SIGNATURES.clear()
    assert metrics._sync_role_metrics(guild) == 1


def test_extractor_thread_guard_rejects_when_workers_are_busy(monkeypatch):
    held = 0
    while media._EXTRACT_THREAD_SLOTS.acquire(blocking=False):
        held += 1
    try:
        assert held >= 1
        monkeypatch.setattr(media, "_extract_info_sync", lambda _url: (_ for _ in ()).throw(AssertionError("called despite saturation")))
        result = media._extract_info_guarded_sync("https://example.org/item")
        assert result.reason == "extractor_thread_capacity_busy"
    finally:
        for _ in range(held):
            media._EXTRACT_THREAD_SLOTS.release()


def test_process_cpu_percent_and_resource_pressure_snapshot(monkeypatch):
    from stoney_verify.startup_guards import process_health

    assert process_health._process_cpu_percent(10.0, 2.0, 20.0, 4.0) == 20.0
    assert process_health._process_cpu_percent(20.0, 4.0, 20.0, 5.0) == 0.0
    assert process_health._process_cpu_percent(20.0, 4.0, 30.0, 3.0) == 0.0
    snapshot = process_health._resource_pressure_snapshot()
    assert "resource_pressure=" in snapshot
    assert "proof_cache=" in snapshot
