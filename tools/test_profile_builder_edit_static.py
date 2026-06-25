from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_profile_panel_has_clear_edit_entrypoint() -> None:
    assert 'label="Edit My Profile"' in TEXT
    assert 'custom_id=f"{PROFILE_PREFIX}edit"' in TEXT


def test_profile_edit_hub_exists() -> None:
    assert "def _profile_edit_embed" in TEXT
    assert "class ProfileEditView" in TEXT
    assert "Edit Pronouns" in TEXT
    assert "Edit Identity" in TEXT
    assert "Edit Interests" in TEXT


def test_view_profile_is_not_dead_end() -> None:
    assert "def _profile_card_view_with_actions" in TEXT
    assert "full_roles_self" in TEXT
    assert "view=_profile_card_view_with_actions(member)" in TEXT


def test_profile_handler_routes_edit_and_full_roles() -> None:
    assert 'if suffix == "edit":' in TEXT
    assert 'if suffix == "full_roles_self":' in TEXT


if __name__ == "__main__":
    for test in (
        test_profile_panel_has_clear_edit_entrypoint,
        test_profile_edit_hub_exists,
        test_view_profile_is_not_dead_end,
        test_profile_handler_routes_edit_and_full_roles,
    ):
        test()
        print(f"PASS {test.__name__}")
