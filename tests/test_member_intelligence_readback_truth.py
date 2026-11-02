"""Member Intelligence: one actionable review, truthful DB coverage and sample limits."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from stoney_verify.commands_ext import member_command_center as center
from stoney_verify.tickets_new import member_context_service as svc

GUILD_ID = 123456789012345678
USER_ID = 223456789012345678
OLD = "2026-01-01T10:00:00+00:00"
NEW = "2026-11-01T10:00:00+00:00"


def test_account_review_menu_for_historical_ids_is_actionable():
    view = center.HistoricalIntelligenceTargetView(USER_ID, USER_ID)
    labels = {x.label for x in view.children}
    assert {"Stored History", "Connections", "Safety Reports",
            "External Sources", "Intelligence Menu"} <= labels
    assert len(view.children) <= 5


def test_no_profile_flag_cannot_be_assumed_in_guild():
    row = svc._normalize_guild_member_row({
        "guild_id": str(GUILD_ID), "user_id": str(USER_ID),
    })
    assert row["in_guild"] is None
    summary = svc._build_join_summary(row, [], [], {})
    assert summary["in_guild"] is None


def test_join_count_uses_maximum_of_actual_evidence_not_stale_counter():
    row = {"times_joined": 1, "times_left": 0, "joined_at": NEW}
    joins = [{"joined_at": NEW}, {"joined_at": OLD}]
    events = [{"event_type": "member_joined"}, {"event_type": "member_left"}]
    out = svc._build_join_summary(row, joins, events, {})
    assert out["times_joined"] == 2
    assert out["times_left"] == 1
    assert out["first_joined_at"] == OLD


def test_missing_profile_with_recorded_join_event_counts_as_recorded():
    out = svc._build_join_summary(None, [], [
        {"event_type": "member_joined", "created_at": NEW},
    ], {})
    assert out["times_joined"] == 1


def test_database_failure_is_explicit_not_clean_history(monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("DB unavailable")
    async def no_open(**kwargs):
        return None
    monkeypatch.setattr(svc, "get_guild_member_row", broken)
    monkeypatch.setattr(svc, "list_member_join_rows", broken)
    monkeypatch.setattr(svc, "list_member_event_rows", broken)
    monkeypatch.setattr(svc, "list_member_ticket_rows", broken)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open)
    snapshot = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID, event_limit=12,
        join_limit=8, ticket_limit=10,
    ))
    assert snapshot["ok"] is False
    assert all(v == "unavailable" for v in snapshot["source_status"].values())
    assert snapshot["sample_limits"] == {"joins": 8, "events": 12, "tickets": 10}


def test_saturated_join_window_finds_earliest_retained_record(monkeypatch):
    joined_at = [f"2026-11-{day:02d}T00:00:00+00:00" for day in range(9, 1, -1)]
    all_queries = []

    async def member(**kwargs):
        return {"joined_at": joined_at[0], "times_joined": 9}
    async def joins(**kwargs):
        all_queries.append(kwargs)
        return [{"joined_at": OLD}] if kwargs.get("descending") is False else [
            {"joined_at": x} for x in joined_at
        ]
    async def empty(**kwargs):
        return []
    async def no_open(**kwargs):
        return None
    monkeypatch.setattr(svc, "get_guild_member_row", member)
    monkeypatch.setattr(svc, "list_member_join_rows", joins)
    monkeypatch.setattr(svc, "list_member_event_rows", empty)
    monkeypatch.setattr(svc, "list_member_ticket_rows", empty)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open)
    snap = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID, join_limit=8,
    ))
    assert snap["ok"] is True
    assert snap["join_summary"]["first_joined_at"] == OLD
    assert any(q.get("descending") is False and q["limit"] == 1 for q in all_queries)


def test_stale_profile_invite_does_not_block_same_visit_event_recovery(monkeypatch):
    recent = NEW
    event = {
        "event_type": "member_joined",
        "created_at": recent,
        "metadata": {
            "joined_at": recent, "invite_code": "CURRENT",
            "entry_method": "invite", "entry_truth_quality": "confirmed",
        },
    }
    calls = []
    async def member(**kwargs):
        return {"joined_at": OLD, "invite_code": "STALE"}
    async def joins(**kwargs):
        return [{"joined_at": NEW, "entry_method": "invite_unresolved"}]
    async def events(**kwargs):
        calls.append(kwargs.get("event_types"))
        return [event] if kwargs.get("event_types") else []
    async def empty(**kwargs):
        return []
    async def no_open(**kwargs):
        return None
    monkeypatch.setattr(svc, "get_guild_member_row", member)
    monkeypatch.setattr(svc, "list_member_join_rows", joins)
    monkeypatch.setattr(svc, "list_member_event_rows", events)
    monkeypatch.setattr(svc, "list_member_ticket_rows", empty)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open)
    snap = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID,
    ))
    assert calls == [None, ("member_joined",)]
    assert snap["relationships"]["invite_code"] == "CURRENT"
    assert snap["relationships"]["attribution_record"] == "member_joined_event"


def test_history_embed_discloses_unavailable_sources_and_actual_activity():
    snap = {
        "member": {
            "display_name": "Review target", "synced_at": "2026-09-01T00:00:00Z",
        },
        "source_status": {
            "profile": "available", "joins": "unavailable",
            "events": "sampled", "tickets": "sampled",
        },
        "sample_limits": {"joins": 8, "events": 12, "tickets": 10},
        "join_summary": {"times_joined": 0, "times_left": 0},
        "relationships": {"entry_truth_quality": "unknown"},
        "event_summary": {"total": 1, "type_counts": {"member_warned": 1}},
        "ticket_summary": {"total": 0},
        "name_history": {"all_names": ["EarlierDisplay"]},
        "recent_events": [{
            "event_type": "member_warned",
            "title": "Abusive spam",
            "created_at": "2026-10-29T00:00:00Z",
        }],
    }
    embed = center._historical_member_embed(USER_ID, snap, [], presence="unavailable")
    fields = {x.name: x.value for x in embed.fields}
    assert "unknown" in fields["Membership History"]
    assert "Could not read" in fields["Evidence coverage & freshness"]
    assert "joins" in fields["Evidence coverage & freshness"]
    assert "2026-09-01" in fields["Evidence coverage & freshness"]
    assert "EarlierDisplay" in fields["Membership History"]
    assert "Abusive spam" in fields["Activity & Moderation Evidence"]
    assert len(embed.fields) <= 6  # compact on mobile, details retained


def test_ticket_repository_strict_mode_exposes_error_without_changing_existing_callers(monkeypatch):
    from unittest.mock import AsyncMock
    import pytest
    from stoney_verify.tickets_new import repository
    broken = AsyncMock(side_effect=RuntimeError("Read unavailable"))
    monkeypatch.setattr(repository, "_select_tickets_for_owner_async", broken)

    old_behavior = asyncio.run(repository.list_tickets_for_owner(
        guild_id=GUILD_ID, owner_id=USER_ID,
    ))
    assert old_behavior == []
    with pytest.raises(RuntimeError, match="Read unavailable"):
        asyncio.run(repository.list_tickets_for_owner(
            guild_id=GUILD_ID, owner_id=USER_ID, strict=True,
        ))


def test_stored_history_buttons_do_not_open_a_second_disconnected_panel():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] /
           "stoney_verify/commands_ext/member_command_center.py").read_text()
    tree = ast.parse(src)
    selector = next(x for x in tree.body if isinstance(x, ast.ClassDef)
                    and x.name == "IntelligenceMemberSelect")
    callback = next(x for x in selector.body
                    if isinstance(x, ast.AsyncFunctionDef) and x.name == "callback")
    code = ast.unparse(callback)
    assert code.count("_show_stored_member_history(") == 1
    assert "view=view" in code
    assert "live_member=member" in code
    assert "await _replace_panel(" not in code
    history = next(x for x in tree.body if isinstance(x, ast.AsyncFunctionDef)
                   and x.name == "_show_stored_member_history")
    history_src = ast.unparse(history)
    assert "await _replace_panel(" in history_src
    assert "if view is not None" in history_src
    assert "Live Discord facts (checked now)" in history_src


def test_activity_feed_lookup_uses_exact_discord_target_and_guild(monkeypatch):
    from types import SimpleNamespace
    calls = []

    class Query:
        def table(self, name):
            calls.append(("table", name))
            return self
        def select(self, fields):
            calls.append(("select", fields))
            return self
        def eq(self, key, value):
            calls.append(("eq", key, value))
            return self
        def order(self, key, desc):
            calls.append(("order", key, desc))
            return self
        def limit(self, limit):
            calls.append(("limit", limit))
            return self
        def execute(self):
            return SimpleNamespace(data=[{
                "id": "e123", "event_family": "moderation",
                "event_type": "ban", "title": "Confirmed staff action",
                "reason": "Verified event", "created_at": NEW,
                "source": "staff_action_logs",
            }])

    monkeypatch.setattr(svc, "_sb", lambda: Query())
    async def sync_call(_name, fn):
        return fn()
    monkeypatch.setattr(svc, "_run_db_op", sync_call)
    rows = asyncio.run(svc.list_member_activity_feed_rows(
        guild_id=GUILD_ID, user_id=USER_ID, strict=True,
    ))
    assert rows[0]["event_family"] == "moderation"
    assert rows[0]["event_type"] == "ban"
    assert ("table", "activity_feed_events") in calls
    assert ("eq", "guild_id", str(GUILD_ID)) in calls
    assert ("eq", "target_user_id", str(USER_ID)) in calls
    assert not any(x[1] in {"actor_user_id", "actor_id", "target_id"}
                   for x in calls if x[0] == "eq")
    assert ("limit", 30) in calls


def test_activity_ledger_uses_exact_member_scope(monkeypatch):
    from types import SimpleNamespace
    calls = []

    class Query:
        def table(self, name):
            calls.append(("table", name))
            return self
        def select(self, fields):
            calls.append(("select", fields))
            return self
        def eq(self, key, value):
            calls.append(("eq", key, value))
            return self
        def limit(self, number):
            calls.append(("limit", number))
            return self
        def execute(self):
            return SimpleNamespace(data=[{
                "last_activity_at": NEW, "last_activity_type": "message",
                "first_observed_at": OLD, "updated_at": NEW,
            }])

    monkeypatch.setattr(svc, "_sb", lambda: Query())
    async def sync_call(_name, fn):
        return fn()
    monkeypatch.setattr(svc, "_run_db_op", sync_call)
    row = asyncio.run(svc.get_member_activity_ledger_row(
        guild_id=GUILD_ID, user_id=USER_ID, strict=True,
    ))
    assert row["last_activity_type"] == "message"
    assert ("table", "member_activity_ledger") in calls
    assert ("eq", "guild_id", str(GUILD_ID)) in calls
    assert ("eq", "user_id", str(USER_ID)) in calls
    assert ("limit", 1) in calls


def test_extended_intelligence_opt_in_and_independent_counts(monkeypatch):
    feed_calls = []
    async def profile(**kwargs):
        return {"joined_at": NEW, "times_joined": 1}
    async def no_rows(**kwargs):
        return []
    async def no_open(**kwargs):
        return None
    async def feed(**kwargs):
        feed_calls.append(kwargs)
        return [
            {"event_family": "moderation", "event_type": "ban",
             "title": "Ban for spam", "created_at": NEW},
            {"event_family": "member", "event_type": "member_joined",
             "created_at": NEW},
        ]
    async def ledger(**kwargs):
        return {"last_activity_at": NEW, "last_activity_type": "message"}
    monkeypatch.setattr(svc, "get_guild_member_row", profile)
    monkeypatch.setattr(svc, "list_member_join_rows", no_rows)
    monkeypatch.setattr(svc, "list_member_event_rows", no_rows)
    monkeypatch.setattr(svc, "list_member_ticket_rows", no_rows)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open)
    monkeypatch.setattr(svc, "list_member_activity_feed_rows", feed)
    monkeypatch.setattr(svc, "get_member_activity_ledger_row", ledger)
    normal = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID,
    ))
    assert not feed_calls
    assert normal["activity_feed_summary"]["total"] == 0
    review = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID, include_extended_intelligence=True,
    ))
    assert feed_calls[0]["strict"] is True
    assert feed_calls[0]["guild_id"] == str(GUILD_ID)
    assert feed_calls[0]["user_id"] == str(USER_ID)
    assert review["activity_feed_summary"] == {"total": 2, "moderation_count": 1}
    assert review["event_summary"]["total"] == 0
    assert review["activity_ledger"]["last_activity_type"] == "message"
    assert review["sample_limits"]["activity_feed"] == 30


def test_extended_feed_outage_shows_unavailable_not_zero_as_safe(monkeypatch):
    async def profile(**kwargs):
        return {"joined_at": NEW}
    async def empty(**kwargs):
        return []
    async def broken(**kwargs):
        raise RuntimeError("Activity feed unavailable")
    async def no_open(**kwargs):
        return None
    monkeypatch.setattr(svc, "get_guild_member_row", profile)
    monkeypatch.setattr(svc, "list_member_join_rows", empty)
    monkeypatch.setattr(svc, "list_member_event_rows", empty)
    monkeypatch.setattr(svc, "list_member_ticket_rows", empty)
    monkeypatch.setattr(svc, "list_member_activity_feed_rows", broken)
    monkeypatch.setattr(svc, "get_member_activity_ledger_row", broken)
    monkeypatch.setattr(svc, "find_open_ticket_for_owner", no_open)
    review = asyncio.run(svc.get_member_context_snapshot(
        guild_id=GUILD_ID, user_id=USER_ID, include_extended_intelligence=True,
    ))
    assert review["ok"] is True
    assert review["source_status"]["activity_feed"] == "unavailable"
    assert review["source_status"]["activity_ledger"] == "unavailable"
    embed = center._historical_member_embed(USER_ID, review, [], presence="unavailable")
    fields = {field.name: field.value for field in embed.fields}
    assert "activity_feed" in fields["Evidence coverage & freshness"]
    assert "activity_ledger" in fields["Evidence coverage & freshness"]


def test_compact_history_renders_separately_labeled_feed_and_member_events():
    snapshot = {
        "member": {"username": "Target"},
        "join_summary": {"times_joined": 1, "times_left": 0},
        "relationships": {"entry_method": "invite_unresolved"},
        "event_summary": {"total": 1, "moderation_count": 0,
                          "latest_event_type": "member_joined"},
        "activity_feed_summary": {"total": 2, "moderation_count": 1},
        "recent_feed_events": [
            {"event_family": "moderation", "event_type": "ban",
             "created_at": NEW, "title": "Documented moderation"},
        ],
        "activity_ledger": {"last_activity_at": NEW, "last_activity_type": "message"},
        "sample_limits": {"events": 30, "activity_feed": 30,
                          "joins": 15, "tickets": 15},
        "source_status": {"profile": "available", "events": "sampled",
                          "activity_feed": "sampled",
                          "activity_ledger": "sampled"},
        "ticket_summary": {"total": 0},
    }
    embed = center._historical_member_embed(USER_ID, snapshot, [], presence="unavailable")
    fields = {f.name: f.value for f in embed.fields}
    assert len(embed.fields) <= 6
    assert "Tracked activity" in fields["Membership History"]
    assert "message" in fields["Membership History"]
    activity = fields["Activity & Moderation Evidence"]
    assert "Member events" in activity
    assert "Activity feed" in activity
    assert "Documented moderation" in activity
    assert "2" in activity and "1" in activity


def test_failed_readbacks_are_never_displayed_as_zero_activity_or_tickets():
    snapshot = {
        "member": {"display_name": "Unverified history"},
        "join_summary": {"times_joined": 0, "times_left": 0},
        "event_summary": {"total": 0, "moderation_count": 0},
        "activity_feed_summary": {"total": 0, "moderation_count": 0},
        "ticket_summary": {"total": 0},
        "source_status": {
            "profile": "unavailable", "joins": "unavailable",
            "events": "unavailable", "tickets": "unavailable",
            "activity_feed": "unavailable", "activity_ledger": "unavailable",
        },
        "sample_limits": {
            "joins": 15, "events": 30, "tickets": 15, "activity_feed": 30,
        },
    }
    embed = center._historical_member_embed(
        USER_ID, snapshot, [{"metadata": {"verdict": "watch"}}],
        presence="unavailable",
    )
    fields = {field.name: field.value for field in embed.fields}
    assert "**unavailable**" in fields["Activity & Moderation Evidence"]
    assert "**unavailable**" in fields["Tickets & Staff Decisions"]
    assert "unavailable" in fields["Membership History"]
    assert "activity_feed" in fields["Evidence coverage & freshness"]
    assert len(embed.fields) <= 6
