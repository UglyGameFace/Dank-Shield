"""Member history must not conflate separate joins or infer membership."""
from __future__ import annotations

from stoney_verify.tickets_new.member_context_service import (
    _build_join_summary,
    _build_relationships,
)

OLDER = "2026-10-01T10:00:00+00:00"
NEWER = "2026-11-01T10:00:00+00:00"


def test_newer_join_cannot_inherit_previous_invite_and_inviter() -> None:
    profile = {
        "joined_at": OLDER,
        "entry_method": "invite",
        "invite_code": "OLDER_CODE",
        "invited_by": "123456789012345678",
        "entry_truth_quality": "confirmed",
    }
    joins = [{
        "joined_at": NEWER,
        "entry_method": "invite_unresolved",
        "entry_truth_quality": "unknown",
    }]
    result = _build_relationships(profile, joins, [])
    assert result["entry_method"] == "invite_unresolved"
    assert result["invite_code"] is None
    assert result["invited_by"] is None
    assert result["entry_truth_quality"] == "unknown"


def test_same_visit_can_reuse_profile_source() -> None:
    profile = {
        "joined_at": NEWER,
        "entry_method": "invite",
        "invite_code": "CURRENT_CODE",
    }
    joins = [{"joined_at": NEWER}]
    result = _build_relationships(profile, joins, [])
    assert result["invite_code"] == "CURRENT_CODE"


def test_missing_join_timestamps_do_not_authorize_cross_visit_claims() -> None:
    profile = {"joined_at": OLDER, "invite_code": "PREVIOUS_CODE"}
    joins = [{"entry_method": "invite_unresolved"}]
    assert _build_relationships(profile, joins, [])["invite_code"] is None


def test_missing_profile_does_not_claim_current_membership() -> None:
    assert _build_join_summary(None, [], [], {})["in_guild"] is None


def test_first_join_uses_oldest_recorded_join_not_first_seen() -> None:
    profile = {"joined_at": NEWER, "first_seen_at": "2026-10-20T00:00:00+00:00"}
    joins = [{"joined_at": NEWER}, {"joined_at": OLDER}]
    result = _build_join_summary(profile, joins, [], {})
    assert result["first_joined_at"] == OLDER
    assert result["latest_joined_at"] == NEWER
