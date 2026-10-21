"""Disposable-only transport proof; synthetic records and no project secrets."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import psycopg

from stoney_verify.source_pg_backup import encrypt_stream_to_file, _sha256_file
from tools import verify_source_backup_restoration as recovery


class EncryptedPg17TransportTest(unittest.TestCase):
    def test_synthetic_pg17_dump_encryption_and_restoration(self):
        if os.getenv("DANK_RECOVERY_DISPOSABLE_PG17") != "YES_ISOLATED":
            self.skipTest("requires dedicated synthetic disposable PostgreSQL CI")
        recovery.validate_disposable_runner()
        source_db = "dank_synthetic_restore_probe"
        dsn = {
            "host": "127.0.0.1", "port": 5432,
            "user": os.environ["PGUSER"], "password": os.environ["PGPASSWORD"],
            "dbname": "postgres", "autocommit": True, "connect_timeout": 10,
        }
        with psycopg.connect(**dsn) as conn:
            if conn.execute(
                "select 1 from pg_database where datname=%s", (source_db,)
            ).fetchone():
                self.fail("synthetic database already exists")
            conn.execute("create database " + source_db)
        with psycopg.connect(**{**dsn, "dbname": source_db}) as conn:
            conn.execute(
                "create table public.synthetic_restore_probe "
                "(id integer primary key, sample text not null)"
            )
            conn.execute(
                "insert into public.synthetic_restore_probe values "
                "(7, 'public synthetic fixture only')"
            )
        password = "SYNTHETIC-TEST-ARCHIVE-PASSPHRASE-NO-REAL-SECRETS"
        with tempfile.TemporaryDirectory(prefix="synthetic-restore-") as temp:
            archive = Path(temp) / "synthetic.dsbak"
            env = recovery._safe_pg_env()
            env["PGDATABASE"] = source_db
            # This matches the owner workflow's PostgreSQL 17 Docker client.
            command = [
                "docker", "run", "--rm", "--network=host",
                "-e", "PGHOST", "-e", "PGPORT", "-e", "PGUSER",
                "-e", "PGPASSWORD", "-e", "PGDATABASE", "postgres:17",
                "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
            ]
            process = subprocess.Popen(
                command, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            try:
                with archive.open("wb") as out:
                    assert process.stdout is not None
                    encrypt_stream_to_file(process.stdout, out, password)
                assert process.stdout is not None
                process.stdout.close()
                self.assertEqual(process.wait(timeout=90), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            digest = _sha256_file(archive)
            recovery._check_archive(archive, digest, password)
            recovery._create_empty_restore_db()
            streamed = recovery._stream_restore(archive, password, digest)
            self.assertGreater(streamed, 64)
        with psycopg.connect(**{**dsn, "dbname": recovery.EXPECTED_RESTORE_DB}) as conn:
            row = conn.execute(
                "select id, sample from public.synthetic_restore_probe"
            ).fetchone()
            self.assertEqual(row, (7, "public synthetic fixture only"))


if __name__ == "__main__":
    unittest.main()
