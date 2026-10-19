"""Offline controls for manual PostgreSQL 18 future guild-config seed."""
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from stoney_verify.core_data_stage import plan_source
from tools import aiven_guild_config_state_seed as mod


def _plan():
    data = [
        {"id": "abc", "guild_id": "123", "settings": {"verify": True}},
        {"id": "def", "guild_id": "456", "settings": {"verify": False}},
    ]
    return plan_source(
        lambda t, k, offset, count: data[offset:offset + count],
        selected_tables=["guild_configs"],
    )


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, sql):
        assert "from dank_core.guild_config_state order by guild_id" in sql
    def fetchall(self):
        return self.rows


class Db:
    def __init__(self, rows):
        self.rows = rows
    def cursor(self):
        return Cursor(self.rows)


def test_preview_and_seed_confirmation_are_separate():
    mod.validate_mode("preview", "")
    mod.validate_mode("seed", mod.CONFIRMATION)
    for mode, word in (("seed", ""), ("seed", "IMPORT_AIVEN_GUILD_CONFIG_SHADOW_ONLY"), ("delete", mod.CONFIRMATION)):
        with pytest.raises(ValueError):
            mod.validate_mode(mode, word)


def test_state_verifier_never_allows_extras_stale_or_partial_rows():
    src = _plan()
    valid = [(r.guild_id, json.loads(r.row_json), 0) for r in src.rows]
    assert mod._state_count_and_integrity(Db([]), src) == 0
    assert mod._state_count_and_integrity(Db(valid), src) == 2
    for bad in (
        [valid[0]],
        [valid[0], (valid[1][0], valid[1][1], 1)],
        [valid[0], ("999", valid[1][1], 0)],
        [valid[0], (valid[1][0], {"guild_id": "456", "verify": False}, 0)],
        valid + [valid[0]],
    ):
        with pytest.raises(RuntimeError):
            mod._state_count_and_integrity(Db(bad), src)


def test_manual_workflow_stays_owner_only_and_without_deployment():
    wf = (Path(__file__).resolve().parents[1] /
          ".github/workflows/aiven-guild-config-state-seed.yml").read_text()
    assert "  workflow_dispatch:" in wf
    for event in ("  push:", "  schedule:", "  pull_request:", "  workflow_run:"):
        assert event not in wf
    assert "github.actor == 'UglyGameFace'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "SEED_AIVEN_OFFLINE_CONFIG_STATE_ONLY" in wf
    assert "secrets.AIVEN_POSTGRES_CA_PEM" in wf
    assert "secrets.AIVEN_POSTGRES_URI" in wf
    assert "secrets.DANK_SOURCE_DATABASE_URL" in wf
    for unsafe in ("actions/upload-artifact", "discloud/deploy-action", "SUPABASE_SERVICE_ROLE_KEY", "DANK_BACKUP_PASSPHRASE"):
        assert unsafe not in wf


def test_reuse_existing_seed_and_direct_full_row_comparison():
    code = (Path(__file__).resolve().parents[1] /
            "tools/aiven_guild_config_state_seed.py").read_text()
    assert "seed_from_verified_shadow" in code
    assert "verify_shadow(conn, first)" not in code or "_check_source_and_shadow(conn, first)" in code
    assert "compare_guild_config_sources" in code
    assert "postgres_page_reader" in code
    assert "source_page" in code
    assert "again = _source_plan(src)" in code
    assert "select pg_catalog.pg_advisory_xact_lock" in code
    assert "Production routing unchanged: Supabase" in code
    assert "Cutover authorized: NO" in code
    assert "delete from" not in code.lower()
    assert "update dank_core.guild_config_state" not in code.lower()
