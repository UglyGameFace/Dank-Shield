from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import (
    public_protection_center,
    public_setup_recommend as recommend,
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
    }


def find_button(
    view: discord.ui.View,
    label: str,
) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if str(getattr(child, "label", "") or "") == label
    ]

    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)

    return matches[0]


class FakeResponse:
    async def edit_message(
        self,
        **kwargs: Any,
    ) -> None:
        return None

    async def send_message(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=8080)
        self.user = SimpleNamespace(id=77)
        self.response = FakeResponse()


def test_advanced_options_has_every_plain_group():
    view = recommend.ManageSetupView()

    assert labels(view) == {
        "Features On / Off",
        "Ticket Choices",
        "Protection",
        "Modlog Tracking",
        "Timers & Behavior",
        "Server Design",
        "Detailed Role / Channel Mapping",
        "Recovery / Start Over",
        "Permission Repair",
        "Help / FAQ",
        "Back Home",
    }


@pytest.mark.parametrize(
    ("label", "route_name"),
    (
        ("Features On / Off", "_open_services"),
        ("Ticket Choices", "_open_ticket_menu"),
        (
            "Detailed Role / Channel Mapping",
            "_open_existing_server",
        ),
        (
            "Recovery / Start Over",
            "_open_recovery_center",
        ),
        ("Protection", "_open_protection_options"),
        ("Modlog Tracking", "_open_modlog_tracking"),
        ("Timers & Behavior", "_open_timers_behavior"),
        ("Back Home", "_home_edit"),
    ),
)
def test_buttons_reuse_existing_runtime_routes(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    route_name: str,
) -> None:
    events: list[str] = []

    async def route(
        *args: Any,
        **kwargs: Any,
    ) -> None:
        events.append(route_name)

    monkeypatch.setattr(
        recommend,
        route_name,
        route,
    )

    view = recommend.ManageSetupView()

    run(
        find_button(
            view,
            label,
        ).callback(FakeInteraction())
    )

    assert events == [route_name]


def test_advanced_options_screen_uses_canonical_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        return True

    async def edit(
        interaction_arg: Any,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_edit_or_followup",
        edit,
    )

    run(recommend._open_manage_setup(interaction))

    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⚙️ Advanced Options"
    assert isinstance(
        captured["view"],
        recommend.ManageSetupView,
    )


def test_protection_reuses_protection_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        return True

    async def refresh(
        interaction_arg: Any,
        *,
        content: str,
    ) -> None:
        assert interaction_arg is interaction
        assert "Advanced Options" in content
        events.append("protection")

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        public_protection_center,
        "_refresh_panel",
        refresh,
    )

    run(recommend._open_protection_options(interaction))

    assert events == ["protection"]


def test_timers_behavior_reuses_existing_behavior_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        return True

    async def add_section(
        embed: discord.Embed,
        guild: Any,
        section: str,
    ) -> None:
        captured["section"] = section
        captured["guild"] = guild

    async def edit(
        interaction_arg: Any,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_add_saved_setup_section",
        add_section,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_edit_or_followup",
        edit,
    )

    run(recommend._open_timers_behavior(interaction))

    assert captured["section"] == "behavior"
    assert captured["guild"] is interaction.guild
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⏱️ Timers & Behavior"
    assert isinstance(
        captured["view"],
        recommend.solid.BehaviorSettingsView,
    )


def test_unused_plain_manage_duplicate_is_removed():
    path = Path(
        "stoney_verify/commands_ext/"
        "public_setup_fresh_choice.py"
    )
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    assert not any(
        isinstance(node, ast.ClassDef)
        and node.name == "PlainManageSetupView"
        for node in ast.walk(tree)
    )

    assert "recommend._open_manage_setup" in text


def test_canonical_view_uses_new_plain_labels():
    path = Path(
        "stoney_verify/commands_ext/"
        "public_setup_recommend.py"
    )
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    manage = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "ManageSetupView"
    )

    source = ast.get_source_segment(text, manage) or ""

    assert 'label="Service Switches"' not in source
    assert 'label="Ticket Menu Options"' not in source
    assert 'label="Saved Roles/Channels"' not in source

    assert 'label="Features On / Off"' in source
    assert 'label="Ticket Choices"' in source
    assert 'label="Timers & Behavior"' in source
