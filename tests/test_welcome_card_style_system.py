from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from stoney_verify import welcome_card_renderer as renderer


def _image_bytes(left: tuple[int, int, int], right: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (1200, 400), left)
    ImageDraw.Draw(image).rectangle((600, 0, 1199, 399), fill=right)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_font_fallback_remains_scalable_without_host_fonts(monkeypatch) -> None:
    monkeypatch.setattr(renderer.Path, "is_file", lambda _path: False)
    small = renderer._font(20, bold=True, style_key="neon", role="name")
    large = renderer._font(90, bold=True, style_key="neon", role="name")
    small_box = small.getbbox("UglyGameFace")
    large_box = large.getbbox("UglyGameFace")
    assert large_box[2] - large_box[0] > (small_box[2] - small_box[0]) * 3
    assert large_box[3] - large_box[1] > (small_box[3] - small_box[1]) * 3


def test_palette_extraction_returns_two_distinct_vibrant_colors() -> None:
    palette = renderer.extract_image_palette(_image_bytes((25, 230, 80), (165, 40, 245)))
    assert palette is not None
    primary, secondary = palette
    assert renderer._color_distance(primary, secondary) >= 60
    assert max(primary) >= 180
    assert max(secondary) >= 180


def test_smart_auto_prioritizes_profile_banner_over_card_background() -> None:
    palette = renderer.resolve_card_palette(
        theme=renderer.BUILTIN_THEMES["cyber_neon"],
        color_mode="auto",
        profile_banner_bytes=_image_bytes((20, 230, 90), (170, 40, 245)),
        card_background_bytes=_image_bytes((245, 65, 35), (30, 100, 245)),
        avatar_bytes=_image_bytes((230, 180, 30), (30, 220, 220)),
    )
    assert palette.source == "profile-banner"


def test_card_mode_uses_uploaded_background_palette() -> None:
    palette = renderer.resolve_card_palette(
        theme=renderer.BUILTIN_THEMES["premium_gold"],
        color_mode="card",
        card_background_bytes=_image_bytes((25, 225, 95), (175, 45, 245)),
    )
    assert palette.source == "card-background"
    assert renderer._color_distance(palette.primary, palette.secondary) >= 60


def test_custom_mode_accepts_hex_and_repairs_near_duplicate_colors() -> None:
    palette = renderer.resolve_card_palette(
        theme=renderer.BUILTIN_THEMES["cyber_neon"],
        color_mode="custom",
        custom_primary="#22DCFF",
        custom_secondary="#22DDFF",
    )
    assert palette.source == "custom"
    assert renderer._color_distance(palette.primary, palette.secondary) >= 54


def test_invalid_hex_color_is_rejected() -> None:
    with pytest.raises(ValueError, match="six-digit hex"):
        renderer.normalize_hex_color("blue-ish")


def test_all_font_styles_render_large_production_cards() -> None:
    avatar = _image_bytes((30, 210, 105), (155, 40, 235))
    background = _image_bytes((8, 12, 22), (30, 8, 45))
    rendered_by_style: dict[str, bytes] = {}
    for style_key in renderer.FONT_STYLES:
        rendered = renderer.render_welcome_card(
            avatar_bytes=avatar,
            display_name="UglyGameFace",
            server_name="The 420 Lobby",
            member_count=73,
            theme_key="420_lobby",
            custom_background_bytes=background,
            font_style_key=style_key,
            color_mode="card",
        )
        with Image.open(BytesIO(rendered)) as image:
            assert image.size == (1200, 400)
            crop = image.convert("RGB").crop((410, 70, 1140, 250))
            bright_pixels = sum(1 for pixel in crop.getdata() if max(pixel) >= 180)
            assert bright_pixels > 5_500
        rendered_by_style[style_key] = rendered

    assert len({hash(value) for value in rendered_by_style.values()}) == len(renderer.FONT_STYLES)


def test_extreme_names_still_fit_with_new_larger_typography() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1200, 400), (0, 0, 0, 0)))
    style = renderer.FONT_STYLES["neon"]
    fitted, fitted_font = renderer._fit_text(
        draw,
        "W" * 64,
        max_width=710,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        bold=True,
        stroke_width=style.name_stroke,
        style_key="neon",
        role="name",
    )
    assert fitted.endswith("…")
    assert renderer._text_width(draw, fitted, font=fitted_font, stroke_width=style.name_stroke) <= 710
