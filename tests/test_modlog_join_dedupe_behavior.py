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


def test_router_suppresses_public_card_when_route_is_staff_modlog(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=777)
    member = SimpleNamespace(id=101, guild=guild)
    public = SimpleNamespace(id=100)
    join_leave = SimpleNamespace(id=200)
    staff = SimpleNamespace(id=200)
    sent: list[int] = []

    async def fake_load(_guild_id):
        return object()

    def fake_resolve(_guild, _cfg, keys):
        if keys == router.PUBLIC_WELCOME_KEYS:
            return public
        if keys == router.JOIN_LEAVE_KEYS:
            return join_leave
        return staff

    async def fake_send(_member, channel):
        sent.append(channel.id)

    monkeypatch.setattr(router, "_load_config", fake_load)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve)
    monkeypatch.setattr(
        router,
        "_same_channel",
        lambda a, b: bool(a and b and a.id == b.id),
    )
    monkeypatch.setattr(router, "_send_join_leave_join", fake_send)

    asyncio.run(router._join_listener(member))
    assert sent == []


def test_router_posts_one_simple_card_when_routes_are_distinct(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=777)
    member = SimpleNamespace(id=101, guild=guild)
    public = SimpleNamespace(id=100)
    join_leave = SimpleNamespace(id=200)
    staff = SimpleNamespace(id=300)
    sent: list[int] = []

    async def fake_load(_guild_id):
        return object()

    def fake_resolve(_guild, _cfg, keys):
        if keys == router.PUBLIC_WELCOME_KEYS:
            return public
        if keys == router.JOIN_LEAVE_KEYS:
            return join_leave
        return staff

    async def fake_send(_member, channel):
        sent.append(channel.id)

    monkeypatch.setattr(router, "_load_config", fake_load)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve)
    monkeypatch.setattr(
        router,
        "_same_channel",
        lambda a, b: bool(a and b and a.id == b.id),
    )
    monkeypatch.setattr(router, "_send_join_leave_join", fake_send)

    asyncio.run(router._join_listener(member))
    assert sent == [200]


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
