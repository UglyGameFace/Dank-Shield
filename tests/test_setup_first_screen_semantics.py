from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import (
    public_setup_fresh_choice as fresh,
    public_setup_recommend as recommend,
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


def setup_choice_labels(view: discord.ui.View) -> set[str]:
    result: set[str] = set()
    for child in view.children:
        if isinstance(child, discord.ui.Select):
            result.update(str(option.label) for option in child.options)
    return result


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
        recommend,
        "_open_choose_setup_type",
        choose,
    )
    monkeypatch.setattr(
        recommend,
        "_open_guided_setup",
        guided,
    )

    view = recommend.ProductSetupHomeView(
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
        recommend,
        "_open_choose_setup_type",
        choose,
    )
    monkeypatch.setattr(
        recommend,
        "_open_guided_setup",
        guided,
    )

    view = recommend.ProductSetupHomeView(
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
