"""Explicit read-only Aiven PostgreSQL connection preflight.

This tool does not migrate schema, inspect user rows, or mutate database state.
It is only executed by the owner-dispatched GitHub Actions preflight.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ProbeConfigurationError(ValueError):
    """Configuration error with non-sensitive, fixed diagnostic messages."""


def parse_aiven_uri(uri: str) -> dict[str, object]:
    """Parse connection credentials privately; reject non-Aiven destinations."""
    if not uri:
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI secret is missing")
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
        database = parsed.path.lstrip("/")
    except (ValueError, TypeError):
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI has an invalid format") from None
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI must be a PostgreSQL URL")
    if not host or not host.lower().endswith(".aivencloud.com"):
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI must target an Aiven service host")
    if not port or not 1 <= port <= 65535:
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI must specify its Aiven service port")
    if not username or not password or not database:
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI must contain user, password, and database")
    if parsed.fragment:
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI cannot contain a URL fragment")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,125}", host):
        raise ProbeConfigurationError("AIVEN_POSTGRES_URI contains an invalid hostname")
    return {
        "host": host,
        "port": port,
        "user": unquote(username),
        "password": unquote(password),
        "dbname": unquote(database),
    }


def check_ca_pem(value: str) -> bool:
    if not value:
        return False
    if (len(value) > 128000 or
        "-----BEGIN CERTIFICATE-----" not in value or
        "-----END CERTIFICATE-----" not in value):
        raise ProbeConfigurationError("AIVEN_POSTGRES_CA_PEM is not a valid PEM certificate block")
    return True


def probe() -> int:
    try:
        connection_options = parse_aiven_uri(os.environ.get("AIVEN_POSTGRES_URI", ""))
        ca_text = os.environ.get("AIVEN_POSTGRES_CA_PEM", "")
        has_ca = check_ca_pem(ca_text)
    except ProbeConfigurationError as exc:
        # These fixed validation errors do not contain user-provided values.
        print("Configuration check failed:", str(exc))
        return 2

    try:
        import psycopg
    except ImportError:
        print("PostgreSQL client dependency unavailable; cannot verify the connection")
        return 3

    ca_path = None
    try:
        if has_ca:
            fd, ca_path = tempfile.mkstemp(prefix="aiven-ca-", suffix=".pem")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as file:
                file.write(ca_text)
            connection_options["sslmode"] = "verify-full"
            connection_options["sslrootcert"] = ca_path
        else:
            # This preliminary check matches Aiven's documented URI behavior:
            # encrypted transport without CA/hostname identity verification.
            connection_options["sslmode"] = "require"
        connection_options["connect_timeout"] = 10
        connection_options["application_name"] = "dankshield_aiven_readonly_probe"
        connection_options["options"] = (
            "-c default_transaction_read_only=on "
            "-c statement_timeout=5000 "
            "-c lock_timeout=1000"
        )
        # SQL is static and SELECT-only. Server-side session default is read-only.
        with psycopg.connect(**connection_options) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_setting('server_version_num')::integer, "
                    "current_setting('transaction_read_only'), "
                    "EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl "
                    "WHERE pid = pg_backend_pid() AND ssl), "
                    "pg_catalog.pg_database_size(current_database()), "
                    "(SELECT count(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public')::integer"
                )
                result = cur.fetchone()
        if not result or len(result) != 5:
            print("Preflight failed: incomplete PostgreSQL metadata")
            return 4
        version, readonly, tls, size_bytes, public_tables = result
        if int(version) < 170000 or readonly != "on" or tls is not True:
            print("Preflight failed: PostgreSQL 17+, read-only, or TLS requirement not met")
            return 4
        print("Aiven PostgreSQL connection: PASS")
        print("PostgreSQL major:", int(version) // 10000)
        print("Read-only transaction: verified")
        print("TLS encrypted: verified")
        print("TLS certificate and hostname: " + ("verified" if has_ca else "NOT VERIFIED (CA secret missing)"))
        print("Current database size (MiB):", round(int(size_bytes) / (1024 * 1024), 1))
        print("Public table count:", int(public_tables))
        print("No data rows read, SQL changes, or production configuration updates were attempted.")
        return 0
    except Exception as exc:
        # libpq/psycopg errors may include the full connection string, host,
        # username, or SQL context. NEVER print exception messages or repr.
        print("Aiven connection preflight failed (error type: " + type(exc).__name__ + ").")
        return 5
    finally:
        if ca_path:
            Path(ca_path).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(probe())
