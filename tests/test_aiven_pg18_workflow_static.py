"""Isolated PG18 workflow regression guard, runnable without any database."""
from pathlib import Path


def test_aiven_pg18_workflow_cannot_access_live_database():
    root = Path(__file__).resolve().parents[1]
    wf = (root / ".github/workflows/aiven-pg18-isolated-compatibility.yml").read_text()
    assert "image: postgres:18" in wf
    assert "PGHOST: 127.0.0.1" in wf
    assert "DANK_TEST_DATABASE_URL: postgresql://postgres:disposable_test_only@127.0.0.1:5432/postgres" in wf
    assert 'RUN_AIVEN_PG18_ISOLATED: "1"' in wf
    assert "contents: read" in wf
    assert "  workflow_dispatch:" in wf
    assert "ON_ERROR_STOP=1" in wf
    assert "for pass in 1 2;" in wf
    assert "secrets." not in wf
    assert "vars." not in wf
    assert "AIVEN_POSTGRES_URI" not in wf
    assert "DISCLOUD_TOKEN" not in wf
    assert "deploy-action" not in wf


def test_pg18_integration_test_hard_rejects_remote_hosts():
    text = (Path(__file__).resolve().parents[1] /
            "tests/test_aiven_pg18_compatibility.py").read_text()
    for proof in (
        'parsed.hostname in {"127.0.0.1", "localhost"}',
        'parsed.path == "/postgres"',
        'parsed.username == "postgres"',
        'os.environ.get("GITHUB_ACTIONS") == "true"',
        'os.environ.get("GITHUB_REPOSITORY") == "UglyGameFace/Dank-Shield"',
        "drift.cutover_authorized is False",
        "public.voice_bot_canary",
        "test_pg18_concurrent_cas_and_stale_revision",
    ):
        assert proof in text
