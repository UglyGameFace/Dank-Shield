from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import welcome_card_runtime as runtime


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
        return FakeMessage(9000 + len(self.sent))


class FakeGuild:
    def __init__(self) -> None:
        self.id = 44
        self.name = "Test Guild"
        self.member_count = 12
        self.me = None
        self.channels: dict[int, FakeChannel] = {}

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(int(channel_id))


class FakeMember:
    def __init__(self, guild: FakeGuild, user_id: int = 77) -> None:
        self.guild = guild
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.display_name = "Tester"
        self.display_avatar = SimpleNamespace(url="https://example.invalid/avatar.png")

    def __str__(self) -> str:
        return "tester"


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime._RECENT_DELIVERIES.clear()
    runtime._DELIVERY_LOCKS.clear()
    monkeypatch.setattr(runtime.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime.discord, "Member", FakeMember)


def _world() -> tuple[FakeGuild, FakeChannel, FakeMember]:
    guild = FakeGuild()
    me = FakeMember(guild, user_id=999)
    guild.me = me
    channel = FakeChannel(123, guild)
    guild.channels[channel.id] = channel
    return guild, channel, FakeMember(guild)


def test_studio_enabled_sends_without_legacy_join_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild, channel, member = _world()

    async def fake_config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        assert refresh is True
        return {
            "welcome_card_enabled": True,
            "join_welcome_channel_id": str(channel.id),
            "welcome_join_enabled": False,
        }

    async def fake_card(_member: object, _cfg: object) -> discord.File:
        return discord.File(BytesIO(b"card"), filename="welcome.png")

    monkeypatch.setattr(runtime, "get_guild_config", fake_config)
    monkeypatch.setattr(runtime, "welcome_card_file", fake_card)

    result = asyncio.run(runtime.send_live_welcome_card(member))

    assert result.sent is True
    assert result.used_image is True
    assert result.channel_id == channel.id
    assert len(channel.sent) == 1
    assert channel.sent[0]["content"] == member.mention
    assert isinstance(channel.sent[0]["file"], discord.File)


def test_disabled_studio_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _guild, channel, member = _world()

    async def fake_config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "welcome_card_enabled": False,
            "join_welcome_channel_id": str(channel.id),
        }

    monkeypatch.setattr(runtime, "get_guild_config", fake_config)

    result = asyncio.run(runtime.send_live_welcome_card(member))

    assert result.sent is False
    assert result.code == "studio_disabled"
    assert channel.sent == []


def test_stale_explicit_channel_never_silently_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild, fallback, member = _world()

    async def fake_config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "welcome_card_enabled": True,
            "join_welcome_channel_id": "999999",
            "welcome_channel_id": str(fallback.id),
        }

    monkeypatch.setattr(runtime, "get_guild_config", fake_config)

    result = asyncio.run(runtime.send_live_welcome_card(member))

    assert result.sent is False
    assert result.code == "channel_unavailable"
    assert fallback.sent == []
    channel, reason = runtime.resolve_join_card_channel(
        guild,
        {
            "join_welcome_channel_id": "999999",
            "welcome_channel_id": str(fallback.id),
        },
    )
    assert channel is None
    assert "999999" in reason


def test_render_failure_uses_one_canonical_embed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member = _world()

    async def fake_config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "welcome_card_enabled": True,
            "join_welcome_channel_id": str(channel.id),
            "welcome_join_title": "Hello {display_name}",
            "welcome_join_body": "Welcome to {server_name}.",
        }

    async def broken_card(_member: object, _cfg: object) -> discord.File:
        raise RuntimeError("broken renderer")

    monkeypatch.setattr(runtime, "get_guild_config", fake_config)
    monkeypatch.setattr(runtime, "welcome_card_file", broken_card)

    result = asyncio.run(runtime.send_live_welcome_card(member))

    assert result.sent is True
    assert result.used_image is False
    assert len(channel.sent) == 1
    assert "file" not in channel.sent[0]
    embed = channel.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Hello Tester"
    assert embed.footer.text == "dank_shield:welcome_card_runtime:v1"


def test_duplicate_join_delivery_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    _guild, channel, member = _world()

    async def fake_config(_guild_id: int, *, refresh: bool = False) -> dict[str, object]:
        return {
            "welcome_card_enabled": True,
            "join_welcome_channel_id": str(channel.id),
        }

    async def fake_card(_member: object, _cfg: object) -> discord.File:
        return discord.File(BytesIO(b"card"), filename="welcome.png")

    monkeypatch.setattr(runtime, "get_guild_config", fake_config)
    monkeypatch.setattr(runtime, "welcome_card_file", fake_card)

    first = asyncio.run(runtime.send_live_welcome_card(member))
    second = asyncio.run(runtime.send_live_welcome_card(member))

    assert first.sent is True
    assert second.sent is False
    assert second.code == "duplicate_suppressed"
    assert len(channel.sent) == 1
