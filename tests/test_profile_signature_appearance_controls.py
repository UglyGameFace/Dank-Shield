from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from stoney_verify.profile_signature_renderer import render_profile_signature
from stoney_verify.welcome_card_typography_engine import FONT_STYLES


def _png_bytes(left=(235, 30, 70), right=(20, 90, 240)) -> bytes:
    image = Image.new("RGB", (160, 160), left)
    for x in range(80, 160):
        for y in range(160):
            image.putpixel((x, y), right)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


AVATAR = _png_bytes()
CUSTOM_BACKGROUND = _png_bytes((240, 170, 20), (15, 190, 130))


def _render(**updates) -> bytes:
    style = {
        "theme": "420_lobby",
        "font": "clean",
        "color_mode": "theme",
        "background_mode": "theme",
        "layout": "classic",
        "avatar_frame": "glow",
    }
    style.update(updates)
    return render_profile_signature(
        avatar_bytes=AVATAR,
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        role_labels=["Pronouns: he/him", "Interests: gaming • music"],
        date_labels=["Joined Jun 2026"],
        platform_labels=["Xbox: UglyGameFace"],
        style=style,
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_every_advertised_font_style_changes_compact_output():
    rendered = {_digest(_render(font=key)) for key in FONT_STYLES}
    assert len(rendered) == len(FONT_STYLES)


def test_layout_controls_are_visibly_distinct():
    rendered = {_digest(_render(layout=key)) for key in ("classic", "minimal", "spotlight")}
    assert len(rendered) == 3


def test_avatar_frame_controls_are_visibly_distinct():
    rendered = {_digest(_render(avatar_frame=key)) for key in ("glow", "ring", "none")}
    assert len(rendered) == 3


def test_background_controls_are_visibly_distinct():
    rendered = {
        _digest(_render(background_mode="theme")),
        _digest(_render(background_mode="profile")),
        _digest(_render(background_mode="custom", custom_background=CUSTOM_BACKGROUND)),
    }
    assert len(rendered) == 3


def test_color_controls_are_visibly_distinct():
    rendered = {
        _digest(_render(color_mode="theme")),
        _digest(_render(color_mode="profile")),
        _digest(
            _render(
                color_mode="custom",
                custom_primary="#FF5A36",
                custom_secondary="#22DCFF",
            )
        ),
    }
    assert len(rendered) == 3
