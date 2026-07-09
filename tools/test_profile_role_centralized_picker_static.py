from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")
PICKER = (ROOT / "stoney_verify/ui/picker.py").read_text(encoding="utf-8")


def test_profile_role_builder_does_not_use_native_role_select() -> None:
    manager = PROFILE.split("class ProfileCosmeticRoleManagerView", 1)[1].split("class ProfileBuilderView", 1)[0]
    assert "DankRoleSelect(" not in manager
    assert "discord.ui.RoleSelect" not in manager
    assert "Browse / Add Server Roles" in manager


def test_profile_role_builder_uses_centralized_multi_picker() -> None:
    assert "ProfileRoleAddPickerView(DankMultiPickerView)" in PROFILE
    assert "_profile_role_picker_candidates" in PROFILE
    assert "_profile_role_picker_choices" in PROFILE
    assert "_handle_profile_role_add_picker" in PROFILE
    assert "DankMultiPickerView" in PROFILE


def test_profile_role_picker_has_pagination_controls() -> None:
    assert "PROFILE_ROLE_PICKER_PAGE_SIZE" in PROFILE
    assert "Previous Roles" in PROFILE
    assert "Next Roles" in PROFILE
    assert "Back to Role Manager" in PROFILE
    assert "role_picker_page" in PROFILE


def test_centralized_picker_contract_exists() -> None:
    assert "class DankMultiPickerView" in PICKER
    assert "Use this for official multi-choice surfaces" in PICKER
    assert "class DankRoleSelect" in PICKER


def test_native_role_select_import_removed_from_profile_builder() -> None:
    assert "DankRoleSelect" not in PROFILE
    assert "from stoney_verify.ui.picker import DankChoice, DankMultiPickerView" in PROFILE


if __name__ == "__main__":
    for test in (
        test_profile_role_builder_does_not_use_native_role_select,
        test_profile_role_builder_uses_centralized_multi_picker,
        test_profile_role_picker_has_pagination_controls,
        test_centralized_picker_contract_exists,
        test_native_role_select_import_removed_from_profile_builder,
    ):
        test()
        print(f"PASS {test.__name__}")
