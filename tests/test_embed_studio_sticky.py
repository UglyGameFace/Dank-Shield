"""Post Builder sticky posts (Embed Studio).

A tracked post can be made sticky: human chat in its channel re-posts it
at the bottom, throttled at the normal sticky pace, one sticky per
channel (Post Builder's own rule is the only gate). These tests drive the real toggle
(``set_post_sticky``), the real runtime (``StudioStickyRuntime``), and the
real publish callback for the edit/deleted-post paths.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from stoney_verify import embed_studio_sticky as sticky
from stoney_verify import embed_studio_ui as ui
from stoney_verify.commands_ext import public_embed_group
from stoney_verify.embed_studio_core import library_payload, new_project, parse_library
from stoney_verify.embed_studio_sticky import (
    StudioStickyChoiceView,
    StudioStickyPromptView,
    StudioStickyRuntime,
    StudioStickyEntry,
    channel_sticky_key,
    post_is_sticky,
    set_post_sticky,
    sticky_state,
)
from stoney_verify.embed_studio_ui import StudioReviewView, StudioSession

GUILD_ID, OWNER_ID, BOT_ID = 5, 10, 999
CHANNEL_ID, MESSAGE_ID = 99, 100
POST_KEY = f"{CHANNEL_ID}:{MESSAGE_ID}"
NEW_MESSAGE_ID = 101
NEW_POST_KEY = f"{CHANNEL_ID}:{NEW_MESSAGE_ID}"
OTHER_MESSAGE_ID = 200
OTHER_POST_KEY = f"{CHANNEL_ID}:{OTHER_MESSAGE_ID}"


def _record(message_id=MESSAGE_ID, **extra):
    row = {
        "channel_id": str(CHANNEL_ID), "message_id": str(message_id),
        "by": str(OWNER_ID), "role_token": "", "role_mode": "buttons",
        "role_actions": [], "form": None, "form_token": "",
    }
    row.update(extra)
    return row


def _sticky_on(**kwargs):
    return {"enabled": True, "last_sent_at": "2026-11-19T00:00:00+00:00", **kwargs}


class LibraryHarness:
    """In-memory Studio library with the same CAS shape as the ghost tests."""

    def __init__(self, published):
        lib = parse_library({"embed_studio_v1": {"published": published}})
        self.state = {"value": library_payload(lib)}
        self.saves = []

    async def load(self, guild_id):
        assert guild_id == GUILD_ID
        source = {"embed_studio_v1": deepcopy(self.state["value"])}
        return source, parse_library(source)

    async def save(self, guild_id, source, lib):
        assert guild_id == GUILD_ID
        if source.get("embed_studio_v1") != self.state["value"]:
            return False
        self.state["value"] = library_payload(lib)
        self.saves.append(deepcopy(self.state["value"]))
        return True

    @property
    def published(self):
        return self.state["value"]["published"]


def _patch_library(monkeypatch, harness, *, module=sticky):
    monkeypatch.setattr(module, "_load_library", harness.load)
    monkeypatch.setattr(module, "_save_library", harness.save)


def _fake_bot():
    listeners = []
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID), guilds=[],
        add_listener=lambda fn, event: listeners.append((fn, event)),
    )
    bot._listeners = listeners
    return bot


# ---------------------------------------------------------------------------
# Pure state helpers
# ---------------------------------------------------------------------------

def test_sticky_state_normalization():
    assert post_is_sticky(_record(sticky=_sticky_on())) is True
    assert post_is_sticky(_record()) is False
    assert post_is_sticky(_record(sticky={"enabled": False})) is False
    assert post_is_sticky("junk") is False
    state = sticky_state(_record(sticky=_sticky_on()))
    assert state["enabled"] is True
    assert state["last_sent_at"] == datetime(2026, 11, 19, tzinfo=timezone.utc)


def test_channel_sticky_key_finds_other_post_only():
    published = {
        POST_KEY: _record(sticky=_sticky_on()),
        OTHER_POST_KEY: _record(OTHER_MESSAGE_ID),
    }
    assert channel_sticky_key(published, CHANNEL_ID, exclude_key=OTHER_POST_KEY) == POST_KEY
    assert channel_sticky_key(published, CHANNEL_ID, exclude_key=POST_KEY) is None
    assert channel_sticky_key(published, 12345) is None


# ---------------------------------------------------------------------------
# The toggle: persistence, conflicts, removal
# ---------------------------------------------------------------------------

def test_make_sticky_persists_on_published_record(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_library(monkeypatch, harness)
    bot = _fake_bot()
    runtime = sticky.ensure_studio_sticky_runtime(bot)

    ok, message = asyncio.run(set_post_sticky(GUILD_ID, POST_KEY, enabled=True, client=bot))

    assert ok is True
    assert "Sticky on" in message
    record = harness.published[POST_KEY]
    assert post_is_sticky(record) is True
    assert record["sticky"]["last_sent_at"]
    entry = runtime.entry_for(CHANNEL_ID)
    assert entry is not None and entry.post_key == POST_KEY


def test_make_sticky_refuses_second_studio_post_in_channel(monkeypatch):
    harness = LibraryHarness({
        POST_KEY: _record(sticky=_sticky_on()),
        OTHER_POST_KEY: _record(OTHER_MESSAGE_ID),
    })
    _patch_library(monkeypatch, harness)

    ok, message = asyncio.run(set_post_sticky(GUILD_ID, OTHER_POST_KEY, enabled=True))

    assert ok is False
    assert str(MESSAGE_ID) in message
    assert "only have one sticky" in message
    assert post_is_sticky(harness.published[OTHER_POST_KEY]) is False


def test_make_sticky_refuses_live_text_post(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record(live_nonce="abc123", live_source={})})
    _patch_library(monkeypatch, harness)

    ok, message = asyncio.run(set_post_sticky(GUILD_ID, POST_KEY, enabled=True))

    assert ok is False
    assert "Live Text" in message
    assert harness.saves == []


def test_remove_sticky_persists_and_unregisters(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record(sticky=_sticky_on())})
    _patch_library(monkeypatch, harness)
    bot = _fake_bot()
    runtime = sticky.ensure_studio_sticky_runtime(bot)
    runtime.set_sticky(StudioStickyEntry(GUILD_ID, CHANNEL_ID, POST_KEY))

    ok, message = asyncio.run(set_post_sticky(GUILD_ID, POST_KEY, enabled=False, client=bot))

    assert ok is True
    assert "Sticky off" in message
    assert "stays where it is" in message
    assert post_is_sticky(harness.published[POST_KEY]) is False
    assert runtime.entry_for(CHANNEL_ID) is None


# ---------------------------------------------------------------------------
# Runtime: movement, throttle, deletion, stand-down
# ---------------------------------------------------------------------------

class FakeTextChannel(discord.TextChannel):
    __slots__ = ("fetch_impl", "send_impl", "sent")

    async def fetch_message(self, message_id):
        return await self.fetch_impl(message_id)

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return await self.send_impl(content=content, **kwargs)

    def permissions_for(self, bot):
        return discord.Permissions.all()


def _bot_message(message_id, *, content="Server is open", deleted=None):
    async def delete():
        if deleted is not None:
            deleted.append(message_id)

    embed = discord.Embed(title="The 420 Server", description="Join us")
    return SimpleNamespace(
        id=message_id, author=SimpleNamespace(id=BOT_ID),
        content=content, embeds=[embed], components=[],
        jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}",
        delete=delete,
    )


class RuntimeHarness:
    def __init__(self, monkeypatch, *, record=None, fetch_impl=None):
        self.harness = LibraryHarness({POST_KEY: record or _record(sticky=_sticky_on())})
        _patch_library(monkeypatch, self.harness)
        self.deleted = []
        self.live_message = _bot_message(MESSAGE_ID, deleted=self.deleted)

        async def default_fetch(message_id):
            assert message_id == MESSAGE_ID
            return self.live_message

        async def default_send(*, content=None, **kwargs):
            return _bot_message(NEW_MESSAGE_ID, content=content or "", deleted=self.deleted)

        self.channel = object.__new__(FakeTextChannel)
        self.channel.id = CHANNEL_ID
        self.channel.name = "minecraft-chat"
        self.channel.fetch_impl = fetch_impl or default_fetch
        self.channel.send_impl = default_send
        self.channel.sent = []
        self.guild = SimpleNamespace(
            id=GUILD_ID, owner_id=OWNER_ID, name="Test Guild", member_count=42,
            get_channel=lambda cid: self.channel if cid == CHANNEL_ID else None,
            get_role=lambda rid: None,
        )
        self.channel.guild = self.guild
        self.bot = _fake_bot()
        self.bot.guilds = [self.guild]
        self.runtime = StudioStickyRuntime(self.bot)
        self.entry = StudioStickyEntry(
            GUILD_ID, CHANNEL_ID, POST_KEY,
            last_sent_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        self.runtime.set_sticky(self.entry)

    def human_message(self, *, author_bot=False):
        return SimpleNamespace(
            guild=self.guild,
            author=SimpleNamespace(id=777, bot=author_bot),
            webhook_id=None, channel=self.channel,
        )


def test_refresh_reposts_at_bottom_and_record_follows(monkeypatch):
    rig = RuntimeHarness(monkeypatch)

    result = asyncio.run(rig.runtime.refresh_channel(rig.channel, entry=rig.entry, force=True))

    assert result is not None and result.id == NEW_MESSAGE_ID
    assert len(rig.channel.sent) == 1
    sent = rig.channel.sent[0]
    assert sent["content"] == "Server is open"
    assert sent["embeds"][0].title == "The 420 Server"
    # The old copy was deleted after the new one landed.
    assert rig.deleted == [MESSAGE_ID]
    # The tracked record followed the new message and stayed sticky.
    assert POST_KEY not in rig.harness.published
    record = rig.harness.published[NEW_POST_KEY]
    assert record["message_id"] == str(NEW_MESSAGE_ID)
    assert post_is_sticky(record) is True
    assert rig.entry.post_key == NEW_POST_KEY


def test_refresh_rolls_back_new_copy_when_tracking_save_fails(monkeypatch):
    rig = RuntimeHarness(monkeypatch)

    async def failing_save(guild_id, source, lib):
        return False

    # The first save inside refresh is the tracking save; make it fail.
    monkeypatch.setattr(sticky, "_save_library", failing_save)
    result = asyncio.run(rig.runtime.refresh_channel(rig.channel, entry=rig.entry, force=True))

    assert result is None
    assert len(rig.channel.sent) == 1
    # New copy deleted again, old copy untouched, record unchanged.
    assert rig.deleted == [NEW_MESSAGE_ID]
    assert post_is_sticky(rig.harness.published[POST_KEY]) is True


def test_on_message_throttle_blocks_until_threshold(monkeypatch):
    rig = RuntimeHarness(monkeypatch)
    # Just moved: neither the count nor the interval may fire early.
    rig.entry.last_sent_at = datetime.now(timezone.utc)

    async def drive():
        for _ in range(sticky.STICKY_MESSAGE_THRESHOLD - 1):
            await rig.runtime.on_message(rig.human_message())
        await asyncio.sleep(0.05)
        assert rig.channel.sent == []
        # The threshold-th human message moves it.
        await rig.runtime.on_message(rig.human_message())
        await asyncio.sleep(0.2)
        assert len(rig.channel.sent) == 1

    asyncio.run(drive())


def test_on_message_interval_path_moves_after_quiet_period(monkeypatch):
    rig = RuntimeHarness(monkeypatch)
    rig.entry.last_sent_at = datetime.now(timezone.utc) - timedelta(hours=2)

    async def drive():
        await rig.runtime.on_message(rig.human_message())
        await asyncio.sleep(0.2)
        assert len(rig.channel.sent) == 1

    asyncio.run(drive())


def test_on_message_ignores_bots_and_webhooks(monkeypatch):
    rig = RuntimeHarness(monkeypatch)
    rig.entry.last_sent_at = datetime.now(timezone.utc) - timedelta(hours=2)

    async def drive():
        await rig.runtime.on_message(rig.human_message(author_bot=True))
        webhook_message = rig.human_message()
        webhook_message.webhook_id = 4242
        await rig.runtime.on_message(webhook_message)
        await asyncio.sleep(0.05)
        assert rig.channel.sent == []

    asyncio.run(drive())


def test_refresh_deleted_sticky_copy_stops_without_resurrecting(monkeypatch):
    async def not_found(message_id):
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")

    rig = RuntimeHarness(monkeypatch, fetch_impl=not_found)
    result = asyncio.run(rig.runtime.refresh_channel(rig.channel, entry=rig.entry, force=True))

    assert result is None
    assert rig.channel.sent == []
    # Sticky turned itself off and the runtime dropped the channel.
    assert post_is_sticky(rig.harness.published[POST_KEY]) is False
    assert rig.runtime.entry_for(CHANNEL_ID) is None


def test_remove_sticky_then_chat_does_not_move_post(monkeypatch):
    rig = RuntimeHarness(monkeypatch)
    rig.runtime.remove_sticky(CHANNEL_ID)
    rig.entry.last_sent_at = datetime.now(timezone.utc) - timedelta(hours=2)

    async def drive():
        for _ in range(10):
            await rig.runtime.on_message(rig.human_message())
        await asyncio.sleep(0.05)
        assert rig.channel.sent == []

    asyncio.run(drive())


# ---------------------------------------------------------------------------
# Publish flow: edits keep sticky; the PR #875 deleted-post recovery still
# works when the deleted message was the sticky one.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, record):
        self._done = False
        self.record = record

    def is_done(self):
        return self._done

    async def defer(self, *, ephemeral=False, thinking=False):
        self._done = True

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.record.append(content)


class _FakeFollowup:
    def __init__(self, record):
        self.record = record

    async def send(self, content=None, **kwargs):
        self.record.append(content)
        self.last_kwargs = kwargs


def _member(user_id, guild):
    member = object.__new__(discord.Member)
    member._user = SimpleNamespace(id=user_id)
    member.guild = guild
    return member


class PublishHarness:
    def __init__(self, monkeypatch, *, fetch_impl):
        self.library = LibraryHarness({POST_KEY: _record(sticky=_sticky_on())})
        _patch_library(monkeypatch, self.library, module=ui)

        async def _ok_safe_action(interaction, *, action):
            return True

        monkeypatch.setattr(
            public_embed_group, "_require_safe_embed_action", _ok_safe_action)

        self.channel = object.__new__(FakeTextChannel)
        self.channel.id = CHANNEL_ID
        self.channel.name = "minecraft-chat"
        self.channel.fetch_impl = fetch_impl
        self.channel.send_impl = self._default_send
        self.channel.sent = []
        self.guild = SimpleNamespace(
            id=GUILD_ID, owner_id=OWNER_ID, name="Test Guild", member_count=42,
            get_channel=lambda cid: self.channel if cid == CHANNEL_ID else None,
            get_role=lambda rid: None,
        )
        self.guild.me = _member(BOT_ID, self.guild)
        self.channel.guild = self.guild
        self.record = []
        self.followup = _FakeFollowup(self.record)
        self.interaction = SimpleNamespace(
            guild=self.guild, user=_member(OWNER_ID, self.guild),
            client=SimpleNamespace(user=SimpleNamespace(id=BOT_ID)),
            response=_FakeResponse(self.record), followup=self.followup,
        )
        self.session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
        self.session.channel_id = CHANNEL_ID
        self.session.post_key = POST_KEY
        self.session.project = new_project()
        self.session.project["content"] = "Updated announcement"

    async def _default_send(self, *, content=None, **kwargs):
        return _bot_message(NEW_MESSAGE_ID, content=content or "")

    def publish(self):
        view = StudioReviewView(self.session)
        return asyncio.run(view.publish.callback(self.interaction))

    @property
    def said(self) -> str:
        return "\n".join(str(item) for item in self.record)


def test_publish_edit_of_sticky_post_keeps_sticky(monkeypatch):
    edits = []

    async def edit(**kwargs):
        edits.append(kwargs)

    live = SimpleNamespace(
        id=MESSAGE_ID, author=SimpleNamespace(id=BOT_ID),
        jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}",
        edit=edit,
    )

    async def fetch(message_id):
        assert message_id == MESSAGE_ID
        return live

    harness = PublishHarness(monkeypatch, fetch_impl=fetch)
    harness.publish()

    assert len(edits) == 1
    assert edits[0]["content"] == "Updated announcement"
    assert "✅ Updated" in harness.said
    record = harness.library.published[POST_KEY]
    assert post_is_sticky(record) is True
    # The confirmation carries the sticky toggle for the tracked post.
    view = getattr(harness.followup, "last_kwargs", {}).get("view")
    assert isinstance(view, StudioStickyPromptView)
    assert view.sticky is True
    assert view.toggle.custom_id == "dank:embed_studio:sticky_prompt:v1"


def test_publish_deleted_sticky_post_recovers_like_pr875(monkeypatch):
    async def fetch(message_id):
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")

    harness = PublishHarness(monkeypatch, fetch_impl=fetch)
    harness.publish()

    said = harness.said
    assert "That post was deleted" in said
    assert "brand-new message" in said
    assert "Publish failed" not in said
    assert "NotFound" not in said
    assert harness.channel.sent == []
    # UPDATED with the Repost build: the record is no longer dropped here.
    # It stays, flagged message_missing with stickiness preserved as
    # was_sticky, so one-tap Repost can bring the original back; the
    # session is still detached so a fresh publish posts a new message.
    record = harness.library.published[POST_KEY]
    assert record["message_missing"] is True
    assert record["was_sticky"] is True
    assert post_is_sticky(record) is False
    assert harness.session.post_key == ""


# ---------------------------------------------------------------------------
# UI wiring: stable custom_ids and plain labels on every new control
# ---------------------------------------------------------------------------

def test_published_menu_has_sticky_button_with_stable_id():
    from stoney_verify.embed_studio_ui import PublishedMenu

    session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    menu = PublishedMenu(session, {"published": {}})
    ids = {getattr(item, "custom_id", None) for item in menu.children}
    assert "dank:embed_studio:sticky_menu:v1" in ids


def test_sticky_views_use_stable_custom_ids_and_plain_labels():
    session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    off = StudioStickyChoiceView(session, POST_KEY, sticky=False)
    on = StudioStickyChoiceView(session, POST_KEY, sticky=True)
    assert off.toggle.label == "Make Sticky"
    assert on.toggle.label == "Remove Sticky"
    for view in (off, on):
        assert view.toggle.custom_id == "dank:embed_studio:sticky_toggle:v1"
        assert view.back.custom_id == "dank:embed_studio:sticky_choice_back:v1"
    prompt = StudioStickyPromptView(session, POST_KEY, sticky=False)
    assert prompt.toggle.custom_id == "dank:embed_studio:sticky_prompt:v1"
    assert prompt.toggle.label == "Make Sticky"


def test_sticky_prompt_toggle_drives_set_post_sticky(monkeypatch):
    session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    calls = []

    async def fake_set(guild_id, post_key, *, enabled, client=None):
        calls.append((guild_id, post_key, enabled))
        return True, "📌 Sticky on. Test message."

    monkeypatch.setattr(sticky, "set_post_sticky", fake_set)
    record = []
    guild = SimpleNamespace(id=GUILD_ID, owner_id=OWNER_ID)
    interaction = SimpleNamespace(
        guild=guild, user=_member(OWNER_ID, guild),
        client=SimpleNamespace(user=SimpleNamespace(id=BOT_ID)),
        response=_FakeResponse(record), followup=_FakeFollowup(record),
    )
    view = StudioStickyPromptView(session, POST_KEY, sticky=False)
    asyncio.run(view.toggle.callback(interaction))

    assert calls == [(GUILD_ID, POST_KEY, True)]
    assert "Sticky on" in "\n".join(str(item) for item in record)


# ---------------------------------------------------------------------------
# Navigation registry: sticky is discoverable, What's New stays in budget
# ---------------------------------------------------------------------------

def test_registry_sticky_discoverability_and_whats_new_budget():
    from stoney_verify import navigation_registry as registry

    feature = registry.feature_by_key("embed_studio")
    assert feature is not None
    assert "sticky post" in feature.aliases
    assert "make sticky" in feature.aliases
    hits = registry.search_features("make sticky")
    assert hits and hits[0].key == "embed_studio"

    from collections import OrderedDict

    grouped: dict[str, list[str]] = OrderedDict()
    for update in registry.RECENT_UPDATES:
        item = registry.feature_by_key(update.key)
        grouped.setdefault(update.merged_date, []).append(
            f"{item.emoji} **{item.label}: {update.headline}**\n  {update.detail}")
    for date, lines in grouped.items():
        assert len("\n\n".join(lines)) <= 1024, f"What's New field for {date} overflows"
    studio_update = next(u for u in registry.RECENT_UPDATES if u.key == "embed_studio")
    assert "sticky" in studio_update.detail
