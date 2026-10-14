from __future__ import annotations

"""Opt-in /toke voice lifecycle, never speculative closures of missing VCs."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke.json")
    return tmp_path


def session(*, guild_id=101, message_id=202, grace=0):
    return {
        "guild_id": guild_id, "message_id": message_id, "channel_id": 303,
        "starter_id": 11, "starter_role_id": 31, "notify_role_id": 32,
        "voice_channel_id": 404, "expires_at": time.time() + 3600,
        "empty_vc_end_minutes": grace,
    }


def age_empty_session(guild_id=101, message_id=202, *, minutes=6):
    data = toke_sessions._read()
    entry = data[f"{guild_id}:{message_id}"]
    assert entry["empty_voice_since"] > 0
    entry["empty_voice_since"] = time.time() - minutes * 60
    toke_sessions._write(data)


def test_host_departure_does_not_end_when_guests_remain(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        present = await toke_sessions.reconcile_voice(101, 202, {11, 22})
        assert present["host_seen_in_vc"] and present["host_away_since"] == 0
        away = await toke_sessions.reconcile_voice(101, 202, {22}, departed_ids={11})
        assert away["active"] and away["host_away_since"] > 0
        assert away["empty_voice_since"] == 0
        assert await toke_sessions.idle_voice_candidates() == []
        returned = await toke_sessions.reconcile_voice(101, 202, {11, 22})
        assert returned["host_away_since"] == 0
        assert returned["active"] and returned["host_seen_in_vc"]
    asyncio.run(run())


def test_never_present_host_is_not_assumed_away(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        state = await toke_sessions.reconcile_voice(101, 202, set())
        assert state["host_seen_in_vc"] is False
        assert state["host_away_since"] == 0
        assert state["empty_voice_since"] == 0
        assert await toke_sessions.idle_voice_candidates() == []
    asyncio.run(run())


def test_only_opted_in_sessions_close_after_occupied_vc_empties(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        await toke_sessions.save_new(session(guild_id=102, grace=0))
        for gid in (101, 102):
            await toke_sessions.reconcile_voice(gid, 202, {11})
            gone = await toke_sessions.reconcile_voice(gid, 202, set(), departed_ids={11})
            assert gone["host_away_since"] > 0 and gone["empty_voice_since"] > 0
            age_empty_session(gid)
        due = await toke_sessions.idle_voice_candidates()
        assert [x["guild_id"] for x in due] == [101]
        assert await toke_sessions.close_idle_voice(101, 202, confirmed_empty=False) is None
        assert (await toke_sessions.get(101, 202))["active"]
        ended = await toke_sessions.close_idle_voice(101, 202, confirmed_empty=True)
        assert not ended["active"] and ended["close_pending"]
        assert ended["close_reason"] == "voice_idle"
        assert await toke_sessions.close_idle_voice(101, 202, confirmed_empty=True) is None
        assert (await toke_sessions.get(102, 202))["active"]
        assert await toke_sessions.idle_voice_candidates() == []
    asyncio.run(run())


def test_rapid_host_leave_starts_grace_but_rejoin_resets_it(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        # A leave event inside a two-second debounce still proves VC use.
        left = await toke_sessions.reconcile_voice(101, 202, set(), departed_ids={11})
        assert left["voice_had_humans"] is True
        assert left["host_away_since"] and left["empty_voice_since"]
        age_empty_session()
        assert len(await toke_sessions.idle_voice_candidates()) == 1
        joined = await toke_sessions.reconcile_voice(101, 202, {11}, departed_ids={11})
        assert joined["host_away_since"] == 0 and joined["empty_voice_since"] == 0
        assert await toke_sessions.idle_voice_candidates() == []
    asyncio.run(run())


def test_durable_empty_timer_and_handoff_reset(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        await toke_sessions.reconcile_voice(101, 202, {11})
        await toke_sessions.reconcile_voice(101, 202, set())
        saved = await toke_sessions.get(101, 202)
        assert saved["empty_voice_since"] and saved["host_away_since"]
        transferred, err = await toke_sessions.transfer_host(
            101, 202, expected_starter_id=11, new_starter_id=22,
        )
        assert not err
        assert transferred["host_away_since"] == 0
        assert transferred["host_seen_in_vc"] is False
        assert transferred["empty_voice_since"] == 0
        # If the newly appointed host has not joined yet, the next observed
        # empty room starts a new full grace period.
        recalc = await toke_sessions.reconcile_voice(101, 202, set())
        assert recalc["empty_voice_since"] > saved["empty_voice_since"]
    asyncio.run(run())


def test_render_host_away_and_disabled_timeout_without_leaking_names(storage, monkeypatch):
    class FakeVoice:
        id = 404
        mention = "<#404>"
        members = [SimpleNamespace(id=22)]
    monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
    guild = SimpleNamespace(get_channel=lambda cid: FakeVoice() if cid == 404 else None)

    async def run():
        await toke_sessions.save_new(session(grace=0))
        await toke_sessions.reconcile_voice(101, 202, {11, 22})
        away = await toke_sessions.reconcile_voice(101, 202, {22})
        card = live.render_card(guild, {**away, "show_member_names": False}, discord.Embed())
        status = "\n".join(f.value for f in card.fields)
        assert "Host away" in status and "This sesh stays active" in status
        assert "auto-close" not in status
        assert "<@11>" not in status

        await toke_sessions.reconcile_voice(101, 202, set())
        away = await toke_sessions.get(101, 202)
        # VC is now empty. The grace message is only shown when opted in.
        FakeVoice.members = []
        card = live.render_card(guild, away, discord.Embed())
        assert "auto-close" not in "\n".join(f.value for f in card.fields)
    asyncio.run(run())


def test_unavailable_voice_does_not_end_or_reconcile_unobserved_absence(storage):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        await toke_sessions.reconcile_voice(101, 202, {11})
        await toke_sessions.reconcile_voice(101, 202, set())
        age_empty_session()
        assert await toke_sessions.idle_voice_candidates()
        guild = SimpleNamespace(get_channel=lambda _id: None)
        current = await live.reconcile_live_attendance(guild, await toke_sessions.get(101, 202))
        assert current["active"] and current["empty_voice_since"] > 0
    asyncio.run(run())


def test_session_timeout_policy_validation_and_owner_controls():
    assert live.settings_from_config({})["empty_vc_end_minutes"] == 0
    cfg = {live.SETTINGS_KEY: {"empty_vc_end_minutes": "7"}}
    assert live.settings_from_config(cfg)["empty_vc_end_minutes"] == 7
    cfg = {live.SETTINGS_KEY: {"empty_vc_end_minutes": 999}}
    assert live.settings_from_config(cfg)["empty_vc_end_minutes"] == 120
    cfg = {live.SETTINGS_KEY: {"empty_vc_end_minutes": "not a number"}}
    assert live.settings_from_config(cfg)["empty_vc_end_minutes"] == 0
    panel = live.TokeSettingsView(44, live.settings_from_config({}))
    labels = {getattr(btn, "label", None) for btn in panel.children}
    assert "Empty VC Policy" in labels
    modal = live.TokeIdlePolicyModal(44, live.settings_from_config({}))
    assert str(modal.idle.default) == "0"


def test_due_idle_worker_skips_missing_voice_and_closes_valid_empty_vc(storage, monkeypatch):
    async def run():
        await toke_sessions.save_new(session(grace=5))
        await toke_sessions.reconcile_voice(101, 202, {11})
        await toke_sessions.reconcile_voice(101, 202, set())
        age_empty_session()
        class FakeVoice:
            id = 404
            members = []
        monkeypatch.setattr(live.discord, "VoiceChannel", FakeVoice)
        fake_voice = FakeVoice()
        guild = SimpleNamespace(id=101, get_channel=lambda cid: fake_voice if cid == 404 else None)
        calls = []
        async def refresh(_bot, state):
            calls.append(state["close_reason"])
            return True
        monkeypatch.setattr(live, "refresh_card", refresh)
        class Bot:
            closed = False
            def is_closed(self):
                return self.closed
            def get_guild(self, gid):
                return guild if gid == 101 else None
        bot = Bot()
        async def sleep(_seconds):
            bot.closed = True
        monkeypatch.setattr(live.asyncio, "sleep", sleep)
        await live._expire_and_refresh(bot)
        closed = await toke_sessions.get(101, 202)
        assert closed["close_reason"] == "voice_idle"
        assert calls and calls[0] == "voice_idle"
    asyncio.run(run())
