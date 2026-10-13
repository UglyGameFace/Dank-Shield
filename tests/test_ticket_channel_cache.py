from __future__ import annotations

import time

import pytest

from stoney_verify.tickets_new import repository as repo


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