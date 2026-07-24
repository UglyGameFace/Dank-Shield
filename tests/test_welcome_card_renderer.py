from __future__ import annotations

import io

from PIL import Image

from stoney_verify.welcome_card_renderer import (
    CARD_SIZE,
    THEMES,
    normalize_theme_name,
    ordinal,
    render_welcome_card_sync,
)


def test_ordinals_are_correct() -> None:
    assert ordinal(1) == "1st"
    assert ordinal(2) == "2nd"
    assert ordinal(3) == "3rd"
    assert ordinal(4) == "4th"
    assert ordinal(11) == "11th"
    assert ordinal(12) == "12th"
    assert ordinal(13) == "13th"
    assert ordinal(21) == "21st"


def test_theme_aliases_and_fallback() -> None:
    assert normalize_theme_name("420") == "420_lobby"
    assert normalize_theme_name("premium gold") == "royal_gold"
    assert normalize_theme_name("not-a-theme") == "neon_pulse"


def test_all_builtin_themes_render_exact_discord_safe_dimensions() -> None:
    for theme_name in THEMES:
        payload = render_welcome_card_sync(
            avatar_bytes=None,
            display_name="UglyGameFace",
            server_name="The 420 Lobby",
            member_count=420,
            theme_name=theme_name,
        )
        image = Image.open(io.BytesIO(payload))
        assert image.size == CARD_SIZE
        assert image.format == "PNG"


def test_long_names_and_server_names_render_without_crashing() -> None:
    payload = render_welcome_card_sync(
        avatar_bytes=b"not-an-image",
        display_name="A_Very_Long_Discord_Display_Name_That_Must_Fit_Cleanly_Without_Overflowing",
        server_name="An Extremely Long Community Server Name That Still Needs To Fit",
        member_count=999_999,
        theme_name="420_lobby",
    )
    image = Image.open(io.BytesIO(payload))
    assert image.size == CARD_SIZE
    assert len(payload) > 10_000
