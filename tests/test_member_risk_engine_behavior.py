from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import raidguard


class FakeAvatar:
    url = "https://example.test/avatar.png"


class FakeMember:
    def __init__(
        self,
        *,
        user_id: int = 101,
        username: str = "normaluser",
        age_days: int = 365,
        default_avatar: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.id = user_id
        self.name = username
        self.display_name = username
        self.bot = False
        self.created_at = now - timedelta(days=age_days)
        self.joined_at = now
        self.avatar = None if default_avatar else FakeAvatar()
        self.display_avatar = FakeAvatar()
        self.guild = SimpleNamespace(id=777, me=None)
        self.roles = []


def empty_hard_context() -> dict:
    return {
        "proof_matches": [],
        "matched_identity_fingerprints": [],
        "manual_confirmed": [],
        "manual_likely": [],
        "manual_not_linked_ids": set(),
    }


def test_listing_source_alone_is_not_risk() -> None:
    member = FakeMember(username="friendlyhuman", age_days=900)
    profile = raidguard.build_member_risk_profile(
        member,
        join_context={"join_source": "Disboard public listing"},
        hard_identity_context=empty_hard_context(),
    )

    assert profile["listing_source"] is True
    assert profile["risk_score"] == 0
    assert profile["alt_evidence_tier"] == "clear"
    assert profile["spam_risk_score"] == 0
    assert profile["review_verdict"] == "LOW CONCERN — NORMAL LISTING TRAFFIC"


def test_birth_year_suffix_on_established_profile_stays_low() -> None:
    member = FakeMember(username="Jamie1992", age_days=1200)
    profile = raidguard.build_member_risk_profile(
        member,
        hard_identity_context=empty_hard_context(),
    )

    assert profile["profile_risk_score"] == 0
    assert profile["alt_evidence_tier"] == "clear"
    assert profile["risk_level"] == "low"


def test_fresh_generated_profile_is_watchlist_not_alt_proof() -> None:
    member = FakeMember(
        username="user1234567",
        age_days=1,
        default_avatar=True,
    )
    profile = raidguard.build_member_risk_profile(
        member,
        hard_identity_context=empty_hard_context(),
    )

    assert profile["profile_risk_score"] > 0
    assert profile["alt_evidence_tier"] in {"clear", "suspicious"}
    assert profile["alt_evidence_tier"] not in {
        "strongly_linked",
        "confirmed_duplicate",
    }
    assert profile["review_verdict"] == "REVIEW RECOMMENDED"


def test_real_spam_behavior_raises_spam_dimension_not_alt_identity() -> None:
    member = FakeMember(username="establisheduser", age_days=700)
    profile = raidguard.build_member_risk_profile(
        member,
        hard_identity_context=empty_hard_context(),
        behavior_context={
            "spam_guard_triggered": True,
            "url_flood": True,
            "cross_channel_flood": True,
            "action_taken": "timeout:30m",
            "deleted_count": 6,
            "channel_count": 3,
        },
    )

    assert profile["alt_evidence_tier"] == "clear"
    assert profile["spam_risk_score"] >= 70
    assert profile["possible_spam_account"] is True
    assert profile["review_verdict"] == "HIGH-CONFIDENCE SPAM ACCOUNT"


def test_hard_identity_proof_remains_confirmed_duplicate() -> None:
    member = FakeMember(username="knownalt", age_days=400)
    hard = empty_hard_context()
    hard["proof_matches"] = [
        {
            "user_id": 202,
            "identity_fingerprint": "proof-abc",
            "match_confidence": 100,
        }
    ]
    hard["matched_identity_fingerprints"] = ["proof-abc"]

    profile = raidguard.build_member_risk_profile(
        member,
        hard_identity_context=hard,
    )

    assert profile["alt_evidence_tier"] == "confirmed_duplicate"
    assert profile["risk_score"] == 100
    assert profile["review_verdict"] == "CONFIRMED DUPLICATE IDENTITY"


def test_async_assessment_loads_hard_context_off_loop_and_records_once(
    monkeypatch,
) -> None:
    member = FakeMember(username="asyncproof", age_days=300)
    loader_calls: list[tuple[int, int]] = []
    records: list[int] = []

    def fake_load(guild_id: int, user_id: int) -> dict:
        loader_calls.append((guild_id, user_id))
        return empty_hard_context()

    def fake_record(recorded_member, profile) -> None:
        assert recorded_member is member
        assert isinstance(profile, dict)
        records.append(recorded_member.id)

    monkeypatch.setattr(
        raidguard,
        "_load_hard_identity_context",
        fake_load,
    )
    monkeypatch.setattr(raidguard, "_record_join_profile", fake_record)

    profile = asyncio.run(
        raidguard.assess_member_join_risk(
            member,
            join_context={"join_source": "invite"},
            record=True,
        )
    )

    assert profile["user_id"] == member.id
    assert loader_calls == [(777, member.id)]
    assert records == [member.id]


def test_mass_strip_reuses_supplied_profile_without_reassessing(
    monkeypatch,
) -> None:
    member = FakeMember(username="reuseprofile", age_days=300)
    monkeypatch.setattr(
        raidguard,
        "_recent_join_burst_count",
        lambda _guild_id: 99,
    )

    async def forbidden_reassessment(*_args, **_kwargs):
        raise AssertionError("supplied profile must be reused")

    monkeypatch.setattr(
        raidguard,
        "assess_member_join_risk",
        forbidden_reassessment,
    )

    result = asyncio.run(
        raidguard._mass_role_strip_if_needed(
            member,
            {
                "level": "low",
                "risk_level": "low",
                "score": 0,
                "risk_score": 0,
                "evidence_tier": "clear",
            },
        )
    )
    assert result is None
