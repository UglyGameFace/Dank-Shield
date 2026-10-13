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
