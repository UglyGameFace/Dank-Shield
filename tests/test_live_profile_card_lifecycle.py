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


def test_runtime_first_ready_enables_lazy_recovery_without_global_reconcile(monkeypatch):
    async def scenario():
        monkeypatch.setattr(runtime_module, "monotonic", lambda: 10.0)
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def forbidden_reconcile():
            calls.append("run")
            raise AssertionError("ready must not scan every guild/channel")

        runtime.reconcile = forbidden_reconcile
        await runtime.on_ready()

        assert calls == []
        assert runtime._last_reconcile_at == 10.0

    asyncio.run(scenario())


def test_runtime_concurrent_ready_events_never_start_history_scans(monkeypatch):
    async def scenario():
        values = iter([20.0, 21.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(values))
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def forbidden_reconcile():
            calls.append("run")
            raise AssertionError("concurrent ready events must remain I/O-free")

        runtime.reconcile = forbidden_reconcile
        await asyncio.gather(runtime.on_ready(), runtime.on_ready())

        assert calls == []
        assert runtime._last_reconcile_at == 21.0

    asyncio.run(scenario())


def test_runtime_reconnects_leave_durable_recovery_on_the_message_path(monkeypatch):
    async def scenario():
        values = iter([30.0, 31.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(values))
        runtime = LiveProfileCardRuntime(FakeBot())
        calls = []

        async def forbidden_reconcile():
            calls.append("run")
            raise RuntimeError("storage unavailable")

        runtime.reconcile = forbidden_reconcile
        await runtime.on_ready()
        await runtime.on_ready()

        assert calls == []
        assert runtime._last_reconcile_at == 31.0

    asyncio.run(scenario())
