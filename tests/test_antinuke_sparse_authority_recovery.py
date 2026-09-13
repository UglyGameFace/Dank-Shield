from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke_incident_runtime as incident
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


class Action:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeBot:
    def __init__(self) -> None:
        self.intents = SimpleNamespace(moderation=True)
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, callback, name: str) -> None:
        self.listeners.append((callback, name))

    def remove_listener(self, callback, name: str) -> None:
        try:
            self.listeners.remove((callback, name))
        except ValueError:
            pass


def _entry(action: str, target_id: int = 123):
    return SimpleNamespace(
        action=Action(action),
        guild=SimpleNamespace(id=9001),
        user=None,
        user_id=777,
        target=SimpleNamespace(id=target_id),
        before=SimpleNamespace(),
        after=SimpleNamespace(),
    )


def test_incident_runtime_replaces_gateway_instead_of_stacking_audit_owners(monkeypatch) -> None:
    bot = FakeBot()
    monkeypatch.setattr(incident, "_patch_security_state_persistence", lambda: True)
    monkeypatch.setattr(incident, "_patch_owner_compromise_policy", lambda: True)
    monkeypatch.setattr(incident, "_install_prewarm_listener", lambda _bot: True)

    assert gateway.install_anti_nuke_gateway_runtime(bot) is True
    assert incident.install_anti_nuke_incident_runtime(bot) is True

    audit_callbacks = [
        callback
        for callback, event_name in bot.listeners
        if event_name == "on_audit_log_entry_create"
    ]
    overwrite_callbacks = [
        callback
        for callback, event_name in bot.listeners
        if event_name == "on_guild_channel_update"
    ]

    assert audit_callbacks == [incident._on_audit_log_entry_create]  # noqa: SLF001
    assert overwrite_callbacks == [guardian._on_guild_channel_update_fallback]  # noqa: SLF001
    assert gateway._on_audit_log_entry_create not in audit_callbacks  # noqa: SLF001
    assert incident.install_anti_nuke_incident_runtime(bot) is False


def test_sparse_role_create_uses_rest_recovery(monkeypatch) -> None:
    event = _entry("role_create")
    recovered_actor = SimpleNamespace(id=777)
    recovered_entry = SimpleNamespace(
        action=Action("role_create"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=SimpleNamespace(), after=SimpleNamespace(),
    )
    calls: list[str] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        assert names == ("role_create",)
        assert target_id == 123
        assert retries == 4
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry):
        calls.append("role_create")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(gateway, "_handle_role_create", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == ["role_create"]


def test_sparse_dangerous_role_update_uses_rest_recovery(monkeypatch) -> None:
    event = _entry("role_update")
    event.after.permissions = SimpleNamespace(administrator=True)
    event.before.permissions = SimpleNamespace(administrator=False)
    recovered_actor = SimpleNamespace(id=777)
    recovered_entry = SimpleNamespace(
        action=Action("role_update"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=event.before, after=event.after,
    )
    calls: list[int] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry, actor):
        calls.append(actor.id)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(gateway, "_handle_dangerous_role_update", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == [777]


def test_sparse_role_update_recovers_before_gateway_diff_classification(monkeypatch) -> None:
    event = _entry("role_update")
    recovered_actor = SimpleNamespace(id=777)
    recovered_before = SimpleNamespace(
        permissions=SimpleNamespace(administrator=False)
    )
    recovered_after = SimpleNamespace(
        permissions=SimpleNamespace(administrator=True)
    )
    recovered_entry = SimpleNamespace(
        action=Action("role_update"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=recovered_before, after=recovered_after,
    )
    calls: list[int] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        assert names == ("role_update",)
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry, actor):
        calls.append(actor.id)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(gateway, "_handle_dangerous_role_update", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == [777]


def test_sparse_member_role_update_uses_rest_recovery(monkeypatch) -> None:
    event = _entry("member_role_update")
    event.after.roles = [SimpleNamespace(id=999)]
    recovered_actor = SimpleNamespace(id=777)
    recovered_entry = SimpleNamespace(
        action=Action("member_role_update"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=event.before, after=event.after,
    )
    calls: list[int] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry, actor):
        calls.append(actor.id)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(gateway, "_handle_member_role_update", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == [777]


def test_sparse_timeout_update_uses_rest_recovery(monkeypatch) -> None:
    event = _entry("member_update")
    recovered_actor = SimpleNamespace(id=777)
    recovered_entry = SimpleNamespace(
        action=Action("member_update"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=event.before, after=event.after,
        recovered_timeout=True,
    )
    calls: list[int] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry, actor):
        calls.append(actor.id)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(
        gateway,
        "_timeout_extended",
        lambda found: bool(getattr(found, "recovered_timeout", False)),
    )
    monkeypatch.setattr(gateway, "_handle_member_timeout", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == [777]


def test_sparse_bot_add_uses_rest_recovery(monkeypatch) -> None:
    event = _entry("bot_add")
    recovered_actor = SimpleNamespace(id=777)
    recovered_entry = SimpleNamespace(
        action=Action("bot_add"), guild=event.guild, user=recovered_actor,
        target=SimpleNamespace(id=123), before=event.before, after=event.after,
    )
    calls: list[int] = []

    async def no_actor(_guild, _entry):
        return None

    async def claim(_guild, names, *, target_id=None, retries=3):
        return recovered_entry, recovered_actor

    async def handled(_guild, _entry, actor):
        calls.append(actor.id)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", no_actor)
    monkeypatch.setattr(guardian, "_claim_priority_entry", claim)
    monkeypatch.setattr(guardian, "_handle_bot_add", handled)
    monkeypatch.setattr(incident.asyncio, "sleep", no_sleep)

    asyncio.run(incident._on_audit_log_entry_create(event))  # noqa: SLF001
    assert calls == [777]
