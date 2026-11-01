from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify import network_safety as network


A = 123456789012345678
B = 223456789012345678
C = 323456789012345678
SUBJECT = 423456789012345678
OWNER_A = 523456789012345678
OWNER_B = 623456789012345678


def row(ident: str, source: int, *, action: str = "incident",
        parent: str | None = None, reason: str = "Repeated harassment",
        created: str = "2026-11-01T00:00:00+00:00") -> dict:
    return {
        "id": ident, "reporting_guild_id": str(source),
        "subject_user_id": str(SUBJECT), "action": action,
        "parent_event_id": parent, "reason": reason, "category": "harassment",
        "created_at": created, "occurred_at": created,
        "evidence_level": "owner_attested",
    }


def test_only_same_guild_original_report_amendments_count():
    original = row("root", A)
    corrections = [
        row("wrong_guild", B, action="withdrawal", parent="root",
            created="2026-11-01T01:00:00+00:00"),
        row("corrected", A, action="correction", parent="root",
            reason="Updated after review", created="2026-11-01T02:00:00+00:00"),
    ]
    reports = network._reconcile_events(corrections + [original])
    assert len(reports) == 1
    assert reports[0]["reason"] == "Updated after review"
    assert reports[0]["active"] is True

    withdrawn = row("withdrawn", A, action="withdrawal", parent="root",
                    created="2026-11-01T03:00:00+00:00")
    assert network._reconcile_events([original, *corrections, withdrawn]) == []


def test_distinct_original_incidents_accumulate_not_overwrite():
    first = row("first", A)
    second = row("second", B, reason="Separate scam attempt",
                 created="2026-11-01T01:00:00+00:00")
    reports = network._reconcile_events([first, second])
    assert len(reports) == 2
    assert {r["reason"] for r in reports} == {
        "Repeated harassment", "Separate scam attempt",
    }


def test_no_receiver_opt_in_means_no_cross_server_read(monkeypatch):
    monkeypatch.setattr(network, "get_network_consent", lambda *_: None)
    monkeypatch.setattr(network, "_load_subject_rows",
                        lambda *_: pytest.fail("Must not query reports"))
    guild = SimpleNamespace(id=B, owner_id=OWNER_B)
    assert network.active_network_reports(guild, SUBJECT, object()) == []


def test_owner_transfer_stops_sharing_and_receiving(monkeypatch):
    monkeypatch.setattr(network, "get_network_consent", lambda *_: {
        "receive_enabled": True, "owner_id": str(OWNER_A)})
    monkeypatch.setattr(network, "_load_subject_rows",
                        lambda *_: pytest.fail("Must not query reports"))
    assert network.active_network_reports(
        SimpleNamespace(id=B, owner_id=OWNER_B), SUBJECT, object()) == []


def test_active_reports_require_live_source_and_explicit_publish(monkeypatch):
    events = [row("a", A), row("c", C, reason="Separate suspicious conduct")]
    monkeypatch.setattr(network, "get_network_consent", lambda *_: {
        "receive_enabled": True, "owner_id": str(OWNER_B)})
    monkeypatch.setattr(network, "_load_subject_rows", lambda *_: events)
    monkeypatch.setattr(network, "_source_consents", lambda *_: {
        str(A): {"guild_label": "Server Alpha", "owner_id": str(OWNER_A)},
        # C published in the past but the bot has left that guild.
        str(C): {"guild_label": "Former Server", "owner_id": str(OWNER_A)},
    })
    guild = SimpleNamespace(id=B, owner_id=OWNER_B)
    bot = SimpleNamespace(get_guild=lambda gid: (
        SimpleNamespace(id=A, owner_id=OWNER_A) if gid == A else None
    ))
    result = network.active_network_reports(guild, SUBJECT, bot)
    assert len(result) == 1
    assert result[0]["source_label"] == "Server Alpha"


def test_different_source_owner_blocks_old_consent(monkeypatch):
    monkeypatch.setattr(network, "get_network_consent", lambda *_: {
        "receive_enabled": True, "owner_id": str(OWNER_B)})
    monkeypatch.setattr(network, "_load_subject_rows", lambda *_: [row("a", A)])
    monkeypatch.setattr(network, "_source_consents", lambda *_: {
        str(A): {"guild_label": "Old owner", "owner_id": str(OWNER_A)}
    })
    bot = SimpleNamespace(get_guild=lambda *_: SimpleNamespace(owner_id=OWNER_B))
    assert network.active_network_reports(
        SimpleNamespace(id=B, owner_id=OWNER_B), SUBJECT, bot) == []


def test_bounded_private_advisory_escapes_mentions():
    rows = [{"id": "x", "source_label": "@everyone Scam Club",
             "category": "scam", "reason": "@here suspicious link",
             "occurred_at": "2026-11-01T00:00:00+00:00"}]
    txt = network.advisory_text(SUBJECT, rows)
    assert "@everyone" not in txt
    assert "@here" not in txt
    assert "No automatic ban" in txt


def test_join_alert_private_owner_only_with_receipt(monkeypatch):
    owner = SimpleNamespace(send=AsyncMock())
    guild = SimpleNamespace(id=B, owner_id=OWNER_B, owner=owner)
    member = SimpleNamespace(id=SUBJECT, guild=guild)
    bot = SimpleNamespace(get_user=lambda *_: owner)
    monkeypatch.setattr(network, "get_network_consent", lambda *_: {
        "receive_enabled": True, "owner_id": str(OWNER_B)})
    monkeypatch.setattr(network, "active_network_reports", lambda *_: [
        {"id": "report", "last_changed": "2026-11-01T00:00:00+00:00",
         "category": "spam", "reason": "Verified spam messages",
         "source_label": "Server Alpha", "occurred_at": "2026-11-01"}
    ])
    monkeypatch.setattr(network, "claim_network_alert", lambda *_: True)
    asyncio.run(network.alert_on_member_join(member, bot))
    owner.send.assert_awaited_once()
    args, kwargs = owner.send.call_args
    assert "Server Alpha" in args[0]
    assert kwargs["allowed_mentions"].everyone is False


def test_join_no_opt_in_never_sends_or_claims(monkeypatch):
    owner = SimpleNamespace(send=AsyncMock())
    guild = SimpleNamespace(id=B, owner_id=OWNER_B, owner=owner)
    monkeypatch.setattr(network, "get_network_consent", lambda *_: None)
    monkeypatch.setattr(network, "claim_network_alert",
                        lambda *_: pytest.fail("No claim without consent"))
    asyncio.run(network.alert_on_member_join(
        SimpleNamespace(id=SUBJECT, guild=guild),
        SimpleNamespace(get_user=lambda *_: owner)))
    owner.send.assert_not_awaited()


def test_validations_reject_unauthorized_parent_and_short_reason(monkeypatch):
    with pytest.raises(ValueError):
        network.record_network_event(A, OWNER_A, SUBJECT, event_key="1",
                                     category="other", reason="short")
    with pytest.raises(ValueError):
        network.record_network_event(A, OWNER_A, SUBJECT, event_key="1",
                                     category="other", reason="Valid reason",
                                     action="withdrawal", parent_id="not-a-uuid")


def test_no_other_guild_report_edit_without_original_ownership(monkeypatch):
    monkeypatch.setattr(network, "get_network_consent",
                        lambda *_: {"owner_id": str(OWNER_A)})
    class Query:
        def select(self, *_): return self
        def eq(self, *_): return self
        def limit(self, *_): return self
        def execute(self): return SimpleNamespace(data=[])
    monkeypatch.setattr(network, "get_supabase",
                        lambda: SimpleNamespace(table=lambda *_: Query()))
    with pytest.raises(PermissionError):
        network.record_network_event(
            A, OWNER_A, SUBJECT, event_key="123", category="other",
            reason="A valid correction reason", action="correction",
            parent_id="00000000-0000-4000-a000-000000000000")


def test_public_profile_registers_single_join_listener_and_no_auto_enforcement():
    root = Path(__file__).resolve().parents[1]
    registry = (root / "stoney_verify/commands_ext/__init__.py").read_text()
    ui = (root / "stoney_verify/commands_ext/public_network_safety.py").read_text()
    service = (root / "stoney_verify/network_safety.py").read_text()
    assert registry.count('"public_network_safety"') == 2
    assert 'bot.add_listener(_on_network_member_join, "on_member_join")' in ui
    assert ".ban(" not in service and ".kick(" not in service
    assert "get_actor_reputation(" not in service
    assert "manual_alt_links" not in service


def test_migration_grants_only_backend_service_role_and_defaults_off():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase_fresh/migrations/202611010001_network_safety_advisories.sql").read_text()
    assert "publish_enabled boolean NOT NULL DEFAULT false" in sql
    assert "receive_enabled boolean NOT NULL DEFAULT false" in sql
    assert "ALTER TABLE public.network_safety_events ENABLE ROW LEVEL SECURITY" in sql
    assert "GRANT EXECUTE ON FUNCTION public.claim_network_safety_alert" in sql
    assert "TO service_role" in sql
