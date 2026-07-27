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
    live_card_footer,
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
        if stored is None:
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
        embed = discord.Embed(title=member.display_name)
        embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
        return LiveCardRender(embed=embed, view=None)

    return render


async def drain(runtime: LiveProfileCardRuntime) -> None:
    for _attempt in range(8):
        tasks = {
            task
            for task in [*runtime._leading.values(), *runtime._pending.values()]
            if task is not None and not task.done()
        }
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def test_legacy_delay_values_migrate_to_responsive_runtime_policy():
    parsed = parse_live_card_config(config(123))
    assert parsed.debounce_seconds == DEFAULT_DEBOUNCE_SECONDS == 0.0
    assert parsed.replacement_cooldown_seconds == DEFAULT_REPLACEMENT_COOLDOWN_SECONDS
    assert parsed.same_speaker_cooldown_seconds == DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS
    assert parsed.replacement_cooldown_seconds < 1.0
    assert parsed.same_speaker_cooldown_seconds <= 2.0


def test_first_message_posts_without_timer_sleep_or_forced_config_refresh(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(10)
        member = guild.add_member(101)
        seen: list[tuple[int, int]] = []
        state_reads = 0
        sleep_calls: list[float] = []

        async def get_config(guild_id: int, **kwargs):
            assert guild_id == guild.id
            assert kwargs == {}
            return config(channel.id)

        async def get_state(*_args):
            nonlocal state_reads
            state_reads += 1
            return None

        async def save_state(*_args, **_kwargs):
            return None

        async def sleep(seconds: float):
            sleep_calls.append(seconds)

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=sleep)

        await runtime.on_message(Incoming(1, guild, channel, member))
        await drain(runtime)

        assert seen == [(member.id, 1)]
        assert len(channel.sent) == 1
        assert sleep_calls == []
        assert state_reads == 1

    asyncio.run(scenario())


def test_same_speaker_back_to_back_messages_keep_existing_signature(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(20)
        member = guild.add_member(202)
        seen: list[tuple[int, int]] = []

        async def get_config(_guild_id: int):
            return config(channel.id)

        async def get_state(*_args):
            return None

        async def save_state(*_args, **_kwargs):
            return None

        async def no_wait(_seconds: float):
            return None

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=no_wait)

        await runtime.on_message(Incoming(1, guild, channel, member))
        await drain(runtime)
        await runtime.on_message(Incoming(2, guild, channel, member))
        await runtime.on_message(Incoming(3, guild, channel, member))
        await drain(runtime)

        assert seen == [(member.id, 1)]
        assert len(channel.sent) == 1
        assert channel.sent[0].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].trigger_message_id == 1

    asyncio.run(scenario())


def test_rapid_speaker_changes_collapse_to_latest_trailing_signature(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(30)
        first = guild.add_member(301)
        second = guild.add_member(302)
        third = guild.add_member(303)
        seen: list[tuple[int, int]] = []
        sleep_gates: list[asyncio.Event] = []

        async def get_config(_guild_id: int):
            return config(channel.id)

        async def get_state(*_args):
            return None

        async def save_state(*_args, **_kwargs):
            return None

        async def gated_sleep(_seconds: float):
            gate = asyncio.Event()
            sleep_gates.append(gate)
            await gate.wait()

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=gated_sleep)

        await runtime.on_message(Incoming(1, guild, channel, first))
        await drain(runtime)
        await runtime.on_message(Incoming(2, guild, channel, second))
        await asyncio.sleep(0)
        await runtime.on_message(Incoming(3, guild, channel, third))
        await asyncio.sleep(0)

        assert sleep_gates
        sleep_gates[-1].set()
        await drain(runtime)

        assert seen == [(first.id, 1), (third.id, 3)]
        assert len(channel.sent) == 2
        assert channel.sent[0].deleted is True
        assert channel.sent[1].deleted is False
        assert runtime._current_cards[(guild.id, channel.id)].user_id == third.id

    asyncio.run(scenario())


def test_warm_channel_replacement_does_not_reread_durable_state(monkeypatch):
    async def scenario() -> None:
        patch_types(monkeypatch)
        guild = Guild()
        channel = guild.add_channel(40)
        first = guild.add_member(401)
        second = guild.add_member(402)
        reads = 0

        async def get_config(_guild_id: int):
            return config(channel.id)

        async def get_state(*_args):
            nonlocal reads
            reads += 1
            return None

        async def save_state(*_args, **_kwargs):
            return None

        async def no_wait(_seconds: float):
            return None

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer([]), sleep=no_wait)

        await runtime.on_message(Incoming(1, guild, channel, first))
        await drain(runtime)
        await runtime.on_message(Incoming(2, guild, channel, second))
        await drain(runtime)

        assert reads == 1
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
                    "show_roles": False,
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
        assert first.embed.url != second.embed.url

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

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", unavailable)
        runtime = LiveProfileCardRuntime(Bot(guild), renderer=renderer(seen), sleep=asyncio.sleep)

        await runtime.on_message(Incoming(1, guild, channel, member))
        await drain(runtime)

        assert seen == []
        assert channel.sent == []

    asyncio.run(scenario())
