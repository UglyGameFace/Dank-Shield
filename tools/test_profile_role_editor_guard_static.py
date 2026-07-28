from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
GUARD = (ROOT / "stoney_verify/startup_guards/profile_role_editor_guard.py").read_text(encoding="utf-8")
SELF_GUARD = (ROOT / "stoney_verify/startup_guards/self_roles_command_guard.py").read_text(encoding="utf-8")
PROFILE = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_profile_tag_guard_loads_before_self_roles_registration() -> None:
    assert "stoney_verify.startup_guards.self_roles_command_guard" in STARTUP
    assert "stoney_verify.startup_guards.profile_role_editor_guard" in STARTUP
    assert STARTUP.index("profile_role_editor_guard") < STARTUP.index("self_roles_command_guard")


def test_self_roles_applies_profile_tag_guard_before_registration() -> None:
    assert "profile_role_editor_guard.apply()" in SELF_GUARD
    assert "register = getattr(public_self_roles_group" in SELF_GUARD
    assert "register(bot," in SELF_GUARD
    assert SELF_GUARD.index("profile_role_editor_guard.apply()") < SELF_GUARD.index("register = getattr(public_self_roles_group")
    assert SELF_GUARD.index("profile_role_editor_guard.apply()") < SELF_GUARD.index("register(bot,")


def test_profile_panel_and_editor_get_review_only_suggestion_buttons() -> None:
    assert "ProfilePanelViewWithTagSuggestions" in GUARD
    assert "ProfileEditViewWithTagSuggestions" in GUARD
    assert "Suggest Profile Tag" in GUARD
    assert "suggest_role" in GUARD
    assert "never creates or assigns" in GUARD
    assert "await guild.create_role" not in GUARD
    assert "member.add_roles" not in GUARD


def test_native_profile_source_has_clear_profile_tag_labels() -> None:
    assert "Server Roles / Cosmetics" not in PROFILE
    assert "Profile Tags & Cosmetics" in PROFILE
    assert "Browse / Add Profile Tags" in PROFILE
    assert "Add Profile Tags & Cosmetics" in PROFILE
    assert "ProfileRoleAddPickerView(DankMultiPickerView)" in PROFILE
    assert "Remove Profile Tag" in PROFILE


def test_old_mixed_cosmetic_wording_is_not_restored_by_guard() -> None:
    for forbidden in (
        "Server Roles / Cosmetics",
        "ProfileBuilderViewWithRoleEditor",
        "builder:role_editor",
        "_open_role_editor",
        "_ORIGINAL_HANDLE_BUILDER",
        "_handle_builder_action_patched",
        "Suggest Role",
        "Suggest Profile Role",
    ):
        assert forbidden not in GUARD, f"obsolete duplicate or mixed wording remains: {forbidden}"


def test_builder_reuses_one_native_profile_tags_manager_button() -> None:
    route = 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"'
    assert route in PROFILE
    assert PROFILE.count(route) == 1
    assert "class ProfileCosmeticRoleManagerView" in PROFILE
    assert "PROFILE_COSMETIC_ROLE_IDS_KEY" in PROFILE


def test_guard_describes_server_roles_and_profile_tags_as_separate_concepts() -> None:
    assert "separate from the member's ordinary server-role visibility setting" in GUARD
    assert "Pronouns, identity, interests, community labels" in GUARD
    assert "staff, access, verification, moderation, ticket" in GUARD.casefold()


if __name__ == "__main__":
    for test in (
        test_profile_tag_guard_loads_before_self_roles_registration,
        test_self_roles_applies_profile_tag_guard_before_registration,
        test_profile_panel_and_editor_get_review_only_suggestion_buttons,
        test_native_profile_source_has_clear_profile_tag_labels,
        test_old_mixed_cosmetic_wording_is_not_restored_by_guard,
        test_builder_reuses_one_native_profile_tags_manager_button,
        test_guard_describes_server_roles_and_profile_tags_as_separate_concepts,
    ):
        test()
        print(f"PASS {test.__name__}")
