from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke_sessions.json")
    monkeypatch.setattr(live, "_LOOP_TASK", None)
    live._PENDING.clear()
    live._REGISTERED.clear()
    live._STARTED_BOTS.clear()
    live._VOICE_REFRESH.clear()
    return tmp_path


def _session(guild=123, message=456, *, expires=None):
    return {
        "guild_id": guild, "message_id": message, "channel_id": 777,
        "starter_id": 111, "starter_role_id": 11, "notify_role_id": 22,
        "voice_channel_id": 0, "max_interested": 100,
        "show_member_names": True,
        "expires_at": expires or time.time() + 3600,
    }


def test_per_guild_participation_is_durable_and_idempotent(isolated_sessions):
    async def run():
        await toke_sessions.save_new(_session(123, 456))
        await toke_sessions.save_new(_session(789, 321))
        data, err = await toke_sessions.react(123, 456, 991, "cheers")
        assert not err
        assert data["cheers_ids"] == [991]
        data, err = await toke_sessions.react(123, 456, 991, "cheers")
        assert not err
        assert data["cheers_ids"] == [991]
        data, err = await toke_sessions.react(123, 456, 991, "going")
        assert not err
        assert data["going_ids"] == [991]
        other = await toke_sessions.get(789, 321)
        assert other["cheers_ids"] == []
        data, err = await toke_sessions.react(123, 456, 991, "withdraw")
        assert not err
        assert data["cheers_ids"] == data["going_ids"] == []
        data, err = await toke_sessions.react(123, 456, 991, "end")
        assert not err and not data["active"]
        data, err = await toke_sessions.react(123, 456, 991, "cheers")
        assert data is None and "ended" in err
        assert (await toke_sessions.get(789, 321))["active"]
    asyncio.run(run())


def test_session_expiry_removes_open_actions(isolated_sessions):
    async def run():
        await toke_sessions.save_new(_session(123, 456, expires=time.time() - 1))
        s = await toke_sessions.get(123, 456)
        assert not s["active"]
        assert await toke_sessions.all_active(123) == []
        result, reason = await toke_sessions.react(123, 456, 77, "cheers")
        assert result is None and "expired" in reason
    asyncio.run(run())


def test_store_corruption_fails_closed_and_does_not_overwrite(isolated_sessions):
    path = toke_sessions.SESSIONS_FILE
    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError):
        asyncio.run(toke_sessions.save_new(_session()))
    assert path.read_text(encoding="utf-8") == "{bad json"


def test_settings_default_and_per_guild_ranges():
    defaults = live.settings_from_config({})
    assert defaults["session_minutes"] == 60
    assert defaults["show_member_names"] is True
    cfg = {live.SETTINGS_KEY: {
        "session_minutes": 9, "user_cooldown_minutes": 45,
        "guild_cooldown_minutes": 8, "max_interested": 500,
        "show_member_names": False, "voice_channel_id": "777",
    }}
    setting = live.settings_from_config(cfg)
    assert setting["session_minutes"] == 15
    assert setting["user_cooldown_minutes"] == 45
    assert setting["guild_cooldown_minutes"] == 8
    assert setting["max_interested"] == 250
    assert setting["show_member_names"] is False
    assert setting["voice_channel_id"] == 777


def test_concurrent_guests_can_only_record_once_and_capacity(isolated_sessions):
    async def run():
        await toke_sessions.save_new({**_session(), "max_interested": 1})
        async def act(uid):
            return await toke_sessions.react(123, 456, uid, "cheers", max_interested=1)
        a, b = await asyncio.gather(act(1), act(2))
        stored = await toke_sessions.get(123, 456)
        assert len(stored["cheers_ids"]) == 1
        assert sum(bool(err) for _value, err in (a, b)) == 1
    asyncio.run(run())


def test_gif_capture_is_only_active_for_pending_author_and_channel(isolated_sessions):
    live.begin_capture(123, 456, 789, 999, "join us")
    pending = live.pending_for(123, 456)
    assert pending is not None and pending.destination_id == 999
    assert live.pending_for(123, 777) is None
    message = SimpleNamespace(
        content="https://tenor.com/view/fun-gif-1234", attachments=[], embeds=[]
    )
    assert live._has_gif_media(message)
    assert live._has_gif_media(SimpleNamespace(
        content="", attachments=[SimpleNamespace(filename="dance.GIF", content_type="image/gif")], embeds=[]
    ))
    assert not live._has_gif_media(SimpleNamespace(content="hello", attachments=[], embeds=[]))
    pending.deadline = time.monotonic() - 2
    assert live.pending_for(123, 456) is None


def test_cheers_view_is_persistent_and_has_separate_participation_controls(isolated_sessions):
    v = live.TokeCheersView(starter_id=11, stoner_role_id=22, guild_id=123, voice_channel_id=456)
    assert v.timeout is None
    labels = {getattr(item, "label", "") for item in v.children}
    assert {"Cheers", "I'm Joining", "Withdraw", "Who's Coming?", "Refresh", "End Sesh", "Join VC"} <= labels
    interactive = [x for x in v.children if isinstance(x, discord.ui.Button) and x.style != discord.ButtonStyle.link]
    assert all(x.custom_id for x in interactive)
    assert any(getattr(x, "url", "").endswith("/123/456") for x in v.children if isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link)


def test_render_distinguishes_cheers_joining_voice_and_hidden_names(isolated_sessions, monkeypatch):
    voice = SimpleNamespace(id=999, mention="<#999>", members=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    # Make the voice channel match the discord.py type for this isolated unit test.
    monkeypatch.setattr(live.discord, "VoiceChannel", type(voice))
    members = {
        1: SimpleNamespace(display_name="RJ"),
        2: SimpleNamespace(display_name="Alex"),
        3: SimpleNamespace(display_name="Taylor"),
    }
    guild = SimpleNamespace(
        get_channel=lambda i: voice if i == 999 else None,
        get_member=lambda i: members.get(i),
    )
    session = {
        **_session(), "voice_channel_id": 999, "cheers_ids": [1,3],
        "going_ids": [3], "active": True,
    }
    card = live.render_card(guild, session, discord.Embed(title="💨 Toke Time"))
    assert any("Cheers • 2" in f.name for f in card.fields)
    assert any("voice now • 2" in f.name for f in card.fields)
    assert any("Taylor" in f.value for f in card.fields)
    assert not any("Alex" in f.value for f in card.fields if f.name.startswith("💨 Cheers"))
    session["show_member_names"] = False
    hidden = live.render_card(guild, session, discord.Embed(title="💨 Toke Time"))
    assert not any("Taylor" in f.value for f in hidden.fields)


def test_manager_and_command_are_wired_to_single_toke_runtime():
    root = Path(__file__).resolve().parents[1]
    script = (root / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    pub = (root / "stoney_verify/commands_ext/public_toke.py").read_text(encoding="utf-8")
    manager = (root / "stoney_verify/commands_ext/public_community_pings.py").read_text(encoding="utf-8")
    live_source = (root / "stoney_verify/toke_live_ui.py").read_text(encoding="utf-8")
    assert "register_toke_runtime(bot)" in script
    assert 'label="Toke Settings"' in manager
    assert "publish_toke_from_draft" in pub
    assert "begin_capture" in pub
    assert "await toke_sessions.save_new(" in pub
    assert "async def _on_toke_media_message(" in live_source
    assert 'bot.add_listener(_on_toke_media_message, "on_message")' in live_source
    assert 'bot.add_listener(voice_update, "on_voice_state_update")' in live_source
    assert 'bot.add_listener(on_ready, "on_ready")' in live_source
    assert "await message.delete()" in live_source
    assert 'await sent.delete()' in pub


def test_toke_participant_names_and_messages_not_persisted(isolated_sessions):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(123, 456, 789, "cheers")
    asyncio.run(run())
    saved = toke_sessions.SESSIONS_FILE.read_text(encoding="utf-8")
    assert '"guild_id":123' in saved
    assert '"cheers_ids":[789]' in saved
    assert '"message_text"' not in saved
    assert '"media_url"' not in saved
