from __future__ import annotations

"""Read-only readiness guard for the shared operation queue schema.

The operation queue is created and hardened exclusively by committed Supabase
migrations. Runtime startup may verify that the service-role REST client can see
the required columns, but it must never recreate or alter the table.
"""

import asyncio
from typing import Any, Optional

_HAS_RUN = False
_TASK: Optional[asyncio.Task] = None
MIGRATION_PATH = "supabase/migrations/20260811175500_operation_queue_security_hardening.sql"
BASE_MIGRATION_PATH = "supabase/migrations/20260613_bot_operation_jobs.sql"

# Compatibility symbol for older imports. Runtime DDL was intentionally removed.
SCHEMA_SQL = ""


def _log(message: str) -> None:
    try:
        print(f"🧱 operation_queue_schema {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ operation_queue_schema {message}")
    except Exception:
        pass


def _probe_sync() -> tuple[bool, str]:
    from stoney_verify.globals import get_supabase

    sb: Any = get_supabase()
    if sb is None:
        return False, "Supabase client unavailable"

    projection = (
        "id,guild_id,actor_id,operation_type,risk_level,source,idempotency_key,"
        "payload_hash,status,progress_current,progress_total,result_json,"
        "locked_by,lock_expires_at,created_at"
    )
    try:
        sb.table("bot_operation_jobs").select(projection).limit(1).execute()
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:240]}"


async def ensure_schema_once() -> bool:
    global _HAS_RUN
    if _HAS_RUN:
        return True
    _HAS_RUN = True

    try:
        ok, reason = await asyncio.to_thread(_probe_sync)
    except Exception as exc:
        _warn(f"readiness probe failed: {type(exc).__name__}: {exc}")
        return False

    if ok:
        _log("bot_operation_jobs schema readable; migrations remain authoritative")
        return True

    _warn(
        "bot_operation_jobs schema is not ready: "
        f"{reason}; apply {BASE_MIGRATION_PATH} and {MIGRATION_PATH} through the migration pipeline"
    )
    return False


def _attach_listener() -> None:
    try:
        from ..globals import bot
    except Exception as exc:
        _warn(f"could not import bot for listener: {exc!r}")
        return
    if getattr(bot, "_stoney_operation_queue_schema_attached", False):
        return

    @bot.listen("on_ready")
    async def _operation_queue_schema_on_ready() -> None:
        await ensure_schema_once()

    try:
        setattr(bot, "_stoney_operation_queue_schema_attached", True)
    except Exception:
        pass
    _log("read-only listener attached")


_attach_listener()

__all__ = ["ensure_schema_once", "SCHEMA_SQL", "MIGRATION_PATH", "BASE_MIGRATION_PATH"]
