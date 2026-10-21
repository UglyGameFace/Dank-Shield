"""Credential-free safety tests for the owner-only dedicated database preflight."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import preflight_dedicated_supabase_runtime as mod

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dankshield-dedicated-runtime-preflight.yml"
CODE = ROOT / "tools/preflight_dedicated_supabase_runtime.py"


class Response:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, *, bad=None):
        self.bad = bad
        self.probed_signatures = []

    def execute(self, stmt, params=None):
        if "current_database()" in stmt:
            return Response(("postgres", 170011, "on"))
        if "from pg_class c join pg_namespace" in stmt:
            bad_rls = 1 if self.bad == "rls" else 0
            bad_public = 1 if self.bad == "public" else 0
            bad_crud = 1 if self.bad == "crud" else 0
            return Response((75, bad_rls, bad_public, bad_crud))
        if "from pg_proc p" in stmt:
            return Response((1 if self.bad == "definer" else 0,))
        if "to_regprocedure(%s)" in stmt:
            self.probed_signatures.append(params[0])
            if self.bad == "missing_function":
                return Response((False, False, False, False))
            if self.bad == "function_public":
                return Response((True, True, True, False))
            if self.bad == "function_backend":
                return Response((True, False, False, False))
            return Response((True, True, False, False))
        if "public.bot_commands" in stmt:
            return Response((1 if self.bad == "queue" else 0, 0))
        raise AssertionError("unknown SQL statement")


def test_clean_read_only_database_contracts():
    fake = FakeConnection()
    r = mod.inspect(fake)
    assert len(fake.probed_signatures) == 8
    assert set(fake.probed_signatures) == set(mod.EXPECTED_FUNCTIONS)
    assert r["checked_public_tables"] == 75
    assert r["checked_backend_functions"] == 8
    assert r["production_cutover_authorized"] is False
    assert r["discloud_network_proven"] is False
    assert r["supabase_rest_api_runtime_proven"] is False
    assert r["source_writers_frozen"] is False
    assert r["source_writes"] == r["target_writes"] == 0


@pytest.mark.parametrize("bad,code", [
    ("rls", "public_table_rls_missing"),
    ("public", "public_table_role_exposure"),
    ("crud", "public_table_backend_crud_missing"),
    ("definer", "public_definer_function_role_exposure"),
    ("missing_function", "required_backend_function_missing"),
    ("function_public", "required_backend_function_permissions"),
    ("function_backend", "required_backend_function_permissions"),
    ("queue", "dedicated_operation_queues_not_empty"),
])
def test_fails_closed_on_privacy_rpc_or_queue_risk(bad, code):
    with pytest.raises(mod.TargetPreflightError) as err:
        mod.inspect(FakeConnection(bad=bad))
    assert err.value.code == code


def test_owner_only_main_and_pinned_candidate(monkeypatch):
    names = ("GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_REF",
             "GITHUB_ACTOR", "DANK_FRESH_DATABASE_URL")
    for key in names:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(mod.TargetPreflightError, match="not_trusted_owner_main"):
        mod.validate_runner()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.EXPECTED_REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_ACTOR", "UglyGameFace")
    monkeypatch.setenv("DANK_FRESH_DATABASE_URL", "synthetic-test-only")
    observed = []
    monkeypatch.setattr(mod, "validate_source_dsn",
                        lambda dsn, project: observed.append((dsn, project)) or {})
    mod.validate_runner()
    assert observed == [("synthetic-test-only", mod.EXPECTED_CANDIDATE)]
    monkeypatch.setenv("GITHUB_ACTOR", "untrusted-contributor")
    with pytest.raises(mod.TargetPreflightError, match="not_trusted_owner_main"):
        mod.validate_runner()


def test_preflight_fails_safely_without_logging_private_errors(monkeypatch, capsys):
    monkeypatch.setattr(mod, "preview",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("secret_url=PRIVATE-NEVER-PRINT")))
    assert mod.main() == 2
    out = capsys.readouterr()
    assert "unclassified_preflight_error" in out.err
    assert "PRIVATE-NEVER-PRINT" not in out.err


def test_workflow_remains_manual_main_only_and_source_free():
    workflow = WORKFLOW.read_text()
    script = CODE.read_text()
    assert "workflow_dispatch:" in workflow
    assert "github.actor == 'UglyGameFace'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "persist-credentials: false" in workflow
    assert "secrets.DANK_FRESH_DATABASE_URL" in workflow
    assert "DANK_SOURCE_DATABASE_URL" not in workflow
    assert "DANK_SOURCE_DATABASE_URL" not in script
    assert "DISCLOUD_TOKEN" not in workflow
    assert "discloud app" not in workflow.lower()
    assert "default_transaction_read_only=on" in (
        ROOT / "tools/audit_dankshield_cutover_delta.py"
    ).read_text()
    assert 'set transaction isolation level repeatable read, read only' in script
    assert '"production_cutover_authorized": False' in script
    for forbidden in ("insert into public.", "update public.", "delete from public.",
                      "truncate public.", "pg_notify("):
        assert forbidden not in script.lower()
