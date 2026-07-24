from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = (ROOT / "stoney_verify/startup_guards/welcome_member_events_guard.py").read_text(encoding="utf-8")
GROUP = (ROOT / "stoney_verify/commands_ext/public_welcome_group.py").read_text(encoding="utf-8")
RENDERER = (ROOT / "stoney_verify/welcome_card_renderer.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/welcome_card_service.py").read_text(encoding="utf-8")


def test_join_path_uses_one_native_card_sender_with_embed_fallback() -> None:
    assert "card = await welcome_card_file(member, cfg)" in GUARD
    assert "file=card" in GUARD
    assert "if sent is None:" in GUARD
    assert "embed=_embed(title, body, member, cfg=cfg, context=context)" in GUARD
    assert GUARD.count("async def _send_join(") == 1


def test_public_card_controls_are_available() -> None:
    for command in (
        'name="card-preview"',
        'name="card-theme"',
        'name="card-upload"',
        'name="card-clear-custom"',
        'name="card-enabled"',
    ):
        assert command in GROUP
    assert "background: discord.Attachment" in GROUP
    assert "normalize_custom_background_for_storage" in GROUP


def test_templates_are_dynamic_not_baked_mockups() -> None:
    assert "render_welcome_card(" in RENDERER
    assert "display_name" in RENDERER
    assert "server_name" in RENDERER
    assert "member_count" in RENDERER
    assert "{USERNAME}" not in RENDERER
    assert "{COUNT}" not in RENDERER
    assert "welcome_card_background_b64" in SERVICE


def test_card_permission_health_includes_attachments() -> None:
    assert GROUP.count('needed["Attach Files"] = perms.attach_files') == 2


if __name__ == "__main__":
    for test in (
        test_join_path_uses_one_native_card_sender_with_embed_fallback,
        test_public_card_controls_are_available,
        test_templates_are_dynamic_not_baked_mockups,
        test_card_permission_health_includes_attachments,
    ):
        test()
        print(f"PASS {test.__name__}")
