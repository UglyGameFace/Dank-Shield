from __future__ import annotations

"""Regression tests for compact uploaded Toke cards and truthful attendance."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "sessions.json")
    live._VOICE_PENDING.clear()
    live._VOICE_DEPARTED.clear()
    live._VOICE_WORKERS.clear()
    return tmp_path


def _session(gid=100, mid=200, vc=300):
    return {
        "guild_id": gid, "message_id": mid, "channel_id": 700,
        "starter_id": 1, "starter_role_id": 9, "notify_role_id": 10,
        "voice_channel_id": vc, "show_member_names": True,
        "max_interested": 100, "expires_at": time.time() + 3600,
    }


def test_planning_stays_until_present_then_departure_retires_it(storage):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.save_new(_session(gid=101, mid=200))
        await toke_sessions.react(100, 200, 45, "going")
        # A never-arrived RSVP is an intention, not a detected departure.
        planned = await toke_sessions.reconcile_voice(100, 200, set())
        assert planned["going_ids"] == [45] and planned["cheers_ids"] == [45]
        present = await toke_sessions.reconcile_voice(100, 200, {45})
        assert present["voice_seen_ids"] == [45]
        # Persisted observation survives a simulated restart/read.
        assert (await toke_sessions.get(100, 200))["voice_seen_ids"] == [45]
        left = await toke_sessions.reconcile_voice(100, 200, set())
        assert left["voice_seen_ids"] == []
        assert left["cheers_ids"] == left["going_ids"] == []
        assert left["past_cheers_ids"] == [45]
        assert left["past_going_ids"] == [45]
        other = await toke_sessions.get(101, 200)
        assert other["cheers_ids"] == []
        # A person can RSVP again after attending and leaving.
        again, error = await toke_sessions.react(100, 200, 45, "going")
        assert not error and again["going_ids"] == [45]
        again = await toke_sessions.reconcile_voice(100, 200, set())
        assert again["going_ids"] == [45]
        assert again["past_going_ids"] == [45]
    asyncio.run(run())



def test_lifetime_response_summary_preserves_departed_member_without_showing_live(storage):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        await toke_sessions.reconcile_voice(100, 200, {45})
        left = await toke_sessions.reconcile_voice(100, 200, set())
        guild = SimpleNamespace(get_channel=lambda _cid: None)
        card = live.render_card(guild, left, discord.Embed(title="💨 Toke Time"))
        assert "Cheers: 0" in card.fields[0].value
        assert "Joining: 0" in card.fields[0].value
        assert "<@45>" not in "\n".join(f.value for f in card.fields)
        await toke_sessions.react(100, 200, 1, "end")
        ended = await toke_sessions.get(100, 200)
        closed = live.render_card(guild, ended, discord.Embed(title="💨 Toke Time"))
        assert "Cheers **1**" in closed.fields[1].value
        assert "Joining **1**" in closed.fields[1].value
        # Attending and leaving twice must not inflate lifetime counts.
        assert len(ended["past_going_ids"]) == 1
    asyncio.run(run())


def test_rapid_in_and_out_event_clears_unobserved_rsvp(storage):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        # Join+leave inside debounce can be missed by periodic voice.member reads.
        departed = await toke_sessions.reconcile_voice(100, 200, set(), departed_ids={45})
        assert departed["cheers_ids"] == departed["going_ids"] == []
        assert departed["past_cheers_ids"] == departed["past_going_ids"] == [45]
        await toke_sessions.react(100, 200, 45, "going")
        # A quick rejoin wins over a queued departure notification.
        rejoined = await toke_sessions.reconcile_voice(100, 200, {45}, departed_ids={45})
        assert rejoined["cheers_ids"] == rejoined["going_ids"] == [45]
        assert rejoined["voice_seen_ids"] == [45]
        # Withdrawal does not leave any lingering seen/attendance marker.
        await toke_sessions.react(100, 200, 45, "withdraw")
        withdrawn = await toke_sessions.get(100, 200)
        assert withdrawn["voice_seen_ids"] == []
    asyncio.run(run())


def test_reconciliation_is_idempotent_and_not_allowed_to_reopen_ended(storage):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        first = await toke_sessions.reconcile_voice(100, 200, {45})
        second = await toke_sessions.reconcile_voice(100, 200, {45})
        assert first == second
        await toke_sessions.react(100, 200, 1, "end")
        ended = await toke_sessions.reconcile_voice(100, 200, set(), departed_ids={45})
        assert ended["active"] is False and ended["close_pending"] is True
        assert ended["going_ids"] == [45]  # Historical final responses remain.
    asyncio.run(run())


def test_inline_uploaded_gallery_has_single_full_width_media_and_reused_callbacks(monkeypatch):
    monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0.001)
    session = {
        **_session(), "active": True, "cheers_ids": [], "going_ids": [],
        "media_layout": "thumbnail_v2", "media_filename": "sesh.gif",
    }
    guild = SimpleNamespace(get_channel=lambda cid: None)
    intro = "## 💨 Toke Time\n<@&10>\n**Test Host** is calling the sesh crowd.\n> Join us"
    view = live.TokeMediaLayout(guild, session, intro=intro)
    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout is None and view.is_persistent()
    nodes = list(view.walk_children())
    assert not any(isinstance(item, discord.ui.Thumbnail) for item in nodes)
    galleries = [item for item in nodes if isinstance(item, discord.ui.MediaGallery)]
    assert len(galleries) == 1
    assert len(galleries[0].items) == 1
    assert galleries[0].items[0].media.url == "attachment://sesh.gif"
    assert len([x for x in nodes if isinstance(x, discord.ui.Button)]) == 7
    assert any(isinstance(x, discord.ui.TextDisplay) and "<@&10>" in x.content for x in nodes)

    dispatched = []
    async def fake_respond(self, interaction, action):
        dispatched.append(action)
    monkeypatch.setattr(live.TokeCheersView, "_respond", fake_respond)
    updated = live.TokeMediaLayout(guild, session, intro=intro)
    join = next(x for x in updated.walk_children() if getattr(x, "custom_id", "") == "dank:toke:v2:going")
    asyncio.run(join.callback(object()))
    assert dispatched == ["going"]
    session["active"] = False
    closed = live.TokeMediaLayout(guild, session, intro=intro)
    assert not any(isinstance(x, discord.ui.Button) for x in closed.walk_children())



def test_closed_toke_gallery_title_is_idempotent_on_repeated_edits():
    data = {
        **_session(), "active": False, "media_layout": "thumbnail_v2",
        "media_filename": "sesh.gif", "cheers_ids": [], "going_ids": [],
    }
    guild = SimpleNamespace(get_channel=lambda _cid: None)
    intro = "## 💨 Toke Time\n<@&10>\nHost is calling the sesh crowd."
    once = live.TokeMediaLayout(guild, data, intro=intro)
    display = next(x.content for x in once.walk_children() if isinstance(x, discord.ui.TextDisplay))
    twice = live.TokeMediaLayout(guild, data, intro=display)
    repeated = next(x.content for x in twice.walk_children() if isinstance(x, discord.ui.TextDisplay))
    assert display.count("Sesh Ended") == 1
    assert repeated.count("Sesh Ended") == 1
    assert "hosted this sesh" in repeated


def test_existing_note_can_be_reused_without_persisting_it(storage):
    intro = "## 💨 Toke Time\n<@&10>\n**Host** is calling the sesh crowd.\n> Original note"
    message = SimpleNamespace(components=[
        SimpleNamespace(children=[SimpleNamespace(children=[SimpleNamespace(content=intro)])])
    ])
    assert live._media_intro(message, _session()) == intro
    async def run():
        await toke_sessions.save_new({**_session(), "media_layout": "thumbnail_v2", "media_filename": "sesh.gif"})
    asyncio.run(run())
    saved = toke_sessions.SESSIONS_FILE.read_text()
    assert "Original note" not in saved
    assert "sesh.gif" in saved


def test_inaccessible_vc_reports_unknown_instead_of_false_zero():
    session = {
        **_session(), "active": True, "cheers_ids": [45], "going_ids": [45],
    }
    guild = SimpleNamespace(get_channel=lambda _cid: None)
    card = live.render_card(guild, session, discord.Embed(title="Toke Time"))
    assert "In VC: Unavailable" in card.fields[0].value
    assert "VC unavailable" in card.fields[0].value
    session["voice_channel_id"] = 0
    no_voice = live.render_card(guild, session, discord.Embed(title="Toke Time"))
    assert "In VC: Not selected" in no_voice.fields[0].value


def test_voice_unavailable_does_not_make_uncertain_rsvps_disappear(storage):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        guild = SimpleNamespace(get_channel=lambda _cid: None)
        state = await live.reconcile_live_attendance(
            guild, await toke_sessions.get(100, 200), departed_ids={45},
        )
        assert state["going_ids"] == [45]
    asyncio.run(run())


def test_voice_burst_preserves_departure_identity_even_without_seen_cache(storage, monkeypatch):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0.001)
        class FakeVoice:
            id = 300
            members = []
        monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
        voice = FakeVoice()
        guild = SimpleNamespace(id=100, get_channel=lambda cid: voice if cid == 300 else None)
        async def active(gid):
            return await toke_sessions.all_active(gid)
        # Exercise the real debounced worker, storage and an actual refresh path.
        calls = []
        async def edited(_bot, session, interaction=None, *, departed_ids=None):
            calls.append(departed_ids)
            await live.reconcile_live_attendance(guild, session, departed_ids=departed_ids)
            return True
        monkeypatch.setattr(live, "refresh_card", edited)
        member = SimpleNamespace(id=45, guild=guild)
        await live._on_toke_voice_state(
            member, SimpleNamespace(channel=voice),
            SimpleNamespace(channel=None), bot=object(),
        )
        await asyncio.wait_for(live._VOICE_WORKERS[100], timeout=1.0)
        assert calls == [{45}]
        assert (await toke_sessions.get(100, 200))["going_ids"] == []
        assert not live._VOICE_DEPARTED and not live._VOICE_PENDING
    asyncio.run(run())


def test_v2_card_edit_keeps_full_width_gallery_and_custom_buttons(storage, monkeypatch):
    class FakeTextChannel:
        id = 700
        def __init__(self, message):
            self.message = message
        async def fetch_message(self, mid):
            assert mid == 200
            return self.message

    class FakeMessage:
        def __init__(self):
            self.edits = []
            self.components = [
                SimpleNamespace(children=[
                    SimpleNamespace(children=[
                        SimpleNamespace(content="## 💨 Toke Time\n<@&10>\n**Host** is calling the sesh crowd.\n> Meet here")
                    ])
                ])
            ]
            self.embeds = []
        async def edit(self, **kwargs):
            self.edits.append(kwargs)

    async def run():
        await toke_sessions.save_new({
            **_session(), "media_layout": "thumbnail_v2",
            "media_filename": "photo.jpg",
        })
        message = FakeMessage()
        channel = FakeTextChannel(message)
        guild = SimpleNamespace(
            id=100, get_channel=lambda cid: channel if cid == 700 else None,
        )
        bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 100 else None)
        monkeypatch.setattr(live.discord, "TextChannel", FakeTextChannel)
        stored = await toke_sessions.get(100, 200)
        assert await live.refresh_card(bot, stored)
        assert len(message.edits) == 1
        assert "embed" not in message.edits[0] and "content" not in message.edits[0]
        view = message.edits[0]["view"]
        assert isinstance(view, live.TokeMediaLayout)
        assert any(
            isinstance(x, discord.ui.MediaGallery) and x.items[0].media.url == "attachment://photo.jpg"
            for x in view.walk_children()
        )
        assert any(
            isinstance(x, discord.ui.TextDisplay) and "Meet here" in x.content
            for x in view.walk_children()
        )
        ended, err = await toke_sessions.react(100, 200, 1, "end")
        assert not err
        assert await live.refresh_card(bot, ended)
        closed = message.edits[-1]["view"]
        assert not any(isinstance(x, discord.ui.Button) for x in closed.walk_children())
        assert not (await toke_sessions.get(100, 200))["close_pending"]
    asyncio.run(run())


def test_who_is_coming_excludes_departed_joiner_after_fresh_reconciliation(storage, monkeypatch):
    class FakeVoice:
        id = 300
        mention = "<#300>"
        members = [SimpleNamespace(id=45)]
    monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
    voice = FakeVoice()
    guild = SimpleNamespace(get_channel=lambda cid: voice if cid == 300 else None)

    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(100, 200, 45, "going")
        await toke_sessions.react(100, 200, 46, "going")
        await toke_sessions.reconcile_voice(100, 200, {45, 46})
        # 46 switched away while bot was down; 45 remains in the sesh VC.
        snapshot = await live.reconcile_live_attendance(
            guild, await toke_sessions.get(100, 200)
        )
        assert snapshot["going_ids"] == [45]
        view = live.TokeRosterView(guild, snapshot)
        body = view.embed().description
        assert "<@45>" in body
        assert "<@46>" not in body
        assert "🔊" in body
    asyncio.run(run())



@pytest.mark.parametrize("layout", ["thumbnail_v2", "gallery_v2"])
def test_uploaded_media_view_restores_after_bot_restart(storage, layout):
    async def run():
        live._REGISTERED.clear()
        await toke_sessions.save_new({
            **_session(), "media_layout": layout,
            "media_filename": "toke-100-1-123.gif",
        })
        guild = SimpleNamespace(id=100, get_channel=lambda cid: None)
        registered = []
        bot = SimpleNamespace(
            get_guild=lambda gid: guild if gid == 100 else None,
            add_view=lambda view, *, message_id: registered.append((view, message_id)),
        )
        assert await live.restore_active_views(bot) == 1
        assert len(registered) == 1
        view, mid = registered[0]
        assert mid == 200 and view.is_persistent()
        assert any(isinstance(item, discord.ui.MediaGallery) for item in view.walk_children())
        custom_ids = {
            button.custom_id for button in view.walk_children()
            if isinstance(button, discord.ui.Button) and button.custom_id
        }
        assert {"dank:toke:v2:roster", "dank:toke:v2:refresh", "dank:toke:v2:going"} <= custom_ids
        assert await live.restore_active_views(bot) == 0
        assert len(registered) == 1
        live._REGISTERED.clear()
    asyncio.run(run())
