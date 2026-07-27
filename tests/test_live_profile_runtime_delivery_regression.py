from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import LiveCardRender, LiveProfileCardRuntime, PendingTrigger, live_card_footer, parse_live_card_config


class Permissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True


class Member:
    def __init__(self, user_id, guild, *, bot=False):
        self.id = int(user_id)
        self.guild = guild
        self.bot = bot
        self.display_name = f"user-{user_id}"
        self.name = self.display_name
        self.roles = []
        self.joined_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        self.color = discord.Color.blurple()


class Sent:
    def __init__(self, message_id, author, embed):
        self.id = int(message_id)
        self.author = author
        self.embeds = [embed]
        self.deleted = False

    async def delete(self):
        self.deleted = True


class Channel:
    def __init__(self, guild):
        self.id = 55
        self.guild = guild
        self.sent = []
        self.fetch_messages = {}

    def permissions_for(self, _member):
        return Permissions()

    async def send(self, **payload):
        sent = Sent(900 + len(self.sent), self.guild.bot_user, payload["embed"])
        self.sent.append(sent)
        self.fetch_messages[sent.id] = sent
        return sent

    async def fetch_message(self, message_id):
        if int(message_id) not in self.fetch_messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing")
        return self.fetch_messages[int(message_id)]


class Guild:
    def __init__(self):
        self.id = 44
        self.bot_user = SimpleNamespace(id=999)
        self.me = Member(999, self, bot=True)
        self.cached_members = {}

    def get_member(self, user_id):
        return self.cached_members.get(int(user_id))


class Message:
    def __init__(self, guild, channel, author):
        self.id = 123
        self.guild = guild
        self.channel = channel
        self.author = author
        self.type = discord.MessageType.default
        self.webhook_id = None


class Bot:
    def __init__(self, guild):
        self.user = guild.bot_user
        self.guilds = [guild]


async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
    embed = discord.Embed(title=member.display_name)
    embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
    return LiveCardRender(embed=embed, view=None)


def patch_types(monkeypatch):
    monkeypatch.setattr(runtime_module.discord, "TextChannel", Channel)
    monkeypatch.setattr(runtime_module.discord, "Member", Member)


def config(channel_id):
    return {
        "profile_live_cards_enabled": True,
        "profile_live_card_channel_ids": [str(channel_id)],
        "profile_live_card_allowed_fields": [],
        "profile_live_card_debounce_seconds": 2,
        "profile_live_card_same_speaker_cooldown_seconds": 180,
    }


def test_message_author_posts_even_when_member_cache_is_empty(monkeypatch):
    async def scenario():
        patch_types(monkeypatch)
        guild = Guild()
        channel = Channel(guild)
        member = Member(42, guild)
        states = []

        async def get_state(*_args):
            return None

        async def save_state(*args, **kwargs):
            states.append((args, kwargs))

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=asyncio.sleep)
        await runtime._replace_card(Message(guild, channel, member), parse_live_card_config(config(channel.id)), PendingTrigger(guild.id, channel.id, member.id, 123))
        assert len(channel.sent) == 1
        assert states and states[0][1]["user_id"] == member.id

    asyncio.run(scenario())


def test_missing_stored_card_does_not_suppress_same_speaker(monkeypatch):
    async def scenario():
        patch_types(monkeypatch)
        guild = Guild()
        channel = Channel(guild)
        member = Member(42, guild)
        deleted = []

        async def get_state(*_args):
            return {
                "message_id": "777",
                "user_id": str(member.id),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        async def delete_state(*args):
            deleted.append(args)

        async def save_state(*_args, **_kwargs):
            return None

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=asyncio.sleep)
        await runtime._replace_card(Message(guild, channel, member), parse_live_card_config(config(channel.id)), PendingTrigger(guild.id, channel.id, member.id, 123))
        assert deleted == [(guild.id, channel.id)]
        assert len(channel.sent) == 1

    asyncio.run(scenario())
