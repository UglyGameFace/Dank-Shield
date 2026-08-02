from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import durable_invite_stats, invite_policy_engine


def _clear_runtime_state(monkeypatch) -> None:
    durable_invite_stats._RECENT_EVENTS.clear()
    durable_invite_stats._PENDING.clear()
    durable_invite_stats._GUILD_LOCKS.clear()
    durable_invite_stats._REFRESH_TASKS.clear()
    durable_invite_stats._LAST_REFRESH_AT.clear()
    monkeypatch.setattr(durable_invite_stats, "_persist_outbox", lambda: None)
    monkeypatch.setattr(durable_invite_stats, "_ensure_retry_task", lambda: None)


def test_blocked_invite_count_uses_actual_unique_blocked_codes() -> None:
    decision = SimpleNamespace(
        blocked_codes=["Alpha", "beta", "ALPHA", ""],
        codes=["ignored-fallback"],
    )
    assert durable_invite_stats.blocked_invite_count(decision) == 2


def test_blocked_invite_count_has_safe_one_message_floor() -> None:
    assert durable_invite_stats.blocked_invite_count(SimpleNamespace(blocked_codes=[], codes=[])) == 1


def test_event_hash_is_stable_per_deleted_message_and_guild() -> None:
    def message(guild_id: int, channel_id: int, message_id: int):
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=guild_id),
            channel=SimpleNamespace(id=channel_id),
        )

    first = durable_invite_stats.event_hash_for_message(message(1, 2, 3))
    assert first == durable_invite_stats.event_hash_for_message(message(1, 2, 3))
    assert first != durable_invite_stats.event_hash_for_message(message(1, 2, 4))
    assert first != durable_invite_stats.event_hash_for_message(message(9, 2, 3))
    assert len(first) == 64


def test_rpc_write_sends_seed_actual_count_and_event_identity(monkeypatch) -> None:
    captured = {}

    class FakeRPC:
        def execute(self):
            return SimpleNamespace(data=[{"applied": True, "invites_blocked": 17}])

    class FakeSupabase:
        def rpc(self, name, params):
            captured["name"] = name
            captured["params"] = dict(params)
            return FakeRPC()

    monkeypatch.setattr(durable_invite_stats, "get_supabase", lambda: FakeSupabase())
    event = durable_invite_stats.PendingInviteEvent(
        event_hash="a" * 64,
        guild_id=123,
        blocked_count=3,
        seed_count=14,
        source="create:invite_shield",
    )

    result = durable_invite_stats._record_with_rpc_sync(event)

    assert captured == {
        "name": durable_invite_stats.RECORD_RPC,
        "params": {
            "p_event_hash": "a" * 64,
            "p_guild_id": "123",
            "p_blocked_count": 3,
            "p_seed_count": 14,
            "p_source": "create:invite_shield",
        },
    }
    assert result.applied is True
    assert result.invites_blocked == 17
    assert result.backend == "event_ledger_rpc"


def test_record_deleted_invite_syncs_visible_count_and_dedupes_in_process(monkeypatch) -> None:
    _clear_runtime_state(monkeypatch)
    writes = []
    syncs = []

    async def fake_legacy(_guild_id: int) -> int:
        return 10

    def fake_write(event):
        writes.append(event)
        return durable_invite_stats.InviteStatWriteResult(
            event_hash=event.event_hash,
            blocked_count=event.blocked_count,
            invites_blocked=12,
            applied=True,
            persisted=True,
            queued=False,
            backend="event_ledger_rpc",
        )

    async def fake_sync(guild_id: int, count: int) -> None:
        syncs.append((guild_id, count))

    monkeypatch.setattr(durable_invite_stats, "_legacy_invite_count", fake_legacy)
    monkeypatch.setattr(durable_invite_stats, "_write_event_sync", fake_write)
    monkeypatch.setattr(durable_invite_stats, "_sync_compatibility_count", fake_sync)

    message = SimpleNamespace(
        id=555,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=456),
    )
    decision = SimpleNamespace(
        blocked_codes=["one", "two"],
        codes=["one", "two"],
        source="create",
        rule_id="invite_shield_external_or_blocked",
    )

    first = asyncio.run(durable_invite_stats.record_deleted_invite_decision(message, decision))
    second = asyncio.run(durable_invite_stats.record_deleted_invite_decision(message, decision))

    assert first.applied is True
    assert first.blocked_count == 2
    assert second.applied is False
    assert second.backend == "recent_event_cache"
    assert len(writes) == 1
    assert writes[0].seed_count == 10
    assert syncs == [(123, 12)]


def test_failed_write_is_queued_and_never_silently_claimed_persisted(monkeypatch) -> None:
    _clear_runtime_state(monkeypatch)
    queued = []

    async def fake_legacy(_guild_id: int) -> int:
        return 7

    def fail_write(_event):
        raise RuntimeError("database unavailable")

    def fake_queue(event):
        queued.append(event)
        durable_invite_stats._PENDING[event.event_hash] = event

    monkeypatch.setattr(durable_invite_stats, "_legacy_invite_count", fake_legacy)
    monkeypatch.setattr(durable_invite_stats, "_write_event_sync", fail_write)
    monkeypatch.setattr(durable_invite_stats, "_queue_pending", fake_queue)

    message = SimpleNamespace(
        id=8,
        guild=SimpleNamespace(id=9),
        channel=SimpleNamespace(id=10),
    )
    decision = SimpleNamespace(
        blocked_codes=["outside"],
        codes=["outside"],
        source="fallback-sweep",
        rule_id="invite_shield_external_or_blocked",
    )

    result = asyncio.run(durable_invite_stats.record_deleted_invite_decision(message, decision))

    assert result.persisted is False
    assert result.queued is True
    assert result.backend == "retry_outbox"
    assert result.invites_blocked == 7
    assert len(queued) == 1
    assert queued[0].blocked_count == 1


def test_compatibility_sync_preserves_other_stats_and_raises_invite_floor(monkeypatch) -> None:
    writes = []
    refreshes = []

    async def fake_get(_guild_id: int, refresh: bool = False):
        assert refresh is True
        return {
            durable_invite_stats.COUNTS_KEY: {
                "spam_blocked": 40,
                "invites_blocked": 2,
                "timeouts_issued": 3,
                "quarantines": 4,
            }
        }

    async def fake_upsert(guild_id: int, patch):
        writes.append((guild_id, patch))
        return patch

    monkeypatch.setattr(durable_invite_stats, "get_guild_config", fake_get)
    monkeypatch.setattr(durable_invite_stats, "upsert_guild_config", fake_upsert)
    monkeypatch.setattr(
        durable_invite_stats,
        "_schedule_display_refresh",
        lambda guild_id: refreshes.append(guild_id),
    )

    asyncio.run(durable_invite_stats._sync_compatibility_count(99, 12))

    assert writes == [
        (
            99,
            {
                durable_invite_stats.COUNTS_KEY: {
                    "spam_blocked": 40,
                    "invites_blocked": 12,
                    "timeouts_issued": 3,
                    "quarantines": 4,
                }
            },
        )
    ]
    assert refreshes == [99]


def test_central_delete_records_one_durable_event_with_full_decision(monkeypatch) -> None:
    calls = []

    async def fake_record(message, decision):
        calls.append((message, decision))
        return durable_invite_stats.InviteStatWriteResult(
            event_hash="b" * 64,
            blocked_count=2,
            invites_blocked=22,
            applied=True,
            persisted=True,
            queued=False,
            backend="event_ledger_rpc",
        )

    monkeypatch.setattr(
        invite_policy_engine.durable_invite_stats,
        "record_deleted_invite_decision",
        fake_record,
    )

    class FakeMessage:
        id = 777
        guild = SimpleNamespace(id=123)
        channel = SimpleNamespace(id=456)

        def __init__(self):
            self.deleted = 0

        async def delete(self):
            self.deleted += 1

    message = FakeMessage()
    decision = invite_policy_engine.InviteDecision(
        action="delete",
        guild_id=123,
        channel_id=456,
        blocked_codes=["external-a", "external-b"],
        codes=["external-a", "external-b"],
    )

    assert asyncio.run(invite_policy_engine.delete_message_if_allowed(message, decision)) is True
    assert message.deleted == 1
    assert decision.delete_succeeded is True
    assert calls == [(message, decision)]


def test_central_delete_remains_successful_when_stats_are_queued(monkeypatch) -> None:
    async def fake_record(_message, _decision):
        return durable_invite_stats.InviteStatWriteResult(
            event_hash="c" * 64,
            blocked_count=1,
            invites_blocked=0,
            applied=False,
            persisted=False,
            queued=True,
            backend="retry_outbox",
        )

    monkeypatch.setattr(
        invite_policy_engine.durable_invite_stats,
        "record_deleted_invite_decision",
        fake_record,
    )

    message = SimpleNamespace(
        id=44,
        guild=SimpleNamespace(id=55),
        channel=SimpleNamespace(id=66),
        delete=lambda: None,
    )

    async def delete():
        return None

    message.delete = delete
    decision = invite_policy_engine.InviteDecision(
        action="delete",
        guild_id=55,
        blocked_codes=["external"],
        codes=["external"],
    )

    assert asyncio.run(invite_policy_engine.delete_message_if_allowed(message, decision)) is True
    assert decision.delete_succeeded is True
