from __future__ import annotations

import pytest
from stoney_verify import postgres_storage_budget as budget


def test_unknown_budget_never_guesses_capacity_from_ram_or_usage():
    status = budget.evaluate_capacity(47 * 1024 * 1024)
    assert status.state == "budget_unverified"
    assert status.utilization is None
    assert status.budget_bytes is None


@pytest.mark.parametrize(
    ("used", "expected"),
    [(69, "within_budget"), (70, "warning"),
     (84, "warning"), (85, "critical"), (105, "critical")],
)
def test_user_supplied_disk_budget_thresholds(used, expected):
    result = budget.evaluate_capacity(
        used * 1024 * 1024, budget_mb=100
    )
    assert result.state == expected


def test_no_zero_budget_or_invalid_thresholds():
    with pytest.raises(ValueError):
        budget.evaluate_capacity(1, 0)
    with pytest.raises(ValueError):
        budget.evaluate_capacity(-1, 100)
    with pytest.raises(ValueError):
        budget.evaluate_capacity(1, 100, warning_fraction=.95,
                                 critical_fraction=.85)


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.in_txn = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.in_txn = False
        return False


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.in_txn = False
        self.readonly = False
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def transaction(self):
        return _Transaction(self)

    def execute(self, statement):
        assert self.in_txn
        self.sql.append(statement)
        if statement == "set transaction read only":
            self.readonly = True
            return _Cursor([])
        assert self.readonly
        if statement == budget.DATABASE_SIZE_SQL:
            return _Cursor([(47 * 1024 * 1024,)])
        assert statement == budget.TABLES_SQL
        return _Cursor([("public", "dank_verify_reminder_delivery",
                         8192, 8, 1, 13, 0)])


def test_snapshot_is_read_only_and_redacts_secrets():
    conn = _Connection()
    dsns = []
    def connect(dsn, **kwargs):
        dsns.append(dsn)
        assert kwargs["connect_timeout"] == 5
        assert kwargs["options"] == "-c statement_timeout=5000"
        return conn

    result = budget.run_from_environment({
        "DANK_VERIFY_REMINDER_DATABASE_URL":
            "postgresql://secret:verysecret@internal:5432/dankshield",
        "DANK_PG_STORAGE_BUDGET_MB": "100",
    }, connect=connect)
    assert result["state"] == "within_budget"
    assert result["top_tables"][0]["table"] == "dank_verify_reminder_delivery"
    assert result["database_allocated_bytes"] == 47 * 1024 * 1024
    assert result["top_tables"][0]["estimated_live_rows"] == 8
    assert conn.sql[0] == "set transaction read only"
    assert len(conn.sql) == 3
    assert dsns[0].startswith("postgresql://")
    assert "secret" not in str(result)
    assert "internal" not in str(result)


def test_private_database_url_required():
    with pytest.raises(ValueError, match="private PostgreSQL URL"):
        budget.run_from_environment({}, connect=lambda *_a, **_k: None)


def test_unrecognized_disk_quota_is_reported_as_unknown():
    result = budget.run_from_environment({
        "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
    }, connect=lambda *_a, **_k: _Connection())
    assert result["state"] == "budget_unverified"


def test_wrong_budget_format_is_rejected():
    with pytest.raises(ValueError, match="integer"):
        budget.run_from_environment({
            "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
            "DANK_PG_STORAGE_BUDGET_MB": "512 MB",
        })
