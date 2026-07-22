from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_setup_config_writer as writer
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def labels(view: discord.ui.View) -> list[str]:
    return [str(getattr(child, "label", "") or "") for child in view.children if isinstance(child, discord.ui.Button)]


def button(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    matches = [
        child for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_custom_setup_does_not_invent_tickets() -> None:
    services = recommend._selected_setup_services({
        "setup_choice": "custom_setup",
        "verification_enabled": True,
        "tickets_enabled": False,
    })
    assert services["tickets"] is False
    assert services["basic_verify"] is True


def test_launch_hides_actions_for_features_that_are_off() -> None:
    view = recommend.LaunchTestView({
        "tickets": False,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": False,
        "logs": False,
        "completed": False,
    })
    assert labels(view) == [
        "Post Simple Verify Panel",
        "Finish Setup",
        "Review Setup",
        "Setup Home",
        "Close",
    ]


def test_finished_launch_does_not_offer_finish_again() -> None:
    view = recommend.LaunchTestView({
        "tickets": True,
        "basic_verify": False,
        "completed": True,
    })
    assert "Finish Setup" not in labels(view)
    assert "Post Simple Verify Panel" not in labels(view)
    assert "Post Ticket Panel" in labels(view)


def test_launch_summary_lists_only_enabled_features() -> None:
    rendered = recommend._launch_state_text({
        "tickets": False,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": True,
        "logs": True,
    })
    assert "Simple Verify" in rendered
    assert "SpamGuard" in rendered
    assert "Logs" in rendered
    assert "Tickets" not in rendered
    assert "OFF" not in rendered


def test_finished_home_opens_summary_instead_of_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def summary(interaction: Any) -> None:
        events.append("summary")

    async def launch(interaction: Any) -> None:
        events.append("launch")

    monkeypatch.setattr(recommend, "_open_completed_summary", summary)
    monkeypatch.setattr(recommend, "_open_test_launch", launch)
    view = recommend.ProductSetupHomeView(started=True, ready=True, completed=True)
    assert button(view, "dank_setup_home:continue").label == "View Setup Summary"
    run(button(view, "dank_setup_home:continue").callback(SimpleNamespace()))
    assert events == ["summary"]


def test_setup_writer_invalidates_completion_after_edit() -> None:
    payload = writer._completion_aware_updates({
        "ticket_prefix": "help",
        "__config_write_mode": "setup_builder",
    })
    assert payload["setup_completed"] is False
    assert payload["setup_completion_invalidated_at"]


def test_finish_write_is_not_invalidated() -> None:
    payload = writer._completion_aware_updates({
        "setup_completed": True,
        "setup_completed_at": "now",
    })
    assert payload["setup_completed"] is True


def test_shared_submenu_navigation_is_compact() -> None:
    view = solid.SetupNavView()
    assert labels(view) == ["Back to All Features", "Setup Home", "Close"]
    counts: dict[int, int] = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        counts[row] = counts.get(row, 0) + 1
    assert all(count <= 5 for count in counts.values())
    assert len(view.children) <= 25
