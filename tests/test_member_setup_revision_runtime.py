from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.commands_ext.public_member_setup import (
    MemberSetupAdminView,
    MemberSetupResourceBrowserView,
    _looks_like_member_setup_panel,
    _message_component_ids,
)
from stoney_verify.ui import DankGuildResourceBrowserView
from stoney_verify.member_setup_service import (
    ACCESS_MODE_STRICT,
    SETUP_SECTIONS,
    SEVERITY_ACCESS_GATED,
    SEVERITY_RECOMMENDED,
    SEVERITY_REQUIRED,
    member_review_status,
    normalize_guild_setup_state,
    normalize_member_setup_state,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "stoney_verify/commands_ext/public_member_setup.py").read_text(encoding="utf-8")
ROLE_CENTER = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
PROFILE = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")


def _guild_state(*, active: bool = False) -> dict:
    return normalize_guild_setup_state(
        {
            "enabled": True,
            "access_mode": ACCESS_MODE_STRICT,
            "gate_active": active,
            "current_revision": 3,
            "setup_channel_id": "10",
            "access_role_id": "20",
            "protected_category_ids": ["30", "31"],
            "history": [
                {
                    "revision": 1,
                    "severity": SEVERITY_REQUIRED,
                    "changed_sections": list(SETUP_SECTIONS),
                    "summary": "Initial setup",
                },
                {
                    "revision": 2,
                    "severity": SEVERITY_RECOMMENDED,
                    "changed_sections": ["interests"],
                    "summary": "New interests",
                },
                {
                    "revision": 3,
                    "severity": SEVERITY_ACCESS_GATED,
                    "changed_sections": ["notifications"],
                    "summary": "Notification consent changed",
                },
            ],
        }
    )


def test_new_member_reviews_every_current_section_even_after_history_changes() -> None:
    status = member_review_status(_guild_state(active=True), normalize_member_setup_state({}))
    assert status["first_time"] is True
    assert status["pending_sections"] == list(SETUP_SECTIONS)
    assert status["access_gated"] is True


def test_returning_member_reviews_only_sections_changed_since_completion() -> None:
    member = normalize_member_setup_state(
        {
            "completed_revision": 1,
            "section_revisions": {section: 1 for section in SETUP_SECTIONS},
        }
    )
    status = member_review_status(_guild_state(active=True), member)
    assert status["pending_sections"] == ["notifications", "interests"]
    assert status["severity"] == SEVERITY_ACCESS_GATED
    assert status["access_gated"] is True




def test_minor_revision_does_not_force_member_review() -> None:
    guild = normalize_guild_setup_state(
        {
            "enabled": True,
            "current_revision": 2,
            "history": [
                {
                    "revision": 1,
                    "severity": SEVERITY_REQUIRED,
                    "changed_sections": list(SETUP_SECTIONS),
                },
                {
                    "revision": 2,
                    "severity": "minor",
                    "changed_sections": [],
                },
            ],
        }
    )
    member = normalize_member_setup_state(
        {
            "completed_revision": 1,
            "section_revisions": {section: 1 for section in SETUP_SECTIONS},
        }
    )
    status = member_review_status(guild, member)
    assert status["pending_sections"] == []
    assert status["is_current"] is True
    assert status["access_gated"] is False


def test_access_gate_never_activates_from_severity_without_live_gate() -> None:
    member = normalize_member_setup_state(
        {
            "completed_revision": 1,
            "section_revisions": {section: 1 for section in SETUP_SECTIONS},
        }
    )
    status = member_review_status(_guild_state(active=False), member)
    assert status["severity"] == SEVERITY_ACCESS_GATED
    assert status["access_gated"] is False


def test_reviewed_sections_do_not_reappear_when_revision_is_current() -> None:
    member = normalize_member_setup_state(
        {
            "completed_revision": 1,
            "section_revisions": {
                "community": 3,
                "notifications": 3,
                "profile": 3,
                "interests": 3,
            },
        }
    )
    status = member_review_status(_guild_state(active=True), member)
    assert status["pending_sections"] == []
    assert status["access_gated"] is False


def test_guild_state_keeps_gate_configuration_scoped_in_one_payload() -> None:
    state = _guild_state(active=True)
    assert state["setup_channel_id"] == "10"
    assert state["access_role_id"] == "20"
    assert state["protected_category_ids"] == ["30", "31"]
    assert state["gate_active"] is True


def test_activation_grandfathers_before_category_visibility_changes() -> None:
    block = RUNTIME.split("async def activate_strict_gate", 1)[1].split(
        "async def suspend_strict_gate", 1
    )[0]
    checkpoint = block.index('gate_transition="activating"')
    role_grant = block.index("await member.add_roles")
    category_gate = block.index("await _set_category_gate")
    assert checkpoint < role_grant < category_gate
    assert "grandfather_revision=current_revision" in block
    assert "grandfather_before=grandfather_before" in block
    assert "await _restore_gate_snapshot(" in block
    assert "activation rollback" in block


def test_suspend_restores_recorded_visibility_snapshot() -> None:
    block = RUNTIME.split("async def suspend_strict_gate", 1)[1].split(
        "async def reconcile_member_access", 1
    )[0]
    transition = block.index('gate_transition="suspending"')
    restore = block.index("await _restore_gate_snapshot(")
    clear_snapshot = block.index("gate_snapshot={}")
    assert transition < restore < clear_snapshot
    assert 'gate_transition=""' in block
    assert "gate_active=False" in block


def test_runtime_fails_closed_at_boot() -> None:
    assert "install_member_setup_runtime" in COMMANDS
    assert "_install_member_setup_runtime(bot, strict=True)" in COMMANDS


def test_member_setup_is_visible_from_role_center_and_persistent_panel() -> None:
    assert 'label="Member Setup"' in ROLE_CENTER
    assert 'label="Member Setup Manager"' in ROLE_CENTER
    assert 'label="Member Setup / Review"' in PROFILE
    assert 'builder:member_setup' in PROFILE
    assert 'suffix == "member_setup"' in PROFILE


def test_access_gated_publish_requires_active_gate() -> None:
    block = RUNTIME.split("class PublishRevisionModal", 1)[1].split(
        "class GateActivationConfirmModal", 1
    )[0]
    assert "Strict Gate is not active" in block
    assert "_schedule_guild_reconcile(guild)" in block


def test_live_panel_adoption_requires_bot_author_and_profile_components() -> None:
    edit = SimpleNamespace(custom_id="dank:profile:v1:edit")
    row = SimpleNamespace(children=[edit])
    message = SimpleNamespace(
        author=SimpleNamespace(id=77),
        components=[row],
        embeds=[SimpleNamespace(title="Profile Panel")],
    )
    guild = SimpleNamespace(me=SimpleNamespace(id=77))

    assert _message_component_ids(message) == {"dank:profile:v1:edit"}
    assert _looks_like_member_setup_panel(message, guild) is True

    wrong_author = SimpleNamespace(
        author=SimpleNamespace(id=88),
        components=[row],
        embeds=[SimpleNamespace(title="Profile Panel")],
    )
    assert _looks_like_member_setup_panel(wrong_author, guild) is False

    unrelated = SimpleNamespace(
        author=SimpleNamespace(id=77),
        components=[SimpleNamespace(children=[SimpleNamespace(custom_id="ticket:create")])],
        embeds=[SimpleNamespace(title="Profile Panel")],
    )
    assert _looks_like_member_setup_panel(unrelated, guild) is False


def test_public_panel_refresh_fails_closed_on_ambiguous_legacy_panels() -> None:
    assert "More than one Member Setup & Profile panel exists" in RUNTIME
    assert "Delete the obsolete duplicate before refreshing" in RUNTIME
    assert 'channel.history(limit=100)' in RUNTIME
    assert 'panel_message_id=message.id' in RUNTIME


def test_new_profile_panel_persists_canonical_message_ownership() -> None:
    assert "sent = await channel.send(" in PROFILE
    assert "panel_message_id=sent.id" in PROFILE
    assert "setup_channel_id=channel.id" in PROFILE
    assert "await sent.delete()" in PROFILE


def test_member_setup_manager_explains_prerequisite_vs_access_role_chain() -> None:
    assert 'name="Member Access role (automatic)"' in RUNTIME
    assert 'name="Eligibility prerequisite (optional)"' in RUNTIME
    assert 'name="How Strict Gate works"' in RUNTIME
    assert "Dank Shield **does not grant** the eligibility prerequisite" in RUNTIME
    assert 'label="Member Access Role"' in RUNTIME
    assert 'label="Eligibility Prerequisite"' in RUNTIME
    assert 'label="Clear Eligibility Rule"' in RUNTIME
    assert "Dank Shield **grants/removes** this role automatically" in RUNTIME
    assert "member must **already have** this role" in RUNTIME
    assert "Dank Shield does **not** grant it" in RUNTIME
    assert "**1. Eligibility:**" in RUNTIME
    assert "**2. Setup:**" in RUNTIME
    assert "**3. Access:**" in RUNTIME
    assert "**4. Visibility:**" in RUNTIME


def test_member_setup_admin_resource_choices_use_shared_search_safe_browser() -> None:
    styled_role = SimpleNamespace(
        id=20,
        name="「✅」𝕍𝕖𝕣𝕚𝕗𝕚𝕖𝕕",
        mention="<@&20>",
        members=[],
        managed=False,
    )
    text_channel = SimpleNamespace(
        id=10,
        name="member-setup",
        mention="<#10>",
        type=discord.ChannelType.text,
        category=None,
        channels=[],
    )
    guild = SimpleNamespace(
        id=123,
        roles=[styled_role],
        channels=[text_channel],
        get_role=lambda role_id: styled_role if int(role_id) == 20 else None,
        get_channel=lambda channel_id: text_channel if int(channel_id) == 10 else None,
    )

    browser = MemberSetupResourceBrowserView(
        77,
        guild,
        mode="access_role",
    )
    searched = browser.clone(query="verified")

    assert isinstance(browser, DankGuildResourceBrowserView)
    assert isinstance(searched, MemberSetupResourceBrowserView)
    assert [item.resource_id for item in searched.candidates] == [20]
    labels = {str(getattr(child, "label", "") or "") for child in browser.children}
    assert {"Back to Member Setup", "Close", "Search"}.issubset(labels)
    assert not any(isinstance(child, (discord.ui.RoleSelect, discord.ui.ChannelSelect)) for child in browser.children)


def test_member_setup_manager_is_single_message_and_dismissible() -> None:
    manager_region = RUNTIME.split("class MemberSetupAdminView", 1)[1].split(
        "async def open_member_setup_admin", 1
    )[0]

    assert "interaction.response.send_message(" not in manager_region
    assert manager_region.count("_open_member_setup_resource_browser(") == 4
    assert 'label="Close"' in manager_region

    view = MemberSetupAdminView(77)
    labels = {str(getattr(child, "label", "") or "") for child in view.children}
    assert "Close" in labels


def test_member_setup_removes_one_off_native_resource_pickers() -> None:
    assert "DankRoleSelect" not in RUNTIME
    assert "DankChannelSelect" not in RUNTIME
    assert "SetupChannelPickerView" not in RUNTIME
    assert "AccessRolePickerView" not in RUNTIME
    assert "ProtectedCategoryPickerView" not in RUNTIME
    assert "class MemberSetupResourceBrowserView(DankGuildResourceBrowserView)" in RUNTIME


def test_member_setup_admin_and_member_defers_keep_separate_response_contracts() -> None:
    member_region = RUNTIME.split("class MemberSetupView", 1)[1].split(
        "def _message_component_ids", 1
    )[0]
    admin_region = RUNTIME.split("class MemberSetupResourceBrowserView", 1)[1].split(
        "async def _restore_gate_snapshot", 1
    )[0]

    assert "await _defer(interaction)" in member_region
    assert "_defer_panel_update(interaction)" not in member_region
    assert "_defer_panel_update(interaction)" in admin_region
    assert "await interaction.response.defer(thinking=False)" in RUNTIME


def test_member_setup_admin_hides_internal_panel_identity_copy() -> None:
    assert "Tracked message:" not in RUNTIME
    assert "Legacy/untracked" not in RUNTIME
    assert "canonical Member Setup & Profile panel" not in RUNTIME
    assert "Connected — **Refresh Public Panel**" in RUNTIME
    assert "Needs refresh — use **Refresh Public Panel**" in RUNTIME
