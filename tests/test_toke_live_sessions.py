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
    live._VOICE_PENDING.clear()
    live._VOICE_WORKERS.clear()
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


def test_per_guild_settings_modal_and_mobile_controls_render():
    saved = live.settings_from_config({
        live.SETTINGS_KEY: {
            "session_minutes": 90, "user_cooldown_minutes": 20,
            "guild_cooldown_minutes": 8, "max_interested": 45,
            "show_member_names": False, "voice_channel_id": 987654321,
        },
    })
    modal = live.TokeSettingsModal(owner_id=123, saved=saved)
    assert len(modal.children) == 5
    assert str(modal.lifetime.default) == "90"
    assert str(modal.user_cooldown.default) == "20"
    assert str(modal.names.default) == "no"
    panel = live.TokeSettingsView(123, saved)
    labels = {getattr(child, "label", "") for child in panel.children}
    assert {"Edit Settings", "Choose Default VC", "Clear Default VC", "Back to Community & Pings"} <= labels
    assert len(panel.children) <= 5


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
    assert {"Cheers", "I'm Joining", "Withdraw", "Who's Coming?", "Refresh", "Remind Me", "Manage", "End Sesh", "Join VC"} <= labels
    interactive = [x for x in v.children if isinstance(x, discord.ui.Button) and x.style != discord.ButtonStyle.link]
    assert all(x.custom_id for x in interactive)
    assert any(getattr(x, "url", "").endswith("/123/456") for x in v.children if isinstance(x, discord.ui.Button) and x.style == discord.ButtonStyle.link)


def test_render_distinguishes_cheers_joining_voice_and_hidden_names(isolated_sessions, monkeypatch):
    voice = SimpleNamespace(id=999, mention="<#999>", members=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    monkeypatch.setattr(live.discord, "VoiceChannel", type(voice))
    # Long styled usernames must not get duplicated as raw text plus mention.
    members = {
        1: SimpleNamespace(display_name="🎃👻💀LongStyledHost💀👻🎃"),
        2: SimpleNamespace(display_name="Alex"),
        3: SimpleNamespace(display_name="Taylor"),
    }
    guild = SimpleNamespace(
        get_channel=lambda i: voice if i == 999 else None,
        get_member=lambda i: members.get(i),
    )
    session = {
        **_session(), "starter_id": 1, "voice_channel_id": 999,
        "cheers_ids": [1, 3], "going_ids": [1, 3], "active": True,
    }
    original = discord.Embed(
        title="💨 Toke Time",
        description="**🎃👻💀LongStyledHost💀👻🎃** is calling the sesh crowd.",
    )
    card = live.render_card(guild, session, original)
    assert "Cheers: 2" in card.fields[0].value
    assert "Joining: 2" in card.fields[0].value
    assert "VC occupants: 2" in card.fields[0].value
    assert "Sesh attendees: 1" in card.fields[0].value
    assert "<#999>" in card.fields[0].value
    crowd = card.fields[1].value
    assert crowd.count("**Host**") == 1
    assert "👑💨🙋🔊" in crowd
    assert crowd.count("<@3>") == 1
    assert "<@2>" not in crowd  # A VC occupant without RSVP is not a sesh participant.
    assert "<@1>" not in crowd  # Host is already identified in the description.
    assert "LongStyledHost" not in crowd
    assert card.description.count("LongStyledHost") == 1
    assert "👥 People" in card.fields[1].name

    session["show_member_names"] = False
    hidden = live.render_card(guild, session, original)
    assert hidden.fields[1].value == "Participant names are private in this server."
    assert "<@3>" not in "\n".join(str(f.value) for f in hidden.fields)
    assert "<#999>" in hidden.fields[0].value


def test_toke_roster_deduplicates_all_attendance_sources(isolated_sessions, monkeypatch):
    voice = SimpleNamespace(
        id=998, mention="<#998>",
        members=[SimpleNamespace(id=3), SimpleNamespace(id=2), SimpleNamespace(id=1)],
    )
    monkeypatch.setattr(live.discord, "VoiceChannel", type(voice))
    guild = SimpleNamespace(get_channel=lambda n: voice if n == 998 else None)
    session = {
        **_session(), "starter_id": 1, "voice_channel_id": 998,
        "cheers_ids": [1, 2, 3], "going_ids": [1, 2, 3, 4],
        "show_member_names": True,
    }
    roster = live.TokeRosterView(guild, session)
    assert len(roster.lines) == 4
    text = "\n".join(roster.lines)
    for member_id in (1, 2, 3, 4):
        assert text.count(f"<@{member_id}>") == 1
    assert text.count("👑") == 1
    assert "🙋" in text and "🔊" in text
    assert roster.pages == 1


def test_toke_roster_pagination_shows_every_member_once(isolated_sessions, monkeypatch):
    voice = SimpleNamespace(id=987, mention="<#987>", members=[])
    monkeypatch.setattr(live.discord, "VoiceChannel", type(voice))
    guild = SimpleNamespace(get_channel=lambda _n: voice)
    session = {
        **_session(), "starter_id": 1, "voice_channel_id": 987,
        "cheers_ids": list(range(2, 41)), "going_ids": list(range(35, 51)),
        "active": True,
    }
    # 1 host + 39 cheers + 10 new joiners = 50 unique people.
    view = live.TokeRosterView(guild, session)
    assert len(view.lines) == 50
    assert view.pages == 4
    names = "\n".join(view.lines)
    for member_id in range(1, 51):
        assert names.count(f"<@{member_id}>") == 1
    crowd = live.render_card(guild, session, discord.Embed(description="A host is calling the sesh crowd."))
    assert "+47 more" in crowd.fields[1].value
    assert len(crowd.fields[1].value) <= 1024
    assert len(crowd.fields[1].value.splitlines()) == 4


def test_toke_single_member_without_cheers_still_shows_host(isolated_sessions):
    guild = SimpleNamespace(get_channel=lambda _n: None)
    session = {**_session(), "starter_id": 1, "cheers_ids": [], "going_ids": [], "active": True}
    card = live.render_card(guild, session, discord.Embed(description="Host is calling."))
    assert card.fields[1].value == "👑  **Host**"
    assert "Cheers: 0" in card.fields[0].value
    assert "No VC selected" in card.fields[0].value


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


def test_embed_only_native_gif_formats_are_detected(isolated_sessions):
    gif_embed = SimpleNamespace(
        url=None,
        image=SimpleNamespace(url="https://media.tenor.com/123/reaction.gif"),
        video=None, thumbnail=None,
    )
    message = SimpleNamespace(content="", attachments=[], embeds=[gif_embed])
    assert live._has_gif_media(message)
    assert live.media_url_from_message(message).endswith("/reaction.gif")
    keyboard = SimpleNamespace(
        content="https://tenor.com/view/dance-gif-1234",
        attachments=[], embeds=[],
    )
    assert live.media_url_from_message(keyboard) == keyboard.content
    unrelated = SimpleNamespace(
        content="https://youtube.com/watch?v=abc", attachments=[], embeds=[],
    )
    assert live.media_url_from_message(unrelated) == ""
    assert not live._has_gif_media(unrelated)


def test_pending_composer_accepts_generic_public_image_share_pages_only_as_one_link(isolated_sessions):
    gif_page = SimpleNamespace(
        content="https://new-gif-site.example/posts/abc123", embeds=[], attachments=[],
    )
    assert live.media_url_from_message(gif_page) == ""
    assert live.media_url_from_message(gif_page, allow_public_page=True) == gif_page.content
    assert live._has_gif_media(gif_page, allow_public_page=True)
    # A regular chat message, an unsafe link, and an arbitrary sentence are not media.
    for content in ("come smoke with us", "https://localhost/internal",
                    "check out https://new-gif-site.example/posts/abc123"):
        source = SimpleNamespace(content=content, embeds=[], attachments=[])
        assert not live._has_gif_media(source, allow_public_page=True)
    # A Discord-supplied public image still takes precedence over the view-page link.
    gif_page.embeds = [SimpleNamespace(
        image=SimpleNamespace(url="https://i.imgur.com/dance.gif"), video=None,
        thumbnail=None, url=gif_page.content,
    )]
    assert live.media_url_from_message(gif_page, allow_public_page=True) == "https://i.imgur.com/dance.gif"


def test_composer_gif_message_is_consumed_only_after_card_is_posted(isolated_sessions, monkeypatch):
    from stoney_verify.commands_ext import public_toke
    events = []

    async def publish(guild, author, pending, captured):
        assert pending.channel_id == captured.channel.id
        events.append("published")
        return SimpleNamespace(jump_url="https://discord.com/channels/123/999/888")

    monkeypatch.setattr(public_toke, "publish_toke_from_draft", publish)

    async def on_delete():
        events.append("deleted")

    async def on_dm(_text):
        events.append("dm")

    author = SimpleNamespace(id=444, bot=False, send=on_dm)
    source = SimpleNamespace(id=555)
    guild = SimpleNamespace(id=123)
    live.begin_capture(guild.id, author.id, source.id, 777, "where are you")
    msg = SimpleNamespace(
        guild=guild, author=author, channel=source, webhook_id=None,
        content="https://tenor.com/view/dancing-gif-123", attachments=[],
        embeds=[], delete=on_delete,
    )
    asyncio.run(live._on_toke_media_message(msg))
    assert events == ["published", "deleted", "dm"]
    assert live.pending_for(guild.id, author.id) is None


def test_failed_gif_publication_preserves_original_message(isolated_sessions, monkeypatch):
    from stoney_verify.commands_ext import public_toke
    events = []

    async def reject(*_args):
        raise RuntimeError("target unavailable")

    monkeypatch.setattr(public_toke, "publish_toke_from_draft", reject)

    async def on_delete():
        events.append("deleted")

    async def on_dm(_text):
        events.append("dm")

    author = SimpleNamespace(id=444, bot=False, send=on_dm)
    source = SimpleNamespace(id=555)
    guild = SimpleNamespace(id=123)
    live.begin_capture(guild.id, author.id, source.id, 777, "")
    msg = SimpleNamespace(
        guild=guild, author=author, channel=source, webhook_id=None,
        content="", attachments=[SimpleNamespace(filename="sesh.gif", content_type="image/gif")],
        embeds=[], delete=on_delete,
    )
    asyncio.run(live._on_toke_media_message(msg))
    assert events == ["dm"]
    assert live.pending_for(guild.id, author.id) is None


def test_regular_text_is_not_intercepted_during_gif_capture(isolated_sessions, monkeypatch):
    from stoney_verify.commands_ext import public_toke
    async def forbidden(*_args):
        raise AssertionError("regular chat was incorrectly consumed")
    monkeypatch.setattr(public_toke, "publish_toke_from_draft", forbidden)
    author = SimpleNamespace(id=444, bot=False)
    source = SimpleNamespace(id=555)
    guild = SimpleNamespace(id=123)
    live.begin_capture(guild.id, author.id, source.id, 777, "")
    msg = SimpleNamespace(
        guild=guild, author=author, channel=source, webhook_id=None,
        content="hello everyone", attachments=[], embeds=[],
    )
    asyncio.run(live._on_toke_media_message(msg))
    assert live.pending_for(guild.id, author.id) is not None


def test_persisted_cooldown_uses_successful_launch_history(isolated_sessions):
    from stoney_verify.commands_ext import public_toke

    async def check():
        await toke_sessions.save_new(_session(123, 456))
        guild_time, user_time = await toke_sessions.recent_launch_times(123, 111)
        assert guild_time > 0 and user_time > 0
        assert (await toke_sessions.recent_launch_times(123, 222))[1] == 0
        settings = live.settings_from_config({})
        delay, reason = await public_toke._persistent_cooldown_remaining(123, 111, settings)
        assert delay > 14 * 60 and "your" in reason
        delay2, reason2 = await public_toke._persistent_cooldown_remaining(123, 222, settings)
        assert delay2 > 4 * 60 and "server" in reason2
    asyncio.run(check())


def test_rsvp_capacity_counts_unique_members_across_both_buttons(isolated_sessions):
    async def run():
        await toke_sessions.save_new({**_session(), "max_interested": 2})
        going, error = await toke_sessions.react(123, 456, 10, "going", max_interested=2)
        assert not error and going["going_ids"] == [10] and going["cheers_ids"] == []
        cheers, error = await toke_sessions.react(123, 456, 10, "cheers", max_interested=2)
        assert not error and cheers["cheers_ids"] == [10]
        second, error = await toke_sessions.react(123, 456, 11, "going", max_interested=2)
        assert not error and second["going_ids"] == [10, 11]
        blocked, error = await toke_sessions.react(123, 456, 12, "cheers", max_interested=2)
        assert "limit" in error and blocked["cheers_ids"] == [10]
        blocked, error = await toke_sessions.react(123, 456, 12, "going", max_interested=2)
        assert "limit" in error and blocked["going_ids"] == [10, 11]
        after, error = await toke_sessions.react(123, 456, 11, "cheers", max_interested=2)
        assert not error and after["cheers_ids"] == [10, 11]
    asyncio.run(run())


def test_host_extension_is_bounded_and_survives_reads(isolated_sessions):
    async def run():
        await toke_sessions.save_new(_session())
        prior = await toke_sessions.get(123, 456)
        initial = prior["expires_at"]
        increased, error = await toke_sessions.extend(123, 456, minutes=15)
        assert not error and increased["expires_at"] == initial + 900
        assert (await toke_sessions.get(123, 456))["expires_at"] == increased["expires_at"]
        for _ in range(20):
            changed, error = await toke_sessions.extend(123, 456, minutes=30)
            if error:
                break
        assert "maximum lifetime" in error
        assert changed["expires_at"] <= changed["created_at"] + 240 * 60
        await toke_sessions.react(123, 456, 111, "end")
        closed, error = await toke_sessions.extend(123, 456, minutes=15)
        assert closed is None and "ended" in error
    asyncio.run(run())


def test_toke_draft_retains_session_specific_voice_override(isolated_sessions):
    live.begin_capture(123, 456, 789, 999, "meeting up", voice_channel_id=556677)
    pending = live.pending_for(123, 456)
    assert pending is not None and pending.voice_channel_id == 556677
    panel = live.TokeManageView(456, 123, 999)
    labels = {getattr(button, "label", "") for button in panel.children}
    assert {"+15 minutes", "+30 minutes", "Close"} <= labels


def test_toke_extension_rechecks_host_authority_at_click_time(monkeypatch):
    async def run():
        events = []
        async def read(_guild_id, _message_id):
            return {**_session(), "active": True}
        async def should_not_extend(*args, **kwargs):
            raise AssertionError("unauthorized extension must not reach the store")
        async def defer(**kwargs):
            events.append("deferred")
        async def edit_original_response(**kwargs):
            events.append(kwargs.get("content", ""))
        monkeypatch.setattr(toke_sessions, "get", read)
        monkeypatch.setattr(toke_sessions, "extend", should_not_extend)
        view = live.TokeManageView(actor_id=222, guild_id=123, message_id=456)
        member = SimpleNamespace(id=222, guild_permissions=SimpleNamespace(manage_guild=False))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123), user=member,
            response=SimpleNamespace(defer=defer),
            edit_original_response=edit_original_response,
        )
        await view._extend(interaction, 15)
        assert events[0] == "deferred"
        assert "no longer manage" in events[1]
    asyncio.run(run())


def test_toke_runtime_registers_global_persistent_button_fallback(isolated_sessions):
    """Old live cards need a callback even without a message-specific ViewStore entry."""
    class Bot:
        def __init__(self):
            self.views = []
            self.listeners = []
        def add_view(self, view, message_id=None):
            self.views.append((view, message_id))
        def add_listener(self, callback, event):
            self.listeners.append((callback, event))

    bot = Bot()
    live.register_toke_runtime(bot)
    live.register_toke_runtime(bot)
    assert len(bot.views) == 1
    view, message_id = bot.views[0]
    assert message_id is None
    assert isinstance(view, live.TokeCheersView)
    assert view.timeout is None
    assert {item.custom_id for item in view.children if getattr(item, "custom_id", None)} >= {
        "dank:toke:v2:cheers", "dank:toke:v2:going",
        "dank:toke:v2:withdraw", "dank:toke:v2:refresh",
    }
    assert [event for _callback, event in bot.listeners] == [
        "on_message", "on_voice_state_update", "on_ready",
    ]


def test_global_toke_fallback_acknowledges_before_store_and_checks_session(isolated_sessions, monkeypatch):
    class Member:
        id = 789
        roles = [SimpleNamespace(id=22)]
    monkeypatch.setattr(live.discord, "Member", Member)
    recorded = []

    async def fake_get(guild_id, message_id):
        recorded.append(("get", guild_id, message_id))
        if message_id != 456:
            return None
        return {
            **_session(), "active": True, "cheers_ids": [],
            "going_ids": [], "reminder_ids": [],
        }

    async def fake_react(guild_id, message_id, user_id, action, **kwargs):
        recorded.append(("react", guild_id, message_id, user_id, action))
        return {**_session(), "active": True}, ""

    async def fake_refresh(*args, **kwargs):
        recorded.append(("refresh",))
        return True

    monkeypatch.setattr(toke_sessions, "get", fake_get)
    monkeypatch.setattr(toke_sessions, "react", fake_react)
    monkeypatch.setattr(live, "refresh_card", fake_refresh)

    async def scenario(message_id):
        async def defer(**kwargs):
            recorded.append(("defer", kwargs))
        async def edit(**kwargs):
            recorded.append(("edit", kwargs.get("content")))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123), user=Member(),
            message=SimpleNamespace(id=message_id), client=object(),
            response=SimpleNamespace(defer=defer), edit_original_response=edit,
        )
        fallback = live.TokeCheersView(starter_id=0, stoner_role_id=0)
        await fallback.cheers.callback(interaction)

    asyncio.run(scenario(456))
    assert recorded[0] == ("defer", {"ephemeral": True, "thinking": True})
    assert ("react", 123, 456, 789, "cheers") in recorded
    assert recorded[-1] == ("edit", "💨 Cheers recorded.")
    recorded.clear()
    asyncio.run(scenario(999))
    assert recorded[0] == ("defer", {"ephemeral": True, "thinking": True})
    assert recorded[1] == ("get", 123, 999)
    assert not any(row[0] == "react" for row in recorded)
    assert "no longer available" in recorded[-1][1]


@pytest.mark.parametrize("success", [True, False])
def test_toke_media_capture_does_not_dm_creator_on_success_but_reports_failure(
    isolated_sessions, monkeypatch, success,
):
    """The invoker already sees the card; failures must still be explained."""
    from stoney_verify.commands_ext import public_toke

    created = []
    sent_dms = []
    deleted = []
    async def send_dm(content):
        sent_dms.append(content)
    async def delete_source():
        deleted.append(True)
    author = SimpleNamespace(id=222, bot=False, send=send_dm)
    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(id=777)
    message = SimpleNamespace(
        guild=guild, channel=channel, author=author,
        webhook_id=None, delete=delete_source,
    )
    monkeypatch.setattr(live, "_has_gif_media", lambda *_args, **_kwargs: True)

    async def publish(guild_arg, author_arg, pending_arg, message_arg):
        created.append((guild_arg, author_arg, pending_arg, message_arg))
        if not success:
            raise OSError("media unavailable")
        return SimpleNamespace(jump_url="https://discord.com/channels/123/777/456")

    monkeypatch.setattr(public_toke, "publish_toke_from_draft", publish)
    live.begin_capture(123, 222, 777, 777, "join us")
    asyncio.run(live._on_toke_media_message(message))

    assert len(created) == 1
    assert created[0][0] is guild and created[0][1] is author
    assert created[0][3] is message
    assert live.pending_for(123, 222) is None
    if success:
        assert deleted == [True]
        assert sent_dms == []
    else:
        assert deleted == []
        assert len(sent_dms) == 1
        assert "Toke could not post your media" in sent_dms[0]
        assert "Your original GIF was kept" in sent_dms[0]


def test_toke_capture_success_with_source_delete_denied_does_not_dm(
    isolated_sessions, monkeypatch,
):
    """No success DM even if a posted card's temporary GIF can't be removed."""
    from stoney_verify.commands_ext import public_toke

    sent_dms = []
    async def send_dm(content):
        sent_dms.append(content)
    async def delete_source():
        raise discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Delete denied",
        )
    async def publish(*_args):
        return SimpleNamespace(jump_url="https://discord.com/channels/123/777/456")
    author = SimpleNamespace(id=222, bot=False, send=send_dm)
    message = SimpleNamespace(
        guild=SimpleNamespace(id=123), channel=SimpleNamespace(id=777),
        author=author, webhook_id=None, delete=delete_source,
    )
    monkeypatch.setattr(live, "_has_gif_media", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(public_toke, "publish_toke_from_draft", publish)
    live.begin_capture(123, 222, 777, 777, "join us")
    asyncio.run(live._on_toke_media_message(message))
    assert sent_dms == []
    assert live.pending_for(123, 222) is None
