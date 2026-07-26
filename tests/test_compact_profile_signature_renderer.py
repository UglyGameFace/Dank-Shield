from io import BytesIO
from pathlib import Path

from PIL import Image

from stoney_verify.profile_signature_renderer import (
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    render_profile_signature,
)


def test_compact_signature_is_a_horizontal_png_with_bounded_height():
    rendered = render_profile_signature(
        avatar_bytes=b"",
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        role_labels=["Identity: man", "Interests: gaming • music • movies"],
        date_labels=["Joined Jun 2026", "Discord since Oct 2019"],
        platform_labels=["Steam: UglyGameFace", "Xbox: UglyGameFace"],
        cfg={"welcome_card_theme": "420_lobby"},
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.format == "PNG"
        assert image.size == (SIGNATURE_WIDTH, SIGNATURE_HEIGHT)
        assert image.width / image.height >= 4.5
        assert image.height <= 240


def test_live_runtime_uses_attachment_image_not_full_profile_field_dump():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
    assert "render_member_profile_signature" in runtime
    assert "attachment://" in runtime
    assert "_profile_card(member)" not in runtime
    assert "embed.add_field" not in runtime
