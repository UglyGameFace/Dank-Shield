from __future__ import annotations

from io import BytesIO

from PIL import Image

from stoney_verify import unicode_font_fallback as fallback
from stoney_verify import welcome_card_typography_engine as engine
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


def test_exit_card_renderer_covers_known_live_unicode_names_across_font_styles() -> None:
    samples = (
        "ᗩ ᗰ ᒪ",
        "PΛMELA",
        "𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫",
    )
    style_keys = ("neon", "street", "blackletter", "clean")

    for style_key in style_keys:
        style = engine._render_style(style_key, None)
        primary_paths = engine._family_candidates(style.family, bold=True)

        for sample in samples:
            runs = fallback.resolve_font_runs(
                sample,
                primary_paths=primary_paths,
                bold=True,
                tracking=style.tracking,
            )

            assert runs, (style_key, sample)
            assert "".join(run.text for run in runs) == sample
            assert all(
                fallback.source_supports(run.source, run.text)
                for run in runs
            ), (
                style_key,
                sample,
                [(run.text, run.source.key) for run in runs],
            )

            rendered = render_exit_card(
                avatar_bytes=_avatar_png(),
                display_name=sample,
                server_name=f"Unicode Test Guild {style_key}",
                member_count=124,
                theme_key="cyber_neon",
                font_style_key=style_key,
                color_mode="theme",
            )

            with Image.open(BytesIO(rendered)) as image:
                assert image.size == (CARD_WIDTH, CARD_HEIGHT)
                name_area = image.convert("RGB").crop((410, 125, 1145, 255))
                bright_pixels = sum(
                    1
                    for pixel in name_area.getdata()
                    if max(pixel) >= 150
                )
                assert bright_pixels > 250, (style_key, sample, bright_pixels)
