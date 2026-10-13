from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new import repository as repo
from stoney_verify import ticket_events


@pytest.fixture(autouse=True)
def _reset_channel_cache(monkeypatch):
    repo._TICKET_CHANNEL_IDS.clear()
    repo._TICKET_CHANNEL_NEGATIVE.clear()
    monkeypatch.delenv("DANK_TICKET_CHANNEL_NEGATIVE_TTL_SECONDS", raising=False)
    yield
    repo._TICKET_CHANNEL_IDS.clear()
    repo._TICKET_CHANNEL_NEGATIVE.clear()


def test_unknown_channel_returns_none():
    assert repo.ticket_channel_cached_state(12345) is None


def test_opened_channel_is_positive():
    repo.note_ticket_channel_opened(12345)
    assert repo.ticket_channel_cached_state(12345) is True


def test_deleted_channel_evicts_positive():
    repo.note_ticket_channel_opened(12345)
    repo.note_ticket_channel_deleted(12345)
    # After delete the channel is unknown (negative cache is opt-in per lookup).
    assert repo.ticket_channel_cached_state(12345) is None


def test_negative_lookup_caches_false_within_ttl(monkeypatch):
    monkeypatch.setenv("DANK_TICKET_CHANNEL_NEGATIVE_TTL_SECONDS", "300")
    repo.note_ticket_channel_lookup(999, False)
    assert repo.ticket_channel_cached_state(999) is False


def test_negative_cache_expires(monkeypatch):
    monkeypatch.setenv("DANK_TICKET_CHANNEL_NEGATIVE_TTL_SECONDS", "300")
    repo.note_ticket_channel_lookup(999, False)
    # Force expiry.
    repo._TICKET_CHANNEL_NEGATIVE[999] = time.monotonic() - 1
    assert repo.ticket_channel_cached_state(999) is None


def test_opened_clears_negative():
    repo.note_ticket_channel_lookup(999, False)
    assert repo.ticket_channel_cached_state(999) is False
    repo.note_ticket_channel_opened(999)
    assert repo.ticket_channel_cached_state(999) is True


def test_invalid_ids_are_ignored():
    repo.note_ticket_channel_opened(0)
    repo.note_ticket_channel_opened(-5)
    repo.note_ticket_channel_opened("nope")
    assert repo._TICKET_CHANNEL_IDS == set()
    assert repo.ticket_channel_cached_state(0) is None

def _fake_channel(monkeypatch, *, name="general", channel_id=991):
    class FakeChannel:
        def __init__(self):
            self.id = channel_id
            self.name = name
            self.topic = ""
            self.guild = SimpleNamespace(id=1)
    monkeypatch.setattr(ticket_events.discord, "TextChannel", FakeChannel)
    return FakeChannel()


def test_ticket_identity_read_failure_does_not_negative_cache(monkeypatch):
    channel = _fake_channel(monkeypatch)
    async def unavailable(cid):
        raise RuntimeError("Supabase outage")
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", unavailable)
    assert asyncio.run(ticket_events._is_ticket_channel(channel)) is False
    assert repo.ticket_channel_cached_state(channel.id) is None


def test_renamed_ticket_overrides_negative_cache_immediately(monkeypatch):
    channel = _fake_channel(monkeypatch, name="ticket-123")
    repo.note_ticket_channel_lookup(channel.id, False)
    assert asyncio.run(ticket_events._is_ticket_channel(channel)) is True
    assert repo.ticket_channel_cached_state(channel.id) is True


def test_claim_first_nonstaff_never_queries_db(monkeypatch):
    channel = _fake_channel(monkeypatch)
    class FakeMember:
        id = 7
    monkeypatch.setattr(ticket_events.discord, "Member", FakeMember)
    monkeypatch.setattr(ticket_events, "claim_policy_is_staff_member", lambda *a, **kw: False)
    async def forbidden(cid):
        raise AssertionError("non-staff must skip the DB")
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", forbidden)
    msg = SimpleNamespace(channel=channel, author=FakeMember())
    assert asyncio.run(ticket_events._enforce_claim_first_staff_message(msg)) is True


def test_claim_first_staff_lookup_failure_never_poison_cache(monkeypatch):
    channel = _fake_channel(monkeypatch)
    class FakeMember:
        id = 7
    monkeypatch.setattr(ticket_events.discord, "Member", FakeMember)
    monkeypatch.setattr(ticket_events, "claim_policy_is_staff_member", lambda *a, **kw: True)
    async def down(cid):
        raise RuntimeError("timeout")
    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", down)
    msg = SimpleNamespace(channel=channel, author=FakeMember())
    assert asyncio.run(ticket_events._enforce_claim_first_staff_message(msg)) is True
    assert repo.ticket_channel_cached_state(channel.id) is None
