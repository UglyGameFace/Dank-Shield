from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from discord.audit_logs import AuditLogChanges

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


class FakeGuild:
    def __init__(self, guild_id: int = 6001) -> None:
        self.id = guild_id
        self.owner_id = 999999
        self.members: dict[int, object] = {}
        self.roles: dict[int, object] = {}

    def get_member(self, user_id: int):
        return self.members.get(int(user_id))

    async def fetch_member(self, user_id: int):
        if int(user_id) not in self.members:
            raise LookupError(user_id)
        return self.members[int(user_id)]

    def get_role(self, role_id: int):
        return self.roles.get(int(role_id))


class FakeEntry:
    def __init__(
        self,
        entry_id: int,
        action_name: str,
        guild: FakeGuild,
        actor=None,
        *,
        user_id: int | None = None,
        target=None,
        before=None,
        after=None,
    ) -> None:
        self.id = entry_id
        self.action = SimpleNamespace(name=action_name)
        self.guild = guild
        self.user = actor
        self.user_id = user_id if user_id is not None else getattr(actor, "id", None)
        self.target = target or SimpleNamespace(id=entry_id + 100, name="target")
        self.before = before
        self.after = after


def _actor(user_id: int):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_small_multi_moderator_burst_does_not_trigger_panic() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(101)
    second = _actor(102)

    states = [
        guardian._panic_state(guild, first, "ban"),
        guardian._panic_state(guild, second, "kick"),
        guardian._panic_state(guild, first, "ban"),
        guardian._panic_state(guild, second, "kick"),
    ]

    assert all(triggered is False for _active, triggered, _observed in states)
    assert guild.id not in guardian._PANIC_UNTIL
    _reset()


def test_distributed_moderation_flood_still_triggers_emergency_ceiling() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(201)
    second = _actor(202)
    triggered = False

    for index in range(guardian._PANIC_MODERATION_EVENT_THRESHOLD):
        actor = first if index % 2 == 0 else second
        active, found_trigger, _observed = guardian._panic_state(
            guild,
            actor,
            "ban" if index % 3 else "kick",
        )
        triggered = triggered or found_trigger
        if found_trigger:
            assert active is True

    assert triggered is True
    assert guardian._PANIC_UNTIL[guild.id] > 0
    _reset()


def test_structural_multi_actor_attack_triggers_before_large_event_count() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(301)
    second = _actor(302)

    assert guardian._panic_state(guild, first, "channel_delete")[1] is False
    active, triggered, observed = guardian._panic_state(
        guild,
        second,
        "role_delete",
    )

    assert active is True
    assert triggered is True
    assert {actor.id for actor in observed} == {301, 302}
    _reset()


def test_cosmetic_role_edit_has_low_panic_weight() -> None:
    entry = SimpleNamespace(
        before=SimpleNamespace(name="Old", position=4, permissions=SimpleNamespace()),
        after=SimpleNamespace(name="New", position=4, permissions=SimpleNamespace()),
    )
    assert guardian._panic_weight("role_update", entry) == 1


def test_role_position_change_is_high_risk_for_panic() -> None:
    entry = SimpleNamespace(
        before=SimpleNamespace(name="Staff", position=4, permissions=SimpleNamespace()),
        after=SimpleNamespace(name="Staff", position=7, permissions=SimpleNamespace()),
    )
    assert guardian._panic_weight("role_update", entry) == 4


def test_discordpy_member_role_add_maps_to_after_roles() -> None:
    guild = FakeGuild()
    role = SimpleNamespace(id=701, name="Moderator")
    guild.roles[role.id] = role
    entry = SimpleNamespace(
        action=discord.AuditLogAction.member_role_update,
        guild=guild,
    )

    changes = AuditLogChanges(
        entry,
        [{"key": "$add", "new_value": [{"id": str(role.id), "name": role.name}]}],
    )

    assert getattr(changes.before, "roles", []) == []
    assert [found.id for found in changes.after.roles] == [role.id]


def test_discordpy_member_role_remove_maps_to_before_roles() -> None:
    guild = FakeGuild()
    role = SimpleNamespace(id=702, name="Trusted")
    guild.roles[role.id] = role
    entry = SimpleNamespace(
        action=discord.AuditLogAction.member_role_update,
        guild=guild,
    )

    changes = AuditLogChanges(
        entry,
        [{"key": "$remove", "new_value": [{"id": str(role.id), "name": role.name}]}],
    )

    assert [found.id for found in changes.before.roles] == [role.id]
    assert getattr(changes.after, "roles", []) == []


def test_sparse_gateway_actor_resolves_from_user_id_member_cache() -> None:
    guild = FakeGuild()
    actor = _actor(401)
    guild.members[actor.id] = actor
    entry = FakeEntry(9001, "channel_delete", guild, actor=None, user_id=actor.id)

    resolved = asyncio.run(guardian._resolve_actor(guild, entry))

    assert resolved is actor


def test_sparse_gateway_evidence_is_not_consumed_before_rest_reconcile(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    entry = FakeEntry(9002, "channel_delete", guild, actor=None, user_id=777)
    reconciled: list[tuple[str, int]] = []

    async def fake_reconcile(_guild, found_entry, action_name, _spec):
        reconciled.append((action_name, int(found_entry.id)))

    monkeypatch.setattr(guardian, "_rest_reconcile", fake_reconcile)

    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert reconciled == [("channel_delete", 9002)]
    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_routine_guild_update_is_ignored(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(601)
    processed: list[int] = []
    entry = FakeEntry(
        9101,
        "guild_update",
        guild,
        actor=actor,
        before=SimpleNamespace(afk_timeout=300),
        after=SimpleNamespace(afk_timeout=600),
    )

    async def fake_process(*_args, **_kwargs):
        processed.append(1)

    monkeypatch.setattr(guardian, "_process", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert processed == []
    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_cosmetic_guild_identity_update_is_ignored(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(605)
    processed: list[int] = []
    entry = FakeEntry(
        9105,
        "guild_update",
        guild,
        actor=actor,
        before=SimpleNamespace(name="The 420 Lobby", icon="old"),
        after=SimpleNamespace(name="The 420 Garden", icon="new"),
    )

    async def fake_process(*_args, **_kwargs):
        processed.append(1)

    monkeypatch.setattr(guardian, "_process", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert processed == []
    assert guardian._panic_weight("guild_update", entry) == 0
    assert anti_nuke._audit_entry_seen(entry) is False
    assert guild.id not in guardian._PANIC_EVENTS
    _reset()


def test_guild_update_panic_weight_tracks_security_severity() -> None:
    routine = SimpleNamespace(
        before=SimpleNamespace(name="A"),
        after=SimpleNamespace(name="B"),
    )
    bounded = SimpleNamespace(
        before=SimpleNamespace(verification_level=3),
        after=SimpleNamespace(verification_level=0),
    )
    immediate = SimpleNamespace(
        before=SimpleNamespace(mfa_level=0),
        after=SimpleNamespace(mfa_level=1),
    )

    assert guardian._panic_weight("guild_update", routine) == 0
    assert guardian._panic_weight("guild_update", bounded) == 2
    assert guardian._panic_weight("guild_update", immediate) == 4


def test_identity_or_security_guild_update_is_enforced(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(602)
    processed: list[tuple[str, str]] = []
    entry = FakeEntry(
        9102,
        "guild_update",
        guild,
        actor=actor,
        before=SimpleNamespace(name="Safe Server", verification_level=3),
        after=SimpleNamespace(name="Pwned", verification_level=0),
    )

    async def fake_process(_guild, found_entry, _actor, action_name, _spec):
        processed.append((action_name, guardian._target_label(action_name, found_entry)))

    monkeypatch.setattr(guardian, "_process", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert processed and processed[0][0] == "guild_update"
    assert "name" in processed[0][1]
    assert "verification_level" in processed[0][1]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_distributed_security_guild_updates_still_trigger_weighted_panic() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(603)
    second = _actor(604)
    entries = (
        (first, FakeEntry(9103, "guild_update", guild, actor=first,
            before=SimpleNamespace(verification_level=3),
            after=SimpleNamespace(verification_level=0))),
        (second, FakeEntry(9104, "guild_update", guild, actor=second,
            before=SimpleNamespace(explicit_content_filter=2),
            after=SimpleNamespace(explicit_content_filter=0))),
        (first, FakeEntry(9106, "guild_update", guild, actor=first,
            before=SimpleNamespace(vanity_url_code="safe"),
            after=SimpleNamespace(vanity_url_code="changed"))),
        (second, FakeEntry(9107, "guild_update", guild, actor=second,
            before=SimpleNamespace(features=["COMMUNITY"]),
            after=SimpleNamespace(features=[]))),
    )

    results = [
        guardian._panic_state(guild, actor, "guild_update", entry=entry)
        for actor, entry in entries
    ]

    assert all(triggered is False for _active, triggered, _observed in results[:3])
    active, triggered, observed = results[-1]
    assert active is True
    assert triggered is True
    assert {actor.id for actor in observed} == {603, 604}
    _reset()


class FakeOverwriteChannel:
    def __init__(self, channel_id: int, current: discord.PermissionOverwrite | None = None) -> None:
        self.id = channel_id
        self.name = "secure"
        self.current = current or discord.PermissionOverwrite()
        self.calls: list[dict] = []

    def overwrites_for(self, _principal):
        return self.current

    async def set_permissions(self, principal, *, overwrite=None, reason=None):
        self.calls.append(
            {"principal": principal, "overwrite": overwrite, "reason": reason}
        )
        self.current = overwrite or discord.PermissionOverwrite()


def _enabled_contain_settings(**patch):
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain", **patch}
    )


def test_undelegated_overwrite_create_is_rolled_back(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor(801)
    principal = SimpleNamespace(id=802, name="Raiders")
    guild.roles[principal.id] = principal
    channel = FakeOverwriteChannel(803)
    entry = FakeEntry(9201, "overwrite_create", guild, actor=actor, target=channel)
    entry.extra = principal

    async def fake_settings(_guild_id):
        return _enabled_contain_settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_overwrite(
            guild,
            entry,
            actor,
            "overwrite_create",
        )
    )

    assert "removed unauthorized" in result
    assert channel.calls and channel.calls[-1]["overwrite"] is None


def test_undelegated_overwrite_update_restores_before_pair(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor(811)
    principal = SimpleNamespace(id=812, name="Members")
    guild.roles[principal.id] = principal
    current = discord.PermissionOverwrite(manage_channels=True)
    channel = FakeOverwriteChannel(813, current=current)
    old_allow = discord.Permissions.none()
    old_deny = discord.Permissions.none()
    old_deny.manage_channels = True
    entry = FakeEntry(
        9202,
        "overwrite_update",
        guild,
        actor=actor,
        target=channel,
        before=SimpleNamespace(allow=old_allow, deny=old_deny),
    )
    entry.extra = principal

    async def fake_settings(_guild_id):
        return _enabled_contain_settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_overwrite(
            guild,
            entry,
            actor,
            "overwrite_update",
        )
    )

    assert "restored overwrite" in result
    restored = channel.calls[-1]["overwrite"]
    allow, deny = restored.pair()
    assert allow.manage_channels is False
    assert deny.manage_channels is True


def test_delegated_operator_overwrite_is_not_rolled_back(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor(821)
    principal = SimpleNamespace(id=822, name="Staff")
    guild.roles[principal.id] = principal
    channel = FakeOverwriteChannel(823)
    entry = FakeEntry(9203, "overwrite_update", guild, actor=actor, target=channel)
    entry.extra = principal

    async def fake_settings(_guild_id):
        return _enabled_contain_settings(antinuke_trusted_user_ids=[actor.id])

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    result = asyncio.run(
        guardian._rollback_untrusted_overwrite(
            guild,
            entry,
            actor,
            "overwrite_update",
        )
    )

    assert result == ""
    assert channel.calls == []


class ListenerBot:
    def __init__(self) -> None:
        self.intents = SimpleNamespace(moderation=True)
        self.added: list[tuple[object, str]] = []
        self.removed: list[tuple[object, str]] = []

    def add_listener(self, callback, name: str) -> None:
        self.added.append((callback, name))

    def remove_listener(self, callback, name: str) -> None:
        self.removed.append((callback, name))


def test_gateway_installer_retires_wrong_native_overwrite_owner() -> None:
    bot = ListenerBot()

    assert gateway.install_anti_nuke_gateway_runtime(bot) is True

    assert (
        anti_nuke.antinuke_on_guild_channel_update,
        "on_guild_channel_update",
    ) in bot.removed
    assert sum(name == "on_guild_channel_update" for _cb, name in bot.added) == 1
    assert sum(name == "on_audit_log_entry_create" for _cb, name in bot.added) == 1


def test_automod_rule_creation_cannot_singlehandedly_trigger_panic() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(501)
    second = _actor(502)

    for index in range(6):
        actor = first if index % 2 == 0 else second
        _active, triggered, _observed = guardian._panic_state(
            guild,
            actor,
            "automod_rule_create",
        )
        assert triggered is False

    assert guild.id not in guardian._PANIC_UNTIL
    _reset()
