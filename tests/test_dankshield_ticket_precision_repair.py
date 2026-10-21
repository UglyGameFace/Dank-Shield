"""Offline ticket confidence schema repair guard tests. No real SQL writes."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import repair_offline_ticket_confidence_precision as mod

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dankshield-offline-ticket-precision-repair.yml"
AUDIT = ROOT / ".github/workflows/dankshield-cutover-delta-audit.yml"


def test_repair_owner_confirmation_and_exact_project_validation(monkeypatch):
    for key in (
        "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_ACTOR",
        "DANK_TICKET_PRECISION_CONFIRMATION", "DANK_FRESH_DATABASE_URL"
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="not_owner_confirmed_offline_repair"):
        mod.validate_request()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_ACTOR", "UglyGameFace")
    monkeypatch.setenv("DANK_TICKET_PRECISION_CONFIRMATION", mod.CONFIRMATION)
    monkeypatch.setenv("DANK_FRESH_DATABASE_URL", "test-provided-only")
    observed = []
    monkeypatch.setattr(
        mod, "validate_source_dsn",
        lambda dsn, ref: observed.append((dsn, ref)) or {"validated": "candidate"},
    )
    assert mod.validate_request() == {"validated": "candidate"}
    assert observed == [("test-provided-only", "viamriafkbqhquvrsnua")]
    monkeypatch.setenv("GITHUB_ACTOR", "somebody_else")
    with pytest.raises(RuntimeError, match="not_owner_confirmed_offline_repair"):
        mod.validate_request()


def test_workflow_is_separate_explicit_write_and_source_excluded():
    text = WORKFLOW.read_text(encoding="utf8")
    audit = AUDIT.read_text(encoding="utf8")
    code = (ROOT / "tools/repair_offline_ticket_confidence_precision.py").read_text()
    assert "workflow_dispatch:" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.actor == 'UglyGameFace'" in text
    assert "FIX_OFFLINE_TICKET_CONFIDENCE_PRECISION_ONLY" in text
    assert "DANK_FRESH_DATABASE_URL: ${{ secrets.DANK_FRESH_DATABASE_URL }}" in text
    assert "DANK_SOURCE_DATABASE_URL" not in text
    assert "DANK_SOURCE_DATABASE_URL" not in code
    assert "supabase_admin" not in code
    assert "DANK_FRESH_DATABASE_URL" not in audit or "DANK_SOURCE_DATABASE_URL" in audit
    assert "tools.repair_offline_ticket_confidence_precision" not in audit
    assert "default_transaction_read_only=on" in (
        ROOT / "tools/audit_dankshield_cutover_delta.py"
    ).read_text()
    assert "lock table public.tickets in access exclusive mode" in code
    assert "numeric(5,2)" in code
    assert "trunc(ai_category_confidence,2)" in code
    assert "trunc(mod_suggestion_confidence,2)" in code


def test_no_raw_exception_or_private_data_in_repair_logs(monkeypatch, capsys):
    monkeypatch.setattr(mod, "repair",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("token=PRIVATE database-url=value")))
    assert mod.main() == 2
    captured = capsys.readouterr()
    assert "OFFLINE_TICKET_PRECISION_REPAIR_FAILED: offline_repair_failed_redacted" in captured.err
    assert "token=PRIVATE" not in captured.err
    assert "database-url" not in captured.err


def test_idempotent_schema_repair_is_atomic(monkeypatch):
    """An in-memory fake verifies exact SQL targets and no extra tables."""
    import sys
    from types import SimpleNamespace

    actions = []
    columns = {
        "ai_category_confidence": "numeric",
        "mod_suggestion_confidence": "numeric",
    }

    class Cursor:
        def __init__(self, val): self.val = val
        def fetchall(self): return self.val
        def fetchone(self): return (self.val,)

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def transaction(self): return self
        def execute(self, sql, params=None):
            actions.append(sql)
            if "select a.attname" in sql:
                return Cursor(sorted(columns.items()))
            if "select exists(" in sql:
                return Cursor(False)
            if "alter table public.tickets" in sql:
                columns.update(dict.fromkeys(columns, "numeric(5,2)"))
            return Cursor(None)

    monkeypatch.setattr(mod, "validate_request", lambda: {
        "PGHOST": "offline-verified-host", "PGPORT": "5432",
        "PGUSER": "postgres", "PGPASSWORD": "synthetic-test",
        "PGDATABASE": "postgres",
    })
    monkeypatch.setitem(sys.modules, "psycopg",
                        SimpleNamespace(connect=lambda **kwargs: Conn()))
    assert mod.repair() == "corrected"
    assert len([x for x in actions if "alter table public.tickets" in x]) == 1
    assert all("dank_source" not in x for x in actions)
    actions.clear()
    assert mod.repair() == "already_exact"
    assert not any("alter table" in x for x in actions)


def test_unsafe_offline_values_abort_before_any_schema_write(monkeypatch):
    import sys
    from types import SimpleNamespace

    actions = []

    class Cursor:
        def __init__(self, result): self.result = result
        def fetchall(self): return [
            ("ai_category_confidence", "numeric"),
            ("mod_suggestion_confidence", "numeric"),
        ]
        def fetchone(self): return (self.result,)

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def transaction(self): return self
        def execute(self, statement, params=None):
            actions.append(statement)
            if "select exists(" in statement:
                return Cursor(True)
            return Cursor(None)

    monkeypatch.setattr(mod, "validate_request", lambda: {
        "PGHOST": "offline-verified-host", "PGPORT": "5432",
        "PGUSER": "postgres", "PGPASSWORD": "synthetic",
        "PGDATABASE": "postgres",
    })
    monkeypatch.setitem(sys.modules, "psycopg",
                        SimpleNamespace(connect=lambda **kwargs: Conn()))
    with pytest.raises(RuntimeError, match="offline_confidence_values_not_representable"):
        mod.repair()
    assert not any("alter table" in x for x in actions)
