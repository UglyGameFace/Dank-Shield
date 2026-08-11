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


def test_persistent_view_is_primary_owner_and_fallback_is_only_failure_path() -> None:
    source = PANEL.read_text(encoding="utf-8")
    assert "super().__init__(timeout=None)" in source
    assert "if not _PANEL_VIEW_REGISTERED and not _PANEL_FALLBACK_LISTENER_REGISTERED" in source
    assert "persistent view unavailable; registered Create Ticket fallback listener" in source
    assert "elif _PANEL_VIEW_REGISTERED:" in source


def test_owner_file_keeps_category_and_persistent_number_ownership() -> None:
    source = PANEL.read_text(encoding="utf-8")
    assert "reserve_persistent_ticket_number" in source
    assert "return await reserve_persistent_ticket_number" in source
    assert "_INTERACTION_TTL_SECONDS" in source
    assert "_MENU_SESSIONS" in source
    assert "_CONFIRM_LOCKS" in source
    assert "Newest menu wins." in source
    assert "You already have a ticket type menu open" not in source
