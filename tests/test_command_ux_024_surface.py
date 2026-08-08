from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify.commands_ext.public_cleanup_command_center import (
    CleanupCenterView,
    UserPurgeView,
)
from stoney_verify.commands_ext.public_command_surface_v2 import (
    CardAssetView,
    CompactDankHomeView,
)
from stoney_verify.commands_ext.public_lifecycle_menu_compat import (
    build_compact_welcome_setup_view,
)
from stoney_verify.commands_ext.public_mod_command_center import ModerationCenterView
from stoney_verify.commands_ext.public_ticket_command_center import (
    TicketActionCenterView,
    TicketCategoryToolsView,
    TicketIntakeToolsView,
    TicketOperationsView,
)
from stoney_verify.commands_ext.public_verify_command_center import (
    VerifyCenterView,
    VerifyMemberActionView,
    VerifyRoleMappingView,
)

ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def _select_values(view: discord.ui.View, custom_id: str) -> set[str]:
    for item in view.children:
        if isinstance(item, discord.ui.Select) and str(getattr(item, "custom_id", "")) == custom_id:
            return {str(option.value) for option in item.options}
    raise AssertionError(f"select not found: {custom_id}")


def test_home_exposes_all_major_centers() -> None:
    labels = _labels(CompactDankHomeView(1))
    assert {
        "Setup & Settings",
        "Protection",
        "Tickets",
        "Verification",
        "Welcome, Join & Exit",
        "Members & Moderation",
        "Server Design",
        "Roles & Profiles",
        "Logs & Activity",
        "My Profile",
        "Status",
        "Diagnostics",
        "Card Assets",
        "Help",
        "Close",
    } <= labels


def test_current_ticket_center_preserves_complete_action_menu() -> None:
    actions = _select_values(TicketActionCenterView(1), "dank:ticket:center:action:v1")
    assert len(actions) == 16
    assert {"info", "claim", "unclaim", "transfer", "priority", "close", "reopen", "transcript"} <= actions
    assert {"add", "remove", "rename", "lock", "unlock", "owner", "access", "delete"} <= actions


def test_ticket_operations_preserve_queue_lookup_and_setup_families() -> None:
    labels = _labels(TicketOperationsView(1))
    assert {
        "Active",
        "Unassigned",
        "Mine",
        "Recent Closed",
        "Overdue",
        "Find Ticket",
        "Current Ticket",
        "Public Panel",
        "Intake & Routing",
        "Categories",
    } <= labels
    assert _select_values(TicketIntakeToolsView(1), "dank:tickets:intake:action:v1") == {
        "categories", "status", "match", "preview", "post-actions"
    }
    assert _select_values(TicketCategoryToolsView(1), "dank:tickets:categories:action:v1") == {
        "sync", "create", "edit", "delete", "set-default", "reorder", "keywords"
    }


def test_verification_center_preserves_member_server_and_role_mapping_paths() -> None:
    assert {
        "Repair Pending Roles",
        "Post / Refresh Verify Panel",
        "Role Mapping",
        "Verification Setup",
    } <= _labels(VerifyCenterView(1))
    assert len(_labels(VerifyMemberActionView(1, 2))) >= 8
    assert {
        "Pending / Unverified",
        "Verified",
        "Member / Resident",
        "Staff / Support",
        "VC Staff",
        "Back",
    } <= _labels(VerifyRoleMappingView(1))


def test_moderation_center_preserves_cleanup_and_recovery_paths() -> None:
    assert {
        "Member Command Center",
        "Cleanup Tools",
        "Ban / Unban by ID",
        "Gateway Intents",
        "Dank Shield Home",
    } <= _labels(ModerationCenterView(1))

    assert {
        "Cleanup Status",
        "Run Configured Cleanup",
        "Channel Purge",
        "User Message Purge",
        "Blocked Invite Cleanup",
        "Report DM Spam",
        "Moderation Center",
    } <= _labels(CleanupCenterView(1))


def test_user_purge_remains_picker_plus_preview_first_flow() -> None:
    view = UserPurgeView(1)
    assert any(isinstance(item, discord.ui.UserSelect) for item in view.children)
    assert any(isinstance(item, discord.ui.ChannelSelect) for item in view.children)
    assert "Preview Options" in _labels(view)
    cleanup_source = (ROOT / "stoney_verify/commands_ext/public_cleanup_command_center.py").read_text(encoding="utf-8")
    assert "dry_run=True" in cleanup_source
    assert "CleanupUserPurgeConfirmView" in (
        ROOT / "stoney_verify/commands_ext/public_cleanup_group.py"
    ).read_text(encoding="utf-8")


def test_asset_center_routes_three_upload_types_and_keeps_clear_font_action() -> None:
    assert {"Welcome / Exit Studio", "Clear Uploaded Font", "Control Center"} <= _labels(CardAssetView(1))
    source = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    for value in ("join_background", "exit_background", "custom_font"):
        assert value in source
    assert "welcome_card_font_clear(interaction)" in source


def test_lifecycle_refresh_keeps_compact_upload_navigation() -> None:
    view = build_compact_welcome_setup_view(owner_id=1, config={})
    custom_ids = {
        str(getattr(item, "custom_id", "") or "")
        for item in view.children
    }
    assert "dank:lifecycle:compact_assets:v1" in custom_ids
    assert "dank:lifecycle:compact_refresh:v1" in custom_ids
    assert "Uploads & Advanced" in _labels(view)
    assert "Refresh" in _labels(view)


def test_final_compaction_occurs_after_exit_compatibility_registration() -> None:
    commands_source = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
    exit_source = (ROOT / "stoney_verify/commands_ext/public_exit_compact_surface.py").read_text(encoding="utf-8")
    profile_source = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
    assert "register_compact_exit_card_commands(bot, bot.tree)" in commands_source
    assert "compact_surface._INSTALLED = False" in exit_source
    assert "compact_surface.install_compact_public_surface_v2(bot, tree)" in exit_source
    assert "install_lifecycle_menu_compat()" in exit_source
    assert "install_cleanup_menu_compat()" in exit_source
    for module in (
        "public_cleanup_group",
        "public_mod_group",
        "public_ticket_group_clean",
        "public_ticket_delete",
        "public_tickets_group",
        "public_ticket_intake_group",
        "public_ticket_category_group",
        "public_ticket_panel_clean",
        "public_verify_basic_panel",
        "public_verify_group",
    ):
        assert f'"{module}"' in profile_source
