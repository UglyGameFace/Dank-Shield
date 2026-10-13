from __future__ import annotations

import os
from pathlib import Path
import pytest

from stoney_verify import core_data_stage as stage

ROOT = Path(__file__).resolve().parents[1]


def _records():
    return [
        {"id": 4, "guild_id": "123", "settings": {"color": "mint"}},
        {"id": 5, "guild_id": "456", "settings": {"color": "purple"}},
    ]


def _fetch_for(source):
    def fetch(table, primary_keys, offset, page_size):
        assert table in stage.SOURCE_KEYS
        assert primary_keys == stage.SOURCE_KEYS[table]
        return source.get(table, [])[offset:offset + page_size]
    return fetch


def test_dry_run_does_not_expose_payload_and_has_stable_digests():
    source = {"guild_configs": _records()}
    a = stage.plan_source(_fetch_for(source),
                          selected_tables=["guild_configs"], page_size=1)
    b = stage.plan_source(_fetch_for(source),
                          selected_tables=["guild_configs"], page_size=2)
    assert a.table_counts == {"guild_configs": 2}
    assert a.table_digests == b.table_digests
    assert a.accounted_bytes == b.accounted_bytes
    assert "purple" not in str(a.summary)
    assert "mint" not in str(a.summary)
    assert all(len(rec.key_hash) == 64 for rec in a.rows)


def test_source_changed_payload_changes_hash():
    rows = _records()
    before = stage.plan_source(_fetch_for({"guild_configs": rows}),
                               selected_tables=["guild_configs"])
    source = {"guild_configs": [rows[0], {**rows[1], "settings": {"color": "blue"}}]}
    after = stage.plan_source(_fetch_for(source),
                              selected_tables=["guild_configs"])
    assert before.table_counts == after.table_counts
    assert before.table_digests != after.table_digests


def test_reject_duplicate_primary_keys_and_missing_keys():
    rows = [{"id": 1}, {"id": 1}]
    with pytest.raises(RuntimeError, match="duplicate key"):
        stage.plan_source(_fetch_for({"guild_configs": rows}),
                          selected_tables=["guild_configs"])
    with pytest.raises(ValueError, match="missing required primary key"):
        stage.plan_source(_fetch_for({"guild_configs": [{"guild_id": "5"}]}),
                          selected_tables=["guild_configs"])


def test_unknown_table_cannot_be_read():
    with pytest.raises(ValueError, match="unknown or duplicate"):
        stage.plan_source(_fetch_for({}), selected_tables=["users"])
    with pytest.raises(ValueError, match="unknown or duplicate"):
        stage.plan_source(_fetch_for({}), selected_tables=["guild_configs", "guild_configs"])


def test_bounded_payload_and_page_limits():
    with pytest.raises(RuntimeError, match="byte budget"):
        stage.plan_source(
            _fetch_for({"guild_configs": [{"id": 1, "settings": {"blob": "x" * 30000}}]}),
            selected_tables=["guild_configs"], max_bytes=1500,
        )
    with pytest.raises(ValueError, match="page size"):
        stage.plan_source(_fetch_for({}), page_size=201)
    with pytest.raises(ValueError, match="payload limit"):
        stage.plan_source(_fetch_for({}), max_bytes=33 * 1024 * 1024)


def test_composite_key_is_order_independent():
    a = {"guild_id": "123", "user_id": "456", "type": "ok"}
    b = {"user_id": "456", "type": "ok", "guild_id": "123"}
    x = stage.plan_source(_fetch_for({"member_activity_ledger": [a]}),
                          selected_tables=["member_activity_ledger"])
    y = stage.plan_source(_fetch_for({"member_activity_ledger": [b]}),
                          selected_tables=["member_activity_ledger"])
    assert x.rows[0].key_hash == y.rows[0].key_hash
    assert x.rows[0].payload_sha256 == y.rows[0].payload_sha256


def test_reader_uses_verified_keys_and_correct_range():
    calls = []
    class Response:
        data = [{"id": 1}]
    class Query:
        def select(self, value):
            assert value == "*"
            return self
        def order(self, key):
            calls.append(("order", key))
            return self
        def range(self, low, high):
            calls.append(("range", low, high))
            return self
        def execute(self):
            return Response()
    class Client:
        def table(self, name):
            assert name == "guild_configs"
            return Query()
    reader = stage.rest_page_reader(Client())
    assert reader("guild_configs", ("id",), 20, 10) == [{"id": 1}]
    assert calls == [("order", "id"), ("range", 20, 29)]
    with pytest.raises(ValueError, match="unapproved"):
        reader("users", ("id",), 0, 10)


def test_limit_does_not_fall_back_to_unsafe_all_tables():
    with pytest.raises(ValueError, match="at least one"):
        stage.plan_source(_fetch_for({}), selected_tables=[])


def test_staging_sql_is_isolated_and_has_no_auto_delete():
    sql = (ROOT / "postgres/migrations/202610130002_core_staging.sql").read_text()
    assert "create schema if not exists dank_migration" in sql.lower()
    assert "create table if not exists dank_migration.staged_records" in sql.lower()
    assert "revoke all on schema dank_migration from public" in sql.lower()
    assert "drop table" not in sql.lower()
    assert "delete from" not in sql.lower()
    assert "public.dank_verify_reminder_delivery" not in sql.lower()


@pytest.mark.skipif(not os.getenv("RUN_CORE_PG_INTEGRATION"),
                    reason="requires isolated PostgreSQL 17 CI service")
def test_postgres_staging_idempotent_and_conflict_is_atomic():
    import psycopg
    source = {"guild_configs": _records()}
    fetch = _fetch_for(source)
    def plan():
        return stage.plan_source(fetch, selected_tables=["guild_configs"])
    dsn = os.environ["DANK_TEST_DATABASE_URL"]
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("delete from dank_migration.staged_records where source_table='guild_configs'")
        result = stage.apply_stage(
            plan(), connection, expected_db="postgres",
            disk_budget_mb=1024, source_reread=plan,
        )
        assert result["staged_count"] == 2
        stage.apply_stage(
            plan(), connection, expected_db="postgres",
            disk_budget_mb=1024, source_reread=plan,
        )
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from dank_migration.staged_records")
            assert cursor.fetchone()[0] == 2
        source["guild_configs"] = [
            source["guild_configs"][0],
            {"id": 5, "guild_id": "456", "settings": {"color": "changed"}},
        ]
        with pytest.raises(RuntimeError, match="changed since"):
            stage.apply_stage(
                plan(), connection, expected_db="postgres",
                disk_budget_mb=1024, source_reread=plan,
            )
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from dank_migration.staged_records")
            assert cursor.fetchone()[0] == 2
