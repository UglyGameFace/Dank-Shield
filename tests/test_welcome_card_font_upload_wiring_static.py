from pathlib import Path


COMMANDS = Path(
    "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")
ENTRYPOINT = Path("stoney_verify/commands.py").read_text(encoding="utf-8")
ASSETS = Path("stoney_verify/welcome_card_font_assets.py").read_text(encoding="utf-8")
ENGINE = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")
REQUIREMENTS = Path("requirements.txt").read_text(encoding="utf-8")


def test_upload_and_clear_commands_are_registered() -> None:
    assert '"card-font-upload"' in COMMANDS
    assert '"card-font-clear"' in COMMANDS
    assert "font_file: discord.Attachment" in COMMANDS
    assert "Only upload fonts you are licensed" in COMMANDS


def test_all_supported_font_formats_are_visible_and_validated() -> None:
    for suffix in (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"):
        assert f'"{suffix}"' in ASSETS
    assert "TTCollection" in ASSETS
    assert "TTFont" in ASSETS
    assert "checkChecksums=2" in ASSETS
    assert "_REJECTED_TABLES" in ASSETS
    assert "ImageFont.truetype(BytesIO(normalized)" in ASSETS


def test_fonttools_woff_support_is_a_runtime_dependency() -> None:
    assert "fonttools[woff]" in REQUIREMENTS.lower()


def test_studio_loads_through_commands_not_a_startup_guard() -> None:
    assert "register_public_welcome_card_studio_commands" in ENTRYPOINT
    assert "welcome_message_command_guard" not in ENTRYPOINT
    assert "remove_command(" not in COMMANDS


def test_live_service_passes_custom_font_to_authoritative_renderer() -> None:
    assert "configured_custom_font" in SERVICE
    assert "custom_font_bytes=custom_font" in SERVICE
    assert "welcome_card_typography_engine" in SERVICE


def test_final_effect_engine_fits_width_and_height_without_stretching() -> None:
    assert "NAME_SAFE_WIDTH = 710" in ENGINE
    assert "NAME_SAFE_HEIGHT = 102" in ENGINE
    assert "def _fitted_tile(" in ENGINE
    assert "tile.width <= max_width and tile.height <= max_height" in ENGINE
    assert "def _styled_tile(" in ENGINE
    assert "x_scale" not in ENGINE
