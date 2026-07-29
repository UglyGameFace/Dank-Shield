from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import security_stats, ticket_events
from stoney_verify import spam_guard


class FakeTicketChannel:
    def __init__(self, name: str, *, topic: str = "", category_name: str = "ACTIVE TICKETS") -> None:
        self.name = name
        self.topic = topic
        self.category = SimpleNamespace(name=category_name)


def test_claimed_subset_can_never_exceed_open_total() -> None:
    assert security_stats._normalize_ticket_status_counts(
        {
            "open_tickets": 0,
            "claimed_tickets": 1,
            "closed_tickets": 4,
        }
    ) == {
        "open_tickets": 1,
        "claimed_tickets": 1,
        "closed_tickets": 4,
    }


def test_live_ticket_channel_floor_prevents_false_open_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = SimpleNamespace(
        id=777,
        member_count=25,
        text_channels=[
            FakeTicketChannel(
                "custom-ticket-name",
                topic="owner_id=55;category=support;ticket_number=42",
            )
        ],
    )

    async def fake_spam(_guild_id: int):
        return True

    async def fake_tickets(_guild_id: int):
        return {
            "open_tickets": 0,
            "claimed_tickets": 0,
            "closed_tickets": 12,
        }

    monkeypatch.setattr(security_stats, "_spam_guard_enabled", fake_spam)
    monkeypatch.setattr(security_stats, "_ticket_status_counts", fake_tickets)

    names = asyncio.run(security_stats._display_names_for_guild(guild, counts={}))

    assert names["open_tickets"] == "🎫 Open Tickets: 1"
    assert names["closed_tickets"] == "✅ Closed Tickets: 12"


def test_ticket_query_paginates_and_falls_back_for_schema_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_stats, "_TICKET_STATS_SELECT_COLUMNS", None)
    monkeypatch.setattr(security_stats, "_TICKET_STATS_PAGE_SIZE", 2)

    rows = [
        {"status": "claimed", "claimed_by": "55"},
        {"status": "open", "claimed_by": None},
        {"status": "closed", "claimed_by": None},
    ]
    selected: list[str] = []
    ranges: list[tuple[int, int]] = []

    class FakeQuery:
        def __init__(self) -> None:
            self.columns = ""
            self.start = 0
            self.end = 0

        def select(self, columns: str):
            self.columns = columns
            selected.append(columns)
            return self

        def eq(self, key: str, value: str):
            assert key == "guild_id"
            assert value == "777"
            return self

        def range(self, start: int, end: int):
            self.start = start
            self.end = end
            ranges.append((start, end))
            return self

        def execute(self):
            if self.columns == "status,claimed_by,assigned_to":
                raise RuntimeError("PGRST204: assigned_to column does not exist")
            return SimpleNamespace(data=rows[self.start : self.end + 1])

    class FakeSupabase:
        def table(self, name: str):
            assert name == "tickets"
            return FakeQuery()

    monkeypatch.setattr(security_stats, "get_supabase", lambda: FakeSupabase())

    assert security_stats._query_ticket_status_counts_sync(777) == {
        "open_tickets": 2,
        "claimed_tickets": 1,
        "closed_tickets": 1,
    }
    assert selected[0] == "status,claimed_by,assigned_to"
    assert "status,claimed_by" in selected
    assert (0, 1) in ranges
    assert (2, 3) in ranges


def test_spam_guard_read_failure_reuses_last_known_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    security_stats._LAST_SPAM_GUARD_ENABLED.clear()
    calls = 0

    async def fake_get_spam_settings(_guild_id: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"enabled": True}
        raise RuntimeError("temporary database outage")

    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)

    assert asyncio.run(security_stats._spam_guard_enabled(123)) is True
    assert asyncio.run(security_stats._spam_guard_enabled(123)) is True


def test_external_ticket_channel_delete_forces_stats_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed: list[int] = []

    async def fake_find(_channel_id: int):
        return {"status": "open"}

    async def fake_close(**_kwargs):
        return True

    async def fake_delete(**_kwargs):
        return True

    async def fake_refresh(guild_id: int):
        refreshed.append(guild_id)
        return True

    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", fake_find)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_closed", fake_close)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_deleted", fake_delete)
    monkeypatch.setattr(security_stats, "refresh_ticket_stats_for_guild_id", fake_refresh)

    channel = SimpleNamespace(id=999, guild=SimpleNamespace(id=777))
    assert asyncio.run(ticket_events._mark_deleted_after_external_channel_delete(channel)) is True
    assert refreshed == [777]


def test_stats_source_has_no_single_page_or_silent_false_offline_regression() -> None:
    source = (
        __import__("pathlib").Path("stoney_verify/security_stats.py")
        .read_text(encoding="utf-8")
    )
    assert 'range_method = getattr(query, "range", None)' in source
    assert "using={'cached' if cached is not None else 'unknown'}" in source
    assert "spam_guard_enabled=bool(spam_enabled)" not in source
