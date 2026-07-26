from __future__ import annotations

from typing import Any

import discord

from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.setup_new.templates import build_setup_template_embed


def labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def field_names(embed: discord.Embed) -> list[str]:
    return [str(field.name) for field in embed.fields]


def test_home_has_one_quick_path_management_and_close() -> None:
    view = recommend.ProductSetupHomeView(
        ready=False,
        started=False,
        completed=False,
    )
    assert labels(view) == [
        "Start Setup",
        "Manage Setup",
        "Close",
    ]


def test_manage_setup_is_task_based() -> None:
    view = recommend.ManageSetupView()
    assert labels(view) == [
        "Change Setup Plan",
        "All Features & Settings",
        "Review Setup",
        "Repair or Restart Setup",
        "Help",
        "Setup Home",
        "Close",
    ]


def test_aio_feature_hub_exposes_all_major_categories() -> None:
    view = recommend.AdvancedSettingsHubView()
    assert labels(view) == [
        "Setup Plan & Server Items",
        "Tickets",
        "Verification",
        "Security & SpamGuard",
        "Logs & Activity",
        "Server Design",
        "Member Profiles & Live Cards",
        "Backups & History",
        "Back to Manage Setup",
        "Setup Home",
        "Close",
    ]


def test_each_major_subsection_has_back_home_and_close() -> None:
    views = (
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    )
    for view in views:
        view_labels = labels(view)
        assert any(label.startswith("Back to ") for label in view_labels)
        assert "Setup Home" in view_labels
        assert "Close" in view_labels


def test_test_screen_still_hides_disabled_feature_actions() -> None:
    view = recommend.LaunchTestView(
        {
            "tickets": False,
            "basic_verify": True,
            "completed": False,
        }
    )
    assert labels(view) == [
        "Post Simple Verify Panel",
        "Finish Setup",
        "Review Setup",
        "Setup Home",
        "Close",
    ]


def test_custom_core_picker_has_predictable_navigation() -> None:
    view = fresh.CustomCoreView()
    assert labels(view)[-3:] == [
        "Back to Setup Choices",
        "Setup Home",
        "Close",
    ]


def test_template_copy_matches_ticket_and_verification_choices() -> None:
    quick = build_setup_template_embed("quick")
    full = build_setup_template_embed("full")
    custom = build_setup_template_embed("custom")

    assert "Verification Panel" in field_names(quick)
    assert "Verification Panel" in field_names(full)
    assert "Open a Ticket Panel" in field_names(full)
    assert "Open a Ticket Panel" not in field_names(quick)
    assert "Choose Core Features" in field_names(custom)
    assert "Verification + tickets" in custom.fields[1].value


def test_template_copy_never_calls_server_control_a_role() -> None:
    for key in ("quick", "full", "custom", "guided"):
        embed = build_setup_template_embed(key)
        text = "\n".join(
            [str(embed.title or ""), str(embed.description or "")]
            + [f"{field.name}\n{field.value}" for field in embed.fields]
        ).lower()
        assert "server-control role" not in text
        assert "server control role" not in text
