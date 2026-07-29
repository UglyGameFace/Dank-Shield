from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.claim_policy import evaluate_ticket_action


ROOT = Path(__file__).resolve().parents[1]


def row(*, status: str = "open", owner: int = 100, claimant: int = 0) -> dict[str, object]:
    return {
        "status": status,
        "user_id": str(owner),
        "claimed_by": str(claimant) if claimant else None,
        "assigned_to": str(claimant) if claimant else None,
    }


def test_claim_is_only_staff_action_allowed_before_claim() -> None:
    ticket = row()
    assert evaluate_ticket_action(ticket, actor_id=200, action="claim").allowed is True

    for action in (
        "message",
        "close",
        "delete",
        "transfer",
        "unclaim",
        "priority",
        "note",
        "view_notes",
        "view_info",
        "macro",
        "transcript",
        "verification_review",
        "reopen",
        "access",
        "rename",
        "lock",
        "unlock",
    ):
        decision = evaluate_ticket_action(ticket, actor_id=200, action=action)
        assert decision.allowed is False, action
        assert decision.code == "claim_required", action


def test_current_claimant_is_authorized_and_other_admin_is_not() -> None:
    ticket = row(status="claimed", claimant=200)

    assert evaluate_ticket_action(ticket, actor_id=200, action="message").allowed is True
    assert evaluate_ticket_action(ticket, actor_id=200, action="close").allowed is True

    decision = evaluate_ticket_action(ticket, actor_id=999, action="close")
    assert decision.allowed is False
    assert decision.code == "claimant_required"
    assert "Transfer" in decision.message


def test_requester_can_only_cancel_an_unclaimed_ticket() -> None:
    allowed = evaluate_ticket_action(
        row(),
        actor_id=100,
        action="close",
        allow_requester_cancel=True,
    )
    assert allowed.allowed is True
    assert allowed.code == "requester_cancel_allowed"

    claimed = evaluate_ticket_action(
        row(status="claimed", claimant=200),
        actor_id=100,
        action="close",
        allow_requester_cancel=True,
    )
    assert claimed.allowed is False
    assert claimed.code == "requester_cancel_after_claim"

    delete = evaluate_ticket_action(
        row(status="closed", claimant=200),
        actor_id=100,
        action="delete",
    )
    assert delete.allowed is False
    assert delete.code == "requester_action_forbidden"


def test_delete_requires_the_claimant_and_a_separate_closed_state() -> None:
    open_decision = evaluate_ticket_action(
        row(status="claimed", claimant=200),
        actor_id=200,
        action="delete",
    )
    assert open_decision.allowed is False
    assert open_decision.code == "close_before_delete"

    closed_decision = evaluate_ticket_action(
        row(status="closed", claimant=200),
        actor_id=200,
        action="delete",
    )
    assert closed_decision.allowed is True


def test_internal_system_operations_are_explicit() -> None:
    denied = evaluate_ticket_action(row(), actor_id=0, action="close")
    assert denied.allowed is False
    assert denied.code == "actor_required"

    allowed = evaluate_ticket_action(
        row(),
        actor_id=0,
        action="close",
        system_action=True,
    )
    assert allowed.allowed is True
    assert allowed.code == "system_action"


def test_service_blocks_unclaimed_human_close_before_repository_write(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.tickets_new import service

    calls: list[str] = []

    async def fake_row(_channel_id: int):
        return row()

    async def fake_repo_close(**_kwargs):
        calls.append("repo_close")
        return True

    monkeypatch.setattr(service, "_ticket_row_for_channel_id", fake_row)
    monkeypatch.setattr(service, "repo_mark_ticket_closed", fake_repo_close)

    channel = SimpleNamespace(id=55, guild=SimpleNamespace(id=1), name="ticket-0001")
    actor = SimpleNamespace(id=200)

    assert asyncio.run(service.mark_ticket_closed(channel=channel, closed_by=actor)) is False
    assert calls == []


def test_static_claim_first_enforcement_covers_all_runtime_surfaces() -> None:
    policy = (ROOT / "stoney_verify/tickets_new/claim_policy.py").read_text(encoding="utf-8")
    service = (ROOT / "stoney_verify/tickets_new/service.py").read_text(encoding="utf-8")
    panel = (ROOT / "stoney_verify/tickets_new/panel.py").read_text(encoding="utf-8")
    macros = (ROOT / "stoney_verify/tickets_new/macros_service.py").read_text(encoding="utf-8")
    events = (ROOT / "stoney_verify/ticket_events.py").read_text(encoding="utf-8")
    transcripts = (ROOT / "stoney_verify/transcripts.py").read_text(encoding="utf-8")
    public_group = (ROOT / "stoney_verify/commands_ext/public_ticket_group.py").read_text(encoding="utf-8")

    assert "Claim is the only staff action allowed" in policy
    assert "async def authorize_ticket_action(" in service
    assert 'action="close"' in service
    assert 'action="delete"' in service
    assert 'action="transcript"' in service
    assert 'action="priority"' in service
    assert 'action="note"' in service
    assert 'action="reopen"' in service
    assert "actor_is_elevated" not in service

    assert "authorize_ticket_action" in panel
    assert 'label != "claim ticket"' in panel

    assert "authorize_ticket_action" in macros
    assert 'action="macro"' in macros

    assert "_enforce_claim_first_staff_message" in events
    assert "await message.delete" in events
    assert "lifecycle-bypass reverted" in events

    assert "authorize_ticket_action" in transcripts
    assert 'action="verification_review"' in transcripts
    assert "Close the ticket first, then use Delete" in transcripts

    assert "class ClaimFirstTicketGroup" in public_group
    assert "async def interaction_check" in public_group
    assert 'if command_name == "claim"' in public_group
    assert "authorize_ticket_action" in public_group
    assert '"info": "view_info"' in public_group
    assert '"owner": "view_info"' in public_group
    assert '"access": "view_info"' in public_group

    assert "send_messages=False" in service
