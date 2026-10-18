from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify import community_hub_game_role_pings as gp
from stoney_verify.commands_ext import public_community_hub as ui


def _config(*, enabled=True, kind="notification", role_id=9, caps=None):
    return {
        "community_pings_v2": {
            "revision": 1,
            "groups": [{"key": "alerts", "label": "Notifications"}],
            "options": [{
                "key": "game", "role_id": str(role_id), "label": "Minecraft Pings",
                "kind": kind, "group_key": "alerts", "enabled": enabled,
                "capabilities": list(caps or []),
            }],
        },
        gp.CONFIG_KEY: {
            "version": 1,
            "games": {
                "minecraft": {
                    "game_name": "Minecraft", "role_id": str(role_id),
                    "cooldown_minutes": 30, "last_at": 0, "last_session_id": "",
                }
            },
            "recent_calls": [],
        },
    }


class FakeRole:
    def __init__(self, guild, role_id=9, *, mentionable=True, managed=False):
        self.guild = guild
        self.id = role_id
        self.mention = f"<@&{role_id}>"
        self.mentionable = mentionable
        self.managed = managed

    def is_default(self):
        return False


class FakeMember:
    pass


class FakeChannel:
    def __init__(self, guild):
        self.guild = guild
        self.id = 99
        self.send = AsyncMock(return_value=SimpleNamespace(jump_url="https://discord.com/channels/7/99/100"))
        self.can_send = True
        self.can_role_view = True

    def permissions_for(self, who):
        return SimpleNamespace(
            view_channel=self.can_role_view if isinstance(who, FakeRole) else True,
            send_messages=self.can_send,
            embed_links=True, mention_everyone=False,
        )


def _discord_setup(monkeypatch):
    monkeypatch.setattr(gp.discord, "Role", FakeRole)
    monkeypatch.setattr(gp.discord, "Member", FakeMember)
    monkeypatch.setattr(gp.discord, "TextChannel", FakeChannel)
    guild = SimpleNamespace(id=7, me=FakeMember())
    role = FakeRole(guild)
    channel = FakeChannel(guild)
    guild.get_role = lambda rid: role if rid == 9 else None
    guild.get_channel = lambda cid: channel if cid == 99 else None
    return guild, channel


def _session():
    return {
        "id": "1234", "guild_id": "7", "game_name": "Minecraft",
        "state": "open", "privacy": "public",
        "panel_channel_id": "99", "panel_message_id": "123",
    }


def _wire_sender(monkeypatch, *, config=None, settings=None, session=None, reserved=True):
    configured = config if config is not None else _config()
    group = session if session is not None else _session()
    monkeypatch.setattr(gp, "get_guild_config", AsyncMock(return_value=configured))
    monkeypatch.setattr(gp.hub, "get_session", AsyncMock(return_value=group))
    monkeypatch.setattr(gp.hub, "get_settings", AsyncMock(return_value=(
        settings if settings is not None else {"enabled": True, "notifications_enabled": True}
    )))
    claimed = AsyncMock(return_value=(reserved, {}))
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", claimed)
    monkeypatch.setattr(gp.time, "time", lambda: 1760800000)
    return claimed


def test_explicit_v2_notification_roles_only_and_no_toke_cheers_reuse(monkeypatch):
    guild, _ = _discord_setup(monkeypatch)
    assert gp.eligible_game_roles(guild, _config()) == [(9, "Minecraft Pings")]
    assert not gp.eligible_game_roles(guild, _config(enabled=False))
    assert not gp.eligible_game_roles(guild, _config(kind="community"))
    assert not gp.eligible_game_roles(guild, _config(caps=[gp.CAP_TOKE_NOTIFY]))
    assert not gp.eligible_game_roles(guild, _config(caps=[gp.CAP_MOVIE_NIGHT_NOTIFY]))
    cheers = _config()
    cheers["cheers_lobby_v1"] = {"role_id": "9"}
    assert not gp.eligible_game_roles(guild, cheers)
    assert not gp.eligible_game_roles(guild, {})  # no legacy implied role


def test_staff_mapping_setup_is_cas_and_preserves_claim_history(monkeypatch):
    guild, _ = _discord_setup(monkeypatch)
    config = _config()
    config[gp.CONFIG_KEY]["games"]["minecraft"]["last_at"] = 1760790000
    config[gp.CONFIG_KEY]["games"]["minecraft"]["last_session_id"] = "old"
    read = AsyncMock(return_value=config)
    saved = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(gp, "get_guild_config", read)
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", saved)
    assert asyncio.run(gp.configure(guild, "Minecraft", 9, cooldown_minutes=45))
    kw = saved.await_args.kwargs
    assert kw["expected"] is config[gp.CONFIG_KEY]
    new = kw["value"]["games"]["minecraft"]
    assert new["cooldown_minutes"] == 45
    assert new["last_at"] == 1760790000
    assert new["last_session_id"] == "old"
    assert kw["source"] == "community_hub_game_role_setup"


def test_staff_config_requires_existing_enabled_notification_role(monkeypatch):
    guild, _ = _discord_setup(monkeypatch)
    monkeypatch.setattr(gp, "get_guild_config", AsyncMock(return_value=_config(enabled=False)))
    saved = AsyncMock()
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", saved)
    with pytest.raises(ValueError, match="enabled Notification"):
        asyncio.run(gp.configure(guild, "Minecraft", 9))
    with pytest.raises(ValueError, match="15 and 240"):
        asyncio.run(gp.configure(guild, "Minecraft", 9, cooldown_minutes=1))
    saved.assert_not_awaited()


def test_send_requires_valid_public_live_session_and_enabled_notifications(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    for blocked in (
        {"enabled": False}, {"maintenance_mode": True},
        {"notifications_enabled": False},
    ):
        _wire_sender(monkeypatch, settings=blocked)
        result = asyncio.run(gp.call_game_crew(guild, "1234")).lower()
        assert "disabled" in result or "unavailable" in result
    for session in (
        {**_session(), "privacy": "private"},
        {**_session(), "state": "ending"},
        {**_session(), "state": "paused"},
    ):
        _wire_sender(monkeypatch, session=session)
        assert "Only current public" in asyncio.run(gp.call_game_crew(guild, "1234"))
    channel.send.assert_not_awaited()


def test_atomically_claim_before_allowed_role_mention(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    call_order = []
    async def cas(*args, **kwargs):
        assert kwargs["value"]["games"]["minecraft"]["last_session_id"] == "1234"
        call_order.append("claimed")
        return (True, {})
    async def send(**kwargs):
        call_order.append("sent")
        assert kwargs["allowed_mentions"].everyone is False
        assert kwargs["allowed_mentions"].users is False
        assert len(kwargs["allowed_mentions"].roles) == 1
        assert kwargs["content"] == "<@&9>"
        return SimpleNamespace(jump_url="https://discord.com/channels/7/99/100")
    _wire_sender(monkeypatch)
    channel.send = send
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", cas)
    text = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "Called" in text
    assert call_order == ["claimed", "sent"]


def test_failed_cas_never_sends_role_ping(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    _wire_sender(monkeypatch, reserved=False)
    text = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "No ping" in text
    channel.send.assert_not_awaited()


def test_missing_or_revoked_notification_role_never_sends(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    _wire_sender(monkeypatch, config=_config(enabled=False))
    assert "no longer an enabled" in asyncio.run(gp.call_game_crew(guild, "1234"))
    _wire_sender(monkeypatch)
    channel.can_role_view = False
    assert "permissions" in asyncio.run(gp.call_game_crew(guild, "1234"))
    channel.send.assert_not_awaited()


def test_shared_hourly_budget_and_one_ping_per_session(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    cfg = _config()
    cfg[gp.CONFIG_KEY]["games"]["minecraft"]["last_session_id"] = "1234"
    _wire_sender(monkeypatch, config=cfg)
    assert "already called" in asyncio.run(gp.call_game_crew(guild, "1234"))
    cfg[gp.CONFIG_KEY]["games"]["minecraft"]["last_session_id"] = ""
    cfg[gp.CONFIG_KEY]["recent_calls"] = [1760799700, 1760799500, 1760799200, 1760799000]
    assert "4 per hour" in asyncio.run(gp.call_game_crew(guild, "1234"))
    channel.send.assert_not_awaited()


def test_staff_manager_and_session_controls_are_discoverable_and_bounded():
    staff = ui.StaffDashboardView(1)
    manage = ui.SessionControlView(1, session_id="session")
    ids = [getattr(child, "custom_id", "") for child in staff.children]
    assert "dank:hub:staff:gamepings:v1" in ids
    ids = [getattr(child, "custom_id", "") for child in manage.children]
    assert "dank:hub:manage:gameping:v1" in ids
    for view in (staff, manage, ui.GameRolePingsManagerView(1)):
        rows = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            rows[row] = rows.get(row, 0) + 1
        assert len(rows) <= 5
        assert max(rows.values()) <= 5


def test_role_picker_is_native_searchless_choice_with_allowed_configured_roles():
    view = ui.GameRolePickerView(1, "Minecraft", 30, [(9, "Minecraft Pings")])
    selector = next(child for child in view.children if isinstance(child, ui.GameRoleOptionSelect))
    assert selector.options[0].value == "9"
    assert len(selector.options) <= 25
    assert selector.custom_id == "dank:hub:gamepings:role:v1"


def test_post_claim_staff_disable_fails_closed_without_role_ping(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    claimed = _wire_sender(monkeypatch)
    settings_read = AsyncMock(side_effect=[
        {"enabled": True, "notifications_enabled": True},
        {"enabled": True, "notifications_enabled": False},
    ])
    monkeypatch.setattr(gp.hub, "get_settings", settings_read)
    result = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "No ping sent" in result
    claimed.assert_awaited_once()
    channel.send.assert_not_awaited()


def test_post_claim_stale_session_fails_closed(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    claimed = _wire_sender(monkeypatch)
    fetch = AsyncMock(side_effect=[
        _session(),
        {**_session(), "state": "ended"},
    ])
    monkeypatch.setattr(gp.hub, "get_session", fetch)
    result = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "No ping sent" in result
    claimed.assert_awaited_once()
    channel.send.assert_not_awaited()


def test_config_conflict_retries_but_storage_exception_fails_closed(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    _wire_sender(monkeypatch)
    cas = AsyncMock(side_effect=[(False, {}), (True, {})])
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", cas)
    assert "Called" in asyncio.run(gp.call_game_crew(guild, "1234"))
    assert cas.await_count == 2

    channel.send.reset_mock()
    failed = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", failed)
    assert "No ping sent" in asyncio.run(gp.call_game_crew(guild, "1234"))
    channel.send.assert_not_awaited()


def test_configure_remove_is_explicit_and_does_not_reenable_role(monkeypatch):
    guild, _ = _discord_setup(monkeypatch)
    config = _config(enabled=False)
    monkeypatch.setattr(gp, "get_guild_config", AsyncMock(return_value=config))
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", cas)
    assert asyncio.run(gp.configure(guild, "Minecraft", 0, remove=True))
    assert cas.await_args.kwargs["value"]["games"] == {}


def test_role_ping_game_mapping_uses_find_players_aliases():
    assert gp.notification_game_key("MC") == gp.notification_game_key("Minecraft Bedrock")
    assert gp.notification_game_key("minecraft java") == gp.notification_game_key("Minecraft")
    assert gp.notification_game_key("GTA 5") == gp.notification_game_key("Grand Theft Auto V")
    assert gp.notification_game_key("Destiny 2") != gp.notification_game_key("Destiny")


def test_one_call_per_session_survives_game_mapping_removal_and_readdition(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    config = _config()
    # Role assignments can be edited by staff, but cannot refund an already
    # claimed session's ping. This guard survives restart and remapping.
    config[gp.CONFIG_KEY]["called_sessions"] = ["1234"]
    claimed = _wire_sender(monkeypatch, config=config)
    result = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "already called" in result
    claimed.assert_not_awaited()
    channel.send.assert_not_awaited()

    config[gp.CONFIG_KEY]["games"] = {}
    monkeypatch.setattr(gp, "get_guild_config", AsyncMock(return_value=config))
    remove_cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", remove_cas)
    assert asyncio.run(gp.configure(guild, "Minecraft", 0, remove=True))
    assert remove_cas.await_args.kwargs["value"]["called_sessions"] == ["1234"]


def test_claim_appends_session_id_with_bounded_persistence(monkeypatch):
    guild, _ = _discord_setup(monkeypatch)
    config = _config()
    config[gp.CONFIG_KEY]["called_sessions"] = [f"prior-{i}" for i in range(1500)]
    claimed = _wire_sender(monkeypatch, config=config)
    result = asyncio.run(gp.call_game_crew(guild, "1234"))
    assert "Called" in result
    stored = claimed.await_args.kwargs["value"]["called_sessions"]
    assert len(stored) == gp.MAX_SESSION_CLAIMS
    assert stored[-1] == "1234"
    assert "prior-0" not in stored


def test_game_cooldown_survives_mapping_removal_and_new_session(monkeypatch):
    guild, channel = _discord_setup(monkeypatch)
    config = _config()
    config[gp.CONFIG_KEY]["games"]["minecraft"]["last_at"] = 0
    config[gp.CONFIG_KEY]["last_game_calls"] = {"minecraft": 1760799900}
    claimed = _wire_sender(monkeypatch, config=config)
    result = asyncio.run(gp.call_game_crew(guild, "other-new-session"))
    assert "crew can be called again" in result
    channel.send.assert_not_awaited()
    claimed.assert_not_awaited()

    config[gp.CONFIG_KEY]["games"] = {}
    monkeypatch.setattr(gp, "get_guild_config", AsyncMock(return_value=config))
    save = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(gp, "compare_and_swap_guild_config_key", save)
    assert asyncio.run(gp.configure(guild, "MC", 9))
    assert save.await_args.kwargs["value"]["games"]["minecraft"]["last_at"] == 1760799900


def test_non_latin_game_names_do_not_share_one_notification_role_key():
    first = gp.notification_game_key("最终幻想")
    second = gp.notification_game_key("魔兽世界")
    assert first and second and first != second
    assert "game" not in {first, second}
