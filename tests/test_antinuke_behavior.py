from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify.globals import bot


class FakePermissions:
    def __init__(self, **values):
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, bool(values.get(name, False)))
        for name, value in values.items():
            setattr(self, name, bool(value))


class FakeRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        *,
        permissions=None,
        position: int = 1,
        managed: bool = False,
        default: bool = False,
        members=None,
    ):
        self.id = role_id
        self.name = name
        self.permissions = permissions or FakePermissions()
        self.position = position
        self.managed = managed
        self._default = default
        self.members = list(members or [])

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other) -> bool:
        return self.position < getattr(other, "position", 0)


class FakeActor:
    def __init__(self, user_id: int, *, roles=None):
        self.id = user_id
        self.roles = list(roles or [])
        self.mention = f"<@{user_id}>"

    def __str__(self) -> str:
        return f"actor-{self.id}"


class FakeAuditEntry:
    def __init__(self, entry_id: int, actor, target_id: int | None):
        self.id = entry_id
        self.user = actor
        self.target = SimpleNamespace(id=target_id) if target_id is not None else None
        self.created_at = datetime.now(timezone.utc)


class FakeAuditGuild:
    def __init__(self, entries):
        self.id = 777
        self.entries = list(entries)
        self.last_limit = None

    async def audit_logs(self, *, limit, action):
        _ = action
        self.last_limit = limit
        for entry in self.entries:
            yield entry


def _reset_runtime_state() -> None:
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()


def test_default_antinuke_policy_is_opt_in_and_containing_when_enabled() -> None:
    settings = anti_nuke.normalize_antinuke_settings({})

    assert settings["antinuke_enabled"] is False
    assert settings["antinuke_mode"] == "contain"
    assert settings["antinuke_channel_delete_threshold"] == 2
    assert settings["antinuke_role_delete_threshold"] == 2
    assert settings["antinuke_ban_threshold"] == 3
    assert settings["antinuke_kick_threshold"] == 3
    assert settings["antinuke_webhook_create_threshold"] == 2
    assert settings["antinuke_protect_role_escalation"] is True


def test_antinuke_settings_are_bounded_and_normalized() -> None:
    settings = anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_enabled": "yes",
            "antinuke_mode": "UNKNOWN",
            "antinuke_window_seconds": 1,
            "antinuke_channel_delete_threshold": 999,
            "antinuke_trusted_user_ids": ["123", 123, "bad", "456"],
            "antinuke_trusted_role_ids": "789",
        }
    )

    assert settings["antinuke_enabled"] is True
    assert settings["antinuke_mode"] == "contain"
    assert settings["antinuke_window_seconds"] == 5
    assert settings["antinuke_channel_delete_threshold"] == 25
    assert settings["antinuke_trusted_user_ids"] == [123, 456]
    assert settings["antinuke_trusted_role_ids"] == [789]


def test_dangerous_permission_escalation_detects_only_new_permissions() -> None:
    before = FakePermissions(manage_channels=True)
    after = FakePermissions(manage_channels=True, administrator=True, manage_roles=True)

    assert anti_nuke.dangerous_permissions_added(before, after) == [
        "administrator",
        "manage_roles",
    ]


def test_trusted_actor_policy_exempts_owner_explicit_user_and_explicit_role() -> None:
    settings = anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_trusted_user_ids": [222],
            "antinuke_trusted_role_ids": [333],
        }
    )
    guild = SimpleNamespace(owner_id=111)

    assert anti_nuke.is_trusted_actor(guild, FakeActor(111), settings) is True
    assert anti_nuke.is_trusted_actor(guild, FakeActor(222), settings) is True
    assert (
        anti_nuke.is_trusted_actor(
            guild,
            FakeActor(444, roles=[FakeRole(333, "Trusted")]),
            settings,
        )
        is True
    )
    assert anti_nuke.is_trusted_actor(guild, FakeActor(555), settings) is False


def test_action_window_counts_only_current_window(monkeypatch) -> None:
    _reset_runtime_state()
    times = iter([100.0, 101.0, 102.0, 130.0])
    monkeypatch.setattr(anti_nuke.time, "monotonic", lambda: next(times))

    assert anti_nuke._record_action(1, 2, "channel_delete", window_seconds=15) == 1
    assert anti_nuke._record_action(1, 2, "channel_delete", window_seconds=15) == 2
    assert anti_nuke._record_action(1, 2, "channel_delete", window_seconds=15) == 3
    assert anti_nuke._record_action(1, 2, "channel_delete", window_seconds=15) == 1

    _reset_runtime_state()


def test_get_settings_uses_cached_guild_config_by_default(monkeypatch) -> None:
    calls: list[bool] = []

    async def fake_get_guild_config(_guild_id: int, *, refresh=False):
        calls.append(bool(refresh))
        return {"antinuke_enabled": True}

    monkeypatch.setattr(anti_nuke, "get_guild_config", fake_get_guild_config)

    settings = asyncio.run(anti_nuke.get_antinuke_settings(1234))

    assert settings["antinuke_enabled"] is True
    assert calls == [False]


def test_permission_health_blocks_unmanageable_dangerous_role_hierarchy() -> None:
    bot_top = FakeRole(10, "Dank Shield", position=50)
    admin_role = FakeRole(
        20,
        "Administrator",
        permissions=FakePermissions(administrator=True),
        position=60,
    )
    attacker = FakeActor(222, roles=[admin_role])
    admin_role.members = [attacker]
    me = SimpleNamespace(
        top_role=bot_top,
        guild_permissions=FakePermissions(
            view_audit_log=True,
            manage_roles=True,
            kick_members=True,
        ),
    )
    guild = SimpleNamespace(id=1, owner_id=999, me=me, roles=[admin_role])
    settings = anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    blockers = anti_nuke.antinuke_permission_health(guild, settings)

    assert any("Role hierarchy" in item and "@Administrator" in item for item in blockers)

    trusted = anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_enabled": True,
            "antinuke_trusted_role_ids": [admin_role.id],
        }
    )
    trusted_blockers = anti_nuke.antinuke_permission_health(guild, trusted)
    assert any("Role hierarchy" in item and "@Administrator" in item for item in trusted_blockers)


def test_mass_delete_threshold_contains_once_and_logs(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=1001, owner_id=9999)
    actor = FakeActor(2222)
    audit_entries = iter(
        [
            FakeAuditEntry(1, actor, 10),
            FakeAuditEntry(2, actor, 11),
            FakeAuditEntry(3, actor, 12),
        ]
    )
    contain_calls: list[tuple[int, str]] = []
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_trusted_user_ids": [actor.id]}
        )

    async def fake_audit(_guild, _action_name, *, target_id=None, retries=3):
        _ = retries
        entry = next(audit_entries)
        assert entry.target.id == target_id
        return entry

    async def fake_contain(_guild, found_actor, *, reason: str):
        contain_calls.append((found_actor.id, reason))
        return ["Administrator"], []

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", fake_audit)
    monkeypatch.setattr(anti_nuke, "_contain_actor", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    async def run() -> None:
        for target_id in (10, 11, 12):
            await anti_nuke._handle_threshold_event(
                guild,
                audit_action="channel_delete",
                action_key="channel_delete",
                action_label="Mass channel deletion",
                target_id=target_id,
                target_label=f"channel-{target_id}",
                threshold_key="antinuke_channel_delete_threshold",
            )

    asyncio.run(run())

    assert len(contain_calls) == 1
    assert contain_calls[0][0] == 2222
    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Triggered"
    assert "channel_delete 2/2" in incidents[0]["count_label"]
    assert "Administrator" in incidents[0]["response_label"]

    _reset_runtime_state()


def test_mixed_destructive_actions_share_actor_wide_threshold(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=2001, owner_id=9999)
    actor = FakeActor(3333)
    audit_entries = iter(
        [
            FakeAuditEntry(11, actor, 101),
            FakeAuditEntry(12, actor, 102),
            FakeAuditEntry(13, actor, 103),
        ]
    )
    contain_calls: list[int] = []
    incidents: list[dict] = []

    settings = anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_enabled": True,
            "antinuke_channel_delete_threshold": 3,
            "antinuke_role_delete_threshold": 3,
            "antinuke_ban_threshold": 3,
            "antinuke_kick_threshold": 3,
            "antinuke_webhook_create_threshold": 3,
            "antinuke_trusted_user_ids": [actor.id],
        }
    )

    async def fake_settings(_guild_id: int):
        return settings

    async def fake_audit(_guild, _action_name, *, target_id=None, retries=3):
        _ = retries
        entry = next(audit_entries)
        assert entry.target.id == target_id
        return entry

    async def fake_contain(_guild, found_actor, *, reason: str):
        _ = reason
        contain_calls.append(found_actor.id)
        return ["Administrator"], []

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", fake_audit)
    monkeypatch.setattr(anti_nuke, "_contain_actor", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    async def run() -> None:
        await anti_nuke._handle_threshold_event(
            guild,
            audit_action="channel_delete",
            action_key="channel_delete",
            action_label="Mass channel deletion",
            target_id=101,
            target_label="channel-101",
            threshold_key="antinuke_channel_delete_threshold",
        )
        await anti_nuke._handle_threshold_event(
            guild,
            audit_action="role_delete",
            action_key="role_delete",
            action_label="Mass role deletion",
            target_id=102,
            target_label="role-102",
            threshold_key="antinuke_role_delete_threshold",
        )
        await anti_nuke._handle_threshold_event(
            guild,
            audit_action="ban",
            action_key="ban",
            action_label="Mass member bans",
            target_id=103,
            target_label="member-103",
            threshold_key="antinuke_ban_threshold",
        )

    asyncio.run(run())

    assert contain_calls == [3333]
    assert len(incidents) == 1
    assert "mixed destructive actions 3/3" in incidents[0]["count_label"]

    _reset_runtime_state()


def test_failed_containment_does_not_start_cooldown(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=3001, owner_id=9999)
    actor = FakeActor(4444)
    audit_entries = iter(
        [
            FakeAuditEntry(21, actor, 201),
            FakeAuditEntry(22, actor, 202),
            FakeAuditEntry(23, actor, 203),
        ]
    )
    contain_calls: list[int] = []
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_trusted_user_ids": [actor.id]}
        )

    async def fake_audit(_guild, _action_name, *, target_id=None, retries=3):
        _ = retries
        entry = next(audit_entries)
        assert entry.target.id == target_id
        return entry

    async def fake_contain(_guild, found_actor, *, reason: str):
        _ = reason
        contain_calls.append(found_actor.id)
        return [], ["Administrator"]

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", fake_audit)
    monkeypatch.setattr(anti_nuke, "_contain_actor", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    async def run() -> None:
        for target_id in (201, 202, 203):
            await anti_nuke._handle_threshold_event(
                guild,
                audit_action="channel_delete",
                action_key="channel_delete",
                action_label="Mass channel deletion",
                target_id=target_id,
                target_label=f"channel-{target_id}",
                threshold_key="antinuke_channel_delete_threshold",
            )

    asyncio.run(run())

    assert contain_calls == [4444, 4444]
    assert len(incidents) == 2
    assert all("will retry" in item["response_label"] for item in incidents)
    assert (
        3001,
        4444,
        anti_nuke._CONTAINMENT_COOLDOWN_KEY,
    ) not in anti_nuke._TRIGGER_COOLDOWNS

    _reset_runtime_state()


def test_audit_lookup_skips_consumed_entry_and_searches_deeper(monkeypatch) -> None:
    _reset_runtime_state()
    actor = FakeActor(5555)
    first = FakeAuditEntry(31, actor, None)
    second = FakeAuditEntry(32, actor, None)
    guild = FakeAuditGuild([first, second])

    anti_nuke._consume_audit_entry(first)
    monkeypatch.setattr(anti_nuke, "_audit_action", lambda _name: object())

    found = asyncio.run(
        anti_nuke._find_recent_audit_entry(
            guild,
            "webhook_create",
            target_id=None,
            retries=1,
        )
    )

    assert found is second
    assert guild.last_limit == anti_nuke._AUDIT_SEARCH_LIMIT == 50

    _reset_runtime_state()


def test_unattributed_destructive_event_never_contains(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=1002, owner_id=9999)
    contain_calls: list[int] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def no_audit(*_args, **_kwargs):
        return None

    async def fake_contain(*_args, **_kwargs):
        contain_calls.append(1)
        return [], []

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", no_audit)
    monkeypatch.setattr(anti_nuke, "_contain_actor", fake_contain)

    asyncio.run(
        anti_nuke._handle_threshold_event(
            guild,
            audit_action="channel_delete",
            action_key="channel_delete",
            action_label="Mass channel deletion",
            target_id=10,
            target_label="channel-10",
            threshold_key="antinuke_channel_delete_threshold",
        )
    )

    assert contain_calls == []
    _reset_runtime_state()


def test_native_antinuke_registers_expected_discord_listeners() -> None:
    expected = {
        anti_nuke.antinuke_on_guild_channel_create,
        anti_nuke.antinuke_on_guild_channel_update,
        anti_nuke.antinuke_on_guild_channel_delete,
        anti_nuke.antinuke_on_guild_role_create,
        anti_nuke.antinuke_on_guild_role_delete,
        anti_nuke.antinuke_on_member_ban,
        anti_nuke.antinuke_on_member_remove,
        anti_nuke.antinuke_on_webhooks_update,
        anti_nuke.antinuke_on_guild_role_update,
        anti_nuke.antinuke_on_member_update,
        anti_nuke.antinuke_on_member_join,
    }
    registered = set()
    extra_events = getattr(bot, "extra_events", {}) or {}
    for listeners in extra_events.values():
        registered.update(listeners or [])

    assert expected.issubset(registered)
