from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from stoney_verify import postgres_guild_config_shadow as shadow
from stoney_verify.core_data_stage import canonical_json

ROOT = Path(__file__).resolve().parents[1]


def entry(guild="123", uid="a1", *, settings=None):
    raw = {"guild_id": guild, "id": uid,
           "settings": settings if settings is not None else {"verify": True},
           "config": {}, "metadata": {}, "meta": {}}
    size = len(canonical_json(raw).encode("utf-8"))
    return ({"id": uid}, raw, sha256(canonical_json(raw).encode()).hexdigest(), size)


class FakeCursor:
    def __init__(self, connection):
        self.c = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        low = " ".join(sql.lower().split())
        self.c.queries.append(low)
        assert self.c.in_txn or self.c.allow_read_without_transaction
        if low.startswith("select current_database(),pg_database_size"):
            self.rows = [(self.c.name, self.c.used_bytes)]
        elif low.startswith("select to_regclass"):
            self.rows = [("dank_migration.staged_records",
                          "dank_core.guild_config_shadow")]
        elif "from dank_migration.staged_records" in low:
            self.rows = self.c.staged[:params[0]]
        elif low.startswith("insert into dank_core.guild_config_shadow"):
            guild, _pk, _raw, digest = params
            if guild not in self.c.shadow:
                payload = next(x[1] for x in self.c.staged if x[1]["guild_id"] == guild)
                self.c.shadow[guild] = (deepcopy(payload), digest)
                self.rows = [(digest,)]
            else:
                self.rows = []
        elif low.startswith("select source_sha256 from dank_core.guild_config_shadow"):
            item = self.c.shadow.get(params[0])
            self.rows = [(item[1],)] if item else []
        elif low.startswith("select guild_id, source_sha256 from dank_core.guild_config_shadow"):
            self.rows = sorted((k, v[1]) for k, v in self.c.shadow.items())
        elif low.startswith("select raw_row,source_sha256 from dank_core.guild_config_shadow"):
            item = self.c.shadow.get(params[0])
            self.rows = [item] if item else []
        elif low.startswith("select pg_database_size"):
            self.rows = [(self.c.used_bytes,)]
        else:
            raise AssertionError("unexpected SQL statement")
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeTxn:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        assert not self.connection.in_txn
        self.connection.snapshot = deepcopy(self.connection.shadow)
        self.connection.in_txn = True
        return self

    def __exit__(self, error_type, *_rest):
        if error_type:
            self.connection.shadow = self.connection.snapshot
        self.connection.in_txn = False
        return False


class FakeConnection:
    def __init__(self, staged):
        self.staged = list(staged)
        self.shadow = {}
        self.name = "postgres"
        self.used_bytes = 5 * 1024 * 1024
        self.queries = []
        self.in_txn = False
        self.allow_read_without_transaction = True

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTxn(self)


def test_readonly_source_inspector_contains_no_user_settings():
    conn = FakeConnection([entry()])
    result = shadow.inspect_staged_config(conn.cursor())
    assert result["guild_count"] == 1
    assert len(result["source_digest"]) == 64
    assert "verify" not in str(result)
    assert not conn.shadow


def test_shadow_import_is_idempotent_and_does_not_touch_other_tables():
    conn = FakeConnection([entry("123", "a"), entry("456", "b")])
    result = shadow.import_config_shadow(conn, expected_database="postgres",
                                         disk_budget_mb=100)
    assert result == {
        "guild_count": 2, "result": "shadow_verified_only",
        "live_guild_storage_changed": False
    }
    shadow.import_config_shadow(conn, expected_database="postgres",
                                disk_budget_mb=100)
    assert len(conn.shadow) == 2
    assert shadow.read_shadow_config(conn, 123)["settings"] == {"verify": True}
    assert not any("insert into public." in query for query in conn.queries)


def test_shadow_rejects_different_existing_row_and_rolls_back():
    conn = FakeConnection([entry("123", "a")])
    conn.shadow["123"] = ({"guild_id": "123", "id": "a"}, "0" * 64)
    with pytest.raises(RuntimeError, match="do not overwrite"):
        shadow.import_config_shadow(conn, expected_database="postgres",
                                    disk_budget_mb=100)
    assert conn.shadow["123"][1] == "0" * 64


def test_shadow_cannot_import_to_wrong_database_or_unknown_disk_budget():
    conn = FakeConnection([entry()])
    with pytest.raises(RuntimeError, match="unexpected"):
        shadow.import_config_shadow(conn, expected_database="voicebot",
                                    disk_budget_mb=100)
    with pytest.raises(ValueError, match="verified disk"):
        shadow.import_config_shadow(conn, expected_database="postgres",
                                    disk_budget_mb=0)
    with pytest.raises(ValueError, match="identity"):
        shadow.import_config_shadow(conn, expected_database="postgres;drop",
                                    disk_budget_mb=100)
    assert not conn.shadow


def test_staging_hash_validation_fails_closed():
    source_pk, raw, digest, size = entry()
    conn = FakeConnection([(source_pk, raw, "0" * 64, size)])
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        shadow.inspect_staged_config(conn.cursor())
    assert digest != "0" * 64


def test_staging_source_pk_and_payload_size_validation():
    pk, raw, digest, size = entry()
    conn = FakeConnection([({"id": "wrong"}, raw, digest, size)])
    with pytest.raises(RuntimeError, match="primary key mismatch"):
        shadow.inspect_staged_config(conn.cursor())
    conn = FakeConnection([(pk, raw, digest, size + 1)])
    with pytest.raises(RuntimeError, match="size mismatch"):
        shadow.inspect_staged_config(conn.cursor())


def test_duplicate_guild_rows_rejected_even_with_distinct_source_ids():
    conn = FakeConnection([entry("123", "a"), entry("123", "b")])
    with pytest.raises(RuntimeError, match="same guild"):
        shadow.inspect_staged_config(conn.cursor())


def test_disk_pressure_blocks_shadow_write():
    conn = FakeConnection([entry()])
    conn.used_bytes = 85 * 1024 * 1024
    with pytest.raises(RuntimeError, match="headroom"):
        shadow.import_config_shadow(conn, expected_database="postgres",
                                    disk_budget_mb=100)
    assert not conn.shadow


def test_tampering_prevents_read():
    conn = FakeConnection([entry()])
    conn.shadow["123"] = ({"guild_id": "123", "id": "a",
                            "settings": {"verify": False}}, "0" * 64)
    with pytest.raises(RuntimeError, match="integrity"):
        shadow.read_shadow_config(conn, 123)
    assert shadow.read_shadow_config(conn, 456) is None


def test_shadow_schema_is_isolated_and_import_has_no_startup_switch():
    sql = (ROOT / "postgres/migrations/202610130003_guild_config_shadow.sql").read_text()
    assert "create schema if not exists dank_core" in sql.lower()
    assert "create table if not exists dank_core.guild_config_shadow" in sql.lower()
    assert "drop table" not in sql.lower()
    assert "public.dank_verify_reminder_delivery" not in sql.lower()
    assert "delete from" not in sql.lower()
    script = (ROOT / "tools/materialize_dankshield_pg_config.py").read_text()
    assert 'if __name__ == "__main__":' in script
    assert '--apply' in script


def test_high_guild_count_blocks_unbounded_import():
    source = [entry(str(1000 + i), str(i)) for i in range(501)]
    conn = FakeConnection(source)
    with pytest.raises(RuntimeError, match="guild count exceeds"):
        shadow.inspect_staged_config(conn.cursor())
    assert not conn.shadow
