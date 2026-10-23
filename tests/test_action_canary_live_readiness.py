from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import action_canary_readiness_report as report
from stoney_verify import action_canary_delivery as dispatcher
from stoney_verify.commands_ext import public_action_canary as console
from stoney_verify.runtime_release import _FINGERPRINT_PATHS


def test_default_off_preflight_never_opens_private_db(monkeypatch):
    for key in (
        report.DATABASE_URL_ENV,
        report.ENABLE_ENV, report.GUILD_ENV, report.USER_ENV, report.REVISION_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(report, "runtime_release_snapshot", lambda: {
        "declared_sha": "", "source_fingerprint": "sample-fingerprint",
    })
    def should_not_query():
        raise AssertionError("No DB configured, so no database query allowed")
    monkeypatch.setattr(report, "ledger_ready_sync", should_not_query)
    monkeypatch.setattr(report, "audit_ready_sync", should_not_query)
    result = asyncio.run(report.read_canary_readiness(501))
    assert not result.db_configured
    assert not result.claims_ready and not result.audit_ready
    assert not result.flags_complete
    assert not result.delivery_activated
    assert not result.release_verified
    assert "not exposed" not in result.source_fingerprint
    assert len(result.blockers) >= 4


def test_only_read_only_queries_and_exact_guild_target_match(monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(report, "runtime_release_snapshot", lambda: {
        "declared_sha": sha, "source_fingerprint": "1234567890abcdef",
    })
    monkeypatch.setenv(report.DATABASE_URL_ENV, "postgresql://test:do-not-print@localhost/postgres")
    monkeypatch.setenv(report.ENABLE_ENV, "1")
    monkeypatch.setenv(report.GUILD_ENV, "501")
    monkeypatch.setenv(report.USER_ENV, "601")
    monkeypatch.setenv(report.REVISION_ENV, "3")
    calls = []
    def claim():
        calls.append("claim_select")
        return True
    def audit():
        calls.append("audit_select")
        return True
    monkeypatch.setattr(report, "ledger_ready_sync", claim)
    monkeypatch.setattr(report, "audit_ready_sync", audit)
    result = asyncio.run(report.read_canary_readiness(501))
    assert calls.count("claim_select") == 1 and calls.count("audit_select") == 1
    assert result.declared_sha == sha
    assert result.db_configured and result.claims_ready and result.audit_ready
    assert result.flags_complete and result.target_guild_match
    assert not result.release_verified  # local SHA is not a GitHub attestation
    assert not result.delivery_activated
    assert "do-not-print" not in str(result)
    mismatch = asyncio.run(report.read_canary_readiness(502))
    assert not mismatch.target_guild_match
    assert "do-not-print" not in str(mismatch)


def test_missing_schema_and_failure_are_blockers_without_leaking_db_error(monkeypatch):
    monkeypatch.setenv(report.DATABASE_URL_ENV, "postgresql://private-top-secret")
    monkeypatch.setattr(report, "runtime_release_snapshot", lambda: {
        "declared_sha": "not-a-sha", "source_fingerprint": "unknown",
    })
    monkeypatch.setattr(report, "ledger_ready_sync", lambda: False)
    def fail():
        raise RuntimeError("private-top-secret")
    monkeypatch.setattr(report, "audit_ready_sync", fail)
    obj = asyncio.run(report.read_canary_readiness(501))
    assert obj.declared_sha == ""
    assert not obj.claims_ready and not obj.audit_ready
    assert "private-top-secret" not in str(obj)
    assert not obj.delivery_activated


def test_diagnostic_does_not_auto_authorize_or_accept_invalid_server():
    with pytest.raises(ValueError):
        asyncio.run(report.read_canary_readiness(0))
    with pytest.raises(ValueError):
        report.CanaryReadinessReport("", "", False, False, False, False, False, False, True)


def test_application_owner_not_required_to_have_separate_guild_staff_role(monkeypatch):
    calls = []
    async def owner(interaction):
        calls.append("checked_owner")
        return bool(getattr(interaction, "application_owner", False))
    async def msg(_interaction, content):
        calls.append(content)
    monkeypatch.setattr(console, "_is_app_owner", owner)
    monkeypatch.setattr(console, "_msg", msg)
    view = console.AppOwnerCanaryView(42, 501, 601, 3)
    assert asyncio.run(view.interaction_check(SimpleNamespace(
        user=SimpleNamespace(id=42), guild_id=501, application_owner=True
    )))
    assert calls == ["checked_owner"]
    assert not asyncio.run(view.interaction_check(SimpleNamespace(
        user=SimpleNamespace(id=42), guild_id=501, application_owner=False
    )))
    assert not asyncio.run(view.interaction_check(SimpleNamespace(
        user=SimpleNamespace(id=43), guild_id=501, application_owner=True
    )))


def test_critical_action_delivery_files_affect_runtime_fingerprint_and_report_is_unwired():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "stoney_verify/action_canary_delivery.py",
        "stoney_verify/action_canary_audit.py",
        "stoney_verify/action_canary_readiness_report.py",
        "stoney_verify/action_member_consent.py",
        "stoney_verify/commands_ext/public_action_canary.py",
    ):
        assert relative in _FINGERPRINT_PATHS
        assert (root / relative).is_file()
    source = Path(report.__file__).read_text()
    assert ".send(" not in source
    assert "reserve_member_setup_sync(" not in source
    assert "create_task(" not in source
    assert "deploy_existing_app" not in source
    assert "apply_migration" not in source
    ui_source = Path(console.__file__).read_text()
    assert "Readiness Report" in ui_source
    assert "Only the original Dank Shield application owner" in ui_source
