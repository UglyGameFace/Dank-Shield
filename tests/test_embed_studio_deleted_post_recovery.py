"""Publish recovery when the tracked Discord post was deleted.

The edit branch used to let ``fetch_message`` raise ``discord.NotFound``
into the broad publish handler, so the user got a raw
"Publish failed: NotFound" and the stale tracking record stayed in the
library forever — every later edit of that post failed identically and
Edit Posted kept offering the ghost. These tests drive the real
``StudioReviewView.publish`` callback with a fake channel.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import embed_studio_ui as ui
from stoney_verify.commands_ext import public_embed_group
from stoney_verify.embed_studio_core import library_payload, new_project, parse_library
from stoney_verify.embed_studio_ui import StudioReviewView, StudioSession

GUILD_ID, OWNER_ID, BOT_ID = 5, 10, 999
CHANNEL_ID, MESSAGE_ID = 99, 100
POST_KEY = f"{CHANNEL_ID}:{MESSAGE_ID}"
NEW_MESSAGE_ID = 101
NEW_POST_KEY = f"{CHANNEL_ID}:{NEW_MESSAGE_ID}"


class FakeTextChannel(discord.TextChannel):
    """A real TextChannel (passes isinstance checks) with stubbed I/O."""

    __slots__ = ("fetch_impl", "send_impl", "sent")

    async def fetch_message(self, message_id):
        return await self.fetch_impl(message_id)

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return await self.send_impl(content=content, **kwargs)

    def permissions_for(self, bot):
        return discord.Permissions.all()


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


def _member(user_id, guild):
    member = object.__new__(discord.Member)
    member._user = SimpleNamespace(id=user_id)
    member.guild = guild
    return member


def _message(message_id, author_id, *, edits):
    async def edit(**kwargs):
        edits.append(kwargs)

    return SimpleNamespace(
        id=message_id, author=SimpleNamespace(id=author_id),
        jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}",
        edit=edit,
    )


class Harness:
    def __init__(self, monkeypatch, *, fetch_impl, send_impl=None):
        lib = parse_library({"embed_studio_v1": {
            "published": {POST_KEY: {"channel_id": str(CHANNEL_ID),
                                     "message_id": str(MESSAGE_ID)}},
        }})
        self.state = {"value": library_payload(lib)}
        self.saves = []

        async def load(guild_id):
            assert guild_id == GUILD_ID
            source = {"embed_studio_v1": deepcopy(self.state["value"])}
            return source, parse_library(source)

        async def save(guild_id, source, lib):
            assert guild_id == GUILD_ID
            if source.get("embed_studio_v1") != self.state["value"]:
                return False
            self.state["value"] = library_payload(lib)
            self.saves.append(deepcopy(self.state["value"]))
            return True

        monkeypatch.setattr(ui, "_load_library", load)
        monkeypatch.setattr(ui, "_save_library", save)

        async def _ok_safe_action(interaction, *, action):
            return True

        monkeypatch.setattr(
            public_embed_group, "_require_safe_embed_action", _ok_safe_action)

        self.channel = object.__new__(FakeTextChannel)
        self.channel.id = CHANNEL_ID
        self.channel.name = "announcements"
        self.channel.fetch_impl = fetch_impl
        self.channel.send_impl = send_impl or self._default_send
        self.channel.sent = []

        self.guild = SimpleNamespace(
            id=GUILD_ID, owner_id=OWNER_ID, name="Test Guild", member_count=42,
            get_channel=lambda cid: self.channel if cid == CHANNEL_ID else None,
            get_role=lambda rid: None,
        )
        self.guild.me = _member(BOT_ID, self.guild)
        self.channel.guild = self.guild

        self.record = []
        self.interaction = SimpleNamespace(
            guild=self.guild, user=_member(OWNER_ID, self.guild),
            client=SimpleNamespace(user=SimpleNamespace(id=BOT_ID)),
            response=_FakeResponse(self.record), followup=_FakeFollowup(self.record),
        )
        self.session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
        self.session.channel_id = CHANNEL_ID
        self.session.post_key = POST_KEY
        self.session.project = new_project()
        self.session.project["content"] = "Updated announcement"

    async def _default_send(self, *, content=None, **kwargs):
        return _message(NEW_MESSAGE_ID, BOT_ID, edits=[])

    def publish(self):
        view = StudioReviewView(self.session)
        return asyncio.run(view.publish.callback(self.interaction))

    @property
    def said(self) -> str:
        return "\n".join(str(item) for item in self.record)


def _not_found(message_id):
    async def fetch(_message_id):
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")
    return fetch


def test_publish_deleted_tracked_post_recovers_in_plain_words(monkeypatch):
    harness = Harness(monkeypatch, fetch_impl=_not_found(MESSAGE_ID))
    project_before = deepcopy(harness.session.project)

    harness.publish()

    # (a) The user gets a plain explanation, never the raw failure.
    said = harness.said
    assert "That post was deleted" in said
    assert "nothing to edit" in said
    assert "brand-new message" in said
    assert "Publish failed" not in said
    assert "NotFound" not in said
    # Nothing was posted as a side effect of the recovery.
    assert harness.channel.sent == []
    # (b) The stale record is gone from the saved library.
    assert harness.state["value"]["published"] == {}
    # The session is detached from the dead record.
    assert harness.session.post_key == ""
    # (c) The draft/project is untouched, and autosave persisted it as-is.
    assert harness.session.project == project_before
    saved_draft = harness.state["value"]["drafts"][str(OWNER_ID)]
    assert saved_draft["project"]["content"] == "Updated announcement"
    assert saved_draft["post_key"] == ""


def test_republish_after_deleted_post_goes_down_fresh_send_path(monkeypatch):
    harness = Harness(monkeypatch, fetch_impl=_not_found(MESSAGE_ID))
    harness.publish()
    assert harness.session.post_key == ""

    # (d) A second publish attempt with the cleared post_key posts fresh.
    harness.record.clear()
    harness.publish()

    assert len(harness.channel.sent) == 1
    assert harness.session.post_key == NEW_POST_KEY
    assert f"✅ Published" in harness.said
    assert NEW_POST_KEY in harness.state["value"]["published"]


def test_publish_live_tracked_post_still_edits_in_place(monkeypatch):
    edits = []
    live = _message(MESSAGE_ID, BOT_ID, edits=edits)

    async def fetch(message_id):
        assert message_id == MESSAGE_ID
        return live

    harness = Harness(monkeypatch, fetch_impl=fetch)
    harness.publish()

    assert len(edits) == 1
    assert edits[0]["content"] == "Updated announcement"
    assert harness.channel.sent == []
    assert "✅ Updated" in harness.said
    assert harness.session.post_key == POST_KEY
    assert POST_KEY in harness.state["value"]["published"]


def test_publish_tracked_post_owned_by_someone_else_is_rejected(monkeypatch):
    foreign = _message(MESSAGE_ID, 4242, edits=[])

    async def fetch(message_id):
        return foreign

    harness = Harness(monkeypatch, fetch_impl=fetch)
    harness.publish()

    assert harness.said == "This message is not owned by Dank Shield. Edit rejected."
    assert harness.session.post_key == POST_KEY
    assert POST_KEY in harness.state["value"]["published"]


def test_publish_deleted_post_still_plain_when_cleanup_save_fails(monkeypatch):
    harness = Harness(monkeypatch, fetch_impl=_not_found(MESSAGE_ID))

    async def failing_save(guild_id, source, lib):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(ui, "_save_library", failing_save)
    harness.publish()

    said = harness.said
    assert "That post was deleted" in said
    assert "nothing to edit" in said
    assert "Publish failed" not in said
    assert "NotFound" not in said
    # The session is still detached so the next publish posts fresh.
    assert harness.session.post_key == ""
