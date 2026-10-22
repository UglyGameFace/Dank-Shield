"""No-network contract tests for opt-in Discord-free Discloud REST diagnostics."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

from stoney_verify import offline_candidate_rest_probe as probe

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://hyncpbvqyjojxhjzcdzg.supabase.co"
CANDIDATE_URL = "https://viamriafkbqhquvrsnua.supabase.co"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(probe.OFFLINE_PROBE_ONLY_ENV, probe.OFFLINE_PROBE_ONLY_CONFIRM)
    monkeypatch.setenv("DANK_CANDIDATE_REST_SHADOW_PROBE", "true")
    monkeypatch.setenv("SUPABASE_URL", SOURCE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "synthetic-original-no-secrets-0001")
    monkeypatch.setenv("DANK_CANDIDATE_SUPABASE_URL", CANDIDATE_URL)
    monkeypatch.setenv(
        "DANK_CANDIDATE_SUPABASE_SERVICE_ROLE_KEY", "synthetic-candidate-no-secrets-0002"
    )


def test_missing_exact_explicit_consent_never_calls_rest(monkeypatch, capsys):
    monkeypatch.setenv(probe.OFFLINE_PROBE_ONLY_ENV, "true")
    monkeypatch.setattr(
        probe, "run_shadow_probe",
        lambda: pytest.fail("Never send network requests without exact consent"),
    )
    assert probe.run_guarded(hold=False) == 2
    assert "invalid_explicit_opt_in" in capsys.readouterr().out


def test_shadow_probe_must_be_enabled(configured, monkeypatch, capsys):
    monkeypatch.delenv("DANK_CANDIDATE_REST_SHADOW_PROBE")
    monkeypatch.setattr(
        probe, "run_shadow_probe",
        lambda: pytest.fail("Never access REST without shadow approval"),
    )
    assert probe.run_guarded(hold=False) == 2
    assert "shadow_probe_not_enabled" in capsys.readouterr().out


def test_success_checks_source_and_candidate_zero_writes(configured, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(probe, "run_shadow_probe", lambda: (
        calls.append(True) or {
            "status": "ready",
            "rest_table_checks": 5,
            "source_writes": 0,
            "candidate_writes": 0,
            "production_database_switched": False,
        }
    ))
    assert probe.run_guarded(hold=False) == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "DANK_OFFLINE_REST_PROBE: pass checks=5" in out
    assert "discord_started=false bot_workers_started=false" in out
    assert "source_writes=0 candidate_writes=0 production_switched=false" in out
    assert "synthetic-" not in out


@pytest.mark.parametrize("wrong_report", [
    {"status": "disabled", "rest_table_checks": 0, "source_writes": 0,
     "candidate_writes": 0, "production_database_switched": False},
    {"status": "ready", "rest_table_checks": 4, "source_writes": 0,
     "candidate_writes": 0, "production_database_switched": False},
    {"status": "ready", "rest_table_checks": 5, "source_writes": 1,
     "candidate_writes": 0, "production_database_switched": False},
    {"status": "ready", "rest_table_checks": 5, "source_writes": 0,
     "candidate_writes": 0, "production_database_switched": True},
])
def test_incomplete_or_unsafe_report_fails_closed(
    configured, monkeypatch, capsys, wrong_report
):
    monkeypatch.setattr(probe, "run_shadow_probe", lambda: wrong_report)
    assert probe.run_guarded(hold=False) == 2
    assert "candidate_rest_read_failed" in capsys.readouterr().out


def test_secret_bearing_errors_never_leak(configured, monkeypatch, capsys):
    def fail():
        raise RuntimeError(
            "authorization bearer synthetic-candidate-no-secrets-0002, member 12345"
        )
    monkeypatch.setattr(probe, "run_shadow_probe", fail)
    assert probe.run_guarded(hold=False) == 2
    output = capsys.readouterr().out
    assert "candidate_rest_read_failed" in output
    assert "synthetic-" not in output and "12345" not in output


def test_main_boot_entrypoint_intercepts_before_all_discord_imports(
    monkeypatch,
):
    monkeypatch.setenv(probe.OFFLINE_PROBE_ONLY_ENV, "even-an-invalid-value")
    module = ModuleType("stoney_verify.offline_candidate_rest_probe")
    called = []
    module.run_guarded = lambda: called.append(True) or 0
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
    assert result.value.code == 0
    assert called == [True]

    script = (ROOT / "main.py").read_text()
    assert script.index("DANK_OFFLINE_CANDIDATE_REST_PROBE_ONLY") < script.index(
        "from stoney_verify.startup_guards.process_health"
    )
    assert "if __name__ == \"__main__\"" in script


def test_probe_source_module_only_imports_existing_readonly_shim():
    module = (ROOT / "stoney_verify/offline_candidate_rest_probe.py").read_text()
    assert "candidate_supabase_shadow_probe" in module
    for forbidden in (
        "from stoney_verify.globals",
        "stoney_verify.app",
        "bot.run(",
        "bot.start(",
        ".insert(",
        ".update(",
        ".delete(",
        "os.environ[\"SUPABASE_URL\"] =",
    ):
        assert forbidden not in module
