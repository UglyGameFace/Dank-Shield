from __future__ import annotations

"""Regression: closed /toke sessions must not look live or time out on clicks."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke_closed.json")
    live._REGISTERED.clear()
    return tmp_path


def _sample(*, expires=None):
    return {
        "guild_id": 123, "message_id": 456, "channel_id": 789,
        "starter_id": 100, "starter_role_id": 101, "notify_role_id": 102,
        "expires_at": time.time() + 3600 if expires is None else expires,
        "show_member_names": True, "voice_channel_id": 999,
        "cheers_ids": [100, 200], "going_ids": [100],
    }


def test_expired_session_remains_pending_until_discord_card_edit_succeeds(isolated_store):
    async def work():
        await toke_sessions.save_new(_sample(expires=time.time() - 1))
        # A prior click/read must NOT permanently drain the closure worker.
        already_closed = await toke_sessions.get(123, 456)
        assert already_closed["active"] is False
        assert already_closed["close_reason"] == "expired"
        pending = await toke_sessions.pending_closed()
        assert len(pending) == 1 and pending[0]["active"] is False
        assert await toke_sessions.end_expired() == pending
        # Simulate a restart by rereading the persisted storage.
        assert len(await toke_sessions.recoverable_views()) == 1
        assert len(await toke_sessions.pending_closed()) == 1
        await toke_sessions.mark_closed_synced(123, 456)
        assert await toke_sessions.pending_closed() == []
        assert (await toke_sessions.get(123, 456))["close_synced"] is True
        assert await toke_sessions.recoverable_views() == []
    asyncio.run(work())


def test_explicit_end_is_durable_and_awaits_card_confirmation(isolated_store):
    async def work():
        await toke_sessions.save_new(_sample())
        ended, err = await toke_sessions.react(123, 456, 100, "end")
        assert not err and not ended["active"]
        assert ended["close_reason"] == "ended"
        assert ended["close_pending"]
        assert len(await toke_sessions.pending_closed()) == 1
        await toke_sessions.mark_closed_synced(123, 456)
        assert not await toke_sessions.pending_closed()
    asyncio.run(work())


def test_legacy_inactive_record_with_stale_buttons_gets_reconciled(isolated_store):
    data = _sample(expires=time.time() - 120)
    data["active"] = False
    toke_sessions._write({"123:456": data})
    async def work():
        sessions = await toke_sessions.pending_closed()
        assert sessions[0]["close_pending"] is True
        assert sessions[0]["close_reason"] == "expired"
    asyncio.run(work())


def test_render_expired_card_cannot_look_active_or_show_live_vc_count():
    session = {**_sample(expires=time.time() - 45), "active": True}
    original = discord.Embed(
        title="💨 Toke Time",
        description="**RJ** is calling the sesh crowd.\n\n> hello",
        color=discord.Color.green(),
    )
    # Even a stale active=True snapshot must be rendered as closed.
    guild = SimpleNamespace(get_channel=lambda _id: None)
    card = live.render_card(guild, session, original)
    assert card.title == "💨 Toke Time · Sesh Ended"
    assert card.color == discord.Color.dark_grey()
    assert "hosted this sesh." in card.description
    assert "is calling" not in card.description
    assert "Sesh ended" in card.fields[0].name or "sesh has ended" in card.fields[0].name
    assert "Ends" not in "\n".join(str(field.value) for field in card.fields)
    assert "In voice now" not in "\n".join(str(field.value) for field in card.fields)
    assert "Final responses" in card.fields[1].name


def _bot_and_message(monkeypatch, *, fail=False):
    class FakeChannel:
        def __init__(self):
            self.message = None

        async def fetch_message(self, mid):
            assert mid == 456
            return self.message

    class FakeMessage:
        def __init__(self):
            self.embeds = [discord.Embed(title="💨 Toke Time", description="RJ is calling the sesh crowd.")]
            self.edits = []
            self.fail = fail

        async def edit(self, **kwargs):
            if self.fail:
                raise OSError("transient network outage")
            self.edits.append(kwargs)
            self.embeds = [kwargs["embed"]]

    channel = FakeChannel()
    message = FakeMessage()
    channel.message = message
    guild = SimpleNamespace(id=123, get_channel=lambda cid: channel if cid == 789 else None)
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 123 else None)
    monkeypatch.setattr(live.discord, "TextChannel", FakeChannel)
    return bot, message


def test_discord_close_edit_removes_buttons_and_retries_failure(isolated_store, monkeypatch):
    async def work():
        await toke_sessions.save_new(_sample(expires=time.time() - 2))
        bot, message = _bot_and_message(monkeypatch, fail=True)
        session = await toke_sessions.get(123, 456)
        with pytest.raises(OSError):
            await live.refresh_card(bot, session)
        assert (await toke_sessions.pending_closed())[0]["close_pending"]
        message.fail = False
        assert await live.refresh_card(bot, session) is True
        assert len(message.edits) == 1
        assert message.edits[0]["view"] is None  # Physically remove old active buttons
        assert message.edits[0]["embed"].title.endswith("Sesh Ended")
        assert await toke_sessions.pending_closed() == []
    asyncio.run(work())


def test_expired_view_restores_dispatch_until_public_button_cleanup(isolated_store):
    async def work():
        await toke_sessions.save_new(_sample(expires=time.time() - 2))
        await toke_sessions.get(123, 456)
        registered = []
        bot = SimpleNamespace(
            get_guild=lambda gid: SimpleNamespace(id=gid),
            add_view=lambda view, message_id: registered.append((view, message_id)),
        )
        count = await live.restore_active_views(bot)
        assert count == 1
        view, mid = registered[0]
        assert mid == 456 and view.timeout is None
        assert all(
            not item.disabled for item in view.children
            if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link
        )
        await toke_sessions.mark_closed_synced(123, 456)
        live._REGISTERED.clear()
        assert await live.restore_active_views(bot) == 0
    asyncio.run(work())


def test_stale_expired_button_acknowledged_before_disk_and_discord_edits(isolated_store, monkeypatch):
    async def work():
        await toke_sessions.save_new(_sample(expires=time.time() - 1))
        class FakeMember:
            id = 100
        monkeypatch.setattr(live.discord, "Member", FakeMember)
        calls = []
        class Response:
            async def defer(self, **kwargs):
                calls.append(("defer", kwargs))
            async def send_message(self, *_args, **_kwargs):
                raise AssertionError("should defer immediately")
        async def edit_original_response(**kwargs):
            calls.append(("answer", kwargs["content"]))
        async def sync_card(*args):
            calls.append(("sync", None))
            return True
        monkeypatch.setattr(live, "refresh_card", sync_card)
        message = SimpleNamespace(id=456)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123), user=FakeMember(),
            message=message, response=Response(), client=object(),
            edit_original_response=edit_original_response,
        )
        view = live.TokeCheersView(100, 101, guild_id=123, message_id=456)
        await view._respond(interaction, "cheers")
        assert calls[0][0] == "defer"
        assert calls[1][0] == "sync"
        assert calls[2][0] == "answer"
        assert "already ended" in calls[2][1]
    asyncio.run(work())


def test_mobile_roster_preview_bounded_with_pagination(monkeypatch):
    class FakeVoice:
        mention = "<#999>"
        members = [SimpleNamespace(id=i) for i in range(100, 131)]
    monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
    guild = SimpleNamespace(get_channel=lambda _cid: FakeVoice())
    session = {**_sample(), "active": True, "cheers_ids": list(range(101, 131)), "going_ids": list(range(105, 135))}
    card = live.render_card(guild, session, discord.Embed(title="Toke Time", description="RJ is calling the sesh crowd."))
    assert "Sesh activity" in card.fields[0].name
    assert "Cheers: 30" in card.fields[0].value
    assert "Joining: 30" in card.fields[0].value
    assert "In VC: 31" in card.fields[0].value
    assert card.fields[1].name.startswith("👥 People")
    assert len(card.fields[1].value.splitlines()) == 4
    assert "+32 more" in card.fields[1].value
    assert "<@100>" not in card.fields[1].value  # Host is already in header
    assert "👑" in card.fields[1].value
    view = live.TokeRosterView(guild, session)
    assert len(view.lines) == 35
    assert view.pages == 3
