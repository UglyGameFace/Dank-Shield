from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.startup_guards import owner_emergency_close_bridge
from stoney_verify.tickets_new import service
from stoney_verify.tickets_new.claim_policy import ticket_has_transcript


def test_reason_prefix_without_confirmed_ui_context_cannot_grant_nonowner_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    ticket = {
        "guild_id": "1",
        "status": "claimed",
        "user_id": "100",
        "claimed_by": "200",
        "assigned_to": "200",
    }

    async def fake_row(_channel_id: int):
        return dict(ticket)

    async def fake_repo_close(**_kwargs):
        calls.append("repo_close")
        return True

    monkeypatch.setattr(service, "_ticket_row_for_channel_id", fake_row)
    monkeypatch.setattr(service, "repo_mark_ticket_closed", fake_repo_close)

    channel = SimpleNamespace(
        id=55,
        name="ticket-0055",
        guild=SimpleNamespace(id=1, owner_id=999, owner=None),
    )
    nonowner = SimpleNamespace(id=998, bot=False)

    assert owner_emergency_close_bridge._confirmed_close_matches(channel, nonowner) is False
    result = asyncio.run(
        service.mark_ticket_closed(
            channel=channel,
            closed_by=nonowner,
            reason="Owner emergency override: typed into an ordinary command",
        )
    )

    assert result is False
    assert calls == []


def test_confirmed_close_authorizer_keeps_real_owner_as_actor() -> None:
    original_calls: list[dict[str, object]] = []
    ticket = {
        "guild_id": "1",
        "status": "claimed",
        "user_id": "100",
        "claimed_by": "200",
        "assigned_to": "200",
    }

    async def original_authorizer(**kwargs):
        original_calls.append(kwargs)
        return SimpleNamespace(allowed=False, code="claimant_required")

    async def fake_row(_channel_id: int):
        return dict(ticket)

    fake_service = SimpleNamespace(
        authorize_ticket_action=original_authorizer,
        _ticket_row_for_channel_id=fake_row,
    )
    assert owner_emergency_close_bridge._patch_close_authorizer(fake_service) is True

    actor = SimpleNamespace(id=999, bot=False)
    outside = asyncio.run(
        fake_service.authorize_ticket_action(
            channel_id=55,
            actor=actor,
            action="close",
            row=ticket,
        )
    )
    assert outside.allowed is False
    assert outside.code == "claimant_required"
    assert len(original_calls) == 1
    assert original_calls[0]["actor"] is actor

    token = owner_emergency_close_bridge._CONFIRMED_CLOSE.set(
        (55, 999, "Server Owner")
    )
    try:
        confirmed = asyncio.run(
            fake_service.authorize_ticket_action(
                channel_id=55,
                actor=actor,
                action="close",
                row=ticket,
            )
        )
    finally:
        owner_emergency_close_bridge._CONFIRMED_CLOSE.reset(token)

    assert confirmed.allowed is True
    assert confirmed.code == "owner_emergency_close_allowed"
    assert confirmed.actor_id == 999
    assert confirmed.claimed_by_id == 200
    assert len(original_calls) == 1


def test_confirmed_close_reuses_one_canonical_owner_attributed_event() -> None:
    captured: list[dict[str, object]] = []

    async def original_logger(**kwargs):
        captured.append(kwargs)
        return True

    fake_service = SimpleNamespace(log_ticket_closed=original_logger)
    assert owner_emergency_close_bridge._patch_close_logger(fake_service) is True

    token = owner_emergency_close_bridge._CONFIRMED_CLOSE.set(
        (55, 999, "Server Owner")
    )
    try:
        result = asyncio.run(
            fake_service.log_ticket_closed(
                guild_id=1,
                actor_user_id=None,
                actor_name=None,
                channel_id=55,
                reason="Owner emergency override: claimant vanished",
                metadata={"moved_to_archive": True},
            )
        )
    finally:
        owner_emergency_close_bridge._CONFIRMED_CLOSE.reset(token)

    assert result is True
    assert len(captured) == 1
    event = captured[0]
    assert event["actor_user_id"] == 999
    assert event["actor_name"] == "Server Owner"
    assert event["reason"] == "claimant vanished"
    assert event["source"] == "tickets_new_owner_emergency_close"
    assert event["metadata"]["owner_emergency_override"] is True
    assert event["metadata"]["moved_to_archive"] is True


def test_safe_delete_requires_a_url_or_complete_discord_message_location() -> None:
    assert ticket_has_transcript({"transcript_url": "https://discord.test/transcript"}) is True
    assert ticket_has_transcript(
        {
            "transcript_message_id": "123456789012345678",
            "transcript_channel_id": "223456789012345678",
        }
    ) is True

    assert ticket_has_transcript({"transcript_channel_id": "223456789012345678"}) is False
    assert ticket_has_transcript({"transcript_message_id": "123456789012345678"}) is False
    assert ticket_has_transcript({"transcript_url": "   "}) is False
