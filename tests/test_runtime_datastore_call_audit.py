"""Regression tests for the read-only runtime datastore dependency audit."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "audit_runtime_datastore_calls.py"
spec = importlib.util.spec_from_file_location("audit_runtime_datastore_calls", MODULE_PATH)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_detects_critical_supabase_call(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    source = tmp_path / "stoney_verify" / "tickets_new"
    source.mkdir(parents=True)
    file = source / "ticket_store.py"
    file.write_text("def load():\n    return get_supabase().table('tickets')\n")
    hits = audit.scan_file(file)
    assert len(hits) == 1
    assert hits[0]["line"] == 2
    assert hits[0]["critical_area"] is True


def test_ignores_strings_comments_and_unrelated_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    file = tmp_path / "unrelated.py"
    file.write_text("# get_supabase()\ns = 'create_client()'\nother()\n")
    assert audit.scan_file(file) == []


def test_reports_invalid_syntax_without_exposing_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    file = tmp_path / "broken.py"
    file.write_text("def invalid(:\n")
    assert audit.scan_file(file) == [{"file": "broken.py", "error": "SyntaxError"}]


def test_report_never_claims_cutover_verified(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    source = tmp_path / "stoney_verify"
    source.mkdir()
    (source / "modlog.py").write_text("get_supabase()\n")
    monkeypatch.setattr(audit, "SOURCE", source)
    assert audit.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["production_postgres_cutover_verified"] is False
    assert report["supabase_call_sites"] == 1
    assert report["results"][0]["critical_area"] is True
