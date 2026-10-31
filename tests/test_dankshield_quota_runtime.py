"""Regressions for quota repair and Discord gateway responsiveness."""
from __future__ import annotations

import asyncio
import ast
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_command_queue_gateway_offloads_all_blocking_database_calls() -> None:
    source = (ROOT / "stoney_verify/tasks_new/command_queue.py").read_text()
    tree = ast.parse(source)
    loop = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_queue_loop"
    )
    threaded = {
        node.args[0].id
        for node in ast.walk(loop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
        and node.func.attr == "to_thread"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert {
        "_fetch_next_pending_command", "_claim_command",
        "_complete_command", "_fail_command",
    }.issubset(threaded)
    assert "_DB_FAILURE_POLL_SECONDS = 30.0" in source
    assert "_POLL_SECONDS = 5.0" in source


def test_idle_worker_uses_wakeable_backoff_not_permanent_busy_polling() -> None:
    source = (ROOT / "stoney_verify/trusted_advert_bulk.py").read_text()
    ast.parse(source)
    assert "_WAKE_QUEUE.put_nowait(None)" in source
    assert "await asyncio.wait_for(_WAKE_QUEUE.get(), timeout=idle_delay)" in source
    assert "_IDLE_MAX_SECONDS = 300" in source


def test_backend_heartbeat_migration_is_private_and_additive() -> None:
    migration = (
        ROOT / "supabase_fresh/migrations/"
        "20261030183000_dankshield_status_heartbeat.sql"
    ).read_text().lower()
    for field in ("bot_id text primary key", "last_seen_at timestamptz",
                  "guild_count integer", "updated_at timestamptz"):
        assert field in migration
    assert "enable row level security" in migration
    assert "from public, anon, authenticated" in migration
    assert "to service_role" in migration
    assert "drop table" not in migration
    assert "delete from" not in migration


def test_command_queue_worker_does_not_fetch_on_event_loop(monkeypatch) -> None:
    from stoney_verify.tasks_new import command_queue as mod

    caller = threading.get_ident()
    observed: list[int] = []
    waits: list[float] = []

    class FakeBot:
        closed = False

        async def wait_until_ready(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

    bot = FakeBot()

    def fetch():
        observed.append(threading.get_ident())
        bot.closed = True
        return None

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(mod, "bot", bot)
    monkeypatch.setattr(mod, "_fetch_next_pending_command", fetch)
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    asyncio.run(mod._queue_loop())
    assert observed and all(worker != caller for worker in observed)
    assert waits == [mod._POLL_SECONDS]


def test_command_queue_storage_failure_is_rate_limited(monkeypatch) -> None:
    from stoney_verify.tasks_new import command_queue as mod

    waits: list[float] = []

    class FakeBot:
        closed = False

        async def wait_until_ready(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

    bot = FakeBot()

    def failing_fetch():
        bot.closed = True
        raise RuntimeError("database unreachable")

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(mod, "bot", bot)
    monkeypatch.setattr(mod, "_fetch_next_pending_command", failing_fetch)
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    asyncio.run(mod._queue_loop())
    assert waits == [mod._DB_FAILURE_POLL_SECONDS]


def test_app_starts_canonical_bot_command_worker_not_legacy_queue() -> None:
    app = (ROOT / "stoney_verify/app.py").read_text()
    worker = (ROOT / "stoney_verify/workers/bot_command_worker.py").read_text()
    assert "from .workers.bot_command_worker import start_worker" in app
    assert "worker_task = start_worker()" in app
    assert "start_command_queue_worker(" not in app
    assert "POLL_INTERVAL = 5.0" in worker
    assert "DB_FAILURE_POLL_SECONDS = 30.0" in worker
    ast.parse(worker)


def test_active_command_worker_idle_queue_uses_five_second_poll(monkeypatch) -> None:
    from stoney_verify.workers import bot_command_worker as mod

    waits: list[float] = []

    class FakeBot:
        closed = False

        async def wait_until_ready(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

    bot = FakeBot()

    async def empty_fetch():
        bot.closed = True
        return None

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(mod, "bot", bot)
    monkeypatch.setattr(mod, "fetch_pending_command", empty_fetch)
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    asyncio.run(mod.worker_loop())
    assert waits == [mod.POLL_INTERVAL]


def test_active_command_worker_storage_failure_backs_off(monkeypatch) -> None:
    from stoney_verify.workers import bot_command_worker as mod

    waits: list[float] = []

    class FakeBot:
        closed = False

        async def wait_until_ready(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

    bot = FakeBot()

    async def failed_fetch():
        bot.closed = True
        raise RuntimeError("database unavailable")

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(mod, "bot", bot)
    monkeypatch.setattr(mod, "fetch_pending_command", failed_fetch)
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    asyncio.run(mod.worker_loop())
    assert waits == [mod.DB_FAILURE_POLL_SECONDS]


def test_active_command_worker_propagates_storage_failure(monkeypatch) -> None:
    from stoney_verify.workers import bot_command_worker as mod

    monkeypatch.setattr(mod, "get_supabase", lambda: None)
    with pytest.raises(RuntimeError, match="Supabase unavailable"):
        asyncio.run(mod.fetch_pending_command())
