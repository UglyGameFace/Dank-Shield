from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian
from stoney_verify.globals import bot


class FakeGuild:
    def __init__(self) -> None:
        self.id = 8801
        self.owner_id = 999999
        self.members: dict[int, object] = {}

    def get_member(self, user_id: int):
        return self.members.get(int(user_id))

    async def fetch_member(self, user_id: int):
        member = self.members.get(int(user_id))
        if member is None:
            raise LookupError(user_id)
        return member


class FakeEntry:
    def __init__(self, entry_id: int, action_name: str, guild: FakeGuild, actor=None, user_id=None, before=None, after=None) -> None:
        self.id = entry_id
        self.action = SimpleNamespace(name=action_name)
        self.guild = guild
        self.user = actor
        self.user_id = user_id if user_id is not None else getattr(actor, "id", None)
        self.target = SimpleNamespace(id=entry_id + 100, name="target")
        self.before = before
        self.after = after


def _actor(user_id: int):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_guardian_boot_owns_gateway_and_legacy_installer_is_suppressed() -> None:
    assert bool(getattr(bot, "_dank_antinuke_guardian_installed", False)) is True
    assert bool(getattr(bot, "_dank_antinuke_gateway_runtime_installed", False)) is True


def test_explicit_channel_overwrite_audit_event_routes_to_canonical_engine(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(101)
    guild.members[101] = actor
    entry = FakeEntry(1, "overwrite_update", guild, actor)
    calls: list[dict] = []

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "channel_update"
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_gateway_user_id_is_resolved_before_entry_is_consumed(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(202)
    guild.members[202] = actor
    entry = FakeEntry(2, "emoji_delete", guild, actor=None, user_id=202)
    users: list[int] = []

    async def fake_process(_guild, **kwargs):
        users.append(int(kwargs["entry"].user.id))
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert users == [202]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_unresolved_executor_is_left_for_rest_reconciliation(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    entry = FakeEntry(3, "sticker_delete", guild, actor=None, user_id=303)
    reconciled: list[str] = []

    async def fake_reconcile(_guild, _entry, action_name, _spec):
        reconciled.append(action_name)

    monkeypatch.setattr(guardian, "_rest_reconcile", fake_reconcile)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert reconciled == ["sticker_delete"]
    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_role_position_change_is_security_state(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(404)
    guild.members[404] = actor
    calls: list[dict] = []
    permissions = SimpleNamespace(
        administrator=False,
        manage_guild=False,
        manage_roles=False,
        manage_channels=False,
        ban_members=False,
        kick_members=False,
        manage_webhooks=False,
        moderate_members=False,
    )
    entry = FakeEntry(
        4,
        "role_update",
        guild,
        actor,
        before=SimpleNamespace(position=2, permissions=permissions),
        after=SimpleNamespace(position=20, permissions=permissions),
    )

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    asyncio.run(guardian._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "role_update"
    _reset()
