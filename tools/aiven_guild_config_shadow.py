"""Explicit Aiven guild-config shadow import. NOT a production configuration switch.

Checks all 12 original configuration rows against Supabase and Aiven staging
before any shadow insert. Never changes Supabase, other tables or the active
future guild_config_state table. All writes are guarded and transactional.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile

from stoney_verify.core_data_stage import canonical_json
from stoney_verify.postgres_guild_config_shadow import (
    import_config_shadow, inspect_staged_config,
)
from tools.aiven_empty_staging_schema import validate_target
from tools.aiven_guild_config_stage import (
    _assert_staged_matches, _source_plan, _destination_state,
    SOURCE_REF, STORAGE_BUDGET_MB, SERVICE_LOCK,
)
from stoney_verify.source_pg_backup import validate_source_dsn

CONFIRMATION = "IMPORT_AIVEN_GUILD_CONFIG_SHADOW_ONLY"
SHADOW_LOCK = SERVICE_LOCK  # prevent racing same migration slice


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in {"preview", "import"}:
        raise ValueError("unrecognized shadow mode")
    if mode == "import" and confirmation != CONFIRMATION:
        raise ValueError("exact shadow import confirmation is required")


def verify_shadow(connection, source) -> int:
    """Validate all shadow JSON bytes/hashes, including source PK; reject extras."""
    with connection.cursor() as cursor:
        cursor.execute(
            "select guild_id,source_pk,raw_row,source_sha256 "
            "from dank_core.guild_config_shadow order by guild_id"
        )
        existing = cursor.fetchall()
    expected = {str(item.guild_id): item for item in source.rows}
    if len(existing) > len(expected):
        raise RuntimeError("shadow contains unexpected extra guild records")
    seen = set()
    for gid, pk, row, checksum in existing:
        gid = str(gid)
        if gid in seen or gid not in expected:
            raise RuntimeError("shadow contains duplicate or unexpected guild")
        seen.add(gid)
        record = expected[gid]
        digest = sha256(canonical_json(row).encode("utf-8")).hexdigest()
        if (str(checksum) != record.payload_sha256 or digest != record.payload_sha256
                or canonical_json(row) != record.row_json
                or canonical_json(pk) != record.source_pk):
            raise RuntimeError("shadow differs from verified source snapshot")
    return len(existing)


def verify_source_stage(connection, source) -> None:
    """Require a complete source-to-stage equality before importing anything."""
    _version, staged_count, _bytes = _destination_state_for_shadow(connection)
    if staged_count != len(source.rows) or not _assert_staged_matches(connection, source):
        raise RuntimeError("Supabase source changed since initial staging")
    with connection.cursor() as cursor:
        staged = inspect_staged_config(cursor)
    if staged["guild_count"] != len(source.rows):
        raise RuntimeError("staged payload content is incomplete")


def _destination_state_for_shadow(connection) -> tuple[int, int, int]:
    """Independent destination guard: original stager forbids existing shadow."""
    with connection.cursor() as cur:
        cur.execute(
            "select current_setting('server_version_num')::integer, "
            "(select count(*) from dank_core.guild_config_state), "
            "(select count(*) from pg_catalog.pg_tables where schemaname='public')"
        )
        row = cur.fetchone()
        if not row or int(row[0]) // 10000 != 18:
            raise RuntimeError("PostgreSQL 18 required")
        if row[1] or row[2]:
            raise RuntimeError("active configuration or public schema unexpectedly populated")
        cur.execute(
            "select count(*) from dank_migration.staged_records "
            "where source_table <> 'guild_configs'"
        )
        if cur.fetchone()[0]:
            raise RuntimeError("unexpected data in migration staging")
        cur.execute(
            "select count(*),coalesce(sum(payload_bytes),0) "
            "from dank_migration.staged_records "
            "where source_table='guild_configs'"
        )
        count, size = cur.fetchone()
    return int(row[0]), int(count), int(size)


def main() -> int:
    mode = os.environ.get("AIVEN_SHADOW_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_SHADOW_CONFIRMATION", ""))
        source_opts = validate_source_dsn(
            os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
        )
        target = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            "preview", "",
        )
        first = _source_plan(source_opts)
        import psycopg
        cert = os.environ["AIVEN_POSTGRES_CA_PEM"]
        fd, cert_path = tempfile.mkstemp(prefix="aiven-shadow-ca-", suffix=".pem")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(cert)
        try:
            target.update({
                "sslmode": "verify-full",
                "sslrootcert": cert_path,
                "connect_timeout": 10,
                "application_name": "dankshield_config_shadow_manual",
                "options": "-c statement_timeout=15000 -c lock_timeout=2000",
            })
            with psycopg.connect(**target, autocommit=True) as connection:
                with connection.transaction():
                    if mode == "preview":
                        connection.execute("set transaction read only")
                    else:
                        connection.execute(
                            "select pg_catalog.pg_advisory_xact_lock(%s)",
                            (SHADOW_LOCK,),
                        )
                    verify_source_stage(connection, first)
                    prior_count = verify_shadow(connection, first)
                    if mode == "import":
                        result = import_config_shadow(
                            connection,
                            expected_database=str(target["dbname"]),
                            disk_budget_mb=STORAGE_BUDGET_MB,
                        )
                        if result["guild_count"] != len(first.rows):
                            raise RuntimeError("unexpected shadow import count")
                        after_count = verify_shadow(connection, first)
                        if after_count != len(first.rows):
                            raise RuntimeError("post-import source row parity failed")
                    reread = _source_plan(source_opts)
                    if (reread.table_counts != first.table_counts
                            or reread.table_digests != first.table_digests):
                        raise RuntimeError("Supabase config rows changed; reject shadow import")
                if mode == "preview":
                    print("Aiven guild-config shadow preview: PASS")
                    print("Staged configs matching current Supabase:", len(first.rows))
                    print("Shadow configs already verified:", prior_count)
                    print("No databases modified; production still Supabase.")
                else:
                    print("Aiven guild-config shadow import: SUCCESS")
                    print("Full-fidelity shadow configs verified:", after_count)
                    print("Source reread matched; active config table unchanged.")
                print("Cutover authorized: NO")
                return 0
        finally:
            Path(cert_path).unlink(missing_ok=True)
    except Exception as exc:
        # Never leak passwords, guild IDs, row payloads, SQL or usernames.
        print("Aiven shadow step failed (error type: " + type(exc).__name__ + ").")
        return 2


if __name__ == "__main__":
    sys.exit(main())
