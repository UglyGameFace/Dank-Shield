from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from stoney_verify.welcome_card_renderer import (
    BUILTIN_THEMES,
    CARD_HEIGHT,
    CARD_WIDTH,
    normalize_theme_key,
    render_welcome_card,
    validate_custom_background,
)


def _avatar_bytes() -> bytes:
    image = Image.new("RGB", (256, 256), (80, 110, 160))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _background_bytes(size: tuple[int, int] = (1200, 400)) -> bytes:
    image = Image.new("RGB", size, (35, 40, 55))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_all_builtin_themes_render_exact_production_dimensions() -> None:
    for theme_key in BUILTIN_THEMES:
        rendered = render_welcome_card(
            avatar_bytes=_avatar_bytes(),
            display_name="UglyGameFace",
            server_name="The 420 Lobby",
            member_count=420,
            theme_key=theme_key,
        )
        with Image.open(BytesIO(rendered)) as image:
            assert image.size == (CARD_WIDTH, CARD_HEIGHT)
            assert image.format == "PNG"


def test_long_names_and_large_counts_never_break_rendering() -> None:
    rendered = render_welcome_card(
        avatar_bytes=_avatar_bytes(),
        display_name="This Is An Extremely Long Discord Display Name That Must Fit Safely",
        server_name="A Very Long Community Server Name That Must Stay Inside The Safe Text Area",
        member_count=987654321,
        theme_key="420_lobby",
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (1200, 400)
        assert len(rendered) > 10_000


def test_missing_or_invalid_avatar_uses_safe_fallback() -> None:
    rendered = render_welcome_card(
        avatar_bytes=b"not-an-image",
        display_name="Member",
        server_name="Server",
        member_count=1,
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (1200, 400)


def test_custom_background_is_cropped_and_keeps_exact_canvas() -> None:
    custom = _background_bytes((1800, 600))
    validate_custom_background(custom)
    rendered = render_welcome_card(
        avatar_bytes=_avatar_bytes(),
        display_name="Custom User",
        server_name="Custom Server",
        member_count=25,
        custom_background_bytes=custom,
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (1200, 400)


def test_invalid_custom_background_dimensions_are_rejected() -> None:
    with pytest.raises(ValueError, match="3:1"):
        validate_custom_background(_background_bytes((800, 800)))


def test_unknown_theme_falls_back_to_supported_theme() -> None:
    assert normalize_theme_key("not-real") in BUILTIN_THEMES
