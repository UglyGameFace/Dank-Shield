from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
GUARD = (ROOT / "stoney_verify/startup_guards/profile_role_editor_guard.py").read_text(encoding="utf-8")
PROFILE = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_profile_role_editor_guard_is_loaded_after_self_roles() -> None:
    assert "stoney_verify.startup_guards.self_roles_command_guard" in STARTUP
    assert "stoney_verify.startup_guards.profile_role_editor_guard" in STARTUP
    assert STARTUP.index("self_roles_command_guard") < STARTUP.index("profile_role_editor_guard")


def test_profile_panel_and_editor_get_suggest_role_buttons() -> None:
    assert "ProfilePanelViewWithRoleSuggestions" in GUARD
    assert "ProfileEditViewWithRoleSuggestions" in GUARD
    assert "Suggest Role" in GUARD
    assert "suggest_role" in GUARD


def test_server_cosmetics_button_is_relabelled_with_roles() -> None:
    assert "Server Roles / Cosmetics" in GUARD
    assert "PROFILE_ROLES_COSMETICS_LABEL" in GUARD
    assert "_retitle_profile_roles_button" in GUARD
    assert "These are profile/server cosmetic roles" in GUARD


def test_builder_gets_profile_roles_cosmetics_editor_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" in GUARD
    assert "Profile Roles / Cosmetics" in GUARD
    assert "builder:role_editor" in GUARD
    assert "_open_role_editor" in GUARD


def test_role_suggestions_are_review_only() -> None:
    assert "ProfileRoleSuggestionModal" in GUARD
    assert "does **not** create, assign, or approve" in GUARD
    assert "never create or assign roles automatically" in GUARD
    assert "await guild.create_role" not in GUARD
    assert "member.add_roles" not in GUARD


def test_existing_profile_cosmetic_manager_still_exists() -> None:
    assert "class ProfileCosmeticRoleManagerView" in PROFILE
    assert "Add an existing cosmetic role" in PROFILE
    assert "PROFILE_COSMETIC_ROLE_IDS_KEY" in PROFILE


if __name__ == "__main__":
    for test in (
        test_profile_role_editor_guard_is_loaded_after_self_roles,
        test_profile_panel_and_editor_get_suggest_role_buttons,
        test_server_cosmetics_button_is_relabelled_with_roles,
        test_builder_gets_profile_roles_cosmetics_editor_button,
        test_role_suggestions_are_review_only,
        test_existing_profile_cosmetic_manager_still_exists,
    ):
        test()
        print(f"PASS {test.__name__}")
