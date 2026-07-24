from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import welcome_card_service as service


class FakePerms:
    def __init__(
        self,
        *,
        view_channel: bool = True,
        send_messages: bool = True,
        embed_links: bool = True,
        attach_files: bool = True,
    ) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages
        self.embed_links = embed_links
        self.attach_files = attach_files


class FakeBotMember:
    pass


class FakeTextChannel:
    def __init__(self, guild, channel_id: int, perms: FakePerms | None = None) -> None:
        self.guild = guild
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self._perms = perms or FakePerms()
        self.sent: list[dict] = []

    def permissions_for(self, _member) -> FakePerms:
        return self._perms

    async def send(self, **kwargs):
        self.sent.append(dict(kwargs))
        return SimpleNamespace(id=9000 + len(self.sent))


class FakeGuild:
    def __init__(self, guild_id: int = 777, *, perms: FakePerms | None = None) -> None:
        self.id = guild_id
        self.name = "The 420 Lobby"
        self.member_count = 420
        self.members = []
        self.me = FakeBotMember()
        self.channel = FakeTextChannel(self, 123, perms=perms)

    def get_channel(self, channel_id: int):
        return self.channel if int(channel_id) == int(self.channel.id) else None


class FakeAvatar:
    url = "https://example.test/avatar.png"


class FakeMember:
    def __init__(self, guild: FakeGuild, member_id: int = 456) -> None:
        self.guild = guild
        self.id = member_id
        self.bot = False
        self.mention = f"<@{member_id}>"
        self.display_name = "UglyGameFace"
        self.display_avatar = FakeAvatar()

    def __str__(self) -> str:
        return self.display_name


def _patch_discord_types(monkeypatch) -> None:
    monkeypatch.setattr(service.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(service.discord, "Member", FakeBotMember)


def _clear_dedupe() -> None:
    service._RECENT_SENDS.clear()


def test_disabled_cards_do_not_send(monkeypatch) -> None:
    _clear_dedupe()
    guild = FakeGuild()
    member = FakeMember(guild)

    async def fake_config(_guild_id, refresh=False):
        return {"welcome_card_enabled": False, "join_welcome_channel_id": 123}

    monkeypatch.setattr(service, "get_guild_config", fake_config)
    result = asyncio.run(service.send_member_welcome_card(member))

    assert result is False
    assert guild.channel.sent == []


def test_enabled_card_sends_once_and_duplicate_join_is_suppressed(monkeypatch) -> None:
    _clear_dedupe()
    _patch_discord_types(monkeypatch)
    guild = FakeGuild()
    member = FakeMember(guild)
    rendered_file = object()

    async def fake_config(_guild_id, refresh=False):
        return {"welcome_card_enabled": True, "join_welcome_channel_id": 123}

    async def fake_build(_member, _cfg):
        return rendered_file

    monkeypatch.setattr(service, "get_guild_config", fake_config)
    monkeypatch.setattr(service, "build_welcome_card_file", fake_build)

    first = asyncio.run(service.send_member_welcome_card(member))
    second = asyncio.run(service.send_member_welcome_card(member))

    assert first is True
    assert second is False
    assert len(guild.channel.sent) == 1
    assert guild.channel.sent[0]["file"] is rendered_file
    assert guild.channel.sent[0]["content"] == member.mention


def test_missing_attach_files_uses_embed_fallback(monkeypatch) -> None:
    _clear_dedupe()
    _patch_discord_types(monkeypatch)
    guild = FakeGuild(perms=FakePerms(attach_files=False))
    member = FakeMember(guild)

    async def fake_config(_guild_id, refresh=False):
        return {"welcome_card_enabled": True, "join_welcome_channel_id": 123}

    async def should_not_render(_member, _cfg):
        raise AssertionError("renderer should not run without Attach Files")

    monkeypatch.setattr(service, "get_guild_config", fake_config)
    monkeypatch.setattr(service, "build_welcome_card_file", should_not_render)

    result = asyncio.run(service.send_member_welcome_card(member))

    assert result is True
    assert len(guild.channel.sent) == 1
    assert "embed" in guild.channel.sent[0]
    assert "file" not in guild.channel.sent[0]


def test_render_failure_uses_embed_fallback(monkeypatch) -> None:
    _clear_dedupe()
    _patch_discord_types(monkeypatch)
    guild = FakeGuild()
    member = FakeMember(guild)

    async def fake_config(_guild_id, refresh=False):
        return {"welcome_card_enabled": True, "join_welcome_channel_id": 123}

    async def broken_renderer(_member, _cfg):
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(service, "get_guild_config", fake_config)
    monkeypatch.setattr(service, "build_welcome_card_file", broken_renderer)

    result = asyncio.run(service.send_member_welcome_card(member))

    assert result is True
    assert len(guild.channel.sent) == 1
    assert "embed" in guild.channel.sent[0]
    assert "file" not in guild.channel.sent[0]


def test_missing_configured_channel_does_not_fall_back_somewhere_else(monkeypatch) -> None:
    _clear_dedupe()
    _patch_discord_types(monkeypatch)
    guild = FakeGuild()
    member = FakeMember(guild)

    async def fake_config(_guild_id, refresh=False):
        return {"welcome_card_enabled": True, "join_welcome_channel_id": 999}

    monkeypatch.setattr(service, "get_guild_config", fake_config)
    result = asyncio.run(service.send_member_welcome_card(member))

    assert result is False
    assert guild.channel.sent == []
