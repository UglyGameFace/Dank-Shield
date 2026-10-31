from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from stoney_verify import member_connection_controls as controls


def run(coro):
    return asyncio.run(coro)


def test_default_monitor_and_review_are_staff_only_and_protection_is_opt_in():
    assert controls.parse_controls(None) == {
        "monitor": True, "review": True, "protected": {},
    }


@pytest.mark.parametrize("bad", [
    [], "enabled", {"monitor": "off"}, {"review": 1},
    {"protected": []}, {"protected": {"0": {}}},
    {"protected": {"123": {"role_id": "456", "state": "active", "token": "x",
                           "original": {"everyone": "no", "role": None}}}},
])
def test_invalid_settings_fail_closed(bad):
    with pytest.raises(controls.ConnectionControlError):
        controls.parse_controls(bad)


def test_missing_config_never_reenables_monitoring(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(controls, "load_controls", unavailable)
    assert run(controls.auto_review_enabled(123, "monitor")) is False
    assert run(controls.auto_review_enabled(123, "review")) is False
    assert run(controls.auto_review_enabled(123, "invalid")) is False


def install_store(monkeypatch):
    record = {"current": None, "attempts": 0}

    async def get_config(guild_id, *, force_refresh=False):
        assert guild_id == 123
        return {"source": "supabase:guild_configs",
                controls.CONFIG_KEY: copy.deepcopy(record["current"])}

    async def cas(gid, key, *, expected, value, source):
        assert gid == 123 and key == controls.CONFIG_KEY
        assert source == "member_connection_controls"
        record["attempts"] += 1
        if record["current"] != expected:
            return False, {}
        record["current"] = copy.deepcopy(value)
        return True, {}

    monkeypatch.setattr(controls, "get_guild_config", get_config)
    monkeypatch.setattr(controls, "compare_and_swap_guild_config_key", cas)
    return record


def test_server_switches_are_persistent_and_independent(monkeypatch):
    record = install_store(monkeypatch)
    result = run(controls.toggle_review(123, "monitor"))
    assert result["monitor"] is False and result["review"] is True
    assert run(controls.auto_review_enabled(123, "monitor")) is False
    assert run(controls.auto_review_enabled(123, "review")) is True
    assert run(controls.toggle_review(123, "review"))["review"] is False
    assert record["current"]["monitor"] is False
    assert record["current"]["review"] is False
    assert record["attempts"] == 2


class FakeOverwrite:
    def __init__(self, view_channel=None):
        self.view_channel = view_channel

    def is_empty(self):
        return self.view_channel is None


class FakePerms:
    administrator = False
    manage_roles = True
    view_channel = False
    manage_guild = False
    moderate_members = False
    kick_members = False
    ban_members = False


class FakeRole:
    def __init__(self, role_id: int, guild=None):
        self.id = role_id
        self.guild = guild
        self.managed = False
        self.permissions = FakePerms()

    def is_bot_managed(self):
        return False


class FakeMember:
    def __init__(self, guild, member_id):
        self.guild = guild
        self.id = member_id
        self.guild_permissions = FakePerms()
        self.bot = member_id == 999


class FakeTextChannel:
    def __init__(self, guild, channel_id):
        self.guild = guild
        self.id = channel_id
        self.name = "staff-secrets"
        self.overwrites = {}
        self.fail_role = False

    def overwrites_for(self, who):
        current = self.overwrites.get(who)
        return FakeOverwrite(current.view_channel if current is not None else None)

    def permissions_for(self, who):
        return FakePerms()

    async def set_permissions(self, who, *, overwrite, reason):
        if self.fail_role and who.id == 456:
            raise RuntimeError("Discord denied role write")
        if overwrite is None:
            self.overwrites.pop(who, None)
        else:
            self.overwrites[who] = overwrite


class FakeGuild:
    def __init__(self):
        self.id = 123
        self.default_role = FakeRole(123, self)
        self.trusted = FakeRole(456, self)
        self.me = FakeMember(self, 999)
        # Bot Administrator bypasses the deny in the ordinary test guild.
        self.me.guild_permissions.administrator = True
        self.channel = FakeTextChannel(self, 789)
        self.roles = [self.default_role, self.trusted]

    def get_channel(self, cid):
        return self.channel if cid == 789 else None

    def get_role(self, rid):
        return self.trusted if rid == 456 else None


def install_discord(monkeypatch):
    monkeypatch.setattr(controls.discord, "Role", FakeRole)
    monkeypatch.setattr(controls.discord, "Member", FakeMember)
    monkeypatch.setattr(controls.discord, "TextChannel", FakeTextChannel)
    guild = FakeGuild()
    return guild, FakeMember(guild, 888)


def test_confirmed_channel_protection_and_exact_restore(monkeypatch):
    record = install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    stored = record["current"]["protected"]["789"]
    assert stored["state"] == "active"
    assert stored["original"] == {"everyone": None, "role": None}
    assert guild.channel.overwrites_for(guild.default_role).view_channel is False
    assert guild.channel.overwrites_for(guild.trusted).view_channel is True
    assert controls.channel_protection_status(guild, "789", stored).startswith("active")
    run(controls.remove_protection(guild, actor, "789"))
    assert record["current"]["protected"] == {}
    assert guild.channel.overwrites == {}


def test_other_role_explicit_allow_blocks_protection(monkeypatch):
    record = install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    other_role = FakeRole(321, guild)
    other_role.permissions = SimpleNamespace(
        administrator=False, manage_guild=False, manage_roles=False,
        manage_messages=False, moderate_members=False, kick_members=False, ban_members=False,
    )
    guild.channel.overwrites[other_role] = FakeOverwrite(True)
    with pytest.raises(controls.ConnectionControlError, match="Another role or member"):
        run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    assert record["current"] is None
    assert guild.channel.overwrites[other_role].view_channel is True


def test_permission_failure_rolls_back_and_removes_pending_record(monkeypatch):
    record = install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    guild.channel.fail_role = True
    with pytest.raises(RuntimeError, match="Discord denied"):
        run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    assert record["current"]["protected"] == {}
    assert guild.channel.overwrites_for(guild.default_role).view_channel is None


def test_outside_permission_change_prevents_blind_restore(monkeypatch):
    record = install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    guild.channel.overwrites[guild.default_role].view_channel = True
    with pytest.raises(controls.ConnectionControlError, match="changed outside"):
        run(controls.remove_protection(guild, actor, "789"))
    assert "789" in record["current"]["protected"]


def test_nobody_is_restricted_for_connections_and_events_respect_switches():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    events = (root / "stoney_verify/events.py").read_text()
    ui = (root / "stoney_verify/commands_ext/member_command_center.py").read_text()
    assert 'auto_review_enabled(guild.id, "monitor")' in events
    assert 'auto_review_enabled(guild.id, "review")' in events
    assert "Connection Controls" in ui
    service = (root / "stoney_verify/member_connection_controls.py").read_text()
    assert "member.ban(" not in service
    assert "member.kick(" not in service
    assert "member.timeout(" not in service


def test_sensitive_connection_alerts_require_private_modlog(monkeypatch):
    from stoney_verify import modlog
    install_store(monkeypatch)
    guild, _ = install_discord(monkeypatch)
    messages = []

    async def get_channel(_guild):
        return guild.channel

    async def send(guild_arg, embed, *, event_key, dedupe_window_seconds):
        messages.append((event_key, dedupe_window_seconds))

    monkeypatch.setattr(modlog, "_get_modlog_channel_async", get_channel)
    monkeypatch.setattr(modlog, "_post_modlog", send)
    assert run(controls.post_private_connection_alert(
        guild, SimpleNamespace(), event_key="connection:1"
    )) is True
    assert messages == [("connection:1", 900)]

    def public_permissions(who):
        return SimpleNamespace(view_channel=True, manage_roles=True)

    guild.channel.permissions_for = public_permissions
    assert run(controls.post_private_connection_alert(
        guild, SimpleNamespace(), event_key="connection:2"
    )) is False
    assert messages == [("connection:1", 900)]


def test_sensitive_alerts_refuse_untrusted_explicit_member_access(monkeypatch):
    from stoney_verify import modlog
    install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    guild.channel.overwrites[actor] = FakeOverwrite(True)

    async def get_channel(_guild):
        return guild.channel

    async def never_send(*args, **kwargs):
        raise AssertionError("Sensitive connection evidence must not be sent")

    monkeypatch.setattr(modlog, "_get_modlog_channel_async", get_channel)
    monkeypatch.setattr(modlog, "_post_modlog", never_send)
    assert run(controls.post_private_connection_alert(
        guild, SimpleNamespace(), event_key="connection:3"
    )) is False


def test_existing_elevated_moderator_role_grant_is_preserved(monkeypatch):
    install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    mod_role = FakeRole(321, guild)
    guild.channel.overwrites[mod_role] = FakeOverwrite(True)
    run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    assert guild.channel.overwrites_for(mod_role).view_channel is True
    run(controls.remove_protection(guild, actor, "789"))
    assert guild.channel.overwrites_for(mod_role).view_channel is True


def test_bot_must_retain_recovery_visibility_after_everyone_deny(monkeypatch):
    record = install_store(monkeypatch)
    guild, actor = install_discord(monkeypatch)
    guild.me.guild_permissions.administrator = False
    guild.me.roles = []
    with pytest.raises(controls.ConnectionControlError, match="lose channel access"):
        run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    assert record["current"] is None

    guild.channel.overwrites[guild.me] = FakeOverwrite(True)
    run(controls.protect_channel(guild, actor, guild.channel, guild.trusted))
    assert record["current"]["protected"]["789"]["state"] == "active"
    assert guild.channel.overwrites_for(guild.me).view_channel is True
