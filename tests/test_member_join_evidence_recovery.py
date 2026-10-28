from __future__ import annotations

from stoney_verify.commands_ext.member_command_center import _historical_member_embed
from stoney_verify.tickets_new.member_context_service import _build_relationships


USER_ID = 123456789012345678
JOIN_AT = "2026-09-07T18:08:45.042+00:00"


def _event(*, when: str = JOIN_AT, code: str = "INVITE123") -> dict:
    return {
        "event_type": "member_joined",
        "created_at": "2026-09-07T18:08:47.277+00:00",
        "metadata": {
            "joined_at": when,
            "entry_method": "invite",
            "join_source": "discord_invite",
            "invite_code": code,
            "invited_by": "234567890123456789",
            "invited_by_name": "ListingBot#0001",
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
    assert relationships["invite_code"] == "INVITE123"
    assert relationships["invited_by_name"] == "ListingBot#0001"
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
    assert "INVITE123" in source
    assert "ListingBot" in source
    assert "Invite creator" in source
    assert "Original member-joined event" in source
    assert "Confirmed in retained join event" in source


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


def test_older_join_event_is_queried_if_recent_activity_has_no_join(monkeypatch) -> None:
    import asyncio
    from stoney_verify.tickets_new import member_context_service as svc

    async def member_reader(**kwargs):
        return {"guild_id": str(kwargs["guild_id"]), "user_id": str(kwargs["user_id"]),
                "joined_at": JOIN_AT, "entry_method": None, "invite_code": None}

    async def joins_reader(**kwargs):
        return []

    event_queries = []

    async def events_reader(**kwargs):
        event_queries.append(kwargs.get("event_types"))
        if kwargs.get("event_types"):
            return [_event()]
        return [{"event_type": "member_updated", "metadata": {},
                 "created_at": "2026-10-28T10:00:00+00:00"}]

    async def tickets_reader(**kwargs):
        return []

    async def no_open_ticket(**kwargs):
        return None

    monkeypatch.setattr(svc, "get_guild_member_row", member_reader)
    monkeypatch.setattr(svc, "list_member_join_rows", joins_reader)
    monkeypatch.setattr(svc, "list_member_event_rows", events_reader)
    monkeypatch.setattr(svc, "list_member_ticket_rows", tickets_reader)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open_ticket)

    snapshot = asyncio.run(svc.get_member_context_snapshot(
        guild_id=987654321098765432, user_id=USER_ID,
    ))
    assert snapshot["relationships"]["invite_code"] == "INVITE123"
    assert snapshot["relationships"]["attribution_record"] == "member_joined_event"
    assert event_queries == [None, ("member_joined",)]
    # Historical source does not alter the activity count or fake a join row.
    assert snapshot["event_summary"]["total"] == 1
    assert snapshot["recent_joins"] == []


def test_unmatched_old_join_event_is_rejected_by_snapshot(monkeypatch) -> None:
    import asyncio
    from stoney_verify.tickets_new import member_context_service as svc

    async def member_reader(**kwargs):
        return {"joined_at": "2026-10-28T12:00:00+00:00", "invite_code": None}

    async def no_rows(**kwargs):
        return []

    async def events_reader(**kwargs):
        if kwargs.get("event_types"):
            return [_event()]
        return []

    async def no_open_ticket(**kwargs):
        return None

    monkeypatch.setattr(svc, "get_guild_member_row", member_reader)
    monkeypatch.setattr(svc, "list_member_join_rows", no_rows)
    monkeypatch.setattr(svc, "list_member_event_rows", events_reader)
    monkeypatch.setattr(svc, "list_member_ticket_rows", no_rows)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open_ticket)

    snapshot = asyncio.run(svc.get_member_context_snapshot(
        guild_id=987654321098765432, user_id=USER_ID,
    ))
    assert snapshot["relationships"]["invite_code"] is None
