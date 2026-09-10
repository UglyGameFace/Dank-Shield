from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import ticket_panel_runtime as runtime
from stoney_verify.commands_ext import public_ticket_panel_clean as panel


COMMANDS = Path("stoney_verify/commands.py").read_text(encoding="utf-8")
RUNTIME = Path("stoney_verify/ticket_panel_runtime.py").read_text(encoding="utf-8")


class FakeBot:
    def __init__(self, *, fail_view: bool = False, fail_listener: bool = False) -> None:
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
    panel._PANEL_VIEW_REGISTERED = False
    panel._PANEL_FALLBACK_LISTENER_REGISTERED = False


@pytest.fixture(autouse=True)
def _isolated_runtime_state():
    _reset_runtime_state()
    try:
        yield
    finally:
        _reset_runtime_state()


def test_runtime_installs_persistent_view_and_independent_fallback(monkeypatch) -> None:
    fake_bot = FakeBot()
    sentinel_view = object()
    monkeypatch.setattr(panel, "PublicCreateTicketPanelView", lambda: sentinel_view)

    assert runtime.install_public_ticket_panel_runtime(fake_bot, strict=True) is True
    assert fake_bot.views == [sentinel_view]
    assert fake_bot.listeners == [
        (runtime._ticket_panel_fallback_listener, "on_interaction")
    ]
    assert runtime.ticket_panel_runtime_status()["ready"] is True
    assert panel._PANEL_VIEW_REGISTERED is True
    assert panel._PANEL_FALLBACK_LISTENER_REGISTERED is True


def test_runtime_install_is_idempotent(monkeypatch) -> None:
    fake_bot = FakeBot()
    monkeypatch.setattr(panel, "PublicCreateTicketPanelView", lambda: object())

    assert runtime.install_public_ticket_panel_runtime(fake_bot, strict=True) is True
    assert runtime.install_public_ticket_panel_runtime(fake_bot, strict=True) is True
    assert len(fake_bot.views) == 1
    assert len(fake_bot.listeners) == 1


def test_runtime_remains_operational_when_primary_view_registration_fails(monkeypatch) -> None:
    fake_bot = FakeBot(fail_view=True)
    monkeypatch.setattr(panel, "PublicCreateTicketPanelView", lambda: object())

    assert runtime.install_public_ticket_panel_runtime(fake_bot, strict=True) is True
    status = runtime.ticket_panel_runtime_status()
    assert status["persistent_view_registered"] is False
    assert status["fallback_listener_registered"] is True
    assert "view registration failed" in status["error"]
    assert len(fake_bot.listeners) == 1


def test_strict_runtime_fails_closed_when_no_interaction_path_can_register(monkeypatch) -> None:
    fake_bot = FakeBot(fail_view=True, fail_listener=True)
    monkeypatch.setattr(panel, "PublicCreateTicketPanelView", lambda: object())

    with pytest.raises(RuntimeError, match="no registered interaction handler"):
        runtime.install_public_ticket_panel_runtime(fake_bot, strict=True)
    status = runtime.ticket_panel_runtime_status()
    assert status["ready"] is False
    assert "view registration failed" in status["error"]
    assert "listener registration failed" in status["error"]


def test_fallback_only_delegates_clean_ticket_custom_id(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def no_sleep(_seconds: float) -> None:
            return None

        async def fake_handler(interaction) -> None:
            calls.append(interaction)

        class Response:
            def __init__(self, done: bool = False) -> None:
                self.done = done

            def is_done(self) -> bool:
                return self.done

        monkeypatch.setattr(runtime.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(panel, "handle_public_ticket_panel_click", fake_handler)

        clean = SimpleNamespace(
            id=101,
            type=discord.InteractionType.component,
            data={"custom_id": panel.PANEL_BUTTON_CUSTOM_ID},
            response=Response(),
        )
        already_acknowledged = SimpleNamespace(
            id=102,
            type=discord.InteractionType.component,
            data={"custom_id": panel.PANEL_BUTTON_CUSTOM_ID},
            response=Response(done=True),
        )
        unrelated = SimpleNamespace(
            id=103,
            type=discord.InteractionType.component,
            data={"custom_id": "something:else"},
            response=Response(),
        )

        await runtime._ticket_panel_fallback_listener(unrelated)
        await runtime._ticket_panel_fallback_listener(already_acknowledged)
        await runtime._ticket_panel_fallback_listener(clean)
        assert calls == [clean]

    asyncio.run(scenario())


def test_ticket_trace_distinguishes_early_ack_from_fallback(monkeypatch, capsys) -> None:
    async def scenario() -> None:
        async def no_sleep(_seconds: float) -> None:
            return None

        class Response:
            def __init__(self, done: bool = False) -> None:
                self.done = done

            def is_done(self) -> bool:
                return self.done

        async def fake_handler(interaction) -> None:
            interaction.response.done = True

        monkeypatch.setattr(runtime.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(panel, "handle_public_ticket_panel_click", fake_handler)

        acknowledged = SimpleNamespace(
            id=201,
            type=discord.InteractionType.component,
            data={"custom_id": panel.PANEL_BUTTON_CUSTOM_ID},
            response=Response(done=True),
        )
        recovered = SimpleNamespace(
            id=202,
            type=discord.InteractionType.component,
            data={"custom_id": panel.PANEL_BUTTON_CUSTOM_ID},
            response=Response(done=False),
        )

        await runtime._ticket_panel_fallback_listener(acknowledged)
        await runtime._ticket_panel_fallback_listener(recovered)

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert "ticket_panel_trace stage=listener_received interaction=201" in output
    assert "stage=ack_observed_before_fallback interaction=201" in output
    assert "ticket_panel_trace stage=listener_received interaction=202" in output
    assert "stage=fallback_dispatch interaction=202" in output
    assert "stage=fallback_return interaction=202" in output
    assert "response_done=True" in output


def test_ticket_trace_is_scoped_to_clean_panel_id(monkeypatch, capsys) -> None:
    async def scenario() -> None:
        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(runtime.asyncio, "sleep", no_sleep)
        unrelated = SimpleNamespace(
            id=301,
            type=discord.InteractionType.component,
            data={"custom_id": "unrelated:component"},
            response=SimpleNamespace(is_done=lambda: False),
        )
        await runtime._ticket_panel_fallback_listener(unrelated)

    asyncio.run(scenario())
    assert "ticket_panel_trace" not in capsys.readouterr().out


def test_commands_installs_ticket_runtime_before_general_command_registration() -> None:
    install_call = "_install_public_ticket_panel_runtime(bot, strict=True)"
    general_registration = "register_all_commands(bot, bot.tree)"

    assert install_call in COMMANDS
    assert general_registration in COMMANDS
    assert COMMANDS.index(install_call) < COMMANDS.index(general_registration)


def test_runtime_delegates_to_canonical_owner_instead_of_creating_tickets() -> None:
    assert "public_ticket_panel_clean as panel" in RUNTIME
    assert "panel.PublicCreateTicketPanelView()" in RUNTIME
    assert "panel.handle_public_ticket_panel_click(interaction)" in RUNTIME
    assert "create_text_channel" not in RUNTIME
    assert "_create_ticket" not in RUNTIME


def test_runtime_trace_reports_delivery_age_and_ack_state_without_business_logic() -> None:
    assert "ticket_panel_trace" in RUNTIME
    assert "age_ms=" in RUNTIME
    assert "response_done=" in RUNTIME
    assert "ack_observed_before_fallback" in RUNTIME
    assert "fallback_dispatch" in RUNTIME
    assert "fallback_return" in RUNTIME
