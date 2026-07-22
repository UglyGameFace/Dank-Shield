from __future__ import annotations

import asyncio

from stoney_verify import security_stats
from stoney_verify.tickets_new import event_service


def test_single_claimed_ticket_counts_as_open_and_claimed() -> None:
    counts = security_stats._ticket_status_counts_from_rows(
        [
            {
                "status": "claimed",
                "claimed_by": "55",
                "assigned_to": "55",
            }
        ]
    )

    assert counts == {
        "open_tickets": 1,
        "claimed_tickets": 1,
        "closed_tickets": 0,
    }


def test_open_ticket_with_assignee_remains_in_active_total() -> None:
    counts = security_stats._ticket_status_counts_from_rows(
        [
            {
                "status": "open",
                "claimed_by": None,
                "assigned_to": "88",
            },
            {
                "status": "open",
                "claimed_by": "0",
                "assigned_to": None,
            },
        ]
    )

    assert counts == {
        "open_tickets": 2,
        "claimed_tickets": 1,
        "closed_tickets": 0,
    }


def test_lifecycle_ticket_event_forces_live_stats_refresh(monkeypatch) -> None:
    refreshes: list[int] = []

    async def fake_log_activity_event(**_kwargs) -> bool:
        return True

    async def fake_refresh(guild_id: int) -> bool:
        refreshes.append(guild_id)
        return True

    monkeypatch.setattr(event_service, "log_activity_event", fake_log_activity_event)
    monkeypatch.setattr(
        security_stats,
        "refresh_ticket_stats_for_guild_id",
        fake_refresh,
    )

    result = asyncio.run(
        event_service.log_ticket_event(
            guild_id=777,
            event_type="ticket_claimed",
            actor_user_id=55,
            actor_name="Staff",
            channel_id=999,
            ticket_row={
                "id": "123",
                "guild_id": "777",
                "status": "claimed",
                "claimed_by": "55",
                "assigned_to": "55",
                "channel_id": "999",
            },
        )
    )

    assert result is True
    assert refreshes == [777]


def test_non_lifecycle_ticket_event_does_not_refresh_stats(monkeypatch) -> None:
    refreshes: list[int] = []

    async def fake_log_activity_event(**_kwargs) -> bool:
        return True

    async def fake_refresh(guild_id: int) -> bool:
        refreshes.append(guild_id)
        return True

    monkeypatch.setattr(event_service, "log_activity_event", fake_log_activity_event)
    monkeypatch.setattr(
        security_stats,
        "refresh_ticket_stats_for_guild_id",
        fake_refresh,
    )

    result = asyncio.run(
        event_service.log_ticket_event(
            guild_id=777,
            event_type="ticket_note_added",
            actor_user_id=55,
            actor_name="Staff",
            channel_id=999,
            ticket_row={
                "id": "123",
                "guild_id": "777",
                "status": "claimed",
                "channel_id": "999",
            },
        )
    )

    assert result is True
    assert refreshes == []
