from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.verification_new import basic_verify as runtime


class FakeBot:
    def __init__(
        self,
        *,
        fail_view: bool = False,
        fail_listener: bool = False,
    ) -> None:
        self.views: list[object] = []
        self.listeners: list[tuple[object, str]] = []
        self.fail_view = fail_view
        self.fail_listener = fail_listener

    def add_view(self, view: object) -> None:
        if self.fail_view:
            raise RuntimeError("view registration failed")
        self.views.append(view)

    def add_listener(self, listener: object, name: str) -> None:
        if self.fail_listener:
            raise RuntimeError("listener registration failed")
        self.listeners.append((listener, name))


def _reset_runtime_state() -> None:
    runtime._RUNTIME_VIEW_REGISTERED = False
    runtime._RUNTIME_FALLBACK_LISTENER_REGISTERED = False
    runtime._RUNTIME_REGISTRATION_ERROR = ""
    runtime._BASIC_VERIFY_LOCKS.clear()


@pytest.fixture(autouse=True)
def _isolated_runtime_state():
    _reset_runtime_state()
    try:
        yield
    finally:
        _reset_runtime_state()


def test_runtime_prefers_one_persistent_view_owner(monkeypatch) -> None:
    fake_bot = FakeBot()
    sentinel_view = object()
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: sentinel_view)

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert fake_bot.views == [sentinel_view]
    assert fake_bot.listeners == []
    assert runtime.basic_verify_runtime_status() == {
        "persistent_view_registered": True,
        "fallback_listener_registered": False,
        "ready": True,
        "error": "",
    }


def test_runtime_uses_listener_only_when_persistent_view_registration_fails(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert fake_bot.views == []
    assert fake_bot.listeners == [
        (runtime._basic_verify_fallback_listener, "on_interaction")
    ]
    status = runtime.basic_verify_runtime_status()
    assert status["persistent_view_registered"] is False
    assert status["fallback_listener_registered"] is True
    assert status["ready"] is True
    assert "view registration failed" in status["error"]


def test_runtime_does_not_add_a_second_owner_on_later_registration(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True
    assert len(fake_bot.listeners) == 1
    assert fake_bot.views == []

    fake_bot.fail_view = False
    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert len(fake_bot.listeners) == 1
    assert fake_bot.views == []
    assert runtime.basic_verify_runtime_status()["fallback_listener_registered"] is True


def test_runtime_install_is_idempotent_after_persistent_registration(
    monkeypatch,
) -> None:
    fake_bot = FakeBot()
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True
    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert len(fake_bot.views) == 1
    assert fake_bot.listeners == []


def test_strict_runtime_fails_closed_when_no_interaction_route_can_register(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True, fail_listener=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    with pytest.raises(RuntimeError, match="no registered interaction handler"):
        runtime.install_basic_verify_runtime(fake_bot, strict=True)

    status = runtime.basic_verify_runtime_status()
    assert status["ready"] is False
    assert "view registration failed" in status["error"]
    assert "listener registration failed" in status["error"]


def test_emergency_fallback_only_delegates_unacknowledged_basic_verify(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def fake_handler(interaction) -> bool:
            calls.append(interaction)
            return True

        class Response:
            def __init__(self, done: bool = False) -> None:
                self.done = done

            def is_done(self) -> bool:
                return self.done

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )

        matching = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=Response(),
        )
        acknowledged = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=Response(done=True),
        )
        unrelated = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": "something:else"},
            response=Response(),
        )

        await runtime._basic_verify_fallback_listener(unrelated)
        await runtime._basic_verify_fallback_listener(acknowledged)
        await runtime._basic_verify_fallback_listener(matching)

        assert calls == [matching]

    asyncio.run(scenario())


def test_button_callback_delegates_to_canonical_interaction_handler(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def fake_handler(interaction) -> bool:
            calls.append(interaction)
            return True

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )
        interaction = SimpleNamespace(id=1001)

        button = runtime.BasicVerifyButton()
        await button.callback(interaction)

        assert calls == [interaction]

    asyncio.run(scenario())


def test_verify_acknowledges_before_role_or_database_work(monkeypatch) -> None:
    async def scenario() -> None:
        events: list[tuple[str, object]] = []
        guild = SimpleNamespace(id=77)

        class FakeMember:
            def __init__(self) -> None:
                self.id = 88
                self.guild = guild

        class Response:
            def __init__(self) -> None:
                self.done = False

            def is_done(self) -> bool:
                return self.done

            async def defer(
                self,
                *,
                ephemeral: bool,
                thinking: bool,
            ) -> None:
                events.append(("defer", (ephemeral, thinking)))
                self.done = True

        class Followup:
            async def send(self, content: str, **kwargs) -> None:
                events.append(("followup", content))

        member = FakeMember()
        response = Response()
        interaction = SimpleNamespace(
            id=9001,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            guild=guild,
            channel=SimpleNamespace(id=99),
            user=member,
            response=response,
            followup=Followup(),
        )

        async def fake_apply(received_member) -> tuple[bool, str]:
            assert received_member is member
            assert response.is_done() is True
            events.append(("apply", received_member.id))
            return True, "verified"

        monkeypatch.setattr(runtime.discord, "Member", FakeMember)
        monkeypatch.setattr(runtime, "apply_basic_verification", fake_apply)

        handled = await runtime.maybe_handle_basic_verify_interaction(interaction)

        assert handled is True
        assert events[0] == ("defer", (True, True))
        assert events[1] == ("apply", member.id)
        assert events[2][0] == "followup"
        assert str(events[2][1]).startswith("✅ ")

    asyncio.run(scenario())


def test_already_acknowledged_duplicate_never_repeats_role_mutation(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=77)

        class FakeMember:
            id = 88

        class Response:
            def is_done(self) -> bool:
                return True

        class Followup:
            async def send(self, *args, **kwargs) -> None:
                raise AssertionError("duplicate route must not send a second reply")

        async def forbidden_apply(_member):
            raise AssertionError("duplicate route must not mutate roles")

        monkeypatch.setattr(runtime.discord, "Member", FakeMember)
        monkeypatch.setattr(runtime, "apply_basic_verification", forbidden_apply)

        interaction = SimpleNamespace(
            id=9002,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            guild=guild,
            channel=SimpleNamespace(id=99),
            user=FakeMember(),
            response=Response(),
            followup=Followup(),
        )

        handled = await runtime.maybe_handle_basic_verify_interaction(interaction)

        assert handled is True

    asyncio.run(scenario())


def test_host_compatibility_path_does_not_install_a_second_basic_verify_dispatcher() -> None:
    code = """
from stoney_verify import interaction_handlers

handler = interaction_handlers.handle_component_interaction
assert not getattr(handler, "_basic_verify_ready", False)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
