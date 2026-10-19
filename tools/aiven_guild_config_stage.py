"""Manually copy ONLY Dank Shield guild configuration snapshots to Aiven staging.

This operation does NOT switch the bot, create database objects, seed live
configuration, or read unrelated tables from the shared Supabase project.
No credentials, guild IDs, payloads or raw connection exceptions are logged.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile

from stoney_verify.core_data_stage import apply_stage, plan_source, SourcePlan
from stoney_verify.source_pg_backup import validate_source_dsn
from tools.aiven_empty_staging_schema import validate_target

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
CONFIRMATION = "STAGE_AIVEN_GUILD_CONFIGS_ONLY"
STORAGE_BUDGET_MB = 900  # conservative ceiling within 1 GB Aiven free plan
MAX_GUILDS = 500
MAX_SOURCE_BYTES = 12 * 1024 * 1024
SERVICE_LOCK = 71421040


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in ("preview", "stage"):
        raise ValueError("unsupported migration stage mode")
    if mode == "stage" and confirmation != CONFIRMATION:
        raise ValueError("exact owner confirmation required for staged writes")


def validate_first_slice(plan: SourcePlan) -> None:
    if set(plan.table_counts) != {"guild_configs"}:
        raise ValueError("only guild_configs may be copied")
    if not 1 <= plan.table_counts["guild_configs"] <= MAX_GUILDS:
        raise ValueError("source guild configuration count is outside safe bound")
    if plan.accounted_bytes > MAX_SOURCE_BYTES:
        raise ValueError("source guild configurations exceed safe byte budget")
    guilds: set[str] = set()
    for row in plan.rows:
        gid = str(row.guild_id or "")
        if row.source_table != "guild_configs" or not re.fullmatch(
            r"[1-9][0-9]{0,19}", gid
        ):
            raise ValueError("invalid guild configuration identifier")
        if gid in guilds:
            raise ValueError("duplicate guild configurations")
        guilds.add(gid)


def _source_plan(source_options: dict) -> SourcePlan:
    import psycopg

    # Supabase source is ALWAYS read-only. No REST service-role access.
    with psycopg.connect(
        host=source_options["PGHOST"],
        port=int(source_options["PGPORT"]),
        user=source_options["PGUSER"],
        password=source_options["PGPASSWORD"],
        dbname=source_options["PGDATABASE"],
        sslmode="require",
        connect_timeout=10,
        application_name="dankshield_aiven_config_stage_source",
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
        autocommit=True,
    ) as connection:
        with connection.transaction():
            connection.execute(
                "set transaction isolation level repeatable read, read only"
            )

            def page(table: str, keys: tuple[str, ...], offset: int, count: int):
                if table != "guild_configs" or keys != ("id",):
                    raise ValueError("unapproved Supabase source table")
                records = connection.execute(
                    "select to_jsonb(g) from public.guild_configs AS g "
                    "order by g.id limit %s offset %s",
                    (count, offset),
                ).fetchall()
                if any(not isinstance(item[0], dict) for item in records):
                    raise RuntimeError("source returned invalid JSON record")
                return [row[0] for row in records]

            plan = plan_source(
                page, selected_tables=["guild_configs"],
                page_size=100, max_bytes=MAX_SOURCE_BYTES,
            )
            validate_first_slice(plan)
            return plan


def _destination_state(connection) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_setting('server_version_num')::integer, "
            "(select count(*) from dank_core.guild_config_shadow), "
            "(select count(*) from dank_core.guild_config_state)"
        )
        result = cursor.fetchone()
        if not result or len(result) != 3 or int(result[0]) // 10000 != 18:
            raise RuntimeError("destination is not PostgreSQL 18 with expected schema")
        if result[1] or result[2]:
            raise RuntimeError("shadow or future live config already populated; refuse staging")
        cursor.execute("select to_regclass('dank_migration.staged_records')")
        if cursor.fetchone()[0] is None:
            raise RuntimeError("Aiven staging table is not installed")
        cursor.execute(
            "select count(*) from pg_catalog.pg_tables where schemaname='public'"
        )
        if cursor.fetchone()[0] != 0:
            raise RuntimeError("unexpected public tables in destination")
        cursor.execute(
            "select count(*) from dank_migration.staged_records "
            "where source_table <> 'guild_configs'"
        )
        if cursor.fetchone()[0] != 0:
            raise RuntimeError("unexpected other source data already staged")
        cursor.execute(
            "select count(*),coalesce(sum(payload_bytes),0) "
            "from dank_migration.staged_records "
            "where source_table='guild_configs'"
        )
        rows, size = cursor.fetchone()
        return int(result[0]), int(rows), int(size)


def _assert_staged_matches(connection, plan: SourcePlan) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "select record_key,payload_sha256 "
            "from dank_migration.staged_records "
            "where source_table='guild_configs' order by record_key"
        )
        target = cursor.fetchall()
    expect = sorted((r.key_hash, r.payload_sha256) for r in plan.rows)
    return target == expect


def main() -> int:
    mode = os.environ.get("AIVEN_GUILD_CONFIG_MODE", "preview")
    try:
        validate_mode(mode, os.environ.get("AIVEN_GUILD_CONFIG_CONFIRMATION", ""))
        src = validate_source_dsn(
            os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
        )
        target = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            "preview", "",
        )
        first = _source_plan(src)
        print("Supabase guild configuration source: read-only snapshot verified")
        print("Source guild configuration count:", len(first.rows))
        print("Source snapshot size (KiB):", round(first.accounted_bytes / 1024, 1))
        import psycopg
        ca_text = os.environ["AIVEN_POSTGRES_CA_PEM"]
        fd, path = tempfile.mkstemp(prefix="dankshield-aiven-config-ca-", suffix=".pem")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            output.write(ca_text)
        try:
            target.update({
                "sslmode": "verify-full",
                "sslrootcert": path,
                "connect_timeout": 10,
                "application_name": "dankshield_aiven_config_staging",
                "options": "-c statement_timeout=15000 -c lock_timeout=2000",
            })
            with psycopg.connect(**target, autocommit=True) as connection:
                if mode == "preview":
                    with connection.transaction():
                        connection.execute("set transaction read only")
                        _, count, _ = _destination_state(connection)
                        if count and not _assert_staged_matches(connection, first):
                            raise RuntimeError("existing staging data differs; stop")
                        matched = count == len(first.rows) and _assert_staged_matches(connection, first)
                    print("Aiven guild configuration staging preview: PASS")
                    print("Existing staged guild configs:", count)
                    print("Already matching source:", matched)
                    print("No source or destination rows modified")
                    return 0
                with connection.transaction():
                    # Locked against another copy of this workflow; no delete/update.
                    connection.execute(
                        "select pg_catalog.pg_advisory_xact_lock(%s)", (SERVICE_LOCK,)
                    )
                    _destination_state(connection)
                    # Existing implementation does idempotent inserts, row-hash
                    # parity, repeat-source read and atomic rollback on mismatch.
                    result = apply_stage(
                        first, connection,
                        expected_db=str(target["dbname"]),
                        disk_budget_mb=STORAGE_BUDGET_MB,
                        source_reread=lambda: _source_plan(src),
                    )
                    if not _assert_staged_matches(connection, first):
                        raise RuntimeError("post-copy staging parity mismatch")
                print("Aiven guild configs staging: SUCCESS")
                print("Verified staged row count:", result["staged_count"])
                print("Source reread matched; atomic stage committed")
                print("Bot remains on Supabase. Live guild_config_state unchanged.")
                return 0
        finally:
            Path(path).unlink(missing_ok=True)
    except Exception as exc:
        # Database exception strings may expose credentials, SQL or guild rows.
        # Never print traceback, DSNs, fields or exception messages.
        print("Aiven guild-config staging failed (error type: " +
              type(exc).__name__ + ").")
        return 2


if __name__ == "__main__":
    sys.exit(main())
