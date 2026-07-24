from pathlib import Path


COMMANDS = Path("stoney_verify/commands_ext/welcome_card_style_commands.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/welcome_card_studio_renderer.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")


def test_font_and_color_commands_open_visual_pickers() -> None:
    assert "DankPickerView" in COMMANDS
    assert "render_font_catalog" in COMMANDS
    assert "render_color_catalog" in COMMANDS
    assert "Ready-Made Palettes" in COMMANDS
    assert "Custom Color Picker" in COMMANDS


def test_hex_is_advanced_fallback_not_required_slash_input() -> None:
    assert "class _AdvancedHexModal" in COMMANDS
    assert "Advanced Hex Fallback" in COMMANDS
    assert "primary: Optional[str]" not in COMMANDS
    assert "secondary: Optional[str]" not in COMMANDS
    assert "@app_commands.describe" not in COMMANDS


def test_live_service_uses_proportional_typography_engine_explicitly() -> None:
    assert "from .welcome_card_typography_engine import (" in SERVICE
    assert "render_welcome_card" in SERVICE
    assert "custom_font_bytes=custom_font" in SERVICE


def test_font_styles_do_not_depend_only_on_host_font_files() -> None:
    for effect in ("neon", "tech", "impact", "chrome", "outline", "arcade", "street", "future", "soft"):
        assert f'effect="{effect}"' in STUDIO
    assert "_apply_mask_transform" in STUDIO
    assert "ImageFont.load_default(size=" in STUDIO


def test_avatar_geometry_stays_on_the_approved_renderer_primitive() -> None:
    assert "legacy._avatar_layer(" in STUDIO
    assert "Preserve the exact avatar geometry approved in production." in STUDIO
