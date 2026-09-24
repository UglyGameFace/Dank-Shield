from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.commands_ext import public_ticket_panel_clean as panel


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "stoney_verify" / "commands_ext" / "public_ticket_panel_clean.py"


def _reset_state() -> None:
    panel._PANEL_INTERACTION_LOCKS.clear()
    panel._PANEL_INTERACTION_DONE_UNTIL.clear()


def test_same_discord_interaction_runs_the_menu_handler_once() -> None:
    async def scenario() -> None:
        _reset_state()
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[int] = []
        interaction = SimpleNamespace(id=123456789)
        original_core = panel._handle_panel_button_core

        async def fake_core(value) -> None:
            calls.append(value.id)
            started.set()
            await release.wait()

        panel._handle_panel_button_core = fake_core
        try:
            first = asyncio.create_task(panel._handle_panel_button(interaction))
            await started.wait()
            await panel._handle_panel_button(interaction)
            release.set()
            await first
            await panel._handle_panel_button(interaction)
            assert calls == [123456789]
        finally:
            panel._handle_panel_button_core = original_core

    asyncio.run(scenario())


def test_distinct_discord_interactions_are_not_member_rate_limited() -> None:
    async def scenario() -> None:
        _reset_state()
        calls: list[int] = []
        original_core = panel._handle_panel_button_core

        async def fake_core(value) -> None:
            calls.append(value.id)

        panel._handle_panel_button_core = fake_core
        try:
            await panel._handle_panel_button(SimpleNamespace(id=101))
            await panel._handle_panel_button(SimpleNamespace(id=102))
            assert calls == [101, 102]
        finally:
            panel._handle_panel_button_core = original_core

    asyncio.run(scenario())


def test_handler_exception_finalizes_duplicate_suppression() -> None:
    async def scenario() -> None:
        _reset_state()
        interaction = SimpleNamespace(id=7001)
        calls = 0
        original_core = panel._handle_panel_button_core

        async def broken(_interaction) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("simulated menu failure")

        panel._handle_panel_button_core = broken
        try:
            with pytest.raises(RuntimeError, match="simulated menu failure"):
                await panel._handle_panel_button(interaction)
            assert interaction.id in panel._PANEL_INTERACTION_DONE_UNTIL
            assert panel._PANEL_INTERACTION_LOCKS[interaction.id].locked() is False
            await panel._handle_panel_button(interaction)
            assert calls == 1
        finally:
            panel._handle_panel_button_core = original_core

    asyncio.run(scenario())


def test_handler_cancellation_finalizes_duplicate_suppression() -> None:
    async def scenario() -> None:
        _reset_state()
        interaction = SimpleNamespace(id=7002)
        started = asyncio.Event()
        original_core = panel._handle_panel_button_core

        async def blocked(_interaction) -> None:
            started.set()
            await asyncio.Event().wait()

        panel._handle_panel_button_core = blocked
        try:
            task = asyncio.create_task(panel._handle_panel_button(interaction))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert interaction.id in panel._PANEL_INTERACTION_DONE_UNTIL
            assert panel._PANEL_INTERACTION_LOCKS[interaction.id].locked() is False
        finally:
            panel._handle_panel_button_core = original_core

    asyncio.run(scenario())


def test_expired_interaction_state_is_pruned_on_next_click() -> None:
    async def scenario() -> None:
        _reset_state()
        panel._PANEL_INTERACTION_DONE_UNTIL[7003] = 0.0
        panel._PANEL_INTERACTION_LOCKS[7003] = asyncio.Lock()
        original_core = panel._handle_panel_button_core

        async def noop(_interaction) -> None:
            return None

        panel._handle_panel_button_core = noop
        try:
            await panel._handle_panel_button(SimpleNamespace(id=7004))
            assert 7003 not in panel._PANEL_INTERACTION_DONE_UNTIL
            assert 7003 not in panel._PANEL_INTERACTION_LOCKS
        finally:
            panel._handle_panel_button_core = original_core

    asyncio.run(scenario())


def test_defer_failure_is_not_treated_as_a_claim() -> None:
    async def scenario() -> None:
        class Response:
            def is_done(self) -> bool:
                return False

            async def defer(self, **_kwargs) -> None:
                raise RuntimeError("simulated ack failure")

        interaction = SimpleNamespace(response=Response())
        assert await panel._defer(interaction, True) is False

    asyncio.run(scenario())


def test_preacknowledged_confirm_path_remains_valid() -> None:
    async def scenario() -> None:
        class Response:
            def is_done(self) -> bool:
                return True

            async def defer(self, **_kwargs) -> None:
                raise AssertionError("already-acknowledged interaction must not defer again")

        interaction = SimpleNamespace(response=Response())
        assert await panel._defer(interaction, True) is True

    asyncio.run(scenario())


def test_panel_click_stops_before_lookup_when_ack_fails(monkeypatch) -> None:
    async def scenario() -> None:
        async def failed_ack(_interaction, _thinking=False) -> bool:
            return False

        async def forbidden_lookup(*_args, **_kwargs):
            raise AssertionError("ticket lookup must not run after ack failure")

        monkeypatch.setattr(panel, "_defer", failed_ack)
        monkeypatch.setattr(panel, "_existing_open", forbidden_lookup)

        await panel._handle_panel_button_core(
            SimpleNamespace(guild=SimpleNamespace(id=77), user=SimpleNamespace(id=88))
        )

    asyncio.run(scenario())


def test_ticket_creation_stops_before_category_lookup_when_ack_fails(monkeypatch) -> None:
    async def scenario() -> None:
        async def failed_ack(_interaction, _thinking=False) -> bool:
            return False

        async def forbidden_category(*_args, **_kwargs):
            raise AssertionError("category lookup must not run after ack failure")

        monkeypatch.setattr(panel, "_defer", failed_ack)
        monkeypatch.setattr(panel, "_active_category", forbidden_category)

        await panel._create_ticket(
            SimpleNamespace(guild=SimpleNamespace(id=77), user=SimpleNamespace(id=88)),
            {"slug": "support", "name": "Support"},
        )

    asyncio.run(scenario())


def test_clean_panel_delegates_runtime_registration_to_single_owner() -> None:
    source = PANEL.read_text(encoding="utf-8")
    assert "super().__init__(timeout=None)" in source
    assert "install_public_ticket_panel_runtime" in source
    assert "ticket_panel_runtime_status" in source
    assert "bot.add_listener(_component_fallback_listener" not in source
    assert "elif _PANEL_VIEW_REGISTERED:" not in source


def test_known_historical_public_ticket_button_ids_stay_compatible() -> None:
    assert panel.PANEL_BUTTON_CUSTOM_ID in panel.PANEL_BUTTON_CUSTOM_IDS
    assert "sv:ticket:panel:create:v6" in panel.PANEL_BUTTON_CUSTOM_IDS
    assert "ticket_create" in panel.PANEL_BUTTON_CUSTOM_IDS


def test_owner_file_keeps_category_and_persistent_number_ownership() -> None:
    source = PANEL.read_text(encoding="utf-8")
    assert "reserve_persistent_ticket_number" in source
    assert "return await reserve_persistent_ticket_number" in source
    assert "_INTERACTION_TTL_SECONDS" in source
    assert "_MENU_SESSIONS" in source
    assert "_CONFIRM_LOCKS" in source
    assert "Newest menu wins." in source
    assert "You already have a ticket type menu open" not in source
