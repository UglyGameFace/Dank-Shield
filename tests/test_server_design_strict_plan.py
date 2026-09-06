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
