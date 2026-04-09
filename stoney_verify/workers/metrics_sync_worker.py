from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List

import discord

from ..globals import (
    bot,
    get_supabase,
    reset_supabase,
    claim_startup_flag,
)

SYNC_INTERVAL = 300
DB_MAX_ATTEMPTS = 5
UPSERT_CHUNK_SIZE = 100
_LAST_ROLE_METRICS_COUNTS: Dict[int, int] = {}
_LAST_STAFF_METRICS_COUNTS: Dict[int, int] = {}
_METRICS_TASK: asyncio.Task | None = None


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "httpcore",
        "httpx",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _chunked(rows: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _execute_db_write(op_name: str, executor, max_attempts: int = DB_MAX_ATTEMPTS):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt {attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise
    raise last_error


def _sync_role_metrics(guild: discord.Guild) -> int:
    payload: List[Dict[str, Any]] = []

    for role in guild.roles:
        if role.name == "@everyone":
            continue
        payload.append(
            {
                "guild_id": str(guild.id),
                "role_id": str(role.id),
                "name": role.name,
                "position": int(role.position),
                "member_count": len(role.members),
            }
        )

    if not payload:
        return 0

    def _bulk_upsert() -> None:
        sb = get_supabase(force_new=False)
        if sb is None:
            return
        for chunk in _chunked(payload, UPSERT_CHUNK_SIZE):
            sb.table("guild_roles").upsert(
                chunk,
                on_conflict="guild_id,role_id",
            ).execute()

    _execute_db_write(f"role metrics bulk upsert ({guild.id})", _bulk_upsert)
    return len(payload)


def _sync_staff_metrics(guild: discord.Guild) -> int:
    def _select_staff():
        sb = get_supabase(force_new=False)
        if sb is None:
            return []
        res = (
            sb.table("guild_members")
            .select("user_id, username")
            .eq("guild_id", str(guild.id))
            .eq("has_staff_role", True)
            .execute()
        )
        return getattr(res, "data", None) or []

    rows = _execute_db_write(f"staff metrics select ({guild.id})", _select_staff) or []

    payload: List[Dict[str, Any]] = []
    for row in rows:
        staff_id = str(row.get("user_id") or "").strip()
        if not staff_id:
            continue
        payload.append(
            {
                "guild_id": str(guild.id),
                "staff_id": staff_id,
                "staff_name": row.get("username") or "",
            }
        )

    if not payload:
        return 0

    def _bulk_upsert() -> None:
        sb = get_supabase(force_new=False)
        if sb is None:
            return
        for chunk in _chunked(payload, UPSERT_CHUNK_SIZE):
            sb.table("staff_metrics").upsert(
                chunk,
                on_conflict="guild_id,staff_id",
            ).execute()

    _execute_db_write(f"staff metrics bulk upsert ({guild.id})", _bulk_upsert)
    return len(payload)


async def update_role_metrics(guild: discord.Guild) -> None:
    try:
        count = await asyncio.to_thread(_sync_role_metrics, guild)
        previous = _LAST_ROLE_METRICS_COUNTS.get(int(guild.id))
        _LAST_ROLE_METRICS_COUNTS[int(guild.id)] = int(count)
        if previous != int(count):
            print(f"📊 Role metrics synced ({count})")
    except Exception as e:
        print("❌ Role metrics sync failed:", repr(e))


async def update_staff_metrics(guild: discord.Guild) -> None:
    try:
        count = await asyncio.to_thread(_sync_staff_metrics, guild)
        previous = _LAST_STAFF_METRICS_COUNTS.get(int(guild.id))
        _LAST_STAFF_METRICS_COUNTS[int(guild.id)] = int(count)
        if previous != int(count):
            print(f"👮 Staff metrics synced ({count})")
    except Exception as e:
        print("❌ Staff metrics sync failed:", repr(e))


async def metrics_loop() -> None:
    await bot.wait_until_ready()
    print("📡 Metrics sync worker started")

    while not bot.is_closed():
        for guild in list(bot.guilds):
            try:
                await update_role_metrics(guild)
                await update_staff_metrics(guild)
            except Exception as e:
                print("❌ Metrics loop error:", repr(e))
        await asyncio.sleep(SYNC_INTERVAL)


def start_metrics_worker() -> None:
    global _METRICS_TASK

    if _METRICS_TASK is not None and not _METRICS_TASK.done():
        return

    if not claim_startup_flag("metrics_sync_worker"):
        return

    _METRICS_TASK = bot.loop.create_task(metrics_loop())
