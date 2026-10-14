from __future__ import annotations

"""Indexed /toke VC dispatch and bounded no-op Discord edit suppression."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from stoney_verify import toke_live_ui as live
from stoney_verify import toke_sessions


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke.json")
    live._VOICE_PENDING.clear()
    live._VOICE_DEPARTED.clear()
    live._VOICE_WORKERS.clear()
    live._VOICE_RENDERED_MEMBERS.clear()
    yield tmp_path
    live._VOICE_PENDING.clear()
    live._VOICE_DEPARTED.clear()
    live._VOICE_WORKERS.clear()
    live._VOICE_RENDERED_MEMBERS.clear()


def _session(guild_id: int, message_id: int, voice_id: int, *, expiry: float | None = None):
    return {
        "guild_id": guild_id,
        "message_id": message_id,
        "channel_id": 8000,
        "starter_id": 11,
        "starter_role_id": 22,
        "notify_role_id": 33,
        "voice_channel_id": voice_id,
        "expires_at": time.time() + 3600 if expiry is None else expiry,
    }


def test_index_routes_only_matching_guild_and_voice_and_keeps_snapshots_isolated(isolated):
    async def run():
        await toke_sessions.save_new(_session(100, 1, 500))
        await toke_sessions.save_new(_session(100, 2, 501))
        await toke_sessions.save_new(_session(100, 3, 500))
        await toke_sessions.save_new(_session(200, 4, 500))
        await toke_sessions.save_new(_session(100, 5, 0))
        matched = await toke_sessions.active_in_voices(100, {500})
        assert {item["message_id"] for item in matched} == {1, 3}
        matched[0]["cheers_ids"].append(999)
        assert all(999 not in item["cheers_ids"] for item in await toke_sessions.active_in_voices(100, {500}))
        both = await toke_sessions.active_in_voices(100, {500, 501})
        assert {item["message_id"] for item in both} == {1, 2, 3}
        assert await toke_sessions.active_in_voices(200, {501}) == []
        assert await toke_sessions.active_in_voices(100, set()) == []
    asyncio.run(run())


def test_hot_voice_lookup_skips_disk_json_reads(isolated, monkeypatch):
    async def run():
        await toke_sessions.save_new(_session(101, 10, 999))
        calls = 0
        original = toke_sessions._read
        def fail_read():
            nonlocal calls
            calls += 1
            raise AssertionError("hot voice dispatch should reuse the validated derived index")
        monkeypatch.setattr(toke_sessions, "_read", fail_read)
        for _ in range(30):
            matches = await toke_sessions.active_in_voices(101, {999})
            assert len(matches) == 1 and matches[0]["message_id"] == 10
        assert calls == 0
        monkeypatch.setattr(toke_sessions, "_read", original)
    asyncio.run(run())


def test_index_invalidated_on_end_expiry_and_external_atomic_replacement(isolated):
    async def run():
        await toke_sessions.save_new(_session(101, 10, 999))
        assert len(await toke_sessions.active_in_voices(101, {999})) == 1
        await toke_sessions.react(101, 10, 11, "end")
        assert await toke_sessions.active_in_voices(101, {999}) == []
        await toke_sessions.save_new(_session(101, 20, 999, expiry=time.time() + 2))
        assert len(await toke_sessions.active_in_voices(101, {999})) == 1
        content = toke_sessions._read()
        content["101:20"]["expires_at"] = time.time() - 30
        # Model a different process atomically replacing the canonical file.
        foreign = toke_sessions.SESSIONS_FILE.with_suffix(".incoming")
        foreign.write_text(json.dumps(content), encoding="utf-8")
        foreign.replace(toke_sessions.SESSIONS_FILE)
        assert await toke_sessions.active_in_voices(101, {999}) == []
    asyncio.run(run())


def test_corrupted_external_replacement_fails_closed_not_stale(isolated):
    async def run():
        await toke_sessions.save_new(_session(101, 10, 999))
        assert len(await toke_sessions.active_in_voices(101, {999})) == 1
        incoming = toke_sessions.SESSIONS_FILE.with_suffix(".incoming")
        incoming.write_text("{invalid", encoding="utf-8")
        incoming.replace(toke_sessions.SESSIONS_FILE)
        with pytest.raises(ValueError):
            await toke_sessions.active_in_voices(101, {999})
        # A failed read never repairs or truncates the corrupt user file.
        assert toke_sessions.SESSIONS_FILE.read_text(encoding="utf-8") == "{invalid"
    asyncio.run(run())


def test_voice_events_skip_unchanged_cards_but_never_skip_rapid_departures(isolated, monkeypatch):
    async def run():
        await toke_sessions.save_new(_session(101, 10, 999))
        members = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        voice = SimpleNamespace(id=999, members=members)
        guild = SimpleNamespace(id=101, get_channel=lambda cid: voice if cid == 999 else None)
        calls = []
        async def refresh(_bot, state, *, departed_ids=None):
            calls.append((state["message_id"], departed_ids))
            return True
        monkeypatch.setattr(live, "refresh_card", refresh)
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0)

        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert calls == [(10, None)]

        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert len(calls) == 1

        members.append(SimpleNamespace(id=3))
        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert len(calls) == 2

        # Even when the final member signature is unchanged, a real rapid
        # departure must be reconciled for RSVP and historical attendance.
        live._VOICE_PENDING[101] = {999}
        live._VOICE_DEPARTED[101] = {999: {2}}
        await live._flush_toke_voice_updates(guild, object())
        assert calls[-1] == (10, {2})
        assert len(calls) == 3
    asyncio.run(run())


def test_voice_dispatch_ignores_other_rooms_and_never_updates_ended_sesh(isolated, monkeypatch):
    async def run():
        await toke_sessions.save_new(_session(101, 10, 999))
        await toke_sessions.save_new(_session(101, 20, 123))
        await toke_sessions.save_new(_session(102, 30, 999))
        voice = SimpleNamespace(id=999, members=[SimpleNamespace(id=1)])
        guild = SimpleNamespace(id=101, get_channel=lambda cid: voice if cid == 999 else None)
        updated = []
        async def refresh(_bot, session, *, departed_ids=None):
            updated.append(session["message_id"])
            return True
        monkeypatch.setattr(live, "refresh_card", refresh)
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0)
        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert updated == [10]
        await toke_sessions.react(101, 10, 11, "end")
        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert updated == [10]
        assert await toke_sessions.active_in_voices(101, {999}) == []
    asyncio.run(run())


def test_noop_fingerprints_have_fixed_memory_cap(isolated, monkeypatch):
    async def run():
        monkeypatch.setattr(live, "_VOICE_RENDER_CACHE_CAP", 2)
        for mid in (1, 2, 3):
            await toke_sessions.save_new(_session(101, mid, 999))
        voice = SimpleNamespace(id=999, members=[SimpleNamespace(id=1)])
        guild = SimpleNamespace(id=101, get_channel=lambda _cid: voice)
        async def refreshed(_bot, _s, *, departed_ids=None):
            return True
        monkeypatch.setattr(live, "refresh_card", refreshed)
        monkeypatch.setattr(live, "_VOICE_DEBOUNCE_SECONDS", 0)
        live._VOICE_PENDING[101] = {999}
        await live._flush_toke_voice_updates(guild, object())
        assert len(live._VOICE_RENDERED_MEMBERS) == 2
    asyncio.run(run())
