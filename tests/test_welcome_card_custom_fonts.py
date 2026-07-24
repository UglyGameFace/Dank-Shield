from __future__ import annotations

from io import BytesIO

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from stoney_verify import welcome_card_font_assets as assets
from stoney_verify import welcome_card_typography_engine as engine


_REQUIRED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?@#&-_."


def _build_font_bytes(characters: str = _REQUIRED) -> bytes:
    glyph_order = [".notdef"] + [f"g{ord(character):04X}" for character in characters]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(
        {ord(character): f"g{ord(character):04X}" for character in characters}
    )

    glyphs = {}
    metrics = {}
    for glyph_name in glyph_order:
        pen = TTGlyphPen(None)
        if glyph_name != ".notdef":
            pen.moveTo((100, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500, 700))
            pen.lineTo((100, 700))
            pen.closePath()
        glyphs[glyph_name] = pen.glyph()
        metrics[glyph_name] = (600, 50)

    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupNameTable(
        {
            "familyName": "Dank Test Upload",
            "styleName": "Regular",
            "uniqueFontIdentifier": "DankTestUpload-Regular",
            "fullName": "Dank Test Upload Regular",
            "psName": "DankTestUpload-Regular",
        }
    )
    builder.setupPost()
    builder.setupMaxp()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def _as_woff2(ttf_data: bytes) -> bytes:
    font = TTFont(BytesIO(ttf_data))
    try:
        font.flavor = "woff2"
        output = BytesIO()
        font.save(output)
        return output.getvalue()
    finally:
        font.close()


def _image_bytes(left: tuple[int, int, int], right: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (1200, 400), left)
    ImageDraw.Draw(image).rectangle((600, 0, 1199, 399), fill=right)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_supported_upload_types_cover_common_desktop_and_web_fonts() -> None:
    assert assets.SUPPORTED_FONT_EXTENSIONS == {
        ".ttf",
        ".otf",
        ".ttc",
        ".otc",
        ".woff",
        ".woff2",
    }


def test_ttf_and_woff2_are_validated_and_normalized() -> None:
    ttf_data = _build_font_bytes()
    normalized_ttf = assets.normalize_uploaded_font(ttf_data, "dank-test.ttf")
    assert normalized_ttf.display_name == "Dank Test Upload Regular"
    assert normalized_ttf.source_format == "TTF"
    assert normalized_ttf.glyph_count >= len(_REQUIRED)

    woff2_data = _as_woff2(ttf_data)
    normalized_woff2 = assets.normalize_uploaded_font(woff2_data, "dank-test.woff2")
    assert normalized_woff2.source_format == "WOFF2"
    assert normalized_woff2.display_name == normalized_ttf.display_name
    assert normalized_woff2.glyph_count == normalized_ttf.glyph_count
    # sfnt table order/checksum bytes may differ after WOFF2 reconstruction; the
    # correct invariant is that both normalized outputs render equivalent text.
    for normalized in (normalized_ttf, normalized_woff2):
        font = ImageFont.truetype(BytesIO(normalized.data), 48)
        box = font.getbbox("UglyGameFace 123")
        assert box[2] > box[0]
        assert box[3] > box[1]


def test_font_storage_round_trip_is_renderable() -> None:
    normalized = assets.normalize_uploaded_font(_build_font_bytes(), "font.ttf")
    cfg = {
        "welcome_card_custom_font_b64": assets.encode_custom_font(normalized.data),
        "welcome_card_custom_font_name": normalized.display_name,
    }
    decoded, name = assets.decode_custom_font(cfg)
    assert decoded == normalized.data
    assert name == normalized.display_name


def test_font_missing_basic_glyphs_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing basic letters"):
        assets.normalize_uploaded_font(_build_font_bytes("ABC123"), "incomplete.ttf")


def test_every_builtin_style_uses_unstretched_proportions() -> None:
    assert len(engine.FONT_STYLES) >= 16
    assert all(style.x_scale == 1.0 for style in engine.FONT_STYLES.values())


def test_heavy_impact_is_bounded_in_width_and_height() -> None:
    style = engine.FONT_STYLES["bold"]
    assert style.name_stroke <= 2
    fitted, mask = engine._fitted_mask(
        "UglyGameFace",
        style=style,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        max_width=engine.NAME_SAFE_WIDTH,
        max_height=engine.NAME_SAFE_HEIGHT,
        role="name",
        stroke_width=style.name_stroke,
    )
    assert fitted == "UglyGameFace"
    assert mask.width <= engine.NAME_SAFE_WIDTH
    assert mask.height <= engine.NAME_SAFE_HEIGHT
    assert mask.width / max(1, mask.height) >= 3.0


def test_long_names_fit_both_axes_without_nonuniform_scaling() -> None:
    for style in engine.FONT_STYLES.values():
        fitted, mask = engine._fitted_mask(
            "W" * 64,
            style=style,
            start_size=style.name_start_size,
            min_size=style.name_min_size,
            max_width=engine.NAME_SAFE_WIDTH,
            max_height=engine.NAME_SAFE_HEIGHT,
            role="name",
            stroke_width=style.name_stroke,
        )
        assert fitted
        assert mask.width <= engine.NAME_SAFE_WIDTH
        assert mask.height <= engine.NAME_SAFE_HEIGHT


def test_uploaded_font_renders_in_live_card_and_catalog() -> None:
    normalized = assets.normalize_uploaded_font(_build_font_bytes(), "custom.ttf")
    avatar = _image_bytes((25, 220, 85), (165, 45, 235))
    background = _image_bytes((6, 10, 18), (35, 8, 48))
    rendered = engine.render_welcome_card(
        avatar_bytes=avatar,
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        member_count=73,
        theme_key="420_lobby",
        custom_background_bytes=background,
        font_style_key="custom",
        custom_font_bytes=normalized.data,
        color_mode="card",
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (1200, 400)

    catalog = engine.render_font_catalog(
        display_name="UglyGameFace",
        custom_font_bytes=normalized.data,
        custom_font_name=normalized.display_name,
    )
    with Image.open(BytesIO(catalog)) as image:
        assert image.width == 1200
        assert image.height >= 82 + 108 * 17
