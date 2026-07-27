from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


runtime = Path("stoney_verify/profile_card_runtime.py")
replace_once(
    runtime,
    '''        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)
        age = _state_age_seconds(state)
        if state and str(state.get("user_id") or "") == str(trigger.user_id):
            if age is not None and age < config.same_speaker_cooldown_seconds:
                self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
                return
        elif state and age is not None and age < config.replacement_cooldown_seconds:
            await self.sleep(config.replacement_cooldown_seconds - age)
            key = (trigger.guild_id, trigger.channel_id)
            if self._latest.get(key) != trigger:
                return
''',
    '''        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)
        age = _state_age_seconds(state)
        state_is_live = await self._stored_state_is_live(channel, state)
        if state and not state_is_live:
            try:
                await delete_live_card_state(trigger.guild_id, trigger.channel_id)
            except Exception as exc:
                print(
                    "⚠️ live_profile_card stale state cleanup failed "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} "
                    f"error={type(exc).__name__}: {exc}"
                )
            state = None
            age = None
        if state and str(state.get("user_id") or "") == str(trigger.user_id):
            if age is not None and age < config.same_speaker_cooldown_seconds:
                self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
                print(
                    "ℹ️ live_profile_card skipped "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                    "reason=same_speaker_card_still_live"
                )
                return
        elif state and age is not None and age < config.replacement_cooldown_seconds:
            await self.sleep(config.replacement_cooldown_seconds - age)
            key = (trigger.guild_id, trigger.channel_id)
            if self._latest.get(key) != trigger:
                print(
                    "ℹ️ live_profile_card skipped "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                    "reason=superseded_during_replacement_cooldown"
                )
                return
''',
    label="verify stale state before cooldown",
)
replace_once(
    runtime,
    '''        rendered = await self.renderer(
            member,
            set(config.allowed_fields),
            trigger_message_id=trigger.message_id,
        )
        if rendered is None:
            print(
                "ℹ️ live_profile_card skipped member disabled "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id}"
            )
            return
''',
    '''        try:
            rendered = await self.renderer(
                member,
                set(config.allowed_fields),
                trigger_message_id=trigger.message_id,
            )
        except Exception as exc:
            print(
                "⚠️ live_profile_card render failed "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return
        if rendered is None:
            print(
                "ℹ️ live_profile_card skipped "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "reason=member_live_signature_disabled"
            )
            return
''',
    label="render diagnostics",
)
replace_once(
    runtime,
    '''        self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
        if old_message_id and old_message_id != int(new_message.id):
            await self._delete_stored_message(channel, old_message_id)
''',
    '''        self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
        print(
            "✅ live_profile_card posted "
            f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
            f"message={new_message.id} trigger={trigger.message_id}"
        )
        if old_message_id and old_message_id != int(new_message.id):
            await self._delete_stored_message(channel, old_message_id)

    async def _stored_state_is_live(
        self,
        channel: discord.TextChannel,
        state: Optional[Mapping[str, Any]],
    ) -> bool:
        if not isinstance(state, Mapping):
            return False
        try:
            message_id = int(str(state.get("message_id") or "0"))
        except Exception:
            return False
        if message_id <= 0:
            return False
        try:
            stored = await channel.fetch_message(message_id)
        except discord.NotFound:
            return False
        except Exception as exc:
            print(
                "⚠️ live_profile_card state verification failed "
                f"guild={channel.guild.id} channel={channel.id} message={message_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return True
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None or int(getattr(stored.author, "id", 0) or 0) != int(bot_user.id):
            return False
        parsed = parse_live_card_footer(stored)
        if parsed is None:
            return False
        try:
            stored_user_id = int(str(state.get("user_id") or "0"))
        except Exception:
            return False
        return int(parsed[0]) == stored_user_id
''',
    label="posted diagnostic and state verifier",
)

core = Path("stoney_verify/profile_card_runtime_core.py")
replace_once(
    core,
    '''        try:
            config = parse_live_card_config(await get_guild_config(message.guild.id))
        except Exception:
            return
        if not config.enabled or message.channel.id not in config.channel_ids:
            return
        if not _channel_can_host_cards(message.channel):
            return
''',
    '''        try:
            config = parse_live_card_config(await get_guild_config(message.guild.id, refresh=True))
        except Exception as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                f"reason=config_read_failed error={type(exc).__name__}: {exc}"
            )
            return
        if not config.enabled:
            return
        if message.channel.id not in config.channel_ids:
            return
        if not _channel_can_host_cards(message.channel):
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                "reason=channel_permissions_incomplete"
            )
            return
''',
    label="refresh runtime config and log permission skip",
)

Path("tests/test_live_profile_runtime_delivery_regression.py").write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)

Path("ACTIVE_TASK.md").write_text(
    '''# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-009 — Restore live signature delivery

**Status:** ROOT CAUSE CONFIRMED / IMPLEMENTATION VALIDATION REQUIRED
**Branch:** `fix/live-profile-signature-runtime-delivery`
**PR:** #139
**Base:** current `main`

## Confirmed findings

- The delayed runtime now uses the triggering message author first, but same-speaker cooldown still trusts stored database state without confirming the referenced Discord card exists.
- A missing/deleted old signature can therefore suppress every new message during cooldown while nothing is visible in Discord.
- Runtime configuration reads use a potentially stale cached guild config.
- Several skip paths still provide inadequate production evidence.

## Scope

- Verify stored card existence and bot ownership before cooldown suppression.
- Remove stale state when the referenced card is missing.
- Refresh guild configuration for live message evaluation.
- Log configured-channel permission, render, send, stale-state, cooldown, and success outcomes.
- Preserve the clear member-facing Live Signature ON/OFF control from PR #137.

## Validation

- [ ] A real message author posts even when `guild.get_member()` returns `None`.
- [ ] Missing stored cards never suppress a replacement signature.
- [ ] Existing valid same-speaker cards remain cooldown-suppressed.
- [ ] Failed send/state-write safety remains intact.
- [ ] Focused tests and changed-module compilation pass.
- [ ] Full unit suite and repository audits pass on exact clean head.
- [ ] Deployed designated-channel message produces `✅ live_profile_card posted` and a visible signature.

## Backlog

- Fix departed-member reconciliation async-generator handling.
- Review contradictory worker startup wording.
- Enable automatic sharding before 100+ public guilds.
''',
    encoding="utf-8",
)

print("materialized live profile runtime delivery correction")
