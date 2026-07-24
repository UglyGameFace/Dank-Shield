from pathlib import Path


COMMANDS = Path(
    "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")
ENGINE = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
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


def test_live_service_uses_canonical_typography_engine_explicitly() -> None:
    assert "from .welcome_card_typography_engine import (" in SERVICE
    assert "render_welcome_card" in SERVICE
    assert "custom_font_bytes=custom_font" in SERVICE


def test_final_effects_are_fitted_not_only_base_glyphs() -> None:
    assert "def _styled_tile(" in ENGINE
    assert "def _fitted_tile(" in ENGINE
    assert "_fits(tile" in ENGINE
    assert "_crop_alpha(tile)" in ENGINE
    assert "ImageFont.load_default(size=" in ENGINE


def test_avatar_geometry_stays_on_the_approved_renderer_primitive() -> None:
    assert "legacy._avatar_layer(" in ENGINE
    assert "canvas.alpha_composite(" in ENGINE
