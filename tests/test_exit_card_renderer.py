from __future__ import annotations

from io import BytesIO

from PIL import Image

from stoney_verify.exit_card_renderer import render_exit_card
from stoney_verify.welcome_card_typography_engine import CARD_HEIGHT, CARD_WIDTH


def _avatar_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (512, 512), (64, 96, 128)).save(output, format="PNG")
    return output.getvalue()


def test_exit_card_renderer_produces_real_canonical_png() -> None:
    rendered = render_exit_card(
        avatar_bytes=_avatar_png(),
        display_name="Nine Byte",
        server_name="Vibers Paradise",
        member_count=124,
        theme_key="cyber_neon",
        font_style_key="neon",
        color_mode="theme",
    )

    assert rendered.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (CARD_WIDTH, CARD_HEIGHT)
        assert image.mode == "RGB"


def test_exit_card_renderer_fits_long_names_without_changing_canvas() -> None:
    rendered = render_exit_card(
        avatar_bytes=_avatar_png(),
        display_name="Extremely Long Display Name That Must Fit Safely Without Cropping Edges",
        server_name="A Very Long Community Server Name That Also Needs Safe Fitting",
        member_count=123456,
        theme_key="minimal_glass",
        font_style_key="street",
        color_mode="custom",
        custom_primary="#22DCFF",
        custom_secondary="#BC42FF",
    )

    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (CARD_WIDTH, CARD_HEIGHT)
