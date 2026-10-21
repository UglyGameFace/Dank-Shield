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

from stoney_verify.source_pg_backup import (
    BackupFailure, create_backup, validate_source_dsn,
)
from tools.verify_source_backup_restoration import (
    RESTORE_OBJECT_KINDS, RecoveryDrillError, run_restore_drill,
    validate_disposable_runner,
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

# These are code-owned, private-data-free recovery categories from the
# existing disposable restore implementation. Unrecognized exception
# messages and codes must never reach GitHub Actions logs.
SAFE_RECOVERY_CODES = frozenset((
    "not_disposable_runner",
    "invalid_expected_digest",
    "invalid_encrypted_archive",
    "invalid_archive_size",
    "encrypted_archive_checksum_mismatch",
    "missing_backup_passphrase",
    "encrypted_archive_auth_or_toc_failed",
    "encrypted_archive_not_authenticated",
    "encrypted_archive_changed_during_verification",
    "restore_target_not_postgres17",
    "restore_db_already_exists",
    "archive_changed_before_restore",
    "pg_restore_closed_input_early",
    "archive_changed_during_restore",
    "critical_dank_tables_missing_after_restore",
    "source_extension_not_available_on_plain_pg17",
    "source_role_not_available_on_plain_pg17",
    "restore_target_permissions_incompatible",
    "source_configuration_not_supported_by_plain_pg17",
    "pg_restore_sql_failed_unclassified",
    "pg_restore_process_failed_unclassified",
    "restore_object_conflicts_with_bootstrap",
    "restore_duplicate_key_conflict",
    "restore_foreign_key_violation",
    "restore_schema_dependency_missing",
    "restore_transaction_unsupported",
    "restore_dependency_conflict",
    "restore_collation_version_mismatch",
    "restore_data_constraint_rejected",
))
SAFE_BACKUP_CODES = frozenset((
    "database_authentication_rejected",
    "pooler_project_or_user_rejected",
    "database_dns_lookup_failed",
    "database_network_unreachable",
    "database_connection_refused",
    "database_tls_failed",
    "database_connection_limit",
    "encrypted_archive_verification_failed",
))


def safe_failure_code(exc: Exception) -> str:
    """Expose only audited constant failure categories, never exception text."""
    if isinstance(exc, RecoveryDrillError) and exc.code in SAFE_RECOVERY_CODES:
        return exc.code
    if isinstance(exc, BackupFailure) and exc.code in SAFE_BACKUP_CODES:
        return exc.code
    return "candidate_recovery_unclassified"



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
    print("CANDIDATE_RECOVERY_PHASE: reading_candidate_manifest", flush=True)
    before = candidate_manifest(dsn)
    out = archive_directory()
    print("CANDIDATE_RECOVERY_PHASE: creating_encrypted_backup", flush=True)
    backup = create_backup(
        dsn=dsn, source_ref=REF,
        passphrase=password, output_dir=out,
    )
    if backup.get("restore_drill_passed") is not False:
        raise RuntimeError("archive incorrectly premarked as restored")
    archive = Path(backup["encrypted_path"])
    if archive.parent != out or archive.suffix != ".dsbak":
        raise RuntimeError("archive outside fixed private folder")
    print("CANDIDATE_RECOVERY_PHASE: authenticating_and_restoring_to_disposable", flush=True)
    _ = run_restore_drill(
        archive, expected_sha256=backup["encrypted_sha256"],
        passphrase=password,
    )
    print("CANDIDATE_RECOVERY_PHASE: comparing_restored_records", flush=True)
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
        # Deterministic, redacted recovery code. No raw SQL error, filename,
        # Discord user ID, hostname, DSN, secret or exception.__str__ in logs.
        print("DEDICATED_RECOVERY_DRILL_FAILED: " + safe_failure_code(exc),
              file=sys.stderr)
        if isinstance(exc, RecoveryDrillError) and exc.code in SAFE_RECOVERY_CODES:
            # Both the failure category and TOC kind are constant allowlists.
            # Never expose pg_restore stderr, object names or archive IDs.
            kind = (exc.object_kind if exc.object_kind in RESTORE_OBJECT_KINDS
                    else "unknown")
            print("DEDICATED_RECOVERY_FAILED_TOC_KIND: " + kind,
                  file=sys.stderr)
        return 2
    print(json.dumps(outcome,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
