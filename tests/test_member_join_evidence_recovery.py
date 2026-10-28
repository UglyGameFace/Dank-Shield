from __future__ import annotations

from stoney_verify.commands_ext.member_command_center import _historical_member_embed
from stoney_verify.tickets_new.member_context_service import _build_relationships


USER_ID = 443230350567800843
JOIN_AT = "2026-09-07T18:08:45.042+00:00"


def _event(*, when: str = JOIN_AT, code: str = "hg5BAT8tsw") -> dict:
    return {
        "event_type": "member_joined",
        "created_at": "2026-09-07T18:08:47.277+00:00",
        "metadata": {
            "joined_at": when,
            "entry_method": "invite",
            "join_source": "discord_invite",
            "invite_code": code,
            "invited_by": "302050872383242240",
            "invited_by_name": "DISBOARD#2760",
            "entry_truth_quality": "confirmed",
            "entry_quality_reason": "Invite usage delta identified a specific invite.",
            "entry_confidence": 95,
        },
    }


def test_missing_join_row_recovers_confirmed_source_from_same_join_event() -> None:
    # This is the shape observed in the owner's connected data: a guild-member
    # record and a retained member_joined event, but no member_joins row.
    member = {
        "joined_at": JOIN_AT,
        "entry_method": None,
        "entry_truth_quality": "unknown",
        "entry_quality_reason": "Join attribution is unknown.",
        "invite_code": None,
    }
    relationships = _build_relationships(member, [], [_event()])
    assert relationships["entry_method"] == "invite"
    assert relationships["invite_code"] == "hg5BAT8tsw"
    assert relationships["invited_by_name"] == "DISBOARD#2760"
    assert relationships["entry_truth_quality"] == "confirmed"
    assert relationships["attribution_record"] == "member_joined_event"

    snapshot = {
        "member": {"display_name": "Example", **member},
        "join_summary": {"times_joined": 1, "times_left": 0, "latest_joined_at": JOIN_AT,
                         "latest_entry_method": relationships["entry_method"]},
        "event_summary": {"total": 1, "moderation_count": 0, "latest_event_type": "member_joined"},
        "ticket_summary": {"total": 0},
        "recent_joins": [],
        "relationships": relationships,
        "recent_events": [_event()],
    }
    rendered = _historical_member_embed(USER_ID, snapshot, [], presence="present")
    fields = {field.name: field.value for field in rendered.fields}
    source = fields["How they joined"]
    assert "Server invite" in source
    assert "hg5BAT8tsw" in source
    assert "DISBOARD" in source
    assert "Original member-joined event" in source
    assert "Confirmed in retained join record" in source


def test_old_event_cannot_supply_source_of_later_rejoin() -> None:
    member = {"joined_at": "2026-10-28T12:00:00+00:00", "entry_method": None}
    result = _build_relationships(member, [], [_event()])
    assert result["invite_code"] is None
    assert result["attribution_record"] == "join_history_or_profile"


def test_join_row_timestamp_takes_priority_over_stale_profile_timestamp() -> None:
    member = {"joined_at": JOIN_AT}
    join_row = {"joined_at": "2026-10-28T12:00:00+00:00", "entry_method": "invite_unresolved"}
    result = _build_relationships(member, [join_row], [_event()])
    assert result["invite_code"] is None
    assert result["entry_method"] == "invite_unresolved"


def test_different_member_event_and_untrusted_timestamp_dont_become_invites() -> None:
    member = {"joined_at": JOIN_AT}
    not_join = {**_event(), "event_type": "member_updated"}
    bad_time = _event(when="invalid")
    result = _build_relationships(member, [], [not_join, bad_time])
    assert result["invite_code"] is None
    assert result["attribution_record"] == "join_history_or_profile"


def test_explicit_join_row_source_remains_authoritative() -> None:
    member = {"joined_at": JOIN_AT}
    join_row = {
        "joined_at": JOIN_AT,
        "entry_method": "invite",
        "invite_code": "ACTUAL",
        "invited_by_name": "Creator",
        "entry_truth_quality": "confirmed",
    }
    result = _build_relationships(member, [join_row], [_event(code="UNRELATED")])
    assert result["invite_code"] == "ACTUAL"
    assert result["invited_by_name"] == "Creator"
    assert result["attribution_record"] == "join_history_or_profile"
