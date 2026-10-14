"""Explicit-only, off-host recovery drill of a real encrypted source archive.

Runs ONLY inside a disposable GitHub Actions PostgreSQL 17 service. It does not
use source credentials, Discloud credentials, bot runtime hooks, or plaintext
temp files. Authentication runs to completion before ANY restore SQL is sent.
Never run this against the existing Discloud instance or the shared Supabase DB.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any

from stoney_verify.source_pg_backup import (
    _iter_verified_plaintext, _sha256_file, verify_archive_toc,
)

EXPECTED_REPO = "UglyGameFace/Dank-Shield"
EXPECTED_RESTORE_DB = "dank_source_recovery_drill"
MAX_ENCRYPTED_BYTES = 2 * 1024 * 1024 * 1024
SENSITIVE_OUTPUT = "Restore result intentionally redacted; do not publish database output."


class RecoveryDrillError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_disposable_runner() -> None:
    """A deliberate independent CI service, NEVER a private source/destination."""
    if not (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("CI") == "true"
        and os.getenv("GITHUB_REPOSITORY") == EXPECTED_REPO
        and os.getenv("DANK_RECOVERY_DISPOSABLE_PG17") == "YES_ISOLATED"
        and os.getenv("PGHOST") == "127.0.0.1"
        and os.getenv("PGPORT") == "5432"
        and os.getenv("PGUSER") == "postgres"
        and os.getenv("PGDATABASE") == "postgres"
        and bool(os.getenv("PGPASSWORD"))
    ):
        raise RecoveryDrillError("not_disposable_runner")


def _check_archive(path: Path, expected_sha256: str, passphrase: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256 or ""):
        raise RecoveryDrillError("invalid_expected_digest")
    if not path.is_file() or path.is_symlink():
        raise RecoveryDrillError("invalid_encrypted_archive")
    size = path.stat().st_size
    if size < 128 or size > MAX_ENCRYPTED_BYTES:
        raise RecoveryDrillError("invalid_archive_size")
    if _sha256_file(path) != expected_sha256:
        raise RecoveryDrillError("encrypted_archive_checksum_mismatch")
    if len(passphrase) < 24:
        raise RecoveryDrillError("missing_backup_passphrase")
    try:
        toc = verify_archive_toc(path, passphrase)
    except Exception:
        raise RecoveryDrillError("encrypted_archive_auth_or_toc_failed") from None
    if not toc.get("authenticated") or not toc.get("archive_toc_readable"):
        raise RecoveryDrillError("encrypted_archive_not_authenticated")
    if _sha256_file(path) != expected_sha256:
        raise RecoveryDrillError("encrypted_archive_changed_during_verification")
    return {
        "encrypted_bytes": size,
        "archive_toc_readable": True,
        "authenticated": True,
    }


def _restore_error_category(buf: bytes) -> str:
    """Categorize stdout/stderr privately. Never print raw pg_restore errors."""
    msg = buf[:65536].decode("utf-8", "replace").lower()
    if "extension" in msg and any(s in msg for s in (
        "not available", "could not open extension control file", "no installation script"
    )):
        return "source_extension_not_available_on_plain_pg17"
    if "role" in msg and "does not exist" in msg:
        return "source_role_not_available_on_plain_pg17"
    if "permission denied" in msg or "must be owner" in msg:
        return "restore_target_permissions_incompatible"
    if "unrecognized configuration parameter" in msg:
        return "source_configuration_not_supported_by_plain_pg17"
    if "could not execute query" in msg or "error" in msg:
        return "pg_restore_sql_failed_unclassified"
    return "pg_restore_process_failed_unclassified"


def _safe_pg_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL")
           if k in os.environ}
    env.update({
        "PGHOST": "127.0.0.1", "PGPORT": "5432",
        "PGUSER": "postgres", "PGPASSWORD": os.environ["PGPASSWORD"],
        "PGDATABASE": EXPECTED_RESTORE_DB,
        "PGCONNECT_TIMEOUT": "10",
    })
    return env


def _create_empty_restore_db() -> None:
    import psycopg
    # Connect ONLY to disposable localhost service and create a NEW named DB.
    with psycopg.connect(
        "host=127.0.0.1 port=5432 user=postgres dbname=postgres connect_timeout=10",
        password=os.environ["PGPASSWORD"], autocommit=True,
    ) as conn:
        version = int(conn.execute("show server_version_num").fetchone()[0])
        if version // 10000 != 17:
            raise RecoveryDrillError("restore_target_not_postgres17")
        exists = conn.execute(
            "select 1 from pg_database where datname=%s",
            (EXPECTED_RESTORE_DB,),
        ).fetchone()
        if exists:
            raise RecoveryDrillError("restore_db_already_exists")
        conn.execute("create database " + EXPECTED_RESTORE_DB)


def _stream_restore(path: Path, passphrase: str, expected_sha256: str) -> int:
    """Use a disposable PG17 client and process. No plaintext archive files."""
    if _sha256_file(path) != expected_sha256:
        raise RecoveryDrillError("archive_changed_before_restore")
    command = [
        "docker", "run", "--rm", "-i", "--network=host",
        "-e", "PGHOST", "-e", "PGPORT", "-e", "PGUSER",
        "-e", "PGPASSWORD", "-e", "PGDATABASE", "-e", "PGCONNECT_TIMEOUT",
        "postgres:17",
        "pg_restore", "--no-owner", "--no-privileges",
        "--single-transaction", "--exit-on-error",
        "--dbname=" + EXPECTED_RESTORE_DB,
    ]
    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, env=_safe_pg_env(),
    )
    stderr_sample = bytearray()
    def drain() -> None:
        assert proc.stderr is not None
        while True:
            data = proc.stderr.read(4096)
            if not data:
                break
            room = max(0, 65536 - len(stderr_sample))
            if room:
                stderr_sample.extend(data[:room])
    drain_thread = threading.Thread(target=drain, daemon=True)
    drain_thread.start()
    sent = 0
    try:
        assert proc.stdin is not None
        try:
            for data in _iter_verified_plaintext(path, passphrase):
                proc.stdin.write(data)
                sent += len(data)
            proc.stdin.close()
        except BrokenPipeError:
            raise RecoveryDrillError("pg_restore_closed_input_early") from None
        status = proc.wait(timeout=300)
        drain_thread.join(timeout=5)
        if status:
            raise RecoveryDrillError(_restore_error_category(bytes(stderr_sample)))
        if _sha256_file(path) != expected_sha256:
            raise RecoveryDrillError("archive_changed_during_restore")
        return sent
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def _restore_inventory() -> dict[str, Any]:
    import psycopg
    # Fixed aggregate-only audit; no rows, guild IDs, user records or
    # plaintext schema contents ever printed.
    with psycopg.connect(
        "host=127.0.0.1 port=5432 user=postgres "
        f"dbname={EXPECTED_RESTORE_DB} connect_timeout=10",
        password=os.environ["PGPASSWORD"],
    ) as conn:
        schema_counts = conn.execute(
            "select table_schema,count(*)::int "
            "from information_schema.tables "
            "where table_type='BASE TABLE' and table_schema in ('public','idle') "
            "group by table_schema order by table_schema"
        ).fetchall()
        required = (
            "guild_configs", "guild_config_versions", "staff_action_logs",
            "activity_feed_events", "tickets"
        )
        restored_tables: dict[str, int | None] = {}
        for table in required:
            exists = conn.execute(
                "select to_regclass(%s)",
                ("public." + table,),
            ).fetchone()[0]
            if exists is None:
                restored_tables[table] = None
            else:
                from psycopg import sql
                # Identifiers derive exclusively from constant code literals.
                result = conn.execute(
                    sql.SQL("select count(*)::bigint from {}.{}").format(
                        sql.Identifier("public"), sql.Identifier(table)
                    )
                ).fetchone()
                restored_tables[table] = int(result[0])
    if any(v is None for v in restored_tables.values()):
        raise RecoveryDrillError("critical_dank_tables_missing_after_restore")
    return {
        "schema_table_counts": {str(k): int(n) for k, n in schema_counts},
        "critical_table_row_counts": restored_tables,
    }


def run_restore_drill(
    archive: Path, *, expected_sha256: str, passphrase: str
) -> dict[str, Any]:
    validate_disposable_runner()
    checked = _check_archive(archive, expected_sha256, passphrase)
    _create_empty_restore_db()
    restored_bytes = _stream_restore(archive, passphrase, expected_sha256)
    inventory = _restore_inventory()
    return {
        "status": "isolated_pg17_restore_verified",
        "source_data_modified": False,
        "discloud_data_modified": False,
        "encrypted_sha256": expected_sha256,
        "restored_archive_bytes": restored_bytes,
        **checked,
        **inventory,
        "caveat": "Platform-managed external Storage, credentials and server roles are not part of this SQL restore. Supabase remains authoritative.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Disposable PostgreSQL 17 source archive recovery drill")
    parser.add_argument("--encrypted-archive", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        out = run_restore_drill(
            Path(args.encrypted_archive),
            expected_sha256=args.sha256,
            passphrase=os.environ.get("DANK_BACKUP_PASSPHRASE", ""),
        )
        print(json.dumps(out, sort_keys=True))
        return 0
    except Exception as exc:
        reason = exc.code if isinstance(exc, RecoveryDrillError) else "recovery_drill_failed_unclassified"
        print(json.dumps({
            "status": "not_recovered", "failure_code": reason,
            "source_data_modified": False, "discloud_data_modified": False,
            "warning": SENSITIVE_OUTPUT,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
