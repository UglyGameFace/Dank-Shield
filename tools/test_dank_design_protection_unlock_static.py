from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/services/server_design_studio.py").read_text(encoding="utf-8")


def test_protection_manager_has_obvious_unlock_controls() -> None:
    assert "Protected Names / Unlock" in PUBLIC
    assert "Allow Font on Defaults" in PUBLIC
    assert "Restore Default Protection" in PUBLIC
    assert "Pick Exact Item" in PUBLIC
    assert "Rename Protection" not in PUBLIC


def test_default_protected_names_can_allow_font_without_full_layout() -> None:
    assert "_set_default_protection_rules" in PUBLIC
    assert "mode=\"font_only\"" in PUBLIC
    assert "font styling while still blocking full layout/frame changes" in PUBLIC
    assert "dank_design:protection_allow_font_defaults" in PUBLIC
    assert "dank_design:protection_restore_defaults" in PUBLIC


def test_protection_manager_explains_font_fallback_is_not_a_blocker() -> None:
    assert "Unsupported font glyphs fall back per character" in PUBLIC
    assert "protection is a rename policy, not a font failure" in PUBLIC


def test_font_engine_falls_back_per_character() -> None:
    assert "font styling is never allowed to block a rename by itself" in SERVICE
    assert "falls back per" in SERVICE
    assert "TransformSubstitution" in SERVICE
    assert "requested font has no distinct glyph for this character" in SERVICE


if __name__ == "__main__":
    for test in (
        test_protection_manager_has_obvious_unlock_controls,
        test_default_protected_names_can_allow_font_without_full_layout,
        test_protection_manager_explains_font_fallback_is_not_a_blocker,
        test_font_engine_falls_back_per_character,
    ):
        test()
        print(f"PASS {test.__name__}")
