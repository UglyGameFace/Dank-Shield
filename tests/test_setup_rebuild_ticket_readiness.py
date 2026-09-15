from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_setup_recovery as recovery
from stoney_verify.commands_ext import public_ticket_panel_clean as ticket_panel


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_rebuild_existing_choices_does_not_report_ready_with_ticket_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def seed(_guild: Any) -> tuple[list[str], list[str], str]:
        return [], ["support"], ""

    async def preflight(_guild: Any):
        return (
            None,
            None,
            ["Dank Shield is missing in **ACTIVE TICKETS**: View Channel."],
            [
                "Ticket panel channel #support has bot permission issues: "
                "Send Messages, Embed Links, Attach Files."
            ],
        )

    monkeypatch.setattr(recovery.solid, "_seed_recommended_categories", seed)
    monkeypatch.setattr(ticket_panel, "_ticket_setup_preflight", preflight)

    message, ok = run(
        recovery._rebuild_recommended_menu(SimpleNamespace(id=1514374173517152418))
    )

    assert ok is False
    assert "Default ticket choices already exist" in message
    assert "ticket setup is **not ready**" in message
    assert "ACTIVE TICKETS" in message
    assert "View Channel" in message
    assert "Send Messages" in message
    assert "Fix Channel Access" in message
    assert "Nothing changed" not in message


def test_rebuild_requires_owner_ticket_choice_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def seed(_guild: Any) -> tuple[list[str], list[str], str]:
        return ["4 catalog option(s)"], ["selection required"], ""

    async def preflight(_guild: Any):
        return None, None, [], []

    monkeypatch.setattr(recovery.solid, "_seed_recommended_categories", seed)
    monkeypatch.setattr(ticket_panel, "_ticket_setup_preflight", preflight)

    message, ok = run(recovery._rebuild_recommended_menu(SimpleNamespace(id=123)))

    assert ok is False
    assert "Created default ticket choices" in message
    assert "need owner confirmation" in message
    assert "Ticket Choices" in message


def test_rebuild_reports_ready_only_after_ticket_preflight_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def seed(_guild: Any) -> tuple[list[str], list[str], str]:
        return [], ["support", "report"], ""

    async def preflight(_guild: Any):
        return None, None, [], []

    monkeypatch.setattr(recovery.solid, "_seed_recommended_categories", seed)
    monkeypatch.setattr(ticket_panel, "_ticket_setup_preflight", preflight)

    message, ok = run(recovery._rebuild_recommended_menu(SimpleNamespace(id=456)))

    assert ok is True
    assert "Default ticket choices already exist" in message
    assert "Ticket creation preflight passed" in message
    assert "not ready" not in message.lower()


def test_rebuild_fails_closed_when_ticket_preflight_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def seed(_guild: Any) -> tuple[list[str], list[str], str]:
        return [], ["support"], ""

    async def preflight(_guild: Any):
        raise RuntimeError("preflight exploded")

    monkeypatch.setattr(recovery.solid, "_seed_recommended_categories", seed)
    monkeypatch.setattr(ticket_panel, "_ticket_setup_preflight", preflight)

    message, ok = run(recovery._rebuild_recommended_menu(SimpleNamespace(id=789)))

    assert ok is False
    assert "Ticket readiness could not be verified" in message
    assert "RuntimeError: preflight exploded" in message
    assert "Nothing should be treated as ready" in message
