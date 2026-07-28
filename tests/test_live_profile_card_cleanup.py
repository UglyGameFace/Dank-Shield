import asyncio
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import LiveProfileCardRuntime


class FakeMember:
    def __init__(self, user_id, guild=None, *, bot=False):
        self.id = int(user_id)
        self.guild = guild
        self.bot = bool(bot)


class FakeChannel:
    def __init__(self, channel_id, guild):
        self.id = int(channel_id)
        self.guild = guild


class FakeGuild:
    def __init__(self, guild_id, bot_user):
        self.id = int(guild_id)
        self.bot_user = bot_user
        self.me = FakeMember(bot_user.id, self, bot=True)
        self.channels = {}

    def add_channel(self, channel_id):
        channel = FakeChannel(channel_id, self)
        self.channels[channel.id] = channel
        return channel

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


def _patch_types(monkeypatch):
    monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime_module.discord, "Member", FakeMember)


def test_disable_channel_keeps_state_when_discord_delete_fails(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(1, bot.user)
        channel = guild.add_channel(10)
        bot.guilds = [guild]
        deleted_states = []
        state = {"message_id": "123", "user_id": "7"}

        async def list_states(_guild_id, _channel_id):
            return [state]

        async def delete_state(*args):
            deleted_states.append(args)

        monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", list_states)
        monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
        runtime = LiveProfileCardRuntime(bot)

        async def fail_delete(_channel, _message_id):
            return False

        runtime._delete_stored_message = fail_delete
        await runtime.disable_channel(guild, channel)
        assert deleted_states == []

    asyncio.run(scenario())


def test_disable_channel_removes_scoped_state_after_verified_delete(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(2, bot.user)
        channel = guild.add_channel(20)
        bot.guilds = [guild]
        deleted_states = []
        state = {"message_id": "456", "user_id": "8"}

        async def list_states(_guild_id, _channel_id):
            return [state]

        async def delete_state(*args):
            deleted_states.append(args)

        monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", list_states)
        monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
        runtime = LiveProfileCardRuntime(bot)

        async def successful_delete(_channel, _message_id):
            return True

        runtime._delete_stored_message = successful_delete
        await runtime.disable_channel(guild, channel)
        assert deleted_states == [(guild.id, channel.id, 8)]

    asyncio.run(scenario())


def test_invalidate_guild_cards_cleans_all_configured_channels(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(3, bot.user)
        guild.add_channel(30)
        guild.add_channel(31)
        bot.guilds = [guild]
        cleaned = []

        async def config(_guild_id):
            return {
                "profile_live_cards_enabled": True,
                "profile_live_card_channel_ids": ["30", "31"],
            }

        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        runtime = LiveProfileCardRuntime(bot)

        async def remove(_guild, channel_id, *, cancel_pending=True):
            cleaned.append((channel_id, cancel_pending))
            return True

        runtime._remove_channel_card_state = remove
        await runtime.invalidate_guild_cards(guild)
        assert cleaned == [(30, True), (31, True)]

    asyncio.run(scenario())


def test_reconcile_disabled_channel_deletes_verified_card_before_state(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(4, bot.user)
        channel = guild.add_channel(40)
        bot.guilds = [guild]
        deleted_messages = []
        deleted_states = []
        state = {
            "guild_id": str(guild.id),
            "channel_id": str(channel.id),
            "message_id": "789",
            "user_id": "9",
        }

        async def list_all_states():
            return [state]

        async def list_channel_states(_guild_id, _channel_id):
            return [state]

        async def config(_guild_id):
            return {
                "profile_live_cards_enabled": False,
                "profile_live_card_channel_ids": [str(channel.id)],
            }

        async def delete_state(*args):
            deleted_states.append(args)

        monkeypatch.setattr(runtime_module, "list_live_card_states", list_all_states)
        monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", list_channel_states)
        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
        runtime = LiveProfileCardRuntime(bot)

        async def verified_delete(_channel, message_id):
            deleted_messages.append(message_id)
            return True

        runtime._delete_stored_message = verified_delete
        await runtime.reconcile()
        assert deleted_messages == [789]
        assert deleted_states == [(guild.id, channel.id, 9)]

    asyncio.run(scenario())


def test_reconcile_keeps_state_when_disabled_card_cannot_be_deleted(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(5, bot.user)
        channel = guild.add_channel(50)
        bot.guilds = [guild]
        deleted_states = []
        state = {
            "guild_id": str(guild.id),
            "channel_id": str(channel.id),
            "message_id": "999",
            "user_id": "10",
        }

        async def list_all_states():
            return [state]

        async def list_channel_states(_guild_id, _channel_id):
            return [state]

        async def config(_guild_id):
            return {
                "profile_live_cards_enabled": False,
                "profile_live_card_channel_ids": [str(channel.id)],
            }

        async def delete_state(*args):
            deleted_states.append(args)

        monkeypatch.setattr(runtime_module, "list_live_card_states", list_all_states)
        monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", list_channel_states)
        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
        runtime = LiveProfileCardRuntime(bot)

        async def failed_delete(_channel, _message_id):
            return False

        runtime._delete_stored_message = failed_delete
        await runtime.reconcile()
        assert deleted_states == []

    asyncio.run(scenario())
