from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.commands_ext import public_protection_center as center


class _Response:
    def __init__(self, events: list[str], *, done: bool = False) -> None:
        self.events = events
        self.done = done
        self.defer_calls = 0

    def is_done(self) -> bool:
        return self.done

    async def defer(self, *, ephemeral: bool = False) -> None:
        assert ephemeral is True
        self.defer_calls += 1
        self.events.append("defer")
        self.done = True


class _Interaction:
    def __init__(self, events: list[str], *, response_done: bool = False) -> None:
        self.guild = SimpleNamespace(id=123)
        self.user = SimpleNamespace(id=456)
        self.channel = None
        self.response = _Response(events, done=response_done)
        self.events = events
        self.last_payload = None

    async def edit_original_response(self, **payload):
        self.events.append("edit")
        self.last_payload = dict(payload)


def _install_refresh_fakes(monkeypatch, events: list[str]) -> None:
    async def get_config(_guild_id: int, *, refresh: bool = False):
        assert refresh is True
        events.append("config")
        await asyncio.sleep(0)
        return {"automod_enabled": True}

    async def get_spam(_guild_id: int):
        events.append("spam")
        await asyncio.sleep(0)
        return ({"enabled": True, "mode": "timeout"}, "loaded")

    async def stats(_interaction, _guild):
        events.append("stats")

    async def unexpected_followup(*args, **kwargs):
        raise AssertionError(f"unexpected fallback followup: {args!r} {kwargs!r}")

    monkeypatch.setattr(center, "get_guild_config", get_config)
    monkeypatch.setattr(center, "_load_spam_settings", get_spam)
    monkeypatch.setattr(center, "_refresh_security_stats_after_panel", stats)
    monkeypatch.setattr(center, "_protection_embed", lambda *args, **kwargs: "embed")
    monkeypatch.setattr(center, "ProtectionCenterView", lambda **kwargs: "view")
    monkeypatch.setattr(center, "safe_send_interaction", unexpected_followup)


def test_refresh_panel_acknowledges_before_slow_loads_and_edits_original(
    monkeypatch,
) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)
    interaction = _Interaction(events)

    asyncio.run(center._refresh_panel(interaction, content="open"))

    assert events[0] == "defer"
    assert events.index("config") > events.index("defer")
    assert events.index("spam") > events.index("defer")
    assert events.index("edit") > events.index("config")
    assert events.index("edit") > events.index("spam")
    assert events.index("stats") > events.index("edit")
    assert interaction.response.defer_calls == 1
    assert interaction.last_payload == {
        "content": "open",
        "embed": "embed",
        "view": "view",
    }


def test_refresh_panel_reuses_existing_ack_without_duplicate_defer(
    monkeypatch,
) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)
    interaction = _Interaction(events, response_done=True)

    asyncio.run(center._refresh_panel(interaction))

    assert "defer" not in events
    assert interaction.response.defer_calls == 0
    assert events[-2:] == ["edit", "stats"]
