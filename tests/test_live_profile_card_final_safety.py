import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import LiveProfileCardRuntime, live_card_footer, parse_live_card_config
from stoney_verify.profile_card_service import (
    InvalidPlatformProfile,
    display_profile_username,
    normalize_platform_url,
)


ROOT = Path(__file__).resolve().parents[1]


class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True


class FakeMember:
    def __init__(self, user_id, guild, *, bot=False):
        self.id = int(user_id)
        self.guild = guild
        self.bot = bool(bot)
        self.display_name = f"user-{user_id}"
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        self.roles = []
        self.joined_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)


class FakeStoredMessage:
    def __init__(self, message_id, author, user_id, trigger_id):
        self.id = int(message_id)
        self.author = author
        self.deleted = False
        embed = discord.Embed(title="Stored")
        embed.set_footer(text=live_card_footer(user_id, trigger_id))
        self.embeds = [embed]

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, channel_id, guild):
        self.id = int(channel_id)
        self.guild = guild
        self.fetch_messages = {}
        self.history_messages = []

    def permissions_for(self, _member):
        return FakePermissions()

    async def fetch_message(self, message_id):
        message = self.fetch_messages.get(int(message_id))
        if message is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing")
        return message

    async def history(self, *, limit):
        for message in self.history_messages[:limit]:
            yield message


class FakeGuild:
    def __init__(self, guild_id, bot_user):
        self.id = int(guild_id)
        self.bot_user = bot_user
        self.me = FakeMember(bot_user.id, self, bot=True)
        self.members = {}
        self.channels = {}

    def add_member(self, user_id, *, bot=False):
        member = FakeMember(user_id, self, bot=bot)
        self.members[member.id] = member
        return member

    def add_channel(self, channel_id):
        channel = FakeChannel(channel_id, self)
        self.channels[channel.id] = channel
        return channel

    def get_member(self, user_id):
        return self.members.get(int(user_id))

    def get_channel(self, channel_id):
        return self.channels.get(int(channel_id))


class FakeBot:
    def __init__(self):
        self.user = SimpleNamespace(id=999)
        self.guilds = []

    def get_guild(self, guild_id):
        for guild in self.guilds:
            if int(guild.id) == int(guild_id):
                return guild
        return None


class FakeIncomingMessage:
    def __init__(self, message_id, guild, channel, author):
        self.id = int(message_id)
        self.guild = guild
        self.channel = channel
        self.author = author
        self.webhook_id = None
        self.type = discord.MessageType.default


def _patch_types(monkeypatch):
    monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime_module.discord, "Member", FakeMember)


def test_platform_username_display_cannot_create_markdown_links_or_bidi_spoofing():
    assert display_profile_username("[click me](evil.example)") == "[click me](evil.example)"
    assert "`" not in display_profile_username("name`link")
    assert "\u202e" not in display_profile_username("safe\u202eevil")


def test_malformed_profile_ports_are_user_safe_validation_errors():
    with pytest.raises(InvalidPlatformProfile, match="invalid port"):
        normalize_platform_url("steam", "https://steamcommunity.com:notaport/id/example")


def test_replacement_cooldown_defaults_prevent_alternating_speaker_spam():
    config = parse_live_card_config(
        {
            "profile_live_cards_enabled": True,
            "profile_live_card_channel_ids": ["123456"],
        }
    )
    assert config.replacement_cooldown_seconds == 30.0
    assert config.same_speaker_cooldown_seconds == 180.0


def test_alternating_speaker_is_delayed_until_channel_replacement_cooldown(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        clock = iter([100.0, 100.0])
        monkeypatch.setattr(runtime_module, "monotonic", lambda: next(clock))
        bot = FakeBot()
        guild = FakeGuild(1, bot.user)
        channel = guild.add_channel(10)
        member = guild.add_member(202)
        bot.guilds = [guild]

        async def config(_guild_id):
            return {
                "profile_live_cards_enabled": True,
                "profile_live_card_channel_ids": [str(channel.id)],
                "profile_live_card_debounce_seconds": 4,
                "profile_live_card_replacement_cooldown_seconds": 30,
                "profile_live_card_same_speaker_cooldown_seconds": 180,
            }

        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        runtime = LiveProfileCardRuntime(bot)
        runtime._last_posted[(guild.id, channel.id)] = (101, 90.0)
        await runtime.on_message(FakeIncomingMessage(1, guild, channel, member))
        trigger = runtime._latest[(guild.id, channel.id)]
        assert trigger.user_id == member.id
        assert trigger.delay_seconds == 20.0
        pending = runtime._pending[(guild.id, channel.id)]
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_reconcile_validates_persisted_card_even_outside_history_window(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(2, bot.user)
        channel = guild.add_channel(20)
        bot.guilds = [guild]
        stored = FakeStoredMessage(50, bot.user, 303, 9)
        channel.fetch_messages[stored.id] = stored
        channel.history_messages = []
        saved = []

        async def save_state(guild_id, channel_id, **payload):
            saved.append((guild_id, channel_id, payload))

        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(bot)
        await runtime._reconcile_channel(
            channel,
            {
                "guild_id": str(guild.id),
                "channel_id": str(channel.id),
                "message_id": str(stored.id),
                "user_id": "303",
                "trigger_message_id": "9",
            },
        )

        assert saved[0][2]["message_id"] == stored.id
        assert saved[0][2]["user_id"] == 303
        assert stored.deleted is False

    asyncio.run(scenario())


def test_user_cleanup_uses_indexed_user_query_not_global_state_scan():
    service = (ROOT / "stoney_verify/profile_card_service.py").read_text(encoding="utf-8")
    runtime = (ROOT / "stoney_verify/profile_card_runtime_core.py").read_text(encoding="utf-8")
    migration = (ROOT / "supabase/migrations/20260725_live_profile_cards.sql").read_text(encoding="utf-8")
    assert "async def list_live_card_states_for_user" in service
    assert '.eq("user_id", str(uid))' in service
    helper = runtime.split("async def _remove_user_card_states", 1)[1].split(
        "async def remove_user_cards", 1
    )[0]
    assert "list_live_card_states_for_user(" in helper
    assert "list_live_card_states()" not in helper
    assert "idx_dank_live_profile_cards_user" in migration


def test_member_and_deleted_channel_cleanup_are_registered_once():
    commands = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
    runtime = (ROOT / "stoney_verify/profile_card_runtime_core.py").read_text(encoding="utf-8")
    assert commands.count('bot.add_listener(runtime.on_member_remove, "on_member_remove")') == 1
    assert commands.count('bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")') == 1
    assert "async def on_member_remove" in runtime
    assert "async def on_guild_channel_delete" in runtime
    assert "upsert_guild_config(" in runtime
