from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "stoney_verify/welcome_card_runtime.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text(encoding="utf-8")
EVENTS = (ROOT / "stoney_verify/events.py").read_text(encoding="utf-8")
GROUP = (ROOT / "stoney_verify/commands_ext/public_welcome_group.py").read_text(encoding="utf-8")
STUDIO = (ROOT / "stoney_verify/welcome_card_studio_ui.py").read_text(encoding="utf-8")
RENDERER = (ROOT / "stoney_verify/welcome_card_renderer.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")


def test_join_path_uses_one_canonical_studio_owned_runtime() -> None:
    assert "async def send_live_welcome_card(" in RUNTIME
    assert "cfg = await get_guild_config(int(member.guild.id), refresh=True)" in RUNTIME
    assert "if not welcome_cards_enabled(cfg):" in RUNTIME
    assert "welcome_join_enabled" not in RUNTIME
    assert "card = await welcome_card_file(member, cfg)" in RUNTIME
    assert "file=card" in RUNTIME
    assert 'embed.set_image(url=f"attachment://{card.filename}")' in RUNTIME
    assert "using canonical embed fallback" in RUNTIME
    assert 'embed.set_footer(text="dank_shield:welcome_card_runtime:v1")' in RUNTIME

    assert "delivery = await send_live_welcome_card(member)" in ROUTER
    assert "_install_listener(_join_listener, \"on_member_join\")" in ROUTER
    assert "welcome_member_events_guard" in ROUTER
    assert "removed old/conflicting member lifecycle listeners" in ROUTER
    assert "dank_shield:join_leave_event:v3" not in RUNTIME


def test_staff_join_audit_remains_separate_from_public_card() -> None:
    assert "@bot.event\nasync def on_member_join" in EVENTS
    assert "await _post_modlog(" in EVENTS
    assert 'event_key=f"member_join:{member.id}"' in EVENTS
    assert "send_live_welcome_card" not in EVENTS
    assert "_post_modlog" not in RUNTIME


def test_studio_panel_survives_preview_failure_and_controls_live_state() -> None:
    assert "class WelcomeCardStudioView(discord.ui.View)" in STUDIO
    assert "WelcomeCardChannelSelect" in STUDIO
    assert '"join_welcome_channel_id": str(channel.id)' in STUDIO
    assert '"welcome_card_enabled": True' in STUDIO
    assert "Preview needs repair" in STUDIO
    assert "The Studio is still usable" in STUDIO
    assert "await _defer(interaction)" in STUDIO
    for label in (
        'label="Enable / Disable"',
        'label="Theme"',
        'label="Font"',
        'label="Colors"',
        'label="Shuffle"',
        'label="Preview"',
        'label="Uploads"',
    ):
        assert label in STUDIO


def test_studio_preview_uses_exact_live_embed_and_image_fallback_paths() -> None:
    assert "from .welcome_card_runtime import build_join_card_embed" in STUDIO
    assert "live_embed = build_join_card_embed(interaction.user, cfg)" in STUDIO
    assert 'live_embed.set_image(url=f"attachment://{file.filename}")' in STUDIO
    assert "Exact live join-card preview" in STUDIO
    assert "the exact live text fallback is below" in STUDIO
    assert "Preview only • dank_shield:welcome_card_runtime:v1" in STUDIO
    assert "Preview fallback • dank_shield:welcome_card_runtime:v1" in STUDIO


def test_public_card_upload_controls_remain_available() -> None:
    for command in (
        'name="card-preview"',
        'name="card-upload"',
        'name="card-clear-custom"',
        'name="card-enabled"',
    ):
        assert command in GROUP
    assert "background: discord.Attachment" in GROUP
    assert "normalize_custom_background_for_storage" in GROUP
    assert GROUP.count('"welcome_card_background_b64": ""') >= 2
    assert "MAX_CUSTOM_BACKGROUND_BYTES" in GROUP


def test_templates_are_dynamic_not_baked_mockups() -> None:
    assert "render_welcome_card(" in RENDERER
    assert "display_name" in RENDERER
    assert "server_name" in RENDERER
    assert "member_count" in RENDERER
    assert "{USERNAME}" not in RENDERER
    assert "{COUNT}" not in RENDERER
    assert "welcome_card_background_b64" in SERVICE
    assert 'return _cfg_bool(cfg, "welcome_card_enabled", False)' in SERVICE
    assert "if theme_override is not None" in SERVICE


def test_card_permission_health_includes_attachments() -> None:
    assert GROUP.count('needed["Attach Files"] = perms.attach_files') == 2
