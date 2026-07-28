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


def test_old_studio_theme_names_route_to_approved_visual_families() -> None:
    expected = {
        "default": "420_lobby",
        "forest": "420_lobby",
        "purple": "cyber_neon",
        "galaxy": "cyber_neon",
        "dark": "premium_gold",
        "minimal": "community_glow",
        "sunset": "esports",
        "ocean": "minimal_glass",
    }
    for old_name, family in expected.items():
        assert renderer_module.THEME_ALIASES[old_name] == family
        assert renderer_module._canonical_theme({"theme": old_name}) == family


def test_avatar_matching_cannot_replace_selected_theme_family(monkeypatch) -> None:
    monkeypatch.setattr(
        renderer_module._legacy,
        "_avatar_colors",
        lambda _payload, _fallback: ((255, 75, 20), (255, 0, 140)),
    )
    green = renderer_module._palette({"theme": "forest", "color_mode": "profile"}, b"avatar")
    assert green.primary[1] > green.primary[0]
    assert green.secondary[1] > green.secondary[0]

    blue = renderer_module._palette({"theme": "ocean", "color_mode": "profile"}, b"avatar")
    assert blue.primary[2] > blue.primary[0]
    assert blue.secondary[2] > blue.secondary[0]


def test_live_banner_uses_reference_layout_bundled_logos_and_integrated_branding() -> None:
    assert "SIGNATURE_WIDTH = 1400" in RENDERER
    assert "SIGNATURE_HEIGHT = 300" in RENDERER
    assert "PLATFORM_LOGO_DIR" in RENDERER
    assert "_LOGOS" in RENDERER
    assert "def _logo_bytes" in RENDERER
    assert "platform_entries" in RENDERER
    assert "guild_icon_bytes" in RENDERER
    assert "def _draw_brand" in RENDERER
    assert "def _draw_card_frame" in RENDERER
    assert "THEME_ALIASES" in RENDERER
    assert "draw.polygon" in RENDERER
    assert "cdn.discordapp.com" not in RENDERER


def test_banner_rich_text_fitter_respects_reserved_pixel_widths() -> None:
    image = Image.new("RGBA", (1400, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = renderer_module._font(20, style_key="clean", regular=True)
    for max_width in (126, 286, 526):
        fitted = renderer_module._fit(
            draw,
            "WIDE SERVER ROLE AND PLATFORM USERNAME " * 20,
            font,
            max_width,
            {},
            20,
        )
        assert fitted.endswith("…")
        assert renderer_module._rich_width(draw, fitted, font, {}, 20) <= max_width


def test_banner_applies_fitting_and_reserved_zones_to_dynamic_labels() -> None:
    assert "spec.content_right - spec.content_x" in RENDERER
    assert "role_width = min(" in RENDERER
    assert "available = spec.content_right - x" in RENDERER
    assert "def _draw_platforms" in RENDERER
    assert "def _draw_pills" in RENDERER
    assert "def _draw_brand" in RENDERER
    assert "def _draw_meta_dates" in RENDERER


def test_member_profile_view_attaches_generated_wide_banner() -> None:
    profile_send = PRIVACY_CORE.split("async def send_privacy_aware_profile", 1)[1].split(
        "def _live_status_embed", 1
    )[0]
    assert "file=rendered.file if rendered is not None else None" in profile_send
    assert "render_live_profile_card(" in profile_send
