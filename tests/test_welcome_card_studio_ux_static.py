from pathlib import Path


COMMANDS = Path(
    "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/welcome_card_studio_ui.py").read_text(encoding="utf-8")
ENGINE = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")


def test_font_and_color_controls_open_owned_visual_pickers() -> None:
    assert "DankPickerView" in COMMANDS
    assert "Welcome Card Fonts" in COMMANDS
    assert "Welcome Card Colors" in COMMANDS
    assert "Ready-Made Palettes" in COMMANDS
    assert "AdvancedWelcomeColorsModal" in COMMANDS
    assert 'label="Font"' in STUDIO
    assert 'label="Colors"' in STUDIO


def test_hex_is_an_advanced_modal_not_required_slash_input() -> None:
    assert "class AdvancedWelcomeColorsModal" in COMMANDS
    assert "Advanced Hex Colors" in COMMANDS
    assert "primary: Optional[str]" not in COMMANDS
    assert "secondary: Optional[str]" not in COMMANDS


def test_save_and_preview_are_separate_failure_domains() -> None:
    assert "async def _save_and_preview(" in COMMANDS
    assert "Settings **were saved**, but the preview could not render" in COMMANDS
    assert "if file is not None:" in COMMANDS
    assert "file=preview" not in COMMANDS
    assert "await _private(interaction, content=content, file=preview)" in COMMANDS


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


def test_shuffle_controls_are_canonical_and_visual() -> None:
    assert '"card-shuffle"' in COMMANDS
    assert "Welcome Card Shuffle" in COMMANDS
    assert "Shuffle Fonts + Themes" in COMMANDS
    assert "Shuffle Everything" in COMMANDS
    assert 'label="Shuffle"' in STUDIO
    assert "configured_shuffle_mode" in SERVICE
    assert "_resolve_effective_welcome_style" in SERVICE
