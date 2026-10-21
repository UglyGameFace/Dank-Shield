"""No-secrets candidate Supabase REST probe tests; no actual API calls."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import candidate_supabase_shadow_probe as mod

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def active_candidate_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://hyncpbvqyjojxhjzcdzg.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "active-source-only-synthetic-key-0001")
    monkeypatch.setenv(mod.CANDIDATE_URL_ENV, "https://viamriafkbqhquvrsnua.supabase.co")
    monkeypatch.setenv(mod.CANDIDATE_KEY_ENV, "separate-offline-synthetic-key-0002")
    monkeypatch.setenv(mod.ENABLED_ENV, "true")


def test_disabled_never_reads_credentials_or_calls_network(monkeypatch):
    monkeypatch.delenv(mod.ENABLED_ENV, raising=False)
    assert mod.run_shadow_probe() == {"status": "disabled", "candidate_writes": 0}
    assert not mod.enabled()


@pytest.mark.parametrize("name,value,expected", [
    ("SUPABASE_URL", "https://viamriafkbqhquvrsnua.supabase.co",
     "active_source_configuration_mismatch"),
    ("SUPABASE_URL", "https://different.supabase.co",
     "active_source_configuration_mismatch"),
    (mod.CANDIDATE_URL_ENV, "https://hyncpbvqyjojxhjzcdzg.supabase.co",
     "candidate_project_identity_mismatch"),
    (mod.CANDIDATE_URL_ENV, "https://viamriafkbqhquvrsnua.supabase.co.evil.example",
     "candidate_project_identity_mismatch"),
    (mod.CANDIDATE_URL_ENV, "https://viamriafkbqhquvrsnua.supabase.co/malicious",
     "candidate_project_identity_mismatch"),
    (mod.CANDIDATE_URL_ENV, "http://viamriafkbqhquvrsnua.supabase.co",
     "candidate_project_identity_mismatch"),
    (mod.CANDIDATE_KEY_ENV, "", "candidate_connection_not_configured"),
    (mod.CANDIDATE_KEY_ENV, "active-source-only-synthetic-key-0001",
     "candidate_key_matches_active_key"),
])
def test_credentials_fail_closed(active_candidate_env, monkeypatch, name, value, expected):
    monkeypatch.setenv(name, value)
    with pytest.raises(mod.ShadowProbeError) as err:
        mod.run_shadow_probe()
    assert err.value.code == expected


def test_only_five_read_queries_to_shadow_client(active_candidate_env, monkeypatch):
    queries = []
    candidate_clients = []

    class Query:
        def select(self, column):
            queries.append(("select", column))
            return self
        def limit(self, n):
            assert n == 1
            queries.append(("limit", n))
            return self
        def execute(self):
            queries.append(("execute",))
            return SimpleNamespace(data=[])

    class Client:
        def table(self, name):
            queries.append(("table", name))
            return Query()

    def create_client(url, key):
        candidate_clients.append((url, key))
        return Client()

    monkeypatch.setitem(sys.modules, "supabase",
                        SimpleNamespace(create_client=create_client))
    result = mod.run_shadow_probe()
    assert result["status"] == "ready"
    assert result["rest_table_checks"] == 5
    assert result["candidate_writes"] == 0
    assert result["source_writes"] == 0
    assert result["production_database_switched"] is False
    assert candidate_clients == [(
        "https://viamriafkbqhquvrsnua.supabase.co",
        "separate-offline-synthetic-key-0002",
    )]
    assert [x[1] for x in queries if x[0] == "table"] == [
        item[0] for item in mod.PROBES
    ]
    assert [x[0] for x in queries].count("execute") == 5
    assert not any(x[0] in ("insert", "update", "delete", "rpc") for x in queries)


def test_network_failure_redacts_tokens_from_logs(active_candidate_env, monkeypatch, capsys):
    class Query:
        def select(self, column): return self
        def limit(self, n): return self
        def execute(self):
            raise RuntimeError("Authorization: Bearer separate-offline-synthetic-key-0002")

    class Client:
        def table(self, name): return Query()
    monkeypatch.setitem(sys.modules, "supabase",
                        SimpleNamespace(create_client=lambda url, key: Client()))
    asyncio.run(mod.run_shadow_probe_at_startup())
    log = capsys.readouterr().out
    assert "candidate_rest_read_failed" in log
    assert "separate-offline-synthetic-key-0002" not in log
    assert "Authorization:" not in log


def test_success_log_contains_only_aggregates(active_candidate_env, monkeypatch, capsys):
    monkeypatch.setattr(mod, "run_shadow_probe", lambda: {
        "status": "ready", "rest_table_checks": 5,
    })
    asyncio.run(mod.run_shadow_probe_at_startup())
    log = capsys.readouterr().out
    assert "DANK_CANDIDATE_REST_SHADOW_PROBE: pass checks=5" in log
    assert "production_switched=false" in log
    assert "source_writes=0 candidate_writes=0" in log


def test_only_opt_in_async_ready_integration():
    script = (ROOT / "stoney_verify/candidate_supabase_shadow_probe.py").read_text()
    events = (ROOT / "stoney_verify/events.py").read_text()
    assert "if candidate_shadow_enabled()" in events
    assert '_candidate_shadow_rest_probe_started' in events
    assert "_assign_startup_task(" in events
    assert "run_shadow_probe_at_startup()" in events
    assert "asyncio.to_thread(run_shadow_probe)" in script
    assert "asyncio.wait_for" in script
    assert "get_supabase()" in script  # only appears in explicit prohibition comment
    assert "get_supabase(" not in script.replace("get_supabase()", "")
    for forbidden in (".insert(", ".upsert(", ".update(", ".delete(", ".rpc(",
                      "os.environ['SUPABASE_URL'] =", 'os.environ["SUPABASE_URL"] ='):
        assert forbidden not in script
