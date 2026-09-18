from __future__ import annotations

from io import BytesIO

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image, ImageDraw

from stoney_verify import unicode_font_fallback as fallback
from stoney_verify import welcome_card_typography_engine as engine


def _build_font_bytes(characters: str, family: str) -> bytes:
    unique = list(dict.fromkeys(characters))
    glyph_order = [".notdef"] + [f"g{ord(character):06X}" for character in unique]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(
        {ord(character): f"g{ord(character):06X}" for character in unique}
    )

    glyphs = {}
    metrics = {}
    for glyph_name in glyph_order:
        pen = TTGlyphPen(None)
        if glyph_name != ".notdef":
            pen.moveTo((90, 0))
            pen.lineTo((510, 0))
            pen.lineTo((510, 700))
            pen.lineTo((90, 700))
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
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": family.replace(" ", "") + "-Regular",
            "fullName": family + " Regular",
            "psName": family.replace(" ", "") + "-Regular",
        }
    )
    builder.setupPost()
    builder.setupMaxp()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def _write_font(tmp_path, name: str, characters: str):
    path = tmp_path / name
    path.write_bytes(_build_font_bytes(characters, name))
    return path


def _avatar_bytes() -> bytes:
    image = Image.new("RGB", (256, 256), (60, 100, 150))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_primary_font_falls_back_only_for_missing_grapheme(tmp_path, monkeypatch) -> None:
    primary = _write_font(tmp_path, "primary.ttf", "AB")
    unicode_face = _write_font(tmp_path, "unicode.ttf", "Λ♡")
    monkeypatch.setattr(
        fallback,
        "_registered_fallback_paths",
        lambda _bold: (str(unicode_face),),
    )

    runs = fallback.resolve_font_runs(
        "AΛB♡",
        primary_paths=(str(primary),),
        bold=True,
    )

    assert "".join(run.text for run in runs) == "AΛB♡"
    assert [run.text for run in runs] == ["A", "Λ", "B", "♡"]
    assert runs[0].source.path == str(primary)
    assert runs[1].source.path == str(unicode_face)
    assert runs[2].source.path == str(primary)
    assert runs[3].source.path == str(unicode_face)
    assert all(fallback.source_supports(run.source, run.text) for run in runs)


def test_partial_custom_font_uses_fallback_without_rewriting_text(tmp_path, monkeypatch) -> None:
    fallback_face = _write_font(tmp_path, "fallback.ttf", "𝓐♡")
    monkeypatch.setattr(
        fallback,
        "_registered_fallback_paths",
        lambda _bold: (str(fallback_face),),
    )
    custom = _build_font_bytes("Angel", "Custom ASCII")

    runs = fallback.resolve_font_runs(
        "A𝓐ngel♡",
        primary_paths=(),
        bold=True,
        custom_font_bytes=custom,
    )

    assert "".join(run.text for run in runs) == "A𝓐ngel♡"
    assert any(run.source.key.startswith("custom:") for run in runs)
    assert any(run.source.path == str(fallback_face) for run in runs)

    mask = fallback.render_text_mask(
        "A𝓐ngel♡",
        size=56,
        primary_paths=(),
        bold=True,
        custom_font_bytes=custom,
    )
    assert mask.getbbox() is not None


def test_complex_text_disables_manual_tracking() -> None:
    assert fallback.safe_tracking("TRACKED", 4) == 4
    assert fallback.safe_tracking("PΛMELA", 4) == 0
    assert fallback.safe_tracking("محمد", 4) == 0
    assert fallback.safe_tracking("👩🏽‍💻", 4) == 0


def test_dynamic_name_is_not_uppercased_by_visual_style() -> None:
    style = engine.FONT_STYLES["tech"]
    assert style.uppercase_name is True

    rendered, tile = engine._fitted_tile(
        "Mc𝓐ngel",
        style=style,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        max_width=engine.NAME_SAFE_WIDTH,
        max_height=engine.NAME_SAFE_HEIGHT,
        role="name",
        primary=(90, 255, 45),
        secondary=(174, 75, 255),
    )

    assert rendered == "Mc𝓐ngel"
    assert tile.getchannel("A").getbbox() is not None


def test_card_text_truncates_only_on_grapheme_boundaries() -> None:
    value = "A👩🏽‍💻B"
    assert engine._safe_card_text(value, fallback="Member", max_graphemes=3) == value
    assert engine._safe_card_text(value, fallback="Member", max_graphemes=2) == "A…"


def test_production_fallback_stack_covers_representative_discord_names() -> None:
    style = engine.FONT_STYLES["clean"]
    primary = engine._family_candidates(style.family, bold=True)
    samples = (
        "PΛMELA",
        "𝓐𝓷𝓰𝓮𝓵♡",
        "José",
        "玩家123",
        "محمد",
        "Женя",
        "👩🏽‍💻",
    )

    for sample in samples:
        runs = fallback.resolve_font_runs(
            sample,
            primary_paths=primary,
            bold=True,
        )
        assert runs, sample
        assert "".join(run.text for run in runs) == sample
        assert all(
            fallback.source_supports(run.source, run.text)
            for run in runs
        ), [(run.text, run.source.key) for run in runs]


def test_representative_unicode_names_render_full_welcome_cards() -> None:
    for sample in (
        "PΛMELA",
        "𝓐𝓷𝓰𝓮𝓵♡",
        "José",
        "玩家123",
        "محمد",
        "Женя",
        "👩🏽‍💻",
    ):
        rendered = engine.render_welcome_card(
            avatar_bytes=_avatar_bytes(),
            display_name=sample,
            server_name="The 420 Lobby 世界",
            member_count=107,
            theme_key="420_lobby",
            font_style_key="neon",
            color_mode="theme",
        )
        with Image.open(BytesIO(rendered)) as image:
            assert image.size == (1200, 400)
            assert image.format == "PNG"
            name_area = image.convert("RGB").crop((410, 130, 1145, 250))
            bright = sum(1 for pixel in name_area.getdata() if max(pixel) >= 150)
            assert bright > 250, sample
