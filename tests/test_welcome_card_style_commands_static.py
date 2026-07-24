from pathlib import Path


COMMANDS = Path(
    "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")
ENTRYPOINT = Path("stoney_verify/commands.py").read_text(encoding="utf-8")
RENDERER = Path("stoney_verify/welcome_card_typography_engine.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")
MAIN = Path("main.py").read_text(encoding="utf-8")


def test_style_commands_use_canonical_command_registration() -> None:
    assert "register_public_welcome_card_studio_commands" in ENTRYPOINT
    assert "welcome_message_command_guard" not in MAIN
    assert "remove_command(" not in COMMANDS
    assert "_replace_existing_command" not in COMMANDS


def test_public_welcome_group_exposes_style_controls() -> None:
    for command in (
        '"card-font"',
        '"card-colors"',
        '"card-style"',
        '"card-font-upload"',
        '"card-font-clear"',
    ):
        assert command in COMMANDS


def test_auto_profile_and_card_color_modes_are_available() -> None:
    assert '"auto": "Smart Auto"' in RENDERER or "COLOR_MODES = legacy.COLOR_MODES" in RENDERER
    assert "profile_banner_bytes" in RENDERER
    assert "card_background_bytes=custom_background_bytes" in RENDERER
    assert "COLOR_PRESETS" in RENDERER
    assert "COLOR_SWATCHES" in RENDERER


def test_service_fetches_profile_visuals_only_for_profile_modes() -> None:
    assert 'if color_mode in {"auto", "profile"}' in SERVICE
    assert "fetch_user(user_id)" in SERVICE
    assert "_PROFILE_VISUAL_CACHE_TTL_SECONDS" in SERVICE
