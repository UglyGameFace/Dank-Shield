from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import (
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_REPLACEMENT_COOLDOWN_SECONDS,
    DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS,
    LiveCardRender,
    LiveProfileCardRuntime,
    live_card_marker_url,
    parse_live_card_config,
)
from stoney_verify.profile_card_service import ProfileStorageUnavailable


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

    async def delete(self) -> None:
        self.deleted = True


class Channel:
    def __init__(self, channel_id: int, guild: "Guild") -> None:
        self.id = int(channel_id)
        self.guild = guild
        self.sent: list[Sent] = []
        self.fetch_messages: dict[int, Sent] = {}

    def permissions_for(self, _member: object) -> Permissions:
        return Permissions()

    async def send(self, **payload):
        sent = Sent(1000 + len(self.sent), self.guild.bot_user, payload["embed"])
        self.sent.append(sent)
        self.fetch_messages[sent.id] = sent
        return sent

    async def fetch_message(self, message_id: int):
        stored = self.fetch_messages.get(int(message_id))
        if stored is None or stored.deleted:
            raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing")
        return stored


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


def patch_types(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module.discord, "TextChannel", Channel)
    monkeypatch.setattr(runtime_module.discord, "Member", Member)


def config(channel_id: int) -> dict[str, object]:
    return {
        "profile_live_cards_enabled": True,
        "profile_live_card_channel_ids": [str(channel_id)],
        "profile_live_card_debounce_seconds": 4,
        "profile_live_card_replacement_cooldown_seconds": 30,
        "profile_live_card_same_speaker_cooldown_seconds": 180,
    }


def renderer(seen: list[tuple[int, int]]):
    async def render(member, _allowed, *, trigger_message_id, require_live_enabled=True):
        seen.append((int(member.id), int(trigger_message_id)))
        embed = discord.Embed(
            title=member.display_name,
            url=live_card_marker_url(member.id, trigger_message_id),
        )
        return LiveCardRender(embed=embed, view=None)

    return render


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


def install_storage(monkeypatch, channel: Channel):
    states: list[dict[str, object]] = []
    reads = {"count": 0}

    async def get_config(guild_id: int, **kwargs):
        assert guild_id == channel.guild.id
        assert kwargs == {}
        return config(channel.id)

    async def list_states(_guild_id: int, _channel_id: int):
        reads["count"] += 1
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
    return states, reads


def test_legacy_delay_values_migrate_to_quiet_window_safety_policy():
    parsed = parse_live_card_config(config(123))

    assert parsed.debounce_seconds == DEFAULT_DEBOUNCE_SECONDS
    assert parsed.replacement_cooldown_seconds == DEFAULT_REPLACEMENT_COOLDOWN_SECONDS
    assert parsed.same_speaker_cooldown_seconds == DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS
    assert 0.5 <= parsed.debounce_seconds <= 2.0
    assert parsed.replacement_cooldown_seconds == parsed.debounce_seconds
    assert parsed.same_speaker_cooldown_seconds <= 2.0


def test_first_message_uses_cached_config_and_waits_for_quiet_window(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(10)
        member = guild.add_member(101)
        _states, reads = install_storage(monkeypatch, channel)
        sleep_calls: list[float] = []
        seen: list[tuple[int, int]] = []

        async def no_wait(seconds: float):
            sleep_calls.append(seconds)

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, member))
        await drain(runtime)

        assert seen == [(member.id, 1)]
        assert len(channel.sent) == 1
        assert sleep_calls and sleep_calls[0] > 0
        assert reads["count"] == 1
        assert runtime._latest_messages == {}
        assert runtime._latest_configs == {}

    asyncio.run(scenario())


def test_warm_channel_replacement_does_not_reread_durable_state(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(2)
        channel = guild.add_channel(20)
        first = guild.add_member(201)
        second = guild.add_member(202)
        _states, reads = install_storage(monkeypatch, channel)

        async def no_wait(_seconds: float):
            return None

        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer([]), sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, first))
        await drain(runtime)
        await runtime.on_message(Incoming(2, guild, channel, second))
        await drain(runtime)

        assert reads["count"] == 1
        assert len(channel.sent) == 2
        assert channel.sent[0].deleted is True
        assert channel.sent[1].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].user_id == second.id

    asyncio.run(scenario())


def test_unchanged_signature_render_is_reused_from_bounded_cache(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        runtime_module._SIGNATURE_CACHE.clear()
        guild = Guild(88)
        member = guild.add_member(880)
        renders = 0

        async def settings(_guild_id: int, _user_id: int):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_server_roles": False,
                    "show_profile_tags": False,
                    "show_account_dates": False,
                    "show_platforms": False,
                },
                "platforms": {},
            }

        async def get_config(_guild_id: int):
            return {}

        async def render_image(_member, **_kwargs):
            nonlocal renders
            renders += 1
            return b"same-image"

        monkeypatch.setattr(runtime_module, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "render_member_profile_signature", render_image)

        first = await runtime_module.render_live_profile_card(member, set(), trigger_message_id=1)
        second = await runtime_module.render_live_profile_card(member, set(), trigger_message_id=2)

        assert first is not None and second is not None
        assert renders == 1
        assert len(runtime_module._SIGNATURE_CACHE) == 1
        assert runtime_module._SIGNATURE_CACHE_MAX_ITEMS == 512
        assert runtime_module._SIGNATURE_CACHE_TTL_SECONDS == 300.0
        assert first.embed.url != second.embed.url
        assert first.embed.description is None

    asyncio.run(scenario())


def test_cold_state_verification_failure_never_creates_a_duplicate(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild(99)
        channel = guild.add_channel(990)
        member = guild.add_member(991)
        seen: list[tuple[int, int]] = []

        async def get_config(_guild_id: int):
            return config(channel.id)

        async def unavailable(*_args):
            raise ProfileStorageUnavailable("offline")

        async def no_wait(_seconds: float):
            return None

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "list_live_card_states_for_channel", unavailable)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=no_wait)
        await runtime.on_message(Incoming(1, guild, channel, member))
        await drain(runtime)

        assert seen == [(member.id, 1)]
        assert channel.sent == []

    asyncio.run(scenario())
