from __future__ import annotations

from stoney_verify.services import server_design_studio as studio
from stoney_verify.commands_ext import public_design_studio  # noqa: F401
from stoney_verify.startup_guards import server_design_strict_layout_guard


def _styled_free_games() -> str:
    text, _subs = studio.transform_text_safe(
        "free-games",
        "fraktur",
        fallback_order=studio.fallback_ladder("fraktur"),
    )
    return text


def test_strict_layout_guard_flags_missing_separator_as_changed():
    server_design_strict_layout_guard.apply()

    result = studio.build_styled_name(
        f"🎮{_styled_free_games()}",
        theme_id="gothic_clean",
        strength=5,
        separator_id="bar_full",
        font="fraktur",
    )

    assert result.status == "changed"
    assert result.after.startswith("🎮｜")


def test_strict_layout_guard_flags_thin_separator_when_fullwidth_is_expected():
    server_design_strict_layout_guard.apply()

    result = studio.build_styled_name(
        f"🎮│{_styled_free_games()}",
        theme_id="gothic_clean",
        strength=5,
        separator_id="bar_full",
        font="fraktur",
    )

    assert result.status == "changed"
    assert result.after.startswith("🎮｜")


def test_strict_layout_guard_allows_exact_separator_match_to_remain_unchanged():
    server_design_strict_layout_guard.apply()

    current = f"🎮｜{_styled_free_games()}"
    result = studio.build_styled_name(
        current,
        theme_id="gothic_clean",
        strength=5,
        separator_id="bar_full",
        font="fraktur",
    )

    assert result.status == "unchanged"
    assert result.after == current


def test_strict_layout_guard_allows_visual_log_channel_repair():
    server_design_strict_layout_guard.apply()

    result = studio.build_styled_name(
        "mod-log",
        theme_id="gothic_clean",
        strength=5,
        separator_id="bar_full",
        font="fraktur",
    )

    assert not result.protected
    assert result.status == "changed"
    assert "｜" in result.after


def test_gothic_clean_default_uses_clear_spaced_pipe_separator():
    server_design_strict_layout_guard.apply()

    theme = studio.THEMES_BY_ID["gothic_clean"]
    result = studio.build_styled_name(
        "free-games",
        theme_id="gothic_clean",
        strength=5,
    )

    assert theme.channel_separator == "pipe_spaced"
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
