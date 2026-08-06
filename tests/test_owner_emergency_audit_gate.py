from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.startup_guards import owner_emergency_audit_gate as audit_gate
from stoney_verify.startup_guards import owner_emergency_override_guard as ui_guard
from stoney_verify.tickets_new.owner_emergency_override import OwnerEmergencyResult


def _ticket_row() -> dict[str, object]:
    return {
        "guild_id": "1",
        "status": "claimed",
        "user_id": "100",
        "claimed_by": "200",
        "assigned_to": "200",
    }


def _channel() -> SimpleNamespace:
    return SimpleNamespace(
        id=55,
        name="ticket-0055",
        guild=SimpleNamespace(id=1, owner_id=999, owner=None),
    )


def _owner() -> SimpleNamespace:
    return SimpleNamespace(id=999, bot=False)


def test_confirmed_override_stops_before_mutation_when_audit_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_row(_channel_id: int):
        return _ticket_row()

    async def audit_unavailable(**_kwargs):
        calls.append("audit")
        return False

    monkeypatch.setattr(audit_gate, "_ticket_row", fake_row)
    monkeypatch.setattr(audit_gate, "_write_audit_event", audit_unavailable)

    result = asyncio.run(
        ui_guard.execute_owner_emergency_override(
            channel=_channel(),
            actor=_owner(),
            action="transfer",
            reason="Claimant is unavailable during an urgent incident.",
            target_member=SimpleNamespace(id=300),
        )
    )

    assert isinstance(result, OwnerEmergencyResult)
    assert result.ok is False
    assert result.code == "audit_unavailable"
    assert result.metadata["mutation_started"] is False
    assert calls == ["audit"]


def test_audit_writer_records_owner_reason_previous_claimant_and_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_log_ticket_event(**kwargs):
        captured.append(kwargs)
        return True

    from stoney_verify.tickets_new import event_service

    monkeypatch.setattr(event_service, "log_ticket_event", fake_log_ticket_event)

    ok = asyncio.run(
        audit_gate._write_audit_event(
            phase="authorized",
            channel=_channel(),
            actor=_owner(),
            action="close",
            reason="Urgent owner intervention is required.",
            row=_ticket_row(),
        )
    )

    assert ok is True
    assert captured
    event = captured[0]
    assert event["event_type"] == "ticket_owner_emergency_override_authorized"
    assert event["reason"] == "Urgent owner intervention is required."
    metadata = event["metadata"]
    assert metadata["override_phase"] == "authorized"
    assert metadata["override_owner_id"] == "999"
    assert metadata["previous_claimed_by"] == "200"


def test_failed_result_event_contains_outcome_without_replacing_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_log_ticket_event(**kwargs):
        captured.append(kwargs)
        return True

    from stoney_verify.tickets_new import event_service

    monkeypatch.setattr(event_service, "log_ticket_event", fake_log_ticket_event)

    result = OwnerEmergencyResult(
        False,
        "delete",
        "transcript_required",
        "A transcript could not be verified.",
        {"mutation_started": False},
    )
    ok = asyncio.run(
        audit_gate._write_audit_event(
            phase="failed",
            channel=_channel(),
            actor=_owner(),
            action="delete",
            reason="Remove prohibited content after preserving evidence.",
            row=_ticket_row(),
            result=result,
        )
    )

    assert ok is True
    assert captured[0]["event_type"] == "ticket_owner_emergency_override_failed"
    metadata = captured[0]["metadata"]
    assert metadata["override_success"] is False
    assert metadata["override_result_code"] == "transcript_required"
    assert metadata["mutation_started"] is False
