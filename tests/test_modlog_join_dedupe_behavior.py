from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import modlog
from stoney_verify.startup_guards import member_lifecycle_router_guard as router


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        self.sent.append(dict(kwargs))
        return SimpleNamespace(id=len(self.sent))


def test_modlog_semantic_event_key_suppresses_only_duplicates(
    monkeypatch,
) -> None:
    modlog._MODLOG_RECENT_EVENT_KEYS.clear()
    channel = FakeChannel(500)
    guild = SimpleNamespace(id=777)

    async def fake_channel(_guild):
        return channel

    monkeypatch.setattr(modlog, "_get_modlog_channel_async", fake_channel)

    async def scenario() -> None:
        first = await modlog._post_modlog(
            guild,
            discord.Embed(title="Member Joined"),
            event_key="member_join:101",
            dedupe_window_seconds=20,
        )
        duplicate = await modlog._post_modlog(
            guild,
            discord.Embed(title="Member Joined Again"),
            event_key="member_join:101",
            dedupe_window_seconds=20,
        )
        distinct = await modlog._post_modlog(
            guild,
            discord.Embed(title="Different Member"),
            event_key="member_join:202",
            dedupe_window_seconds=20,
        )

        assert first is not None
        assert duplicate is None
        assert distinct is not None

    asyncio.run(scenario())
    assert len(channel.sent) == 2


def test_router_delegates_join_to_canonical_welcome_runtime(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=777)
    member = SimpleNamespace(id=101, guild=guild)
    calls: list[int] = []

    async def fake_send_live_welcome_card(target):
        calls.append(int(target.id))
        return SimpleNamespace(
            sent=True,
            code="sent",
            channel_id=200,
            used_image=True,
        )

    monkeypatch.setattr(
        router,
        "send_live_welcome_card",
        fake_send_live_welcome_card,
    )

    asyncio.run(router._join_listener(member))
    assert calls == [101]


def test_router_join_never_reenters_retired_route_resolution(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=777)
    member = SimpleNamespace(id=202, guild=guild)
    calls: list[int] = []

    async def forbidden_load(*_args, **_kwargs):
        raise AssertionError("join listener read the retired lifecycle route")

    def forbidden_resolve(*_args, **_kwargs):
        raise AssertionError("join listener resolved the retired simple join card")

    async def fake_send_live_welcome_card(target):
        calls.append(int(target.id))
        return SimpleNamespace(
            sent=False,
            code="studio_disabled",
            channel_id=0,
            used_image=False,
        )

    monkeypatch.setattr(router, "_load_config", forbidden_load)
    monkeypatch.setattr(router, "_resolve_channel", forbidden_resolve)
    monkeypatch.setattr(
        router,
        "send_live_welcome_card",
        fake_send_live_welcome_card,
    )

    asyncio.run(router._join_listener(member))
    assert calls == [202]


def test_identical_unkeyed_embeds_are_coalesced_for_short_bursts(
    monkeypatch,
) -> None:
    modlog._MODLOG_RECENT_EVENT_KEYS.clear()
    channel = FakeChannel(501)
    guild = SimpleNamespace(id=778)

    async def fake_channel(_guild):
        return channel

    monkeypatch.setattr(modlog, "_get_modlog_channel_async", fake_channel)

    async def scenario() -> None:
        first = await modlog._post_modlog(
            guild,
            discord.Embed(
                title="Repeated Audit",
                description="same payload",
            ),
        )
        duplicate = await modlog._post_modlog(
            guild,
            discord.Embed(
                title="Repeated Audit",
                description="same payload",
            ),
        )
        assert first is not None
        assert duplicate is None

    asyncio.run(scenario())
    assert len(channel.sent) == 1
