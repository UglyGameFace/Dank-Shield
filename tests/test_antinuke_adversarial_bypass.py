from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke


class FakePermissions:
    def __init__(self, **values):
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, bool(values.get(name, False)))
        self.view_audit_log = bool(values.get("view_audit_log", False))
        self.manage_roles = bool(values.get("manage_roles", False))


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
    ) -> None:
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


class FakeMember:
    def __init__(self, user_id: int, *, roles=None, top_role=None, guild=None, bot=False):
        self.id = user_id
        self.roles = list(roles or [])
        self.top_role = top_role or (self.roles[-1] if self.roles else None)
        self.guild = guild
        self.bot = bot
        self.mention = f"<@{user_id}>"

    def __str__(self) -> str:
        return f"member-{self.id}"


class FakeAuditEntry:
    def __init__(self, entry_id: int, actor, target_id: int | None):
        self.id = entry_id
        self.user = actor
        self.target = SimpleNamespace(id=target_id) if target_id is not None else None
        self.created_at = datetime.now(timezone.utc)


def _reset_runtime_state() -> None:
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()


def test_effective_member_hierarchy_blocks_lower_dangerous_role_bypass() -> None:
    bot_top = FakeRole(100, "Dank Shield", position=50)
    dangerous_low = FakeRole(
        200,
        "Moderator Power",
        permissions=FakePermissions(manage_roles=True, manage_channels=True),
        position=40,
    )
    harmless_high = FakeRole(300, "Cosmetic VIP", position=60)
    attacker = FakeMember(
        444,
        roles=[dangerous_low, harmless_high],
        top_role=harmless_high,
    )
    dangerous_low.members = [attacker]
    harmless_high.members = [attacker]

    me = SimpleNamespace(
        top_role=bot_top,
        guild_permissions=FakePermissions(view_audit_log=True, manage_roles=True),
    )
    guild = SimpleNamespace(
        id=1,
        owner_id=999,
        me=me,
        roles=[dangerous_low, harmless_high],
    )
    settings = anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    blockers = anti_nuke.antinuke_permission_health(guild, settings)

    assert any(
        "member 444 outranks Dank Shield while inheriting @Moderator Power" in item
        for item in blockers
    )


def test_same_event_trusted_role_cannot_launder_actor_into_exemption() -> None:
    trusted = FakeRole(333, "Trusted", position=10)
    actor = FakeMember(444, roles=[trusted], top_role=trusted)
    guild = SimpleNamespace(owner_id=999)
    settings = anti_nuke.normalize_antinuke_settings(
        {"antinuke_trusted_role_ids": [trusted.id]}
    )

    assert anti_nuke.is_trusted_actor(guild, actor, settings) is True
    assert (
        anti_nuke.is_trusted_actor(
            guild,
            actor,
            settings,
            ignore_role_ids={trusted.id},
        )
        is False
    )


def test_self_granted_trusted_role_is_treated_as_security_sensitive(monkeypatch) -> None:
    _reset_runtime_state()
    trusted = FakeRole(333, "Trusted", position=10)
    guild = SimpleNamespace(
        id=77,
        owner_id=999,
        me=SimpleNamespace(top_role=FakeRole(1, "Bot", position=50)),
    )
    before = FakeMember(444, roles=[], top_role=FakeRole(2, "Base", position=5), guild=guild)
    after = FakeMember(444, roles=[trusted], top_role=trusted, guild=guild)
    entry = FakeAuditEntry(9001, after, after.id)
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {
                "antinuke_enabled": True,
                "antinuke_trusted_role_ids": [trusted.id],
            }
        )

    async def fake_audit(_guild, _action, *, target_id=None, retries=4):
        _ = retries
        assert target_id == after.id
        return entry

    async def fake_contain(*_args, **_kwargs):
        return [], []

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", fake_audit)
    monkeypatch.setattr(anti_nuke, "_contain_actor", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    asyncio.run(anti_nuke._handle_member_dangerous_role_grant(before, after))

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Security-Sensitive Role Grant"
    assert "trusted-exemption" in incidents[0]["action_label"]
    _reset_runtime_state()


def test_pretrusted_delegate_cannot_mint_another_trusted_exemption(monkeypatch) -> None:
    _reset_runtime_state()
    trusted = FakeRole(333, "Trusted", position=10)
    actor = FakeMember(555, roles=[trusted], top_role=trusted)
    guild = SimpleNamespace(id=77, owner_id=999)
    before = FakeMember(444, roles=[], guild=guild)
    after = FakeMember(444, roles=[trusted], top_role=trusted, guild=guild)
    entry = FakeAuditEntry(9002, actor, after.id)
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {
                "antinuke_enabled": True,
                "antinuke_mode": "alert",
                "antinuke_trusted_role_ids": [trusted.id],
            }
        )

    async def fake_audit(*_args, **_kwargs):
        return entry

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", fake_audit)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    asyncio.run(anti_nuke._handle_member_dangerous_role_grant(before, after))

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Security-Sensitive Role Grant"
    _reset_runtime_state()


def test_structural_slow_roll_triggers_even_when_short_window_is_evaded(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=88, owner_id=999)
    actor = FakeMember(777)
    entries = iter(
        [
            FakeAuditEntry(1, actor, 101),
            FakeAuditEntry(2, actor, 102),
            FakeAuditEntry(3, actor, 103),
            FakeAuditEntry(4, actor, 104),
        ]
    )
    now = {"value": 0.0}
    contain_calls: list[int] = []
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def fake_audit(_guild, _action, *, target_id=None, retries=4):
        _ = retries
        entry = next(entries)
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
    monkeypatch.setattr(anti_nuke.time, "monotonic", lambda: now["value"])

    async def run() -> None:
        for offset, target_id in enumerate((101, 102, 103, 104)):
            now["value"] = float(offset * 20)
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

    assert contain_calls == [777]
    assert len(incidents) == 1
    assert "delegated long-horizon" in incidents[0]["count_label"]
    _reset_runtime_state()


def test_member_remove_falls_back_to_immediate_prune_detection(monkeypatch) -> None:
    calls: list[dict] = []
    guild = SimpleNamespace(id=91)
    member = FakeMember(1234, guild=guild)

    async def fake_handle(_guild, **kwargs):
        calls.append(kwargs)
        return len(calls) == 2

    monkeypatch.setattr(anti_nuke, "_handle_threshold_event", fake_handle)

    asyncio.run(anti_nuke.antinuke_on_member_remove(member))

    assert calls[0]["audit_action"] == "kick"
    assert calls[1]["audit_action"] == "member_prune"
    assert calls[1]["threshold_override"] == 1
