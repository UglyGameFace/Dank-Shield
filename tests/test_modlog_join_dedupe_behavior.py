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

    async def fake_load_config(_guild_id):
        return {}

    def fake_resolve_channel(_guild, _cfg, _keys):
        return None

    monkeypatch.setattr(
        router,
        "send_live_welcome_card",
        fake_send_live_welcome_card,
    )
    monkeypatch.setattr(router, "_load_config", fake_load_config)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve_channel)

    asyncio.run(router._join_listener(member))
    assert calls == [101]


def test_router_join_log_is_independent_of_welcome_card_gate(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=777)
    member = SimpleNamespace(id=202, guild=guild)
    join_log_channel = SimpleNamespace(id=333)
    welcome_calls: list[int] = []
    join_log_calls: list[tuple[int, int]] = []

    async def fake_send_live_welcome_card(target):
        welcome_calls.append(int(target.id))
        return SimpleNamespace(
            sent=False,
            code="studio_disabled",
            channel_id=0,
            used_image=False,
        )

    async def fake_load_config(guild_id):
        assert int(guild_id) == 777
        return {"join_leave_log_channel_id": "333"}

    def fake_resolve_channel(_guild, _cfg, keys):
        assert keys is router.JOIN_LEAVE_KEYS
        return join_log_channel

    async def fake_send_join_log_event(target, channel):
        join_log_calls.append((int(target.id), int(channel.id)))
        return True

    monkeypatch.setattr(
        router,
        "send_live_welcome_card",
        fake_send_live_welcome_card,
    )
    monkeypatch.setattr(router, "_load_config", fake_load_config)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve_channel)
    monkeypatch.setattr(router, "_send_join_log_event", fake_send_join_log_event)
    monkeypatch.setattr(router.discord, "TextChannel", SimpleNamespace)

    asyncio.run(router._join_listener(member))

    assert welcome_calls == [202]
    assert join_log_calls == [(202, 333)]


def test_router_join_log_suppresses_only_true_same_channel_duplicate(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=778)
    member = SimpleNamespace(id=303, guild=guild)
    join_log_channel = SimpleNamespace(id=444)
    join_log_calls: list[tuple[int, int]] = []

    async def fake_send_live_welcome_card(_target):
        return SimpleNamespace(
            sent=True,
            code="sent",
            channel_id=444,
            used_image=True,
        )

    async def fake_load_config(_guild_id):
        return {"join_leave_log_channel_id": "444"}

    def fake_resolve_channel(_guild, _cfg, keys):
        assert keys is router.JOIN_LEAVE_KEYS
        return join_log_channel

    async def fake_send_join_log_event(target, channel):
        join_log_calls.append((int(target.id), int(channel.id)))
        return True

    monkeypatch.setattr(
        router,
        "send_live_welcome_card",
        fake_send_live_welcome_card,
    )
    monkeypatch.setattr(router, "_load_config", fake_load_config)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve_channel)
    monkeypatch.setattr(router, "_send_join_log_event", fake_send_join_log_event)
    monkeypatch.setattr(router.discord, "TextChannel", SimpleNamespace)

    asyncio.run(router._join_listener(member))

    assert join_log_calls == []


def test_router_leave_log_is_independent_of_exit_card_gate(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=779)
    member = SimpleNamespace(id=404, guild=guild)
    leave_log_channel = SimpleNamespace(id=555)
    exit_calls: list[int] = []
    leave_log_calls: list[tuple[int, int]] = []

    async def fake_send_live_exit_card(target):
        exit_calls.append(int(target.id))
        return SimpleNamespace(
            sent=False,
            code="studio_disabled",
            channel_id=0,
            used_image=False,
        )

    async def fake_load_config(guild_id):
        assert int(guild_id) == 779
        return {"join_leave_log_channel_id": "555"}

    def fake_resolve_channel(_guild, _cfg, keys):
        assert keys is router.JOIN_LEAVE_KEYS
        return leave_log_channel

    async def fake_send_leave_log_event(target, channel):
        leave_log_calls.append((int(target.id), int(channel.id)))
        return True

    monkeypatch.setattr(router, "send_live_exit_card", fake_send_live_exit_card)
    monkeypatch.setattr(router, "_load_config", fake_load_config)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve_channel)
    monkeypatch.setattr(router, "_send_leave_log_event", fake_send_leave_log_event)
    monkeypatch.setattr(router.discord, "TextChannel", SimpleNamespace)

    asyncio.run(router._leave_listener(member))

    assert exit_calls == [404]
    assert leave_log_calls == [(404, 555)]


def test_router_leave_log_suppresses_only_true_same_channel_duplicate(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=780)
    member = SimpleNamespace(id=505, guild=guild)
    leave_log_channel = SimpleNamespace(id=666)
    leave_log_calls: list[tuple[int, int]] = []

    async def fake_send_live_exit_card(_target):
        return SimpleNamespace(
            sent=True,
            code="sent",
            channel_id=666,
            used_image=True,
        )

    async def fake_load_config(_guild_id):
        return {"join_leave_log_channel_id": "666"}

    def fake_resolve_channel(_guild, _cfg, keys):
        assert keys is router.JOIN_LEAVE_KEYS
        return leave_log_channel

    async def fake_send_leave_log_event(target, channel):
        leave_log_calls.append((int(target.id), int(channel.id)))
        return True

    monkeypatch.setattr(router, "send_live_exit_card", fake_send_live_exit_card)
    monkeypatch.setattr(router, "_load_config", fake_load_config)
    monkeypatch.setattr(router, "_resolve_channel", fake_resolve_channel)
    monkeypatch.setattr(router, "_send_leave_log_event", fake_send_leave_log_event)
    monkeypatch.setattr(router.discord, "TextChannel", SimpleNamespace)

    asyncio.run(router._leave_listener(member))

    assert leave_log_calls == []


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
