from __future__ import annotations

"""Non-spam personal Toke reminders: strict opt-in, durability, pacing and privacy."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke_sessions.json")
    return tmp_path


def _session(guild=123, message=456, *, mins=60):
    return {
        "guild_id": guild, "message_id": message, "channel_id": 700,
        "starter_id": 111, "starter_role_id": 11, "notify_role_id": 22,
        "voice_channel_id": 0,
        "expires_at": time.time() + mins * 60,
    }


def _due(guild=123, message=456):
    saved = toke_sessions._read()
    session = saved[f"{guild}:{message}"]
    session["created_at"] = time.time() - 1800
    session["expires_at"] = time.time() + 8 * 60
    toke_sessions._write(saved)


def test_opt_in_is_only_on_explicit_request_and_can_cancel(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        s = await toke_sessions.get(123, 456)
        assert s["reminder_ids"] == s["reminder_attempted_ids"] == []
        enrolled, status = await toke_sessions.toggle_reminder(123, 456, 101)
        assert "enabled" in status and enrolled["reminder_ids"] == [101]
        cancelled, status = await toke_sessions.toggle_reminder(123, 456, 101)
        assert "cancelled" in status and cancelled["reminder_ids"] == []
        _due()
        assert await toke_sessions.claim_due_reminder() is None
        other, status = await toke_sessions.toggle_reminder(222, 456, 101)
        assert other is None and "ended" in status
    asyncio.run(run())


def test_reminder_limits_and_atomic_one_time_claim_across_restarts(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        for uid in range(1, 21):
            s, status = await toke_sessions.toggle_reminder(123, 456, uid)
            assert "enabled" in status
        full, status = await toke_sessions.toggle_reminder(123, 456, 21)
        assert "limit" in status and len(full["reminder_ids"]) == 20
        _due()
        first, second = await asyncio.gather(
            toke_sessions.claim_due_reminder(),
            toke_sessions.claim_due_reminder(),
        )
        assert first[1] != second[1]
        saved = await toke_sessions.get(123, 456)
        assert set(saved["reminder_attempted_ids"]) == {first[1], second[1]}
        assert len(saved["reminder_ids"]) == 18
        # Repeated taps and restarts cannot re-enqueue an already claimed user.
        _, status = await toke_sessions.toggle_reminder(123, 456, first[1])
        assert "already processed" in status
        for _ in range(18):
            assert await toke_sessions.claim_due_reminder() is not None
        assert await toke_sessions.claim_due_reminder() is None
        saved = await toke_sessions.get(123, 456)
        assert len(saved["reminder_attempted_ids"]) == 20
        assert saved["reminder_ids"] == []
    asyncio.run(run())


def test_reminders_do_not_fire_early_late_or_after_end(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.toggle_reminder(123, 456, 1001)
        assert await toke_sessions.claim_due_reminder() is None
        _due()
        saved = toke_sessions._read()
        saved["123:456"]["created_at"] = time.time() - 10
        toke_sessions._write(saved)
        assert await toke_sessions.claim_due_reminder() is None
        saved["123:456"]["created_at"] = time.time() - 1800
        saved["123:456"]["expires_at"] = time.time() + 60
        toke_sessions._write(saved)
        assert await toke_sessions.claim_due_reminder() is None
        saved["123:456"]["expires_at"] = time.time() + 8 * 60
        toke_sessions._write(saved)
        _, status = await toke_sessions.react(123, 456, 111, "end")
        assert not status
        assert await toke_sessions.claim_due_reminder() is None
    asyncio.run(run())


def test_legacy_session_without_reminder_fields_is_safe(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        stored = toke_sessions._read()
        del stored["123:456"]["reminder_ids"]
        del stored["123:456"]["reminder_attempted_ids"]
        toke_sessions._write(stored)
        assert await toke_sessions.claim_due_reminder() is None
        latest, status = await toke_sessions.toggle_reminder(123, 456, 101)
        assert "enabled" in status and latest["reminder_ids"] == [101]
    asyncio.run(run())


def test_reminder_button_requires_role_and_returns_ephemeral_consent(isolated_store, monkeypatch):
    class FakeMember:
        id = 101
        roles = [SimpleNamespace(id=22)]
    monkeypatch.setattr(live.discord, "Member", FakeMember)
    async def run():
        await toke_sessions.save_new(_session())
        calls = []
        async def defer(**kw):
            calls.append(("defer", kw))
        async def edited(**kw):
            calls.append(("edit", kw))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123),
            message=SimpleNamespace(id=456),
            user=FakeMember(),
            response=SimpleNamespace(defer=defer),
            edit_original_response=edited,
        )
        view = live.TokeCheersView(111, 11, guild_id=123, message_id=456)
        await view.remind.callback(interaction)
        assert calls[0] == ("defer", {"ephemeral": True, "thinking": True})
        assert "enabled" in calls[-1][1]["content"]
        assert (await toke_sessions.get(123, 456))["reminder_ids"] == [101]
        FakeMember.roles = []
        calls.clear()
        await view.remind.callback(interaction)
        assert "role is required" in calls[-1][1]["content"]
        assert (await toke_sessions.get(123, 456))["reminder_ids"] == [101]
    asyncio.run(run())


def test_deliver_one_private_dm_no_role_ping_and_no_duplicate(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.toggle_reminder(123, 456, 101)
        _due()
        dms = []
        async def send(content, **kwargs):
            dms.append((content, kwargs))
        member = SimpleNamespace(
            id=101, roles=[SimpleNamespace(id=22)], bot=False, send=send,
        )
        guild = SimpleNamespace(id=123, get_member=lambda uid: member if uid == 101 else None)
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda gid: guild if gid == 123 else None)
        await live._deliver_one_toke_reminder(bot)
        await live._deliver_one_toke_reminder(bot)
        assert len(dms) == 1
        assert "discord.com/channels/123/700/456" in dms[0][0]
        assert "one-time" in dms[0][0]
        assert dms[0][1]["allowed_mentions"] == discord.AllowedMentions.none()
        assert (await toke_sessions.get(123, 456))["reminder_attempted_ids"] == [101]
    asyncio.run(run())


def test_revoked_role_or_missing_guild_never_sends_even_when_due(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.toggle_reminder(123, 456, 101)
        _due()
        sends = []
        async def send(*_a, **_kw):
            sends.append("sent")
        member = SimpleNamespace(id=101, roles=[], bot=False, send=send)
        guild = SimpleNamespace(get_member=lambda _uid: member)
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _gid: guild)
        await live._deliver_one_toke_reminder(bot)
        assert sends == []
        assert (await toke_sessions.get(123, 456))["reminder_attempted_ids"] == [101]
        assert await toke_sessions.claim_due_reminder() is None
    asyncio.run(run())


def test_reminder_is_not_sent_when_member_leaves_during_fetch(isolated_store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.toggle_reminder(123, 456, 101)
        _due()
        dms = []
        async def send(*_args, **_kw):
            dms.append(True)
        async def fetch_member(uid):
            assert uid == 101
            await toke_sessions.react(123, 456, 111, "end")
            return SimpleNamespace(
                id=uid, bot=False, roles=[SimpleNamespace(id=22)], send=send,
            )
        guild = SimpleNamespace(get_member=lambda _uid: None, fetch_member=fetch_member)
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _gid: guild)
        await live._deliver_one_toke_reminder(bot)
        assert dms == []
    asyncio.run(run())


def test_rendered_media_maintains_five_buttons_per_row():
    session = {**_session(), "active": True, "cheers_ids": [], "going_ids": [],
               "media_filename": "sesh.gif", "media_layout": "gallery_v2"}
    guild = SimpleNamespace(get_channel=lambda _cid: None)
    view = live.TokeMediaLayout(guild, session, intro="## 💨 Toke Time")
    rows = [node for node in view.walk_children() if isinstance(node, discord.ui.ActionRow)]
    assert len(rows) == 2
    assert [len(row.children) for row in rows] == [4, 5]
