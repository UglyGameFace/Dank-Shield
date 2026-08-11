from __future__ import annotations

import asyncio

import discord

from stoney_verify.members_new.join_truth_integrity import (
    approval_context,
    merge_join_context,
    normalize_join_context,
)
from stoney_verify.operation_queue import GuildOperationQueue, OperationJob
from stoney_verify.permission_repair import (
    _apply_missing_to_overwrite,
    approved_public_permissions,
)


def test_approval_does_not_overwrite_confirmed_invite_truth() -> None:
    existing = {
        "entry_method": "invite",
        "join_source": "invite",
        "invite_code": "GOODCODE",
        "invited_by": "123",
        "invited_by_name": "Inviter",
        "entry_truth_quality": "confirmed",
        "entry_confidence": 95,
        "entry_quality_reason": "Specific invite delta.",
    }
    approval = approval_context(
        approved_by="456",
        approved_by_name="Staff",
        verification_source="ticket_staff_approval",
        approval_reason="Approved",
        source_ticket_id="789",
    )
    merged = merge_join_context(existing, approval, incoming_is_approval=True)

    assert merged["entry_method"] == "invite"
    assert merged["join_source"] == "invite"
    assert merged["invite_code"] == "GOODCODE"
    assert merged["invited_by"] == "123"
    assert merged["entry_truth_quality"] == "confirmed"
    assert merged["approved_by"] == "456"
    assert merged["verification_source"] == "ticket_staff_approval"
    assert merged["approval_truth_quality"] == "confirmed"


def test_conflicting_invite_evidence_is_not_silently_trusted() -> None:
    existing = {
        "entry_method": "invite",
        "join_source": "invite",
        "invite_code": "FIRST",
        "invited_by": "111",
        "entry_truth_quality": "confirmed",
        "entry_confidence": 95,
    }
    incoming = {
        "entry_method": "invite",
        "join_source": "invite",
        "invite_code": "SECOND",
        "invited_by": "222",
        "entry_truth_quality": "confirmed",
        "entry_confidence": 99,
    }
    merged = merge_join_context(existing, incoming)

    assert merged["entry_conflict"] is True
    assert merged["entry_truth_quality"] == "partial"
    assert int(merged["entry_confidence"]) <= 70
    assert "Conflicting join evidence" in str(merged["entry_quality_reason"])


def test_join_aliases_normalize_to_one_canonical_shape() -> None:
    normalized = normalize_join_context(
        {
            "inviter_id": "321",
            "inviter_name": "Old Alias",
            "entry_source": "invite",
            "entry_method": "invite",
            "verification_ticket_id": "654",
        }
    )
    assert normalized["invited_by"] == "321"
    assert normalized["invited_by_name"] == "Old Alias"
    assert normalized["join_source"] == "invite"
    assert normalized["source_ticket_id"] == "654"


def test_public_reauthorization_permissions_never_include_administrator() -> None:
    perms = approved_public_permissions()
    assert perms.administrator is False
    assert perms.manage_channels is True
    assert perms.manage_roles is True
    assert perms.kick_members is True
    assert perms.send_messages is True
    assert perms.read_message_history is True


def test_safe_permission_repair_preserves_explicit_bot_deny_until_confirmed() -> None:
    overwrite = discord.PermissionOverwrite(send_messages=False, embed_links=None)
    repaired, changed, preserved = _apply_missing_to_overwrite(
        overwrite,
        ["send_messages", "embed_links"],
        clear_explicit_denies=False,
    )
    assert repaired.send_messages is False
    assert repaired.embed_links is True
    assert "send_messages" in preserved
    assert "embed_links" in changed

    repaired, changed, preserved = _apply_missing_to_overwrite(
        repaired,
        ["send_messages"],
        clear_explicit_denies=True,
    )
    assert repaired.send_messages is True
    assert "send_messages" in changed
    assert not preserved


def test_persisted_operation_job_can_reattach_after_restart() -> None:
    row = {
        "id": "job-1",
        "guild_id": "42",
        "actor_id": "7",
        "operation_type": "channel_builder_apply_plan",
        "risk_level": "dangerous",
        "source": "dashboard",
        "idempotency_key": "stable-key",
        "payload_hash": "abc",
        "status": "succeeded",
        "progress_current": 2,
        "progress_total": 2,
        "result_json": {
            "concurrency_class": "channel_mutation",
            "concurrency_key": "42:channel_mutation:channel_builder",
            "rollback_plan": [{"action": "delete_created_channel", "channel_id": "99"}],
        },
        "created_at": "2026-08-11T00:00:00+00:00",
        "started_at": "2026-08-11T00:00:01+00:00",
        "finished_at": "2026-08-11T00:00:02+00:00",
    }
    job = OperationJob.from_persisted(row)
    public = job.public_payload()
    assert public["id"] == "job-1"
    assert public["status"] == "succeeded"
    assert public["result"]["rollback_plan"][0]["channel_id"] == "99"


def test_operation_queue_blocks_duplicate_exclusive_submission() -> None:
    async def scenario() -> None:
        manager = GuildOperationQueue()

        async def none_row(*args, **kwargs):
            return None

        async def no_rows(*args, **kwargs):
            return []

        async def noop(*args, **kwargs):
            return None

        manager._persistence.fetch_duplicate = none_row  # type: ignore[assignment]
        manager._persistence.reconcile_stale = no_rows  # type: ignore[assignment]
        manager._persistence.upsert_job = noop  # type: ignore[assignment]
        manager._persistence.update_job = noop  # type: ignore[assignment]
        manager._persistence.audit_job = noop  # type: ignore[assignment]

        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return {"ok": True}

        first_state, first_result, first_job = await manager.run_exclusive(
            guild_id=42,
            actor_id=7,
            operation_type="ticket_close",
            payload={"channel_id": 99},
            idempotency_key="same-operation",
            concurrency_class="ticket_channel_mutation",
            concurrency_key="channel:99",
            factory=factory,
        )
        second_state, second_result, second_job = await manager.run_exclusive(
            guild_id=42,
            actor_id=7,
            operation_type="ticket_close",
            payload={"channel_id": 99},
            idempotency_key="same-operation",
            concurrency_class="ticket_channel_mutation",
            concurrency_key="channel:99",
            factory=factory,
        )

        assert first_state == "succeeded"
        assert first_result == {"ok": True}
        assert first_job is not None
        assert second_state == "duplicate"
        assert second_result is None
        assert second_job is not None
        assert second_job.id == first_job.id
        assert calls == 1

    asyncio.run(scenario())
