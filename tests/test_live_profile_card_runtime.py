import asyncio
from io import BytesIO
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import (
    LiveCardRender,
    LiveProfileCardRuntime,
    PendingTrigger,
    _copy_base_profile_embed,
    live_card_footer,
    parse_live_card_config,
)
from stoney_verify.profile_card_service import ProfileStorageUnavailable


class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True


class FakeAvatar:
    url = "https://cdn.example/avatar.png"


class FakeMember:
    def __init__(self, user_id, guild, *, bot=False, name=None):
        self.id = int(user_id)
        self.guild = guild
        self.bot = bool(bot)
        self.name = name or f"user-{user_id}"
        self.display_name = self.name
        self.roles = []
        self.display_avatar = FakeAvatar()
        self.joined_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)


class FakeSentMessage:
    def __init__(self, message_id, author, embeds):
        self.id = int(message_id)
        self.author = author
        self.embeds = list(embeds)
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, channel_id, guild):
        self.id = int(channel_id)
        self.guild = guild
        self.sent = []
        self.history_messages = []
        self.fetch_messages = {}
        self.fail_send = False
        self.reject_none_view = False
        self.sent_payloads = []

    def permissions_for(self, _member):
        return FakePermissions()

    async def send(self, **payload):
        if self.fail_send:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="send failed"), "send failed")
        if self.reject_none_view and "view" in payload and payload["view"] is None:
            raise TypeError("expected view parameter to be of type View or LayoutView, not NoneType")
        self.sent_payloads.append(dict(payload))
        message = FakeSentMessage(1000 + len(self.sent), self.guild.bot_user, [payload["embed"]])
        self.sent.append(message)
        self.fetch_messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        if int(message_id) not in self.fetch_messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing")
        return self.fetch_messages[int(message_id)]

    async def history(self, *, limit):
        for message in self.history_messages[:limit]:
            yield message


class FakeGuild:
    def __init__(self, guild_id, bot_user):
        self.id = int(guild_id)
        self.name = f"Guild {guild_id}"
        self.bot_user = bot_user
        self.me = FakeMember(bot_user.id, self, bot=True, name="Dank Shield")
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

    async def wait_until_ready(self):
        return None


class FakeIncomingMessage:
    def __init__(self, message_id, guild, channel, author, *, webhook_id=None):
        self.id = int(message_id)
        self.guild = guild
        self.channel = channel
        self.author = author
        self.webhook_id = webhook_id
        self.type = discord.MessageType.default


async def _wait_for_pending(runtime):
    tasks = list(runtime._pending.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _config(channel_id, *, cooldown=180):
    return {
        "profile_live_cards_enabled": True,
        "profile_live_card_channel_ids": [str(channel_id)],
        "profile_live_card_allowed_fields": ["roles", "account_dates", "platforms"],
        "profile_live_card_debounce_seconds": 2,
        "profile_live_card_same_speaker_cooldown_seconds": cooldown,
    }


def _fake_renderer(seen):
    async def render(member, allowed, *, trigger_message_id, require_live_enabled=True):
        seen.append((member.id, set(allowed), trigger_message_id, require_live_enabled))
        embed = discord.Embed(title=f"Profile {member.id}")
        embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
        return LiveCardRender(embed=embed, view=None)

    return render


def _patch_discord_types(monkeypatch):
    monkeypatch.setattr(runtime_module.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(runtime_module.discord, "Member", FakeMember)


def test_server_live_cards_are_disabled_by_default():
    parsed = parse_live_card_config({})
    assert parsed.enabled is False
    assert parsed.channel_ids == frozenset()


def test_hiding_roles_removes_dynamic_paginated_role_fields():
    base = discord.Embed(title="Profile")
    base.add_field(name="Profile roles 1-8 of 12", value="private", inline=False)
    base.add_field(name="Profile roles", value="12", inline=True)
    base.add_field(name="Pages", value="Page 1/2", inline=True)
    base.add_field(name="Account created", value="today", inline=True)
    filtered = _copy_base_profile_embed(base, show_roles=False, show_dates=True)
    assert [field.name for field in filtered.fields] == ["Account created"]


def test_message_bursts_coalesce_to_latest_human_speaker(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(1, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(10)
        first = guild.add_member(101)
        second = guild.add_member(202)
        gate = asyncio.Event()
        seen = []
        states = []

        async def sleep(_seconds):
            await gate.wait()

        async def get_config(_guild_id):
            return _config(channel.id)

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(guild_id, channel_id, **payload):
            states.append((guild_id, channel_id, payload))

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)

        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer(seen), sleep=sleep)
        await runtime.on_message(FakeIncomingMessage(1, guild, channel, first))
        await runtime.on_message(FakeIncomingMessage(2, guild, channel, second))
        gate.set()
        await _wait_for_pending(runtime)

        assert [item[0] for item in seen] == [second.id]
        assert len(channel.sent) == 1
        assert states[0][2]["user_id"] == second.id
        assert states[0][2]["trigger_message_id"] == 2

    asyncio.run(scenario())


def test_same_speaker_is_suppressed_during_cooldown(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(2, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(20)
        member = guild.add_member(303)
        seen = []

        async def get_config(_guild_id):
            return _config(channel.id, cooldown=300)

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(*_args, **_kwargs):
            return None

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer(seen), sleep=asyncio.sleep)

        await runtime.on_message(FakeIncomingMessage(1, guild, channel, member))
        await _wait_for_pending(runtime)
        await runtime.on_message(FakeIncomingMessage(2, guild, channel, member))
        await _wait_for_pending(runtime)

        assert len(channel.sent) == 1
        assert len(seen) == 1

    asyncio.run(scenario())


def test_bot_and_webhook_messages_never_schedule_cards(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(3, bot.user)
        channel = guild.add_channel(30)
        human = guild.add_member(404)
        bot_member = guild.add_member(405, bot=True)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)

        await runtime.on_message(FakeIncomingMessage(1, guild, channel, bot_member))
        await runtime.on_message(FakeIncomingMessage(2, guild, channel, human, webhook_id=77))
        assert runtime._pending == {}

    asyncio.run(scenario())


def test_failed_send_leaves_existing_card_untouched(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(4, bot.user)
        channel = guild.add_channel(40)
        channel.fail_send = True
        member = guild.add_member(505)
        old = FakeSentMessage(900, bot.user, [])
        channel.fetch_messages[old.id] = old
        deleted_old = []

        async def get_state(_guild_id, _channel_id):
            return {"message_id": str(old.id), "user_id": "9999"}

        async def forbidden_save(*_args, **_kwargs):
            raise AssertionError("state must not update after send failure")

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", forbidden_save)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)

        async def track_delete(_channel, message_id):
            deleted_old.append(message_id)
            return True

        runtime._delete_stored_message = track_delete
        trigger = PendingTrigger(guild.id, channel.id, member.id, 1)
        await runtime._replace_card(
            FakeIncomingMessage(1, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )
        assert deleted_old == []
        assert old.deleted is False

    asyncio.run(scenario())


def test_failed_state_write_deletes_only_new_bot_owned_card(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(5, bot.user)
        channel = guild.add_channel(50)
        member = guild.add_member(606)
        old = FakeSentMessage(901, bot.user, [])
        channel.fetch_messages[old.id] = old

        async def get_state(_guild_id, _channel_id):
            return {"message_id": str(old.id), "user_id": "9999"}

        async def fail_save(*_args, **_kwargs):
            raise ProfileStorageUnavailable("offline")

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", fail_save)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)
        trigger = PendingTrigger(guild.id, channel.id, member.id, 2)
        await runtime._replace_card(
            FakeIncomingMessage(2, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )

        assert len(channel.sent) == 1
        assert channel.sent[0].deleted is True
        assert old.deleted is False

    asyncio.run(scenario())


def test_delete_guard_refuses_user_messages_even_with_marker(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(6, bot.user)
        user = guild.add_member(707)
        embed = discord.Embed(title="Fake")
        embed.set_footer(text=live_card_footer(user.id, 3))
        message = FakeSentMessage(902, user, [embed])
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)
        assert await runtime._delete_verified_card(message) is False
        assert message.deleted is False

    asyncio.run(scenario())


def test_restart_reconciliation_keeps_newest_owned_card_and_cleans_duplicates(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(7, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(70)
        old_embed = discord.Embed(title="Old")
        old_embed.set_footer(text=live_card_footer(808, 10))
        new_embed = discord.Embed(title="New")
        new_embed.set_footer(text=live_card_footer(909, 11))
        old = FakeSentMessage(100, bot.user, [old_embed])
        new = FakeSentMessage(200, bot.user, [new_embed])
        channel.history_messages = [new, old]
        saved = []

        async def get_config(_guild_id):
            return _config(channel.id)

        async def list_states():
            return []

        async def save_state(guild_id, channel_id, **payload):
            saved.append((guild_id, channel_id, payload))

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "list_live_card_states", list_states)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)
        await runtime.reconcile()

        assert saved[0][2]["message_id"] == new.id
        assert saved[0][2]["user_id"] == 909
        assert new.deleted is False
        assert old.deleted is True

    asyncio.run(scenario())



def test_live_send_omits_none_view_and_keeps_attachment(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(81, bot.user)
        channel = guild.add_channel(810)
        channel.reject_none_view = True
        member = guild.add_member(811)
        states = []

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(guild_id, channel_id, **payload):
            states.append((guild_id, channel_id, payload))

        async def renderer(member, allowed, *, trigger_message_id, require_live_enabled=True):
            embed = discord.Embed(title=f"Profile {member.id}")
            embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
            return LiveCardRender(
                embed=embed,
                view=None,
                file=discord.File(BytesIO(b"image-bytes"), filename="profile.png"),
            )

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(bot, renderer=renderer, sleep=asyncio.sleep)
        trigger = PendingTrigger(guild.id, channel.id, member.id, 91)
        await runtime._replace_card(
            FakeIncomingMessage(91, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )

        assert len(channel.sent) == 1
        assert "view" not in channel.sent_payloads[0]
        assert channel.sent_payloads[0]["file"].filename == "profile.png"
        assert states and states[0][2]["user_id"] == member.id

    asyncio.run(scenario())


def test_basic_signature_renders_when_every_optional_field_is_hidden(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(82, bot.user)
        member = guild.add_member(821)
        seen = []

        async def settings(_guild_id, _user_id):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_roles": False,
                    "show_account_dates": False,
                    "show_platforms": False,
                },
                "platforms": {},
            }

        async def config(_guild_id):
            return {}

        async def render_image(_member, *, style, role_labels, date_labels, platform_labels):
            seen.append((style, role_labels, date_labels, platform_labels))
            return b"image-bytes"

        monkeypatch.setattr(runtime_module, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        monkeypatch.setattr(runtime_module, "render_member_profile_signature", render_image)
        rendered = await runtime_module.render_live_profile_card(
            member,
            set(),
            trigger_message_id=92,
            require_live_enabled=False,
        )

        assert rendered is not None
        assert rendered.file is not None
        assert seen and seen[0][1:] == ([], [], [])

    asyncio.run(scenario())


def test_live_send_failure_is_visible_in_logs(monkeypatch, capsys):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(83, bot.user)
        channel = guild.add_channel(830)
        channel.fail_send = True
        member = guild.add_member(831)

        async def get_state(_guild_id, _channel_id):
            return None

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)
        trigger = PendingTrigger(guild.id, channel.id, member.id, 93)
        await runtime._replace_card(
            FakeIncomingMessage(93, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert "live_profile_card send failed" in output
    assert "guild=83" in output
    assert "channel=830" in output


def test_live_runtime_uses_message_author_when_guild_member_cache_misses(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(901, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(902)
        author = FakeMember(903, guild)
        assert guild.get_member(author.id) is None
        seen = []
        states = []

        async def get_config(_guild_id):
            return _config(channel.id, cooldown=30)

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(guild_id, channel_id, **payload):
            states.append((guild_id, channel_id, payload))

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)

        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer(seen), sleep=asyncio.sleep)
        await runtime.on_message(FakeIncomingMessage(904, guild, channel, author))
        await _wait_for_pending(runtime)

        assert [item[0] for item in seen] == [author.id]
        assert len(channel.sent) == 1
        assert states[0][2]["user_id"] == author.id

    asyncio.run(scenario())


def test_profile_privacy_has_one_clear_live_signature_switch():
    from stoney_verify.commands_ext.public_profile_cards import ProfileSettingsView

    enabled = ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"live_cards_enabled": True},
        guild_settings={},
    )
    disabled = ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"live_cards_enabled": False},
        guild_settings={},
    )
    enabled_labels = [str(child.label) for child in enabled.children if isinstance(child, discord.ui.Button)]
    disabled_labels = [str(child.label) for child in disabled.children if isinstance(child, discord.ui.Button)]
    assert enabled_labels.count("Turn Off Live Signature") == 1
    assert disabled_labels.count("Turn On Live Signature") == 1
    assert not any(label.startswith("Every Server Live") for label in enabled_labels)
