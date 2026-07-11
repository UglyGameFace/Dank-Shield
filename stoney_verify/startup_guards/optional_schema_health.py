from __future__ import annotations

"""REST-based optional schema health check.

This guard does not create tables. It uses the configured Supabase REST client to
probe optional production tables and prints precise migration guidance when a
feature is running in memory/fallback mode.
"""

import asyncio
from typing import Any

_TASK: asyncio.Task | None = None
_HAS_RUN = False

_OPTIONAL_TABLES: tuple[tuple[str, str], ...] = (
    (
        "member_activity_ledger",
        "supabase/migrations/20260711_member_activity_truth_ledger.sql",
    ),
    (
        "member_activity_tracker_state",
        "supabase/migrations/20260711_member_activity_truth_ledger.sql",
    ),
    ("member_activity_notices", "supabase/migrations/20260611_member_activity_notices.sql"),
    ("ticket_automation_settings", "supabase/migrations/20260611_ticket_automation_tables.sql"),
    ("ticket_automation_state", "supabase/migrations/20260611_ticket_automation_tables.sql"),
)


def _log(message: str) -> None:
    try:
        print(f"🧱 optional_schema_health {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ optional_schema_health {message}")
    except Exception:
        pass


def _table_readable_sync(table: str) -> tuple[bool, str]:
    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return False, "supabase client unavailable"
        sb.table(table).select("*").limit(1).execute()
        return True, "ok"
    except Exception as e:
        text = repr(e)
        lowered = text.lower()
        if "pgrst205" in lowered or "could not find the table" in lowered or "schema cache" in lowered:
            return False, "missing_table"
        if "permission denied" in lowered or "row level security" in lowered or "401" in lowered or "403" in lowered:
            return False, "permission_or_rls"
        return False, text[:240]


async def _run_once() -> None:
    global _HAS_RUN
    if _HAS_RUN:
        return
    _HAS_RUN = True

    missing: list[str] = []
    blocked: list[str] = []
    for table, migration in _OPTIONAL_TABLES:
        ok, reason = await asyncio.to_thread(_table_readable_sync, table)
        if ok:
            continue
        if reason == "missing_table":
            missing.append(f"{table} -> {migration}")
        else:
            blocked.append(f"{table}: {reason}")

    if not missing and not blocked:
        _log("optional tables readable")
        return

    if missing:
        _warn("missing optional tables: " + "; ".join(missing))
    if blocked:
        _warn("optional tables not readable: " + "; ".join(blocked))


def attach(bot: Any) -> bool:
    async def _on_ready_optional_schema_health() -> None:
        global _TASK
        try:
            if _TASK is not None and not _TASK.done():
                return
            _TASK = asyncio.create_task(_run_once(), name="optional_schema_health")
        except Exception as e:
            _warn(f"failed scheduling check: {e!r}")

    try:
        bot.add_listener(_on_ready_optional_schema_health, "on_ready")
        _log("listener attached")
        return True
    except Exception as e:
        _warn(f"listener attach failed: {e!r}")
        return False


def apply() -> bool:
    try:
        from stoney_verify.globals import bot

        return attach(bot)
    except Exception as e:
        _warn(f"failed to attach: {e!r}")
        return False


apply()

__all__ = ["apply", "attach"]
