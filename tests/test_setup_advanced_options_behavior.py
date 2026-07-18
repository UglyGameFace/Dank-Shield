from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_protection_center
from stoney_verify.commands_ext import public_setup_recommend as recommend


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def labels(view: discord.ui.View) -> set[str]:
    return {str(getattr(child, "label", "") or "") for child in view.children}


def find_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [child for child in view.children if str(getattr(child, "label", "") or "") == label]
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


def test_more_options_is_secondary_and_literal():
    assert labels(recommend.ManageSetupView()) == {
        "Change Setup Type",
        "Other Settings",
        "Check Setup for Problems",
        "Fix Setup or Start Over",
        "Help",
        "Back Home",
    }


def test_other_settings_hub_uses_literal_task_names():
    assert labels(recommend.AdvancedSettingsHubView()) == {
        "Features, Roles & Channels",
        "Tickets",
        "Logs & Safety",
        "Server Design",
        "Back to More Options",
        "Back Home",
    }


def test_other_settings_submenus_keep_existing_tools():
    assert labels(recommend.AdvancedCoreSetupView()) == {
        "Turn Features On / Off",
        "Timers & Rules",
        "Choose Roles & Channels",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedMemberExperienceView()) == {
        "Ticket Choices",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedMonitoringRepairView()) == {
        "Choose What Gets Logged",
        "Spam & Raid Protection",
        "Fix Channel Permissions",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedAppearanceView()) == {
        "Server Design",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedDangerZoneView()) == {
        "Fix or Start Over",
        "Back to More Options",
        "Back Home",
    }


def test_reset_is_not_mixed_into_normal_settings():
    for view in (
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedMonitoringRepairView(),
        recommend.AdvancedAppearanceView(),
    ):
        assert "Fix or Start Over" not in labels(view)
    assert "Fix or Start Over" in labels(recommend.AdvancedDangerZoneView())


def test_all_secondary_pages_are_mobile_compact():
    for view in (
        recommend.ManageSetupView(),
        recommend.AdvancedSettingsHubView(),
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
        (recommend.ManageSetupView, "Change Setup Type", "_open_choose_setup_type"),
        (recommend.ManageSetupView, "Other Settings", "_open_advanced_settings"),
        (recommend.ManageSetupView, "Check Setup for Problems", "_open_health_check"),
        (recommend.ManageSetupView, "Fix Setup or Start Over", "_open_advanced_danger_zone"),
        (recommend.ManageSetupView, "Back Home", "_home_edit"),
        (recommend.AdvancedCoreSetupView, "Turn Features On / Off", "_open_services"),
        (recommend.AdvancedCoreSetupView, "Timers & Rules", "_open_timers_behavior"),
        (recommend.AdvancedCoreSetupView, "Choose Roles & Channels", "_open_existing_server"),
        (recommend.AdvancedMemberExperienceView, "Ticket Choices", "_open_ticket_menu"),
        (recommend.AdvancedMonitoringRepairView, "Choose What Gets Logged", "_open_modlog_tracking"),
        (recommend.AdvancedMonitoringRepairView, "Spam & Raid Protection", "_open_protection_options"),
        (recommend.AdvancedMonitoringRepairView, "Fix Channel Permissions", "_open_permission_repair"),
        (recommend.AdvancedDangerZoneView, "Fix or Start Over", "_open_recovery_center"),
    ),
)
def test_buttons_reuse_existing_runtime_routes(monkeypatch: pytest.MonkeyPatch, view_cls: type[discord.ui.View], label: str, route_name: str) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    run(find_button(view_cls(), label).callback(FakeInteraction()))
    assert events == [route_name]


@pytest.mark.parametrize(
    ("label", "route_name"),
    (
        ("Features, Roles & Channels", "_open_advanced_core_setup"),
        ("Tickets", "_open_advanced_member_experience"),
        ("Logs & Safety", "_open_advanced_monitoring_repair"),
        ("Server Design", "_open_advanced_appearance"),
    ),
)
def test_other_settings_groups_open_focused_submenus(monkeypatch: pytest.MonkeyPatch, label: str, route_name: str) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    run(find_button(recommend.AdvancedSettingsHubView(), label).callback(FakeInteraction()))
    assert events == [route_name]


def test_more_options_screen_uses_canonical_view(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert captured["embed"].title == "••• More Options"
    assert isinstance(captured["view"], recommend.ManageSetupView)
    assert any("Fix Setup or Start Over" in field.name for field in captured["embed"].fields)


def test_other_settings_screen_uses_canonical_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_advanced_settings(interaction))
    assert captured["embed"].title == "⚙️ Other Settings"
    assert isinstance(captured["view"], recommend.AdvancedSettingsHubView)


def test_protection_reuses_protection_center(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def refresh(interaction_arg: Any, *, content: str) -> None:
        assert interaction_arg is interaction
        assert "Other Settings" in content
        events.append("protection")

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(public_protection_center, "_refresh_panel", refresh)
    run(recommend._open_protection_options(interaction))
    assert events == ["protection"]


def test_timers_rules_reuses_existing_behavior_view(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert captured["embed"].title == "⏱️ Timers & Rules"
    assert isinstance(captured["view"], recommend.solid.BehaviorSettingsView)


def test_unused_plain_manage_duplicate_is_removed():
    text = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")
    assert "class PlainManageSetupView" not in text
    assert "Advanced Options" not in text


def test_vague_group_names_are_not_user_facing():
    text = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
    for stale in ("Member Experience", "Core Setup", "Monitoring & Repair", "Danger Zone"):
        assert stale not in text
