from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian


def _run(coro):
    return asyncio.run(coro)


def test_generic_channel_update_is_not_destructive_antinuke_evidence(monkeypatch) -> None:
    processed: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_process(*args, **kwargs):
        processed.append((args, kwargs))

    monkeypatch.setattr(guardian, "_process", fake_process)

    actor = SimpleNamespace(id=99)
    entry = SimpleNamespace(
        id=1,
        guild=SimpleNamespace(id=10),
        action=SimpleNamespace(name="channel_update"),
        user=actor,
        user_id=actor.id,
        target=SimpleNamespace(id=20, name="renamed-channel"),
        before=SimpleNamespace(name="old-name"),
        after=SimpleNamespace(name="renamed-channel"),
    )

    _run(guardian._on_audit_log_entry_create(entry))  # noqa: SLF001

    assert "channel_update" not in guardian._ACTIONS  # noqa: SLF001
    assert processed == []


def test_explicit_channel_overwrite_update_remains_guarded(monkeypatch) -> None:
    processed: list[tuple[str, tuple[str, str, str, object]]] = []
    rollback_actions: list[str] = []
    actor = SimpleNamespace(id=99)

    async def fake_resolve(_guild, _entry):
        return actor

    async def fake_rollback(_guild, _entry, _actor, action_name):
        rollback_actions.append(action_name)
        return "restored"

    async def fake_process(_guild, _entry, _actor, action_name, spec):
        processed.append((action_name, spec))

    monkeypatch.setattr(guardian, "_resolve_actor", fake_resolve)
    monkeypatch.setattr(guardian, "_rollback_untrusted_overwrite", fake_rollback)
    monkeypatch.setattr(guardian, "_process", fake_process)
    monkeypatch.setattr(guardian.anti_nuke, "_consume_audit_entry", lambda _entry: False)

    entry = SimpleNamespace(
        id=2,
        guild=SimpleNamespace(id=10),
        action=SimpleNamespace(name="overwrite_update"),
        user=actor,
        user_id=actor.id,
        target=SimpleNamespace(id=20, name="staff"),
        before=SimpleNamespace(),
        after=SimpleNamespace(),
    )

    _run(guardian._on_audit_log_entry_create(entry))  # noqa: SLF001

    assert rollback_actions == ["overwrite_update"]
    assert processed == [
        ("overwrite_update", guardian._ACTIONS["overwrite_update"])  # noqa: SLF001
    ]


def test_real_overwrite_paths_keep_shared_channel_update_counter() -> None:
    for action_name in ("overwrite_create", "overwrite_update", "overwrite_delete"):
        assert guardian._ACTIONS[action_name][2] == "channel_update"  # noqa: SLF001

    assert "channel_update" in anti_nuke._SLOW_BURN_ACTIONS  # noqa: SLF001
