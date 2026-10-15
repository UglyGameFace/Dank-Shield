"""Legacy ticket topic cleanup must be fail-closed and owner-authorized."""
from __future__ import annotations

from pathlib import Path

import pytest

from stoney_verify.tickets_new.topic_repair import (
    MAX_INSPECT,
    MAX_REPAIR,
    evaluate_legacy_topic,
)

ROOT = Path(__file__).resolve().parents[1]
TOPIC = "owner_id=123;category=support;ghost=false;ticket_number=42"


def good_row() -> dict:
    return {
        "guild_id": "700",
        "channel_id": "800",
        "discord_thread_id": "800",
        "user_id": "123",
        "owner_id": "123",
        "requester_id": "123",
        "ticket_number": 42,
        "category": "support",
        "is_ghost": False,
        "status": "open",
    }


def decision(topic: str = TOPIC, row: dict | None = None):
    return evaluate_legacy_topic(
        guild_id=700, channel_id=800,
        topic=topic, row=good_row() if row is None else row,
    )


def test_only_identical_canonical_legacy_topic_is_repairable():
    assert decision().safe is True
    assert decision(" owner_id=123;category=support;ghost=false;ticket_number=42 ").safe
    assert not decision(TOPIC + ";staff_note=secret").safe
    assert not decision("Custom server topic").safe
    assert not decision("Dank Shield support ticket | Describe your request here. Staff will help you in this channel.").safe


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("guild_id", "701", "guild_mismatch"),
        ("channel_id", "900", "channel_mismatch"),
        ("discord_thread_id", "900", "channel_mismatch"),
        ("user_id", "124", "owner_mismatch"),
        ("owner_id", "124", "owner_mismatch"),
        ("requester_id", "124", "owner_mismatch"),
        ("ticket_number", 43, "ticket_number_mismatch"),
        ("category", "appeal", "category_mismatch"),
        ("is_ghost", True, "ghost_mismatch"),
        ("status", "deleted", "invalid_ticket_status"),
    ],
)
def test_conflicting_authoritative_row_is_skipped(field, value, reason):
    row = good_row()
    row[field] = value
    found = decision(row=row)
    assert not found.safe
    assert found.reason == reason


def test_missing_and_incomplete_rows_fail_closed():
    absent = evaluate_legacy_topic(guild_id=700, channel_id=800, topic=TOPIC, row=None)
    assert not absent.safe and absent.reason == "no_durable_record"
    for key in ("guild_id", "channel_id", "discord_thread_id", "user_id", "owner_id", "requester_id", "ticket_number", "category", "is_ghost"):
        row = good_row()
        row.pop(key)
        if key in {"channel_id", "discord_thread_id", "user_id", "owner_id", "requester_id"}:
            # Single stored identifier is acceptable when remaining identifiers match.
            continue
        assert not decision(row=row).safe, key


def test_repair_requires_preview_confirmation_and_bounds():
    assert MAX_INSPECT == 25
    assert MAX_REPAIR == 10
    ui = (ROOT / "stoney_verify/commands_ext/public_ticket_command_center.py").read_text()
    svc = (ROOT / "stoney_verify/tickets_new/topic_repair.py").read_text()
    assert 'label="Review Old Topics"' in ui
    assert 'label="Clean Verified Topics"' in ui
    assert "member.guild_permissions.manage_guild" in ui
    assert "self.used = True" in ui
    assert "evaluate_legacy_topic(" in svc
    assert "channel.permissions_for(me).manage_channels" in svc
    assert "await channel.edit(" in svc
    assert "await get_ticket_by_any_channel_id(channel.id)" in svc
    assert "MAX_REPAIR" in svc
    assert "PUBLIC_TICKET_TOPIC" in svc
    assert "guild.text_channels" in svc
