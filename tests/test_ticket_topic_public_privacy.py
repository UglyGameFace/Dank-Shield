"""Regression checks for ticket channel topic privacy.

A Discord ticket topic is member-readable, never an internal metadata store.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify.tickets_new.topic_policy import PUBLIC_TICKET_TOPIC, is_public_ticket_topic

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_topic_contains_no_internal_ticket_fields() -> None:
    assert is_public_ticket_topic(PUBLIC_TICKET_TOPIC)
    assert is_public_ticket_topic(" " + PUBLIC_TICKET_TOPIC.lower() + " ")
    assert not is_public_ticket_topic("owner_id=123;category=support;ghost=false;ticket_number=4")
    for token in ("owner_id=", "requester_id=", "user_id=", "ghost=", "category=", "ticket_number="):
        assert token not in PUBLIC_TICKET_TOPIC
    assert "support ticket" in PUBLIC_TICKET_TOPIC.lower()


def test_all_three_ticket_writers_use_public_topic() -> None:
    service = _source("stoney_verify/tickets_new/service.py")
    panel = _source("stoney_verify/commands_ext/public_ticket_panel_clean.py")
    verification = _source("stoney_verify/startup_guards/unverified_ticket_panel_flow.py")
    assert "return PUBLIC_TICKET_TOPIC" in service
    assert "topic=PUBLIC_TICKET_TOPIC," in panel
    assert "topic = PUBLIC_TICKET_TOPIC" in verification
    for writer in (service, panel, verification):
        assert 'topic=f"owner_id=' not in writer
        assert 'topic = f"owner_id=' not in writer


def test_ticket_detection_preserves_legacy_readers() -> None:
    service = _source("stoney_verify/tickets_new/service.py")
    assert r"owner_id=(\d+)" in service
    assert r"ticket_number=(\d+)" in service
    for path in (
        "stoney_verify/tickets_new/sync_service.py",
        "stoney_verify/tickets_new/sync_categories.py",
        "stoney_verify/ticket_events.py",
        "stoney_verify/tickets.py",
    ):
        assert "is_public_ticket_topic(" in _source(path)


def test_reconciliation_uses_stored_owner_even_if_staff_has_overwrite() -> None:
    from stoney_verify.tickets_new.sync_service import _extract_owner_member

    owner = SimpleNamespace(id=123)
    staff = object()  # Hashable simulated channel-overwrite target.
    guild = SimpleNamespace(get_member=lambda uid: owner if uid == owner.id else None)
    channel = SimpleNamespace(topic=PUBLIC_TICKET_TOPIC, overwrites={staff: SimpleNamespace(view_channel=True)})
    assert _extract_owner_member(guild, channel, existing={"user_id": "123"}) is owner
    # Owner departed the guild: do not silently replace with a staff member.
    assert _extract_owner_member(guild, channel, existing={"user_id": "999"}) is None


def test_reconciliation_keeps_db_category_ghost_and_sequence() -> None:
    sync = _source("stoney_verify/tickets_new/sync_service.py")
    assert "owner = _extract_owner_member(guild, channel, existing=existing)" in sync
    assert 'category = str(existing_dict.get("category")' in sync
    assert 'is_ghost = _safe_bool(existing_dict.get("is_ghost")' in sync
    assert 'ticket_number = _safe_int(existing_dict.get("ticket_number"), 0) or None' in sync


def test_unpersisted_public_ticket_is_not_recovered_with_guessed_owner() -> None:
    sync = _source("stoney_verify/tickets_new/sync_service.py")
    assert 'if is_public_ticket_topic(_safe_topic(channel)) and not isinstance(existing, dict):' in sync
    assert '"reason": "public_topic_has_no_authoritative_ticket_row"' in sync
    service = _source("stoney_verify/tickets_new/service.py")
    assert 'if not str(channel.topic or "").strip():' in service
    assert 'if channel.topic != desired_topic:' not in service
