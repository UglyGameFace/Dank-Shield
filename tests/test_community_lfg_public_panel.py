"""Public LFG panel: persistent interaction, safe summaries and single-message lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import discord
import pytest

from stoney_verify import community_lfg_public_panel as panel
from stoney_verify.commands_ext import community_lfg_public_panel_ui as staff
from stoney_verify.commands_ext import public_community_hub as ui


GID = 111111111111111111
CID = 222222222222222222
MID = 333333333333333333
BOT = 444444444444444444


def _session(**kw):
    now = datetime.now(timezone.utc)
    return {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "game_name": "Minecraft", "privacy": "public", "state": "open",
        "capacity": 8,
        "created_at": (now - timedelta(minutes=5)).isoformat(),
        "last_activity_at": now.isoformat(),
        "notes": "PRIVATE_NOTES_NEVER_TO_PUBLISH",
        "host_id": "PRIVATE_HOST_ID_NEVER_TO_PUBLISH",
        "thread_id": "PRIVATE_DISCUSSION_ID",
        **kw,
    }


def _cfg(*, channel_id=CID, message_id=MID):
    return {panel.CONFIG_KEY: {
        "state": "active", "channel_id": str(channel_id),
        "message_id": str(message_id),
    }}


class FakeChannel:
    id = CID
    mention = "<#222222222222222222>"
    def __init__(self, message=None):
        self.message = message
        self.sent = []
        self.fetched = []
    def permissions_for(self, member):
        return SimpleNamespace(
            view_channel=True, send_messages=True,
            embed_links=True, read_message_history=True,
        )
    async def fetch_message(self, mid):
        self.fetched.append(mid)
        return self.message
    async def send(self, **kwargs):
        msg = SimpleNamespace(
            id=MID+1, author=SimpleNamespace(id=BOT),
            embeds=[kwargs["embed"]],
            edit=AsyncMock(), delete=AsyncMock(),
        )
        self.sent.append(kwargs)
        return msg


def _guild(channel):
    return SimpleNamespace(
        id=GID, name="The 420 Lobby",
        me=SimpleNamespace(id=BOT),
        get_channel=lambda cid: channel if cid == CID else None,
    )


def test_board_only_shows_public_fresh_joinable_groups():
    now = datetime.now(timezone.utc)
    rows = [
        _session(game_name="@everyone Minecraft"),
        _session(game_name="Secret Game", privacy="private"),
        _session(game_name="Closed Game", state="ended"),
        _session(game_name="Old Game",
                 last_activity_at=(now-timedelta(hours=3)).isoformat()),
        _session(game_name="Expired Game",
                 created_at=(now-timedelta(days=2)).isoformat()),
    ]
    embed = panel.board_embed("Lobby", rows, {
        "enabled": True, "maintenance_mode": False,
        "idle_timeout_seconds": 1800,
        "max_session_lifetime_seconds": 43200,
    })
    content = repr(embed.to_dict())
    assert "＠everyone" in content
    assert "Minecraft" in content
    for term in ("PRIVATE_NOTES", "PRIVATE_HOST", "PRIVATE_DISCUSSION",
                 "Secret Game", "Closed Game", "Old Game", "Expired Game"):
        assert term not in content
    assert "Find Players" in content


def test_public_panel_exposes_no_personal_presence_or_unsaved_post_links():
    embed = panel.board_embed("Gaming", [], {"enabled": True})
    assert "No public groups yet" in repr(embed.to_dict())
    outage = panel.board_embed("Gaming", [_session()], {}, unavailable=True)
    assert "Temporarily unavailable" in repr(outage.to_dict())
    assert "Minecraft" not in repr(outage.to_dict())


def test_six_public_buttons_have_stable_ids_and_no_timeout():
    view = panel.PublicLFGBoardView()
    assert view.timeout is None
    keys = {c.custom_id for c in view.children}
    assert keys == {
        "dank:lfg:board:find:v1", "dank:lfg:board:create:v1",
        "dank:lfg:board:mine:v1", "dank:lfg:board:outside:v1",
        "dank:lfg:board:network:v1", "dank:lfg:board:refresh:v1",
    }
    assert all(c.custom_id and getattr(c, "row", 0) <= 4 for c in view.children)
    assert len(view.children) <= 25


def test_old_copied_or_disabled_public_panel_is_rejected(monkeypatch):
    monkeypatch.setattr(panel, "get_guild_config", AsyncMock(return_value=_cfg()))
    callback = SimpleNamespace(
        guild=SimpleNamespace(id=GID), channel_id=CID, user=SimpleNamespace(id=9, bot=False),
        message=SimpleNamespace(id=MID+1),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    allowed = asyncio.run(panel.PublicLFGBoardView().interaction_check(callback))
    assert not allowed
    callback.response.send_message.assert_awaited_once()
    callback.message.id = MID
    allowed = asyncio.run(panel.PublicLFGBoardView().interaction_check(callback))
    assert allowed


def test_public_panel_refreshes_in_place_not_by_duplicate_send(monkeypatch):
    original_embed = panel.board_embed("The 420 Lobby", [_session()], {"enabled": True})
    msg = SimpleNamespace(
        author=SimpleNamespace(id=BOT),
        id=MID, embeds=[original_embed],
        edit=AsyncMock(), delete=AsyncMock(),
    )
    chan = FakeChannel(msg)
    guild = _guild(chan)
    monkeypatch.setattr(panel.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(panel, "get_guild_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(panel, "_current_snapshot", AsyncMock(return_value=original_embed))
    monkeypatch.setattr(panel, "_RENDERED", {})
    assert asyncio.run(panel.refresh_one(guild)) == "unchanged"
    msg.edit.assert_not_awaited()
    monkeypatch.setattr(panel, "_current_snapshot", AsyncMock(return_value=panel.board_embed(
        "The 420 Lobby", [_session(game_name="Valorant")], {"enabled": True})))
    assert asyncio.run(panel.refresh_one(guild)) == "updated"
    msg.edit.assert_awaited_once()
    assert len(chan.sent) == 0


def test_staff_post_reserves_config_then_publishes_exactly_one_message(monkeypatch):
    chan = FakeChannel()
    guild = _guild(chan)
    monkeypatch.setattr(panel.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(panel, "get_guild_config", AsyncMock(
        return_value={"source": "guild_config"}))
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(panel, "compare_and_swap_guild_config_key", cas)
    monkeypatch.setattr(panel, "_current_snapshot",
                        AsyncMock(return_value=panel.board_embed("Lobby", [], {"enabled": True})))
    response = asyncio.run(panel.install_public_board(guild, chan))
    assert "posted" in response.lower()
    assert len(chan.sent) == 1
    assert cas.await_count == 2
    first, second = cas.await_args_list
    assert first.kwargs["value"]["state"] == "installing"
    assert second.kwargs["expected"] == first.kwargs["value"]
    assert second.kwargs["value"]["state"] == "active"
    assert second.kwargs["value"]["message_id"] == str(MID+1)
    assert chan.sent[0]["allowed_mentions"].everyone is False


def test_staff_post_rolls_back_reservation_if_send_fails(monkeypatch):
    chan = FakeChannel()
    chan.send = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Missing access"),
        {"code": 50013, "message": "Missing Permissions"},
    ))
    monkeypatch.setattr(panel.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(panel, "get_guild_config", AsyncMock(return_value={}))
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(panel, "compare_and_swap_guild_config_key", cas)
    monkeypatch.setattr(panel, "_current_snapshot",
                        AsyncMock(return_value=panel.board_embed("Lobby", [], {"enabled": True})))
    with pytest.raises(discord.Forbidden):
        asyncio.run(panel.install_public_board(_guild(chan), chan))
    assert cas.await_count == 2
    assert cas.await_args.kwargs["value"] is None


def test_remove_authorizes_config_first_and_deletes_only_bot_message(monkeypatch):
    msg = SimpleNamespace(
        id=MID, author=SimpleNamespace(id=BOT), delete=AsyncMock(),
    )
    chan = FakeChannel(msg)
    monkeypatch.setattr(panel.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(panel, "get_guild_config", AsyncMock(return_value=_cfg()))
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(panel, "compare_and_swap_guild_config_key", cas)
    assert asyncio.run(panel.remove_public_board(_guild(chan)))
    msg.delete.assert_awaited_once()
    assert cas.await_args.kwargs["value"] is None


def test_setup_reuses_search_safe_picker_and_staff_permissions():
    source = Path(staff.__file__).read_text()
    assert "DankGuildResourceBrowserView" in source
    assert '"Use This Channel"' in source
    assert 'resource_kinds=("text",)' in source
    assert "_staff_authorized" in source
    assert len(staff.PublicPanelSetupView(9, GID).children) <= 25


def test_worker_wired_in_existing_startup_with_event_driven_invalidations():
    root = Path(__file__).resolve().parents[1]
    app = (root / "stoney_verify/app.py").read_text()
    service = (root / "stoney_verify/community_hub_service.py").read_text()
    surface = (root / "stoney_verify/commands_ext/public_community_hub.py").read_text()
    assert "start_public_board_worker" in app
    assert 'custom_id="dank:hub:staff:publicpanel:v1"' in surface
    assert "mark_dirty(gid)" in service
    assert "bot.add_view(PublicLFGBoardView())" in Path(panel.__file__).read_text()
    assert "MAX_GUILDS_PER_TICK = 4" in Path(panel.__file__).read_text()
