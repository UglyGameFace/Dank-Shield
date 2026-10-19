"""No-network fail-closed contracts for Aiven's 15 operational source tables."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from stoney_verify.core_data_stage import SOURCE_KEYS
from tools import aiven_core_operational_stage as core


def fixture(table="ticket_notes", key="8f77c2bd-ec2a-45f2-b740-3424bc172e6d",
            gid=None):
    keys = core.TABLE_KEYS[table]
    payload = {
        **{col: (key if len(keys)==1 else f"{key}-{i}")
           for i,col in enumerate(keys)},
        "guild_id": gid,
        "message": "A legacy record that must not be dropped",
        "exact_decimal": 1234567890,
    }
    return payload


def raw(row):
    return json.dumps(row,separators=(",", ":"),ensure_ascii=False)


def test_allowlist_is_exact_existing_first_slice_minus_guild_configs():
    assert core.EXPECTED_TABLES == frozenset(SOURCE_KEYS) - {"guild_configs"}
    assert len(core.TABLE_KEYS)==15
    assert "guild_configs" not in core.TABLE_KEYS
    assert "activity_feed_events" not in core.TABLE_KEYS
    assert "staff_action_logs" not in core.TABLE_KEYS


def test_confirmations_for_all_write_modes_are_separate():
    core.validate_mode("preview", "")
    core.validate_mode("install", core.SCHEMA_CONFIRMATION)
    core.validate_mode("stage", core.STAGE_CONFIRMATION)
    for mode,confirm in (("stage",""),("install",""),
                         ("stage",core.SCHEMA_CONFIRMATION),
                         ("install",core.STAGE_CONFIRMATION),
                         ("delete",core.STAGE_CONFIRMATION)):
        with pytest.raises(ValueError):
            core.validate_mode(mode,confirm)


def test_exact_original_source_text_preserves_precision_and_nonstandard_guilds():
    source=(
        '{"id":"original","guild_id":null,'
        '"exact_decimal":1234567890.123456789012345678901234567890}'
    )
    r=core.encode_original("ticket_notes",source)
    assert r.guild_id is None
    assert r.source_json_text==source
    assert r.source_sha256==sha256(source.encode()).hexdigest()
    assert r.raw_bytes==len(source.encode())
    x=core.encode_original("guild_role_rules",
            '{"guild_id":"legacy-guild","role_id":"role-1","active":true}')
    assert x.guild_id=="legacy-guild"
    assert json.loads(x.source_pk_json)=={
        "guild_id":"legacy-guild","role_id":"role-1",
    }


def test_composite_key_is_deterministic_and_distinct():
    row1={"guild_id":"legacy","role_id":"one","active":True}
    row2={"guild_id":"legacy","role_id":"two","active":True}
    one=core.encode_original("guild_role_rules",raw(row1))
    reordered=core.encode_original("guild_role_rules",raw({
        "active":True,"role_id":"one","guild_id":"legacy"
    }))
    two=core.encode_original("guild_role_rules",raw(row2))
    assert one.record_key==reordered.record_key
    assert one.record_key!=two.record_key
    assert one.source_sha256!=reordered.source_sha256  # byte-exact, not sorted copy


def test_source_pk_missing_unknown_or_invalid_records_rejected():
    with pytest.raises(ValueError):
        core.encode_original("guild_configs",'{"id":"0"}')
    with pytest.raises(ValueError):
        core.encode_original("ticket_notes",'{"guild_id":"1234"}')
    with pytest.raises(ValueError):
        core.encode_original("ticket_notes",'["bad"]')
    with pytest.raises(ValueError):
        core.encode_original("ticket_notes",'{"id":null}')
    with pytest.raises(ValueError):
        core.encode_original("ticket_notes",'not-json')
    with pytest.raises(ValueError):
        core.encode_original("ticket_notes", '{"id":"a","data":"'
                             + "x"*(core.MAX_RECORD_BYTES+10)+'"}')


class FakeSourceCursor:
    def __init__(self,rows):
        self.rows=rows
        self.pos=0
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def execute(self,sql):
        self.pos=0
        self.rows=self.rows(sql)
        return self
    def fetchmany(self,size):
        result=self.rows[self.pos:self.pos+size]
        self.pos+=len(result)
        return result


class FakeSource:
    def __init__(self,rows_by_table):
        self.data=rows_by_table
    def cursor(self):
        def rows(sql):
            for name in core.TABLE_KEYS:
                if f"public.{name} " in sql:
                    return [(value,) for value in self.data.get(name,())]
            raise AssertionError("source query escaped allowlist")
        return FakeSourceCursor(rows)


def test_read_only_snapshot_keeps_empty_tables_and_all_source_keys():
    data={
        "tickets":[raw({"id":"ticket-1","guild_id":"1234"})],
        "ticket_notes":[raw({"id":"note-1","guild_id":None})],
        "guild_role_rules":[raw({"guild_id":"legacy","role_id":"x"})],
    }
    p=core.source_snapshot(FakeSource(data))
    assert p.counts["tickets"]==1 and p.counts["ticket_notes"]==1
    assert p.counts["guild_role_rules"]==1
    assert p.counts["member_verify_timers"]==0
    assert len(p.counts)==15 and len(p.originals)==3


def test_duplicate_source_composite_keys_fail_closed():
    duplicate=raw({"guild_id":"legacy","role_id":"same"})
    with pytest.raises(RuntimeError,match="duplicate primary keys"):
        core.source_snapshot(FakeSource({"guild_role_rules":[duplicate,duplicate]}))


class DestinationCursor:
    def __init__(self,rows):
        self.rows=rows
        self.pos=0
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def execute(self,sql):
        assert "from dank_migration.core_staged_originals" in sql
        self.pos=0
    def fetchmany(self,size):
        out=self.rows[self.pos:self.pos+size]
        self.pos+=len(out)
        return out


class Destination:
    def __init__(self,rows):
        self.rows=rows
    def cursor(self):
        return DestinationCursor(self.rows)


def test_stage_verifies_original_text_and_rejects_extras_or_tampering():
    p=core.source_snapshot(FakeSource({
        "ticket_notes":[raw({"id":"n1","guild_id":None,"message":"hi"})]
    }))
    r=p.originals[0]
    actual=(r.source_table,r.record_key,r.source_pk_json,
            r.guild_id,r.source_json_text,r.source_sha256,r.raw_bytes)
    assert core.verify_existing(Destination([actual]),p)==1
    assert core.verify_existing(Destination([]),p)==0
    bad=list(actual)
    bad[4]='{"id":"n1","guild_id":null,"message":"tampered"}'
    with pytest.raises(RuntimeError,match="differs"):
        core.verify_existing(Destination([tuple(bad)]),p)
    bad=list(actual)
    bad[1]="a"*64
    with pytest.raises(RuntimeError,match="unexpected"):
        core.verify_existing(Destination([tuple(bad)]),p)


def test_aggregate_storage_metrics_never_fetch_source_rows():
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self,*_):
            return False
        def execute(self,sql):
            assert "pg_database_size(current_database())" in sql
            assert "pg_total_relation_size" in sql
            assert "source_json_text" not in sql
        def fetchone(self):
            return (10485760, 1048576, 262144)
    class Database:
        def cursor(self):
            return Cursor()
    assert core.storage_usage_mib(Database()) == (10.0, 1.0, 0.25)


def test_workflow_is_owner_dispatch_only_without_deploy_or_sensitive_artifacts():
    wf=(Path(__file__).resolve().parents[1]/
        ".github/workflows/aiven-core-operational-staging.yml").read_text()
    assert "  workflow_dispatch:" in wf
    assert "options: [preview, install, stage]" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "github.actor == 'UglyGameFace'" in wf
    assert "secrets.DANK_SOURCE_DATABASE_URL" in wf
    assert "secrets.AIVEN_POSTGRES_URI" in wf
    # This job also runs the lifecycle contract, which imports Discord/Supabase modules.
    # Keep the manual runner dependencies aligned with the isolated PG17 CI runner.
    for requirement in (
        "\"discord.py>=2.6,<3\"",
        "\"supabase>=2.24,<3\"",
        "\"python-dotenv>=1,<1.1\"",
    ):
        assert requirement in wf
    for danger in ("  push:","  schedule:","  workflow_run:",
                   "actions/upload-artifact","discloud/deploy-action",
                   "SUPABASE_SERVICE_ROLE_KEY"):
        assert danger not in wf


def test_sql_and_code_never_change_original_history_or_config_state():
    sql=core.SCHEMA_FILE.read_text().lower()
    assert "dank_migration.core_staged_originals" in sql
    assert "revoke all on table dank_migration.core_staged_originals from public" in sql
    for name in core.TABLE_KEYS:
        assert name in sql
    for illegal in ("drop table", "truncate table", "delete from","grant all"):
        assert illegal not in sql
    code=(Path(__file__).resolve().parents[1]/
          "tools/aiven_core_operational_stage.py").read_text().lower()
    assert "default_transaction_read_only=on" in code
    assert "set transaction isolation level repeatable read, read only" in code
    assert "on conflict (source_table,record_key) do nothing" in code
    assert "source deletions: none" in code
    assert "cutover authorized: no" in code
    assert "delete from" not in code
    assert "update dank_core.guild_config_state" not in code
