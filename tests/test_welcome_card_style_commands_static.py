from pathlib import Path


COMMANDS = Path("stoney_verify/commands_ext/welcome_card_style_commands.py").read_text(encoding="utf-8")
GUARD = Path("stoney_verify/startup_guards/welcome_message_command_guard.py").read_text(encoding="utf-8")
RENDERER = Path("stoney_verify/welcome_card_renderer.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")


def test_style_commands_are_attached_before_discord_sync() -> None:
    assert "welcome_card_style_commands" in GUARD
    assert GUARD.index("welcome_card_style_commands") < GUARD.index("register(None, None)")


def test_public_welcome_group_exposes_style_controls() -> None:
    for command in ('name="card-font"', 'name="card-colors"', 'name="card-style"'):
        assert command in COMMANDS


def test_auto_profile_and_card_color_modes_are_available() -> None:
    assert '"auto": "Smart Auto"' in RENDERER
    assert '"profile": "Member Profile"' in RENDERER
    assert '"card": "Card Background"' in RENDERER
    assert "profile_banner_bytes" in RENDERER
    assert "card_background_bytes" in RENDERER


def test_service_fetches_profile_visuals_only_for_profile_modes() -> None:
    assert 'if color_mode in {"auto", "profile"}' in SERVICE
    assert "fetch_user(user_id)" in SERVICE
    assert "_PROFILE_VISUAL_CACHE_TTL_SECONDS" in SERVICE
