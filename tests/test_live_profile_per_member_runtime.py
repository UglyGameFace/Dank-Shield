import asyncio
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import (
    LiveCardRender,
    LiveProfileCardRuntime,
    LiveCardConfig,
    is_internal_live_signature_message,
    live_card_marker_url,
)


class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True


class FakeAvatar:
    url = "https://cdn.example/avatar.png"
    key = "avatar"


class FakeMember:
    def __init__(self, user_id, guild, *, bot=False):
        self.id = int(user_id)
        self.guild = guild
        self.bot = bool(bot)
        self.name = f"user-{user_id}"
        self.display_name = self.name
        self.display_avatar = FakeAvatar()
        self.roles = []
        self.color = discord.Color.blurple()
        self.joined_at = None
        self.created_at = None


class FakeSentMessage:
    def __init__(self, message_id, author, embeds):
        self.id = int(message_id)
        self.author = author
        self.embeds = list(embeds)
        self.attachments = []
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, channel_id, guild):
        self.id = int(channel_id)
        self.guild = guild
        self.sent = []
        self.preloaded = []
        self.fetch_messages = {}

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **payload):
        message = FakeSentMessage(
            1000 + len(self.sent),
            self.guild.bot_user,
            [payload["embed"]],
        )
        self.sent.append(message)
        self.fetch_messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        message = self.fetch_messages.get(int(message_id))
        if message is None or message.deleted:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="missing"),
                "missing",
            )
        return message

    async def history(self, *, limit):
        visible = [
            message
            for message in [*self.sent, *self.preloaded]
            if not message.deleted
        ]
        for message in list(reversed(visible))[:limit]:
            yield message


class FakeGuild:
    def __init__(self, guild_id, bot_user):
        self.id = int(guild_id)
        self.name = f"Guild {guild_id}"
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
        self.user = SimpleNamespace(id=999, bot=True)
        self.guilds = []


class FakeIncomingMessage:
    def __init__(self, message_id, guild, channel, author, *, webhook_id=None):
        self.id = int(message_id)
        self.guild = guild
        self.channel = channel
        self.author = author
        self.webhook_id = webhook_id
        self.type = discord.MessageType.default


async def _wait_for_workers(runtime):
    while runtime._workers:
        tasks = list(runtime._workers.values())
        await asyncio.gather(*tasks, return_exceptions=True)


def _patch_types(monkeypatch):
    monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime_module.discord, "Member", FakeMember)


def _config(channel_id):
    return LiveCardConfig(
        enabled=True,
        channel_ids=frozenset({int(channel_id)}),
        allowed_fields=frozenset({"roles", "account_dates", "platforms"}),
        debounce_seconds=0.0,
        replacement_cooldown_seconds=0.01,
        same_speaker_cooldown_seconds=0.05,
    )


def _renderer(seen):
    async def render(member, allowed, *, trigger_message_id, require_live_enabled=True):
        del allowed, require_live_enabled
        seen.append((member.id, int(trigger_message_id)))
        embed = discord.Embed(
            title=f"Profile {member.id}",
            url=live_card_marker_url(member.id, trigger_message_id),
        )
        return LiveCardRender(embed=embed, view=None)

    return render


def _install_runtime_dependencies(monkeypatch, channel_id):
    async def get_config(_guild_id):
        return {"enabled": True}

    async def delete_state(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
    monkeypatch.setattr(
        runtime_module,
        "parse_live_card_config",
        lambda _raw: _config(channel_id),
    )
    monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)


def test_same_member_burst_leaves_one_latest_visible_card(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(1, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(10)
        member = guild.add_member(101)
        seen = []
        _install_runtime_dependencies(monkeypatch, channel.id)

        runtime = LiveProfileCardRuntime(bot, renderer=_renderer(seen), sleep=asyncio.sleep)
        await runtime.on_message(FakeIncomingMessage(1, guild, channel, member))
        await asyncio.sleep(0.002)
        await runtime.on_message(FakeIncomingMessage(2, guild, channel, member))
        await runtime.on_message(FakeIncomingMessage(3, guild, channel, member))
        await _wait_for_workers(runtime)

        visible = [message for message in channel.sent if not message.deleted]
        assert len(visible) == 1
        assert runtime_module.parse_live_card_footer(visible[0]) == (member.id, 3)
        assert seen[-1] == (member.id, 3)

    asyncio.run(scenario())


def test_different_members_keep_independent_public_cards(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(2, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(20)
        first = guild.add_member(201)
        second = guild.add_member(202)
        seen = []
        _install_runtime_dependencies(monkeypatch, channel.id)

        runtime = LiveProfileCardRuntime(bot, renderer=_renderer(seen), sleep=asyncio.sleep)
        await runtime.on_message(FakeIncomingMessage(11, guild, channel, first))
        await _wait_for_workers(runtime)
        await runtime.on_message(FakeIncomingMessage(12, guild, channel, second))
        await _wait_for_workers(runtime)

        visible = [message for message in channel.sent if not message.deleted]
        assert len(visible) == 2
        owners = {
            runtime_module.parse_live_card_footer(message)[0]
            for message in visible
        }
        assert owners == {first.id, second.id}

    asyncio.run(scenario())


def test_lazy_warmup_removes_existing_duplicate_for_only_that_member(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(3, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(30)
        member = guild.add_member(301)
        other = guild.add_member(302)
        _install_runtime_dependencies(monkeypatch, channel.id)

        old = FakeSentMessage(
            700,
            bot.user,
            [discord.Embed(url=live_card_marker_url(member.id, 1))],
        )
        newer = FakeSentMessage(
            701,
            bot.user,
            [discord.Embed(url=live_card_marker_url(member.id, 2))],
        )
        other_card = FakeSentMessage(
            702,
            bot.user,
            [discord.Embed(url=live_card_marker_url(other.id, 5))],
        )
        channel.preloaded.extend([old, newer, other_card])
        channel.fetch_messages.update({700: old, 701: newer, 702: other_card})

        runtime = LiveProfileCardRuntime(bot, renderer=_renderer([]), sleep=asyncio.sleep)
        await runtime.on_message(FakeIncomingMessage(3, guild, channel, member))
        await _wait_for_workers(runtime)

        assert old.deleted is True
        assert other_card.deleted is False
        visible_member_cards = [
            message
            for message in [*channel.preloaded, *channel.sent]
            if not message.deleted
            and runtime_module.parse_live_card_footer(message)
            and runtime_module.parse_live_card_footer(message)[0] == member.id
        ]
        assert len(visible_member_cards) == 1

    asyncio.run(scenario())


def test_bot_and_webhook_messages_never_create_signature_workers(monkeypatch):
    async def scenario():
        _patch_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(4, bot.user)
        channel = guild.add_channel(40)
        human = guild.add_member(401)
        bot_member = guild.add_member(402, bot=True)
        _install_runtime_dependencies(monkeypatch, channel.id)
        runtime = LiveProfileCardRuntime(bot, renderer=_renderer([]), sleep=asyncio.sleep)

        await runtime.on_message(FakeIncomingMessage(1, guild, channel, bot_member))
        await runtime.on_message(
            FakeIncomingMessage(2, guild, channel, human, webhook_id=77)
        )
        assert runtime._workers == {}

    asyncio.run(scenario())


def test_internal_signature_classifier_requires_bot_author_and_owned_marker():
    bot = SimpleNamespace(id=999, bot=True)
    human = SimpleNamespace(id=111, bot=False)
    marked = FakeSentMessage(
        1,
        bot,
        [discord.Embed(url=live_card_marker_url(111, 222))],
    )
    human_copy = FakeSentMessage(
        2,
        human,
        [discord.Embed(url=live_card_marker_url(111, 222))],
    )
    unrelated_bot = FakeSentMessage(3, bot, [discord.Embed(title="normal bot output")])

    assert is_internal_live_signature_message(marked, bot_user_id=bot.id) is True
    assert is_internal_live_signature_message(human_copy, bot_user_id=bot.id) is False
    assert is_internal_live_signature_message(unrelated_bot, bot_user_id=bot.id) is False
