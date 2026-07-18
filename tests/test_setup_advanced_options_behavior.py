from __future__ import annotations

import asyncio
import ast
from collections import Counter
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


def assert_mobile_rows(view: discord.ui.View) -> None:
    counts = Counter(int(getattr(child, "row", 0) or 0) for child in view.children)
    assert counts
    assert max(counts.values()) <= 2


class FakeResponse:
    async def edit_message(self, **kwargs: Any) -> None:
        return None

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=8080)
        self.user = SimpleNamespace(id=77)
        self.response = FakeResponse()


def test_advanced_options_hub_is_grouped_not_flat():
    view = recommend.ManageSetupView()
    assert labels(view) == {
        "Core Setup",
        "Member Experience",
        "Monitoring & Repair",
        "Appearance",
        "Danger Zone",
        "Help / FAQ",
        "Back Home",
    }
    assert "Recovery / Start Over" not in labels(view)
    assert "Features On / Off" not in labels(view)


def test_advanced_submenus_keep_every_existing_tool():
    assert labels(recommend.AdvancedCoreSetupView()) == {
        "Features On / Off",
        "Timers & Behavior",
        "Detailed Role / Channel Mapping",
        "Back to Advanced",
        "Back Home",
    }
    assert labels(recommend.AdvancedMemberExperienceView()) == {
        "Ticket Choices",
        "Protection",
        "Back to Advanced",
        "Back Home",
    }
    assert labels(recommend.AdvancedMonitoringRepairView()) == {
        "Modlog Tracking",
        "Permission Repair",
        "Back to Advanced",
        "Back Home",
    }
    assert labels(recommend.AdvancedAppearanceView()) == {
        "Server Design",
        "Back to Advanced",
        "Back Home",
    }
    assert labels(recommend.AdvancedDangerZoneView()) == {
        "Recovery / Start Over",
        "Back to Advanced",
        "Back Home",
    }


def test_recovery_is_isolated_to_danger_zone():
    normal_views = (
        recommend.ManageSetupView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedMonitoringRepairView(),
        recommend.AdvancedAppearanceView(),
    )
    for view in normal_views:
        assert "Recovery / Start Over" not in labels(view)
    assert "Recovery / Start Over" in labels(recommend.AdvancedDangerZoneView())


def test_all_advanced_pages_are_mobile_compact():
    for view in (
        recommend.ManageSetupView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedMonitoringRepairView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    ):
        assert_mobile_rows(view)


@pytest.mark.parametrize(
    ("view_cls", "label", "route_name"),
    (
        (recommend.AdvancedCoreSetupView, "Features On / Off", "_open_services"),
        (recommend.AdvancedCoreSetupView, "Timers & Behavior", "_open_timers_behavior"),
        (recommend.AdvancedCoreSetupView, "Detailed Role / Channel Mapping", "_open_existing_server"),
        (recommend.AdvancedMemberExperienceView, "Ticket Choices", "_open_ticket_menu"),
        (recommend.AdvancedMemberExperienceView, "Protection", "_open_protection_options"),
        (recommend.AdvancedMonitoringRepairView, "Modlog Tracking", "_open_modlog_tracking"),
        (recommend.AdvancedMonitoringRepairView, "Permission Repair", "_open_permission_repair"),
        (recommend.AdvancedDangerZoneView, "Recovery / Start Over", "_open_recovery_center"),
        (recommend.ManageSetupView, "Back Home", "_home_edit"),
    ),
)
def test_buttons_reuse_existing_runtime_routes(
    monkeypatch: pytest.MonkeyPatch,
    view_cls: type[discord.ui.View],
    label: str,
    route_name: str,
) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    view = view_cls()
    run(find_button(view, label).callback(FakeInteraction()))
    assert events == [route_name]


def test_group_buttons_open_focused_submenus(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = (
        ("Core Setup", "_open_advanced_core_setup"),
        ("Member Experience", "_open_advanced_member_experience"),
        ("Monitoring & Repair", "_open_advanced_monitoring_repair"),
        ("Appearance", "_open_advanced_appearance"),
        ("Danger Zone", "_open_advanced_danger_zone"),
    )
    for label, route_name in mapping:
        events: list[str] = []

        async def route(*args: Any, _route_name: str = route_name, **kwargs: Any) -> None:
            events.append(_route_name)

        monkeypatch.setattr(recommend, route_name, route)
        run(find_button(recommend.ManageSetupView(), label).callback(FakeInteraction()))
        assert events == [route_name]


def test_advanced_options_screen_uses_canonical_grouped_view(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)

    run(recommend._open_manage_setup(interaction))
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⚙️ Advanced Options"
    assert isinstance(captured["view"], recommend.ManageSetupView)
    assert any("Danger Zone" in field.name for field in captured["embed"].fields)


def test_protection_reuses_protection_center(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def refresh(interaction_arg: Any, *, content: str) -> None:
        assert interaction_arg is interaction
        assert "Advanced Options" in content
        events.append("protection")

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(public_protection_center, "_refresh_panel", refresh)
    run(recommend._open_protection_options(interaction))
    assert events == ["protection"]


def test_timers_behavior_reuses_existing_behavior_view(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def add_section(embed: discord.Embed, guild: Any, section: str) -> None:
        captured["section"] = section
        captured["guild"] = guild

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_add_saved_setup_section", add_section)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)

    run(recommend._open_timers_behavior(interaction))
    assert captured["section"] == "behavior"
    assert captured["guild"] is interaction.guild
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⏱️ Timers & Behavior"
    assert isinstance(captured["view"], recommend.solid.BehaviorSettingsView)


def test_unused_plain_manage_duplicate_is_removed():
    path = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "PlainManageSetupView"
        for node in ast.walk(tree)
    )
    assert "recommend._open_manage_setup" in text


def test_canonical_views_use_grouped_plain_labels():
    path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    classes = {
        node.name: ast.get_source_segment(text, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }

    hub = classes["ManageSetupView"]
    assert 'label="Core Setup"' in hub
    assert 'label="Member Experience"' in hub
    assert 'label="Monitoring & Repair"' in hub
    assert 'label="Appearance"' in hub
    assert 'label="Danger Zone"' in hub
    assert 'label="Recovery / Start Over"' not in hub

    combined = "\n".join(
        classes[name]
        for name in (
  "AdvancedCoreSetupView",
  "AdvancedMemberExperienceView",
  "AdvancedMonitoringRepairView",
  "AdvancedAppearanceView",
  "AdvancedDangerZoneView",
        )
    )
    for label in (
        "Features On / Off",
        "Ticket Choices",
        "Protection",
        "Modlog Tracking",
        "Timers & Behavior",
        "Server Design",
        "Detailed Role / Channel Mapping",
        "Permission Repair",
        "Recovery / Start Over",
    ):
        assert f'label="{label}"' in combined
