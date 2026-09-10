from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import durable_invite_stats


def _reset_state() -> None:
    durable_invite_stats._RECENT_EVENTS.clear()
    durable_invite_stats._PENDING.clear()
    durable_invite_stats._GUILD_LOCKS.clear()
    durable_invite_stats._LAST_DURABLE_COUNT.clear()


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


def test_bulk_recovery_reuses_last_durable_count_and_defers_compatibility_sync(monkeypatch) -> None:
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
    assert durable_invite_stats._LAST_DURABLE_COUNT[123] == 12


def test_live_event_keeps_immediate_compatibility_sync(monkeypatch) -> None:
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
    assert durable_invite_stats._LAST_DURABLE_COUNT[123] == 21


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


def test_reconcile_guild_remembers_authoritative_durable_total(monkeypatch) -> None:
    _reset_state()
    syncs: list[tuple[int, int]] = []

    monkeypatch.setattr(durable_invite_stats, "_read_durable_count_sync", lambda _gid: 88)

    async def fake_sync(guild_id: int, count: int) -> None:
        syncs.append((guild_id, count))

    monkeypatch.setattr(durable_invite_stats, "_sync_compatibility_count", fake_sync)

    result = asyncio.run(durable_invite_stats.reconcile_guild(777))

    assert result == 88
    assert durable_invite_stats._LAST_DURABLE_COUNT[777] == 88
    assert syncs == [(777, 88)]
