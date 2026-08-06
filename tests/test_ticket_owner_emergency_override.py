from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.claim_policy import evaluate_ticket_action
from stoney_verify.tickets_new import owner_emergency_override as emergency


def ticket_row(
    *,
    status: str = "claimed",
    claimant: int = 200,
    transcript: bool = False,
) -> dict[str, object]:
    return {
        "guild_id": "1",
        "status": status,
        "user_id": "100",
        "claimed_by": str(claimant) if claimant else None,
        "assigned_to": str(claimant) if claimant else None,
        "transcript_url": "https://discord.test/transcript" if transcript else None,
    }


def test_explicit_owner_emergency_actions_are_owner_only() -> None:
    row = ticket_row()

    for action in (
        "owner_emergency_transfer",
        "owner_emergency_unclaim",
        "owner_emergency_close",
    ):
        allowed = evaluate_ticket_action(
            row,
            actor_id=999,
            action=action,
            guild_owner_id=999,
        )
        assert allowed.allowed is True, action

        denied = evaluate_ticket_action(
            row,
            actor_id=555,
            action=action,
            guild_owner_id=999,
        )
        assert denied.allowed is False, action
        assert denied.code == "guild_owner_required", action


def test_normal_owner_actions_stay_claimant_only() -> None:
    row = ticket_row()

    for action in ("transfer", "unclaim", "delete", "note", "macro", "reopen"):
        denied = evaluate_ticket_action(
            row,
            actor_id=999,
            action=action,
            guild_owner_id=999,
        )
        assert denied.allowed is False, action
        assert denied.code == "claimant_required", action


def test_owner_safe_delete_requires_closed_ticket_and_transcript() -> None:
    open_prepare = evaluate_ticket_action(
        ticket_row(status="claimed"),
        actor_id=999,
        action="owner_emergency_delete_prepare",
        guild_owner_id=999,
    )
    assert open_prepare.allowed is False
    assert open_prepare.code == "owner_emergency_delete_requires_closed"

    closed_prepare = evaluate_ticket_action(
        ticket_row(status="closed"),
        actor_id=999,
        action="owner_emergency_delete_prepare",
        guild_owner_id=999,
    )
    assert closed_prepare.allowed is True

    no_transcript = evaluate_ticket_action(
        ticket_row(status="closed"),
        actor_id=999,
        action="owner_emergency_delete",
        guild_owner_id=999,
    )
    assert no_transcript.allowed is False
    assert no_transcript.code == "owner_emergency_delete_requires_transcript"

    safe_delete = evaluate_ticket_action(
        ticket_row(status="closed", transcript=True),
        actor_id=999,
        action="owner_emergency_delete",
        guild_owner_id=999,
    )
    assert safe_delete.allowed is True
    assert safe_delete.code == "owner_emergency_delete_allowed"


def test_available_actions_follow_ticket_lifecycle() -> None:
    assert emergency.available_owner_emergency_actions(ticket_row(status="open", claimant=0)) == (
        "transfer",
        "close",
    )
    assert emergency.available_owner_emergency_actions(ticket_row(status="claimed")) == (
        "transfer",
        "unclaim",
        "close",
    )
    assert emergency.available_owner_emergency_actions(ticket_row(status="closed")) == ("delete",)
    assert emergency.available_owner_emergency_actions(ticket_row(status="deleted")) == ()


class FakeChannel:
    def __init__(self, state: dict[str, object]):
        self.id = 55
        self.name = "ticket-0055"
        self.guild = SimpleNamespace(id=1, owner_id=999, owner=None)
        self.state = state
        self.deleted = False
        self.sent: list[str] = []

    async def send(self, content: str, **_kwargs) -> None:
        self.sent.append(content)

    async def delete(self, **_kwargs) -> None:
        self.deleted = True


def owner() -> SimpleNamespace:
    return SimpleNamespace(id=999, bot=False, mention="<@999>")


@pytest.mark.parametrize("action", ["transfer", "unclaim"])
def test_owner_emergency_assignment_changes_emit_one_canonical_event(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    state = ticket_row(status="claimed", claimant=200)
    channel = FakeChannel(state)
    actor = owner()
    target = SimpleNamespace(
        id=300,
        bot=False,
        mention="<@300>",
        guild=channel.guild,
    )
    events: list[dict[str, object]] = []

    async def fake_row(_channel_id: int):
        return dict(state)

    async def fake_transfer(**_kwargs):
        state["status"] = "claimed"
        state["claimed_by"] = "300"
        state["assigned_to"] = "300"
        return True

    async def fake_unclaim(**_kwargs):
        state["status"] = "open"
        state["claimed_by"] = None
        state["assigned_to"] = None
        return True

    async def fake_event(**kwargs):
        events.append(kwargs)
        return True

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(emergency, "_row", fake_row)
    monkeypatch.setattr(emergency, "repo_transfer", fake_transfer)
    monkeypatch.setattr(emergency, "repo_unclaim", fake_unclaim)
    monkeypatch.setattr(emergency, "_target_is_staff", lambda _target: True)
    monkeypatch.setattr(emergency, "_sync_claimant_permissions", noop)
    monkeypatch.setattr(emergency, "_log_assignment_event", fake_event)
    monkeypatch.setattr(emergency, "_send", noop)

    result = asyncio.run(
        emergency.execute_owner_emergency_override(
            channel=channel,
            actor=actor,
            action=action,
            reason="Claimant is unavailable during an urgent incident.",
            target_member=target if action == "transfer" else None,
        )
    )

    assert result.ok is True
    assert len(events) == 1
    assert events[0]["previous_claimed_by"] == 200
    assert events[0]["reason"] == "Claimant is unavailable during an urgent incident."
    if action == "transfer":
        assert state["claimed_by"] == "300"
        assert events[0]["target"] is target
    else:
        assert state["claimed_by"] is None


def test_safe_delete_stops_when_transcript_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ticket_row(status="closed", claimant=200)
    channel = FakeChannel(state)
    actor = owner()

    async def fake_row(_channel_id: int):
        return dict(state)

    async def fake_transcript(*_args, **_kwargs):
        return False, dict(state), {"transcript_error": "missing transcript channel"}

    monkeypatch.setattr(emergency, "_row", fake_row)
    monkeypatch.setattr(emergency, "_ensure_transcript", fake_transcript)

    result = asyncio.run(
        emergency.execute_owner_emergency_override(
            channel=channel,
            actor=actor,
            action="delete",
            reason="Ticket contains prohibited content and must be removed safely.",
        )
    )

    assert result.ok is False
    assert result.code == "transcript_required"
    assert result.metadata["mutation_started"] is False
    assert result.metadata["previous_claimed_by"] == 200
    assert channel.deleted is False


def test_safe_delete_removes_channel_then_marks_database_and_logs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ticket_row(status="closed", claimant=200, transcript=True)
    channel = FakeChannel(state)
    actor = owner()
    delete_events: list[dict[str, object]] = []

    async def fake_row(_channel_id: int):
        return dict(state)

    async def fake_transcript(*_args, **_kwargs):
        return True, dict(state), {"transcript_already_existed": True}

    async def fake_mark_deleted(**_kwargs):
        state["status"] = "deleted"
        return True

    async def fake_delete_event(**kwargs):
        delete_events.append(kwargs)
        return True

    monkeypatch.setattr(emergency, "_row", fake_row)
    monkeypatch.setattr(emergency, "_ensure_transcript", fake_transcript)
    monkeypatch.setattr(emergency, "repo_mark_deleted", fake_mark_deleted)
    monkeypatch.setattr(emergency, "_log_delete_event", fake_delete_event)

    result = asyncio.run(
        emergency.execute_owner_emergency_override(
            channel=channel,
            actor=actor,
            action="delete",
            reason="Closed emergency ticket must be removed after preserving evidence.",
        )
    )

    assert result.ok is True
    assert channel.deleted is True
    assert state["status"] == "deleted"
    assert len(delete_events) == 1
    assert delete_events[0]["previous_claimed_by"] == 200
    assert delete_events[0]["transcript_metadata"]["transcript_already_existed"] is True
