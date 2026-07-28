from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from stoney_verify.profile_signature_renderer import (
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    _draw_theme_motif,
    render_profile_signature,
)
from stoney_verify.profile_signature_style import (
    DEFAULT_SERVER_PROFILE_STYLE,
    SERVER_STYLE_CONFIG_KEYS,
    theme_style_updates,
)
from stoney_verify.welcome_card_typography_engine import BUILTIN_THEMES, DEFAULT_THEME_KEY


def _render(theme: str) -> bytes:
    return render_profile_signature(
        avatar_bytes=b"",
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        role_labels=["Interests: gaming • music"],
        date_labels=["Discord since Oct 2019"],
        platform_labels=["Xbox: UglyGameFace"],
        style={
            "theme": theme,
            "font": "clean",
            "color_mode": "theme",
            "background_mode": "theme",
            "layout": "classic",
            "avatar_frame": "glow",
        },
    )


def test_default_profile_theme_is_a_real_builtin_theme() -> None:
    assert DEFAULT_SERVER_PROFILE_STYLE["theme"] == DEFAULT_THEME_KEY
    assert DEFAULT_SERVER_PROFILE_STYLE["theme"] in BUILTIN_THEMES


def test_member_theme_selection_preserves_independent_colors_and_background() -> None:
    assert theme_style_updates("420_lobby", member=True) == {
        "signature_theme": "420_lobby",
    }
    assert theme_style_updates("server", member=True) == {
        "signature_theme": "server",
    }


def test_server_theme_selection_preserves_independent_colors_and_background() -> None:
    assert theme_style_updates("cyber_neon", member=False) == {
        SERVER_STYLE_CONFIG_KEYS["theme"]: "cyber_neon",
    }


def test_420_lobby_theme_renders_its_lime_and_purple_accents() -> None:
    with Image.open(BytesIO(_render("420_lobby"))) as image:
        pixels = set(image.convert("RGB").getdata())
    assert BUILTIN_THEMES["420_lobby"].primary in pixels
    assert BUILTIN_THEMES["420_lobby"].secondary in pixels


def test_each_builtin_theme_motif_has_a_distinct_compact_treatment() -> None:
    rendered: list[bytes] = []
    for theme in BUILTIN_THEMES.values():
        image = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        _draw_theme_motif(
            draw,
            motif=theme.motif,
            primary=(90, 255, 45),
            secondary=(174, 75, 255),
            layout="classic",
        )
        rendered.append(image.tobytes())
    assert len(set(rendered)) == len(BUILTIN_THEMES)
