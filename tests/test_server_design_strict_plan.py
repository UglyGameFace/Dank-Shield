from __future__ import annotations

from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_studio as studio


def _styled_free_games() -> str:
    text, _subs = studio.transform_text_safe(
        "free-games",
        "fraktur",
        fallback_order=studio.fallback_ladder("fraktur"),
    )
    return text


def _strict_options(**extra: object) -> dict[str, object]:
    return plan_service.normalize_plan_options(
        {"theme_id": "gothic_clean", "strength": 5, **extra},
        strict=True,
    )


def test_native_strict_plan_flags_missing_separator_as_changed() -> None:
    options = _strict_options(separator_id="bar_full")
    result = studio.build_styled_name(
        f"🎮{_styled_free_games()}",
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(options["separator_id"]),
        font="fraktur",
        exact_match=bool(options["exact_match"]),
    )
    assert result.status == "changed"
    assert result.after.startswith("🎮｜")


def test_native_strict_plan_flags_thin_separator_when_fullwidth_is_expected() -> None:
    options = _strict_options(separator_id="bar_full")
    result = studio.build_styled_name(
        f"🎮│{_styled_free_games()}",
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(options["separator_id"]),
        font="fraktur",
        exact_match=bool(options["exact_match"]),
    )
    assert result.status == "changed"
    assert result.after.startswith("🎮｜")


def test_native_strict_plan_allows_exact_separator_match_to_remain_unchanged() -> None:
    options = _strict_options(separator_id="bar_full")
    current = f"🎮｜{_styled_free_games()}"
    result = studio.build_styled_name(
        current,
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(options["separator_id"]),
        font="fraktur",
        exact_match=bool(options["exact_match"]),
    )
    assert result.status == "unchanged"
    assert result.after == current


def test_native_plan_allows_visual_log_channel_repair_without_global_protected_set_mutation() -> None:
    before = set(studio.DEFAULT_PROTECTED_NAMES)
    options = _strict_options()
    result = studio.build_styled_name(
        "mod-log",
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(options["separator_id"]),
        font="fraktur",
        exact_match=bool(options["exact_match"]),
        protection_rules=options["protection_rules"],
    )
    assert not result.protected
    assert result.status == "changed"
    assert " | " in result.after
    assert set(studio.DEFAULT_PROTECTED_NAMES) == before


def test_gothic_clean_default_uses_clear_spaced_pipe_without_theme_catalog_patch() -> None:
    original_theme_separator = studio.THEMES_BY_ID["gothic_clean"].channel_separator
    options = _strict_options()
    result = studio.build_styled_name(
        "free-games",
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(options["separator_id"]),
        exact_match=bool(options["exact_match"]),
    )
    assert original_theme_separator == "bar_full"
    assert options["separator_id"] == "pipe_spaced"
    assert result.status == "changed"
    assert result.after.startswith("🎮 | ")
    assert "｜" not in result.after
    assert "┃" not in result.after
    assert "❘" not in result.after


def test_known_separator_is_not_swallowed_into_icon_prefix() -> None:
    with_icon = studio.parse_channel_name("🎮│free-games")
    assert with_icon["emoji"] == "🎮"
    assert with_icon["separator"] == "│"
    assert with_icon["base_name"] == "free-games"

    separator_only = studio.parse_channel_name("│free-games")
    assert separator_only["emoji"] == ""
    assert separator_only["separator"] == "│"
    assert separator_only["base_name"] == "free-games"


def test_new_unicode_fonts_transform_and_decode_cleanly() -> None:
    for style in ("sans", "double_struck"):
        rendered, substitutions = studio.transform_text_safe(
            "general-chat-420",
            style,
            fallback_order=studio.fallback_ladder(style),
        )
        assert rendered != "general-chat-420"
        assert substitutions == []
        assert studio.normalize_base_name(rendered) == "general-chat-420"


def test_curated_theme_catalog_only_uses_supported_fonts() -> None:
    assert {"modern_minimal", "double_struck_luxe", "night_gothic", "luxury_script"} <= set(studio.THEMES_BY_ID)
    assert all(theme.font in studio.DESIGN_FONT_STYLES for theme in studio.THEMES)
    assert len(studio.THEMES) <= 25


def test_double_hyphen_is_first_class_and_round_trips_cleanly() -> None:
    parsed = studio.parse_channel_name("👋--welcome")

    assert parsed["emoji"] == "👋"
    assert parsed["separator"] == "--"
    assert parsed["base_name"] == "welcome"
    assert parsed["duplicate_separators"] is False

    accidental = studio.parse_channel_name("👋----welcome")
    assert accidental["separator"] == "--"
    assert accidental["duplicate_separators"] is True

    result = studio.build_styled_name(
        "👋--welcome",
        theme_id="420_lounge",
        strength=2,
        separator_id="double_dash",
        font="normal",
        emoji_override="👋",
        protection_mode="full",
        exact_match=True,
    )

    assert result.status == "unchanged"
    assert result.after == "👋--welcome"
    assert result.separator_id == "double_dash"


def test_first_class_spaced_pipe_reproduces_existing_mixed_layout() -> None:
    result = studio.build_styled_name(
        "🏁start-here-verify",
        theme_id="gothic_clean",
        strength=3,
        separator_id="pipe_spaced",
        font="normal",
        emoji_override="🏁",
        protection_mode="full",
        exact_match=True,
    )

    assert studio.SEPARATORS_BY_ID["pipe_spaced"].value == " | "
    assert result.after == "🏁 | start-here-verify"
    assert result.separator_id == "pipe_spaced"



def test_real_mixed_server_separator_patterns_are_parseable() -> None:
    cases = (
        ("👋--welcome", "--", "welcome"),
        ("📣-announcements", "-", "announcements"),
        ("🚩-rules", "-", "rules"),
        ("🎉--giveaway", "--", "giveaway"),
        ("📊--levelups", "--", "levelups"),
        ("❌--unverified-chat", "--", "unverified-chat"),
        ("🎟️--support", "--", "support"),
        ("📁transcripts", "", "transcripts"),
        ("🗄️--mod-log", "--", "mod-log"),
        ("⬜--mods-only", "--", "mods-only"),
        ("🤖--price-glitch-bot", "--", "price-glitch-bot"),
        ("🎙️--vc-verify-requests", "--", "vc-verify-requests"),
        ("📰--welcome-exit", "--", "welcome-exit"),
    )

    for raw, separator, base_name in cases:
        parsed = studio.parse_channel_name(raw)
        assert parsed["separator"] == separator, raw
        assert parsed["base_name"] == base_name, raw
