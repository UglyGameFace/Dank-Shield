"""Owner-only encrypted backup and disposable restore for dedicated Dank Shield.

This script does not contact the original source, Discord or Discloud.
It reads the offline dedicated Supabase candidate, creates an authenticated
AES-GCM archive with the existing hardened backup helper, then restores to
the CI runner's separate PostgreSQL 17 service and checks critical row hashes.
Only ciphertext is eligible for short-lived GitHub artifact storage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from stoney_verify.source_pg_backup import create_backup, validate_source_dsn
from tools.verify_source_backup_restoration import (
    run_restore_drill, validate_disposable_runner,
)

REPO = "UglyGameFace/Dank-Shield"
REF = "viamriafkbqhquvrsnua"
RESTORED_DB = "dank_source_recovery_drill"
TABLES = (
    "guild_configs", "guild_security_actor_reputation",
    "guild_members", "tickets", "member_events", "staff_action_logs",
    "activity_feed_events", "dank_profile_users",
    "dank_cinema_user_media", "dank_community_hub_settings",
    "dank_stickies", "dank_quiet_notices",
    "bot_operation_jobs", "bot_commands",
)
MAX_DATA_TABLES = 120


def validate_owner_and_runner() -> None:
    validate_disposable_runner()
    if (
        os.getenv("GITHUB_REPOSITORY") != REPO
        or os.getenv("GITHUB_REF") != "refs/heads/main"
        or os.getenv("GITHUB_ACTOR") != "UglyGameFace"
    ):
        raise RuntimeError("owner/main runner approval not satisfied")
    if not os.getenv("DANK_FRESH_DATABASE_URL") or not os.getenv(
        "DANK_BACKUP_PASSPHRASE"
    ):
        raise RuntimeError("missing candidate backup secrets")


def archive_directory() -> Path:
    raw = os.getenv("RUNNER_TEMP", "")
    if not raw:
        raise RuntimeError("private runner temporary folder missing")
    root = Path(raw)
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("unsafe runner scratch directory")
    candidate = root / "dankshield-private-candidate-backup"
    candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
    candidate.chmod(0o700)
    return candidate


def readonly_candidate_params(dsn: str) -> dict[str, Any]:
    pinned = validate_source_dsn(dsn, REF)
    return {
        "host": pinned["PGHOST"], "port": int(pinned["PGPORT"]),
        "user": pinned["PGUSER"], "password": pinned["PGPASSWORD"],
        "dbname": pinned["PGDATABASE"], "sslmode": "require",
        "connect_timeout": 10, "autocommit": True,
        "application_name": "dankshield_candidate_restore_baseline",
        "options": "-c default_transaction_read_only=on "
                   "-c statement_timeout=150000 -c lock_timeout=2000",
    }


def manifest(conn) -> dict[str, tuple[int, str]]:
    from psycopg import sql
    result = {}
    for table in TABLES:
        # Table identifiers are code literals from a fixed allowlist.
        query = sql.SQL(
            "select count(*)::bigint, "
            "md5(coalesce(jsonb_agg(to_jsonb(t) "
            "order by to_jsonb(t)::text)::text,'[]')) "
            "from {}.{} t"
        ).format(sql.Identifier("public"), sql.Identifier(table))
        row = conn.execute(query).fetchone()
        count, checksum = int(row[0]), str(row[1])
        if count < 0 or count > 100_000 or len(checksum) != 32:
            raise RuntimeError("candidate inventory bounds violated")
        result[table] = (count, checksum)
    return result


def candidate_manifest(dsn: str) -> dict[str, tuple[int, str]]:
    import psycopg
    with psycopg.connect(**readonly_candidate_params(dsn)) as db:
        with db.transaction():
            db.execute("set transaction isolation level repeatable read, read only")
            public_tables = int(db.execute(
                "select count(*) from pg_tables where schemaname='public'"
            ).fetchone()[0])
            if not len(TABLES) <= public_tables <= MAX_DATA_TABLES:
                raise RuntimeError("unexpected dedicated schema size")
            return manifest(db)


def restored_manifest() -> dict[str, tuple[int, str]]:
    import psycopg
    # Local disposable service ONLY. No candidate/source DSN here.
    with psycopg.connect(
        host="127.0.0.1", port=5432,
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        dbname=RESTORED_DB, connect_timeout=10,
        options="-c default_transaction_read_only=on "
                "-c statement_timeout=150000",
        autocommit=True,
    ) as db:
        with db.transaction():
            db.execute("set transaction isolation level repeatable read, read only")
            return manifest(db)


def execute() -> dict[str, Any]:
    validate_owner_and_runner()
    dsn = os.environ["DANK_FRESH_DATABASE_URL"]
    password = os.environ["DANK_BACKUP_PASSPHRASE"]
    before = candidate_manifest(dsn)
    out = archive_directory()
    backup = create_backup(
        dsn=dsn, source_ref=REF,
        passphrase=password, output_dir=out,
    )
    if backup.get("restore_drill_passed") is not False:
        raise RuntimeError("archive incorrectly premarked as restored")
    archive = Path(backup["encrypted_path"])
    if archive.parent != out or archive.suffix != ".dsbak":
        raise RuntimeError("archive outside fixed private folder")
    _ = run_restore_drill(
        archive, expected_sha256=backup["encrypted_sha256"],
        passphrase=password,
    )
    after = restored_manifest()
    if before != after:
        raise RuntimeError("disposable restore does not match dedicated candidate")
    return {
        "status": "ENCRYPTED_DEDICATED_BACKUP_RESTORED",
        "critical_tables_compared": len(TABLES),
        "critical_counts_and_hashes_match": True,
        "disposable_pg17_restore_completed": True,
        "encrypted_archive_sha256": backup["encrypted_sha256"],
        "encrypted_archive_bytes": backup["encrypted_bytes"],
        "original_source_writes": 0,
        "dedicated_candidate_writes": 0,
        "production_cutover_authorized": False,
        "off_runner_durable_copy_still_required": True,
        "grants_secrets_and_external_storage_not_in_dump": True,
    }


def main() -> int:
    try:
        outcome = execute()
    except Exception as exc:
        # No raw SQL errors, file names or PostgreSQL credentials in CI logs.
        print("DEDICATED_RECOVERY_DRILL_FAILED: " + type(exc).__name__,
              file=sys.stderr)
        return 2
    print(json.dumps(outcome,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
