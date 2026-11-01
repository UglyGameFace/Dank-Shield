from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.member_connection_intelligence import (
    build_connection_report,
    _read_connection_report,
    _recorded_join_event,
    verified_ban_connections,
    enrich_connection_ban_status,
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
    assert report["coverage"] == "partial"
    assert report["matches"] == []
    assert len(tables) == 11
    assert "identity_proof_matches" not in tables
    assert report["history_gap"].startswith("No recorded relationship")

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


def test_ban_enrichment_keeps_identity_and_ban_status_separate() -> None:
    import discord

    banned_id = OTHER
    unbanned_id = "303030303030303030"
    report = {
        "coverage": "available",
        "matches": [
            {"user_id": banned_id, "category": "documented server relationship", "evidence": [{"kind": "social"}]},
            {"user_id": unbanned_id, "category": "unverified join relationship", "evidence": [{"kind": "weak"}]},
        ],
    }

    class Guild:
        async def fetch_ban(self, user):
            if user.id == int(banned_id):
                return SimpleNamespace(user=SimpleNamespace(id=user.id))
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Ban")

    result = asyncio.run(enrich_connection_ban_status(Guild(), report))
    assert [row["ban_status"] for row in result["matches"]] == ["banned", "not_banned"]
    assert [row["category"] for row in result["matches"]] == [
        "documented server relationship", "unverified join relationship"
    ]
    assert result["matches"][1]["evidence"] == [{"kind": "weak"}]
    assert result["coverage"] == "available"


def test_ban_lookup_permission_failure_marks_partial_without_erasing_evidence() -> None:
    import discord

    report = {"coverage": "available", "matches": [
        {"user_id": OTHER, "evidence": [{"label": "Recorded vouch", "kind": "social"}]}
    ]}

    class Guild:
        async def fetch_ban(self, user):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing permission")

    result = asyncio.run(enrich_connection_ban_status(Guild(), report))
    assert result["matches"][0]["ban_status"] == "unavailable"
    assert result["coverage"] == "partial"
    assert result["matches"][0]["evidence"][0]["label"] == "Recorded vouch"


def test_unexpected_ban_lookup_failure_keeps_report_and_marks_error() -> None:
    report = {"coverage": "available", "matches": [{"user_id": OTHER}]}

    class Guild:
        async def fetch_ban(self, user):
            raise RuntimeError("Discord connection unavailable")

    result = asyncio.run(enrich_connection_ban_status(Guild(), report))
    assert result["matches"][0]["ban_status"] == "unavailable"
    assert result["coverage"] == "partial"
    assert "RuntimeError" in result["errors"][0]


def test_invalid_ban_identity_does_not_call_discord() -> None:
    report = {"coverage": "available", "matches": [{"user_id": "invalid"}]}

    class Guild:
        async def fetch_ban(self, user):
            raise AssertionError("No lookup for invalid account IDs")

    result = asyncio.run(enrich_connection_ban_status(Guild(), report))
    assert result["matches"][0]["ban_status"] == "unknown"


def _joined_event(*, member: str, actor: str, inviter: str = "",
                  voucher: str = "", quality: str = "confirmed",
                  event_type: str = "member_joined",
                  joined_at: str = "2026-10-20T11:45:10+00:00",
                  created_at: str = "2026-10-20T11:45:12+00:00",
                  conflicted: bool = False) -> dict:
    return {
        "guild_id": TARGET,
        "user_id": member,
        "actor_id": actor,
        "event_type": event_type,
        "created_at": created_at,
        "metadata": {
            "joined_at": joined_at,
            "invited_by": inviter,
            "vouched_by": voucher,
            "entry_truth_quality": quality,
            "entry_conflict": conflicted,
        },
    }


def test_original_join_event_requires_correct_type_and_timestamp() -> None:
    valid = _joined_event(member=TARGET, actor=OTHER, inviter=OTHER)
    assert _recorded_join_event(valid)["invited_by"] == OTHER
    assert _recorded_join_event({**valid, "event_type": "member_banned"}) is None
    assert _recorded_join_event({**valid, "created_at": "2026-10-22T11:45:12+00:00"}) is None
    assert _recorded_join_event({**valid, "metadata": {"invited_by": OTHER}}) is None
    assert _recorded_join_event({**valid, "metadata": "not a JSON object"}) is None
    missing_conflict = {**valid, "metadata": {
        "joined_at": "2026-10-20T11:45:10+00:00",
        "invited_by": OTHER, "entry_truth_quality": "confirmed",
    }}
    assert _recorded_join_event(missing_conflict)["entry_conflict"] is True


def test_historical_events_preserve_quality_and_identity_boundaries() -> None:
    event = {
        "user_id": OTHER, "invited_by": TARGET, "entry_truth_quality": "partial",
        "entry_conflict": False, "vouched_by": "",
    }
    report = build_connection_report(
        target_id=TARGET, source={"event_related": [event]},
    )
    assert report["matches"][0]["category"] == "unverified join relationship"
    assert report["matches"][0]["evidence"][0]["source"] == "original member_joined event"

    event["entry_truth_quality"] = "confirmed"
    report = build_connection_report(
        target_id=TARGET, source={"event_related": [event]},
    )
    assert report["matches"][0]["category"] == "documented server relationship"
    assert report["finding"] == "relationships_only_not_leak_proof"

    event["entry_conflict"] = True
    report = build_connection_report(target_id=TARGET, source={"event_related": [event]})
    assert report["matches"][0]["category"] == "unverified join relationship"


def test_missing_join_rows_recover_recorded_forward_and_reverse_events(monkeypatch) -> None:
    # A member's event survives a failed member_joins insert. Reverse queries
    # are limited to actor_id, and actor_id can be a staff approver, so require
    # the original metadata to explicitly identify an inviter or voucher.
    rows = [
        _joined_event(member=TARGET, actor=OTHER, inviter=OTHER),
        _joined_event(member=OTHER, actor=TARGET, inviter=TARGET),
        _joined_event(member="303030303030303030", actor=TARGET,
                      inviter=OTHER),  # approval, NOT an invite by TARGET
        # The actor is OTHER (the inviter), but TARGET is the voucher.
        _joined_event(member="505050505050505050", actor=OTHER,
                      inviter=OTHER, voucher=TARGET),
        _joined_event(member="404040404040404040", actor=OTHER,
                      inviter=OTHER, event_type="member_banned"),
    ]

    class Query:
        def __init__(self, table: str):
            self.table = table
            self.filters = {}

        def select(self, columns):
            assert columns
            return self

        def eq(self, column, value):
            self.filters[column] = str(value)
            return self

        def limit(self, amount):
            assert amount <= 40
            return self

        def execute(self):
            if self.table != "member_events":
                return SimpleNamespace(data=[])
            filtered = [
                row for row in rows
                if all(
                    (str((row.get("metadata") or {}).get(key.split("->>", 1)[1]))
                     if key.startswith("metadata->>") else str(row.get(key))) == value
                    for key, value in self.filters.items()
                )
            ]
            return SimpleNamespace(data=filtered)

    class DB:
        def table(self, name):
            assert name in {"guild_members", "member_joins", "manual_alt_links", "member_events"}
            return Query(name)

    monkeypatch.setattr("stoney_verify.globals.get_supabase", lambda: DB())
    report = _read_connection_report(int(TARGET), int(TARGET))
    assert report["history_status"] == "events_only"
    assert report["recovered_join_events"] == 3
    assert report["history_events"] == 3
    assert report["coverage"] == "partial"  # History is still missing.
    assert report["history_gap"].startswith("No join-history row")
    assert [item["user_id"] for item in report["matches"]] == [OTHER, "505050505050505050"]
    assert len(report["matches"][0]["evidence"]) == 2


def test_known_account_relationships_require_distinct_proofs() -> None:
    # Two reviewed identities and two documented invitations must never
    # become an automatically confirmed four-person alt cluster.
    candidate_two = "303030303030303030"
    social_one = "404040404040404040"
    social_two = "505050505050505050"
    report = build_connection_report(
        target_id=TARGET,
        source={
            "manual_a": [{"user_a_id": TARGET, "user_b_id": OTHER,
                          "link_type": "confirmed_duplicate", "status": "active"},
                         {"user_a_id": TARGET, "user_b_id": candidate_two,
                          "link_type": "same_person_likely", "status": "active"}],
            "event_related": [
                {"user_id": social_one, "invited_by": TARGET,
                 "entry_truth_quality": "confirmed"},
                {"user_id": social_two, "vouched_by": TARGET,
                 "entry_truth_quality": "partial"},
            ],
        },
    )
    categories = {row["user_id"]: row["category"] for row in report["matches"]}
    assert categories[OTHER] == "reviewed identity record"
    assert categories[candidate_two] == "possible identity link"
    assert categories[social_one] == "documented server relationship"
    assert categories[social_two] == "documented server relationship"
    assert report["finding"] == "relationships_only_not_leak_proof"
