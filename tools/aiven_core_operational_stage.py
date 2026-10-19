"""Manual Aiven PG18 exact-text operational staging: never bot runtime.

Stages the existing first-slice SOURCE_KEYS tables except guild_configs,
which already have independently validated staging/shadow/state. Source is
always read-only. The destination gets strictly verified, immutable copies
in a NEW private table, not in existing guild-config or historic archive tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from stoney_verify.core_data_stage import SOURCE_KEYS
from stoney_verify.source_pg_backup import validate_source_dsn
from tools.aiven_empty_staging_schema import validate_target
from tools.aiven_guild_config_stage import (
    SOURCE_REF, SERVICE_LOCK, STORAGE_BUDGET_MB,
)

TABLE_KEYS = {name: keys for name, keys in SOURCE_KEYS.items()
              if name != "guild_configs"}
EXPECTED_TABLES = frozenset((
    "guild_security_settings", "guild_role_rules", "guild_roles",
    "member_activity_ledger", "member_activity_tracker_state",
    "member_cleanup_settings", "member_verify_timers",
    "ticket_categories", "ticket_panels", "ticket_panel_presets",
    "ticket_panel_rules", "tickets", "ticket_notes",
    "ticket_form_responses", "dank_community_hub_settings",
))
SCHEMA_FILE = (Path(__file__).resolve().parents[1] /
               "postgres/migrations/202610190002_aiven_core_operational_staging.sql")
SCHEMA_CONFIRMATION = "INSTALL_AIVEN_CORE_STAGING_SCHEMA_ONLY"
STAGE_CONFIRMATION = "STAGE_AIVEN_CORE_OPERATIONAL_ONLY"
CORE_LOCK = SERVICE_LOCK + 8
MAX_RECORDS = 20_000
MAX_TOTAL_SOURCE_BYTES = 24 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
PAGE_SIZE = 100
MAX_DISK_FRACTION = 0.70


@dataclass(frozen=True)
class Original:
    source_table: str
    record_key: str
    source_pk_json: str
    guild_id: str | None
    source_json_text: str
    source_sha256: str
    raw_bytes: int


@dataclass(frozen=True)
class Snapshot:
    originals: tuple[Original, ...]
    counts: dict[str, int]
    digests: dict[str, str]
    raw_bytes: int


def validate_mode(mode: str, confirmation: str) -> None:
    confirm = {"preview": "", "install": SCHEMA_CONFIRMATION,
               "stage": STAGE_CONFIRMATION}
    if mode not in confirm or (mode != "preview" and confirmation != confirm[mode]):
        raise ValueError("invalid mode or missing exact confirmation")
    if frozenset(TABLE_KEYS) != EXPECTED_TABLES:
        raise RuntimeError("the operational table allowlist has changed")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":"))


def encode_original(table: str, raw_json: str) -> Original:
    """Retain exact source JSON text, even if it contains high-precision decimals.

    The source uses PostgreSQL to_jsonb(t)::text, avoiding float/Decimal
    conversion in the copy and permitting exact SHA verification on Aiven.
    """
    if table not in TABLE_KEYS or not isinstance(raw_json, str):
        raise ValueError("unapproved source or invalid JSON text")
    raw_bytes = raw_json.encode("utf-8")
    if not 2 <= len(raw_bytes) <= MAX_RECORD_BYTES:
        raise ValueError("source record exceeds size budget")
    row = json.loads(raw_json)
    if not isinstance(row, dict):
        raise ValueError("source must return a JSON object")
    keys = TABLE_KEYS[table]
    if any(key not in row or row[key] is None for key in keys):
        raise ValueError("source logical primary key is missing")
    pk_json = _canonical({key: row[key] for key in keys})
    if len(pk_json.encode("utf-8")) > 8192:
        raise ValueError("source key exceeds database bound")
    guild_id = row.get("guild_id")
    # Preserve legacy, nonstandard guild ID values and NULLs as found.
    # These rows need a later authoritative ownership reconciliation,
    # not silent deletion or reassignment.
    guild_text = None if guild_id is None else str(guild_id)
    return Original(
        table, sha256(pk_json.encode("utf-8")).hexdigest(),
        pk_json, guild_text, raw_json,
        sha256(raw_bytes).hexdigest(), len(raw_bytes),
    )


def source_snapshot(source) -> Snapshot:
    originals = []
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    raw_bytes = 0
    for table, keys in TABLE_KEYS.items():
        digest = sha256()
        seen_keys: set[str] = set()
        count = 0
        # In-code fixed SQL identifiers, never supplied by the workflow input.
        order = ", ".join(f"t.{key}" for key in keys)
        with source.cursor() as cursor:
            cursor.execute(
                f"select to_jsonb(t)::text from public.{table} t order by {order}"
            )
            while True:
                batch = cursor.fetchmany(PAGE_SIZE)
                if not batch:
                    break
                for (row_json,) in batch:
                    item = encode_original(table, row_json)
                    if item.record_key in seen_keys:
                        raise RuntimeError("source has overlapping/duplicate primary keys")
                    seen_keys.add(item.record_key)
                    originals.append(item)
                    count += 1
                    raw_bytes += item.raw_bytes
                    if (len(originals) > MAX_RECORDS or
                            raw_bytes > MAX_TOTAL_SOURCE_BYTES):
                        raise RuntimeError("source records exceed explicit limits")
                    digest.update(item.record_key.encode("ascii"))
                    digest.update(item.source_sha256.encode("ascii"))
        counts[table] = count
        digests[table] = digest.hexdigest()
    return Snapshot(tuple(originals), counts, digests, raw_bytes)


def destination_guard(connection, expected_db: str, *, require_table: bool):
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_setting('server_version_num')::integer, "
            "current_database(),pg_database_size(current_database()),"
            "(select count(*) from pg_catalog.pg_tables where schemaname='public'),"
            "to_regclass('dank_core.guild_config_shadow'), "
            "to_regclass('dank_migration.staged_records'), "
            "to_regclass('dank_migration.core_staged_originals')"
        )
        state = cursor.fetchone()
        if (not state or int(state[0]) // 10000 != 18
                or str(state[1]) != expected_db or int(state[3]) != 0
                or state[4] is None or state[5] is None):
            raise RuntimeError("destination is not the approved isolated Aiven PG18")
        cursor.execute("select count(*) from dank_core.guild_config_shadow")
        if not 1 <= int(cursor.fetchone()[0]) <= 500:
            raise RuntimeError("original guild configuration shadow is missing")
        cursor.execute(
            "select count(*) from dank_migration.staged_records "
            "where source_table <> 'guild_configs'"
        )
        if int(cursor.fetchone()[0]) != 0:
            raise RuntimeError("original guild config staging must stay isolated")
        if require_table and state[6] is None:
            raise RuntimeError("core operational staging schema not installed")
    return int(state[2]), state[6] is not None


def verify_existing(connection, snapshot: Snapshot) -> int:
    expected = {(r.source_table, r.record_key): r for r in snapshot.originals}
    seen: set[tuple[str, str]] = set()
    with connection.cursor() as cur:
        cur.execute(
            "select source_table, record_key, source_pk_json, guild_id, "
            "source_json_text,source_sha256,raw_bytes "
            "from dank_migration.core_staged_originals order by source_table,record_key"
        )
        while True:
            rows = cur.fetchmany(PAGE_SIZE)
            if not rows:
                break
            for table, key, pk, gid, body, digest, raw_bytes in rows:
                marker = (str(table), str(key))
                if marker in seen or marker not in expected:
                    raise RuntimeError("staging contains unexpected/duplicate source records")
                seen.add(marker)
                record = expected[marker]
                if (str(pk) != record.source_pk_json or gid != record.guild_id
                        or str(body) != record.source_json_text
                        or str(digest) != record.source_sha256
                        or int(raw_bytes) != record.raw_bytes
                        or sha256(str(body).encode("utf-8")).hexdigest() != digest):
                    raise RuntimeError("staging original differs from source snapshot")
    if seen and len(seen) != len(expected):
        raise RuntimeError("staging only has a partial source snapshot")
    return len(seen)


def insert_snapshot(connection, snapshot: Snapshot, expected_db: str) -> int:
    # The destination transaction is owned by the caller and rolls back
    # every insert if a hash, size, source or quota check fails.
    used, installed = destination_guard(connection, expected_db, require_table=True)
    projection = used + 4 * snapshot.raw_bytes
    if projection > STORAGE_BUDGET_MB * 1048576 * MAX_DISK_FRACTION:
        raise RuntimeError("insufficient Aiven disk headroom for operational staging")
    prior = verify_existing(connection, snapshot)
    if prior not in (0, len(snapshot.originals)):
        raise RuntimeError("staging partial parity")
    with connection.cursor() as cur:
        cur.executemany(
            "insert into dank_migration.core_staged_originals "
            "(source_table,record_key,source_pk_json,guild_id,"
            "source_json_text,source_sha256,raw_bytes) "
            "values (%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (source_table,record_key) do nothing",
            [(r.source_table,r.record_key,r.source_pk_json,r.guild_id,
              r.source_json_text,r.source_sha256,r.raw_bytes)
             for r in snapshot.originals],
        )
    if verify_existing(connection, snapshot) != len(snapshot.originals):
        raise RuntimeError("destination original-row parity failed")
    with connection.cursor() as cur:
        cur.execute("select pg_database_size(current_database())")
        final = int(cur.fetchone()[0])
    if final > STORAGE_BUDGET_MB * 1048576 * MAX_DISK_FRACTION:
        raise RuntimeError("Aiven stage exceeded conservative disk budget")
    return len(snapshot.originals)


def storage_usage_mib(connection) -> tuple[float, float, float]:
    """Aggregate allocated bytes only; never read row content or member IDs."""
    with connection.cursor() as cursor:
        cursor.execute(
            "select pg_database_size(current_database()), "
            "coalesce(pg_total_relation_size("
            "to_regclass('dank_archive.original_rows')), 0), "
            "coalesce(pg_total_relation_size("
            "to_regclass('dank_migration.core_staged_originals')), 0)"
        )
        values = cursor.fetchone()
    return tuple(round(int(value) / 1048576, 2) for value in values)


def _connect_source(params):
    import psycopg
    return psycopg.connect(
        host=params["PGHOST"],port=int(params["PGPORT"]),
        user=params["PGUSER"],password=params["PGPASSWORD"],
        dbname=params["PGDATABASE"],sslmode="require",
        connect_timeout=10, autocommit=True,
        application_name="dankshield_aiven_core_source_readonly",
        options="-c default_transaction_read_only=on -c statement_timeout=25000",
    )


def _read_source(params) -> Snapshot:
    with _connect_source(params) as source:
        with source.transaction():
            source.execute("set transaction isolation level repeatable read, read only")
            return source_snapshot(source)


def main() -> int:
    mode = os.environ.get("AIVEN_CORE_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_CORE_CONFIRMATION", ""))
        target = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""), "preview", "",
        )
        source_params = None
        if mode != "install":
            source_params = validate_source_dsn(
                os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
            )
        import psycopg
        fd, ca_path = tempfile.mkstemp(prefix="aiven-core-staging-ca-",suffix=".pem")
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="ascii") as output:
            output.write(os.environ["AIVEN_POSTGRES_CA_PEM"])
        try:
            target.update({
                "sslmode":"verify-full","sslrootcert":ca_path,
                "connect_timeout":10,
                "application_name":"dankshield_core_staging_manual",
                "options":"-c statement_timeout=30000 -c lock_timeout=2000",
            })
            with psycopg.connect(**target,autocommit=True) as dest:
                if mode == "install":
                    with dest.transaction():
                        dest.execute(
                            "select pg_catalog.pg_advisory_xact_lock(%s)",
                            (CORE_LOCK,),
                        )
                        _,installed = destination_guard(
                            dest,str(target["dbname"]),require_table=False,
                        )
                        if not installed:
                            dest.execute(SCHEMA_FILE.read_text("utf-8"), prepare=False)
                            destination_guard(
                                dest,str(target["dbname"]),require_table=True,
                            )
                    print("Aiven core operational staging schema: INSTALLED")
                else:
                    snapshot = _read_source(source_params)
                    with dest.transaction():
                        if mode == "preview":
                            dest.execute("set transaction read only")
                        else:
                            dest.execute(
                                "select pg_catalog.pg_advisory_xact_lock(%s)",
                                (CORE_LOCK,),
                            )
                        used,installed = destination_guard(
                            dest,str(target["dbname"]),
                            require_table=(mode=="stage"),
                        )
                        if mode=="stage":
                            total=insert_snapshot(dest,snapshot,str(target["dbname"]))
                            # Independent fresh read, after destination writes and
                            # before atomic destination commit. Not a write freeze.
                            after=_read_source(source_params)
                            if (after.counts!=snapshot.counts or
                                    after.digests!=snapshot.digests):
                                raise RuntimeError("Supabase source changed while staging")
                            print("Operational original records staged and verified:",total)
                        elif installed:
                            print("Aiven staged originals matching source:",
                                  verify_existing(dest,snapshot))
                        else:
                            print("Operational staging schema not yet installed")
                    for table, count in snapshot.counts.items():
                        print(table,"source snapshot rows:",count)
                    print("Original source payload (KiB):",
                          round(snapshot.raw_bytes/1024,2))
                    print("Stage status:",
                          "SOURCE-VERIFIED" if mode=="stage" else "PREVIEW-ONLY")
                db_mib, history_mib, core_mib = storage_usage_mib(dest)
                print("Aiven allocated MiB (database/history/core):",
                      db_mib, history_mib, core_mib)
                print("Production still Supabase. Source deletions: NONE. Cutover authorized: NO")
                return 0
        finally:
            Path(ca_path).unlink(missing_ok=True)
    except Exception as exc:
        # Never expose source SQL, individual IDs, member content, DSNs or secrets.
        print("Aiven operational staging failed (error type: " +
              type(exc).__name__ + ").")
        return 2


if __name__=="__main__":
    sys.exit(main())
