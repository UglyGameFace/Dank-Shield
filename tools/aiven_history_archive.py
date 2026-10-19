"""Manual, full-fidelity compressed history archive on isolated Aiven PG18.

Source PostgreSQL is read-only in one repeatable-read snapshot. Each original
record is stored as compressed canonical JSON with a SHA-256 checksum. This
never deletes source rows, changes live guild configuration, enables retention
or routes a Discord command to Aiven. The archive is NOT an independent backup.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zlib

from stoney_verify.core_data_stage import canonical_json
from stoney_verify.source_pg_backup import validate_source_dsn
from tools.aiven_empty_staging_schema import validate_target
from tools.aiven_guild_config_stage import SOURCE_REF, STORAGE_BUDGET_MB, SERVICE_LOCK

TABLE_KEYS = {
    "guild_config_versions": "version_id",
    "staff_action_logs": "id",
    "activity_feed_events": "id",
    "dank_invite_block_events": "event_hash",
}
INSTALL_CONFIRMATION = "INSTALL_AIVEN_HISTORY_ARCHIVE_SCHEMA_ONLY"
ARCHIVE_CONFIRMATION = "ARCHIVE_AIVEN_HISTORY_ONLY"
SCHEMA_FILE = (
    Path(__file__).resolve().parents[1]
    / "postgres/migrations/202610190001_aiven_compressed_history_archive.sql"
)
MAX_ROWS = 30_000
MAX_SOURCE_BYTES = 180 * 1024 * 1024
MAX_COMPRESSED_BYTES = 90 * 1024 * 1024
MAX_ROW_BYTES = 1024 * 1024
BATCH_SIZE = 100
MAX_DISK_FRACTION = 0.70
HISTORY_LOCK = SERVICE_LOCK + 7


def validate_mode(mode: str, confirmation: str) -> None:
    expected = {
        "preview": "",
        "install": INSTALL_CONFIRMATION,
        "archive": ARCHIVE_CONFIRMATION,
    }
    if mode not in expected or (mode != "preview" and confirmation != expected[mode]):
        raise ValueError("invalid archive mode or missing exact owner confirmation")


def pack_record(table: str, key: str, guild_id: str, created_at: datetime, data: dict | str):
    """Preserve the exact source PostgreSQL JSONB textual representation.

    Source rows arrive as to_jsonb(t)::text, NOT client-decoded JSON numbers;
    this retains decimal precision and every original feed field.
    Dict input exists only for deterministic unit fixtures.
    """
    if table not in TABLE_KEYS:
        raise ValueError("unapproved source table")
    pk = TABLE_KEYS[table]
    try:
        raw_text = data if isinstance(data, str) else canonical_json(data)
        parsed = json.loads(raw_text)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid source JSON")
    if not isinstance(parsed, dict) or str(parsed.get(pk)) != str(key):
        raise ValueError("original row has mismatched source primary key")
    if not isinstance(key, str) or not 1 <= len(key) <= 80:
        raise ValueError("invalid source key")
    if not isinstance(guild_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", guild_id):
        raise ValueError("invalid guild ID")
    if str(parsed.get("guild_id")) != guild_id:
        raise ValueError("original row has mismatched guild ID")
    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise ValueError("original row is missing timestamp")
    raw = raw_text.encode("utf-8")
    if not 2 <= len(raw) <= MAX_ROW_BYTES:
        raise ValueError("source record exceeds archive bounds")
    compressed = zlib.compress(raw, level=6)
    if not 2 <= len(compressed) <= MAX_ROW_BYTES:
        raise ValueError("compressed record exceeds archive bounds")
    return (table, key, guild_id, created_at, sha256(raw).hexdigest(), len(raw), compressed)


def unpack_record(payload: bytes, checksum: str, size: int) -> dict:
    """Bound decompression and verify exact canonical contents on every read."""
    inflater = zlib.decompressobj()
    raw = inflater.decompress(payload, MAX_ROW_BYTES + 1)
    if (len(raw) > MAX_ROW_BYTES or not inflater.eof or inflater.unused_data
            or inflater.unconsumed_tail or inflater.flush()):
        raise ValueError("invalid or oversized compressed archive record")
    if len(raw) != size or sha256(raw).hexdigest() != checksum:
        raise ValueError("original archive record checksum mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("archive contains non-object JSON")
    return value


def source_preflight(connection) -> dict[str, tuple[int, int]]:
    counts = {}
    total_rows = total_bytes = 0
    for table in TABLE_KEYS:
        # All identifiers come from the in-code allowlist, never user input.
        with connection.cursor() as cursor:
            cursor.execute(
                f"select count(*), coalesce(sum(octet_length(to_jsonb(t)::text)),0) "
                f"from public.{table} t"
            )
            count, size = cursor.fetchone()
        count, size = int(count), int(size)
        total_rows += count
        total_bytes += size
        if count < 0 or size < 0 or total_rows > MAX_ROWS or total_bytes > MAX_SOURCE_BYTES:
            raise RuntimeError("source history exceeds explicitly bounded quota")
        counts[table] = (count, size)
    return counts


def _destination_guard(connection, db_name: str, *, require_archive: bool):
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_setting('server_version_num')::integer, "
            "current_database(), pg_database_size(current_database()), "
            "(select count(*) from pg_catalog.pg_tables where schemaname='public'), "
            "to_regclass('dank_core.guild_config_shadow'), "
            "to_regclass('dank_migration.staged_records'), "
            "to_regclass('dank_archive.original_rows')"
        )
        metadata = cursor.fetchone()
        if (not metadata or int(metadata[0]) // 10000 != 18
                or str(metadata[1]) != db_name or int(metadata[3]) != 0
                or metadata[4] is None or metadata[5] is None):
            raise RuntimeError("destination is not the approved isolated Aiven PG18")
        cursor.execute("select count(*) from dank_core.guild_config_shadow")
        if not 1 <= int(cursor.fetchone()[0]) <= 500:
            raise RuntimeError("verified guild-config shadow must exist before archiving")
        installed = metadata[6] is not None
        if require_archive and not installed:
            raise RuntimeError("archive schema is not installed")
    return int(metadata[2]), installed


def verify_batch(connection, records: list[tuple]) -> None:
    table = records[0][0]
    keys = [row[1] for row in records]
    with connection.cursor() as cursor:
        cursor.execute(
            "select record_key,guild_id,event_at,source_sha256,raw_bytes,compressed_payload "
            "from dank_archive.original_rows "
            "where source_table=%s and record_key=any(%s)", (table, keys)
        )
        actual = {str(row[0]): row for row in cursor.fetchall()}
    if set(actual) != set(keys):
        raise RuntimeError("history archive is missing a source record")
    for record in records:
        _, key, guild, timestamp, digest, raw_size, _ = record
        stored = actual[key]
        if (str(stored[1]) != guild or stored[2] != timestamp
                or str(stored[3]) != digest or int(stored[4]) != raw_size):
            raise RuntimeError("archived record metadata differs from source")
        row = unpack_record(bytes(stored[5]), str(stored[3]), int(stored[4]))
        if str(row[TABLE_KEYS[table]]) != key or str(row["guild_id"]) != guild:
            raise RuntimeError("archived record content differs from source")


def _write_batch(connection, records: list[tuple]) -> None:
    if not records:
        return
    with connection.cursor() as cursor:
        cursor.executemany(
            "insert into dank_archive.original_rows "
            "(source_table,record_key,guild_id,event_at,source_sha256,raw_bytes,compressed_payload) "
            "values (%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (source_table,record_key) do nothing",
            records,
        )
    verify_batch(connection, records)


def archive_source(connection, source, counts: dict[str, tuple[int, int]]) -> dict[str, int]:
    """All inserts and post-write parity checks run in one destination tx."""
    imported = {}
    total_raw = total_compressed = 0
    budget_bytes = STORAGE_BUDGET_MB * 1024 * 1024
    for table, pk in TABLE_KEYS.items():
        batch = []
        seen = 0
        with source.cursor(name=f"dank_history_{table}") as stream:
            stream.itersize = BATCH_SIZE
            stream.execute(
                f"select t.{pk}::text, t.guild_id::text, t.created_at, to_jsonb(t)::text "
                f"from public.{table} t order by t.{pk}"
            )
            while True:
                page = stream.fetchmany(BATCH_SIZE)
                if not page:
                    break
                for key, guild, timestamp, payload in page:
                    rec = pack_record(table, str(key), str(guild), timestamp, payload)
                    total_raw += rec[5]
                    total_compressed += len(rec[6])
                    seen += 1
                    if (total_raw > MAX_SOURCE_BYTES
                            or total_compressed > MAX_COMPRESSED_BYTES):
                        raise RuntimeError("history payload exceeds safe archive budget")
                    batch.append(rec)
                _write_batch(connection, batch)
                batch.clear()
        if seen != counts[table][0]:
            raise RuntimeError("source snapshot count changed during archive")
        imported[table] = seen
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from dank_archive.original_rows where source_table=%s",
                (table,),
            )
            if int(cursor.fetchone()[0]) < seen:
                raise RuntimeError("archive table count is below source snapshot")
    with connection.cursor() as cursor:
        cursor.execute("select pg_database_size(current_database())")
        used = int(cursor.fetchone()[0])
    # 4x compressed size allows for indexes and WAL/transient overhead.
    if used + 4 * total_compressed > budget_bytes * MAX_DISK_FRACTION:
        raise RuntimeError("Aiven archive would exceed conservative disk headroom")
    return imported


def main() -> int:
    mode = os.environ.get("AIVEN_HISTORY_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_HISTORY_CONFIRMATION", ""))
        target = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            "preview", "",
        )
        src = None
        if mode != "install":
            src = validate_source_dsn(
                os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
            )
        import psycopg
        fd, ca_path = tempfile.mkstemp(prefix="aiven-history-ca-", suffix=".pem")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as cert:
            cert.write(os.environ["AIVEN_POSTGRES_CA_PEM"])
        target.update({
            "sslmode": "verify-full", "sslrootcert": ca_path,
            "connect_timeout": 10, "application_name": "dankshield_history_archive_manual",
            "options": "-c statement_timeout=30000 -c lock_timeout=2000",
        })
        try:
            with psycopg.connect(**target, autocommit=True) as dest:
                if mode == "install":
                    with dest.transaction():
                        dest.execute("select pg_catalog.pg_advisory_xact_lock(%s)", (HISTORY_LOCK,))
                        _destination_guard(dest, str(target["dbname"]), require_archive=False)
                        dest.execute(SCHEMA_FILE.read_text("utf-8"))
                        _destination_guard(dest, str(target["dbname"]), require_archive=True)
                    print("Private Aiven history archive schema: INSTALLED")
                else:
                    with psycopg.connect(
                        host=src["PGHOST"], port=int(src["PGPORT"]),
                        user=src["PGUSER"], password=src["PGPASSWORD"],
                        dbname=src["PGDATABASE"], sslmode="require",
                        connect_timeout=10, autocommit=True,
                        application_name="dankshield_history_source_readonly",
                        options="-c default_transaction_read_only=on -c statement_timeout=30000",
                    ) as source:
                        with source.transaction():
                            source.execute("set transaction isolation level repeatable read, read only")
                            counts = source_preflight(source)
                            with dest.transaction():
                                if mode == "preview":
                                    dest.execute("set transaction read only")
                                else:
                                    dest.execute(
                                        "select pg_catalog.pg_advisory_xact_lock(%s)", (HISTORY_LOCK,)
                                    )
                                used, installed = _destination_guard(
                                    dest, str(target["dbname"]), require_archive=(mode == "archive")
                                )
                                if used > STORAGE_BUDGET_MB * 1024 * 1024 * MAX_DISK_FRACTION:
                                    raise RuntimeError("Aiven database already exceeds staging headroom")
                                if mode == "archive":
                                    results = archive_source(dest, source, counts)
                                else:
                                    results = {table: count for table, (count, _) in counts.items()}
                            for table in TABLE_KEYS:
                                print(table, "source snapshot rows:", results[table])
                            print("Archive schema installed:", installed)
                            print("Archive status:", "SOURCE-VERIFIED" if mode == "archive" else "PREVIEW-ONLY")
                            if mode == "preview":
                                print("Source raw JSON size (MiB):",
                                      round(sum(size for _, size in counts.values()) / 1048576, 2))
                                print("Neither database was modified.")
                print("Production still Supabase. Source deletions: NONE. Cutover authorized: NO")
                return 0
        finally:
            Path(ca_path).unlink(missing_ok=True)
    except Exception as exc:
        # Never print source content, SQL, guild/user IDs, URLs, credentials, or raw exceptions.
        print("Aiven history archive operation failed (error type: "
              + type(exc).__name__ + ").")
        return 2


if __name__ == "__main__":
    sys.exit(main())
