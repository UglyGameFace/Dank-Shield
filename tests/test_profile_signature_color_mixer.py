from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify import profile_signature_studio as studio
from stoney_verify.profile_signature_style import (
    PROFILE_THEME_SPECS,
    SERVER_STYLE_CONFIG_KEYS,
    derive_custom_colors,
)


ROOT = Path(__file__).resolve().parents[1]
STUDIO_SOURCE = (ROOT / "stoney_verify/profile_signature_studio.py").read_text(encoding="utf-8")
STYLE_SOURCE = (ROOT / "stoney_verify/profile_signature_style.py").read_text(encoding="utf-8")


def test_appearance_menu_exposes_every_real_control() -> None:
    view = studio.ProfileAppearanceView(author_id=42)
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert labels == {
        "Theme",
        "Font",
        "Colors",
        "Mix Colors",
        "Background",
        "Layout",
        "Avatar Frame",
        "Custom Art Guide",
        "Preview",
        "Back",
    }


def test_four_color_updates_use_distinct_persistent_slots() -> None:
    values = ("#8FFF52", "#2DE8CD", "#B85BFF", "#FFCC52")
    member = studio._mix_updates(values, server=False)
    server = studio._mix_updates(values, server=True)

    assert member == {
        "signature_color_mode": "custom",
        "signature_custom_primary": "#8FFF52",
        "signature_custom_secondary": "#2DE8CD",
        "signature_custom_tertiary": "#B85BFF",
        "signature_custom_highlight": "#FFCC52",
    }
    assert server == {
        SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
        SERVER_STYLE_CONFIG_KEYS["custom_primary"]: "#8FFF52",
        SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: "#2DE8CD",
        SERVER_STYLE_CONFIG_KEYS["custom_tertiary"]: "#B85BFF",
        SERVER_STYLE_CONFIG_KEYS["custom_highlight"]: "#FFCC52",
    }


def test_legacy_two_color_profiles_derive_two_safe_extra_accents() -> None:
    colors = derive_custom_colors("#8FFF52", "#2DE8CD")
    assert colors[:2] == ("#8FFF52", "#2DE8CD")
    assert all(value.startswith("#") and len(value) == 7 for value in colors)
    assert colors[2] not in {"", colors[0], colors[1]}
    assert colors[3] != ""


def test_color_mixer_has_four_slots_and_complete_edit_controls() -> None:
    view = studio.ColorMixerView(
        author_id=42,
        server=False,
        values=("#8FFF52", "#2DE8CD", "#B85BFF", "#FFCC52"),
    )
    labels = [str(child.label) for child in view.children if isinstance(child, discord.ui.Button)]
    assert any(label.startswith("1. Primary:") for label in labels)
    assert any(label.startswith("2. Secondary:") for label in labels)
    assert any(label.startswith("3. Accent 3:") for label in labels)
    assert any(label.startswith("4. Highlight:") for label in labels)
    assert {"Swap 1 ↔ 2", "Rotate Order", "Remove Last", "Reset to Theme", "Advanced Hex", "Back to Appearance"} <= set(labels)


def test_advanced_hex_is_optional_four_field_fallback() -> None:
    modal = studio.AdvancedProfileColorsModal(
        server=False,
        author_id=42,
        values=("#8FFF52", "#2DE8CD", "", ""),
    )
    assert len(modal.children) == 4
    assert [str(child.label) for child in modal.children] == [
        "Primary hex (optional)",
        "Secondary hex (optional)",
        "Accent 3 hex (optional)",
        "Highlight hex (optional)",
    ]
    assert all(child.required is False for child in modal.children)


def test_each_color_save_immediately_renders_the_real_card() -> None:
    save_block = STUDIO_SOURCE.split("async def _save_mix_and_preview(", 1)[1].split(
        "async def _reset_mix_and_preview(", 1
    )[0]
    assert "await _preview_color_mixer(" in save_block
    preview_block = STUDIO_SOURCE.split("async def _preview_color_mixer(", 1)[1].split(
        "async def _save_mix_and_preview(", 1
    )[0]
    assert "await _preview(" in preview_block
    assert "view_factory=lambda _source: ColorMixerView(" in preview_block
    assert "send_modal" not in STUDIO_SOURCE.split(
        '@discord.ui.button(label="Mix Colors"', 1
    )[1].split('@discord.ui.button(label="Background"', 1)[0]


def test_profile_theme_catalog_contains_real_platform_focused_families() -> None:
    assert {
        "steam_focus",
        "xbox_focus",
        "playstation_focus",
        "epic_focus",
        "multi_platform",
    } <= set(PROFILE_THEME_SPECS)
    for key in ("steam_focus", "xbox_focus", "playstation_focus", "epic_focus", "multi_platform"):
        assert "focus" in PROFILE_THEME_SPECS[key].description.lower() or key == "multi_platform"


def test_style_schema_keeps_four_color_keys_for_members_and_servers() -> None:
    for key in (
        "signature_custom_primary",
        "signature_custom_secondary",
        "signature_custom_tertiary",
        "signature_custom_highlight",
    ):
        assert key in STYLE_SOURCE
    for key in (
        "custom_primary",
        "custom_secondary",
        "custom_tertiary",
        "custom_highlight",
    ):
        assert key in SERVER_STYLE_CONFIG_KEYS
