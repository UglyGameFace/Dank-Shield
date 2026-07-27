from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import (
    LiveCardRender,
    LiveProfileCardRuntime,
    PendingTrigger,
    live_card_marker_url,
    parse_live_card_config,
)


class Permissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True


class Member:
    def __init__(self, user_id: int, guild: "Guild", *, bot: bool = False) -> None:
        self.id = int(user_id)
        self.guild = guild
        self.bot = bool(bot)
        self.display_name = f"user-{user_id}"
        self.name = self.display_name
        self.roles = []
        self.joined_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)
        self.display_avatar = SimpleNamespace(url=f"https://cdn.example/{user_id}.png")
        self.color = discord.Color.blurple()


class Sent:
    def __init__(self, message_id: int, author: object, embed: discord.Embed) -> None:
        self.id = int(message_id)
        self.author = author
        self.embeds = [embed]
        self.attachments = []
        self.deleted = False
        self.fail_delete = False

    async def delete(self) -> None:
        if self.fail_delete:
            raise RuntimeError("delete blocked")
        self.deleted = True


class Channel:
    def __init__(self, channel_id: int, guild: "Guild") -> None:
        self.id = int(channel_id)
        self.guild = guild
        self.sent: list[Sent] = []
        self.fetch_messages: dict[int, Sent] = {}
        self.history_messages: list[Sent] = []
        self.send_started: asyncio.Event | None = None
        self.send_release: asyncio.Event | None = None

    def permissions_for(self, _member: object) -> Permissions:
        return Permissions()

    async def send(self, **payload):
        if self.send_started is not None:
            self.send_started.set()
        if self.send_release is not None:
            await self.send_release.wait()
        sent = Sent(1000 + len(self.sent), self.guild.bot_user, payload["embed"])
        self.sent.append(sent)
        self.fetch_messages[sent.id] = sent
        return sent

    async def fetch_message(self, message_id: int):
        message = self.fetch_messages.get(int(message_id))
        if message is None or message.deleted:
            raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing")
        return message

    async def history(self, *, limit: int):
        for message in self.history_messages[:limit]:
            yield message


class Guild:
    def __init__(self, guild_id: int = 1) -> None:
        self.id = int(guild_id)
        self.name = f"Guild {guild_id}"
        self.bot_user = SimpleNamespace(id=999)
        self.me = Member(999, self, bot=True)
        self.members: dict[int, Member] = {}
        self.channels: dict[int, Channel] = {}

    def add_member(self, user_id: int) -> Member:
        member = Member(user_id, self)
        self.members[member.id] = member
        return member

    def add_channel(self, channel_id: int) -> Channel:
        channel = Channel(channel_id, self)
        self.channels[channel.id] = channel
        return channel

    def get_member(self, user_id: int):
        return self.members.get(int(user_id))

    def get_channel(self, channel_id: int):
        return self.channels.get(int(channel_id))


class Bot:
    def __init__(self, guild: Guild) -> None:
        self.user = guild.bot_user
        self.guilds = [guild]

    def get_guild(self, guild_id: int):
        return self.guilds[0] if int(self.guilds[0].id) == int(guild_id) else None


class Incoming:
    def __init__(self, message_id: int, guild: Guild, channel: Channel, author: Member) -> None:
        self.id = int(message_id)
        self.guild = guild
        self.channel = channel
        self.author = author
        self.type = discord.MessageType.default
        self.webhook_id = None


def config(channel_id: int) -> dict[str, object]:
    return {
        "profile_live_cards_enabled": True,
        "profile_live_card_channel_ids": [str(channel_id)],
        "profile_live_card_allowed_fields": ["roles", "account_dates", "platforms"],
    }


def rendered(member: Member, trigger_message_id: int) -> LiveCardRender:
    embed = discord.Embed(
        title=member.display_name,
        url=live_card_marker_url(member.id, trigger_message_id),
    )
    return LiveCardRender(embed=embed, view=None)


def patch_types(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module.discord, "TextChannel", Channel)
    monkeypatch.setattr(runtime_module.discord, "Member", Member)


def patch_storage(monkeypatch, channel: Channel):
    states: list[dict[str, object]] = []

    async def get_config(_guild_id: int):
        return config(channel.id)

    async def list_states(_guild_id: int, _channel_id: int):
        return list(states)

    async def delete_state(_guild_id: int, _channel_id: int, _user_id=None):
        states.clear()

    async def save_state(guild_id: int, channel_id: int, **payload):
        states.clear()
        states.append({"guild_id": guild_id, "channel_id": channel_id, **payload})

    monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
    monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", list_states)
    monkeypatch.setattr(runtime_module, "delete_live_card_state", delete_state)
    monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
    return states


async def drain(runtime: LiveProfileCardRuntime) -> None:
    for _attempt in range(12):
        tasks = [task for task in runtime._pending.values() if task is not None and not task.done()]
        if not tasks:
            await asyncio.sleep(0)
            tasks = [task for task in runtime._pending.values() if task is not None and not task.done()]
            if not tasks:
                return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def test_three_speakers_in_one_burst_produce_only_latest_signature(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(10)
        first = guild.add_member(101)
        second = guild.add_member(202)
        third = guild.add_member(303)
        states = patch_storage(monkeypatch, channel)
        seen: list[tuple[int, int]] = []

        async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
            seen.append((member.id, trigger_message_id))
            return rendered(member, trigger_message_id)

        async def no_wait(_seconds: float):
            return None

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, first))
        await runtime.on_message(Incoming(2, guild, channel, second))
        await runtime.on_message(Incoming(3, guild, channel, third))
        await drain(runtime)

        assert seen == [(third.id, 3)]
        assert len(channel.sent) == 1
        assert channel.sent[0].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].user_id == third.id
        assert len(states) == 1
        assert states[0]["user_id"] == third.id
        assert all(len(key) == 2 for key in runtime._current_cards)

    asyncio.run(scenario())


def test_new_speaker_during_slow_render_discards_stale_result(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(2)
        channel = guild.add_channel(20)
        first = guild.add_member(401)
        second = guild.add_member(402)
        patch_storage(monkeypatch, channel)
        render_started = asyncio.Event()
        release_render = asyncio.Event()
        seen: list[tuple[int, int]] = []

        async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
            seen.append((member.id, trigger_message_id))
            if member.id == first.id:
                render_started.set()
                await release_render.wait()
            return rendered(member, trigger_message_id)

        async def no_wait(_seconds: float):
            return None

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, first))
        await render_started.wait()
        await runtime.on_message(Incoming(2, guild, channel, second))
        release_render.set()
        await drain(runtime)

        assert seen == [(first.id, 1), (second.id, 2)]
        assert len(channel.sent) == 1
        assert runtime._current_cards[(guild.id, channel.id)].user_id == second.id

    asyncio.run(scenario())


def test_existing_member_stack_is_collapsed_before_replacement(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(3)
        channel = guild.add_channel(30)
        first = guild.add_member(501)
        second = guild.add_member(502)
        latest = guild.add_member(503)

        old_a = Sent(700, guild.bot_user, rendered(first, 10).embed)
        old_b = Sent(800, guild.bot_user, rendered(second, 11).embed)
        channel.fetch_messages = {old_a.id: old_a, old_b.id: old_b}
        channel.history_messages = [old_b, old_a]
        states = patch_storage(monkeypatch, channel)
        states.extend(
            [
                {"message_id": old_a.id, "user_id": first.id, "trigger_message_id": 10},
                {"message_id": old_b.id, "user_id": second.id, "trigger_message_id": 11},
            ]
        )

        async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
            return rendered(member, trigger_message_id)

        async def no_wait(_seconds: float):
            return None

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=no_wait)
        await runtime.on_message(Incoming(12, guild, channel, latest))
        await drain(runtime)

        assert old_a.deleted is True
        assert old_b.deleted is True
        assert len(channel.sent) == 1
        assert channel.sent[0].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].user_id == latest.id
        assert len(states) == 1
        assert states[0]["user_id"] == latest.id

    asyncio.run(scenario())


def test_failed_old_card_delete_blocks_new_public_post(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(4)
        channel = guild.add_channel(40)
        old_member = guild.add_member(601)
        new_member = guild.add_member(602)
        patch_storage(monkeypatch, channel)
        old = Sent(900, guild.bot_user, rendered(old_member, 1).embed)
        old.fail_delete = True

        async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
            return rendered(member, trigger_message_id)

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=asyncio.sleep)
        key = (guild.id, channel.id)
        runtime._current_cards[key] = runtime_module._CurrentCard(
            message_id=old.id,
            user_id=old_member.id,
            trigger_message_id=1,
            message=old,
        )
        trigger = PendingTrigger(guild.id, channel.id, new_member.id, 2)
        runtime._latest[key] = trigger
        await runtime._replace_card(
            Incoming(2, guild, channel, new_member),
            parse_live_card_config(config(channel.id)),
            trigger,
        )

        assert channel.sent == []
        assert old.deleted is False

    asyncio.run(scenario())


def test_message_during_send_removes_stale_post_and_worker_posts_latest(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(5)
        channel = guild.add_channel(50)
        first = guild.add_member(701)
        second = guild.add_member(702)
        patch_storage(monkeypatch, channel)
        channel.send_started = asyncio.Event()
        channel.send_release = asyncio.Event()

        async def renderer(member, _allowed, *, trigger_message_id, require_live_enabled=True):
            return rendered(member, trigger_message_id)

        async def no_wait(_seconds: float):
            return None

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer, sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, first))
        await channel.send_started.wait()
        await runtime.on_message(Incoming(2, guild, channel, second))
        channel.send_release.set()
        await drain(runtime)

        assert len(channel.sent) == 2
        assert channel.sent[0].deleted is True
        assert channel.sent[1].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].user_id == second.id

    asyncio.run(scenario())


def test_runtime_source_forbids_per_member_channel_ownership_and_text_duplication():
    source = (Path(__file__).resolve().parents[1] / "stoney_verify/profile_card_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_ChannelKey = tuple[int, int]" in source
    assert "_MemberCardKey" not in source
    assert "one_per_channel" in source
    assert "Connected profiles" not in source
    assert "description=_platform_link_line" not in source
    replace = source.split("async def _replace_card", 1)[1].split("async def reconcile", 1)[0]
    assert replace.index("await self._delete_verified_card") < replace.index("await channel.send")
    assert replace.count("if not self._is_latest(key, trigger):") >= 4
