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
    assert len(tables) == 13
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


def test_connection_report_paginates_all_loaded_matches() -> None:
    from stoney_verify.commands_ext.member_command_center import (
        _connection_embed, ConnectionReportView,
    )

    rows = [
        {
            "user_id": str(600000000000000000 + number),
            "category": "documented server relationship",
            "evidence": [{"label": "Original recorded join evidence", "kind": "social"}],
            "ban_status": "banned" if number == 11 else "not_banned",
        }
        for number in range(12)
    ]
    report = {
        "target_user_id": TARGET, "matches": rows, "total_matches": 12,
        "coverage": "available", "errors": [], "truncated": False,
    }
    first = _connection_embed(report, page=0)
    second = _connection_embed(report, page=1)
    names_first = [field.name for field in first.fields]
    names_second = [field.name for field in second.fields]
    assert "Page 1/2" in first.fields[0].value
    assert "Page 2/2" in second.fields[0].value
    assert len([name for name in names_first if name.startswith("Account ")]) == 6
    assert len([name for name in names_second if name.startswith("Account ")]) == 6
    assert {name for name in names_first if name.startswith("Account ")}.isdisjoint(
        {name for name in names_second if name.startswith("Account ")}
    )
    assert any("Discord ban: Banned" in field.value for field in second.fields)
    start_view = ConnectionReportView(123, int(TARGET), report)
    end_view = ConnectionReportView(123, int(TARGET), report, page=1)
    assert start_view.previous.disabled and not start_view.next_page.disabled
    assert end_view.next_page.disabled and not end_view.previous.disabled


def test_owner_affiliation_does_not_turn_association_into_duplicate_identity() -> None:
    from stoney_verify.member_connection_intelligence import build_connection_report
    target = TARGET
    report = build_connection_report(target_id=target, source={
        "owner_affiliations": [
            {"user_id": OTHER, "mode": "known"},
            {"user_id": "303030303030303030", "mode": "possible"},
            {"user_id": "404040404040404040", "mode": "cleared"},
        ],
    })
    categories = {row["user_id"]: row["category"] for row in report["matches"]}
    assert categories[OTHER] == "owner-reported known affiliate"
    assert categories["303030303030303030"] == "owner-reported possible affiliate"
    assert categories["404040404040404040"] == "owner-cleared association"
    assert all(row["category"] not in {"reviewed identity record", "possible identity link"}
               for row in report["matches"])
    assert report["finding"] == "relationships_only_not_leak_proof"


def test_owner_affiliation_is_append_only_separate_from_manual_alt_links(monkeypatch) -> None:
    import pytest
    from stoney_verify.member_connection_intelligence import save_owner_affiliation

    inserts = []
    class DB:
        def table(self, name):
            assert name == "member_events"
            return self
        def insert(self, row):
            inserts.append(dict(row))
            return self
        def execute(self):
            return SimpleNamespace(data=[{"id": "example"}])

    monkeypatch.setattr("stoney_verify.globals.get_supabase", lambda: DB())
    save_owner_affiliation(
        guild_id=int(TARGET), target_id=int(OTHER),
        other_id=int(TARGET), actor_id=int(TARGET),
        mode="known", reason="Manually reviewed and acknowledged relationship.",
    )
    assert len(inserts) == 1
    record = inserts[0]
    assert record["event_type"] == "owner_affiliation_review"
    assert record["user_id"] == TARGET
    assert record["metadata"]["other_user_id"] == OTHER
    assert record["metadata"]["does_not_prove_identity"] is True
    assert record["metadata"]["origin"] == "owner_report"
    with pytest.raises(ValueError):
        save_owner_affiliation(guild_id=int(TARGET), target_id=int(OTHER),
                               other_id=int(OTHER), actor_id=int(TARGET),
                               mode="known", reason="Should never store self-links")
    with pytest.raises(ValueError):
        save_owner_affiliation(guild_id=int(TARGET), target_id=int(OTHER),
                               other_id=int(TARGET), actor_id=int(TARGET),
                               mode="confirmed_duplicate", reason="Not supported here")


def test_owner_affiliation_review_latest_status_preserved_from_evidence_log() -> None:
    from stoney_verify.member_connection_intelligence import _read_owner_affiliations

    reviews = [
        {"id": "a", "event_type": "owner_affiliation_review", "user_id": TARGET,
         "created_at": "2026-10-25T10:00:00+00:00",
         "metadata": {"origin": "owner_report", "other_user_id": OTHER, "relation": "known"}},
        {"id": "b", "event_type": "owner_affiliation_review", "user_id": TARGET,
         "created_at": "2026-10-26T10:00:00+00:00",
         "metadata": {"origin": "owner_report", "other_user_id": OTHER, "relation": "cleared"}},
        {"id": "c", "event_type": "owner_affiliation_review", "user_id": TARGET,
         "created_at": "2026-10-27T10:00:00+00:00",
         "metadata": {"origin": "owner_report", "other_user_id": "303030303030303030", "relation": "possible"}},
    ]
    class Query:
        def __init__(self):
            self.filters = {}
        def select(self, _columns):
            return self
        def eq(self, key, value):
            self.filters[key] = value
            return self
        def limit(self, amount):
            assert amount == 40
            return self
        def execute(self):
            if self.filters.get("user_id") == TARGET:
                return SimpleNamespace(data=reviews)
            return SimpleNamespace(data=[])
    class DB:
        def table(self, table):
            assert table == "member_events"
            return Query()

    actual, errors, partial = _read_owner_affiliations(DB(), TARGET, TARGET)
    assert not errors and not partial
    by_user = {item["user_id"]: item["mode"] for item in actual}
    assert by_user == {OTHER: "cleared", "303030303030303030": "possible"}


def test_affiliate_action_is_server_owner_only_and_uses_existing_private_view() -> None:
    from stoney_verify.commands_ext.member_command_center import (
        _is_guild_owner_for_affiliations, ConnectionReportView,
        OwnerAffiliationChoiceView, OwnerAffiliationTargetView,
        OwnerAffiliationUserSelect, OwnerAffiliationModal,
    )

    owner = SimpleNamespace(user=SimpleNamespace(id=123),
                            guild=SimpleNamespace(owner_id=123))
    moderator = SimpleNamespace(user=SimpleNamespace(id=456),
                                guild=SimpleNamespace(owner_id=123))
    assert _is_guild_owner_for_affiliations(owner)
    assert not _is_guild_owner_for_affiliations(moderator)
    assert not _is_guild_owner_for_affiliations(SimpleNamespace(
        user=owner.user, guild=None,
    ))
    review_view = OwnerAffiliationChoiceView(123, int(TARGET))
    select = next(item for item in review_view.children
                  if getattr(item, "placeholder", None) == "Choose relationship review")
    assert {option.value for option in select.options} == {"known", "possible", "cleared"}
    assert len(review_view.children) == 2
    choice = OwnerAffiliationTargetView(123, int(TARGET), "known")
    assert len(choice.children) == 3
    assert sum(isinstance(item, OwnerAffiliationUserSelect) for item in choice.children) == 1
    assert {"Enter Account ID", "Back to Relationship"} <= {
        getattr(item, "label", "") for item in choice.children
    }
    modal = OwnerAffiliationModal(int(TARGET), "known")
    prefilled = OwnerAffiliationModal(int(TARGET), "known", other_id=int(OTHER))
    assert len(modal.children) == 2
    assert str(prefilled.other_account.default) == OTHER
    report = {"target_user_id": TARGET, "matches": [], "total_matches": 0,
              "coverage": "partial", "errors": [], "truncated": False}
    view = ConnectionReportView(123, int(TARGET), report)
    assert len(view.children) <= 9
    assert "Record Affiliate" in {getattr(item, "label", "") for item in view.children}
