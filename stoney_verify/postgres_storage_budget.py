"""Read-only Discloud PostgreSQL capacity preflight.

One-shot operator diagnostic. Never touches schema, calls cleanup, or emits
connection strings. Usage is allocated PostgreSQL bytes, not provider disk
quota or Supabase billing-cycle usage.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

# Discloud Diamond's confirmed shared plan allowance is 10 decimal GB,
# NOT ten dedicated database GiB. This constant is used only by the explicit
# private operator migration commands, never at Discord startup.
DIAMOND_SHARED_STORAGE_BYTES = 10_000_000_000
MIB = 1024 * 1024
# An additional shared-pool reserve protects app files, new writes and WAL.
SHARED_POOL_RESERVE_BYTES = 1024 * MIB


def migration_database_budget_mb(environ: Mapping[str, str]) -> int:
    """Require a verified DB earmark inside the *shared* Diamond allowance.

    Other-file consumption is measured outside the target DB. Without that
    operator-provided measurement, the apply path cannot assume the entire
    10 GB plan belongs to PostgreSQL. All values use MiB (not decimal MB).
    """
    raw_target = str(environ.get("DANK_PG_STORAGE_BUDGET_MB") or "").strip()
    raw_other = str(environ.get("DANK_PG_OTHER_APP_STORAGE_MIB") or "").strip()
    if not raw_target or not raw_other:
        raise ValueError("shared-storage usage and dedicated DB budget must be verified")
    try:
        budget_mb = int(raw_target)
        other_mb = int(raw_other)
    except ValueError:
        raise ValueError("storage inputs must be integer MiB") from None
    if budget_mb <= 0 or other_mb < 0:
        raise ValueError("invalid positive DB budget or other app usage")
    committed = (budget_mb + other_mb) * MIB + SHARED_POOL_RESERVE_BYTES
    if committed > DIAMOND_SHARED_STORAGE_BYTES:
        raise ValueError("database budget exceeds remaining shared Diamond storage")
    return budget_mb


DEFAULT_WARNING_FRACTION = 0.70
DEFAULT_CRITICAL_FRACTION = 0.85
MAX_TABLES = 12

# The database-wide budget must be obtained from the Discloud plan, not from
# the service's 512 MB RAM allocation or its current 47 MB usage.
DATABASE_SIZE_SQL = "select pg_database_size(current_database())"
TABLES_SQL = """
select n.nspname, c.relname, pg_total_relation_size(c.oid) as bytes_used,
       coalesce(s.n_live_tup, 0) as live_rows,
       coalesce(s.n_dead_tup, 0) as dead_rows,
       coalesce(s.n_tup_ins, 0) as writes,
       coalesce(s.n_tup_del, 0) as deletes
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_user_tables s on s.relid = c.oid
where c.relkind in ('r', 'p')
  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
order by pg_total_relation_size(c.oid) desc, n.nspname, c.relname
limit 12
"""


@dataclass(frozen=True)
class Capacity:
    used_bytes: int
    budget_bytes: int | None
    utilization: float | None
    state: str


def evaluate_capacity(
    used_bytes: int, budget_mb: int | None = None,
    *, warning_fraction: float = DEFAULT_WARNING_FRACTION,
    critical_fraction: float = DEFAULT_CRITICAL_FRACTION,
) -> Capacity:
    if used_bytes < 0:
        raise ValueError("used_bytes must not be negative")
    if not (0 < warning_fraction < critical_fraction < 1):
        raise ValueError("invalid capacity thresholds")
    if budget_mb is None:
        return Capacity(used_bytes, None, None, "budget_unverified")
    if budget_mb <= 0:
        raise ValueError("budget must be a positive amount of disk space")
    budget = int(budget_mb) * 1024 * 1024
    ratio = used_bytes / budget
    state = ("critical" if ratio >= critical_fraction
             else "warning" if ratio >= warning_fraction else "within_budget")
    return Capacity(used_bytes, budget, ratio, state)


def read_only_snapshot(
    dsn: str, *, budget_mb: int | None = None,
    connect: Any = None,
) -> dict[str, Any]:
    """Read one consistent snapshot; does no UPDATE/INSERT/DDL/DELETE.

    A read-only transaction prevents accidentally introducing writes when
    this diagnostic is extended. SQL is constant and has no user identifiers.
    """
    if not dsn:
        raise ValueError("private PostgreSQL URL is required")
    if connect is None:
        import psycopg
        connect = psycopg.connect
    with connect(
        dsn, connect_timeout=5, options="-c statement_timeout=5000"
    ) as connection:
        with connection.transaction():
            connection.execute("set transaction read only")
            row = connection.execute(DATABASE_SIZE_SQL).fetchone()
            if not row:
                raise RuntimeError("PostgreSQL returned no size")
            used = int(row[0])
            records = connection.execute(TABLES_SQL).fetchall()
    state = evaluate_capacity(used, budget_mb)
    return {
        "state": state.state,
        "database_allocated_bytes": used,
        "budget_bytes": state.budget_bytes,
        "utilization_pct": (
            round(state.utilization * 100, 2)
            if state.utilization is not None else None
        ),
        "top_tables": [
            {
                "schema": str(schema), "table": str(table),
                "allocated_bytes": int(size),
                "estimated_live_rows": int(live),
                "estimated_dead_rows": int(dead),
                "writes_since_stats_reset": int(writes),
                "deletes_since_stats_reset": int(deletes),
            }
            for schema, table, size, live, dead, writes, deletes in records
        ],
        "disclaimer": (
            "PostgreSQL allocated bytes are not Discloud disk free space. "
            "Row counts are planner estimates. No records were changed."
        ),
    }


def run_from_environment(
    environ: Mapping[str, str] | None = None,
    *, connect: Any = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    dsn = str(env.get("DANK_CORE_POSTGRES_DATABASE_URL") or
              env.get("DANK_VERIFY_REMINDER_DATABASE_URL") or "").strip()
    raw_budget = str(env.get("DANK_PG_STORAGE_BUDGET_MB") or "").strip()
    if raw_budget:
        try:
            budget = int(raw_budget)
        except ValueError as exc:
            raise ValueError("DANK_PG_STORAGE_BUDGET_MB must be an integer") from exc
    else:
        budget = None
    return read_only_snapshot(dsn, budget_mb=budget, connect=connect)
