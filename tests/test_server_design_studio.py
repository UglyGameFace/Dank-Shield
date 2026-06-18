from __future__ import annotations

import pytest

from stoney_verify.services import server_design_studio as studio


ASCII_SAMPLE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ "


def test_separator_library_contains_required_packs_and_previews():
    packs = {spec.pack for spec in studio.SEPARATOR_LIBRARY}
    assert "Clean Vertical" in packs
    assert "Minimal" in packs
    assert "Aesthetic" in packs
    assert "Brackets" in packs
    assert "Gaming / Tech" in packs
    assert "Loud / Premium" in packs
    for spec in studio.SEPARATOR_LIBRARY:
        ok, _warnings = studio.validate_separator(spec)
        assert ok, spec.id
        assert "announcements" in studio.separator_preview(spec.id)


def test_category_frame_builder_validates_length_shape():
    for frame in studio.CATEGORY_FRAMES:
        preview = studio.category_frame_preview(frame.id)
        assert preview
        assert len(preview) <= studio.DISCORD_NAME_LIMIT


def test_icon_suggestion_matching():
    assert studio.suggested_icon("announcements", icon_pack="420_lounge") == "📢"
    assert studio.suggested_icon("voice-verification", icon_pack="420_lounge") in {"🎙️", "🔐"}
    assert studio.suggested_icon("bot-commands", icon_pack="bot_utility") == "🤖"


@pytest.mark.parametrize("font", studio.FONT_STYLES)
def test_every_font_processes_ascii_without_throwing(font: str):
    output, substitutions = studio.transform_text_safe(ASCII_SAMPLE, font)
    assert output
    assert isinstance(substitutions, list)
    assert "-" in output or "－" in output


def test_unsupported_characters_do_not_block_or_empty_output():
    output, substitutions = studio.transform_text_safe("rules-雪-🚀", "fraktur")
    assert output
    assert "雪" in output
    assert isinstance(substitutions, list)


def test_styled_names_are_stripped_to_base_before_restyle():
    current = "📢｜𝔞𝔫𝔫𝔬𝔲𝔫𝔠𝔢𝔪𝔢𝔫𝔱𝔰"
    parsed = studio.parse_channel_name(current)
    assert parsed["base_name"] == "announcements"
    result = studio.build_styled_name(current, theme_id="premium_clean", strength=4)
    assert "📢｜📢" not in result.after
    assert result.base_name == "announcements"


def test_protected_channels_are_skipped():
    result = studio.build_styled_name("mod-log", theme_id="gothic_clean", strength=5)
    assert result.protected
    assert result.status == "protected"
    assert result.after == "mod-log"


def test_duplicate_output_detection():
    items = [
        {"before": "general", "after": "💬｜general", "status": "changed"},
        {"before": "General", "after": "💬｜general", "status": "changed"},
    ]
    assert studio.detect_duplicate_outputs(items)


def test_preview_and_design_score_include_warning_state():
    item = studio.build_styled_name("very long channel name that will be cramped on mobile", theme_id="gothic_clean", strength=5).to_plan_item()
    lines = studio.preview_lines([item], filter_mode="warnings")
    score = studio.design_score([item])
    assert lines
    assert int(score["readability"]) <= 100


def test_never_returns_empty_for_non_empty_input():
    result = studio.build_styled_name("!!!", theme_id="gothic_clean", strength=5)
    assert result.after


def test_fallback_substitutions_are_reported_for_missing_requested_glyphs():
    _output, substitutions = studio.transform_text_safe("ABC123", "small_caps")
    assert substitutions
