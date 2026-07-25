from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from stoney_verify import welcome_card_typography_engine as studio


def _image_bytes(left: tuple[int, int, int], right: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (1200, 400), left)
    ImageDraw.Draw(image).rectangle((600, 0, 1199, 399), fill=right)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _ink(mask: Image.Image) -> int:
    return sum(int(value) for value in mask.getdata())


def _visible_bbox(image: Image.Image, threshold: int = 4):
    alpha = image.getchannel("A")
    visible = alpha.point(lambda value: 255 if value >= threshold else 0)
    return visible.getbbox()


def test_all_font_styles_are_distinct_without_host_fonts(monkeypatch) -> None:
    monkeypatch.setattr(studio.Path, "is_file", lambda _path: False)
    avatar = _image_bytes((30, 210, 105), (155, 40, 235))
    background = _image_bytes((8, 12, 22), (30, 8, 45))
    rendered: dict[str, bytes] = {}

    assert len(studio.FONT_STYLES) >= 16
    for style_key in studio.FONT_STYLES:
        card = studio.render_welcome_card(
            avatar_bytes=avatar,
            display_name="UglyGameFace",
            server_name="The 420 Lobby",
            member_count=73,
            theme_key="420_lobby",
            custom_background_bytes=background,
            font_style_key=style_key,
            color_mode="card",
        )
        with Image.open(BytesIO(card)) as image:
            assert image.size == (1200, 400)
        rendered[style_key] = card

    assert len({hash(value) for value in rendered.values()}) == len(studio.FONT_STYLES)


def test_every_final_styled_tile_fits_both_axes() -> None:
    names = (
        "UglyGameFace",
        "WavyLowercase",
        "M" * 28,
        "iiiiiiilllll",
        "W" * 64,
    )
    for style in studio.FONT_STYLES.values():
        for name in names:
            _fitted, tile = studio._fitted_tile(
                name,
                style=style,
                start_size=style.name_start_size,
                min_size=style.name_min_size,
                max_width=studio.NAME_SAFE_WIDTH,
                max_height=studio.NAME_SAFE_HEIGHT,
                role="name",
                primary=(34, 220, 255),
                secondary=(188, 66, 255),
            )
            assert tile.width <= studio.NAME_SAFE_WIDTH, (style.key, name, tile.size)
            assert tile.height <= studio.NAME_SAFE_HEIGHT, (style.key, name, tile.size)
            assert _visible_bbox(tile) is not None


def test_positive_shear_preserves_the_complete_leading_glyph() -> None:
    # This is the exact production regression that changed UglyGameFace into
    # JglyGameFace. A valid slant may redistribute antialiasing, but it must not
    # discard a meaningful portion of the first U.
    for key in ("street", "soft"):
        style = studio.FONT_STYLES[key]
        font = studio._font(style.name_start_size, style=style, bold=True)
        original = studio._tracked_mask("U", font=font, tracking=0)
        transformed = studio._transform_mask(original, style)
        assert _ink(original) > 0
        assert _ink(transformed) / _ink(original) >= 0.95, (
            key,
            original.size,
            transformed.size,
        )


def test_alpha_crop_ignores_invisible_blur_tail_pixels() -> None:
    image = Image.new("RGBA", (240, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((70, 35, 150, 85), fill=(255, 255, 255, 255))
    image.putpixel((230, 110), (255, 255, 255, 1))

    cropped = studio._crop_alpha(image)
    assert cropped.width < 120
    assert cropped.height < 90
    assert _visible_bbox(cropped) is not None


def test_problem_styles_keep_visible_pixels_inside_a_safety_margin() -> None:
    for key in ("outline", "street", "soft", "retro", "bold", "neon", "prism"):
        style = studio.FONT_STYLES[key]
        _text, tile = studio._fitted_tile(
            "UglyGameFace",
            style=style,
            start_size=style.name_start_size,
            min_size=style.name_min_size,
            max_width=680,
            max_height=68,
            role="name",
            primary=(34, 220, 255),
            secondary=(188, 66, 255),
        )
        assert tile.width <= 680
        assert tile.height <= 68
        box = _visible_bbox(tile)
        assert box is not None
        left, top, right, bottom = box
        assert left >= 1 and top >= 1, (key, tile.size, box)
        assert right <= tile.width - 1 and bottom <= tile.height - 1, (key, tile.size, box)


def test_renderer_owned_icons_are_vector_drawn_not_unicode_tofu() -> None:
    source = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
    assert "  •  " not in source
    assert "_draw_sparkle" in source
    assert "_draw_member_icon" in source
    assert 'suffix = "..."' in source


def test_visual_font_and_color_catalogs_render() -> None:
    font_catalog = studio.render_font_catalog(display_name="UglyGameFace")
    palette_catalog = studio.render_color_catalog(swatches=False)
    swatch_catalog = studio.render_color_catalog(swatches=True)

    with Image.open(BytesIO(font_catalog)) as image:
        assert image.width >= 1000
        assert image.height >= 1700
    with Image.open(BytesIO(palette_catalog)) as image:
        assert image.width >= 1000
        assert image.height >= 500
    with Image.open(BytesIO(swatch_catalog)) as image:
        assert image.width >= 1000
        assert image.height >= 500


def test_visual_picker_has_enough_human_named_choices() -> None:
    assert len(studio.COLOR_PRESETS) >= 16
    assert len(studio.COLOR_SWATCHES) >= 20
    assert all(preset.label and preset.description for preset in studio.COLOR_PRESETS.values())
    assert all(swatch.label and swatch.hex_value.startswith("#") for swatch in studio.COLOR_SWATCHES.values())
