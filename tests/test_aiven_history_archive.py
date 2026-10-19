"""No-network contracts for the isolated, lossless Aiven history archive."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zlib

import pytest

from stoney_verify.core_data_stage import canonical_json
from tools import aiven_history_archive as archive


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def sample(table="activity_feed_events", key="event-1", guild="1514374173517152418"):
    column = archive.TABLE_KEYS[table]
    value = {
        column: key,
        "guild_id": guild,
        "created_at": NOW.isoformat(),
        "message": "Full sensitive history 🌿",
        "metadata": {"original": [1, None, True], "role": "admin"},
    }
    return value


def test_owner_confirmation_is_required_for_each_write_mode():
    archive.validate_mode("preview", "")
    archive.validate_mode("install", archive.INSTALL_CONFIRMATION)
    archive.validate_mode("archive", archive.ARCHIVE_CONFIRMATION)
    for mode, confirmation in (
        ("install", ""), ("install", archive.ARCHIVE_CONFIRMATION),
        ("archive", ""), ("archive", archive.INSTALL_CONFIRMATION),
        ("delete", archive.ARCHIVE_CONFIRMATION),
    ):
        with pytest.raises(ValueError):
            archive.validate_mode(mode, confirmation)


@pytest.mark.parametrize("table,key", [
    ("guild_config_versions", "17836"),
    ("staff_action_logs", "1234"),
    ("activity_feed_events", "12345678-1234-1234-1234-123456789abc"),
    ("dank_invite_block_events", "a" * 64),
])
def test_compressed_archive_roundtrips_every_original_field(table, key):
    row = sample(table, key=key)
    record = archive.pack_record(table, key, row["guild_id"], NOW, row)
    decoded = archive.unpack_record(record[6], record[4], record[5])
    assert decoded == row
    assert record[4] == sha256(canonical_json(row).encode()).hexdigest()
    assert len(record[6]) < record[5]


def test_decompression_fails_closed_on_tampering_size_drift_and_bombs():
    row = sample()
    rec = archive.pack_record("activity_feed_events", "event-1", row["guild_id"], NOW, row)
    with pytest.raises(ValueError):
        archive.unpack_record(rec[6], "a" * 64, rec[5])
    with pytest.raises(ValueError):
        archive.unpack_record(rec[6], rec[4], rec[5] + 1)
    with pytest.raises((ValueError, zlib.error)):
        archive.unpack_record(rec[6][:-3], rec[4], rec[5])
    huge = zlib.compress(b"x" * (archive.MAX_ROW_BYTES + 10))
    with pytest.raises(ValueError):
        archive.unpack_record(huge, "a" * 64, archive.MAX_ROW_BYTES + 10)
    invalid_json = b'["not an object"]'
    with pytest.raises(ValueError):
        archive.unpack_record(zlib.compress(invalid_json), sha256(invalid_json).hexdigest(), len(invalid_json))


def test_archived_json_text_retains_exact_decimal_precision():
    original = (
        '{"id":"event-1","guild_id":"1514374173517152418",'
        '"created_at":"2026-10-19T12:00:00+00:00",'
        '"exact_decimal":1234567890.12345678901234567890}'
    )
    record = archive.pack_record("activity_feed_events", "event-1",
                                 "1514374173517152418", NOW, original)
    assert archive.unpack_original_json(record[6], record[4], record[5]) == original


def test_unapproved_tables_and_mismatched_identity_are_rejected():
    row = sample()
    for table, key, guild in (
        ("users", "event-1", row["guild_id"]),
        ("activity_feed_events", "wrong-id", row["guild_id"]),
        ("activity_feed_events", "event-1", "999"),
        ("activity_feed_events", "event-1", "0"),
    ):
        with pytest.raises(ValueError):
            archive.pack_record(table, key, guild, NOW, row)
    with pytest.raises(ValueError):
        archive.pack_record("activity_feed_events", "event-1", row["guild_id"],
                            datetime(2026, 10, 19), row)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.inserted = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, sql, params=None):
        assert "source_table=%s and record_key=any(%s)" in sql
        assert params[0] in archive.TABLE_KEYS
    def executemany(self, sql, records):
        assert "on conflict (source_table,record_key) do nothing" in sql
        self.inserted.extend(records)
    def fetchall(self):
        return self.rows


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.cursors = []
    def cursor(self):
        c = FakeCursor(self.rows)
        self.cursors.append(c)
        return c


def test_existing_archive_rows_are_verified_even_on_conflict():
    row = sample()
    record = archive.pack_record("activity_feed_events", "event-1", row["guild_id"], NOW, row)
    stored = (record[1], record[2], record[3], record[4], record[5], record[6])
    db = FakeDb([stored])
    archive._write_batch(db, [record])
    assert db.cursors[0].inserted == [record]
    assert len(db.cursors) == 2
    wrong_digest = list(stored)
    wrong_digest[3] = "0" * 64
    with pytest.raises(RuntimeError, match="differs"):
        archive._write_batch(FakeDb([tuple(wrong_digest)]), [record])
    with pytest.raises(RuntimeError, match="missing"):
        archive._write_batch(FakeDb([]), [record])


def test_workflow_is_manual_owner_only_and_never_deploys_or_exports_sensitive_rows():
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/aiven-compressed-history-archive.yml").read_text("utf-8")
    assert "  workflow_dispatch:" in workflow
    assert "options: [preview, install, archive]" in workflow
    assert "github.actor == 'UglyGameFace'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    for event in ("  push:", "  schedule:", "  pull_request:", "  workflow_run:"):
        assert event not in workflow
    for unsafe in ("actions/upload-artifact", "discloud/deploy-action",
                   "SUPABASE_SERVICE_ROLE_KEY", "DANK_BACKUP_PASSPHRASE"):
        assert unsafe not in workflow
    assert "secrets.DANK_SOURCE_DATABASE_URL" in workflow
    assert "secrets.AIVEN_POSTGRES_URI" in workflow


def test_archive_schema_private_and_not_runtime_routed():
    sql = archive.SCHEMA_FILE.read_text("utf-8").lower()
    assert "create schema if not exists dank_archive" in sql
    assert "revoke all on schema dank_archive from public" in sql
    assert "revoke all on table dank_archive.original_rows from public" in sql
    for table in archive.TABLE_KEYS:
        assert table in sql
    for dangerous in ("drop table", "truncate ", "delete from", "set search_path", "grant all"):
        assert dangerous not in sql
    code = (Path(__file__).resolve().parents[1] /
            "tools/aiven_history_archive.py").read_text("utf-8").lower()
    assert "default_transaction_read_only=on" in code
    assert "set transaction isolation level repeatable read, read only" in code
    assert "on conflict (source_table,record_key) do nothing" in code
    assert "source deletions: none" in code
    assert "cutover authorized: no" in code
    assert "delete from" not in code
    assert "update dank_core.guild_config_state" not in code
    assert "pg_database_size(current_database())" in code
