from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import config_history_ui
from stoney_verify.commands_ext import public_protection_center
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def find_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == label
    ]
    assert len(matches) == 1
    return matches[0]


def assert_mobile_rows(view: discord.ui.View) -> None:
    counts = Counter(
        int(getattr(child, "row", 0) or 0)
        for child in view.children
    )
    assert counts
    assert max(counts.values()) <= 3
    assert len(view.children) <= 25


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


def test_manage_setup_is_secondary_and_task_based() -> None:
    assert labels(recommend.ManageSetupView()) == {
        "Change Setup Plan",
        "All Features & Settings",
        "Review Setup",
        "Repair or Restart Setup",
        "Help",
        "Setup Home",
        "Close",
    }


def test_all_features_hub_uses_aio_module_names() -> None:
    assert labels(recommend.AdvancedSettingsHubView()) == {
        "Setup Plan & Server Items",
        "Tickets",
        "Verification",
        "Security & SpamGuard",
        "Logs & Activity",
        "Server Design",
        "Backups & History",
        "Back to Manage Setup",
        "Setup Home",
        "Close",
    }


def test_aio_submenus_keep_existing_tools() -> None:
    assert labels(recommend.AdvancedCoreSetupView()) == {
        "Choose Core Modules",
        "Timers & Rules",
        "Choose Roles & Channels",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedMemberExperienceView()) == {
        "Ticket Choices",
        "Roles & Channels",
        "Timers & Rules",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedVerificationView()) == {
        "Choose Core Modules",
        "Roles & Channels",
        "Timers & Rules",
        "Review Old Voice Items",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedSecurityView()) == {
        "Protection Center",
        "Check Bot Access",
        "Fix Channel Permissions",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedLogsActivityView()) == {
        "Choose What Gets Logged",
        "Check Activity Access",
        "Log Channels",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedAppearanceView()) == {
        "Open Server Design",
        "Back to All Features",
        "Setup Home",
        "Close",
    }
    assert labels(recommend.AdvancedDangerZoneView()) == {
        "Open Repair & Restart Tools",
        "Back to Manage Setup",
        "Setup Home",
        "Close",
    }


def test_advanced_section_footer_matches_back_button_label() -> None:
    embed = recommend._advanced_section_embed(
        title="Test",
        description="Test",
        items=("Test",),
    )
    footer = str(embed.footer.text or "")

    assert "Back to All Features" in footer
    assert "Back to All Features & Settings" not in footer



def test_repair_is_not_mixed_into_normal_feature_sections() -> None:
    normal_views = (
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
    )
    for view in normal_views:
        assert "Open Repair & Restart Tools" not in labels(view)
    assert "Open Repair & Restart Tools" in labels(
        recommend.AdvancedDangerZoneView()
    )


def test_all_secondary_pages_are_mobile_compact() -> None:
    for view in (
        recommend.ManageSetupView(),
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    ):
        assert_mobile_rows(view)


@pytest.mark.parametrize(
    ("view_cls", "label", "route_name"),
    (
        (
            recommend.ManageSetupView,
            "Change Setup Plan",
            "_open_choose_setup_type",
        ),
        (
            recommend.ManageSetupView,
            "All Features & Settings",
            "_open_advanced_settings",
        ),
        (
            recommend.ManageSetupView,
            "Review Setup",
            "_open_health_check",
        ),
        (
            recommend.ManageSetupView,
            "Repair or Restart Setup",
            "_open_advanced_danger_zone",
        ),
        (recommend.ManageSetupView, "Setup Home", "_home_edit"),
        (recommend.ManageSetupView, "Close", "_close_setup"),
        (
            recommend.AdvancedCoreSetupView,
            "Choose Core Modules",
            "_open_services",
        ),
        (
            recommend.AdvancedCoreSetupView,
            "Timers & Rules",
            "_open_timers_behavior",
        ),
        (
            recommend.AdvancedCoreSetupView,
            "Choose Roles & Channels",
            "_open_existing_server",
        ),
        (
            recommend.AdvancedMemberExperienceView,
            "Ticket Choices",
            "_open_ticket_menu",
        ),
        (
            recommend.AdvancedMemberExperienceView,
            "Roles & Channels",
            "_open_existing_server",
        ),
        (
            recommend.AdvancedMemberExperienceView,
            "Timers & Rules",
            "_open_timers_behavior",
        ),
        (
            recommend.AdvancedVerificationView,
            "Choose Core Modules",
            "_open_services",
        ),
        (
            recommend.AdvancedVerificationView,
            "Roles & Channels",
            "_open_existing_server",
        ),
        (
            recommend.AdvancedVerificationView,
            "Timers & Rules",
            "_open_timers_behavior",
        ),
        (
            recommend.AdvancedSecurityView,
            "Protection Center",
            "_open_protection_options",
        ),
        (
            recommend.AdvancedSecurityView,
            "Check Bot Access",
            "_open_bot_access_check",
        ),
        (
            recommend.AdvancedSecurityView,
            "Fix Channel Permissions",
            "_open_permission_repair",
        ),
        (
            recommend.AdvancedLogsActivityView,
            "Choose What Gets Logged",
            "_open_modlog_tracking",
        ),
        (
            recommend.AdvancedLogsActivityView,
            "Check Activity Access",
            "_open_bot_access_check",
        ),
        (
            recommend.AdvancedLogsActivityView,
            "Log Channels",
            "_open_existing_server",
        ),
        (
            recommend.AdvancedDangerZoneView,
            "Open Repair & Restart Tools",
            "_open_recovery_center",
        ),
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
    run(find_button(view_cls(), label).callback(FakeInteraction()))
    assert events == [route_name]


@pytest.mark.parametrize(
    ("label", "route_name"),
    (
        ("Setup Plan & Server Items", "_open_advanced_core_setup"),
        ("Tickets", "_open_advanced_member_experience"),
        ("Verification", "_open_advanced_verification"),
        ("Security & SpamGuard", "_open_advanced_security"),
        ("Logs & Activity", "_open_advanced_logs_activity"),
        ("Server Design", "_open_advanced_appearance"),
        ("Backups & History", "_open_config_history"),
        ("Back to Manage Setup", "_open_manage_setup"),
    ),
)
def test_feature_groups_open_focused_submenus(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    route_name: str,
) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    run(
        find_button(
            recommend.AdvancedSettingsHubView(),
            label,
        ).callback(FakeInteraction())
    )
    assert events == [route_name]


def test_manage_setup_screen_uses_canonical_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
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

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_manage_setup(interaction))
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⚙️ Manage Setup"
    assert isinstance(captured["view"], recommend.ManageSetupView)
    assert any(
        "Repair or Restart Setup" in field.name
        for field in captured["embed"].fields
    )


def test_all_features_screen_uses_canonical_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def edit(
        interaction_arg: Any,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_advanced_settings(interaction))
    assert captured["embed"].title == "🧰 All Features & Settings"
    assert isinstance(captured["view"], recommend.AdvancedSettingsHubView)


def test_protection_reuses_protection_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def refresh(interaction_arg: Any, *, content: str) -> None:
        assert interaction_arg is interaction
        assert "All Features & Settings" in content
        events.append("protection")

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(
        public_protection_center,
        "_refresh_panel",
        refresh,
    )
    run(recommend._open_protection_options(interaction))
    assert events == ["protection"]


def test_backups_history_reuses_native_history_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def open_history(interaction_arg: Any) -> None:
        assert interaction_arg is interaction
        events.append("history")

    monkeypatch.setattr(
        config_history_ui,
        "open_config_history",
        open_history,
    )
    run(recommend._open_config_history(interaction))
    assert events == ["history"]


def test_timers_rules_reuses_native_behavior_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
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

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(
        recommend.solid,
        "_add_saved_setup_section",
        add_section,
    )
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_timers_behavior(interaction))
    assert captured["section"] == "behavior"
    assert captured["guild"] is interaction.guild
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⏱️ Timers & Rules"
    assert isinstance(captured["view"], solid.BehaviorSettingsView)


def test_native_timers_rules_owns_verification_timer_entry() -> None:
    view = solid.BehaviorSettingsView()

    assert "Verification Timers" in labels(view)
    assert "Set Prefix / Ticket Timer Hours" in labels(view)
    assert "Clear Optional Access Roles" in labels(view)


def test_native_verification_timer_flow_exposes_no_start_controls() -> None:
    main = solid.VerificationTimerSettingsView()
    advanced = solid.VerificationIdleTimerSettingsView()

    assert labels(main) >= {
        "Enable Wait Timer",
        "Disable + Clear Wait Timer",
        "Change Wait Hours",
        "Clear Active Wait Timers",
        "Advanced No-Start Timer",
    }
    assert labels(advanced) >= {
        "Enable No-Start Timer",
        "Disable + Clear No-Start",
        "Change No-Start Minutes",
    }

    for view in (main, advanced):
        assert "Back to All Features" in labels(view)
        assert "Setup Home" in labels(view)
        assert "Close" in labels(view)
