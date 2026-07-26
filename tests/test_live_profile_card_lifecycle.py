import asyncio

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import LiveProfileCardRuntime


class FakeBot:
    user = None
    guilds = []

    def get_guild(self, _guild_id):
        return None


def test_registration_uses_additive_ready_listener_not_import_time_task():
    source = open(
        "stoney_verify/commands_ext/public_profile_cards.py",
        "r",
        encoding="utf-8",
    ).read()
    assert source.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert source.count('bot.add_listener(runtime.on_ready, "on_ready")') == 1
    assert "asyncio.create_task(runtime.reconcile_after_ready())" not in source
    assert "_reconcile_task" not in source


def test_runtime_first_ready_runs_even_when_host_monotonic_is_under_one_minute(monkeypatch):
    async def scenario():
        clock = iter([10.0, 10.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(clock))
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def reconcile():
            calls.append("run")

        runtime.reconcile = reconcile
        await runtime.on_ready()
        assert calls == ["run"]

    asyncio.run(scenario())


def test_runtime_ready_listener_coalesces_concurrent_ready_events(monkeypatch):
    async def scenario():
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []
        gate = asyncio.Event()

        async def reconcile():
            calls.append("start")
            await gate.wait()

        runtime.reconcile = reconcile
        first = asyncio.create_task(runtime.on_ready())
        await asyncio.sleep(0)
        second = asyncio.create_task(runtime.on_ready())
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        assert calls == ["start"]

    asyncio.run(scenario())


def test_runtime_ready_listener_throttles_repeated_reconnects(monkeypatch):
    async def scenario():
        clock = iter([100.0, 100.0, 101.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(clock))
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def reconcile():
            calls.append("run")

        runtime.reconcile = reconcile
        await runtime.on_ready()
        await runtime.on_ready()
        assert calls == ["run"]

    asyncio.run(scenario())


def test_runtime_ready_listener_fails_closed_without_retry_storm(monkeypatch):
    async def scenario():
        clock = iter([200.0, 200.0, 201.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(clock))
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def reconcile():
            calls.append("run")
            raise RuntimeError("storage unavailable")

        runtime.reconcile = reconcile
        await runtime.on_ready()
        await runtime.on_ready()
        assert calls == ["run"]

    asyncio.run(scenario())
