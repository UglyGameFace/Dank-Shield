from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_setup_recommend as recommend


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeMember:
    def __init__(self, user_id: int = 77) -> None:
        self.id = user_id
        self.mention = f"<@{user_id}>"


class FakeResponse:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.done = False
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs: Any) -> None:
        _ = kwargs
        self.events.append("defer")
        self.done = True

    async def send_message(self, content: str, **kwargs: Any) -> None:
        _ = kwargs
        assert not self.done
        self.events.append("response")
        self.done = True
        self.messages.append(content)


class FakeFollowup:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs: Any) -> None:
        _ = kwargs
        self.events.append("followup")
        self.messages.append(content)


class FakeInteraction:
    def __init__(self, events: list[str]) -> None:
        self.guild = SimpleNamespace(id=4040)
        self.user = FakeMember()
        self.response = FakeResponse(events)
        self.followup = FakeFollowup(events)


@pytest.fixture(autouse=True)
def clear_setup_test_ticket_locks() -> None:
    recommend._SETUP_TEST_TICKET_LOCKS.clear()
    yield
    recommend._SETUP_TEST_TICKET_LOCKS.clear()


def test_test_ticket_defers_before_loading_setup_state(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    interaction = FakeInteraction(events)

    async def allow(_interaction: Any) -> bool:
        return True

    async def defer(interaction_arg: Any) -> None:
        assert interaction_arg is interaction
        await interaction.response.defer(thinking=False)

    async def launch_state(_guild: Any) -> dict[str, Any]:
        assert interaction.response.is_done() is True
        events.append("state")
        return {"tickets": False}

    monkeypatch.setattr(recommend.discord, "Member", FakeMember)
    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_safe_defer_update", defer)
    monkeypatch.setattr(recommend, "_launch_state", launch_state)

    run(recommend._create_setup_test_ticket(interaction))

    assert events[:2] == ["defer", "state"]
    assert events[-1] == "followup"
    assert interaction.response.messages == []
    assert "Tickets are OFF" in interaction.followup.messages[-1]


def test_test_ticket_not_ready_reuses_deferred_setup_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    interaction = FakeInteraction(events)

    async def allow(_interaction: Any) -> bool:
        return True

    async def defer(interaction_arg: Any) -> None:
        await interaction_arg.response.defer(thinking=False)

    async def launch_state(_guild: Any) -> dict[str, Any]:
        assert interaction.response.is_done() is True
        events.append("state")
        return {"tickets": True}

    async def target(_guild: Any) -> tuple[str, str, str, str]:
        assert interaction.response.is_done() is True
        events.append("target")
        return ("roles", "Role", "Choose role", "ticket_staff_role")

    async def health(interaction_arg: Any, **kwargs: Any) -> None:
        assert interaction_arg is interaction
        assert kwargs == {"already_deferred": True}
        events.append("health")

    monkeypatch.setattr(recommend.discord, "Member", FakeMember)
    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_safe_defer_update", defer)
    monkeypatch.setattr(recommend, "_launch_state", launch_state)
    monkeypatch.setattr(recommend, "_guided_setup_target", target)
    monkeypatch.setattr(recommend, "_open_health_check", health)

    run(recommend._create_setup_test_ticket(interaction))

    assert events == ["defer", "state", "target", "health"]


def test_test_ticket_lock_contention_stays_fast_and_does_not_read_state(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    interaction = FakeInteraction(events)

    async def allow(_interaction: Any) -> bool:
        return True

    async def forbidden_state(_guild: Any) -> dict[str, Any]:
        raise AssertionError("lock contention must return before setup-state I/O")

    lock_key = (interaction.guild.id, interaction.user.id)
    lock = asyncio.Lock()
    recommend._SETUP_TEST_TICKET_LOCKS[lock_key] = lock

    monkeypatch.setattr(recommend.discord, "Member", FakeMember)
    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend, "_launch_state", forbidden_state)

    async def exercise() -> None:
        await lock.acquire()
        try:
            await recommend._create_setup_test_ticket(interaction)
        finally:
            lock.release()

    run(exercise())

    assert events == ["response"]
    assert "already running" in interaction.response.messages[-1]
