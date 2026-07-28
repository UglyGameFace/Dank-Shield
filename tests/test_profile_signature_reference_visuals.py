from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

import stoney_verify.profile_signature_live_renderer as renderer
from stoney_verify.startup_guards.profile_username_copy_guard import plain_copy_content


def _png(color: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _render(*, theme: str = "420_lobby", layout: str = "classic", frame: str = "glow", background: str = "theme") -> bytes:
    logo_dir = Path(renderer.PLATFORM_LOGO_DIR)
    return renderer.render_profile_signature(
        avatar_bytes=_png((55, 65, 75)),
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        date_labels=["Joined Jun 2026", "Discord since Oct 2019"],
        server_role_labels=["Lobby OG"],
        profile_tag_labels=[
            "Pronouns: no pronouns",
            "Identity: man",
            "Interests: smoke lounge 🎬 movies",
        ],
        platform_entries=[
            {"platform": "steam", "username": "UglyGameFace", "mode": "username"},
            {"platform": "xbox", "username": "UglyGameFace", "mode": "username"},
        ],
        platform_logo_bytes={
            "steam": (logo_dir / "steam.png").read_bytes(),
            "xbox": (logo_dir / "xbox.png").read_bytes(),
        },
        guild_icon_bytes=_png((20, 80, 35)),
        emoji_assets={"🎬": _png((225, 65, 75), (72, 72))},
        style={
            "theme": theme,
            "font": "tech",
            "color_mode": "theme",
            "background_mode": background,
            "layout": layout,
            "avatar_frame": frame,
        },
    )


def test_reference_theme_families_are_distinct_and_bright() -> None:
    expected = {
        "420_lobby",
        "cyber_neon",
        "premium_gold",
        "community_glow",
        "esports",
        "minimal_glass",
    }
    assert expected == set(renderer.PROFILE_THEME_PALETTES)
    accents = {renderer.PROFILE_THEME_PALETTES[key].primary for key in expected}
    assert len(accents) == len(expected)
    for palette in renderer.PROFILE_THEME_PALETTES.values():
        assert max(palette.primary) >= 170
        assert sum(palette.text) >= 700
        assert sum(palette.panel) < sum(palette.text) / 4


def test_all_reference_themes_render_different_cards() -> None:
    hashes = {
        hashlib.sha256(_render(theme=theme)).hexdigest()
        for theme in renderer.PROFILE_THEME_PALETTES
    }
    assert len(hashes) == len(renderer.PROFILE_THEME_PALETTES)


def test_layout_and_avatar_frame_settings_change_real_geometry() -> None:
    variants = {
        hashlib.sha256(_render(layout="classic", frame="glow")).hexdigest(),
        hashlib.sha256(_render(layout="minimal", frame="ring")).hexdigest(),
        hashlib.sha256(_render(layout="spotlight", frame="none")).hexdigest(),
    }
    assert len(variants) == 3
    assert renderer._LAYOUTS["classic"] != renderer._LAYOUTS["minimal"]
    assert renderer._LAYOUTS["classic"] != renderer._LAYOUTS["spotlight"]


def test_rendered_card_stays_compact_and_has_visible_safe_zones() -> None:
    payload = _render()
    with Image.open(BytesIO(payload)) as image:
        assert image.size == (1400, 300)
        assert image.getpixel((22, 150)) != image.getpixel((700, 150))
        assert image.getpixel((1170, 150)) != image.getpixel((1210, 150))


def test_emoji_parser_keeps_regular_and_joined_emoji_as_one_token() -> None:
    tokens = renderer._emoji_tokens("movies 🎬 family 👨‍👩‍👧‍👦 done")
    emoji = [value for value, is_emoji in tokens if is_emoji]
    assert "🎬" in emoji
    assert "👨‍👩‍👧‍👦" in emoji


def test_rich_text_uses_image_asset_instead_of_tofu_box() -> None:
    image = Image.new("RGBA", (220, 60), (0, 0, 0, 255))
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
    assert image.getbbox() is not None


def test_copy_guard_removes_only_the_fenced_username_wrapper() -> None:
    assert plain_copy_content("```text\nUglyGameFace\n```") == "UglyGameFace"
    assert plain_copy_content("normal private message") == "normal private message"
    assert plain_copy_content(None) is None
