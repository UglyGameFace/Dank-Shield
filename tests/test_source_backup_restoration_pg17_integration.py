"""Opt-in real PG17 encrypted archive restoration of disposable fixture data."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from stoney_verify import source_pg_backup as backup
from tools.verify_source_backup_restoration import run_restore_drill

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PG_BACKUP_INTEGRATION") != "1",
    reason="requires local disposable PostgreSQL 17 CI service and Docker",
)


def test_full_disposable_pg17_restore_with_critical_data(tmp_path, monkeypatch):
    import psycopg
    from psycopg import sql

    connection = (
        "host=127.0.0.1 port=5432 user=postgres dbname=postgres connect_timeout=10"
    )
    with psycopg.connect(connection, password="postgres") as conn:
        for table in (
            "guild_configs", "guild_config_versions", "staff_action_logs",
            "activity_feed_events", "tickets",
        ):
            conn.execute(
                sql.SQL(
                    "create table if not exists public.{} "
                    "(id bigint primary key, fixture_label text not null)"
                ).format(sql.Identifier(table))
            )
            conn.execute(
                sql.SQL(
                    "insert into public.{} (id, fixture_label) "
                    "values (999, 'fixture only') on conflict (id) do nothing"
                ).format(sql.Identifier(table))
            )

    # No source Supabase or Discloud environment variables. This is a
    # disposable local Postgres source that we can safely restore elsewhere.
    dumping = subprocess.Popen([
        "docker", "run", "--rm", "-i", "--network=host",
        "-e", "PGPASSWORD=postgres", "postgres:17",
        "pg_dump", "-h", "127.0.0.1", "-U", "postgres",
        "-d", "postgres", "--format=custom",
        "--no-owner", "--no-privileges",
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    secret = "TEST_ONLY_PG17_offline_restoration_secret_2026"
    encrypted = tmp_path / "ephemeral-pg17.dsbak"
    try:
        assert dumping.stdout is not None
        with encrypted.open("wb") as file:
            backup.encrypt_stream_to_file(dumping.stdout, file, secret)
        dumping.stdout.close()
        assert dumping.wait(timeout=90) == 0
    finally:
        if dumping.poll() is None:
            dumping.kill()
            dumping.wait()

    for k, v in {
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "GITHUB_REPOSITORY": "UglyGameFace/Dank-Shield",
        "DANK_RECOVERY_DISPOSABLE_PG17": "YES_ISOLATED",
        "PGHOST": "127.0.0.1", "PGPORT": "5432",
        "PGUSER": "postgres", "PGDATABASE": "postgres",
        "PGPASSWORD": "postgres",
    }.items():
        monkeypatch.setenv(k, v)
    wrapper = tmp_path / "pg-client"
    subprocess.run([
        "bash", "tools/setup_pg17_backup_clients.sh", str(wrapper)
    ], check=True)
    monkeypatch.setenv("PATH", f"{wrapper}:{os.environ.get('PATH','')}")
    outcome = run_restore_drill(
        encrypted, expected_sha256=backup._sha256_file(encrypted),
        passphrase=secret,
    )
    assert outcome["status"] == "isolated_pg17_restore_verified"
    assert outcome["authenticated"] is True
    assert outcome["archive_toc_readable"] is True
    assert outcome["source_data_modified"] is False
    assert outcome["discloud_data_modified"] is False
    assert all(
        count >= 1 for count in outcome["critical_table_row_counts"].values()
    )
    assert outcome["schema_table_counts"]["public"] >= 5


@pytest.mark.skipif(
    os.getenv("RUN_BACKUP9_SUPABASE_IMAGE_INTEGRATION") != "1",
    reason="requires an isolated Supabase PostgreSQL 17 CI service",
)
def test_supabase_recovery_service_can_create_vault_extension():
    """Prove the missing extension can actually be restored into fresh PG17."""
    import psycopg
    from tools import verify_source_backup_restoration as recovery

    recovery.validate_disposable_runner()
    assert os.environ["PGUSER"] == "supabase_admin"
    settings = dict(
        host="127.0.0.1", port=5432,
        user="supabase_admin", password="postgres", connect_timeout=10,
    )
    with psycopg.connect(
        dbname="postgres", autocommit=True, **settings
    ) as conn:
        assert int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 == 17
        assert conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'supabase_vault'"
        ).fetchone() is not None
        # GitHub publishes service logs at teardown. The exact restore
        # preflight must prevent private SQL statements from reaching them.
        conn.execute("ALTER SYSTEM SET log_min_error_statement = 'panic'")
        conn.execute("ALTER SYSTEM SET log_min_messages = 'fatal'")
        conn.execute("SELECT pg_reload_conf()")
        assert conn.execute("SHOW log_min_error_statement").fetchone()[0] == "panic"
        assert conn.execute("SHOW log_min_messages").fetchone()[0] == "fatal"
    recovery._create_empty_restore_db()
    with psycopg.connect(
        dbname=recovery.EXPECTED_RESTORE_DB, autocommit=True, **settings
    ) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS vault")
        conn.execute("CREATE EXTENSION IF NOT EXISTS supabase_vault WITH SCHEMA vault")
        assert conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'supabase_vault'"
        ).fetchone() is not None
