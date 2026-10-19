"""Guarded offline Aiven guild configuration seed and source parity audit.

No bot startup path imports this module. This does not route any Discord
operation to Aiven, modify Supabase, or copy data outside guild_configs.
Preview never writes; a seed must be explicitly dispatched by the owner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from stoney_verify.core_data_stage import canonical_json
from stoney_verify.postgres_guild_config_parity import (
    compare_guild_config_sources, postgres_page_reader,
)
from stoney_verify.postgres_guild_config_repo import seed_from_verified_shadow
from stoney_verify.source_pg_backup import validate_source_dsn
from tools.aiven_empty_staging_schema import validate_target
from tools.aiven_guild_config_stage import (
    _assert_staged_matches, _source_plan, SERVICE_LOCK, SOURCE_REF,
    STORAGE_BUDGET_MB,
)
from tools.aiven_guild_config_shadow import verify_shadow

CONFIRMATION = "SEED_AIVEN_OFFLINE_CONFIG_STATE_ONLY"


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in {"preview", "seed"}:
        raise ValueError("unsupported offline config seed mode")
    if mode == "seed" and confirmation != CONFIRMATION:
        raise ValueError("offline seed requires exact owner confirmation")


def _check_source_and_shadow(connection, source) -> None:
    """Reject unknown destination objects, extra rows and stale source."""
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_setting('server_version_num')::integer, "
            "(select count(*) from pg_catalog.pg_tables where schemaname='public'), "
            "(select count(*) from dank_migration.staged_records "
            "where source_table <> 'guild_configs'), "
            "(select count(*) from dank_migration.staged_records "
            "where source_table = 'guild_configs')"
        )
        metadata = cursor.fetchone()
    if (not metadata or int(metadata[0]) // 10000 != 18
            or int(metadata[1]) != 0 or int(metadata[2]) != 0):
        raise RuntimeError("destination schema/version is not approved for guild config seed")
    if int(metadata[3]) != len(source.rows) or not _assert_staged_matches(connection, source):
        raise RuntimeError("source has changed since staging; reject seed")
    if verify_shadow(connection, source) != len(source.rows):
        raise RuntimeError("shadow does not contain every source config")


def _state_count_and_integrity(connection, source) -> int:
    """All existing state must be complete, byte-equivalent, revision zero."""
    expected = {str(rec.guild_id): rec.row_json for rec in source.rows}
    with connection.cursor() as cursor:
        cursor.execute(
            "select guild_id,row_payload,revision "
            "from dank_core.guild_config_state order by guild_id"
        )
        rows = cursor.fetchall()
    if len(rows) > len(expected):
        raise RuntimeError("unapproved extra offline state row")
    seen: set[str] = set()
    for gid, payload, revision in rows:
        gid = str(gid)
        if (gid in seen or gid not in expected or int(revision) != 0
                or canonical_json(payload) != expected[gid]):
            raise RuntimeError("offline state differs from original source config")
        seen.add(gid)
    if rows and len(rows) != len(expected):
        raise RuntimeError("partially seeded offline state is not allowed")
    return len(rows)


def _compare_full_rows(connection, source) -> bool:
    """Diagnostic full-row source snapshot versus PostgreSQL, not cutover."""
    ordered = sorted(source.rows, key=lambda rec: str(rec.guild_id))
    all_rows = [json.loads(item.row_json) for item in ordered]

    def source_page(offset: int, size: int):
        return all_rows[offset:offset + size]

    parity = compare_guild_config_sources(
        source_page, postgres_page_reader(connection),
    )
    return bool(parity.matching and not parity.cutover_authorized)


def main() -> int:
    mode = os.environ.get("AIVEN_OFFLINE_SEED_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_OFFLINE_SEED_CONFIRMATION", ""))
        src = validate_source_dsn(
            os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF,
        )
        dst = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            "preview", "",
        )
        first = _source_plan(src)
        import psycopg
        fd, ca_path = tempfile.mkstemp(prefix="aiven-offline-seed-ca-", suffix=".pem")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as cert:
            cert.write(os.environ["AIVEN_POSTGRES_CA_PEM"])
        try:
            dst.update({
                "sslmode": "verify-full",
                "sslrootcert": ca_path,
                "connect_timeout": 10,
                "application_name": "dankshield_offline_config_seed_manual",
                "options": "-c statement_timeout=15000 -c lock_timeout=2000",
            })
            with psycopg.connect(**dst, autocommit=True) as conn:
                with conn.transaction():
                    if mode == "preview":
                        conn.execute("set transaction read only")
                    else:
                        conn.execute(
                            "select pg_catalog.pg_advisory_xact_lock(%s)",
                            (SERVICE_LOCK,),
                        )
                    _check_source_and_shadow(conn, first)
                    before = _state_count_and_integrity(conn, first)
                    if mode == "seed":
                        seeded = seed_from_verified_shadow(
                            conn, expected_database=str(dst["dbname"]),
                            disk_budget_mb=STORAGE_BUDGET_MB,
                        )
                        if seeded["seeded_guilds"] != len(first.rows):
                            raise RuntimeError("offline seed count mismatch")
                        after = _state_count_and_integrity(conn, first)
                        if after != len(first.rows) or not _compare_full_rows(conn, first):
                            raise RuntimeError("offline config full-row parity failed")
                    else:
                        after = before
                        if after and not _compare_full_rows(conn, first):
                            raise RuntimeError("existing offline config parity failed")
                    # A new real source connection/snapshot catches ordinary
                    # concurrent writes; not a write freeze or cutover guarantee.
                    again = _source_plan(src)
                    if (again.table_counts != first.table_counts
                            or again.table_digests != first.table_digests):
                        raise RuntimeError("Supabase configurations changed; abort transaction")
                if mode == "preview":
                    print("Aiven offline config seed preview: PASS")
                    print("Staged and shadow source configs verified:", len(first.rows))
                    print("Existing offline state configs:", before)
                    print("No database writes performed.")
                else:
                    print("Aiven offline config seed: SUCCESS")
                    print("Original source configs matched by full row:", after)
                    print("Offline revisions: zero")
                    print("Source reread matched; stage and shadow preserved.")
                print("Production routing unchanged: Supabase")
                print("Cutover authorized: NO")
                return 0
        finally:
            Path(ca_path).unlink(missing_ok=True)
    except Exception as exc:
        # No SQL, row contents, guild identifiers, DSNs or exception messages.
        print("Aiven offline config seed check failed (error type: " +
              type(exc).__name__ + ").")
        return 2


if __name__ == "__main__":
    sys.exit(main())
