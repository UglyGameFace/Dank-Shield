"""Owner-dispatched Aiven PG18 original-copy stage for 18 remaining core tables.

Point-in-time record preservation only, not runtime table conversion. Sensitive
verification tokens stay restricted and must NEVER be accepted for live auth.
No automatic retention, Supabase writes, bot startup hooks or deploys.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile

from stoney_verify.source_pg_backup import validate_source_dsn
from tools.aiven_empty_staging_schema import validate_target
from tools import aiven_core_operational_stage as core

# Explicitly verified against PostgreSQL 17 pg_constraint on Oct 19, 2026.
# Do not derive identities from a UI request or stage other-app tables.
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "guild_members": ("guild_id", "user_id"),
    "identity_proofs": ("id",),
    "manual_alt_links": ("id",),
    "member_activity_batch_receipts": ("batch_id",),
    "member_activity_notices": ("notice_id",),
    "member_activity_scan_locks": ("guild_id", "user_id"),
    "member_events": ("id",),
    "member_joins": ("id",),
    "ticket_automation_settings": ("guild_id",),
    "ticket_automation_state": ("guild_id", "channel_id"),
    "ticket_counters": ("guild_id",),
    "ticket_macros": ("id",),
    "ticket_messages": ("id",),
    "ticket_panel_categories": ("id",),
    "vc_verify_sessions": ("token",),
    "verification_flags": ("id",),
    "verification_kick_timers": ("channel_id",),
    "verification_tokens": ("token",),
}

RELATION = "dank_migration.aux_staged_originals"
MIGRATION = (Path(__file__).resolve().parents[1] /
             "postgres/migrations/202610190003_aiven_auxiliary_original_staging.sql")
INSTALL_CONFIRMATION = "INSTALL_AIVEN_AUXILIARY_STAGING_SCHEMA_ONLY"
STAGE_CONFIRMATION = "STAGE_AIVEN_AUXILIARY_ORIGINALS_ONLY"
ADVISORY_LOCK = core.CORE_LOCK + 1
MAX_ROWS = 20_000
MAX_BYTES = 32 * 1048576
PAGE_SIZE = 100


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in ("preview", "install", "stage"):
        raise ValueError("unsupported migration mode")
    if mode == "install" and confirmation != INSTALL_CONFIRMATION:
        raise ValueError("install requires owner confirmation")
    if mode == "stage" and confirmation != STAGE_CONFIRMATION:
        raise ValueError("stage requires owner confirmation")
    # Credentials and table names are fixed, not user-editable job inputs.
    if not set(TABLE_KEYS).isdisjoint(core.TABLE_KEYS):
        raise RuntimeError("auxiliary staging overlaps existing operational slice")


def encode_record(table: str, source_json_text: str) -> core.Original:
    if table not in TABLE_KEYS or not isinstance(source_json_text, str):
        raise ValueError("unknown table or invalid source text")
    data = source_json_text.encode("utf-8")
    if not 2 <= len(data) <= core.MAX_RECORD_BYTES:
        raise ValueError("source record exceeds size limit")
    row = json.loads(source_json_text)
    if not isinstance(row, dict):
        raise ValueError("source must be a JSON object")
    pk = TABLE_KEYS[table]
    if any(k not in row or row[k] is None for k in pk):
        raise ValueError("missing source primary key")
    pk_json = core._canonical({key: row[key] for key in pk})
    if len(pk_json.encode("utf-8")) > 8192:
        raise ValueError("source primary key too large")
    guild_id = row.get("guild_id")
    return core.Original(
        source_table=table,
        record_key=sha256(pk_json.encode("utf-8")).hexdigest(),
        source_pk_json=pk_json,
        guild_id=None if guild_id is None else str(guild_id),
        source_json_text=source_json_text,
        source_sha256=sha256(data).hexdigest(),
        raw_bytes=len(data),
    )


def source_snapshot(conn) -> core.Snapshot:
    originals: list[core.Original] = []
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    total_bytes = 0
    for table, columns in TABLE_KEYS.items():
        digest = sha256()
        seen: set[str] = set()
        count = 0
        ordered_by = ",".join("t." + column for column in columns)
        with conn.cursor() as cur:
            cur.execute(
                f"select to_jsonb(t)::text from public.{table} t "
                f"order by {ordered_by}"
            )
            while True:
                batch = cur.fetchmany(PAGE_SIZE)
                if not batch:
                    break
                for (raw_text,) in batch:
                    r = encode_record(table, raw_text)
                    if r.record_key in seen:
                        raise RuntimeError("duplicate or overlapping source key")
                    seen.add(r.record_key)
                    count += 1
                    originals.append(r)
                    total_bytes += r.raw_bytes
                    if len(originals) > MAX_ROWS or total_bytes > MAX_BYTES:
                        raise RuntimeError("source exceeds explicit staging budget")
                    digest.update(r.record_key.encode("ascii"))
                    digest.update(r.source_sha256.encode("ascii"))
        counts[table] = count
        digests[table] = digest.hexdigest()
    return core.Snapshot(tuple(originals), counts, digests, total_bytes)


def snapshot_source(params) -> core.Snapshot:
    with core._connect_source(params) as source:
        with source.transaction():
            source.execute("set transaction isolation level repeatable read, read only")
            return source_snapshot(source)


def target_guard(conn, database: str, *, require_installed: bool) -> tuple[int, bool]:
    used, core_installed = core.destination_guard(
        conn, database, require_table=True
    )
    if not core_installed:
        raise RuntimeError("previous operational staging is not installed")
    with conn.cursor() as cur:
        cur.execute("select count(*) from dank_migration.core_staged_originals")
        if int(cur.fetchone()[0]) <= 0:
            raise RuntimeError("previous operational stage has not been verified")
        cur.execute("select count(*) from dank_core.guild_config_state")
        if not 1 <= int(cur.fetchone()[0]) <= 500:
            raise RuntimeError("offline guild configurations not seeded")
        cur.execute("select to_regclass(%s)", (RELATION,))
        installed = cur.fetchone()[0] is not None
        if installed:
            # Verify expected columns exist before selecting or inserting.
            cur.execute(
                "select source_table,record_key,source_pk_json,guild_id,"
                "source_json_text,source_sha256,raw_bytes "
                "from dank_migration.aux_staged_originals limit 0"
            )
    if require_installed and not installed:
        raise RuntimeError("auxiliary schema not installed")
    return used, installed


def verify_target(conn, snapshot: core.Snapshot) -> int:
    expected = {(r.source_table, r.record_key): r for r in snapshot.originals}
    observed: set[tuple[str, str]] = set()
    with conn.cursor() as cur:
        cur.execute(
            "select source_table,record_key,source_pk_json,guild_id,"
            "source_json_text,source_sha256,raw_bytes "
            "from dank_migration.aux_staged_originals "
            "order by source_table,record_key"
        )
        while True:
            batch = cur.fetchmany(PAGE_SIZE)
            if not batch:
                break
            for table, key, pk_json, guild, body, checksum, raw_bytes in batch:
                marker = (str(table), str(key))
                if marker in observed or marker not in expected:
                    raise RuntimeError("unexpected/duplicate target source key")
                r = expected[marker]
                observed.add(marker)
                if (
                    str(pk_json) != r.source_pk_json
                    or guild != r.guild_id
                    or str(body) != r.source_json_text
                    or str(checksum) != r.source_sha256
                    or int(raw_bytes) != r.raw_bytes
                    or sha256(str(body).encode("utf-8")).hexdigest() != checksum
                ):
                    raise RuntimeError("destination original-row parity mismatch")
    if observed and len(observed) != len(expected):
        raise RuntimeError("partial destination row set; stop")
    return len(observed)


def stage(conn, snapshot: core.Snapshot, database: str) -> int:
    used, _ = target_guard(conn, database, require_installed=True)
    if used + 4 * snapshot.raw_bytes > (
        core.STORAGE_BUDGET_MB * 1048576 * core.MAX_DISK_FRACTION
    ):
        raise RuntimeError("insufficient conservative Aiven storage headroom")
    current = verify_target(conn, snapshot)
    if current not in (0, len(snapshot.originals)):
        raise RuntimeError("partially present staging originals")
    with conn.cursor() as cur:
        cur.executemany(
            "insert into dank_migration.aux_staged_originals "
            "(source_table,record_key,source_pk_json,guild_id,"
            "source_json_text,source_sha256,raw_bytes) "
            "values (%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (source_table,record_key) do nothing",
            [(x.source_table,x.record_key,x.source_pk_json,x.guild_id,
              x.source_json_text,x.source_sha256,x.raw_bytes)
             for x in snapshot.originals],
        )
    if verify_target(conn, snapshot) != len(snapshot.originals):
        raise RuntimeError("destination source parity mismatch")
    with conn.cursor() as cur:
        cur.execute("select pg_database_size(current_database())")
        final_size = int(cur.fetchone()[0])
    if final_size > core.STORAGE_BUDGET_MB * 1048576 * core.MAX_DISK_FRACTION:
        raise RuntimeError("destination budget exceeded; rollback")
    return len(snapshot.originals)


def allocated_sizes(conn) -> tuple[float, float]:
    with conn.cursor() as cur:
        cur.execute(
            "select pg_database_size(current_database()), "
            "coalesce(pg_total_relation_size(to_regclass(%s)),0)",
            (RELATION,),
        )
        return tuple(round(int(n) / 1048576, 2) for n in cur.fetchone())


def main() -> int:
    mode = os.environ.get("AIVEN_AUX_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_AUX_CONFIRMATION", ""))
        target = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            "preview", "",
        )
        source_params = None if mode == "install" else validate_source_dsn(
            os.environ.get("DANK_SOURCE_DATABASE_URL", ""), core.SOURCE_REF
        )
        import psycopg
        fd, ca_path = tempfile.mkstemp(
            prefix="aiven-aux-original-ca-", suffix=".pem"
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(os.environ["AIVEN_POSTGRES_CA_PEM"])
        try:
            target.update({
                "sslmode": "verify-full", "sslrootcert": ca_path,
                "connect_timeout": 10,
                "application_name": "dankshield_aiven_aux_manual",
                "options": "-c statement_timeout=30000 -c lock_timeout=2000",
            })
            with psycopg.connect(**target, autocommit=True) as conn:
                if mode == "install":
                    with conn.transaction():
                        conn.execute(
                            "select pg_catalog.pg_advisory_xact_lock(%s)",
                            (ADVISORY_LOCK,),
                        )
                        _, exists = target_guard(
                            conn, str(target["dbname"]), require_installed=False
                        )
                        if not exists:
                            conn.execute(
                                MIGRATION.read_text("utf-8"), prepare=False
                            )
                            target_guard(
                                conn, str(target["dbname"]), require_installed=True
                            )
                    print("Auxiliary original-only staging schema: INSTALLED")
                else:
                    snapshot = snapshot_source(source_params)
                    with conn.transaction():
                        if mode == "preview":
                            conn.execute("set transaction read only")
                        else:
                            conn.execute(
                                "select pg_catalog.pg_advisory_xact_lock(%s)",
                                (ADVISORY_LOCK,),
                            )
                        _, installed = target_guard(
                            conn, str(target["dbname"]),
                            require_installed=(mode == "stage"),
                        )
                        if mode == "stage":
                            transferred = stage(
                                conn, snapshot, str(target["dbname"])
                            )
                            # A new source transaction catches usual live writes.
                            # It does not quiesce Discord or provide a final delta.
                            reread = snapshot_source(source_params)
                            if (reread.counts != snapshot.counts or
                                    reread.digests != snapshot.digests):
                                raise RuntimeError("live source changed during staging")
                            print("Auxiliary original records staged and verified:",
                                  transferred)
                        elif installed:
                            print("Existing staged originals matching source:",
                                  verify_target(conn, snapshot))
                        else:
                            print("Auxiliary staging schema not yet installed")
                    for name, count in snapshot.counts.items():
                        print(name, "source snapshot rows:", count)
                    print("Original source JSON (KiB):",
                          round(snapshot.raw_bytes / 1024, 2))
                    print("Stage status:",
                          "SOURCE-VERIFIED" if mode == "stage" else "PREVIEW-ONLY")
                db_mib, aux_mib = allocated_sizes(conn)
                print("Aiven allocated MiB (database/auxiliary):", db_mib, aux_mib)
                print("Sensitive token originals: OFFLINE ONLY, not valid auth state.")
                print("Production still Supabase. Source deletions: NONE. Cutover authorized: NO")
                return 0
        finally:
            Path(ca_path).unlink(missing_ok=True)
    except Exception as exc:
        print("Aiven auxiliary staging failed (error type: "
              + type(exc).__name__ + ").")
        return 2


if __name__ == "__main__":
    sys.exit(main())
