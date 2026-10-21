"""Prove the pre-fix pg_cron restore incompatibility on synthetic CI only."""
from __future__ import annotations

import os
import unittest

import psycopg

from tools.verify_source_backup_restoration import validate_disposable_runner

PROBE_DB = "dank_pg_cron_negative_probe"


class PgCronWrongDatabaseTest(unittest.TestCase):
    def test_pg_cron_rejects_unconfigured_disposable_database(self):
        if os.getenv("DANK_RECOVERY_DISPOSABLE_PG17") != "YES_ISOLATED":
            self.skipTest("only the disposable PG17 service can be tested")
        validate_disposable_runner()
        params = dict(
            host="127.0.0.1", port=5432, dbname="postgres",
            user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
            autocommit=True, connect_timeout=10,
        )
        with psycopg.connect(**params) as conn:
            self.assertEqual(
                conn.execute("show cron.database_name").fetchone()[0], "postgres"
            )
            self.assertIsNone(conn.execute(
                "select 1 from pg_database where datname=%s", (PROBE_DB,)
            ).fetchone())
            conn.execute("create database " + PROBE_DB)
        try:
            with psycopg.connect(**{**params, "dbname": PROBE_DB}) as target:
                with self.assertRaises(psycopg.Error) as failed:
                    target.execute("CREATE EXTENSION pg_cron WITH SCHEMA pg_catalog")
                primary = str(failed.exception.diag.message_primary or "")
                self.assertTrue(
                    primary.startswith("can only create extension in database "),
                    "expected PostgreSQL pg_cron wrong-database safeguard",
                )
        finally:
            with psycopg.connect(**params) as conn:
                conn.execute("drop database " + PROBE_DB)


if __name__ == "__main__":
    unittest.main()
