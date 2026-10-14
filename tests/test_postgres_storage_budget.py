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
        "DANK_PG_OTHER_APP_STORAGE_MIB": "0",
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


def test_storage_audit_refuses_unverified_shared_usage_without_connecting():
    calls = []

    def connect(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("should fail before connecting")

    with pytest.raises(ValueError, match="shared-storage usage"):
        budget.run_from_environment({
            "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
            "DANK_PG_STORAGE_BUDGET_MB": "4096",
        }, connect=connect)
    assert calls == []


def test_storage_audit_refuses_overcommitted_shared_quota():
    with pytest.raises(ValueError, match="remaining shared Diamond storage"):
        budget.run_from_environment({
            "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
            "DANK_PG_STORAGE_BUDGET_MB": "8000",
            "DANK_PG_OTHER_APP_STORAGE_MIB": "1200",
        }, connect=lambda *_a, **_kw: None)


def test_storage_audit_uses_actual_earmarked_db_budget_not_plan_total():
    report = budget.run_from_environment({
        "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
        "DANK_PG_STORAGE_BUDGET_MB": "50",
        "DANK_PG_OTHER_APP_STORAGE_MIB": "50",
    }, connect=lambda *_args, **_kwargs: _Connection())
    assert report["state"] == "critical"
    assert report["database_allocated_bytes"] == 47 * 1024 * 1024
    assert report["budget_bytes"] == 50 * 1024 * 1024
    assert report["utilization_pct"] == 94.0


def test_wrong_budget_format_is_rejected():
    with pytest.raises(ValueError, match="integer"):
        budget.run_from_environment({
            "DANK_CORE_POSTGRES_DATABASE_URL": "postgresql://private",
            "DANK_PG_STORAGE_BUDGET_MB": "512 MB",
        })


@pytest.mark.parametrize("budget_mb,other_app_mib", [
    (512, 0), (4096, 1024), (5000, 2000),
])
def test_confirmed_10_decimal_gb_shared_storage_allows_earmarked_db_space(
    budget_mb, other_app_mib,
):
    assert budget.migration_database_budget_mb({
        "DANK_PG_STORAGE_BUDGET_MB": str(budget_mb),
        "DANK_PG_OTHER_APP_STORAGE_MIB": str(other_app_mib),
    }) == budget_mb


def test_shared_storage_budget_never_treats_10gb_as_dedicated_db():
    assert budget.DIAMOND_SHARED_STORAGE_BYTES == 10_000_000_000
    assert budget.SHARED_POOL_RESERVE_BYTES == 1024 * 1024 * 1024
    with pytest.raises(ValueError, match="remaining shared Diamond storage"):
        budget.migration_database_budget_mb({
            "DANK_PG_STORAGE_BUDGET_MB": "9000",
            "DANK_PG_OTHER_APP_STORAGE_MIB": "0",
        })
    with pytest.raises(ValueError, match="remaining shared Diamond storage"):
        budget.migration_database_budget_mb({
            "DANK_PG_STORAGE_BUDGET_MB": "7000",
            "DANK_PG_OTHER_APP_STORAGE_MIB": "2000",
        })


@pytest.mark.parametrize("env", [
    {},
    {"DANK_PG_STORAGE_BUDGET_MB": "512"},
    {"DANK_PG_OTHER_APP_STORAGE_MIB": "0"},
    {"DANK_PG_STORAGE_BUDGET_MB": "512", "DANK_PG_OTHER_APP_STORAGE_MIB": "-1"},
    {"DANK_PG_STORAGE_BUDGET_MB": "0", "DANK_PG_OTHER_APP_STORAGE_MIB": "0"},
    {"DANK_PG_STORAGE_BUDGET_MB": "512MB", "DANK_PG_OTHER_APP_STORAGE_MIB": "0"},
])
def test_shared_storage_migration_fails_closed_without_verified_accounting(env):
    with pytest.raises(ValueError):
        budget.migration_database_budget_mb(env)


def test_all_explicit_migration_apply_commands_use_one_storage_authority():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (
        "tools/stage_dankshield_core_data.py",
        "tools/materialize_dankshield_pg_config.py",
    ):
        text = (root / path).read_text("utf-8")
        assert "migration_database_budget_mb(os.environ)" in text
        assert "DANK_PG_STORAGE_BUDGET_MB" not in text


def test_all_storage_operator_paths_share_one_validator():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = (root / "stoney_verify/postgres_storage_budget.py").read_text("utf-8")
    assert "budget = migration_database_budget_mb(env) if raw_budget else None" in code
    assert "DANK_PG_OTHER_APP_STORAGE_MIB" in code
