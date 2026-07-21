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
        "Start Quick Setup",
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
    state = type(
        "State",
        (),
        {
            "tickets": True,
            "verification": False,
            "voice": False,
            "spamguard": True,
            "moderation": True,
            "as_payload": lambda self: {
                "tickets_enabled": True,
                "verification_enabled": False,
                "voice_verification_enabled": False,
                "spam_guard_enabled": True,
                "moderation_enabled": True,
            },
        },
    )()
    view = fresh.CustomServiceModeView(state)
    view_labels = labels(view)
    assert "Continue Quick Setup" in view_labels
    assert "Back to Setup Plans" in view_labels
    assert "Setup Home" in view_labels
    assert "Close" in view_labels


def test_custom_picker_explains_core_modules_and_aio_tools() -> None:
    state = type(
        "State",
        (),
        {
            "tickets": True,
            "verification": False,
            "voice": False,
            "spamguard": True,
            "moderation": True,
            "as_payload": lambda self: {
                "tickets_enabled": True,
                "verification_enabled": False,
                "voice_verification_enabled": False,
                "spam_guard_enabled": True,
                "moderation_enabled": True,
            },
        },
    )()
    guild = type("Guild", (), {"id": 123})()
    embed = fresh._custom_services_embed(guild, state)
    assert embed.title == "🧩 Choose Core Features"
    assert "Manage Setup" in str(embed.description)
    assert "Core Modules" in field_names(embed)


def test_template_preview_uses_current_quick_setup_language() -> None:
    embed = build_setup_template_embed(
        selected_key="custom_setup",
        guild_name="Example Server",
    )
    rendered = "\n".join(
        [
            str(embed.title or ""),
            str(embed.description or ""),
            *[str(field.value) for field in embed.fields],
        ]
    )
    assert "Use This Plan" in rendered
    assert "Manage Setup" in rendered
    assert "Use My Existing Server" not in rendered
