from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import setup_legacy_voice_cleanup as legacy
from stoney_verify import setup_resource_reconcile as reconcile
from stoney_verify.commands_ext import public_setup_config_writer as writer
from stoney_verify.commands_ext import public_setup_defaults as defaults


class _FakeChannel:
    def __init__(
        self,
        channel_id: int,
        name: str,
        channel_type: discord.ChannelType,
        *,
        has_history: bool = False,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.type = channel_type
        self.mention = f"<#{channel_id}>"
        self.members = []
        self.deleted = False
        self.has_history = has_history

    async def delete(self, *, reason: str = "") -> None:
        assert reason
        self.deleted = True

    async def history(self, *, limit: int = 1):
        if self.has_history and limit:
            yield SimpleNamespace(id=1)


class _CreateGuild:
    def __init__(self) -> None:
        self.id = 7001
        self.voice_channels: list[_FakeChannel] = []
        self.text_channels: list[_FakeChannel] = []
        self.voice_create_count = 0
        self.text_create_count = 0
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_channels=True)
        )

    async def create_voice_channel(self, *, name, category, overwrites, reason):
        _ = category, overwrites
        assert reason
        self.voice_create_count += 1
        await asyncio.sleep(0.01)
        channel = _FakeChannel(
            8000 + self.voice_create_count,
            name,
            discord.ChannelType.voice,
        )
        self.voice_channels.append(channel)
        return channel

    async def create_text_channel(self, *, name, category, overwrites, topic, reason):
        _ = category, overwrites, topic
        assert reason
        self.text_create_count += 1
        await asyncio.sleep(0.01)
        channel = _FakeChannel(
            9000 + self.text_create_count,
            name,
            discord.ChannelType.text,
        )
        self.text_channels.append(channel)
        return channel


def test_voice_channel_creation_is_serialized_per_guild_and_name():
    async def scenario():
        guild = _CreateGuild()

        async def create_once():
            return await defaults._ensure_voice(
                guild,
                defaults.VC_VERIFY_CHANNEL_NAME,
                category=None,
                overwrites={},
                notes=[],
                created=[],
                reused=[],
            )

        first, second = await asyncio.gather(create_once(), create_once())
        assert guild.voice_create_count == 1
        assert first is second

    asyncio.run(scenario())


def test_text_channel_creation_is_serialized_per_guild_and_name():
    async def scenario():
        guild = _CreateGuild()

        async def create_once():
            return await defaults._ensure_text(
                guild,
                defaults.VC_QUEUE_CHANNEL_NAME,
                category=None,
                overwrites={},
                topic="Staff requests and updates for Voice Verify.",
                notes=[],
                created=[],
                reused=[],
            )

        first, second = await asyncio.gather(create_once(), create_once())
        assert guild.text_create_count == 1
        assert first is second

    asyncio.run(scenario())


def test_ambiguous_legacy_aliases_never_auto_delete_multiple_voice_channels(monkeypatch):
    first = _FakeChannel(101, defaults.VC_VERIFY_CHANNEL_NAME, discord.ChannelType.voice)
    second = _FakeChannel(102, defaults.VC_VERIFY_CHANNEL_NAME, discord.ChannelType.voice)

    class _Guild:
        id = 999
        me = None

        def __init__(self):
            self._channels = {101: first, 102: second}

        def get_channel(self, channel_id):
            return self._channels.get(int(channel_id))

    cfg = {
        "vc_verify_channel_id": "101",
        "voice_verify_channel_id": "102",
    }

    async def fake_cfg(*_args, **_kwargs):
        return cfg

    async def fake_audit(*_args, **_kwargs):
        return True

    cleared = {}

    async def fake_clear(_guild_id, keys, **_kwargs):
        cleared["keys"] = set(keys)
        return {}

    monkeypatch.setattr(reconcile, "get_guild_config", fake_cfg)
    monkeypatch.setattr(reconcile, "_audit_proves_bot_created", fake_audit)
    monkeypatch.setattr(writer, "clear_guild_config_keys", fake_clear)

    message = asyncio.run(reconcile.reconcile_disabled_voice_verify(_Guild()))

    assert first.deleted is False
    assert second.deleted is False
    assert "Multiple legacy Voice Verify voice mappings disagree" in message
    assert "vc_verify_channel_id" in cleared["keys"]
    assert "voice_verify_channel_id" in cleared["keys"]


def test_owner_confirmed_legacy_cleanup_removes_only_reviewed_exact_defaults(monkeypatch):
    voice = _FakeChannel(301, defaults.VC_VERIFY_CHANNEL_NAME, discord.ChannelType.voice)
    queue = _FakeChannel(302, defaults.VC_QUEUE_CHANNEL_NAME, discord.ChannelType.text)
    unrelated = _FakeChannel(303, "🎙️ Voice Verification Backup", discord.ChannelType.voice)

    class _Guild:
        id = 888

        def __init__(self):
            self.voice_channels = [voice, unrelated]
            self.text_channels = [queue]
            self._channels = {301: voice, 302: queue, 303: unrelated}

        def get_channel(self, channel_id):
            return self._channels.get(int(channel_id))

    cfg = {
        "guild_id": "888",
        "setup_choice": "custom_setup",
        "voice_verification_enabled": False,
        "vc_verify_enabled": False,
        "voice_verify_enabled": False,
        "verification_allows_voice": False,
    }

    async def fake_cfg(*_args, **_kwargs):
        return cfg

    async def fake_clear(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(legacy, "get_guild_config", fake_cfg)
    monkeypatch.setattr(writer, "clear_guild_config_keys", fake_clear)

    guild = _Guild()
    preview = asyncio.run(legacy.find_legacy_voice_cleanup_candidates(guild))
    assert preview.voice_id == 301
    assert preview.queue_id == 302

    result = asyncio.run(
        legacy.remove_legacy_voice_cleanup_candidates(
            guild,
            expected_voice_id=301,
            expected_queue_id=302,
        )
    )

    assert voice.deleted is True
    assert queue.deleted is True
    assert unrelated.deleted is False
    assert "No other channels were touched." in result


def test_legacy_cleanup_refuses_to_guess_when_exact_default_names_are_duplicated(monkeypatch):
    first = _FakeChannel(401, defaults.VC_VERIFY_CHANNEL_NAME, discord.ChannelType.voice)
    second = _FakeChannel(402, defaults.VC_VERIFY_CHANNEL_NAME, discord.ChannelType.voice)

    class _Guild:
        id = 777
        text_channels = []
        voice_channels = [first, second]

        def get_channel(self, channel_id):
            return {401: first, 402: second}.get(int(channel_id))

    cfg = {
        "guild_id": "777",
        "setup_choice": "custom_setup",
        "voice_verification_enabled": False,
    }

    async def fake_cfg(*_args, **_kwargs):
        return cfg

    monkeypatch.setattr(legacy, "get_guild_config", fake_cfg)

    preview = asyncio.run(legacy.find_legacy_voice_cleanup_candidates(_Guild()))
    assert preview.voice_id == 0
    assert any("More than one exact default" in note for note in preview.notes)
