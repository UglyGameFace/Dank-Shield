from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.startup_guards import owner_emergency_close_bridge
from stoney_verify.tickets_new import service
from stoney_verify.tickets_new.claim_policy import ticket_has_transcript


def test_owner_reason_prefix_without_confirmed_ui_context_cannot_bypass_claim(
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
    owner = SimpleNamespace(id=999, bot=False)

    assert owner_emergency_close_bridge._confirmed_close_matches(channel, owner) is False
    result = asyncio.run(
        service.mark_ticket_closed(
            channel=channel,
            closed_by=owner,
            reason="Owner emergency override: typed into an ordinary command",
        )
    )

    assert result is False
    assert calls == []


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
