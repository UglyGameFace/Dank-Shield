from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import action_member_consent as consent
from stoney_verify import profile_card_service as profiles


STAMP = "2026-10-23T16:00:00+00:00"


def _row(guild: str, user: str, *, flag=True, version=1, timestamp=STAMP):
    return {
        "guild_id": guild, "user_id": user,
        "settings": {
            consent.KEY: {
                "version": version, "member_setup_dm": flag, "updated_at": timestamp,
            }
        },
    }


class _Query:
    def __init__(self, items):
        self.items = items
        self.trace = []

    def select(self, value):
        self.trace.append(("select", value))
        return self

    def eq(self, field, value):
        self.trace.append(("eq", field, value))
        return self

    def contains(self, field, value):
        self.trace.append(("contains", field, value))
        return self

    def gt(self, field, value):
        self.trace.append(("gt", field, value))
        return self

    def order(self, field):
        self.trace.append(("order", field))
        return self

    def limit(self, value):
        self.trace.append(("limit", value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.items)


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.table_name = ""

    def table(self, name):
        self.table_name = name
        self.query = _Query(self.rows)
        return self.query


def test_opted_in_page_uses_existing_store_json_filter_not_entire_guild(monkeypatch):
    client = _Client([_row("50", "1000"), _row("50", "900")])
    async def execute(_label, callback):
        return callback(client)
    monkeypatch.setattr(profiles, "_execute", execute)

    # The profile schema stores snowflakes as TEXT; lexical order is intentional.
    first = asyncio.run(consent.read_member_setup_opt_in_page(50, limit=2))
    assert first.user_ids == (1000, 900)
    assert first.next_after_user_id == 900
    trace = client.query.trace
    assert ("eq", "guild_id", "50") in trace
    assert ("contains", "settings", {consent.KEY: {"member_setup_dm": True}}) in trace
    assert ("select", "guild_id,user_id,settings") in trace
    assert ("order", "user_id") in trace
    assert ("limit", 2) in trace
    assert not any(q[0] in ("range", "fetch_members", "send") for q in trace)
    assert client.table_name == profiles.PROFILE_GUILD_SETTINGS_TABLE

    client.rows = [_row("50", "901")]
    second = asyncio.run(consent.read_member_setup_opt_in_page(
        50, after_user_id=900, limit=2
    ))
    assert second.user_ids == (901,)
    assert second.next_after_user_id is None
    assert ("gt", "user_id", "900") in client.query.trace


def test_nonconsenting_or_invalid_rows_never_create_a_send_candidate(monkeypatch):
    client = _Client([_row("50", "100", flag=False)])
    async def execute(_label, callback):
        return callback(client)
    monkeypatch.setattr(profiles, "_execute", execute)
    with pytest.raises(Exception):
        asyncio.run(consent.read_member_setup_opt_in_page(50, limit=1))

    for row in (
        _row("50", "100", version=2),
        _row("50", "100", timestamp=""),
        _row("999", "100"),
        _row("50", "0"),
        _row("50", "100", timestamp="bad-date"),
    ):
        client.rows = [row]
        with pytest.raises((ValueError, RuntimeError, profiles.ProfileStorageUnavailable)):
            asyncio.run(consent.read_member_setup_opt_in_page(50, limit=1))


def test_bad_cursor_page_size_and_storage_outage_fail_closed(monkeypatch):
    async def broken(*_args, **_kwargs):
        raise profiles.ProfileStorageUnavailable("Private DB unavailable")
    monkeypatch.setattr(profiles, "_execute", broken)
    with pytest.raises(profiles.ProfileStorageUnavailable):
        asyncio.run(consent.read_member_setup_opt_in_page(50, limit=1))
    for bad in (0, -1, 11, True, "10"):
        with pytest.raises(ValueError):
            asyncio.run(consent.read_member_setup_opt_in_page(50, limit=bad))
    with pytest.raises(ValueError):
        asyncio.run(consent.read_member_setup_opt_in_page(0))
    with pytest.raises(ValueError):
        asyncio.run(consent.read_member_setup_opt_in_page(50, after_user_id=-5))


def test_empty_or_short_page_stops_without_lying_about_eligibility(monkeypatch):
    client = _Client([])
    async def execute(_label, callback):
        return callback(client)
    monkeypatch.setattr(profiles, "_execute", execute)
    page = asyncio.run(consent.read_member_setup_opt_in_page(50, limit=3))
    assert page.user_ids == ()
    assert page.next_after_user_id is None
    with pytest.raises(ValueError):
        consent.MemberSetupOptInPage((100,), next_after_user_id=99)


def test_opt_in_source_is_not_a_new_worker_sender_or_slash_command():
    source = Path(profiles.__file__).read_text()
    raw = Path(consent.__file__).read_text()
    scope = source[source.index("async def list_profile_guild_settings_namespace_page("):
                   source.index("async def upsert_profile_guild_namespace(")]
    reader = raw[raw.index("async def read_member_setup_opt_in_page("):
                 raw.index("async def save_member_consent(")]
    for forbidden in ("create_task(", "member.send(", "channel.send(",
                      "guild.fetch_members(", "guild.fetch_member(",
                      ".insert(", ".update(", ".rpc(", "reserve_member_setup_sync("):
        assert forbidden not in scope + reader
    assert "list_profile_guild_settings_namespace_page" in reader
    assert "parse_consent" in reader
    assert "read_member_consent" in raw  # live eligibility remains a separate fresh read
