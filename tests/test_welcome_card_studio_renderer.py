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
            assert tile.getchannel("A").getbbox() is not None


def test_problem_styles_preserve_alpha_margin_and_counters() -> None:
    for key in ("outline", "street", "retro", "bold"):
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
        assert tile.getchannel("A").getbbox() == (0, 0, tile.width, tile.height)


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
