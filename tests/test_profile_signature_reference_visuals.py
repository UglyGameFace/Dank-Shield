from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

import stoney_verify.profile_signature_live_renderer as renderer


def _png(color: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _render(
    *,
    theme: str = "420_lobby",
    layout: str = "classic",
    frame: str = "glow",
    background: str = "theme",
    display_name: str = "UglyGameFace",
    color_mode: str = "theme",
) -> bytes:
    logo_dir = Path(renderer.PLATFORM_LOGO_DIR)
    return renderer.render_profile_signature(
        avatar_bytes=_png((55, 65, 75)),
        display_name=display_name,
        server_name="The 420 Lobby",
        date_labels=["Joined Jun 2026", "Discord since Oct 2019"],
        server_role_labels=["Lobby OG"],
        profile_tag_labels=[
            "Pronouns: no pronouns",
            "Identity: man",
            "Interests: smoke lounge / movies 🎬 / music 🎵",
        ],
        platform_entries=[
            {"platform": "steam", "username": "UglyGameFace", "mode": "username"},
            {"platform": "xbox", "username": "UglyGameFace", "mode": "username"},
            {"platform": "playstation", "username": "UglyGameFace", "mode": "username"},
            {"platform": "epic", "username": "UglyGameFace", "mode": "username"},
        ],
        platform_logo_bytes={
            key: (logo_dir / f"{key}.png").read_bytes()
            for key in ("steam", "xbox", "playstation", "epic")
        },
        guild_icon_bytes=_png((20, 80, 35)),
        emoji_assets={
            "🎬": _png((225, 65, 75), (72, 72)),
            "🎵": _png((65, 175, 225), (72, 72)),
        },
        style={
            "theme": theme,
            "font": "tech",
            "color_mode": color_mode,
            "custom_primary": "#8FFF52",
            "custom_secondary": "#2DE8CD",
            "custom_tertiary": "#B85BFF",
            "custom_highlight": "#FFCC52",
            "background_mode": background,
            "layout": layout,
            "avatar_frame": frame,
        },
    )


def test_reference_and_platform_theme_families_are_distinct_and_bright() -> None:
    expected = {
        "420_lobby",
        "cyber_neon",
        "premium_gold",
        "community_glow",
        "esports",
        "minimal_glass",
        "steam_focus",
        "xbox_focus",
        "playstation_focus",
        "epic_focus",
        "multi_platform",
    }
    assert expected == set(renderer.PROFILE_THEME_PALETTES)
    accents = {renderer.PROFILE_THEME_PALETTES[key].primary for key in expected}
    assert len(accents) == len(expected)
    for palette in renderer.PROFILE_THEME_PALETTES.values():
        assert len(set(palette.accents)) >= 3
        assert max(palette.primary) >= 170
        assert sum(palette.text) >= 700
        assert sum(palette.panel) < sum(palette.text) / 4


def test_all_theme_families_render_different_cards() -> None:
    hashes = {
        hashlib.sha256(_render(theme=theme)).hexdigest()
        for theme in renderer.PROFILE_THEME_PALETTES
    }
    assert len(hashes) == len(renderer.PROFILE_THEME_PALETTES)


def test_platform_focused_themes_declare_real_logo_focus() -> None:
    assert renderer.PROFILE_THEME_PALETTES["steam_focus"].focus_platform == "steam"
    assert renderer.PROFILE_THEME_PALETTES["xbox_focus"].focus_platform == "xbox"
    assert renderer.PROFILE_THEME_PALETTES["playstation_focus"].focus_platform == "playstation"
    assert renderer.PROFILE_THEME_PALETTES["epic_focus"].focus_platform == "epic"
    assert renderer.PROFILE_THEME_PALETTES["multi_platform"].focus_platform == ""


def test_layout_avatar_frame_and_four_colors_change_real_geometry() -> None:
    variants = {
        hashlib.sha256(_render(layout="classic", frame="glow")).hexdigest(),
        hashlib.sha256(_render(layout="minimal", frame="ring")).hexdigest(),
        hashlib.sha256(_render(layout="spotlight", frame="none")).hexdigest(),
        hashlib.sha256(_render(color_mode="custom")).hexdigest(),
    }
    assert len(variants) == 4
    assert renderer._LAYOUTS["classic"] != renderer._LAYOUTS["minimal"]
    assert renderer._LAYOUTS["classic"] != renderer._LAYOUTS["spotlight"]


def test_rendered_card_stays_compact_and_long_name_is_clipped_to_safe_zone() -> None:
    payload = _render(display_name="UGLYGAMEFACE-" * 20)
    with Image.open(BytesIO(payload)) as image:
        assert image.size == (1400, 300)
        assert image.getpixel((22, 150)) != image.getpixel((700, 150))
        assert image.getpixel((1170, 150)) != image.getpixel((1210, 150))
    spec = renderer._LAYOUTS["classic"]
    tile = renderer._bounded_name_tile(
        "UGLYGAMEFACE-" * 20,
        style_key="tech",
        spec=spec,
        palette=renderer.PROFILE_THEME_PALETTES["420_lobby"],
        custom_font_bytes=b"",
    )
    assert tile.width <= spec.content_right - spec.content_x - 24
    assert tile.height == 76


def test_emoji_parser_keeps_regular_joined_skin_tone_keycap_and_flag_sequences() -> None:
    tokens = renderer._emoji_tokens("🎬 👨‍👩‍👧‍👦 👍🏽 1️⃣ 🇺🇸")
    emoji = [value for value, is_emoji in tokens if is_emoji]
    assert emoji == ["🎬", "👨‍👩‍👧‍👦", "👍🏽", "1️⃣", "🇺🇸"]


def test_rich_text_uses_image_assets_instead_of_font_tofu() -> None:
    image = Image.new("RGBA", (260, 60), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    font = renderer._font(20, style_key="clean", regular=True)
    emoji_payload = _png((255, 50, 60), (72, 72))
    width = renderer._draw_rich(
        image,
        draw,
        (4, 8),
        "Movies 🎬",
        font=font,
        fill=(255, 255, 255, 255),
        assets={"🎬": emoji_payload},
        emoji_size=24,
    )
    assert width > 70
    assert image.getpixel((80, 20))[:3] != (0, 0, 0)


def test_copy_callback_returns_only_raw_username_and_guard_is_removed() -> None:
    source = Path("stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
    startup = Path("stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
    assert "await _send_private(interaction, content=username)" in source
    assert "```text" not in source
    assert "profile_username_copy_guard" not in startup
    assert not Path("stoney_verify/startup_guards/profile_username_copy_guard.py").exists()
