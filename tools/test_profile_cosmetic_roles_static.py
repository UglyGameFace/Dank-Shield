from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_cosmetics_use_designated_pickers() -> None:
    assert "DankMultiPickerView" in TEXT
    assert "ProfileRoleAddPickerView(DankMultiPickerView)" in TEXT
    assert "DankRoleSelect" not in TEXT
    assert "ProfileCosmeticRoleManagerView" in TEXT


def test_cosmetic_allowlist_is_per_guild_config() -> None:
    assert 'PROFILE_COSMETIC_ROLE_IDS_KEY = "profile_cosmetic_role_ids"' in TEXT
    assert "upsert_guild_config" in TEXT
    assert "_profile_cosmetic_role_ids" in TEXT


def test_staff_builder_has_cosmetic_manager_entry() -> None:
    assert 'label="Profile Roles / Cosmetics"' in TEXT
    assert 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' in TEXT
    assert 'if action == "cosmetics":' in TEXT


def test_user_panel_has_cosmetic_picker_entry() -> None:
    assert 'label="Server Roles / Cosmetics"' in TEXT
    assert 'custom_id=f"{PROFILE_PREFIX}cosmetics"' in TEXT
    assert 'if suffix == "cosmetics":' in TEXT


def test_cosmetics_block_sensitive_roles() -> None:
    assert "PROFILE_COSMETIC_DENY_PERMISSIONS" in TEXT
    assert '"administrator"' in TEXT
    assert '"manage_roles"' in TEXT
    assert "_profile_cosmetic_role_blocker" in TEXT


if __name__ == "__main__":
    for test in (
        test_cosmetics_use_designated_pickers,
        test_cosmetic_allowlist_is_per_guild_config,
        test_staff_builder_has_cosmetic_manager_entry,
        test_user_panel_has_cosmetic_picker_entry,
        test_cosmetics_block_sensitive_roles,
    ):
        test()
        print(f"PASS {test.__name__}")
