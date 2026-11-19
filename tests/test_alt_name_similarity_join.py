from __future__ import annotations

"""Join-time guild-wide name-similarity alt evidence.

Regression coverage for the 2026-11-19 live failure: a joiner named
``legendarydata64phone`` was rated LOW CONCERN even though the obvious
main account ``legendarydata64`` was already a guild member. The old
pipeline only compared names inside a ~20-minute recent-join window and
required a 0.86 SequenceMatcher ratio (the pair scores 0.857), and the
risk engine ignored a lone name match entirely.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import raidguard
from stoney_verify.member_risk_engine import enrich_member_risk_profile
from stoney_verify.name_similarity import find_name_matches, score_name_pair


class FakeAvatar:
    url = "https://example.test/avatar.png"


class FakeMember:
    def __init__(
        self,
        *,
        user_id: int,
        username: str,
        display_name: str | None = None,
        age_days: int = 900,
        default_avatar: bool = False,
        bot: bool = False,
        guild=None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.id = user_id
        self.name = username
        self.display_name = display_name if display_name is not None else username
        self.bot = bot
        self.created_at = now - timedelta(days=age_days)
        self.joined_at = now
        self.avatar = None if default_avatar else FakeAvatar()
        self.display_avatar = FakeAvatar()
        self.guild = guild
        self.roles = []

    def __str__(self) -> str:
        return self.name


def empty_hard_context() -> dict:
    return {
        "proof_matches": [],
        "matched_identity_fingerprints": [],
        "manual_confirmed": [],
        "manual_likely": [],
        "manual_not_linked_ids": set(),
    }


def make_guild(*members: FakeMember, guild_id: int = 4242):
    guild = SimpleNamespace(id=guild_id, me=None, members=list(members))
    for member in members:
        member.guild = guild
    return guild


def build(joiner: FakeMember, **kwargs):
    kwargs.setdefault("hard_identity_context", empty_hard_context())
    return raidguard.build_member_risk_profile(joiner, **kwargs)


def test_screenshot_case_now_flags_possible_alt() -> None:
    main = FakeMember(
        user_id=111222333444555666,
        username="legendarydata64",
        display_name="🎃👻LEGENDARYDATA64👻🎃",
        age_days=1200,
    )
    joiner = FakeMember(
        user_id=1462200511347429712,
        username="legendarydata64phone",
        display_name="Legendarydata64 (phone)",
        age_days=305,
        default_avatar=True,
    )
    make_guild(main, joiner)

    profile = build(joiner)

    assert profile["strong_name_match_count"] == 1
    match = profile["guild_name_matches"][0]
    assert match["user_id"] == main.id
    assert match["source"] == "member"
    assert profile["alt_evidence_tier"] == "suspicious"
    assert profile["alt_risk_score"] >= 40
    assert profile["possible_alt_account"] is True
    assert profile["review_verdict"] == "POSSIBLE ALT LINK — REVIEW"
    assert "No action needed" not in profile["recommended_action"]
    cluster_ids = [row["user_id"] for row in profile["cluster_members"]]
    assert main.id in cluster_ids
    reasons_blob = " ".join(profile["reasons"])
    assert "legendarydata64" in reasons_blob
    assert "not identity proof" in reasons_blob


def test_decorated_display_name_variant_matches() -> None:
    # The main's username differs, but its decorated display name carries
    # the same core name; emoji must be stripped before comparison.
    main = FakeMember(
        user_id=2001,
        username="x420blazeit",
        display_name="🎃👻 LEGENDARYDATA64 👻🎃",
        age_days=800,
    )
    joiner = FakeMember(
        user_id=2002,
        username="legendarydata64phone",
        display_name="Legendarydata64 (phone)",
        age_days=305,
        default_avatar=True,
    )
    make_guild(main, joiner)

    profile = build(joiner)

    assert profile["strong_name_match_count"] == 1
    assert profile["guild_name_matches"][0]["user_id"] == main.id
    assert profile["alt_evidence_tier"] == "suspicious"


def test_alt_suffix_and_number_variants_match() -> None:
    for joiner_name in ("legendarydata64alt", "legendarydata642", "legendarydata64backup"):
        main = FakeMember(user_id=3001, username="legendarydata64", age_days=900)
        joiner = FakeMember(
            user_id=3002,
            username=joiner_name,
            age_days=400,
            default_avatar=True,
        )
        make_guild(main, joiner)
        profile = build(joiner)
        assert profile["strong_name_match_count"] == 1, joiner_name
        assert profile["alt_evidence_tier"] == "suspicious", joiner_name


def test_leetspeak_variant_matches() -> None:
    main = FakeMember(user_id=4001, username="legendarydata64", age_days=900)
    joiner = FakeMember(
        user_id=4002,
        username="l3gendarydata64",
        age_days=500,
        default_avatar=True,
    )
    make_guild(main, joiner)

    profile = build(joiner)

    assert profile["strong_name_match_count"] == 1
    assert profile["alt_evidence_tier"] == "suspicious"


def test_banned_account_match_is_flagged_as_ban_evasion_review() -> None:
    joiner = FakeMember(
        user_id=5002,
        username="legendarydata64phone",
        display_name="Legendarydata64 (phone)",
        age_days=305,
        default_avatar=True,
    )
    make_guild(joiner)
    banned = [
        {
            "user_id": 5001,
            "username": "legendarydata64",
            "display_name": "LEGENDARYDATA64",
            "source": "banned",
        }
    ]

    profile = build(joiner, extra_name_candidates=banned)

    assert profile["strong_name_match_count"] == 1
    assert profile["guild_name_matches"][0]["source"] == "banned"
    assert profile["alt_evidence_tier"] == "suspicious"
    assert any("BANNED" in reason for reason in profile["reasons"])


def test_departed_member_match_is_flagged() -> None:
    joiner = FakeMember(
        user_id=6002,
        username="legendarydata64alt",
        age_days=200,
        default_avatar=True,
    )
    make_guild(joiner)
    departed = [
        {
            "user_id": 6001,
            "username": "legendarydata64",
            "display_name": "Legendarydata64",
            "source": "departed",
        }
    ]

    profile = build(joiner, extra_name_candidates=departed)

    assert profile["strong_name_match_count"] == 1
    assert profile["guild_name_matches"][0]["source"] == "departed"
    assert profile["alt_evidence_tier"] == "suspicious"


def test_shared_common_fragment_is_not_a_strong_match() -> None:
    for main_name, joiner_name in (
        ("stoneyqueen", "stoneyking"),
        ("420lover", "420smoker"),
        ("darklady", "darklord"),
    ):
        main = FakeMember(user_id=7001, username=main_name, age_days=900)
        joiner = FakeMember(
            user_id=7002,
            username=joiner_name,
            age_days=600,
            default_avatar=False,
        )
        make_guild(main, joiner)
        profile = build(joiner)
        assert profile["strong_name_match_count"] == 0, (main_name, joiner_name)
        assert profile["alt_evidence_tier"] == "clear", (main_name, joiner_name)


def test_short_names_need_exact_match() -> None:
    main = FakeMember(user_id=8001, username="alex", age_days=900)
    joiner = FakeMember(user_id=8002, username="alexa", age_days=600)
    make_guild(main, joiner)

    profile = build(joiner)

    assert profile["strong_name_match_count"] == 0
    assert profile["alt_evidence_tier"] == "clear"
    assert score_name_pair("alex", "alexa") < 0.999


def test_member_never_matches_itself_or_bots() -> None:
    joiner = FakeMember(
        user_id=9002,
        username="legendarydata64phone",
        display_name="Legendarydata64 (phone)",
        age_days=305,
        default_avatar=True,
    )
    bot_member = FakeMember(
        user_id=9003,
        username="legendarydata64",
        age_days=900,
        bot=True,
    )
    guild = make_guild(joiner, bot_member)
    # The joiner is in the member cache too (it just joined).
    guild.members = [joiner, bot_member]

    profile = build(joiner)

    assert profile["strong_name_match_count"] == 0
    assert profile["alt_evidence_tier"] == "clear"


def test_staff_not_linked_suppresses_name_match() -> None:
    main = FakeMember(user_id=10001, username="legendarydata64", age_days=900)
    joiner = FakeMember(
        user_id=10002,
        username="legendarydata64phone",
        age_days=305,
        default_avatar=True,
    )
    make_guild(main, joiner)
    hard = empty_hard_context()
    hard["manual_not_linked_ids"] = {main.id}

    profile = build(joiner, hard_identity_context=hard)

    assert profile["strong_name_match_count"] == 0
    assert profile["alt_evidence_tier"] == "clear"


def test_risk_engine_treats_strong_name_match_as_suspicious_only() -> None:
    member = FakeMember(user_id=11002, username="whoever", age_days=900)
    out = enrich_member_risk_profile(
        member,
        {
            "username": "whoever",
            "risk_score": 40,
            "strong_name_match_count": 1,
            "reasons": [],
        },
    )
    assert out["alt_evidence_tier"] == "suspicious"
    assert out["review_verdict"] == "POSSIBLE ALT LINK — REVIEW"
    assert out["possible_alt_account"] is True


def test_assess_path_loads_extra_candidates_fail_soft() -> None:
    # The async join path must not explode when the guild has no bans()
    # iterator and no database: member-cache matches still apply.
    main = FakeMember(user_id=12001, username="legendarydata64", age_days=900)
    joiner = FakeMember(
        user_id=12002,
        username="legendarydata64phone",
        age_days=305,
        default_avatar=True,
    )
    make_guild(main, joiner)

    original = raidguard._load_hard_identity_context
    raidguard._load_hard_identity_context = lambda gid, uid: empty_hard_context()
    try:
        profile = asyncio.run(
            raidguard.assess_member_join_risk(joiner, join_context={}, record=False)
        )
    finally:
        raidguard._load_hard_identity_context = original

    assert profile["strong_name_match_count"] == 1
    assert profile["alt_evidence_tier"] == "suspicious"


def test_find_name_matches_returns_top_match_only_once() -> None:
    candidates = [
        {
            "user_id": 13001,
            "username": "legendarydata64",
            "display_name": "🎃👻LEGENDARYDATA64👻🎃",
            "source": "member",
        },
        {
            "user_id": 13001,
            "username": "legendarydata64",
            "display_name": "duplicate row",
            "source": "departed",
        },
        {
            "user_id": 13002,
            "username": "unrelatedperson",
            "display_name": "Unrelated",
            "source": "member",
        },
    ]
    matches = find_name_matches(
        ["legendarydata64phone", "Legendarydata64 (phone)"],
        candidates,
        exclude_user_ids={13999},
    )
    assert len(matches) == 1
    assert matches[0]["user_id"] == 13001
    assert matches[0]["similarity"] >= 0.9
