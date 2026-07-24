from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import welcome_card_service as service


def test_welcome_cards_are_opt_in_for_existing_servers() -> None:
    assert service.welcome_cards_enabled({}) is False
    assert service.welcome_cards_enabled({"welcome_card_enabled": False}) is False
    assert service.welcome_cards_enabled({"welcome_card_enabled": True}) is True


def test_builtin_preview_ignores_saved_custom_background(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_avatar(_member) -> bytes:
        return b"avatar"

    def fake_decode(_cfg) -> bytes:
        return b"custom-background"

    def fake_render(**kwargs) -> bytes:
        seen.update(kwargs)
        return b"rendered"

    member = SimpleNamespace(
        display_name="Preview Member",
        guild=SimpleNamespace(name="Preview Server", member_count=42),
    )

    monkeypatch.setattr(service, "_avatar_bytes", fake_avatar)
    monkeypatch.setattr(service, "decode_custom_background", fake_decode)
    monkeypatch.setattr(service, "render_welcome_card", fake_render)

    result = asyncio.run(
        service.render_member_welcome_card(
            member,
            {"welcome_card_background_b64": "saved"},
            theme_override="esports",
        )
    )

    assert result == b"rendered"
    assert seen["theme_key"] == "esports"
    assert seen["custom_background_bytes"] is None


def test_live_render_keeps_saved_custom_background(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_avatar(_member) -> bytes:
        return b"avatar"

    def fake_decode(_cfg) -> bytes:
        return b"custom-background"

    def fake_render(**kwargs) -> bytes:
        seen.update(kwargs)
        return b"rendered"

    member = SimpleNamespace(
        display_name="Live Member",
        guild=SimpleNamespace(name="Live Server", member_count=99),
    )

    monkeypatch.setattr(service, "_avatar_bytes", fake_avatar)
    monkeypatch.setattr(service, "decode_custom_background", fake_decode)
    monkeypatch.setattr(service, "render_welcome_card", fake_render)

    result = asyncio.run(
        service.render_member_welcome_card(
            member,
            {"welcome_card_background_b64": "saved"},
        )
    )

    assert result == b"rendered"
    assert seen["custom_background_bytes"] == b"custom-background"
