from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/exit_card_runtime.py").read_text(encoding="utf-8")
STUDIO = (ROOT / "stoney_verify/exit_card_studio_ui.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/exit_card_service.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
COMPACT = (ROOT / "stoney_verify/commands_ext/public_exit_compact_surface.py").read_text(encoding="utf-8")
UPLOAD = (ROOT / "stoney_verify/commands_ext/public_exit_card_studio.py").read_text(encoding="utf-8")


def test_router_has_one_canonical_public_leave_sender() -> None:
    assert "delivery = await send_live_exit_card(member)" in ROUTER
    assert '_install_listener(_leave_listener, "on_member_remove")' in ROUTER
    assert "async def _send_public_leave(" not in ROUTER
    assert "dank_shield:leave_event:v4" not in ROUTER
    assert "resolve_exit_card_channel" in ROUTER
    assert "staff audit remains a separate route" in ROUTER.lower()


def test_exit_runtime_owns_live_gate_route_image_and_fallback() -> None:
    assert "async def send_live_exit_card(" in RUNTIME
    assert "if not exit_cards_enabled(cfg):" in RUNTIME
    assert "exit_card_file(member, cfg)" in RUNTIME
    assert "using canonical embed fallback" in RUNTIME
    assert 'embed.set_footer(text="dank_shield:exit_card_runtime:v1")' in RUNTIME
    assert "duplicate_suppressed" in RUNTIME
    assert "exit_card_channel_id" in RUNTIME


def test_exit_studio_exposes_complete_button_first_controls() -> None:
    assert "class ExitCardStudioView(_OwnedView)" in STUDIO
    assert "ExitCardChannelSelect" in STUDIO
    for label in (
        'label="Enable / Disable"',
        'label="Edit Text"',
        'label="Theme"',
        'label="Font"',
        'label="Colors"',
        'label="Shuffle"',
        'label="Preview"',
        'label="Clear Artwork"',
        'label="Reset Design"',
        'label="Uploads"',
    ):
        assert label in STUDIO
    assert 'emoji="🔌"' in STUDIO
    assert "⏻" not in STUDIO


def test_exit_config_is_separate_but_legacy_compatible() -> None:
    for key in (
        "exit_card_enabled",
        "exit_card_theme",
        "exit_card_font_style",
        "exit_card_color_mode",
        "exit_card_background_b64",
        "exit_card_shuffle_mode",
    ):
        assert key in SERVICE
    assert "welcome_leave_enabled" in SERVICE
    assert "goodbye_enabled" in SERVICE
    assert "leave_message_enabled" in SERVICE
    assert "retired v4 sender historically posted" in SERVICE


def test_exit_compact_commands_are_registered_after_main_compaction() -> None:
    assert "register_compact_exit_card_commands(bot, bot.tree)" in COMMANDS
    assert "register_compact_exit_card_commands(bot, tree)" in COMMANDS
    for name in ("exit-card-studio", "exit-card-preview", "exit-card-upload"):
        assert f'"{name}"' in COMPACT
    assert "DANK_PAYLOAD_SAFETY_LIMIT" in COMPACT
    assert 'name="exit-card-upload"' in UPLOAD
    assert "exit_card_background_b64" in UPLOAD
