from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import (
    public_setup_compact as compact,
    public_setup_fresh_choice as fresh,
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def button_by_id(
    view: discord.ui.View,
    custom_id: str,
) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if str(getattr(child, "custom_id", "") or "")
        == custom_id
    ]

    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)

    return matches[0]


def button_labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def setup_choice_labels(view: discord.ui.View) -> set[str]:
    # Picker migration slice 3: plan choices live in the house picker
    # the opener button builds, not in a raw select on the panel.
    opener = button_by_id(view, "dank_setup_plans:choose:open:v1")
    captured: dict[str, Any] = {}

    async def edit_message(**kwargs: Any) -> None:
        captured.update(kwargs)

    interaction = SimpleNamespace(
        guild=getattr(opener, "_guild", None),
        message=SimpleNamespace(embeds=[]),
        response=SimpleNamespace(edit_message=edit_message),
    )
    run(opener.callback(interaction))
    picker = captured["view"]
    return {str(choice.label) for choice in picker._all_choices}


def test_new_server_opens_setup_type_before_guided_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def choose(
        interaction: Any,
    ) -> None:
        events.append("choose")

    async def guided(
        interaction: Any,
    ) -> None:
        events.append("guided")

    monkeypatch.setattr(
        compact,
        "_open_plan",
        choose,
    )
    monkeypatch.setattr(
        compact,
        "_open_guided",
        guided,
    )

    view = compact.CompactSetupHomeView(
        started=False,
    )

    run(
        button_by_id(
            view,
            "dank_setup_home:continue",
        ).callback(SimpleNamespace())
    )

    assert events == ["choose"]


def test_started_server_continues_guided_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def choose(
        interaction: Any,
    ) -> None:
        events.append("choose")

    async def guided(
        interaction: Any,
    ) -> None:
        events.append("guided")

    monkeypatch.setattr(
        compact,
        "_open_plan",
        choose,
    )
    monkeypatch.setattr(
        compact,
        "_open_guided",
        guided,
    )

    view = compact.CompactSetupHomeView(
        started=True,
    )

    run(
        button_by_id(
            view,
            "dank_setup_home:continue",
        ).callback(SimpleNamespace())
    )

    assert events == ["guided"]


def test_public_setup_type_screen_has_exact_five_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fresh,
        "id_verify_allowed_for_guild",
        lambda guild: False,
    )

    view = fresh.SetupTypeChoiceView(
        guild=SimpleNamespace(id=100),
    )

    assert setup_choice_labels(view) == {
        "Recommended Setup",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose Core Features",
    }


def test_quick_setup_plan_picker_is_a_root_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fresh,
        "id_verify_allowed_for_guild",
        lambda guild: False,
    )

    view = fresh.SetupTypeChoiceView(
        guild=SimpleNamespace(id=101),
    )

    assert set(button_labels(view)) == {
        "Choose a setup plan",
        "Setup Home",
        "Close",
    }
    assert "Back to All Features" not in button_labels(view)
    assert "Back to Manage Setup" not in button_labels(view)


def test_id_web_choices_exist_only_for_allowed_guilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fresh,
        "id_verify_allowed_for_guild",
        lambda guild: True,
    )

    view = fresh.SetupTypeChoiceView(
        guild=SimpleNamespace(id=200),
    )

    labels = setup_choice_labels(view)

    assert {
        "Recommended Setup",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose Core Features",
    } <= labels

    assert "ID / Web Verify" in labels
    assert "ID / Web + Voice" in labels


def test_old_plain_setup_choice_owner_is_removed() -> None:
    assert not hasattr(
        fresh,
        "PlainSetupChoiceView",
    )

    assert hasattr(
        fresh,
        "SetupTypeChoiceView",
    )
