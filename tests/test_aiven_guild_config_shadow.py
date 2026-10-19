"""Offline safety tests: no external databases or live Discord activity."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from stoney_verify.core_data_stage import canonical_json, plan_source
from tools import aiven_guild_config_shadow as shadow


def _source(guild="123", pk="one"):
    row = {"id": pk, "guild_id": guild, "settings": {"welcome": "hey"},
           "config": {"welcome": "hey"}, "metadata": {}}
    return plan_source(
        lambda table, keys, offset, count: [row][offset:offset + count],
        selected_tables=["guild_configs"],
    )


class Cursor:
    def __init__(self, records):
        self.records = records
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def execute(self, sql):
        assert "from dank_core.guild_config_shadow order by guild_id" in sql
        return self
    def fetchall(self):
        return self.records


class DB:
    def __init__(self, records):
        self.records = records
    def cursor(self):
        return Cursor(self.records)


def _shadow_record(source):
    item = source.rows[0]
    return (item.guild_id, __import__("json").loads(item.source_pk),
            __import__("json").loads(item.row_json), item.payload_sha256)


def test_preview_and_import_modes_have_distinct_confirmation():
    shadow.validate_mode("preview", "")
    shadow.validate_mode("import", shadow.CONFIRMATION)
    for mode, confirm in (("import", ""), ("import", "STAGE_AIVEN_GUILD_CONFIGS_ONLY"), ("delete", shadow.CONFIRMATION)):
        with pytest.raises(ValueError):
            shadow.validate_mode(mode, confirm)


def test_shadow_verifier_accepts_only_matching_full_row_and_pk():
    src = _source()
    record = _shadow_record(src)
    assert shadow.verify_shadow(DB([]), src) == 0
    assert shadow.verify_shadow(DB([record]), src) == 1
    for corrupt in (
        (record[0], record[1], {**record[2], "settings": {"welcome": "bad"}}, record[3]),
        (record[0], {"id": "wrong"}, record[2], record[3]),
        (record[0], record[1], record[2], "0" * 64),
        ("999", record[1], record[2], record[3]),
    ):
        with pytest.raises(RuntimeError):
            shadow.verify_shadow(DB([corrupt]), src)
    with pytest.raises(RuntimeError):
        shadow.verify_shadow(DB([record, record]), src)


def test_source_stage_guard_never_uses_old_stager_which_refuses_shadow():
    from tools import aiven_guild_config_stage as stage
    script = (Path(__file__).resolve().parents[1] /
              "tools/aiven_guild_config_shadow.py").read_text()
    assert "def _destination_state_for_shadow" in script
    assert "_destination_state(connection)" not in script
    assert "inspect_staged_config(cursor)" in script
    assert "_assert_staged_matches(connection, source)" in script
    assert "import_config_shadow(" in script
    assert "connection.execute(\"set transaction read only\")" in script
    assert "source reread" in script.lower() or "reread = _source_plan(source_opts)" in script
    assert "default_transaction_read_only" not in script
    assert "STORAGE_BUDGET_MB" in script
    assert "delete from" not in script.lower()


def test_manual_owner_only_workflow_no_copy_or_cutover_on_push():
    code = (Path(__file__).resolve().parents[1] /
            ".github/workflows/aiven-guild-config-shadow.yml").read_text()
    assert "  workflow_dispatch:" in code
    for auto in ("  push:", "  schedule:", "  pull_request:", "  workflow_run:"):
        assert auto not in code
    assert "github.actor == 'UglyGameFace'" in code
    assert "github.ref == 'refs/heads/main'" in code
    assert "IMPORT_AIVEN_GUILD_CONFIG_SHADOW_ONLY" in code
    assert "secrets.AIVEN_POSTGRES_URI" in code
    assert "secrets.AIVEN_POSTGRES_CA_PEM" in code
    assert "secrets.DANK_SOURCE_DATABASE_URL" in code
    for unsafe in ("actions/upload-artifact", "discloud/deploy-action", "SUPABASE_SERVICE_ROLE_KEY", "DANK_BACKUP_PASSPHRASE"):
        assert unsafe not in code
