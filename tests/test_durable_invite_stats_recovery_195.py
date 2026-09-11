from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import durable_invite_stats


def _reset_state() -> None:
    durable_invite_stats._RECENT_EVENTS.clear()
    durable_invite_stats._PENDING.clear()
    durable_invite_stats._GUILD_LOCKS.clear()
    durable_invite_stats._BULK_RECOVERY_SEED.clear()


def _message(message_id: int, *, guild_id: int = 123, channel_id: int = 456):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
    )


def _decision(source: str):
    return SimpleNamespace(
        blocked_codes=["outside"],
        codes=["outside"],
        source=source,
        rule_id="invite_shield_external_or_blocked",
    )


def test_bulk_recovery_reuses_pass_local_durable_count_and_defers_compatibility_sync(monkeypatch) -> None:
    _reset_state()
    legacy_reads: list[int] = []
    seeds: list[int] = []
    syncs: list[tuple[int, int]] = []
    outbox_writes: list[bool] = []

    async def fake_legacy(guild_id: int) -> int:
        legacy_reads.append(guild_id)
        return 10

    def fake_write(event):
        seeds.append(event.seed_count)
        return durable_invite_stats.InviteStatWriteResult(
            event_hash=event.event_hash,
            blocked_count=event.blocked_count,
            invites_blocked=event.seed_count + event.blocked_count,
            applied=True,
            persisted=True,
            queued=False,
            backend="event_ledger_rpc",
        )

    async def fake_sync(guild_id: int, count: int) -> None:
        syncs.append((guild_id, count))

    async def fake_outbox() -> None:
        outbox_writes.append(True)

    monkeypatch.setattr(durable_invite_stats, "_legacy_invite_count", fake_legacy)
    monkeypatch.setattr(durable_invite_stats, "_write_event_sync", fake_write)
    monkeypatch.setattr(durable_invite_stats, "_sync_compatibility_count", fake_sync)
    monkeypatch.setattr(durable_invite_stats, "_persist_outbox_async", fake_outbox)

    first = asyncio.run(
        durable_invite_stats.record_deleted_invite_decision(
            _message(1),
            _decision("auto-reconcile:ready"),
        )
    )
    second = asyncio.run(
        durable_invite_stats.record_deleted_invite_decision(
            _message(2),
            _decision("auto-reconcile:ready"),
        )
    )

    assert first.invites_blocked == 11
    assert second.invites_blocked == 12
    assert legacy_reads == [123]
    assert seeds == [10, 11]
    assert syncs == []
    assert outbox_writes == []
    assert durable_invite_stats._BULK_RECOVERY_SEED[123] == 12


def test_live_event_keeps_immediate_compatibility_sync_and_does_not_seed_bulk_state(monkeypatch) -> None:
    _reset_state()
    syncs: list[tuple[int, int]] = []
    outbox_writes: list[bool] = []

    async def fake_legacy(_guild_id: int) -> int:
        return 20

    def fake_write(event):
        return durable_invite_stats.InviteStatWriteResult(
            event_hash=event.event_hash,
            blocked_count=1,
            invites_blocked=21,
            applied=True,
            persisted=True,
            queued=False,
            backend="event_ledger_rpc",
        )

    async def fake_sync(guild_id: int, count: int) -> None:
        syncs.append((guild_id, count))

    async def fake_outbox() -> None:
        outbox_writes.append(True)

    monkeypatch.setattr(durable_invite_stats, "_legacy_invite_count", fake_legacy)
    monkeypatch.setattr(durable_invite_stats, "_write_event_sync", fake_write)
    monkeypatch.setattr(durable_invite_stats, "_sync_compatibility_count", fake_sync)
    monkeypatch.setattr(durable_invite_stats, "_persist_outbox_async", fake_outbox)

    result = asyncio.run(
        durable_invite_stats.record_deleted_invite_decision(
            _message(3),
            _decision("globals_live_enforcer"),
        )
    )

    assert result.invites_blocked == 21
    assert syncs == [(123, 21)]
    assert outbox_writes == []
    assert 123 not in durable_invite_stats._BULK_RECOVERY_SEED


def test_success_only_rewrites_outbox_when_a_pending_entry_was_removed(monkeypatch) -> None:
    _reset_state()
    outbox_writes: list[bool] = []

    async def fake_legacy(_guild_id: int) -> int:
        return 4

    def fake_write(event):
        return durable_invite_stats.InviteStatWriteResult(
            event_hash=event.event_hash,
            blocked_count=1,
            invites_blocked=5,
            applied=True,
            persisted=True,
            queued=False,
            backend="event_ledger_rpc",
        )

    async def fake_sync(_guild_id: int, _count: int) -> None:
        return None

    async def fake_outbox() -> None:
        outbox_writes.append(True)

    monkeypatch.setattr(durable_invite_stats, "_legacy_invite_count", fake_legacy)
    monkeypatch.setattr(durable_invite_stats, "_write_event_sync", fake_write)
    monkeypatch.setattr(durable_invite_stats, "_sync_compatibility_count", fake_sync)
    monkeypatch.setattr(durable_invite_stats, "_persist_outbox_async", fake_outbox)

    message = _message(4)
    event_hash = durable_invite_stats.event_hash_for_message(message)
    durable_invite_stats._PENDING[event_hash] = durable_invite_stats.PendingInviteEvent(
        event_hash=event_hash,
        guild_id=123,
        blocked_count=1,
        seed_count=4,
        source="retry",
    )

    asyncio.run(
        durable_invite_stats.record_deleted_invite_decision(
            message,
            _decision("globals_live_enforcer"),
        )
    )

    assert event_hash not in durable_invite_stats._PENDING
    assert outbox_writes == [True]


def test_finish_bulk_recovery_flushes_once_and_clears_pass_local_seed(monkeypatch) -> None:
    _reset_state()
    durable_invite_stats._BULK_RECOVERY_SEED[777] = 88
    calls: list[int] = []

    async def fake_reconcile(guild_id: int):
        calls.append(guild_id)
        return 90

    monkeypatch.setattr(durable_invite_stats, "reconcile_guild", fake_reconcile)

    result = asyncio.run(durable_invite_stats.finish_bulk_recovery(777))

    assert result == 90
    assert calls == [777]
    assert 777 not in durable_invite_stats._BULK_RECOVERY_SEED


def test_finish_bulk_recovery_clears_pass_local_seed_when_flush_fails(monkeypatch) -> None:
    _reset_state()
    durable_invite_stats._BULK_RECOVERY_SEED[778] = 91

    async def fail_reconcile(_guild_id: int):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(durable_invite_stats, "reconcile_guild", fail_reconcile)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(durable_invite_stats.finish_bulk_recovery(778))

    assert 778 not in durable_invite_stats._BULK_RECOVERY_SEED


def test_finish_bulk_recovery_is_noop_without_active_pass(monkeypatch) -> None:
    _reset_state()
    calls: list[int] = []

    async def fake_reconcile(guild_id: int):
        calls.append(guild_id)
        return 1

    monkeypatch.setattr(durable_invite_stats, "reconcile_guild", fake_reconcile)

    assert asyncio.run(durable_invite_stats.finish_bulk_recovery(779)) is None
    assert calls == []
