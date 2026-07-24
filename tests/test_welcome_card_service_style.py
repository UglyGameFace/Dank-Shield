from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import welcome_card_service as service


def test_style_config_defaults_are_automatic_and_neon() -> None:
    assert service.configured_font_style_key({}) == "neon"
    assert service.configured_color_mode({}) == "auto"
    assert service.configured_custom_colors({}) == ("", "")


def test_style_config_reads_nested_per_guild_settings() -> None:
    cfg = {
        "settings": {
            "welcome_card_font_style": "tech",
            "welcome_card_color_mode": "custom",
            "welcome_card_custom_primary": "#22DCFF",
            "welcome_card_custom_secondary": "#BC42FF",
        }
    }
    assert service.configured_font_style_key(cfg) == "tech"
    assert service.configured_color_mode(cfg) == "custom"
    assert service.configured_custom_colors(cfg) == ("#22DCFF", "#BC42FF")


def test_profile_visual_fetch_is_cached() -> None:
    service._PROFILE_VISUAL_CACHE.clear()
    calls = {"fetch": 0, "read": 0}

    class FakeAccent:
        def to_rgb(self):
            return (20, 220, 255)

    class FakeBanner:
        def replace(self, **_kwargs):
            return self

        async def read(self):
            calls["read"] += 1
            return b"banner"

    class FakeClient:
        async def fetch_user(self, user_id: int):
            calls["fetch"] += 1
            assert user_id == 123
            return SimpleNamespace(accent_color=FakeAccent(), banner=FakeBanner())

    client = FakeClient()
    state = SimpleNamespace(_get_client=lambda: client)
    member = SimpleNamespace(id=123, _state=state)

    first = asyncio.run(service._profile_visuals(member))
    second = asyncio.run(service._profile_visuals(member))

    assert first == (b"banner", (20, 220, 255))
    assert second == first
    assert calls == {"fetch": 1, "read": 1}


def test_render_passes_saved_style_and_profile_visuals(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_avatar(_member):
        return b"avatar"

    async def fake_profile(_member):
        return b"profile-banner", (12, 34, 56)

    def fake_render(**kwargs):
        seen.update(kwargs)
        return b"rendered"

    monkeypatch.setattr(service, "_avatar_bytes", fake_avatar)
    monkeypatch.setattr(service, "_profile_visuals", fake_profile)
    monkeypatch.setattr(service, "render_welcome_card", fake_render)
    monkeypatch.setattr(service, "decode_custom_background", lambda _cfg: b"card-background")

    member = SimpleNamespace(
        id=123,
        display_name="Member",
        guild=SimpleNamespace(name="Server", member_count=73),
    )
    cfg = {
        "welcome_card_font_style": "tech",
        "welcome_card_color_mode": "auto",
        "welcome_card_custom_primary": "#112233",
        "welcome_card_custom_secondary": "#445566",
    }

    result = asyncio.run(service.render_member_welcome_card(member, cfg))
    assert result == b"rendered"
    assert seen["font_style_key"] == "tech"
    assert seen["color_mode"] == "auto"
    assert seen["profile_banner_bytes"] == b"profile-banner"
    assert seen["profile_accent"] == (12, 34, 56)
    assert seen["custom_background_bytes"] == b"card-background"
