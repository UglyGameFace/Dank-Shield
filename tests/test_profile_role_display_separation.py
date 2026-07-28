from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

import stoney_verify.profile_signature_live_renderer as renderer_module

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "stoney_verify/profile_card_service.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
STUDIO = (ROOT / "stoney_verify/profile_signature_studio.py").read_text(encoding="utf-8")
PRIVACY = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
PRIVACY_CORE = (ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
COSMETICS = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")
SETUP = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
SETUP_CORE = (ROOT / "stoney_verify/profile_card_setup_ui_core.py").read_text(encoding="utf-8")
RENDERER = (ROOT / "stoney_verify/profile_signature_live_renderer.py").read_text(encoding="utf-8")


def test_server_roles_and_profile_tags_have_separate_privacy_keys() -> None:
    assert '"show_server_roles": False' in SERVICE
    assert '"show_profile_tags": True' in SERVICE
    assert '"server_roles", "profile_tags"' in SERVICE
    assert 'result["show_profile_tags"] = bool(raw.get("show_roles"))' in SERVICE


def test_runtime_builds_server_roles_and_profile_tags_independently() -> None:
    assert "def _compact_server_role_labels" in RUNTIME
    assert "def _compact_profile_tag_labels" in RUNTIME
    assert 'preferences.get("show_server_roles", False)' in RUNTIME
    assert 'preferences.get("show_profile_tags", True)' in RUNTIME
    assert "server_role_labels=server_role_labels" in RUNTIME
    assert "profile_tag_labels=profile_tag_labels" in RUNTIME


def test_member_studio_does_not_send_server_roles_into_cosmetic_editor() -> None:
    assert 'label="Server Roles"' in STUDIO
    assert 'label="Profile Tags"' in STUDIO
    assert "await open_server_role_display(interaction)" in STUDIO
    profile_tags_block = STUDIO.split('label="Profile Tags"', 1)[1].split('label="Preview"', 1)[0]
    assert "ProfileEditView" in profile_tags_block
    server_roles_block = STUDIO.split('label="Server Roles"', 1)[1].split('label="Profile Tags"', 1)[0]
    assert "ProfileEditView" not in server_roles_block
    assert "does **not** open or edit pronouns, identity, interests, or cosmetic tags" in STUDIO


def test_privacy_and_server_setup_name_both_categories_clearly() -> None:
    for source in (PRIVACY, PRIVACY_CORE):
        assert "Server Roles" in source
        assert "Profile Tags" in source
    assert '"server_roles": "Server roles"' in SETUP_CORE
    assert '"profile_tags": "Profile tags"' in SETUP_CORE
    assert 'label="Profile Tags & Cosmetics"' in SETUP


def test_cosmetic_editor_is_renamed_profile_tags_not_generic_roles() -> None:
    assert "Profile Tags & Cosmetics" in COSMETICS
    assert "View Full Profile Tags" in COSMETICS
    assert "Clear Profile Tags" in COSMETICS
    assert "Profile Roles / Cosmetics" not in COSMETICS


def test_live_banner_uses_wide_layout_bundled_logos_and_server_branding() -> None:
    assert "SIGNATURE_WIDTH = 1400" in RENDERER
    assert "SIGNATURE_HEIGHT = 340" in RENDERER
    assert "PLATFORM_LOGO_DIR" in RENDERER
    assert "_PLATFORM_LOGO_BYTES_CACHE" in RENDERER
    assert "_bundled_platform_logo_bytes" in RENDERER
    assert "platform_entries" in RENDERER
    assert "guild_icon_bytes" in RENDERER
    assert "cdn.discordapp.com" not in RENDERER

def test_banner_text_fitter_respects_reserved_pixel_widths() -> None:
    image = Image.new("RGBA", (1400, 340), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = renderer_module._font(20, style_key="clean", regular=True)
    for max_width in (126, 335, 884):
        fitted = renderer_module._fit_text_width(
            draw,
            "WIDE SERVER ROLE AND PLATFORM USERNAME " * 20,
            font,
            max_width=max_width,
        )
        assert fitted.endswith("…")
        box = draw.textbbox((0, 0), fitted, font=font)
        assert box[2] - box[0] <= max_width


def test_banner_applies_pixel_fitting_to_every_dynamic_right_side_label() -> None:
    assert "badge_max_width = 335" in RENDERER
    assert 'max_width=335' in RENDERER
    assert 'max_width=server_size' in RENDERER
    assert "available_text_width = max(0, max_chip_x - chip_x - 32)" in RENDERER


def test_member_profile_view_attaches_generated_wide_banner() -> None:
    profile_send = PRIVACY_CORE.split("async def send_privacy_aware_profile", 1)[1].split(
        "def _live_status_embed", 1
    )[0]
    assert "file=rendered.file if rendered is not None else None" in profile_send
    assert "render_live_profile_card(" in profile_send
