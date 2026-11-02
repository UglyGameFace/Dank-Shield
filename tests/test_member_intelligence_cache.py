"""Safety and concurrency regressions for the staff-only Member Intelligence cache."""
from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify.tickets_new.member_intelligence_cache import (
    MemberIntelligenceSnapshotCache,
)


def _snapshot(*, ok=True, source_status=None, name="Person", history=True):
    return {
        "ok": ok,
        "member": {"username": name} if name else {},
        "source_status": ({"profile": "available", "events": "sampled"}
                          if source_status is None else source_status),
        "recent_events": [{"event_type": "warn", "reason": "evidence"}] if history else [],
    }


def _clock_cache(*, ttl=25.0, negative_ttl=5.0, stale=120.0, max_entries=128):
    current = [100.0]
    cache = MemberIntelligenceSnapshotCache(
        ttl_seconds=ttl, negative_ttl_seconds=negative_ttl,
        stale_seconds=stale, max_entries=max_entries, clock=lambda: current[0],
    )
    return cache, current


def test_fresh_hit_ttl_force_refresh_and_mutation_isolation():
    cache, clock = _clock_cache()
    calls = [0]
    async def loader():
        calls[0] += 1
        return _snapshot(name=f"Account{calls[0]}")
    async def steps():
        first = await cache.get_or_load(("1", "10"), loader)
        assert first["cache_state"] == "fresh"
        first["member"]["username"] = "MUTATED"
        again = await cache.get_or_load(("1", "10"), loader)
        assert again["cache_state"] == "hit"
        assert again["member"]["username"] == "Account1"
        assert calls == [1]
        clock[0] += 26
        refreshed = await cache.get_or_load(("1", "10"), loader)
        assert refreshed["cache_state"] == "fresh"
        assert refreshed["member"]["username"] == "Account2"
        forced = await cache.get_or_load(("1", "10"), loader, force_refresh=True)
        assert forced["member"]["username"] == "Account3"
        assert calls == [3]
    asyncio.run(steps())


def test_negative_cache_short_ttl():
    cache, clock = _clock_cache()
    calls = [0]
    async def empty():
        calls[0] += 1
        return _snapshot(name=None, history=False)
    async def steps():
        assert (await cache.get_or_load(("1", "10"), empty))["cache_state"] == "fresh"
        clock[0] += 4
        assert (await cache.get_or_load(("1", "10"), empty))["cache_state"] == "hit"
        clock[0] += 2
        assert (await cache.get_or_load(("1", "10"), empty))["cache_state"] == "fresh"
        assert calls == [2]
    asyncio.run(steps())


def test_outage_stale_cache_labeled_and_never_rewrites_fresh_window():
    cache, clock = _clock_cache()
    async def good():
        return _snapshot(name="Original")
    async def outage():
        return _snapshot(
            ok=False, name=None, history=False,
            source_status={"profile": "unavailable", "events": "unavailable"},
        )
    async def steps():
        await cache.get_or_load(("1", "10"), good)
        clock[0] += 27
        stale = await cache.get_or_load(("1", "10"), outage)
        assert stale["cache_state"] == "stale"
        assert stale["cache_refresh_unavailable"] == ["profile", "events"]
        assert "older retained evidence" in stale["cache_warning"]
        assert stale["cache_age_seconds"] == 27
        clock[0] += 95
        partial = await cache.get_or_load(("1", "10"), outage)
        assert partial["cache_state"] == "partial"
        assert partial["source_status"]["profile"] == "unavailable"
        assert partial["member"] == {}
    asyncio.run(steps())


def test_partial_outage_must_not_reuse_old_snapshot_or_be_cached():
    cache, clock = _clock_cache()
    calls = [0]
    async def loader():
        calls[0] += 1
        if calls[0] == 1:
            return _snapshot(name="Initial")
        return _snapshot(
            name=f"Updated{calls[0]}",
            source_status={"profile": "available", "events": "unavailable"},
        )
    async def steps():
        await cache.get_or_load(("1", "10"), loader)
        clock[0] += 26
        first = await cache.get_or_load(("1", "10"), loader)
        second = await cache.get_or_load(("1", "10"), loader)
        assert first["cache_state"] == second["cache_state"] == "partial"
        assert first["member"]["username"] == "Updated2"
        assert second["member"]["username"] == "Updated3"
        assert calls == [3]
    asyncio.run(steps())


def test_singleflight_same_key_and_isolation_by_guild():
    cache, _ = _clock_cache()
    calls = [0]
    async def loader():
        calls[0] += 1
        await asyncio.sleep(0.02)
        return _snapshot()
    async def steps():
        rows = await asyncio.gather(*[
            cache.get_or_load(("guild-a", "user"), loader) for _ in range(15)
        ])
        assert len(rows) == 15
        assert calls == [1]
        await cache.get_or_load(("guild-b", "user"), loader)
        await cache.get_or_load(("guild-a", "other-user"), loader)
        assert calls == [3]
    asyncio.run(steps())


def test_invalidation_prevents_inflight_resurrection():
    cache, _ = _clock_cache()
    async def steps():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def old():
            entered.set()
            await release.wait()
            return _snapshot(name="Old")
        lookup = asyncio.create_task(cache.get_or_load(("g", "u", 15), old))
        await entered.wait()
        cache.invalidate("g", "u")
        release.set()
        result = await lookup
        assert result["cache_state"] == "superseded"
        assert not cache._entries
        async def new():
            return _snapshot(name="New")
        fresh = await cache.get_or_load(("g", "u", 15), new)
        assert fresh["member"]["username"] == "New"
        assert fresh["cache_state"] == "fresh"
    asyncio.run(steps())


def test_waiter_cancellation_does_not_cancel_shared_read():
    cache, _ = _clock_cache()
    async def steps():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = [0]
        async def loader():
            calls[0] += 1
            entered.set()
            await release.wait()
            return _snapshot()
        first = asyncio.create_task(cache.get_or_load(("1", "10"), loader))
        await entered.wait()
        second = asyncio.create_task(cache.get_or_load(("1", "10"), loader))
        await asyncio.sleep(0)
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        release.set()
        result = await second
        assert result["cache_state"] == "fresh"
        assert calls == [1]
    asyncio.run(steps())


def test_capacity_and_keyed_invalidation():
    cache, _ = _clock_cache(max_entries=2)
    async def loader():
        return _snapshot()
    async def steps():
        await cache.get_or_load(("g", "u1", 1), loader)
        await cache.get_or_load(("g", "u2", 1), loader)
        await cache.get_or_load(("g", "u3", 1), loader)
        assert len(cache._entries) == 2
        cache.invalidate("g", "u2")
        assert ("g", "u2", 1) not in cache._entries
        assert ("g", "u3", 1) in cache._entries
    asyncio.run(steps())


def test_bad_source_status_is_never_cached():
    cache, _ = _clock_cache()
    async def no_status():
        return {"ok": True, "source_status": {}, "member": {"username": "Unverified"}}
    async def steps():
        r = await cache.get_or_load(("1", "10"), no_status)
        assert r["cache_state"] == "uncached"
        assert not cache._entries
    asyncio.run(steps())


def test_production_cache_integration_is_staff_only_and_cannot_cache_live_status():
    root = Path(__file__).resolve().parents[1]
    service = (root / "stoney_verify/tickets_new/member_context_service.py").read_text()
    ui = (root / "stoney_verify/commands_ext/member_command_center.py").read_text()
    events = (root / "stoney_verify/events.py").read_text()
    join = (root / "stoney_verify/members_new/join_context_service.py").read_text()
    assert "use_intelligence_cache: bool = False" in service
    assert "force_refresh: bool = False" in service
    assert "use_intelligence_cache=True" in ui
    assert "force_refresh=force_refresh" in ui
    assert "force_refresh=True" in ui
    assert "_resolve_intelligence_member(interaction.guild, user_id)" in ui
    assert "invalidate_member_intelligence" in events
    assert "invalidate_member_intelligence(guild_id, user_id)" in join


def test_unexpected_loader_error_is_not_misreported_as_known_db_outage():
    cache, clock = _clock_cache()
    async def good():
        return _snapshot()
    async def bad():
        raise ValueError("programmer bug")
    async def steps():
        await cache.get_or_load(("g", "u"), good)
        clock[0] += 26
        try:
            await cache.get_or_load(("g", "u"), bad)
        except ValueError as exc:
            assert str(exc) == "programmer bug"
        else:
            raise AssertionError("Unexpected reader failure must propagate")
        # A failed load does not overwrite the last known validated snapshot.
        assert ("g", "u") in cache._entries
    asyncio.run(steps())
