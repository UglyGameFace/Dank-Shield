from __future__ import annotations

import inspect

from stoney_verify.commands_ext import public_profile_contextual_permission_repair as repair
from stoney_verify.commands_ext import public_setup_gate as gate


def test_profile_contextual_integration_has_no_local_permission_mutation() -> None:
    source = inspect.getsource(repair)
    assert "set_permissions(" not in source
    assert "contextual.repair_context(" in source
    assert "clear_explicit_denies" not in source


def test_profile_builder_fix_is_intercepted_by_shared_repair_owner() -> None:
    source = inspect.getsource(repair.contextual_builder_action)
    assert 'action != "fix"' in source
    assert "_ORIGINAL_BUILDER_ACTION" in source
    assert "contextual.repair_context(" in source
    assert "interaction.edit_original_response(" in source


def test_profile_builder_uses_shared_three_state_button_contract() -> None:
    source = inspect.getsource(repair.ContextualProfileBuilderView)
    assert 'label = "Access Healthy"' in source
    assert 'label = "Fix Issues"' in source
    assert 'label = "Manual Fix Needed"' in source
    assert "builder:fix" in source


def test_profile_builder_status_keeps_role_prerequisites_manual() -> None:
    source = inspect.getsource(repair.contextual_profile_builder_status)
    assert "_profile_manual_issues" in source
    assert "row.blockers" in source
    assert "row.repairable" in source
    assert "explicit deny" in source


def test_compact_profile_signatures_get_same_screen_repair_control() -> None:
    source = inspect.getsource(repair.ContextualProfileCardSetupView)
    assert "SignatureAccessButton" in source
    button_source = inspect.getsource(repair.SignatureAccessButton)
    assert "repair_button_state" in button_source
    assert "contextual.repair_context(" in button_source
    assert "view.refresh(" in button_source


def test_compact_signature_selection_repairs_exact_selection_before_save() -> None:
    source = inspect.getsource(repair.contextual_save_selected_channels)
    assert "_selection_problems" in source
    assert "_targets_for_ids" in source
    assert "contextual.repair_context(" in source
    assert "_ORIGINAL_SIGNATURE_SAVE" in source
    assert "selected channels were not saved" in source


def test_roles_center_repairs_only_selected_panel_channel_before_post() -> None:
    source = inspect.getsource(repair.contextual_roles_panel_post)
    assert 'label="Self-role panel channel"' in source
    assert "contextual.audit_context(" in source
    assert "contextual.repair_context(" in source
    assert "_ORIGINAL_ROLES_POST" in source


def test_profile_targets_use_general_minimum_profile_and_no_name_guessing() -> None:
    source = inspect.getsource(repair)
    assert 'feature="general"' in source
    assert "guild.get_channel(raw)" in source
    assert "guess" not in inspect.getsource(repair._target).lower()


def test_late_public_bootstrap_activates_profile_contextual_repair() -> None:
    source = inspect.getsource(gate.register_public_setup_gate)
    assert "apply_profile_contextual_permission_repair" in source
    assert "profile_contextual_repair" in source


def test_apply_rebinds_all_three_profile_self_role_surfaces(monkeypatch) -> None:
    monkeypatch.setattr(repair, "_PATCHED", False)

    original_status = repair.profile._profile_builder_status
    original_view = repair.profile.ProfileBuilderView
    original_action = repair.profile._handle_builder_action
    original_signature_view = repair.signatures.ProfileCardSetupView
    original_signature_save = repair.signatures._save_selected_channels
    original_roles_post = repair.roles_center._post_default_panel

    try:
        assert repair.apply_profile_contextual_permission_repair() is True
        assert repair.profile._profile_builder_status is repair.contextual_profile_builder_status
        assert repair.profile.ProfileBuilderView is repair.ContextualProfileBuilderView
        assert repair.profile._handle_builder_action is repair.contextual_builder_action
        assert repair.signatures.ProfileCardSetupView is repair.ContextualProfileCardSetupView
        assert repair.signatures._save_selected_channels is repair.contextual_save_selected_channels
        assert repair.roles_center._post_default_panel is repair.contextual_roles_panel_post
    finally:
        repair.profile._profile_builder_status = original_status
        repair.profile.ProfileBuilderView = original_view
        repair.profile._handle_builder_action = original_action
        repair.signatures.ProfileCardSetupView = original_signature_view
        repair.signatures._save_selected_channels = original_signature_save
        repair.roles_center._post_default_panel = original_roles_post
        repair._PATCHED = False
