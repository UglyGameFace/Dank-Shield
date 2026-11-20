"""Delete Post + one-tap Repost for Post Builder published posts.

Delete Post: two-step confirm naming the post; the Discord message and
the published record go together; drafts are never touched; an
already-gone message still removes the record; a Forbidden delete
keeps the record and says so.

Repost: a deleted post is flagged (delete listener / reconcile), the
Published post-actions screen offers one-tap Repost, which re-checks
the message, re-sends the record's saved copy, repoints the same
record, and restores stickiness. Polls restart at zero votes; ended
polls and Live Text posts are refused in plain words.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import discord

from stoney_verify import embed_studio_post_actions as actions
from stoney_verify import embed_studio_sticky as sticky
from stoney_verify.embed_studio_core import library_payload, new_project, parse_library
from stoney_verify.embed_studio_post_actions import (
    StudioDeletePostConfirmView,
    StudioPostActionsView,
    delete_published_post,
    repost_published_post,
)
from stoney_verify.embed_studio_sticky import StudioStickyRuntime, post_is_sticky
from stoney_verify.embed_studio_ui import StudioSession

GUILD_ID, OWNER_ID, BOT_ID = 5, 10, 999
CHANNEL_ID, MESSAGE_ID = 99, 100
POST_KEY = f"{CHANNEL_ID}:{MESSAGE_ID}"
NEW_MESSAGE_ID = 555


def _record(message_id=MESSAGE_ID, **extra):
    row = {
        "channel_id": str(CHANNEL_ID), "message_id": str(message_id),
        "by": str(OWNER_ID), "role_token": "", "role_mode": "buttons",
        "role_actions": [], "form": None, "form_token": "",
    }
    row.update(extra)
    return row


def _project(title="Server Night", content="Come play"):
    project = new_project()
    project["content"] = content
    project["embeds"][0]["title"] = title
    return project


class LibraryHarness:
    def __init__(self, published, drafts=None):
        lib = parse_library({"embed_studio_v1": {
            "published": published, "drafts": drafts or {},
        }})
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

    @property
    def drafts(self):
        return self.state["value"]["drafts"]


def _patch(monkeypatch, harness):
    for module in (actions, sticky):
        monkeypatch.setattr(module, "_load_library", harness.load)
        monkeypatch.setattr(module, "_save_library", harness.save)


class FakeTextChannel(discord.TextChannel):
    __slots__ = ("fetch_impl", "send_impl", "sent", "fetch_calls")

    async def fetch_message(self, message_id):
        self.fetch_calls.append(message_id)
        return await self.fetch_impl(message_id)

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return await self.send_impl(content=content, **kwargs)

    def permissions_for(self, bot):
        return discord.Permissions.all()


def _message(message_id, *, deleted=None, delete_impl=None):
    async def delete():
        if delete_impl is not None:
            return await delete_impl()
        if deleted is not None:
            deleted.append(message_id)

    return SimpleNamespace(
        id=message_id, author=SimpleNamespace(id=BOT_ID),
        content="Come play", embeds=[], components=[],
        jump_url=f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}",
        delete=delete,
    )


def _not_found(_message_id):
    raise discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")


def _forbidden(_message_id):
    raise discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


class Rig:
    def __init__(self, monkeypatch, *, record=None, fetch_impl=None, send_impl=None):
        draft = {"version": 1, "updated_at": 1, "post_key": POST_KEY,
                 "project": _project()}
        self.harness = LibraryHarness(
            {POST_KEY: record if record is not None else _record()},
            drafts={str(OWNER_ID): draft},
        )
        _patch(monkeypatch, self.harness)
        self.deleted = []
        self.live = _message(MESSAGE_ID, deleted=self.deleted)

        async def default_fetch(message_id):
            return self.live

        async def default_send(*, content=None, **kwargs):
            return _message(NEW_MESSAGE_ID, deleted=self.deleted)

        self.channel = object.__new__(FakeTextChannel)
        self.channel.id = CHANNEL_ID
        self.channel.name = "minecraft-chat"
        self.channel.fetch_impl = fetch_impl or default_fetch
        self.channel.send_impl = send_impl or default_send
        self.channel.sent = []
        self.channel.fetch_calls = []
        self.guild = SimpleNamespace(
            id=GUILD_ID, owner_id=OWNER_ID, name="Test Guild",
            text_channels=[self.channel],
            get_channel=lambda cid: self.channel if cid == CHANNEL_ID else None,
            get_role=lambda rid: None,
        )
        self.channel.guild = self.guild
        listeners = []
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=BOT_ID), guilds=[self.guild],
            add_listener=lambda fn, event: listeners.append((fn, event)),
        )
        self.bot._listeners = listeners


# ---------------------------------------------------------------------------
# Delete Post
# ---------------------------------------------------------------------------

def test_delete_happy_path_deletes_message_and_record_keeps_draft(monkeypatch):
    rig = Rig(monkeypatch)
    ok, message = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert "Post deleted" in message
    assert rig.deleted == [MESSAGE_ID]
    assert rig.harness.published == {}
    # The saved draft is untouched, so the post can be published again.
    assert rig.harness.drafts[str(OWNER_ID)]["project"]["embeds"][0]["title"] == "Server Night"


def test_delete_already_gone_message_still_removes_record(monkeypatch):
    async def fetch(message_id):
        return _not_found(message_id)

    rig = Rig(monkeypatch, fetch_impl=fetch)
    ok, message = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert "already deleted in Discord" in message
    assert "just removed it from Post Builder" in message
    assert rig.harness.published == {}


def test_delete_flagged_record_skips_fetch_and_removes_record(monkeypatch):
    async def fetch(message_id):
        raise AssertionError("flagged records must not be fetched")

    rig = Rig(monkeypatch, record=_record(message_missing=True), fetch_impl=fetch)
    ok, message = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert "already deleted in Discord" in message
    assert rig.harness.published == {}


def test_delete_forbidden_on_fetch_keeps_record(monkeypatch):
    async def fetch(message_id):
        return _forbidden(message_id)

    rig = Rig(monkeypatch, fetch_impl=fetch)
    ok, message = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "still tracked" in message
    assert POST_KEY in rig.harness.published


def test_delete_forbidden_on_message_delete_keeps_record(monkeypatch):
    async def bad_delete():
        raise discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    rig = Rig(monkeypatch)
    rig.live = _message(MESSAGE_ID, delete_impl=bad_delete)

    async def fetch(message_id):
        return rig.live

    rig.channel.fetch_impl = fetch
    ok, message = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "Manage Messages" in message
    assert "still tracked" in message
    assert POST_KEY in rig.harness.published


def test_delete_sticky_post_removes_record_and_runtime_entry(monkeypatch):
    record = _record(sticky={"enabled": True, "last_sent_at": "2026-11-19T00:00:00+00:00"})
    rig = Rig(monkeypatch, record=record)
    runtime = sticky.ensure_studio_sticky_runtime(rig.bot)
    runtime.set_sticky(sticky.StudioStickyEntry(GUILD_ID, CHANNEL_ID, POST_KEY))
    ok, _message_text = asyncio.run(delete_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert rig.harness.published == {}
    assert runtime.entry_for(CHANNEL_ID) is None


def test_delete_confirm_view_names_the_post(monkeypatch):
    rig = Rig(monkeypatch, record=_record(repost_source=_project()))
    captured = {}

    async def fake_menu(interaction, *, heading, description, view):
        captured.update(heading=heading, description=description, view=view)

    monkeypatch.setattr(actions, "_menu", fake_menu)
    interaction = SimpleNamespace(guild=rig.guild)
    session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    asyncio.run(actions.open_delete_post_confirm(interaction, session, POST_KEY))
    assert "Server Night" in captured["description"]
    assert "#minecraft-chat" in captured["description"]
    assert "saved draft is **not** deleted" in captured["description"]
    assert isinstance(captured["view"], StudioDeletePostConfirmView)
    ids = {getattr(item, "custom_id", None) for item in captured["view"].children}
    assert "dank:embed_studio:delete_confirm:v1" in ids
    assert "dank:embed_studio:delete_cancel:v1" in ids


def test_post_actions_view_custom_ids_and_repost_gating():
    session = StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    live_view = StudioPostActionsView(session, POST_KEY, missing=False)
    ids = {getattr(item, "custom_id", None) for item in live_view.children}
    assert "dank:embed_studio:delete_post:v1" in ids
    assert "dank:embed_studio:repost:v1" in ids
    assert live_view.repost.disabled is True
    assert live_view.edit.disabled is False
    missing_view = StudioPostActionsView(session, POST_KEY, missing=True)
    assert missing_view.repost.disabled is False
    assert missing_view.edit.disabled is True


# ---------------------------------------------------------------------------
# Detection: the delete listener + flag bookkeeping
# ---------------------------------------------------------------------------

def test_on_message_delete_marks_tracked_record_and_preserves_stickiness(monkeypatch):
    record = _record(sticky={"enabled": True, "last_sent_at": "2026-11-19T00:00:00+00:00"})
    rig = Rig(monkeypatch, record=record)
    runtime = StudioStickyRuntime(rig.bot)
    runtime.set_sticky(sticky.StudioStickyEntry(GUILD_ID, CHANNEL_ID, POST_KEY))
    deleted_message = SimpleNamespace(
        id=MESSAGE_ID, guild=rig.guild, channel=rig.channel,
        author=SimpleNamespace(id=BOT_ID))
    asyncio.run(runtime.on_message_delete(deleted_message))
    saved = rig.harness.published[POST_KEY]
    assert saved["message_missing"] is True
    assert saved["was_sticky"] is True
    assert post_is_sticky(saved) is False
    assert runtime.entry_for(CHANNEL_ID) is None


def test_on_message_delete_ignores_untracked_and_foreign_messages(monkeypatch):
    rig = Rig(monkeypatch)
    runtime = StudioStickyRuntime(rig.bot)
    untracked = SimpleNamespace(
        id=424242, guild=rig.guild, channel=rig.channel,
        author=SimpleNamespace(id=BOT_ID))
    asyncio.run(runtime.on_message_delete(untracked))
    foreign = SimpleNamespace(
        id=MESSAGE_ID, guild=rig.guild, channel=rig.channel,
        author=SimpleNamespace(id=777))
    asyncio.run(runtime.on_message_delete(foreign))
    assert "message_missing" not in rig.harness.published[POST_KEY]
    assert rig.harness.saves == []


# ---------------------------------------------------------------------------
# Repost
# ---------------------------------------------------------------------------

def _missing_rig(monkeypatch, **record_extra):
    async def fetch(message_id):
        return _not_found(message_id)

    record = _record(message_missing=True, **record_extra)
    return Rig(monkeypatch, record=record, fetch_impl=fetch)


def test_repost_happy_path_repoints_record_and_clears_flag(monkeypatch):
    rig = _missing_rig(monkeypatch, repost_source=_project())
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert message == "Post is back up in #minecraft-chat."
    assert len(rig.channel.sent) == 1
    assert rig.channel.sent[0]["embeds"][0].title == "Server Night"
    saved = rig.harness.published[POST_KEY]  # same post_key, new message id
    assert saved["message_id"] == str(NEW_MESSAGE_ID)
    assert not saved.get("message_missing")


def test_repost_refuses_when_message_still_up_and_clears_stale_flag(monkeypatch):
    rig = Rig(monkeypatch, record=_record(message_missing=True, repost_source=_project()))
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "still up — nothing to repost" in message
    assert rig.channel.sent == []
    assert not rig.harness.published[POST_KEY].get("message_missing")


def test_repost_sticky_record_restores_stickiness(monkeypatch):
    rig = _missing_rig(
        monkeypatch, repost_source=_project(),
        was_sticky=True, sticky={"enabled": False, "last_sent_at": None})
    runtime = sticky.ensure_studio_sticky_runtime(rig.bot)
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert "sticky again" in message
    saved = rig.harness.published[POST_KEY]
    assert post_is_sticky(saved) is True
    entry = runtime.entry_for(CHANNEL_ID)
    assert entry is not None and entry.post_key == POST_KEY


def test_repost_poll_zeroes_votes(monkeypatch):
    poll = {"question": "Play Friday?", "options": ["Minecraft", "Movie"],
            "votes": {"111": 0, "222": 1}, "state": "paused"}
    rig = _missing_rig(monkeypatch, poll=poll, poll_token="ab" * 16)
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is True
    assert "votes start over" in message
    saved = rig.harness.published[POST_KEY]
    assert saved["poll"]["votes"] == {}
    assert saved["poll"]["state"] == "active"
    assert saved["message_id"] == str(NEW_MESSAGE_ID)


def test_repost_ended_poll_refused(monkeypatch):
    poll = {"question": "Done?", "options": ["A", "B"], "votes": {}, "state": "ended"}
    rig = _missing_rig(monkeypatch, poll=poll, poll_token="ab" * 16)
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "already ended" in message
    assert rig.channel.sent == []


def test_repost_live_text_refused(monkeypatch):
    rig = _missing_rig(
        monkeypatch, live_nonce="nonce", live_source=_project())
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "Live Text" in message
    assert rig.channel.sent == []


def test_repost_without_saved_copy_refused_plainly(monkeypatch):
    rig = _missing_rig(monkeypatch)
    ok, message = asyncio.run(repost_published_post(
        GUILD_ID, POST_KEY, guild=rig.guild, client=rig.bot))
    assert ok is False
    assert "don't have a saved copy" in message
    assert rig.channel.sent == []
    # The record stays flagged — Delete Post can still remove it.
    assert rig.harness.published[POST_KEY]["message_missing"] is True
