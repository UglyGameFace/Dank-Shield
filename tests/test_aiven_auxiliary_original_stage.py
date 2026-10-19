"""No-network and disposable-PG contracts for auxiliary original-only staging."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from tools import aiven_auxiliary_original_stage as aux
from tools import aiven_core_operational_stage as core


def test_fixed_nonoverlapping_source_allowlist_and_db_pk_map():
    assert len(aux.TABLE_KEYS) == 18
    assert not (set(aux.TABLE_KEYS) & set(core.TABLE_KEYS))
    assert aux.TABLE_KEYS["ticket_automation_state"] == ("guild_id", "channel_id")
    assert aux.TABLE_KEYS["member_activity_scan_locks"] == ("guild_id", "user_id")
    assert aux.TABLE_KEYS["guild_members"] == ("guild_id", "user_id")
    assert aux.TABLE_KEYS["verification_tokens"] == ("token",)
    assert aux.TABLE_KEYS["vc_verify_sessions"] == ("token",)
    assert "global_accounts" not in aux.TABLE_KEYS
    assert "guild_profiles" not in aux.TABLE_KEYS
    assert "world" not in aux.TABLE_KEYS
    assert "users" not in aux.TABLE_KEYS


def test_preview_and_distinct_write_confirmations():
    aux.validate_mode("preview", "")
    aux.validate_mode("install", aux.INSTALL_CONFIRMATION)
    aux.validate_mode("stage", aux.STAGE_CONFIRMATION)
    for mode, key in (("install",""),("stage",""),
                      ("install",aux.STAGE_CONFIRMATION),
                      ("stage",aux.INSTALL_CONFIRMATION),
                      ("delete",aux.STAGE_CONFIRMATION)):
        with pytest.raises(ValueError):
            aux.validate_mode(mode,key)


def test_sensitive_tokens_originals_remain_exact_and_never_logged():
    raw='{"token":"opaque-persisted-credential","guild_id":null,' \
        '"amount":12345678901234567890.1234567890123456789}'
    original=aux.encode_record("verification_tokens",raw)
    assert original.source_json_text == raw
    assert original.guild_id is None
    assert original.source_sha256 == sha256(raw.encode()).hexdigest()
    assert "opaque-persisted-credential" in original.source_pk_json
    assert original.record_key == sha256(
        original.source_pk_json.encode("utf-8")
    ).hexdigest()


def test_exact_composite_keys_and_null_preservation():
    first=aux.encode_record("guild_members",
        '{"guild_id":"1234","user_id":"5678","value":1}')
    reordered=aux.encode_record("guild_members",
        '{"value":1,"user_id":"5678","guild_id":"1234"}')
    distinct=aux.encode_record("guild_members",
        '{"guild_id":"1234","user_id":"5679","value":1}')
    assert first.record_key == reordered.record_key
    assert first.source_sha256 != reordered.source_sha256
    assert first.record_key != distinct.record_key


def test_missing_pk_invalid_json_and_row_bounds_rejected():
    for table,body in (
        ("ticket_messages",'{"ticket_id":"x"}'),
        ("verification_tokens",'{"token":null}'),
        ("guild_members",'{"guild_id":"1","user_id":null}'),
        ("ticket_counters",'["bad"]'),
        ("guild_members","not-json"),
        ("guild_configs",'{"id":"1"}'),
        ("ticket_messages",'{"id":"x","oversize":"'+"x"*(core.MAX_RECORD_BYTES+1)+'"}'),
    ):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            aux.encode_record(table,body)


class SourceCursor:
    def __init__(self, dispatcher):
        self.dispatcher=dispatcher
        self.rows=[]
        self.index=0
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def execute(self,sql):
        match=[name for name in aux.TABLE_KEYS if f"public.{name} " in sql]
        assert len(match)==1
        self.rows=[(x,) for x in self.dispatcher.get(match[0],[])]
        self.index=0
    def fetchmany(self,size):
        data=self.rows[self.index:self.index+size]
        self.index+=len(data)
        return data


class Source:
    def __init__(self,records):
        self.records=records
    def cursor(self):
        return SourceCursor(self.records)


def test_snapshot_empty_tables_counts_and_immutable_key_digests():
    base={
        "guild_members":['{"guild_id":"123","user_id":"456","active":true}'],
        "ticket_messages":['{"id":"old-message","guild_id":"123"}'],
        "verification_tokens":['{"token":"old-token","guild_id":"123"}'],
    }
    p=aux.source_snapshot(Source(base))
    assert len(p.counts)==18
    assert sum(p.counts.values())==3
    assert p.counts["ticket_macros"]==0
    assert p.raw_bytes>0
    assert p.digests["guild_members"] != p.digests["verification_tokens"]


def test_duplicate_source_key_fails_closed():
    val='{"token":"duplicate","guild_id":"123"}'
    with pytest.raises(RuntimeError,match="duplicate"):
        aux.source_snapshot(Source({"verification_tokens":[val,val]}))


class DestCursor:
    def __init__(self,rows):
        self.rows=rows
        self.cursor=0
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def execute(self,query):
        assert "dank_migration.aux_staged_originals" in query
        self.cursor=0
    def fetchmany(self,n):
        page=self.rows[self.cursor:self.cursor+n]
        self.cursor+=len(page)
        return page


class Dest:
    def __init__(self,rows):
        self.rows=rows
    def cursor(self):
        return DestCursor(self.rows)


def test_target_verification_fails_on_tampering_and_unexpected_pk():
    p=aux.source_snapshot(Source({
        "ticket_messages":['{"id":"m1","guild_id":"999","content":"hello"}']
    }))
    r=p.originals[0]
    actual=(r.source_table,r.record_key,r.source_pk_json,r.guild_id,
            r.source_json_text,r.source_sha256,r.raw_bytes)
    assert aux.verify_target(Dest([actual]),p)==1
    assert aux.verify_target(Dest([]),p)==0
    altered=list(actual)
    altered[4]='{"id":"m1","guild_id":"999","content":"changed"}'
    with pytest.raises(RuntimeError,match="parity"):
        aux.verify_target(Dest([tuple(altered)]),p)
    altered=list(actual)
    altered[1]="0"*64
    with pytest.raises(RuntimeError,match="unexpected"):
        aux.verify_target(Dest([tuple(altered)]),p)


def test_workflow_is_owner_only_and_no_production_secrets_or_artifacts():
    wf=(Path(__file__).resolve().parents[1] /
        ".github/workflows/aiven-auxiliary-original-staging.yml").read_text()
    assert "  workflow_dispatch:" in wf
    assert "options: [preview, install, stage]" in wf
    assert "github.actor == 'UglyGameFace'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "fetch-depth: 0" in wf
    assert "secrets.DANK_SOURCE_DATABASE_URL" in wf
    assert "secrets.AIVEN_POSTGRES_URI" in wf
    assert '"discord.py>=2.6,<3"' in wf
    for unsafe in ("  push:", "  schedule:", "  workflow_run:",
                   "actions/upload-artifact", "DISCORD_TOKEN",
                   "SUPABASE_SERVICE_ROLE_KEY", "discloud/deploy-action"):
        assert unsafe not in wf


def test_fixed_migration_is_private_not_public_or_auto_pruned():
    sql=aux.MIGRATION.read_text("utf-8").lower()
    assert aux.RELATION in sql
    assert "revoke all on table dank_migration.aux_staged_originals from public" in sql
    for name in aux.TABLE_KEYS:
        assert f"'{name}'" in sql
    for danger in ("drop table","truncate table","delete from",
                   "grant all","create trigger","on delete cascade"):
        assert danger not in sql
    code=(Path(__file__).resolve().parents[1] /
          "tools/aiven_auxiliary_original_stage.py").read_text()
    assert "set transaction isolation level repeatable read, read only" in code
    assert "on conflict (source_table,record_key) do nothing" in code
    assert "Cutover authorized: NO" in code
    assert "verify-full" in code


@pytest.mark.skipif(
    os.environ.get("RUN_PG_GUILD_CONFIG_INTEGRATION") != "1",
    reason="requires isolated PostgreSQL service with existing staging schema",
)
def test_disposable_postgres_install_copy_verify_and_rollback(monkeypatch):
    """No real external database! CI provides a disposable localhost PG17."""
    import psycopg

    dsn=os.environ["DANK_TEST_DATABASE_URL"]
    assert "127.0.0.1" in dsn or "localhost" in dsn
    rows=Source({
        "guild_members":['{"guild_id":"1","user_id":"2","active":true}'],
        "verification_tokens":['{"token":"temporary-test-only","guild_id":"1"}'],
    })
    snapshot=aux.source_snapshot(rows)
    with psycopg.connect(dsn,autocommit=True) as conn:
        assert conn.execute(
            "select to_regclass('dank_migration.aux_staged_originals')"
        ).fetchone()[0] is None
        db, aux_size=aux.allocated_sizes(conn)
        assert db>0 and aux_size==0
        class ExpectedRollback(Exception):
            pass
        with pytest.raises(ExpectedRollback):
            with conn.transaction():
                conn.execute(aux.MIGRATION.read_text("utf-8"),prepare=False)
                monkeypatch.setattr(aux, "target_guard",
                    lambda _conn,_database,*,require_installed: (100000,True))
                expected_database=conn.execute("select current_database()").fetchone()[0]
                assert aux.stage(conn,snapshot,expected_database)==2
                assert aux.verify_target(conn,snapshot)==2
                assert aux.stage(conn,snapshot,expected_database)==2
                assert aux.allocated_sizes(conn)[1]>0
                raise ExpectedRollback
        assert conn.execute(
            "select to_regclass('dank_migration.aux_staged_originals')"
        ).fetchone()[0] is None
