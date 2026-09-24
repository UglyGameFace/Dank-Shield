from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from stoney_verify import exit_card_service
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


def test_exit_card_renderer_uses_bundled_long_tail_fallback_without_host_fonts(
    monkeypatch,
) -> None:
    fallback._registered_fallback_paths.cache_clear()
    monkeypatch.setattr(
        fallback,
        "_registered_fallback_paths",
        lambda _bold: (),
    )

    style = engine._render_style("neon", None)
    runs = fallback.resolve_font_runs(
        "ᗩ ᗰ ᒪ",
        primary_paths=engine._family_candidates(style.family, bold=True),
        bold=True,
        tracking=style.tracking,
    )

    assert runs
    assert "".join(run.text for run in runs) == "ᗩ ᗰ ᒪ"
    assert all(fallback.source_supports(run.source, run.text) for run in runs)
    assert any(
        str(run.source.path or "").endswith("NotoSansCanadianAboriginal-VF.ttf")
        for run in runs
    )

    rendered = render_exit_card(
        avatar_bytes=_avatar_png(),
        display_name="ᗩ ᗰ ᒪ",
        server_name="Cross Guild Unicode Test",
        member_count=124,
        theme_key="cyber_neon",
        font_style_key="neon",
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
        assert bright_pixels > 250


def test_two_guild_font_configs_use_same_unicode_capable_exit_renderer(
    monkeypatch,
) -> None:
    async def fake_avatar_bytes(_member) -> bytes:
        return _avatar_png()

    monkeypatch.setattr(exit_card_service, "_avatar_bytes", fake_avatar_bytes)

    async def render_for(guild_id: int, style_key: str) -> bytes:
        guild = SimpleNamespace(
            id=guild_id,
            name=f"Guild {guild_id}",
            member_count=42,
        )
        member = SimpleNamespace(
            id=9000 + guild_id,
            guild=guild,
            display_name="𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫",
        )
        cfg = {
            "exit_card_font_style": style_key,
            "exit_card_color_mode": "theme",
            "exit_card_shuffle_mode": "off",
        }
        return await exit_card_service.render_member_exit_card(member, cfg)

    first = asyncio.run(render_for(101, "neon"))
    second = asyncio.run(render_for(202, "blackletter"))

    for rendered in (first, second):
        with Image.open(BytesIO(rendered)) as image:
            assert image.size == (CARD_WIDTH, CARD_HEIGHT)
            name_area = image.convert("RGB").crop((410, 125, 1145, 255))
            bright_pixels = sum(
                1
                for pixel in name_area.getdata()
                if max(pixel) >= 150
            )
            assert bright_pixels > 250
