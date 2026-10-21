"""Align pg_cron only on a GitHub Actions disposable Supabase PG17 service.

The full authenticated archive includes pg_cron, which PostgreSQL can install
only in cron.database_name. The default service database is "postgres" while
the isolated restore database is intentionally distinct. Never run this on an
existing managed or production database.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

from tools.verify_source_backup_restoration import (
    EXPECTED_RESTORE_DB, validate_disposable_runner,
)

SERVICE_IMAGE = "supabase/postgres:17.6.1.136"


def _connect():
    import psycopg
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="postgres",
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        autocommit=True, connect_timeout=5,
    )


def prepare() -> None:
    validate_disposable_runner()
    if os.environ.get("PGUSER") != "supabase_admin":
        raise RuntimeError("unexpected disposable service user")
    container_id = os.environ.get("DANK_RECOVERY_POSTGRES_CONTAINER", "")
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RuntimeError("missing immutable GitHub service container ID")
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=True, timeout=10,
    )
    if inspected.stdout.decode("ascii", "replace").strip() != SERVICE_IMAGE:
        raise RuntimeError("container image mismatch")
    with _connect() as conn:
        if int(conn.execute("show server_version_num").fetchone()[0]) // 10000 != 17:
            raise RuntimeError("unexpected PostgreSQL major version")
        database_name = conn.execute("show cron.database_name").fetchone()[0]
        if database_name not in ("postgres", EXPECTED_RESTORE_DB):
            raise RuntimeError("unexpected pg_cron database setting")
        exists = conn.execute(
            "select 1 from pg_database where datname=%s",
            (EXPECTED_RESTORE_DB,),
        ).fetchone()
        if exists:
            raise RuntimeError("restore database must not exist before backup")
        if database_name == EXPECTED_RESTORE_DB:
            raise RuntimeError("recovery service was already reconfigured")
        # A restored scheduled job must NEVER run against the CI service.
        conn.execute("ALTER SYSTEM SET cron.launch_active_jobs = 'off'")
        conn.execute(
            "ALTER SYSTEM SET cron.database_name = '" + EXPECTED_RESTORE_DB + "'"
        )
    subprocess.run(
        ["docker", "restart", container_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=True, timeout=45,
    )
    # This is a genuine service readiness wait after a deliberate restart,
    # not a retry masking restore errors.
    for attempt in range(30):
        try:
            with _connect() as conn:
                configured = conn.execute(
                    "select current_setting('cron.database_name'), "
                    "current_setting('cron.launch_active_jobs')"
                ).fetchone()
                if configured == (EXPECTED_RESTORE_DB, "off"):
                    print("ISOLATED_PG_CRON_READY: disabled_jobs_target_aligned")
                    return
                raise RuntimeError("unsafe pg_cron settings after restart")
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    raise RuntimeError("disposable PG17 failed readiness check")


def main() -> int:
    try:
        prepare()
    except Exception:
        # Never log Docker output, config contents, DSNs or PostgreSQL errors.
        print("ISOLATED_PG_CRON_PREPARATION_FAILED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
