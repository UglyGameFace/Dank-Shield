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
    assert "Signature Settings" in TEXT


def test_view_profile_uses_the_privacy_aware_composer_and_keeps_actions() -> None:
    assert "def _profile_card_view_with_actions" in TEXT
    assert "full_roles_self" in TEXT
    assert "send_privacy_aware_profile" in TEXT
    assert "view=_profile_card_view_with_actions(member)" not in TEXT


def test_profile_handler_routes_edit_privacy_and_full_roles() -> None:
    assert 'if suffix == "edit":' in TEXT
    assert 'if suffix == "privacy":' in TEXT
    assert 'if suffix == "full_roles_self":' in TEXT
    assert "open_profile_signature_studio" in TEXT


if __name__ == "__main__":
    for test in (
        test_profile_panel_has_clear_edit_entrypoint,
        test_profile_edit_hub_exists,
        test_view_profile_uses_the_privacy_aware_composer_and_keeps_actions,
        test_profile_handler_routes_edit_privacy_and_full_roles,
    ):
        test()
        print(f"PASS {test.__name__}")
