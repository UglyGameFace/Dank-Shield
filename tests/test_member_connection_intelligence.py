from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.member_connection_intelligence import (
    build_connection_report,
    _read_connection_report,
    verified_ban_connections,
)


TARGET = "101010101010101010"
OTHER = "202020202020202020"


def test_confirmed_invite_is_a_social_link_not_a_leak_or_alt() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={"invited": [{
            "user_id": OTHER,
            "invited_by": TARGET,
            "entry_truth_quality": "confirmed",
            "entry_conflict": False,
        }]},
    )
    assert report["coverage"] == "available"
    assert report["total_matches"] == 1
    assert report["matches"][0]["category"] == "documented server relationship"
    assert report["finding"] == "relationships_only_not_leak_proof"


def test_historical_invite_and_vouch_evidence_are_reused() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={
            "join_invited": [{"user_id": OTHER, "invited_by": TARGET, "entry_truth_quality": "confirmed"}],
            "join_vouched": [{"user_id": OTHER, "vouched_by": TARGET}],
        },
    )
    assert report["total_matches"] == 1
    assert len(report["matches"][0]["evidence"]) == 2


def test_conflicting_invite_not_upgraded_to_confirmed() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={"invited": [{
            "user_id": OTHER,
            "invited_by": TARGET,
            "entry_truth_quality": "confirmed",
            "entry_conflict": True,
        }]},
    )
    assert report["total_matches"] == 0


def test_reverse_inviter_link_does_not_invent_verified_relationship() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={"target": [{
            "user_id": TARGET,
            "invited_by": OTHER,
            "entry_truth_quality": "partial",
            "entry_conflict": False,
        }]},
    )
    assert report["total_matches"] == 1
    assert report["matches"][0]["category"] == "unverified join relationship"
    assert report["matches"][0]["evidence"][0]["kind"] == "weak"


def test_reverse_inviter_link_can_be_confirmed_with_source_quality() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={"join_target": [{
            "user_id": TARGET,
            "invited_by": OTHER,
            "entry_truth_quality": "confirmed",
            "entry_conflict": False,
        }]},
    )
    assert report["matches"][0]["category"] == "documented server relationship"


def test_query_limit_marks_coverage_partial() -> None:
    rows = [{"user_id": str(303030303030303030 + i), "vouched_by": TARGET}
            for i in range(40)]
    report = build_connection_report(target_id=TARGET, source={"vouched": rows})
    assert report["truncated"]
    assert report["coverage"] == "partial"


def test_reviewed_identity_is_different_from_social_evidence() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={
            "manual_a": [{
                "user_a_id": TARGET, "user_b_id": OTHER,
                "status": "active", "link_type": "confirmed_duplicate",
            }],
            "vouched": [{"user_id": OTHER, "vouched_by": TARGET}],
        },
    )
    assert report["matches"][0]["category"] == "reviewed identity record"
    assert len(report["matches"][0]["evidence"]) == 2


def test_not_linked_verdict_suppresses_identity_claim_not_real_invite() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={
            "manual_b": [{
                "user_a_id": OTHER, "user_b_id": TARGET,
                "status": "active", "link_type": "not_linked",
            }],
            "manual_a": [{"user_a_id": TARGET, "user_b_id": OTHER, "status": "active", "link_type": "confirmed_duplicate"}],
            "invited": [{"user_id": OTHER, "invited_by": TARGET, "entry_truth_quality": "confirmed"}],
        },
    )
    item = report["matches"][0]
    assert item["identity_review"] == "staff_marked_not_same_person"
    assert item["category"] == "documented server relationship"
    assert all(e["kind"] != "reviewed_identity" for e in item["evidence"])


def test_missing_data_cannot_be_displayed_as_complete() -> None:
    report = build_connection_report(
        target_id=TARGET,
        source={},
        errors=["database unavailable"],
    )
    assert report["matches"] == []
    assert report["coverage"] == "partial"


def test_self_matches_discarded_and_caps_are_explicit() -> None:
    rows = [{"user_id": TARGET, "vouched_by": TARGET}]
    rows += [{"user_id": str(303030303030303030 + i), "vouched_by": TARGET} for i in range(25)]
    report = build_connection_report(target_id=TARGET, source={"vouched": rows})
    assert report["total_matches"] == 25
    assert report["truncated"]
    assert len(report["matches"]) == 12
    assert all(row["user_id"] != TARGET for row in report["matches"])



def test_live_reader_uses_only_tables_in_dedicated_supabase(monkeypatch) -> None:
    """Missing identity_proof_matches must not create two failing queries per review."""
    tables = []

    class Query:
        def select(self, columns):
            assert columns
            return self

        def eq(self, column, value):
            assert column and value
            return self

        def limit(self, count):
            assert count <= 40
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class DB:
        def table(self, name):
            tables.append(name)
            assert name in {"guild_members", "member_joins", "manual_alt_links", "member_events"}
            return Query()

    monkeypatch.setattr("stoney_verify.globals.get_supabase", lambda: DB())
    report = _read_connection_report(int(TARGET), int(OTHER))
    assert report["coverage"] == "available"
    assert report["matches"] == []
    assert len(tables) == 9
    assert "identity_proof_matches" not in tables\n    assert report["history_gap"].startswith("No relationship evidence")

def test_unverified_invitations_and_weak_names_do_not_trigger_ban_assertions() -> None:
    class Guild:
        calls = 0

        async def fetch_ban(self, user: object) -> None:
            self.calls += 1
            raise AssertionError("No verified link should request a ban lookup")

    guild = Guild()
    matched = asyncio.run(verified_ban_connections(
        guild,
        {"invited_by": OTHER, "entry_truth_quality": "partial"},
        {"cluster_members": [{"user_id": OTHER, "reason": "similar_username"}]},
    ))
    assert matched == []
    assert guild.calls == 0


def test_verified_link_requires_actual_discord_ban() -> None:
    class Guild:
        async def fetch_ban(self, user: object) -> object:
            if user.id == int(OTHER):
                return SimpleNamespace(user=SimpleNamespace(id=user.id))
            raise LookupError("not banned")

    matched = asyncio.run(verified_ban_connections(
        Guild(), {"invited_by": OTHER, "entry_truth_quality": "confirmed"}, {}
    ))
    assert matched == [{
        "user_id": OTHER,
        "basis": "confirmed Discord ban plus existing link evidence",
    }]


def test_single_existing_staff_surface_and_ban_join_hooks() -> None:
    root = Path(__file__).resolve().parents[1]
    center = (root / "stoney_verify/commands_ext/member_command_center.py").read_text()
    events = (root / "stoney_verify/events.py").read_text()
    assert "Recent Ban Connections" in center
    assert "class RecentBanConnectionView" in center
    assert "class ConnectionByIdModal" in center
    assert "await require_review(interaction)" in center
    assert "analyze_member_connections(guild.id, user.id)" in events
    assert "verified_ban_connections(guild, join_context, risk_profile)" in events
