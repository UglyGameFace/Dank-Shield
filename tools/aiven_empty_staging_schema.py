"""Guarded schema preflight and optional empty staging installation on Aiven.

No Supabase connection, no source-data copy, no guild runtime routing, and no
automated startup DDL. Only the three reviewed PostgreSQL staging SQL files.
The installation requires a manual, exact phrase confirmation from the owner.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

from tools.probe_aiven_postgres import ProbeConfigurationError, parse_aiven_uri

SERVICE_NAME = "pg-3e03c30c"
MAX_EMPTY_DB_BYTES = 100 * 1024 * 1024
INSTALL_CONFIRMATION = "CREATE_EMPTY_AIVEN_STAGING_ONLY"
MIGRATIONS = (
    "202610130002_core_staging.sql",
    "202610130003_guild_config_shadow.sql",
    "202610130004_guild_config_atomic_state.sql",
)
RELATIONS = (
    "dank_migration.staged_records",
    "dank_core.guild_config_shadow",
    "dank_core.guild_config_state",
)
ROOT = Path(__file__).resolve().parents[1]


def validate_target(uri: str, ca: str, mode: str, confirmation: str) -> dict:
    if mode not in {"preview", "install"}:
        raise ProbeConfigurationError("unsupported Aiven schema mode")
    if mode == "install" and confirmation != INSTALL_CONFIRMATION:
        raise ProbeConfigurationError("installation requires exact manual confirmation")
    if not ca or len(ca) > 128_000 or not (
        ca.startswith("-----BEGIN CERTIFICATE-----")
        and "-----END CERTIFICATE-----" in ca
    ):
        raise ProbeConfigurationError("Aiven CA certificate is required")
    opts = parse_aiven_uri(uri)
    host = str(opts["host"]).lower()
    if not host.startswith((SERVICE_NAME + "-", SERVICE_NAME + ".")):
        raise ProbeConfigurationError("Aiven URI is not for the approved Dank Shield service")
    return opts


def snapshot(connection) -> tuple[int, bool, int, int, tuple[bool, ...], tuple[bool, ...]]:
    """Only server metadata, never any source or member data."""
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_setting('server_version_num')::integer, "
            "exists(select 1 from pg_catalog.pg_stat_ssl "
            "where pid=pg_backend_pid() and ssl), "
            "pg_catalog.pg_database_size(current_database()), "
            "(select count(*) from pg_catalog.pg_tables where schemaname='public')"
        )
        version, ssl, size, public_count = cursor.fetchone()
        schemas = []
        for name in ("dank_migration", "dank_core"):
            cursor.execute("select to_regnamespace(%s) is not null", (name,))
            schemas.append(bool(cursor.fetchone()[0]))
        tables = []
        for name in RELATIONS:
            cursor.execute("select to_regclass(%s) is not null", (name,))
            tables.append(bool(cursor.fetchone()[0]))
    return int(version), bool(ssl), int(size), int(public_count), tuple(schemas), tuple(tables)


def state_guard(value: tuple) -> str:
    version, ssl, size, public_count, schemas, tables = value
    if version // 10000 != 18 or not ssl:
        raise RuntimeError("Aiven PostgreSQL 18 with TLS is required")
    if size > MAX_EMPTY_DB_BYTES:
        raise RuntimeError("database size is unexpectedly large; refusing installation")
    if public_count != 0:
        raise RuntimeError("public application tables already exist; refusing installation")
    if not any(schemas) and not any(tables):
        return "empty"
    if all(schemas) and all(tables):
        return "already_installed"
    raise RuntimeError("partially initialized or unexpected staging schema")


def main() -> int:
    mode = os.environ.get("AIVEN_STAGING_MODE", "preview")
    confirmation = os.environ.get("AIVEN_STAGING_CONFIRMATION", "")
    try:
        opts = validate_target(
            os.environ.get("AIVEN_POSTGRES_URI", ""),
            os.environ.get("AIVEN_POSTGRES_CA_PEM", ""),
            mode,
            confirmation,
        )
    except ProbeConfigurationError as exc:
        # All errors are static, vetted messages, not credentials.
        print("Aiven staging configuration rejected:", str(exc))
        return 2
    ca_path = None
    try:
        import psycopg
        fd, ca_path = tempfile.mkstemp(prefix="aiven-staging-ca-", suffix=".pem")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as out:
            out.write(os.environ["AIVEN_POSTGRES_CA_PEM"])
        opts.update({
            "sslmode": "verify-full",
            "sslrootcert": ca_path,
            "connect_timeout": 10,
            "application_name": "dankshield_empty_staging_manual",
            "options": "-c statement_timeout=15000 -c lock_timeout=2000",
        })
        # autocommit=True lets a preview be strictly read-only.
        with psycopg.connect(**opts, autocommit=True) as conn:
            if mode == "preview":
                with conn.transaction():
                    conn.execute("set transaction read only")
                    snap = snapshot(conn)
                    status = state_guard(snap)
                print("Aiven PostgreSQL 18 manual staging preview: PASS")
                print("Target service identity: verified")
                print("Verified certificate and hostname: yes")
                print("Schema state:", status)
                print("Database allocated MiB:", round(snap[2] / (1024 * 1024), 1))
                print("No tables, records, or settings changed.")
                return 0

            with conn.transaction():
                # Serializes concurrent manual installation requests.
                conn.execute("select pg_catalog.pg_advisory_xact_lock(71421039)")
                status = state_guard(snapshot(conn))
                if status == "already_installed":
                    print("Empty Aiven staging schema was previously installed; no new changes.")
                    return 0
                for filename in MIGRATIONS:
                    source = (ROOT / "postgres/migrations" / filename).read_text("utf-8")
                    # Simple protocol permits the reviewed multi-statement files.
                    conn.execute(source, prepare=False)
                final = snapshot(conn)
                if not all(final[4]) or not all(final[5]):
                    raise RuntimeError("expected empty staging schema was not created")
                # Source/member data stays empty; only metadata is queried.
                with conn.cursor() as cur:
                    for relation in RELATIONS:
                        cur.execute("select has_table_privilege('public', %s, 'SELECT')", (relation,))
                        if cur.fetchone()[0]:
                            raise RuntimeError("unexpected public table permission")
            print("Aiven staging installation: SUCCESS")
            print("Three empty staging tables installed in dank_core/dank_migration.")
            print("No source rows imported. No production bot routing changed.")
            return 0
    except Exception as exc:
        # No raw exception text (libpq sometimes embeds connection details).
        print("Aiven staging execution failed (error type: " + type(exc).__name__ + ").")
        return 5
    finally:
        if ca_path is not None:
            Path(ca_path).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
