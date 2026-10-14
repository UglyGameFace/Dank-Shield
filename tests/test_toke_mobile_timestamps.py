from __future__ import annotations

"""Prevent stale mobile relative clocks and dropped voice-presence updates."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_live_ui as live


def _session(active: bool = True):
    return {
        "guild_id": 123, "message_id": 456, "channel_id": 789,
        "starter_id": 42, "starter_role_id": 9, "notify_role_id": 10,
        "expires_at": time.time() + (1800 if active else -120),
        "closed_at": time.time() - 120 if not active else None,
        "close_reason": "expired",
        "voice_channel_id": 0,
        "show_member_names": False,
        "cheers_ids": [42, 43], "going_ids": [43],
        "active": active,
    }


def test_active_card_uses_absolute_local_end_time_not_frozen_relative_counter():
    data = _session()
    card = live.render_card(
        SimpleNamespace(get_channel=lambda _cid: None), data,
        discord.Embed(title="Toke Time", description="RJ is calling the sesh crowd."),
    )
    session_value = card.fields[-1].value
    assert f"<t:{int(data['expires_at'])}:f>" in session_value
    assert "Scheduled end:" in session_value
    assert "Counts last checked: <t:" in session_value
    assert "Tap **Refresh**" in session_value
    assert ":R>" not in str(card.to_dict())
    assert "Ends in" not in session_value


def test_closed_card_shows_fixed_end_clock_not_seconds_ago():
    data = _session(active=False)
    card = live.render_card(
        SimpleNamespace(get_channel=lambda _cid: None), data,
        discord.Embed(title="Toke Time", description="RJ is calling the sesh crowd."),
    )
    description = card.fields[0].value
    assert f"<t:{int(data['closed_at'])}:f>" in description
    assert "Closed at" in description
    assert ":R>" not in str(card.to_dict())
    assert "seconds ago" not in description
    assert "Sesh Ended" in card.title


def test_toke_voice_events_are_coalesced_without_losing_last_join(monkeypatch):
    async def run():
        live._VOICE_PENDING.clear()
        live._VOICE_WORKERS.clear()
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0.001)
        calls = []
        stored = [
            {"guild_id": 123, "message_id": 456, "voice_channel_id": 987},
            {"guild_id": 123, "message_id": 457, "voice_channel_id": 444},
        ]
        async def all_active(guild_id):
            assert guild_id == 123
            return stored
        async def refresh(_bot, session, interaction=None):
            calls.append(session["message_id"])
            return True

        monkeypatch.setattr(live.toke_sessions, "all_active", all_active)
        monkeypatch.setattr(live, "refresh_card", refresh)
        guild = SimpleNamespace(id=123)
        member = SimpleNamespace(guild=guild)
        voice = SimpleNamespace(id=987)
        before, after = SimpleNamespace(channel=None), SimpleNamespace(channel=voice)
        for _ in range(6):
            await live._on_toke_voice_state(member, before, after, bot=object())
        worker = live._VOICE_WORKERS[123]
        await worker
        assert calls == [456]  # One API edit for all six rapid joins.
        assert not live._VOICE_PENDING
        assert not live._VOICE_WORKERS
    asyncio.run(run())


def test_toke_voice_events_during_edit_trigger_trailing_update(monkeypatch):
    async def run():
        live._VOICE_PENDING.clear()
        live._VOICE_WORKERS.clear()
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0.001)
        gate = asyncio.Event()
        first_seen = asyncio.Event()
        recorded = []
        async def all_active(_guild_id):
            return [{"guild_id": 123, "message_id": 456, "voice_channel_id": 987}]
        async def refresh(_bot, session, interaction=None):
            recorded.append(session["message_id"])
            if len(recorded) == 1:
                first_seen.set()
                await gate.wait()
            return True

        monkeypatch.setattr(live.toke_sessions, "all_active", all_active)
        monkeypatch.setattr(live, "refresh_card", refresh)
        member = SimpleNamespace(guild=SimpleNamespace(id=123))
        channel = SimpleNamespace(id=987)
        before, after = SimpleNamespace(channel=None), SimpleNamespace(channel=channel)
        await live._on_toke_voice_state(member, before, after, bot=object())
        worker = live._VOICE_WORKERS[123]
        await asyncio.wait_for(first_seen.wait(), timeout=1.0)
        await live._on_toke_voice_state(member, before, after, bot=object())
        gate.set()
        await asyncio.wait_for(worker, timeout=1.0)
        assert recorded == [456, 456]  # The last join is never dropped.
        assert live._VOICE_PENDING == {}
    asyncio.run(run())


def test_toke_voice_updates_are_isolated_by_guild(monkeypatch):
    async def run():
        live._VOICE_PENDING.clear()
        live._VOICE_WORKERS.clear()
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0.001)
        groups = []
        async def all_active(guild_id):
            return [{"guild_id": guild_id, "message_id": guild_id + 1, "voice_channel_id": 987}]
        async def refresh(_bot, session, interaction=None):
            groups.append(session["guild_id"])
            return True

        monkeypatch.setattr(live.toke_sessions, "all_active", all_active)
        monkeypatch.setattr(live, "refresh_card", refresh)
        voice = SimpleNamespace(id=987)
        for gid in (123, 234):
            await live._on_toke_voice_state(
                SimpleNamespace(guild=SimpleNamespace(id=gid)),
                SimpleNamespace(channel=None), SimpleNamespace(channel=voice),
                bot=object(),
            )
        await asyncio.gather(*tuple(live._VOICE_WORKERS.values()))
        assert sorted(groups) == [123, 234]
    asyncio.run(run())


def test_refresh_returns_current_counts_in_private_reply_even_if_embed_is_cached(monkeypatch):
    class FakeVoice:
        id = 987
        mention = "<#987>"
        members = [SimpleNamespace(id=42), SimpleNamespace(id=43), SimpleNamespace(id=44)]

    monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
    session = {
        **_session(), "voice_channel_id": 987,
        "cheers_ids": [42, 43], "going_ids": [43],
    }
    events = []

    async def read(_gid, _mid):
        return dict(session)

    async def public_card_refresh(*_args, **_kwargs):
        events.append("public_edit")
        return True

    async def defer(**kwargs):
        events.append("deferred")
        assert kwargs.get("ephemeral") is True

    async def reply(**kwargs):
        events.append(("reply", kwargs))

    monkeypatch.setattr(live.toke_sessions, "get", read)
    monkeypatch.setattr(live, "refresh_card", public_card_refresh)

    voice = FakeVoice()
    guild = SimpleNamespace(id=123, get_channel=lambda cid: voice if cid == 987 else None)
    interaction = SimpleNamespace(
        guild=guild, message=SimpleNamespace(id=456), client=object(),
        response=SimpleNamespace(defer=defer),
        edit_original_response=reply,
    )
    view = live.TokeCheersView(42, 9, guild_id=123, message_id=456)
    asyncio.run(view.refresh.callback(interaction))  # discord.py View binds the button callback
    assert events[:2] == ["deferred", "public_edit"]
    body = events[2][1]["content"]
    assert "Cheers: **2**" in body
    assert "Joining: **1**" in body
    assert "VC occupants: **3**" in body
    assert "Sesh attendees: **2**" in body
    assert "<#987>" in body
    assert "Scheduled end: <t:" in body and ":f>" in body
    assert "status checked <t:" in body
    assert ":R>" not in body
    assert "\\n" not in body  # Real multiline text, not literal backslashes.
