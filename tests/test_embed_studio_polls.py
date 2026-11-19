"""Post Builder polls (Embed Studio).

Polls are a first-class Post Builder post kind: the poll — question,
choices, every member's vote, and its state — lives on the tracked
published record and moves through the existing library CAS. These tests
drive the real vote path (``cast_poll_vote``), the real global listener,
the real publish callback, the real sticky runtime moving a poll, and
the legacy migration converters.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import embed_studio_polls as polls
from stoney_verify import embed_studio_sticky as sticky
from stoney_verify import navigation_registry as registry
from stoney_verify.embed_studio_core import (
    StudioValidationError,
    library_payload,
    parse_library,
)
from stoney_verify.embed_studio_polls import (
    cast_poll_vote,
    parse_poll_custom_id,
    poll_counts,
    poll_custom_id,
    poll_embed,
    poll_view,
    set_poll_state,
    studio_poll_from_legacy,
    studio_record_from_legacy_sticky,
    validate_poll,
)
from stoney_verify.embed_studio_sticky import (
    StudioStickyEntry,
    StudioStickyRuntime,
    post_is_sticky,
)

GUILD_ID, OWNER_ID, BOT_ID = 5, 10, 999
CHANNEL_ID, MESSAGE_ID = 99, 100
POST_KEY = f"{CHANNEL_ID}:{MESSAGE_ID}"
NEW_MESSAGE_ID = 101
NEW_POST_KEY = f"{CHANNEL_ID}:{NEW_MESSAGE_ID}"
TOKEN = "a" * 32


def _poll(**extra):
    poll = {
        "question": "What should we play on Friday?",
        "options": ["Minecraft", "Movie night", "Tournament"],
        "votes": {},
        "state": "active",
    }
    poll.update(extra)
    return poll


def _record(message_id=MESSAGE_ID, **extra):
    row = {
        "channel_id": str(CHANNEL_ID), "message_id": str(message_id),
        "by": str(OWNER_ID), "role_token": "", "role_mode": "buttons",
        "role_actions": [], "form": None, "form_token": "",
        "poll": _poll(), "poll_token": TOKEN,
    }
    row.update(extra)
    return row


class LibraryHarness:
    """In-memory Studio library with the same CAS shape as the sticky tests."""

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


def _patch_polls_library(monkeypatch, harness):
    monkeypatch.setattr(polls, "_load_library", harness.load)
    monkeypatch.setattr(polls, "_save_library", harness.save)


# ---------------------------------------------------------------------------
# Validation + rendering
# ---------------------------------------------------------------------------

def test_validate_poll_bounds():
    clean = validate_poll(_poll())
    assert clean["options"] == ["Minecraft", "Movie night", "Tournament"]
    assert clean["state"] == "active"

    with pytest.raises(StudioValidationError):
        validate_poll(_poll(question="   "))
    with pytest.raises(StudioValidationError):
        validate_poll(_poll(question="x" * 301))
    with pytest.raises(StudioValidationError):
        validate_poll(_poll(options=["Only one"]))
    with pytest.raises(StudioValidationError):
        validate_poll(_poll(options=[f"Choice {i}" for i in range(11)]))
    with pytest.raises(StudioValidationError):
        validate_poll(_poll(options=["a" * 81, "B"]))
    with pytest.raises(StudioValidationError):
        validate_poll(_poll(state="open"))
    with pytest.raises(StudioValidationError):
        validate_poll(None)


def test_validate_poll_dedupes_and_filters_votes():
    clean = validate_poll(_poll(
        options=["Yes", "yes", "No"],
        votes={"42": 0, "43": 9, "not-a-user": 1, "44": "x"},
    ))
    assert clean["options"] == ["Yes", "No"]
    # Index 9 is out of range for the surviving choices; junk keys dropped.
    assert clean["votes"] == {"42": 0}


def test_poll_embed_shows_counts_and_percents():
    poll = _poll(votes={"1": 0, "2": 0, "3": 1})
    embed = poll_embed(poll)
    assert embed.title == "📊 Poll"
    assert embed.description == "What should we play on Friday?"
    field = embed.fields[0]
    assert field.name == "Voting open"
    assert "Minecraft** — 2 votes (67%)" in field.value
    assert "Movie night** — 1 vote (33%)" in field.value
    assert "Tournament** — 0 votes (0%)" in field.value
    assert embed.footer.text.startswith("3 total votes")
    assert poll_counts(poll) == (2, 1, 0)


def test_poll_custom_ids_are_stable_and_parse():
    assert poll_custom_id(TOKEN, 2) == f"dank:es:poll:v1:{TOKEN}:2"
    assert parse_poll_custom_id(f"dank:es:poll:v1:{TOKEN}:2") == (TOKEN, 2)
    assert parse_poll_custom_id("dank:es:poll:v1:short:1") is None
    assert parse_poll_custom_id(f"dank:es:poll:v1:{TOKEN}:10") is None
    assert parse_poll_custom_id("dank:sticky:vote:v1:1:2") is None
    assert parse_poll_custom_id("") is None


def test_poll_view_states():
    active = poll_view(_poll(), TOKEN)
    assert active is not None and active.timeout is None
    buttons = list(active.children)
    assert [b.custom_id for b in buttons] == [
        poll_custom_id(TOKEN, i) for i in range(3)]
    assert all(not b.disabled for b in buttons)

    paused = poll_view(_poll(state="paused"), TOKEN)
    assert paused is not None
    assert all(b.disabled for b in paused.children)

    assert poll_view(_poll(state="ended"), TOKEN) is None
    assert poll_view(_poll(), "not-a-token") is None


# ---------------------------------------------------------------------------
# Voting through the library CAS
# ---------------------------------------------------------------------------

def test_cast_vote_and_change_vote(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)

    async def drive():
        saved = await cast_poll_vote(GUILD_ID, POST_KEY, 42, 0)
        assert saved["votes"] == {"42": 0}
        # Pressing another choice moves the vote; the total stays one.
        saved = await cast_poll_vote(GUILD_ID, POST_KEY, 42, 2)
        assert saved["votes"] == {"42": 2}
        saved = await cast_poll_vote(GUILD_ID, POST_KEY, 43, 2)
        assert saved["votes"] == {"42": 2, "43": 2}
        return saved

    saved = asyncio.run(drive())
    stored = harness.published[POST_KEY]["poll"]
    assert stored["votes"] == {"42": 2, "43": 2}
    assert poll_counts(stored) == (0, 0, 2)
    assert saved["question"] == stored["question"]


def test_cast_vote_refusals(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)

    async def drive():
        await set_poll_state(GUILD_ID, POST_KEY, "paused")
        with pytest.raises(StudioValidationError, match="paused"):
            await cast_poll_vote(GUILD_ID, POST_KEY, 42, 0)
        await set_poll_state(GUILD_ID, POST_KEY, "ended")
        with pytest.raises(StudioValidationError, match="ended"):
            await cast_poll_vote(GUILD_ID, POST_KEY, 42, 0)
        with pytest.raises(StudioValidationError, match="no longer tracked"):
            await cast_poll_vote(GUILD_ID, "99:404040", 42, 0)

    asyncio.run(drive())
    assert harness.published[POST_KEY]["poll"]["votes"] == {}


def test_set_poll_state_transitions(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)

    async def drive():
        poll, _item = await set_poll_state(GUILD_ID, POST_KEY, "paused")
        assert poll["state"] == "paused"
        poll, _item = await set_poll_state(GUILD_ID, POST_KEY, "active")
        assert poll["state"] == "active"
        poll, _item = await set_poll_state(GUILD_ID, POST_KEY, "ended")
        assert poll["state"] == "ended"
        with pytest.raises(StudioValidationError):
            await set_poll_state(GUILD_ID, POST_KEY, "open")

    asyncio.run(drive())
    assert harness.published[POST_KEY]["poll"]["state"] == "ended"


# ---------------------------------------------------------------------------
# The global vote listener
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self):
        self.done = False
        self.edits = []
        self.messages = []

    def is_done(self):
        return self.done

    async def edit_message(self, **kwargs):
        self.done = True
        self.edits.append(kwargs)

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.messages.append(content)


class _FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append(content)


def _vote_interaction(*, message_id=MESSAGE_ID, token=TOKEN, index=1, user_id=42,
                      author_id=BOT_ID):
    interaction = SimpleNamespace(
        type=discord.InteractionType.component,
        data={"custom_id": poll_custom_id(token, index)},
        guild=SimpleNamespace(id=GUILD_ID),
        channel=SimpleNamespace(id=CHANNEL_ID),
        message=SimpleNamespace(
            id=message_id, author=SimpleNamespace(id=author_id)),
        user=SimpleNamespace(id=user_id),
        client=SimpleNamespace(user=SimpleNamespace(id=BOT_ID)),
        response=_FakeResponse(),
        followup=_FakeFollowup(),
    )
    return interaction


def test_listener_records_vote_and_refreshes_card(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)
    polls._VOTE_LAST_CLICK.clear()
    interaction = _vote_interaction()

    asyncio.run(polls._listener(interaction))

    assert harness.published[POST_KEY]["poll"]["votes"] == {"42": 1}
    assert len(interaction.response.edits) == 1
    edit = interaction.response.edits[0]
    assert "Movie night** — 1 vote (100%)" in edit["embed"].fields[0].value
    assert edit["view"] is not None


def test_listener_refuses_stale_card_without_saving(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)
    polls._VOTE_LAST_CLICK.clear()
    # A button on an older copy of the poll: its message id is not the
    # tracked one, so the vote must be refused, never double-counted.
    interaction = _vote_interaction(message_id=555)

    asyncio.run(polls._listener(interaction))

    assert harness.published[POST_KEY]["poll"]["votes"] == {}
    assert interaction.response.edits == []
    answers = interaction.response.messages + interaction.followup.messages
    assert any("older poll card" in str(text) for text in answers)


def test_listener_ignores_foreign_buttons(monkeypatch):
    harness = LibraryHarness({POST_KEY: _record()})
    _patch_polls_library(monkeypatch, harness)
    interaction = _vote_interaction()
    interaction.data = {"custom_id": "dank:es:role:v1:whatever"}

    asyncio.run(polls._listener(interaction))

    assert harness.published[POST_KEY]["poll"]["votes"] == {}
    assert interaction.response.messages == []
    assert interaction.response.edits == []


# ---------------------------------------------------------------------------
# Publishing a poll from the create flow
# ---------------------------------------------------------------------------

class _FakeSentMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.jump_url = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}"
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakePublishChannel(discord.TextChannel):
    __slots__ = ("send_impl", "sent_kwargs")

    async def send(self, content=None, **kwargs):
        self.sent_kwargs = {"content": content, **kwargs}
        return await self.send_impl(content=content, **kwargs)


def _publish_interaction(monkeypatch, harness, sent_message):
    channel = object.__new__(_FakePublishChannel)
    channel.id = CHANNEL_ID
    channel.sent_kwargs = {}

    async def send_impl(*, content=None, **kwargs):
        return sent_message

    channel.send_impl = send_impl
    guild = SimpleNamespace(
        id=GUILD_ID, get_channel=lambda cid: channel if cid == CHANNEL_ID else None)
    channel.guild = guild
    response = SimpleNamespace(
        done=False,
        defer_calls=[],
    )

    async def defer(*, ephemeral=False, thinking=False):
        response.done = True
        response.defer_calls.append((ephemeral, thinking))

    response.defer = defer
    response.is_done = lambda: response.done
    interaction = SimpleNamespace(
        guild=guild, channel=channel, user=SimpleNamespace(id=OWNER_ID),
        client=SimpleNamespace(user=SimpleNamespace(id=BOT_ID)),
        response=response, followup=_FakeFollowup(),
    )
    monkeypatch.setattr(polls, "_post_blockers", lambda i, c: [])
    return interaction, channel


def test_publish_pending_poll_tracks_record(monkeypatch):
    harness = LibraryHarness({})
    _patch_polls_library(monkeypatch, harness)
    sent = _FakeSentMessage(MESSAGE_ID)
    interaction, channel = _publish_interaction(monkeypatch, harness, sent)
    shown = []
    monkeypatch.setattr(
        polls, "_show_poll_control",
        lambda i, s, k, notice="": shown.append((k, notice)) or asyncio.sleep(0))
    session = SimpleNamespace(guild_id=GUILD_ID, owner_id=OWNER_ID)
    polls._PENDING_POLLS[(GUILD_ID, OWNER_ID)] = _poll()

    async def drive():
        await polls._publish_pending_poll(interaction, session, str(CHANNEL_ID))

    asyncio.run(drive())

    record = harness.published[POST_KEY]
    assert record["poll"]["question"] == "What should we play on Friday?"
    assert record["poll"]["votes"] == {}
    assert polls.TOKEN_RE.fullmatch(record["poll_token"])
    view = channel.sent_kwargs["view"]
    assert [b.custom_id for b in view.children] == [
        poll_custom_id(record["poll_token"], i) for i in range(3)]
    assert sent.deleted is False
    assert shown and shown[0][0] == POST_KEY
    assert (GUILD_ID, OWNER_ID) not in polls._PENDING_POLLS


def test_publish_pending_poll_rolls_back_when_tracking_fails(monkeypatch):
    harness = LibraryHarness({})
    _patch_polls_library(monkeypatch, harness)

    async def failing_save(guild_id, source, lib):
        return False

    monkeypatch.setattr(polls, "_save_library", failing_save)
    sent = _FakeSentMessage(MESSAGE_ID)
    interaction, _channel = _publish_interaction(monkeypatch, harness, sent)
    said = []

    async def fake_say(i, text, **kwargs):
        said.append(text)

    monkeypatch.setattr(polls, "_say", fake_say)
    session = SimpleNamespace(guild_id=GUILD_ID, owner_id=OWNER_ID)
    polls._PENDING_POLLS[(GUILD_ID, OWNER_ID)] = _poll()

    asyncio.run(polls._publish_pending_poll(interaction, session, str(CHANNEL_ID)))

    # An untracked poll could never record votes: the message is removed
    # and the manager is told plainly.
    assert sent.deleted is True
    assert harness.published == {}
    assert any("could not be saved" in text for text in said)


# ---------------------------------------------------------------------------
# Sticky move: votes ride along, never reset
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


def test_sticky_repost_carries_poll_votes(monkeypatch):
    record = _record(
        poll=_poll(votes={"42": 0, "43": 0, "44": 2}),
        sticky={"enabled": True, "last_sent_at": "2026-11-19T00:00:00+00:00"},
    )
    harness = LibraryHarness({POST_KEY: record})
    monkeypatch.setattr(sticky, "_load_library", harness.load)
    monkeypatch.setattr(sticky, "_save_library", harness.save)

    async def no_community_sticky(channel_id):
        return None

    monkeypatch.setattr(sticky, "get_sticky", no_community_sticky)

    deleted = []

    async def delete():
        deleted.append(MESSAGE_ID)

    live_message = SimpleNamespace(
        id=MESSAGE_ID, author=SimpleNamespace(id=BOT_ID),
        content="", embeds=[poll_embed(record["poll"])], components=[],
        jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}",
        delete=delete,
    )

    async def fetch_impl(message_id):
        assert message_id == MESSAGE_ID
        return live_message

    async def send_impl(*, content=None, **kwargs):
        return SimpleNamespace(
            id=NEW_MESSAGE_ID, author=SimpleNamespace(id=BOT_ID),
            content=content or "", embeds=kwargs.get("embeds") or [],
            components=[],
            jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{NEW_MESSAGE_ID}",
            delete=delete,
        )

    channel = object.__new__(FakeTextChannel)
    channel.id = CHANNEL_ID
    channel.name = "polls"
    channel.fetch_impl = fetch_impl
    channel.send_impl = send_impl
    channel.sent = []
    guild = SimpleNamespace(
        id=GUILD_ID, owner_id=OWNER_ID, name="Test Guild", member_count=42,
        get_channel=lambda cid: channel if cid == CHANNEL_ID else None,
        get_role=lambda rid: None,
    )
    channel.guild = guild
    listeners = []
    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID), guilds=[guild],
        add_listener=lambda fn, event: listeners.append((fn, event)),
    )
    runtime = StudioStickyRuntime(bot)
    entry = StudioStickyEntry(
        GUILD_ID, CHANNEL_ID, POST_KEY,
        last_sent_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    runtime.set_sticky(entry)

    result = asyncio.run(runtime.refresh_channel(channel, entry=entry, force=True))

    assert result is not None and result.id == NEW_MESSAGE_ID
    assert deleted == [MESSAGE_ID]
    assert POST_KEY not in harness.published
    moved = harness.published[NEW_POST_KEY]
    assert post_is_sticky(moved) is True
    # The whole point: identical votes and token on the new copy, so the
    # old buttons can never double-count and no vote is ever reset.
    assert moved["poll"]["votes"] == {"42": 0, "43": 0, "44": 2}
    assert moved["poll_token"] == TOKEN
    sent = channel.sent[0]
    assert sent["content"] is None
    assert "Minecraft** — 2 votes (67%)" in sent["embeds"][0].fields[0].value
    assert [b.custom_id for b in sent["view"].children] == [
        poll_custom_id(TOKEN, i) for i in range(3)]


# ---------------------------------------------------------------------------
# Migration converters (legacy Community Tools -> Studio)
# ---------------------------------------------------------------------------

def test_studio_poll_from_legacy_preserves_votes_and_state():
    legacy = {
        "question": "Best night for movie night?",
        "options": ["Friday", "Saturday"],
        "votes": {"111": 1, "222": 0, "333": 1},
        "state": "paused",
    }
    converted = studio_poll_from_legacy(legacy)
    assert converted == {
        "question": "Best night for movie night?",
        "options": ["Friday", "Saturday"],
        "votes": {"111": 1, "222": 0, "333": 1},
        "state": "paused",
    }


def test_studio_record_from_legacy_sticky_adopts_live_message():
    config = {
        "guild_id": GUILD_ID, "channel_id": CHANNEL_ID,
        "last_message_id": MESSAGE_ID, "updated_by": OWNER_ID,
    }
    legacy_poll = {
        "question": "Q?", "options": ["A", "B"],
        "votes": {"7": 1}, "state": "active",
    }
    key, record = studio_record_from_legacy_sticky(config, legacy_poll)
    assert key == POST_KEY
    assert record["channel_id"] == str(CHANNEL_ID)
    assert record["message_id"] == str(MESSAGE_ID)
    assert record["sticky"] == {"enabled": True, "last_sent_at": None}
    assert record["poll"]["votes"] == {"7": 1}
    assert polls.TOKEN_RE.fullmatch(record["poll_token"])

    key, plain = studio_record_from_legacy_sticky(config)
    assert key == POST_KEY
    assert "poll" not in plain and "poll_token" not in plain
    assert plain["sticky"]["enabled"] is True

    with pytest.raises(StudioValidationError):
        studio_record_from_legacy_sticky({**config, "last_message_id": 0})


# ---------------------------------------------------------------------------
# Registry: poll words route to Post Builder
# ---------------------------------------------------------------------------

def test_poll_words_route_to_post_builder():
    for query in ("sticky poll", "create poll", "vote", "server poll"):
        assert registry.search_features(query)[0].key == "embed_studio", query
    # Bare "poll" also surfaces Community Tools, whose native Discord
    # polls are a separate feature that stays; Post Builder must be in
    # the results a member sees.
    keys = [item.key for item in registry.search_features("poll")]
    assert "embed_studio" in keys
