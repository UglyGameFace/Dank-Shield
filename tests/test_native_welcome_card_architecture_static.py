from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = (ROOT / "stoney_verify/welcome_card_renderer.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands_ext/public_welcome_card_group.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/commands_ext/public_member_lifecycle_runtime.py").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_renderer_owns_dynamic_text_and_canonical_dimensions() -> None:
    assert "CARD_WIDTH = 1200" in RENDERER
    assert "CARD_HEIGHT = 400" in RENDERER
    assert "display_name" in RENDERER
    assert "server_name" in RENDERER
    assert "member_count" in RENDERER
    assert '"{USERNAME}"' not in RENDERER
    assert '"#{COUNT}"' not in RENDERER


def test_listener_uses_normal_public_runtime_registration() -> None:
    assert "register_public_welcome_card_commands" in RUNTIME
    assert "bot.add_listener(_welcome_card_join_listener, \"on_member_join\")" in COMMANDS
    assert "@bot.event" not in COMMANDS
    assert "startup_guards/welcome_card" not in RUNTIME


def test_delivery_is_opt_in_exact_channel_and_deduped() -> None:
    assert '"welcome_card_enabled"' in SERVICE
    assert '"join_welcome_channel_id"' in SERVICE
    assert "_RECENT_SENDS" in SERVICE
    assert "Attach Files" in SERVICE
    assert "_fallback_embed" in SERVICE


def test_public_setup_supports_builtins_and_custom_backgrounds() -> None:
    for command in (
        'name="card-preview"',
        'name="card-theme"',
        'name="card-toggle"',
        'name="card-background"',
        'name="card-background-reset"',
        'name="card-health"',
    ):
        assert command in COMMANDS
    assert "welcome_card_background_b64" in COMMANDS
    assert "1200×400" in COMMANDS
    assert "_MAX_UPLOAD_BYTES" in COMMANDS
    assert "_MAX_STORED_BYTES" in COMMANDS


def test_pillow_is_an_explicit_production_dependency() -> None:
    assert "Pillow>=11.0.0,<12.0.0" in REQUIREMENTS
