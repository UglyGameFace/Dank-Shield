from pathlib import Path


COMMANDS = Path(
    "stoney_verify/commands_ext/welcome_card_font_upgrade_commands.py"
).read_text(encoding="utf-8")
GUARD = Path(
    "stoney_verify/startup_guards/welcome_message_command_guard.py"
).read_text(encoding="utf-8")
ASSETS = Path("stoney_verify/welcome_card_font_assets.py").read_text(encoding="utf-8")
ENGINE = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")
REQUIREMENTS = Path("requirements.txt").read_text(encoding="utf-8")


def test_upload_and_clear_commands_are_registered() -> None:
    assert 'name="card-font-upload"' in COMMANDS
    assert 'name="card-font-clear"' in COMMANDS
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


def test_upgrade_loads_after_color_studio_before_sync() -> None:
    assert "welcome_card_style_commands" in GUARD
    assert "welcome_card_font_upgrade_commands" in GUARD
    assert GUARD.index("welcome_card_style_commands") < GUARD.index(
        "welcome_card_font_upgrade_commands"
    )
    assert GUARD.index("welcome_card_font_upgrade_commands") < GUARD.index(
        "register(None, None)"
    )


def test_live_service_passes_custom_font_to_authoritative_renderer() -> None:
    assert "configured_custom_font" in SERVICE
    assert "custom_font_bytes=custom_font" in SERVICE
    assert "welcome_card_typography_engine" in SERVICE


def test_proportional_engine_fits_width_and_height_without_stretching() -> None:
    assert "NAME_SAFE_WIDTH = 710" in ENGINE
    assert "NAME_SAFE_HEIGHT = 104" in ENGINE
    assert "mask.width <= max_width and mask.height <= max_height" in ENGINE
    assert "x_scale ==" not in ENGINE
    assert "No built-in style uses horizontal stretching" in ENGINE
