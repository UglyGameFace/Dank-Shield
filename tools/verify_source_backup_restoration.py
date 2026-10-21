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
from stoney_verify.core_data_stage import SOURCE_KEYS

EXPECTED_REPO = "UglyGameFace/Dank-Shield"
EXPECTED_RESTORE_DB = "dank_source_recovery_drill"
MAX_ENCRYPTED_BYTES = 2 * 1024 * 1024 * 1024
SENSITIVE_OUTPUT = "Restore result intentionally redacted; do not publish database output."

# Only constant pg_dump TOC object classes may leave the private runner. In
# particular, never disclose restored schema, relation, function or role names.
RESTORE_OBJECT_KINDS = frozenset((
    "ACL", "BLOB", "COLLATION", "COMMENT", "CONSTRAINT", "DATABASE",
    "DEFAULT ACL", "DOMAIN", "EVENT TRIGGER", "EXTENSION", "FK CONSTRAINT",
    "FUNCTION", "INDEX", "MATERIALIZED VIEW", "POLICY", "PROCEDURE",
    "PUBLICATION", "PUBLICATION TABLE", "RULE", "SCHEMA", "SEQUENCE",
    "SEQUENCE SET", "TABLE", "TABLE DATA", "TRIGGER", "TYPE", "VIEW",
))


class RecoveryDrillError(RuntimeError):
    def __init__(self, code: str, *, object_kind: str = "unknown"):
        self.code = code
        self.object_kind = (
            object_kind if object_kind in RESTORE_OBJECT_KINDS else "unknown"
        )
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
        and os.getenv("PGUSER") in ("postgres", "supabase_admin")
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


def _restore_failed_object_kind(buf: bytes) -> str:
    """Whitelisted TOC object *type* only; never log names or archive entry IDs."""
    msg = buf[:65536].decode("utf-8", "replace")
    found = re.search(
        r"pg_restore:\s*from TOC entry \d+;\s*\d+\s+\d+\s+([^\r\n]{1,128})",
        msg, re.IGNORECASE,
    )
    if found:
        descriptor = found.group(1).upper()
        for kind in sorted(RESTORE_OBJECT_KINDS, key=len, reverse=True):
            if descriptor == kind or descriptor.startswith(kind + " "):
                return kind
    # --exit-on-error may omit "from TOC entry". The existing TOC parser
    # therefore reported unknown even for actionable SQL failures. With the
    # restore client's --verbose output, use only the last fixed object kind;
    # object identifiers and private SQL never leave memory.
    last_kind = "unknown"
    for line in msg.splitlines():
        if re.match(r"^pg_restore:\\s*processing data for table\\b", line, re.I):
            last_kind = "TABLE DATA"
            continue
        matched = re.match(r"^pg_restore:\\s*creating\\s+([A-Z ]+?)(?:\\s+[\\\"']|$)", line, re.I)
        if matched:
            proposed = matched.group(1).upper().strip()
            if proposed in RESTORE_OBJECT_KINDS:
                last_kind = proposed
    return last_kind


def _restore_error_category(buf: bytes) -> str:
    """Categorize stderr without treating all client errors as failed SQL."""
    msg = buf[:65536].decode("utf-8", "replace").lower()
    # Client transport/archive errors have no pg_restore TOC entry. Previously
    # the generic word "error" mislabeled these as rejected SQL statements.
    if any(term in msg for term in (
        "connection to server at", "could not connect to server",
        "connection refused", "could not translate host name",
        "server closed the connection unexpectedly",
        "password authentication failed", "no password supplied",
    )):
        return "pg_restore_connection_failed"
    if any(term in msg for term in (
        "did not find magic string", "input file is too short",
        "unsupported version", "could not read from input file",
        "could not read header", "could not read from archive",
        "unexpected end of file",
    )):
        return "pg_restore_archive_read_failed"
    if any(term in msg for term in (
        "unrecognized option", "invalid option", "too many command-line arguments",
    )):
        return "pg_restore_invocation_failed"
    if "extension" in msg and any(s in msg for s in (
        "not available", "could not open extension control file", "no installation script"
    )):
        return "source_extension_not_available_on_plain_pg17"
    if "role" in msg and "does not exist" in msg:
        return "source_role_not_available_on_plain_pg17"
    if any(s in msg for s in (
        "permission denied", "must be owner", "must be superuser",
        "only superusers", "must be member of role", "is not a member of role",
    )):
        return "restore_target_permissions_incompatible"
    if any(s in msg for s in (
        "cannot run inside a transaction block",
        "cannot be executed inside a transaction block",
        "may not be called in a transaction block",
    )):
        return "restore_transaction_unsupported"
    if "cannot drop" in msg and "depend on it" in msg:
        return "restore_dependency_conflict"
    if "collation version mismatch" in msg:
        return "restore_collation_version_mismatch"
    if "unrecognized configuration parameter" in msg:
        return "source_configuration_not_supported_by_plain_pg17"
    # Return only fixed, audited categories. Avoid exposing role, schema,
    # table, SQL expression, user ID or password from pg_restore stderr.
    if "already exists" in msg or "duplicate object" in msg:
        return "restore_object_conflicts_with_bootstrap"
    if "duplicate key value violates unique constraint" in msg:
        return "restore_duplicate_key_conflict"
    if "violates foreign key constraint" in msg:
        return "restore_foreign_key_violation"
    if any(s in msg for s in (
        "violates not-null constraint", "violates check constraint",
        "invalid input syntax for type", "value too long for type",
    )):
        return "restore_data_constraint_rejected"
    if "does not exist" in msg and any(term in msg for term in (
        "relation", "function", "type", "schema",
    )):
        return "restore_schema_dependency_missing"
    if "could not execute query" in msg or re.search(r"(?:^|\n)\s*error:\s", msg):
        return "pg_restore_sql_failed_unclassified"
    return "pg_restore_process_failed_unclassified"


def _safe_pg_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL")
           if k in os.environ}
    env.update({
        "PGHOST": "127.0.0.1", "PGPORT": "5432",
        "PGUSER": os.environ["PGUSER"], "PGPASSWORD": os.environ["PGPASSWORD"],
        "PGDATABASE": EXPECTED_RESTORE_DB,
        "PGCONNECT_TIMEOUT": "10",
    })
    return env


def _create_empty_restore_db() -> None:
    import psycopg
    # Connect ONLY to disposable localhost service and create a NEW named DB.
    with psycopg.connect(
        "host=127.0.0.1 port=5432 dbname=postgres connect_timeout=10",
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        autocommit=True,
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


def _validate_container_restore_connection() -> None:
    """Check the exact Docker/host-network path used by the restore client."""
    command = [
        "docker", "run", "--rm", "--network=host",
        "-e", "PGHOST", "-e", "PGPORT", "-e", "PGUSER",
        "-e", "PGPASSWORD", "-e", "PGDATABASE", "-e", "PGCONNECT_TIMEOUT",
        "postgres:17", "psql", "--no-psqlrc", "--quiet", "--tuples-only",
        "--no-align", "--dbname=" + EXPECTED_RESTORE_DB,
        "--command=select 1",
    ]
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=_safe_pg_env(), timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise RecoveryDrillError("restore_container_connection_unavailable") from None
    if result.returncode != 0 or result.stdout.strip() != b"1":
        raise RecoveryDrillError("restore_container_connection_unavailable")


def _stream_restore(path: Path, passphrase: str, expected_sha256: str) -> int:
    """Use a disposable PG17 client and process. No plaintext archive files."""
    if _sha256_file(path) != expected_sha256:
        raise RecoveryDrillError("archive_changed_before_restore")
    _validate_container_restore_connection()
    command = [
        "docker", "run", "--rm", "-i", "--network=host",
        "-e", "PGHOST", "-e", "PGPORT", "-e", "PGUSER",
        "-e", "PGPASSWORD", "-e", "PGDATABASE", "-e", "PGCONNECT_TIMEOUT",
        "postgres:17",
        "pg_restore", "--verbose", "--no-owner", "--no-privileges",
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
            # Keep the last bounded section, where the actual failure and
            # immediate preceding verbose object-kind marker appear.
            stderr_sample.extend(data)
            if len(stderr_sample) > 65536:
                del stderr_sample[:-65536]
    drain_thread = threading.Thread(target=drain, daemon=True)
    drain_thread.start()
    sent = 0
    input_closed_early = False
    try:
        assert proc.stdin is not None
        for data in _iter_verified_plaintext(path, passphrase):
            if input_closed_early:
                # Authenticate the rest of the encrypted archive even after
                # pg_restore exits on a SQL error. Never ignore that exit.
                continue
            try:
                proc.stdin.write(data)
                sent += len(data)
            except BrokenPipeError:
                input_closed_early = True
        try:
            proc.stdin.close()
        except BrokenPipeError:
            input_closed_early = True
        status = proc.wait(timeout=300)
        drain_thread.join(timeout=5)
        if status:
            # A SQL error closes stdin early: classify the actual PostgreSQL
            # failure before treating the pipe closure as the cause.
            errors = bytes(stderr_sample)
            raise RecoveryDrillError(
                _restore_error_category(errors),
                object_kind=_restore_failed_object_kind(errors),
            )
        if input_closed_early:
            raise RecoveryDrillError("pg_restore_closed_input_early")
        if _sha256_file(path) != expected_sha256:
            raise RecoveryDrillError("archive_changed_during_restore")
        return sent
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        if proc.stderr is not None:
            proc.stderr.close()


def _staging_candidate_inventory(conn: Any) -> dict[str, Any]:
    """Read-only checks of the *existing* 16-table migration allowlist.

    Counts and key-integrity flags only; nothing here proves the table belongs
    to Dank Shield, represents live source state, or authorizes migration.
    Missing/unsafe candidates are reported explicitly instead of silently
    treating absent tables as empty. No user data is returned to CI logs.
    """
    from psycopg import sql

    results: dict[str, dict[str, Any]] = {}
    for table, keys in SOURCE_KEYS.items():
        # The only identifiers are the fixed audited constants from
        # core_data_stage; never take table/column names from workflow input.
        rel = conn.execute(
            "select c.oid from pg_class c join pg_namespace n "
            "on n.oid=c.relnamespace where n.nspname='public' "
            "and c.relname=%s and c.relkind in ('r','p')",
            (table,),
        ).fetchone()
        if rel is None:
            results[table] = {"status": "missing"}
            continue
        attributes = {
            str(row[0]) for row in conn.execute(
                "select attname from pg_attribute "
                "where attrelid=%s and attnum>0 and not attisdropped",
                (rel[0],),
            ).fetchall()
        }
        if any(key not in attributes for key in keys):
            results[table] = {"status": "missing_key_columns"}
            continue
        target = sql.SQL("{}.{}").format(
            sql.Identifier("public"), sql.Identifier(table)
        )
        safe_keys = [sql.Identifier(key) for key in keys]
        row_count = int(conn.execute(
            sql.SQL("select count(*)::bigint from {}").format(target)
        ).fetchone()[0])
        null_condition = sql.SQL(" OR ").join(
            sql.SQL("{} IS NULL").format(key) for key in safe_keys
        )
        null_keys = conn.execute(
            sql.SQL("select exists(select 1 from {} where {} limit 1)").format(
                target, null_condition
            )
        ).fetchone()[0]
        duplicate_keys = conn.execute(
            sql.SQL(
                "select exists(select 1 from {} group by {} "
                "having count(*) > 1 limit 1)"
            ).format(target, sql.SQL(", ").join(safe_keys))
        ).fetchone()[0]
        results[table] = {
            "status": ("invalid_keys" if null_keys or duplicate_keys
                       else "keys_valid"),
            "rows": row_count,
            "null_keys_present": bool(null_keys),
            "duplicate_keys_present": bool(duplicate_keys),
        }
    return {
        "source_snapshot_only": True,
        "cutover_authorized": False,
        "candidate_tables": results,
    }


def _restore_inventory() -> dict[str, Any]:
    import psycopg
    # Fixed aggregate-only audit; no rows, guild IDs, user records or
    # plaintext schema contents ever printed.
    with psycopg.connect(
        "host=127.0.0.1 port=5432 "
        f"dbname={EXPECTED_RESTORE_DB} connect_timeout=10",
        user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
        options="-c statement_timeout=20000",
    ) as conn:
        conn.execute("set transaction read only")
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
        candidate_inventory = _staging_candidate_inventory(conn)
    if any(v is None for v in restored_tables.values()):
        raise RecoveryDrillError("critical_dank_tables_missing_after_restore")
    return {
        "schema_table_counts": {str(k): int(n) for k, n in schema_counts},
        "critical_table_row_counts": restored_tables,
        "staging_candidate_inventory": candidate_inventory,
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
