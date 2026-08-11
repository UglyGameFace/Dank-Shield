from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import exit_card_runtime as runtime
from stoney_verify.exit_card_service import exit_cards_enabled


class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeChannel:
    def __init__(self, channel_id: int, guild: "FakeGuild") -> None:
        self.id = channel_id
        self.guild = guild
        self.mention = f"<#{channel_id}>"
        self.sent: list[dict[str, object]] = []
        self.permissions = FakePermissions()

    def permissions_for(self, _member: object) -> FakePermissions:
        return self.permissions

    async def send(self, **kwargs: object) -> FakeMessage:
        self.sent.append(dict(kwargs))
        return FakeMessage(8000 + len(self.sent))


class FakeGuild:
    def __init__(self) -> None:
        self.id = 55
        self.name = "Vibers Paradise"
        self.member_count = 124
        self.me = None
        self.channels: dict[int, FakeChannel] = {}
        self.text_channels: list[FakeChannel] = []
        self.categories = []

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(int(channel_id))


class FakeMember:
    def __init__(self, guild: FakeGuild, user_id: int = 77) -> None:
        self.guild = guild
        self.id = user_id
        self.name = "9byte"
        self.display_name = "Nine Byte"
        self.mention = f"<@{user_id}>"
        self.display_avatar = SimpleNamespace(url="https://example.invalid/avatar.png")
        now = discord.utils.utcnow()
        self.created_at = now - timedelta(days=100)
        self.joined_at = now - timedelta(days=20)

    def __str__(self) -> str:
        return "9byte"


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._RECENT_DELIVERIES.clear()
    runtime._DELIVERY_LOCKS.clear()
    monkeypatch.setattr(runtime.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime.discord, "Member", FakeMember)


def _world() -> tuple[FakeGuild, FakeChannel, FakeMember]:
    guild = FakeGuild()
    me = FakeMember(guild, 999)
    guild.me = me
    channel = FakeChannel(321, guild)
    guild.channels[channel.id] = channel
    guild.text_channels.append(channel)
    return guild, channel, FakeMember(guild)


def test_exit_studio_sends_one_image_card_with_no_departed_member_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        assert refresh is True
        return {
            "exit_card_enabled": True,
            "exit_card_channel_id": str(channel.id),
            "exit_card_title": "Goodbye { username }",
            "exit_card_body": "{display_name} left {server_name}. Members: {member_count}",
        }

    async def card(_member: object, _cfg: object) -> discord.File:
        return discord.File(BytesIO(b"exit"), filename="exit.png")

    monkeypatch.setattr(runtime, "get_guild_config", config)
    monkeypatch.setattr(runtime, "exit_card_file", card)

    result = asyncio.run(runtime.send_live_exit_card(member))

    assert result.sent is True
    assert result.used_image is True
    assert len(channel.sent) == 1
    assert "content" not in channel.sent[0]
    embed = channel.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Goodbye 9byte"
    assert "Nine Byte left Vibers Paradise. Members: 124" in (embed.description or "")
    assert not getattr(embed.footer, "text", None)


def test_live_exit_image_normalizes_decorative_unicode_without_rewriting_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()
    member.display_name = "𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫"
    seen: dict[str, str] = {}

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "exit_card_enabled": True,
            "exit_card_channel_id": str(channel.id),
            "exit_card_body": "{display_name} left {server_name}.",
        }

    async def card(render_member: object, _cfg: object) -> discord.File:
        seen["display_name"] = str(getattr(render_member, "display_name"))
        return discord.File(BytesIO(b"exit"), filename="exit.png")

    monkeypatch.setattr(runtime, "get_guild_config", config)
    monkeypatch.setattr(runtime, "exit_card_file", card)

    result = asyncio.run(runtime.send_live_exit_card(member))

    assert result.sent is True
    assert seen["display_name"] == "Eyez Of Bob"
    embed = channel.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert "𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫" in (embed.description or "")
    assert not getattr(embed.footer, "text", None)


def test_explicit_exit_disable_wins_over_old_leave_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "exit_card_enabled": False,
            "exit_card_channel_id": str(channel.id),
            "goodbye_enabled": True,
            "leave_channel_id": str(channel.id),
        }

    monkeypatch.setattr(runtime, "get_guild_config", config)
    result = asyncio.run(runtime.send_live_exit_card(member))

    assert result.sent is False
    assert result.code == "studio_disabled"
    assert channel.sent == []


def test_legacy_route_without_old_boolean_remains_enabled_during_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()
    cfg = {"leave_channel_id": str(channel.id)}
    assert exit_cards_enabled(cfg) is True

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return cfg

    async def card(_member: object, _cfg: object) -> discord.File:
        return discord.File(BytesIO(b"exit"), filename="exit.png")

    monkeypatch.setattr(runtime, "get_guild_config", config)
    monkeypatch.setattr(runtime, "exit_card_file", card)

    result = asyncio.run(runtime.send_live_exit_card(member))
    assert result.sent is True
    assert result.channel_id == channel.id
    assert len(channel.sent) == 1


def test_stale_explicit_exit_channel_never_reroutes_to_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild, fallback, member = _world()

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "exit_card_enabled": True,
            "exit_card_channel_id": "999999",
            "leave_channel_id": str(fallback.id),
        }

    monkeypatch.setattr(runtime, "get_guild_config", config)
    result = asyncio.run(runtime.send_live_exit_card(member))

    assert result.sent is False
    assert result.code == "channel_unavailable"
    assert fallback.sent == []
    channel, reason = runtime.resolve_exit_card_channel(
        guild,
        {
            "exit_card_channel_id": "999999",
            "leave_channel_id": str(fallback.id),
        },
    )
    assert channel is None
    assert "999999" in reason


def test_image_failure_uses_exactly_one_exit_embed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "exit_card_enabled": True,
            "exit_card_channel_id": str(channel.id),
        }

    async def broken(_member: object, _cfg: object) -> discord.File:
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(runtime, "get_guild_config", config)
    monkeypatch.setattr(runtime, "exit_card_file", broken)

    result = asyncio.run(runtime.send_live_exit_card(member))

    assert result.sent is True
    assert result.used_image is False
    assert len(channel.sent) == 1
    assert "file" not in channel.sent[0]
    embed = channel.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert not getattr(embed.footer, "text", None)


def test_duplicate_exit_delivery_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    _guild, channel, member = _world()

    async def config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "exit_card_enabled": True,
            "exit_card_channel_id": str(channel.id),
        }

    async def card(_member: object, _cfg: object) -> discord.File:
        return discord.File(BytesIO(b"exit"), filename="exit.png")

    monkeypatch.setattr(runtime, "get_guild_config", config)
    monkeypatch.setattr(runtime, "exit_card_file", card)

    first = asyncio.run(runtime.send_live_exit_card(member))
    second = asyncio.run(runtime.send_live_exit_card(member))

    assert first.sent is True
    assert second.sent is False
    assert second.code == "duplicate_suppressed"
    assert len(channel.sent) == 1
