from __future__ import annotations

"""Host handoff must survive restarts, reject stale panels, and keep one authority."""

import asyncio
import time
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import toke_sessions
from stoney_verify import toke_live_ui as live


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(toke_sessions, "SESSIONS_FILE", tmp_path / "toke.json")
    return tmp_path


def _session(*, guild_id=123, message_id=456, expires_at=None):
    return {
        "guild_id": guild_id, "message_id": message_id, "channel_id": 789,
        "starter_id": 111, "starter_role_id": 15, "notify_role_id": 16,
        "voice_channel_id": 0, "show_member_names": True,
        "expires_at": expires_at if expires_at is not None else time.time() + 3600,
    }


def test_transfer_preserves_session_and_is_restart_durable(store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.react(123, 456, 200, "going")
        before = await toke_sessions.get(123, 456)
        current, err = await toke_sessions.transfer_host(
            123, 456, expected_starter_id=111, new_starter_id=222,
        )
        assert not err
        assert current["starter_id"] == 222
        assert current["original_starter_id"] == 111
        assert current["host_transfer_count"] == 1
        assert current["expires_at"] == before["expires_at"]
        assert current["created_at"] == before["created_at"]
        assert current["going_ids"] == [200]
        assert (await toke_sessions.get(123, 456)) == current
        next_host, err = await toke_sessions.transfer_host(
            123, 456, expected_starter_id=222, new_starter_id=333,
        )
        assert not err and next_host["original_starter_id"] == 111
        assert next_host["host_transfer_count"] == 2
    asyncio.run(run())


def test_transfer_stale_confirmation_fails_without_overwrite(store):
    async def run():
        await toke_sessions.save_new(_session())
        first, second = await asyncio.gather(
            toke_sessions.transfer_host(123, 456, expected_starter_id=111, new_starter_id=222),
            toke_sessions.transfer_host(123, 456, expected_starter_id=111, new_starter_id=333),
        )
        errors = [item[1] for item in (first, second)]
        assert sum(not error for error in errors) == 1
        assert any("already changed" in error for error in errors)
        winner = (await toke_sessions.get(123, 456))["starter_id"]
        assert winner in (222, 333)
        assert (await toke_sessions.get(123, 456))["host_transfer_count"] == 1
    asyncio.run(run())


def test_transfer_rejected_for_expired_and_other_guild(store):
    async def run():
        await toke_sessions.save_new(_session())
        missing, reason = await toke_sessions.transfer_host(
            777, 456, expected_starter_id=111, new_starter_id=222,
        )
        assert missing is None and "ended" in reason
        assert (await toke_sessions.get(123, 456))["starter_id"] == 111
        await toke_sessions.save_new(_session(message_id=555, expires_at=time.time() - 1))
        expired, reason = await toke_sessions.transfer_host(
            123, 555, expected_starter_id=111, new_starter_id=222,
        )
        assert expired is None and "expired" in reason
        unchanged, reason = await toke_sessions.transfer_host(
            123, 456, expected_starter_id=111, new_starter_id=111,
        )
        assert "already hosting" in reason and unchanged["starter_id"] == 111
    asyncio.run(run())


def test_current_host_shown_without_changing_original_author(store):
    async def run():
        await toke_sessions.save_new(_session())
        session, err = await toke_sessions.transfer_host(
            123, 456, expected_starter_id=111, new_starter_id=222,
        )
        assert not err
        guild = SimpleNamespace(get_channel=lambda _cid: None)
        embed = discord.Embed(description="Original host is calling the sesh crowd.")
        card = live.render_card(guild, session, embed)
        assert "started this sesh" in card.description
        assert "Current host: <@222>" in card.fields[-1].value
        hidden = live.render_card(guild, {**session, "show_member_names": False}, embed)
        assert "<@222>" not in hidden.fields[-1].value
        assert "Host was reassigned" in hidden.fields[-1].value
        gallery = live.TokeMediaLayout(
            guild, {**session, "media_layout": "gallery_v2", "media_filename": "sesh.gif"},
            intro="## 💨 Toke Time\n<@111> is calling the sesh crowd.",
        )
        content = [node.content for node in gallery.walk_children()
                   if isinstance(node, discord.ui.TextDisplay)]
        assert "started this sesh." in content[0]
        assert "Current host: <@222>" in content[-1]
    asyncio.run(run())


def test_private_manager_transfer_has_confirmation_and_no_public_ping():
    manager = live.TokeManageView(actor_id=111, guild_id=123, message_id=456)
    labels = {getattr(node, "label", "") for node in manager.children}
    assert {"Transfer Host", "+15 minutes", "+30 minutes", "Close"} <= labels
    picker = live.TokeHostPickerView(actor_id=111, guild_id=123, message_id=456)
    assert len(picker.children) == 1
    assert isinstance(picker.children[0], discord.ui.UserSelect)
    confirm = live.TokeHostConfirmView(
        111, 123, 456, expected_starter_id=111, new_starter_id=222,
    )
    assert {getattr(node, "label", "") for node in confirm.children} == {"Confirm Transfer", "Cancel"}


def test_confirm_rechecks_member_role_and_existing_host(store, monkeypatch):
    async def run():
        await toke_sessions.save_new(_session())
        edits = []
        async def defer(**kwargs):
            edits.append(("defer", kwargs))
        async def edited(**kwargs):
            edits.append(("edit", kwargs))
        roles = [SimpleNamespace(id=999)]
        member = SimpleNamespace(id=222, bot=False, roles=roles)
        guild = SimpleNamespace(id=123, get_member=lambda uid: member if uid == 222 else None)
        owner = SimpleNamespace(id=111, guild_permissions=SimpleNamespace(manage_guild=False))
        click = SimpleNamespace(
            guild=guild, user=owner, response=SimpleNamespace(defer=defer),
            edit_original_response=edited, client=object(),
        )
        view = live.TokeHostConfirmView(111, 123, 456, expected_starter_id=111, new_starter_id=222)
        await view.confirm.callback(click)
        assert "no longer has" in edits[-1][1]["content"]
        assert (await toke_sessions.get(123, 456))["starter_id"] == 111
        roles[:] = [SimpleNamespace(id=15)]
        async def refresh(*_args):
            return True
        monkeypatch.setattr(live, "refresh_card", refresh)
        edits.clear()
        await view.confirm.callback(click)
        assert "new host can now" in edits[-1][1]["content"]
        assert (await toke_sessions.get(123, 456))["starter_id"] == 222
        edits.clear()
        await view.confirm.callback(click)
        assert "no longer manage" in edits[-1][1]["content"]
    asyncio.run(run())


def test_handoff_authorizes_new_host_for_end_confirmation(store):
    async def run():
        await toke_sessions.save_new(_session())
        await toke_sessions.transfer_host(
            123, 456, expected_starter_id=111, new_starter_id=222,
        )
        answers = []
        async def defer(**kwargs):
            answers.append(("defer", kwargs))
        async def edited(**kwargs):
            answers.append(("edit", kwargs))
        view = live.TokeCheersView(111, 15, guild_id=123, message_id=456)
        click = SimpleNamespace(
            guild=SimpleNamespace(id=123), message=SimpleNamespace(id=456),
            user=SimpleNamespace(id=222, guild_permissions=SimpleNamespace(manage_guild=False)),
            response=SimpleNamespace(defer=defer), edit_original_response=edited,
        )
        await view.end.callback(click)
        assert isinstance(answers[-1][1]["view"], live.TokeEndConfirmView)
        click.user.id = 111
        answers.clear()
        await view.end.callback(click)
        assert "Only the current host" in answers[-1][1]["content"]
        assert "view" not in answers[-1][1]
    asyncio.run(run())


def test_selected_member_cache_miss_uses_one_bounded_lookup(monkeypatch):
    async def run():
        calls = []
        candidate = SimpleNamespace(id=222, bot=False, roles=[SimpleNamespace(id=15)])
        async def fetch_member(uid):
            calls.append(uid)
            return candidate
        guild = SimpleNamespace(get_member=lambda _uid: None, fetch_member=fetch_member)
        resolved = await live._resolve_toke_handoff_target(guild, 222)
        assert resolved is candidate
        assert calls == [222]

        class Missing(discord.NotFound):
            pass

        async def forbidden(_uid):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No permission")
        guild.fetch_member = forbidden
        assert await live._resolve_toke_handoff_target(guild, 222) is None
    asyncio.run(run())
