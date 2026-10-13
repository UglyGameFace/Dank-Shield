from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import ticket_events
from stoney_verify.tickets_new import repository


class FakeGuild:
    id = 777

    def __init__(self, present=()):
        self.present = {int(value) for value in present}

    def get_channel(self, cid):
        return object() if cid in self.present else None

    def get_thread(self, cid):
        return None


def active_row(cid=990, *, thread=None, ghost=False):
    return {
        "id": str(cid),
        "channel_id": str(cid) if cid else None,
        "discord_thread_id": str(thread) if thread else None,
        "status": "claimed",
        "is_ghost": ghost,
    }


def discord_error(code=10003, *, status=404):
    class Response:
        reason = "Not Found"

    Response.status = status
    if status == 403:
        return discord.Forbidden(Response(), {"code": code, "message": "Missing Access"})
    return discord.NotFound(Response(), {"code": code, "message": "Unknown Channel"})


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clear_reconcile_cursor():
    ticket_events._TICKET_RECONCILE_CURSOR.clear()
    yield
    ticket_events._TICKET_RECONCILE_CURSOR.clear()


def test_active_channel_snapshot_paginates_without_truncating(monkeypatch):
    rows = [
        active_row(i + 1000)
        for i in range(503)
    ]
    ranges = []

    class Query:
        def select(self, value):
            assert value == "id,status,channel_id,discord_thread_id,is_ghost"
            return self

        def eq(self, key, value):
            assert (key, value) == ("guild_id", "777")
            return self

        def in_(self, key, values):
            assert key == "status" and values == ["open", "claimed", "closed"]
            return self

        def order(self, key, *, desc):
            assert key == "id" and desc is False
            return self

        def range(self, start, end):
            self.start, self.end = start, end
            ranges.append((start, end))
            return self

        def execute(self):
            return SimpleNamespace(data=rows[self.start : self.end + 1])

    monkeypatch.setattr(repository, "_sb", lambda: SimpleNamespace(table=lambda _: Query()))
    assert len(repository._select_non_deleted_ticket_channel_identities_for_guild_sync("777")) == 503
    assert ranges == [(0, 249), (250, 499), (500, 749)]


def test_active_snapshot_unavailable_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(repository, "_sb", lambda: None)
    assert repository._select_non_deleted_ticket_channel_identities_for_guild_sync("777") is None


def test_missed_native_delete_closes_and_deletes_without_duplicate_refresh(monkeypatch):
    calls = []

    async def list_rows(_gid):
        return [active_row()]

    async def fetch(_cid):
        raise discord_error()

    async def find(cid):
        assert cid == 990
        return {"status": "claimed"}

    async def close(**kwargs):
        calls.append(("closed", kwargs["channel_id"]))
        return True

    async def delete(**kwargs):
        calls.append(("deleted", kwargs["channel_id"]))
        return True

    async def unexpected_refresh(_gid):
        raise AssertionError("periodic stats worker owns the single refresh")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", find)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_closed", close)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_deleted", delete)
    from stoney_verify import security_stats
    monkeypatch.setattr(security_stats, "refresh_ticket_stats_for_guild_id", unexpected_refresh)

    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result == {"checked": 1, "deleted": 1, "uncertain": 0, "rest_checks": 1}
    assert calls == [("closed", 990), ("deleted", 990)]


def test_closed_archived_ticket_removed_offline_is_marked_deleted(monkeypatch):
    closed = active_row(995)
    closed["status"] = "closed"

    async def list_rows(_gid):
        return [closed]

    async def fetch(_cid):
        raise discord_error()

    async def find(cid):
        assert cid == 995
        return {"status": "closed"}

    async def no_close(**_kwargs):
        raise AssertionError("already closed tickets must not be closed twice")

    deletes = []

    async def delete(**kwargs):
        deletes.append(kwargs["channel_id"])
        return True

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", find)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_closed", no_close)
    monkeypatch.setattr(ticket_events, "repo_mark_ticket_deleted", delete)
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["deleted"] == 1 and deletes == [995]


def test_raw_thread_deletion_reuses_ticket_delete_lifecycle(monkeypatch):
    calls = []

    async def find(cid):
        assert cid == 991
        return {"guild_id": "777", "status": "open", "discord_thread_id": "991"}

    async def mark(channel):
        calls.append(channel.id)
        assert channel.guild.id == 777
        return True

    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(get_guild=lambda gid: FakeGuild()))
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", find)
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", mark)
    payload = SimpleNamespace(guild_id=777, thread_id=991)
    run(ticket_events._handle_raw_ticket_thread_delete(payload))
    assert calls == [991]


def test_raw_thread_delete_ignores_unknown_or_cross_guild_rows(monkeypatch):
    async def find(_cid):
        return {"guild_id": "999", "status": "open"}

    async def never_mark(_channel):
        raise AssertionError("cross-guild row must not be changed")

    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(get_guild=lambda gid: FakeGuild()))
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", find)
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", never_mark)
    run(ticket_events._handle_raw_ticket_thread_delete(
        SimpleNamespace(guild_id=777, thread_id=991)
    ))


def test_existing_cached_ticket_never_fetches_or_deletes(monkeypatch):
    async def list_rows(_gid):
        return [active_row()]

    async def no_fetch(_cid):
        raise AssertionError("cached channel must not incur REST lookup")

    async def no_delete(*_args, **_kwargs):
        raise AssertionError("cached ticket must not be deleted")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=no_fetch))
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", no_delete)
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild([990])))
    assert result["deleted"] == 0 and result["rest_checks"] == 0


def test_ghost_and_channel_less_tickets_are_preserved(monkeypatch):
    async def list_rows(_gid):
        return [active_row(991, ghost=True), active_row(0)]

    async def no_fetch(_cid):
        raise AssertionError("ghost or unbound row must not incur REST lookup")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=no_fetch))
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["deleted"] == 0 and result["rest_checks"] == 0


@pytest.mark.parametrize("err", [
    discord_error(50001, status=403),
    discord_error(10008, status=404),
    discord.HTTPException(
        SimpleNamespace(status=502, reason="Bad Gateway"),
        {"code": 0, "message": "Gateway failure"},
    ),
    asyncio.TimeoutError(),
])
def test_uncertain_discord_result_never_deletes(monkeypatch, err):
    async def list_rows(_gid):
        return [active_row()]

    async def fetch(_cid):
        raise err

    async def no_delete(*_args, **_kwargs):
        raise AssertionError("uncertain channel state must not be deleted")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", no_delete)
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["deleted"] == 0 and result["uncertain"] == 1


def test_second_valid_identity_prevents_false_ticket_delete(monkeypatch):
    async def list_rows(_gid):
        return [active_row(990, thread=991)]

    async def fetch(cid):
        if cid == 990:
            raise discord_error()
        return object()

    async def no_delete(*_args, **_kwargs):
        raise AssertionError("existing thread prevents deletion")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", no_delete)
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["deleted"] == 0 and result["rest_checks"] == 2


def test_rest_budget_rotates_past_inconclusive_rows(monkeypatch):
    rows = [active_row(cid) for cid in range(1000, 1040)]
    seen = []

    async def list_rows(_gid):
        return rows

    async def fetch(cid):
        seen.append(cid)
        raise discord_error(50001, status=403)

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    first = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    second = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert first["rest_checks"] == second["rest_checks"] == 25
    assert seen[0] == 1000
    assert seen[25] == 1025
    assert 1039 in seen


def test_db_outage_skips_scan_and_never_calls_discord(monkeypatch):
    async def list_rows(_gid):
        return None

    async def no_fetch(_cid):
        raise AssertionError("DB outage must not trigger speculative deletions")

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=no_fetch))
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["uncertain"] == 1 and result["rest_checks"] == 0


def test_failed_lifecycle_write_remains_retriable(monkeypatch):
    async def list_rows(_gid):
        return [active_row()]

    async def fetch(_cid):
        raise discord_error()

    async def fail_to_write(*_args, **_kwargs):
        return False

    monkeypatch.setattr(ticket_events, "list_non_deleted_ticket_channel_identities_for_guild", list_rows)
    monkeypatch.setattr(ticket_events, "bot", SimpleNamespace(fetch_channel=fetch))
    monkeypatch.setattr(ticket_events, "_mark_deleted_after_external_channel_delete", fail_to_write)
    result = run(ticket_events.reconcile_missing_ticket_channels_for_guild(FakeGuild()))
    assert result["deleted"] == 0 and result["uncertain"] == 1


def test_stats_worker_reconciles_before_reading_ticket_counts(monkeypatch):
    from stoney_verify import security_stats

    calls = []
    guild = FakeGuild()
    monkeypatch.setattr(security_stats, "_ACTIVE_DISPLAY_GUILDS", {guild.id})
    monkeypatch.setattr(
        security_stats, "bot",
        SimpleNamespace(get_guild=lambda gid: guild if gid == guild.id else None),
    )

    async def reconcile(received_guild):
        assert received_guild is guild
        calls.append("reconcile")
        return {"checked": 1, "deleted": 1, "uncertain": 0, "rest_checks": 1}

    async def refresh(received_guild):
        assert received_guild is guild
        calls.append("refresh")
        return True

    monkeypatch.setattr(ticket_events, "reconcile_missing_ticket_channels_for_guild", reconcile)
    monkeypatch.setattr(security_stats, "refresh_security_stats_display", refresh)
    run(security_stats.refresh_all_security_stats_displays.coro())
    assert calls == ["reconcile", "refresh"]


def test_deleted_record_not_misrepresented_as_completed_close():
    from stoney_verify import security_stats

    result = security_stats._ticket_status_counts_from_rows([
        {"status": "closed"},
        {"status": "deleted"},
        {"status": "claimed", "claimed_by": "22"},
    ])
    assert result == {
        "open_tickets": 1,
        "claimed_tickets": 1,
        "closed_tickets": 1,
    }


def test_staff_refresh_reports_partial_ticket_truth(monkeypatch):
    from stoney_verify.commands_ext import public_server_stats

    async def reconcile(_guild):
        return {"deleted": 0, "uncertain": 2, "rest_checks": 3}

    monkeypatch.setattr(ticket_events, "reconcile_missing_ticket_channels_for_guild", reconcile)
    summary = run(public_server_stats._reconcile_tickets_before_stats(FakeGuild()))
    assert summary["uncertain"] == 2
    assert "could not be verified" in public_server_stats._ticket_reconciliation_note(summary)
    assert "Reconciled 2" in public_server_stats._ticket_reconciliation_note({
        "deleted": 2, "uncertain": 0, "rest_checks": 2,
    })


def test_staff_refresh_handles_reconciliation_error_without_claiming_accuracy(monkeypatch):
    from stoney_verify.commands_ext import public_server_stats

    async def reconcile(_guild):
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(ticket_events, "reconcile_missing_ticket_channels_for_guild", reconcile)
    summary = run(public_server_stats._reconcile_tickets_before_stats(FakeGuild()))
    assert summary["uncertain"] == 1
    assert "may" in public_server_stats._ticket_reconciliation_note(summary)
