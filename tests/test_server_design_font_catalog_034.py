from __future__ import annotations

from stoney_verify.services import channel_builder_runtime
from stoney_verify.services import server_design_studio as studio
from stoney_verify.startup_guards import channel_builder_full_font_catalog_guard as full_catalog
from stoney_verify.startup_guards import channel_font_exact_unicode_guard as exact_guard
from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_setup


def test_font_catalogs_share_clean_sans_and_double_struck() -> None:
    for style in ("sans", "double_struck"):
        assert style in studio.FONT_STYLES
        assert style in font_setup._STYLE_LABELS

        runtime = channel_builder_runtime._unicode_map(style)
        full = full_catalog.full_unicode_map(style)
        exact = exact_guard.exact_unicode_map(style)

        for char in ("a", "g", "Z", "4"):
            assert runtime[char] != char
            assert full[char] != char
            assert exact[char] != char

        assert exact_guard.live_font_missing_after_fallback(style) == ""


def test_double_struck_special_capitals_are_real_unicode_letters() -> None:
    mapping = exact_guard.exact_unicode_map("double_struck")
    assert mapping["C"] == "ℂ"
    assert mapping["H"] == "ℍ"
    assert mapping["N"] == "ℕ"
    assert mapping["R"] == "ℝ"
    assert mapping["Z"] == "ℤ"


def test_server_font_preview_uses_same_transform_engine() -> None:
    for style in studio.FONT_STYLES:
        preview = studio.font_preview(style, sample="general-420")
        assert preview
        if style != "normal":
            assert preview != "general-420"
        assert studio.normalize_base_name(preview) == "general-420"


def test_theme_catalog_is_safe_for_discord_selector_limit() -> None:
    assert len(studio.THEMES) <= 25
    assert len(studio.FONT_STYLES) + 1 <= 25
    assert all(theme.font in studio.FONT_STYLES for theme in studio.THEMES)
