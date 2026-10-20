from __future__ import annotations

import ast
from pathlib import Path

from stoney_verify.source_pg_backup import validate_source_dsn
from tools import audit_dankshield_source_metadata as mod

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/audit_dankshield_source_metadata.py"
WORKFLOW = ROOT / ".github/workflows/dankshield-source-metadata-audit.yml"
ORIGINAL = ROOT / "tools/aiven_guild_config_stage.py"


def test_source_ref_is_exact_pinned_legacy_project() -> None:
    tree = ast.parse(ORIGINAL.read_text(encoding="utf-8"))
    vals = [node.value.value for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "SOURCE_REF" for t in node.targets)
            and isinstance(node.value, ast.Constant)]
    assert vals == [mod.SOURCE_REF]
    good = f"postgresql://postgres:dummy_test_secret@db.{mod.SOURCE_REF}.supabase.co:5432/postgres"
    assert validate_source_dsn(good, mod.SOURCE_REF)["PGDATABASE"] == "postgres"
    try:
        validate_source_dsn(good, "viamriafkbqhquvrsnua")
    except ValueError:
        pass
    else:
        raise AssertionError("Candidate was accepted as the old source!")


def test_source_allowlist_excludes_shared_idle_grow_op_data() -> None:
    mod._safe_table_contract()
    assert len(mod.EXPECTED_TABLES) == len(set(mod.EXPECTED_TABLES))
    assert {"warns","raid_events","audit_events","audit_logs","staff_metrics"} <= set(mod.EXPECTED_TABLES)
    assert {"dank_cinema_watch_sessions","dank_profile_guild_settings",
            "dank_community_hub_settings"} <= set(mod.EXPECTED_TABLES)
    assert mod.ROW_PROBE_CAP <= 2500
    for unrelated in ("global_accounts", "guild_worlds", "world", "users", "guild_profiles"):
        assert unrelated not in mod.EXPECTED_TABLES
    assert mod.DETAIL_TABLES == frozenset(
        ("warns","raid_events","audit_events","audit_logs","staff_metrics")
    )


def test_audit_has_no_row_data_or_persistent_writes() -> None:
    source=SCRIPT.read_text(encoding="utf-8").lower()
    assert "default_transaction_read_only=on" in source
    assert "isolation level repeatable read, read only" in source
    assert "statement_timeout=4000" in source
    assert "lock_timeout=1000" in source
    assert "select 1 from {}.{} limit %s" in source
    assert "sql.identifier(name)" in source
    assert "to_jsonb(" not in source
    assert "select *" not in source
    assert "return collect_source_metadata(connection)" in source
    assert 'except exception:' in source
    assert "details and credentials withheld" in source
    assert "upload-artifact" not in source


def test_manual_workflow_refuses_outsiders_and_never_uploads_data() -> None:
    wf=WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf
    assert "github.actor == 'UglyGameFace'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "secrets.DANK_SOURCE_DATABASE_URL" in wf
    assert "AUDIT_DANKSHIELD_SOURCE_METADATA_ONLY" in wf
    assert 'python -m tools.audit_dankshield_source_metadata' in wf
    assert "actions/upload-artifact" not in wf
    assert "discloud/deploy-action" not in wf
    assert "cron:" not in wf
