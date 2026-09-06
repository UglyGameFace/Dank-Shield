from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/services/server_design_studio.py").read_text(encoding="utf-8")


def test_protection_manager_has_obvious_unlock_controls() -> None:
    assert 'label="Saved Rules & Protection"' in V2
    assert 'label="Protection"' in V2
    assert "Allow Font + Layout on Defaults" in LEGACY
    assert "Restore Default Protection" in LEGACY
    assert "Pick Category" in LEGACY
    assert "Pick Channel" in LEGACY
    assert "Rename Protection" not in LEGACY


def test_default_protected_names_can_allow_font_and_layout_without_full_framing() -> None:
    assert "_set_default_protection_rules" in LEGACY
    assert "mode=\"font_only\"" in LEGACY
    assert "Default Protected Names Allow Font + Layout" in LEGACY
    assert "separator + font styling while still blocking category-frame/full styling" in LEGACY
    assert "dank_design:protection_allow_font_defaults" in LEGACY
    assert "dank_design:protection_restore_defaults" in LEGACY


def test_font_fallback_is_a_renderer_contract_not_protection_ui_copy() -> None:
    assert "Unsupported font glyphs fall back per character" not in V2
    assert "protection is a rename policy, not a font failure" not in V2
    assert "font styling is never allowed to block a rename by itself" in SERVICE
    assert "falls back per" in SERVICE
    assert "TransformSubstitution" in SERVICE
    assert "requested font has no distinct glyph for this character" in SERVICE


if __name__ == "__main__":
    for test in (
        test_protection_manager_has_obvious_unlock_controls,
        test_default_protected_names_can_allow_font_and_layout_without_full_framing,
        test_font_fallback_is_a_renderer_contract_not_protection_ui_copy,
    ):
        test()
        print(f"PASS {test.__name__}")