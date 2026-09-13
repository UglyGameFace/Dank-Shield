from __future__ import annotations

"""Read-only schema readiness guard.

Committed files under ``supabase/migrations`` are the only schema mutation
authority.  This module keeps its historical name for import compatibility, but
startup no longer creates or alters database objects and never executes migration
SQL.  It only probes the REST-visible schema and reports which committed
migrations should be applied when required objects are missing.
"""

import asyncio
from pathlib import Path
from typing import Any, Optional

import discord

_HAS_RUN = False
_TASK: Optional[asyncio.Task] = None

# Kept as migration guidance/compatibility metadata.  These files are resolved
# and reported, never executed by runtime startup.
_BOOTSTRAP_MIGRATION_FILES = (
    "20260711_member_activity_truth_ledger.sql",
    "20260802225500_durable_invite_stats.sql",
)
_BOOTSTRAP_MIGRATION_PATTERNS = (
    "*ticket_counter*.sql",
)

# Compatibility symbol for older imports. Runtime DDL was intentionally removed.
SCHEMA_SQL = ""

# table, lightweight REST projection, migration guidance
_SCHEMA_PROBES: tuple[tuple[str, str, str], ...] = (
    (
        "ticket_categories",
        "id,guild_id,slug",
        "supabase/migrations/202607310001_managed_ticket_category_catalog.sql",
    ),
    (
        "tickets",
        "id,guild_id,ticket_number",
        "supabase/migrations/20260430172000_ticket_panel_system.sql",
    ),
    (
        "member_joins",
        "id,guild_id,user_id",
        "supabase/2026-05-08_runtime_stability_schema.sql",
    ),
    (
        "member_activity_ledger",
        "guild_id,user_id",
        "supabase/migrations/20260711_member_activity_truth_ledger.sql",
    ),
    (
        "dank_invite_block_stats",
        "guild_id,invites_blocked",
        "supabase/migrations/20260802225500_durable_invite_stats.sql",
    ),
    (
        "ticket_counters",
        "guild_id,last_ticket_number",
        "supabase/migrations/20260731141000_ticket_counter_durability.sql",
    ),
)


def _log(message: str) -> None:
    try:
        print(f"🧱 schema_readiness {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ schema_readiness {message}")
    except Exception:
        pass


def _required_bootstrap_migrations(migrations_dir: Path) -> list[Path]:
    """Resolve migration guidance without executing any SQL."""
    migration_paths: list[Path] = []

    for migration_name in _BOOTSTRAP_MIGRATION_FILES:
        migration = migrations_dir / migration_name
        if not migration.exists():
            raise RuntimeError(f"Required migration is missing from repository: {migration_name}")
        migration_paths.append(migration)

    for pattern in _BOOTSTRAP_MIGRATION_PATTERNS:
        matches = sorted(migrations_dir.glob(pattern))
        if not matches:
            raise RuntimeError(f"Required migration pattern matched nothing: {pattern}")
        migration_paths.extend(matches)

    deduplicated: list[Path] = []
    seen: set[Path] = set()
    for migration in migration_paths:
        if migration in seen:
            continue
        seen.add(migration)
        deduplicated.append(migration)
    return deduplicated


def _classify_schema_error(exc: BaseException) -> str:
    text = repr(exc).lower()
    if (
        "pgrst204" in text
        or "pgrst205" in text
        or "could not find the table" in text
        or "could not find the" in text and "column" in text
        or "schema cache" in text
    ):
        return "missing_schema"
    if (
        "permission denied" in text
        or "row level security" in text
        or "401" in text
        or "403" in text
    ):
        return "permission_or_rls"
    return "other"


def _probe_schema_sync() -> tuple[list[str], list[str]]:
    from stoney_verify.globals import get_supabase

    sb: Any = get_supabase()
    if sb is None:
        return [], ["Supabase client unavailable"]

    missing: list[str] = []
    blocked: list[str] = []
    for table, projection, migration in _SCHEMA_PROBES:
        try:
            sb.table(table).select(projection).limit(1).execute()
        except Exception as exc:
            category = _classify_schema_error(exc)
            if category == "missing_schema":
                missing.append(f"{table} -> {migration}")
            else:
                blocked.append(f"{table}: {type(exc).__name__}: {str(exc)[:180]}")
    return missing, blocked


async def ensure_schema_once() -> bool:
    """Check schema readiness without mutating the database."""
    global _HAS_RUN
    if _HAS_RUN:
        return True
    _HAS_RUN = True

    migrations_dir = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    try:
        guidance = _required_bootstrap_migrations(migrations_dir)
    except Exception as exc:
        _warn(f"migration manifest invalid: {type(exc).__name__}: {exc}")
        return False

    try:
        missing, blocked = await asyncio.to_thread(_probe_schema_sync)
    except Exception as exc:
        _warn(f"schema readiness probe failed: {type(exc).__name__}: {exc}")
        return False

    if missing:
        _warn(
            "missing schema objects; apply committed Supabase migrations, do not rely on runtime repair: "
            + "; ".join(missing)
        )
    if blocked:
        _warn("schema objects could not be verified: " + "; ".join(blocked))

    if missing or blocked:
        if guidance:
            _log(
                "migration guidance registered: "
                + ", ".join(path.name for path in guidance)
            )
        return False

    _log("required schema readable; migrations remain the sole mutation authority")
    return True


def _attach_listener() -> None:
    try:
        from ..globals import bot
    except Exception as exc:
        _warn(f"could not import bot for listener: {exc!r}")
        return

    if getattr(bot, "_stoney_auto_schema_bootstrap_attached", False):
        return

    @bot.listen("on_ready")
    async def _schema_readiness_on_ready() -> None:
        await ensure_schema_once()

    try:
        setattr(bot, "_stoney_auto_schema_bootstrap_attached", True)
    except Exception:
        pass
    _log("read-only listener attached")


_attach_listener()

__all__ = ["ensure_schema_once", "SCHEMA_SQL"]
