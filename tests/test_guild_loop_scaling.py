from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify.guild_sweep import run_bounded_guild_sweep


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_bounded_worker_pool_processes_each_guild_once() -> None:
    async def scenario():
        running = 0
        peak = 0
        seen = []

        async def visit(guild):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            seen.append(guild)
            running -= 1
            return True

        stats = await run_bounded_guild_sweep(range(300), visit, concurrency=7)
        assert stats.total == 300
        assert stats.succeeded == 300
        assert stats.failed == 0
        assert stats.concurrency == 7
        assert 1 < peak <= 7
        assert sorted(seen) == list(range(300))

    asyncio.run(scenario())


def test_exceptions_are_isolated_per_guild() -> None:
    async def scenario():
        seen = []
        errors = []

        async def visit(guild):
            seen.append(guild)
            if guild == 4:
                raise RuntimeError("guild failure")
            return guild != 7

        stats = await run_bounded_guild_sweep(
            range(10), visit, concurrency=3,
            on_error=lambda guild, exc: errors.append((guild, type(exc).__name__)),
        )
        assert sorted(seen) == list(range(10))
        assert errors == [(4, "RuntimeError")]
        assert stats.succeeded == 8
        assert stats.failed == 2

    asyncio.run(scenario())


def test_worker_pool_cancellation_cancels_children() -> None:
    async def scenario():
        entered = asyncio.Event()
        active = 0

        async def blocked(_guild):
            nonlocal active
            active += 1
            entered.set()
            try:
                await asyncio.sleep(3600)
            finally:
                active -= 1

        task = asyncio.create_task(
            run_bounded_guild_sweep(range(100), blocked, concurrency=4)
        )
        await entered.wait()
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("sweep swallowed cancellation")
        assert active == 0

    asyncio.run(scenario())


def test_empty_sweep_has_no_tasks() -> None:
    async def scenario():
        async def should_not_run(_guild):
            raise AssertionError("empty sweep must not call handler")
        result = await run_bounded_guild_sweep([], should_not_run, concurrency=8)
        assert (result.total, result.concurrency) == (0, 0)
    asyncio.run(scenario())


def test_heartbeat_uses_single_bounded_process_cycle_and_preserves_fail_closed() -> None:
    src = _source("stoney_verify/members_new/activity_tracker.py")
    block = src.split("async def _heartbeat_loop(", 1)[1].split(
        "async def _on_ready(", 1
    )[0]
    guild = src.split("async def _heartbeat_one_guild(", 1)[1].split(
        "async def _heartbeat_loop(", 1
    )[0]
    assert "run_bounded_guild_sweep(" in block
    assert "elapsed_s=" in block and "overrun=" in block
    assert "_db_breaker_open()" in guild
    assert "_PENDING_FAIL_REPORTS" in guild
    assert guild.index("try:\n        await _refresh_scope_status(guild)") < guild.index(
        "await asyncio.to_thread(_heartbeat_sync, gid)"
    )
    error_block = src.split("def _combined_local_error(", 1)[1].split(
        "def _rpc_sync(", 1
    )[0]
    assert "if _db_breaker_open():" in error_block
    assert "_LOCAL_ERRORS.pop" in guild
    assert "await _mark_failure(" in guild
    assert "_STARTUP_TASKS" in guild


def test_metrics_keeps_per_guild_role_before_staff_and_pacing() -> None:
    src = _source("stoney_verify/workers/metrics_sync_worker.py")
    guild = src.split("async def _sync_metrics_for_guild(", 1)[1].split(
        "async def metrics_loop(", 1
    )[0]
    loop = src.split("async def metrics_loop(", 1)[1].split(
        "def start_metrics_worker(", 1
    )[0]
    assert guild.index("await update_role_metrics(") < guild.index(
        "await update_staff_metrics("
    )
    assert "run_bounded_guild_sweep(" in loop
    assert "random.uniform" in loop
    assert "max(10.0, SYNC_INTERVAL - result.seconds)" in loop


def test_ticket_sweep_serializes_same_guild_and_jitters() -> None:
    src = _source("stoney_verify/workers/ticket_automation_worker.py")
    assert "WeakValueDictionary" in src
    assert "async with _automation_guild_lock(int(guild.id)):" in src
    assert "run_bounded_guild_sweep(" in src
    assert "max(10.0, AUTOMATION_POLL_SECONDS - elapsed)" in src
    assert 'overall["guilds_failed"]' in src
    assert "_process_ticket(guild, row, settings)" in src


def test_spam_guard_warm_nonblocking_and_stops_outage_storm() -> None:
    src = _source("stoney_verify/spam_guard.py")
    ready = src.split('@bot.listen("on_ready")\nasync def _spam_guard_warm_settings_cache():', 1)[1].split(
        "def register_spam_guard_persistent_views(", 1
    )[0]
    assert "asyncio.create_task(" in ready
    assert "_SPAM_GUARD_WARM_TASK" in ready
    assert "run_bounded_guild_sweep(" in src
    assert "if unpersisted_count >= 5:" in src
    assert "get_spam_settings(guild.id)" in src


def test_canary_defaults_are_not_activated_in_code() -> None:
    src = _source("stoney_verify/command_runtime.py")
    assert 'return _env_bool("DISCORD_AUTO_SHARD", False)' in src
    assert "DankAutoShardedBot" in src
    assert "chunk_guilds_at_startup" in src


def test_ticket_enabled_scan_is_paginated_and_no_default_guilds_are_fetched(monkeypatch):
    from types import SimpleNamespace
    from stoney_verify.workers import ticket_automation_worker as worker

    calls = []
    data = [{"guild_id": str(i)} for i in range(1, 1252)]

    class Query:
        def select(self, *a):
            return self

        def eq(self, key, value):
            assert key == "enabled" and value is True
            return self

        def order(self, key):
            assert key == "guild_id"
            return self

        def range(self, start, end):
            self.start, self.end = start, end
            calls.append((start, end))
            return self

        def execute(self):
            return SimpleNamespace(data=data[self.start:self.end + 1])

    monkeypatch.setattr(worker, "_settings_table", lambda: Query())
    monkeypatch.setattr(worker, "_execute_db", lambda _label, executor: executor())
    assert worker._list_enabled_ticket_automation_guild_ids_sync() == set(
        range(1, 1252)
    )
    assert calls == [(0, 999), (1000, 1999)]


def test_ticket_enabled_scan_db_failure_aborts_without_fallback(monkeypatch):
    from stoney_verify.workers import ticket_automation_worker as worker

    def unavailable(*a):
        raise RuntimeError("Supabase unavailable")

    monkeypatch.setattr(worker, "_execute_db", unavailable)
    try:
        worker._list_enabled_ticket_automation_guild_ids_sync()
    except RuntimeError as exc:
        assert "Supabase unavailable" in str(exc)
    else:
        raise AssertionError("ticket scan must fail closed on DB errors")


def test_heartbeat_breaker_blocks_every_guild_before_an_rpc(monkeypatch):
    from types import SimpleNamespace
    from stoney_verify.members_new import activity_tracker as tracker

    monkeypatch.setattr(tracker, "_STARTED_GUILDS", {101})
    monkeypatch.setattr(tracker, "_STARTUP_TASKS", {})
    monkeypatch.setattr(tracker, "_db_breaker_open", lambda: True)

    async def prohibited(*a, **kw):
        raise AssertionError("breaker must prevent DB traffic")
    monkeypatch.setattr(tracker, "_refresh_scope_status", prohibited)
    assert asyncio.run(tracker._heartbeat_one_guild(SimpleNamespace(id=101))) is False
    assert "circuit breaker" in tracker._combined_local_error(202)


def test_heartbeat_scope_exception_fails_closed(monkeypatch):
    from types import SimpleNamespace
    from stoney_verify.members_new import activity_tracker as tracker

    monkeypatch.setattr(tracker, "_STARTED_GUILDS", {101})
    monkeypatch.setattr(tracker, "_STARTUP_TASKS", {})
    monkeypatch.setattr(tracker, "_db_breaker_open", lambda: False)
    errors = []

    async def fail_scope(_guild):
        raise RuntimeError("scope audit exception")

    async def record_error(guild_id, message):
        errors.append((guild_id, message))

    monkeypatch.setattr(tracker, "_refresh_scope_status", fail_scope)
    monkeypatch.setattr(tracker, "_mark_failure", record_error)
    assert asyncio.run(tracker._heartbeat_one_guild(SimpleNamespace(id=101))) is False
    assert len(errors) == 1 and errors[0][0] == 101
    assert "heartbeat failed" in errors[0][1]


def test_ticket_automation_same_guild_lock_is_shared(monkeypatch):
    from stoney_verify.workers import ticket_automation_worker as worker

    monkeypatch.setattr(worker, "_AUTOMATION_GUILD_LOCKS", worker.WeakValueDictionary())

    async def scenario():
        first_entered = asyncio.Event()
        release = asyncio.Event()
        second_entered = asyncio.Event()

        async def first():
            async with worker._automation_guild_lock(11):
                first_entered.set()
                await release.wait()

        async def second():
            async with worker._automation_guild_lock(11):
                second_entered.set()

        a = asyncio.create_task(first())
        await first_entered.wait()
        b = asyncio.create_task(second())
        await asyncio.sleep(0)
        assert not second_entered.is_set()
        release.set()
        await asyncio.gather(a, b)
        assert second_entered.is_set()
    asyncio.run(scenario())
