from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_commands_compact_dank_after_all_additive_registrars() -> None:
    source = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
    assert source.index("register_public_profile_cards(bot, bot.tree)") < source.index(
        "compact_public_dank_surface(bot, bot.tree)"
    )


def test_ui_first_surface_has_small_explicit_entry_set() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_command_hub.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None
    for required in (
        '"home"',
        '"profile"',
        '"help"',
        '"setup"',
        '"status"',
        '"diagnostics"',
        '"welcome"',
        '"card-studio"',
        '"card-preview"',
    ):
        assert required in source
    assert "DANK_PAYLOAD_SAFETY_LIMIT = 7600" in source
    assert "dank_payload_size(tree)" in source


def test_welcome_and_profiles_are_separate_ui_destinations() -> None:
    setup_source = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
    profile_source = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
    welcome_source = (ROOT / "stoney_verify/welcome_setup_ui.py").read_text(encoding="utf-8")
    studio_source = (ROOT / "stoney_verify/welcome_card_studio_ui.py").read_text(encoding="utf-8")
    assert 'label="Welcome & Join"' in setup_source
    assert 'label="Profile Signatures"' in setup_source
    assert "Add Welcome Channel" not in profile_source
    assert "Welcome Card Studio" in welcome_source
    assert 'label="Join Card Studio"' in welcome_source
    assert "canonical live runtime" in studio_source
    assert "Profile Signatures" in profile_source


def test_member_signature_studio_exposes_age_friendly_controls() -> None:
    source = (ROOT / "stoney_verify/profile_signature_studio.py").read_text(encoding="utf-8")
    for label in (
        "Appearance",
        "Privacy",
        "Platforms",
        "Server Roles",
        "Profile Tags",
        "Preview",
        "Reset My Look",
        "Theme",
        "Font",
        "Colors",
        "Background",
        "Layout",
        "Avatar Frame",
    ):
        assert f'label="{label}"' in source
    assert 'label="Profile Roles"' not in source


def test_image_signatures_require_attach_files() -> None:
    setup_core = (ROOT / "stoney_verify/profile_card_setup_ui_core.py").read_text(encoding="utf-8")
    runtime_core = (ROOT / "stoney_verify/profile_card_runtime_core.py").read_text(encoding="utf-8")
    assert '("Attach Files", permissions.attach_files)' in setup_core
    assert "permissions.attach_files" in runtime_core
