"""No credentials, live network, DB writes or Discord clients in audit CI."""
from __future__ import annotations
import ast
from pathlib import Path
from tools import audit_dankshield_cutover_delta as mod

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"tools/audit_dankshield_cutover_delta.py"
CI=ROOT/".github/workflows/dankshield-cutover-delta-tests.yml"
MANUAL=ROOT/".github/workflows/dankshield-cutover-delta-audit.yml"

def test_critical_tables_are_fixed_private_allowlist():
    assert len(mod.TABLES)==34
    assert len(set(mod.TABLES))==len(mod.TABLES)
    assert {"guild_configs","guild_members","tickets","activity_feed_events",
            "member_events","staff_action_logs","warns",
            "dank_cinema_user_media","dank_quiet_notices",
            "dank_invite_block_stats","bot_operation_jobs"}-set(mod.TABLES)=={"bot_operation_jobs"}
    assert mod.ALLOW_DESTINATION_ONLY_COLUMNS=={
        "staff_metrics":frozenset(("created_at","updated_at"))
    }
    assert mod.ALLOW_STRICTER_TARGET_NOT_NULL=={
        "staff_metrics":frozenset((
            "staff_name","tickets_handled","approvals",
            "denials","avg_response_minutes"
        ))
    }
    assert 'AuditBlocker("source_null_unsafe", table)' in PY.read_text(encoding="utf8")
    assert "audit_logs" not in mod.TABLES
    assert "dank_invite_block_events" not in mod.TABLES
    assert "verification_tokens" not in mod.TABLES

def test_sql_is_explicitly_read_only_and_no_cutover_route():
    text=PY.read_text(encoding="utf8")
    ast.parse(text)
    assert "default_transaction_read_only=on" in text
    assert "set transaction isolation level repeatable read, read only" in text
    assert "production_cutover_authorized\": False" in text
    assert "requires_independent_encrypted_backup_restore\": True" in text
    assert "requires_source_writer_pause_and_second_audit\": True" in text
    assert "MAX_ROW_BYTES = 1024 * 1024" in text
    assert "MAX_TOTAL_BYTES = 350 * 1024 * 1024" in text
    for forbidden in ("delete from public.","update public.","truncate table",
                      "insert into public.","pg_notify(", "discord.Client("):
        assert forbidden not in text.lower()

def test_dangerous_live_work_flags_and_distinct_no_replay_queues():
    assert {"PENDING","STAFF_ACCEPTED"} <= set(mod.LIVE_SESSION_STATES)
    assert {"queued","running","waiting_rate_limit"} <= set(mod.ACTIVE_OPERATION_STATES)
    assert {"pending","processing"} <= set(mod.ACTIVE_COMMAND_STATES)
    text=PY.read_text()
    assert "where status in ('PENDING','STAFF_ACCEPTED','IN_PROGRESS','ACTIVE')" in text
    assert "where expires_at > now() and not used" in text
    assert "where last_seen_at > now()-interval '90 minutes'" in text
    assert "where state not in ('cleaned','ended','expired','closed'" in text

def test_manual_workflow_only_on_owner_main_and_no_production_deploy():
    text=MANUAL.read_text(encoding="utf8")
    ci=CI.read_text(encoding="utf8")
    assert "workflow_dispatch:" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.actor == 'UglyGameFace'" in text
    assert "secrets.DANK_SOURCE_DATABASE_URL" in text
    assert "secrets.DANK_FRESH_DATABASE_URL" in text
    assert "persist-credentials: false" in text
    assert "schedule:" not in text
    assert "on:\n  pull_request:" in ci
    assert "secrets." not in ci
    assert "discloud" not in ci.lower()


def test_delta_contract_failure_is_precise_and_privacy_safe(monkeypatch, capsys):
    import pytest
    source = [
        ("ai_category_confidence", "numeric(5,2)", False),
        ("mod_suggestion_confidence", "numeric(5,2)", False),
    ]
    target = [
        ("ai_category_confidence", "numeric", False),
        ("mod_suggestion_confidence", "numeric", False),
    ]
    monkeypatch.setattr(mod, "column_contract",
                        lambda _conn, table: source if _conn == "src" else target)
    with pytest.raises(mod.AuditBlocker) as exc:
        mod.table_contract("src", "dest", "tickets")
    assert (exc.value.table, exc.value.code) == (
        "tickets", "schema_column_contract_mismatch"
    )

    def failed_preview():
        raise exc.value
    monkeypatch.setattr(mod, "preview", failed_preview)
    assert mod.main() == 2
    log = capsys.readouterr().err
    assert "schema_column_contract_mismatch table=tickets" in log
    assert "numeric(" not in log
    assert "credential" not in log


def test_delta_diagnostics_refuse_untrusted_exception_contents():
    err = mod.AuditBlocker("private-row-token-DONOTPRINT", "private-user-id")
    assert err.code == "unexpected_table_audit_failure"
    assert err.table == "unknown"
    assert "private-row-token" not in str(err)
