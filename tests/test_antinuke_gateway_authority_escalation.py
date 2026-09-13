from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


def _permissions(**overrides):
    values = {name: False for name in anti_nuke.DANGEROUS_PERMISSION_NAMES}
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(action_name: str, *, before=None, after=None):
    actor = SimpleNamespace(id=400, roles=[], mention="<@400>")
    return SimpleNamespace(
        id=900,
        action=SimpleNamespace(name=action_name),
        guild=SimpleNamespace(id=100, owner_id=999),
        user=actor,
        user_id=actor.id,
        target=SimpleNamespace(id=500, name="target"),
        before=before or SimpleNamespace(),
        after=after or SimpleNamespace(),
    )


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_dangerous_role_permission_addition_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(permissions=_permissions(administrator=False)),
        after=SimpleNamespace(permissions=_permissions(administrator=True)),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_dangerous_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_benign_role_update_stays_with_guardian(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(
            permissions=_permissions(),
            name="old",
        ),
        after=SimpleNamespace(
            permissions=_permissions(),
            name="new",
        ),
    )
    delegated: list[int] = []

    async def fake_guardian(found_entry):
        delegated.append(int(found_entry.id))

    monkeypatch.setattr(guardian, "_on_audit_log_entry_create", fake_guardian)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert delegated == [900]
    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_sensitive_member_role_grant_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    dangerous_role = SimpleNamespace(
        id=700,
        name="Admin",
        permissions=_permissions(administrator=True),
    )
    entry = _entry(
        "member_role_update",
        before=SimpleNamespace(roles=[]),
        after=SimpleNamespace(roles=[dangerous_role]),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_role_removal_also_uses_claimed_gateway_evidence(monkeypatch) -> None:
    _reset()
    role = SimpleNamespace(id=701, name="Moderator", permissions=_permissions())
    entry = _entry(
        "member_role_update",
        before=SimpleNamespace(roles=[role]),
        after=SimpleNamespace(roles=[]),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_timeout_extension_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    now = datetime.now(timezone.utc)
    entry = _entry(
        "member_update",
        before=SimpleNamespace(timed_out_until=None),
        after=SimpleNamespace(timed_out_until=now + timedelta(minutes=30)),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_timeout", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_unresolved_gateway_actor_is_not_consumed(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(permissions=_permissions(administrator=False)),
        after=SimpleNamespace(permissions=_permissions(administrator=True)),
    )

    async def unresolved(_guild, _entry):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", unresolved)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()
