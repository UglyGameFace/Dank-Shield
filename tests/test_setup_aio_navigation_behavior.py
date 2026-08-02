from __future__ import annotations

from typing import Any

import discord

from stoney_verify.commands_ext import public_setup_compact as compact
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.setup_new.templates import build_setup_template_embed


def labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def select_labels(view: discord.ui.View) -> list[str]:
    result: list[str] = []
    for child in view.children:
        if isinstance(child, discord.ui.Select):
            result.extend(str(option.label) for option in child.options)
    return result


def field_names(embed: discord.Embed) -> list[str]:
    return [str(field.name) for field in embed.fields]


def test_new_server_home_has_one_quick_path_and_close() -> None:
    view = compact.CompactSetupHomeView(
        ready=False,
        started=False,
        completed=False,
    )
    assert labels(view) == [
        "Start Setup",
        "Close",
    ]
    assert select_labels(view) == []


def test_started_home_exposes_direct_area_picker_without_manage_hop() -> None:
    view = compact.CompactSetupHomeView(
        ready=False,
        started=True,
        completed=False,
    )
    assert labels(view) == [
        "Continue Setup",
        "Change Plan",
        "Check Configuration",
        "Advanced",
        "Close",
    ]
    assert "Manage Setup" not in labels(view)
    assert len(select_labels(view)) == 9


def test_ready_home_calls_real_feature_testing_by_name() -> None:
    view = compact.CompactSetupHomeView(
        ready=True,
        started=True,
        completed=False,
    )
    assert labels(view)[0] == "Test Features"
    assert "Check Configuration" in labels(view)


def test_manage_screen_is_one_compact_feature_picker() -> None:
    view = compact.CompactManagerView()
    assert labels(view) == [
        "Change Plan",
        "Check Configuration",
        "Advanced",
        "Setup Home",
        "Close",
    ]
    assert select_labels(view) == [
        "Setup Plan & Server Items",
        "Tickets",
        "Verification",
        "Security & SpamGuard",
        "Logs & Activity",
        "Server Design",
        "Welcome & Join",
        "Profile Signatures",
        "Backups & History",
    ]


def test_feature_picker_does_not_repeat_areas_as_buttons() -> None:
    view = compact.CompactManagerView()
    assert not set(select_labels(view)).intersection(labels(view))
    assert len(
        [child for child in view.children if isinstance(child, discord.ui.Select)]
    ) == 1


def test_test_screen_hides_disabled_features_and_explains_finish_gate() -> None:
    view = compact.CompactTestView(
        {
            "tickets": False,
            "verification": True,
            "basic_verify": True,
            "voice_verify": False,
            "id_verify": False,
            "spam_guard": False,
            "logs": False,
            "completed": False,
        }
    )
    assert labels(view) == [
        "Finish Setup",
        "Recheck Configuration",
        "Setup Home",
        "Close",
    ]
    assert select_labels(view) == ["Simple Verify"]
    finish = next(
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and child.label == "Finish Setup"
    )
    assert finish.disabled is True


def test_feature_test_pages_expose_only_relevant_direct_actions() -> None:
    tickets = compact.FeatureTestView(
        {"tickets": True},
        frozenset(),
        "tickets",
    )
    verify = compact.FeatureTestView(
        {"basic_verify": True},
        frozenset(),
        "simple_verify",
    )
    logs = compact.FeatureTestView(
        {"logs": True},
        frozenset(),
        "logs",
    )

    assert labels(tickets) == [
        "Post / Refresh Ticket Panel",
        "Create Test Ticket",
        "Mark Tested",
        "Back to Checklist",
        "Close",
    ]
    assert labels(verify) == [
        "Post / Refresh Verify Panel",
        "Mark Tested",
        "Back to Checklist",
        "Close",
    ]
    assert labels(logs) == [
        "Mark Tested",
        "Back to Checklist",
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
    assert "Continue Setup" in view_labels
    assert "Back" in view_labels
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


def test_custom_voice_toggle_applies_and_explains_dependencies() -> None:
    payload, effective, changed, note = fresh._apply_custom_service_toggle(
        {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "voice_verification_enabled",
    )

    assert changed is True
    assert effective is True
    assert payload["voice_verification_enabled"] is True
    assert payload["verification_enabled"] is True
    assert payload["tickets_enabled"] is True
    assert payload["moderation_enabled"] is True
    assert "needs Simple Verify, Tickets, and Essential Logs" in note
    assert "turned on" in note


def test_custom_dependency_cannot_be_silently_disabled() -> None:
    payload, effective, changed, note = fresh._apply_custom_service_toggle(
        {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": True,
        },
        "tickets_enabled",
    )

    assert changed is False
    assert effective is True
    assert payload["tickets_enabled"] is True
    assert payload["voice_verification_enabled"] is True
    assert "Voice Verify" in note
    assert "needs" in note


def test_custom_spamguard_toggle_explains_log_dependency() -> None:
    payload, effective, changed, note = fresh._apply_custom_service_toggle(
        {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "spam_guard_enabled",
    )

    assert changed is True
    assert effective is True
    assert payload["spam_guard_enabled"] is True
    assert payload["moderation_enabled"] is True
    assert "SpamGuard needs Essential Logs" in note


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
