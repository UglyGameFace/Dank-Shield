from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Dict, List

import discord

from ..guild_sweep import run_bounded_guild_sweep
from ..guild_config import guild_config_cache_snapshot

from ..globals import (
    bot,
    get_supabase,
    reset_supabase,
    claim_startup_flag,
)

SYNC_INTERVAL = 300
DB_MAX_ATTEMPTS = 5
UPSERT_CHUNK_SIZE = 100
# Role/staff snapshots are not live event streams. Avoid identical upserts,
# but always re-persist periodically to repair external drift or DB restores.
_FULL_METRICS_RESYNC_SECONDS = 60 * 60
_LAST_ROLE_SIGNATURES: Dict[int, tuple[float, tuple]] = {}
_LAST_STAFF_SIGNATURES: Dict[int, tuple[float, tuple]] = {}
_METRICS_SKIPPED = {"roles": 0, "staff": 0}
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

    guild_id = int(guild.id)
    signature = tuple(sorted(
        (row["role_id"], row["name"], row["position"], row["member_count"])
        for row in payload
    ))
    now = time.monotonic()
    previous = _LAST_ROLE_SIGNATURES.get(guild_id)
    if previous and previous[1] == signature and now - previous[0] < _FULL_METRICS_RESYNC_SECONDS:
        _METRICS_SKIPPED["roles"] += 1
        return len(payload)

    def _bulk_upsert() -> None:
        sb = get_supabase(force_new=False)
        if sb is None:
            raise RuntimeError("Supabase unavailable for role metrics")
        for chunk in _chunked(payload, UPSERT_CHUNK_SIZE):
            sb.table("guild_roles").upsert(
                chunk,
                on_conflict="guild_id,role_id",
            ).execute()

    _execute_db_write(f"role metrics bulk upsert ({guild.id})", _bulk_upsert)
    _LAST_ROLE_SIGNATURES[guild_id] = (time.monotonic(), signature)
    return len(payload)


def _sync_staff_metrics(guild: discord.Guild) -> int:
    def _select_staff():
        sb = get_supabase(force_new=False)
        if sb is None:
            raise RuntimeError("Supabase unavailable for staff metrics")
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

    guild_id = int(guild.id)
    signature = tuple(sorted(
        (row["staff_id"], row["staff_name"]) for row in payload
    ))
    now = time.monotonic()
    previous = _LAST_STAFF_SIGNATURES.get(guild_id)
    if previous and previous[1] == signature and now - previous[0] < _FULL_METRICS_RESYNC_SECONDS:
        _METRICS_SKIPPED["staff"] += 1
        return len(payload)

    def _bulk_upsert() -> None:
        sb = get_supabase(force_new=False)
        if sb is None:
            raise RuntimeError("Supabase unavailable for staff metrics")
        for chunk in _chunked(payload, UPSERT_CHUNK_SIZE):
            sb.table("staff_metrics").upsert(
                chunk,
                on_conflict="guild_id,staff_id",
            ).execute()

    _execute_db_write(f"staff metrics bulk upsert ({guild.id})", _bulk_upsert)
    _LAST_STAFF_SIGNATURES[guild_id] = (time.monotonic(), signature)
    return len(payload)


async def update_role_metrics(guild: discord.Guild) -> bool:
    try:
        count = await asyncio.to_thread(_sync_role_metrics, guild)
        previous = _LAST_ROLE_METRICS_COUNTS.get(int(guild.id))
        _LAST_ROLE_METRICS_COUNTS[int(guild.id)] = int(count)
        if previous != int(count):
            print(f"📊 Role metrics synced ({count})")
        return True
    except Exception as e:
        print("❌ Role metrics sync failed:", repr(e))
        return False


async def update_staff_metrics(guild: discord.Guild) -> bool:
    try:
        count = await asyncio.to_thread(_sync_staff_metrics, guild)
        previous = _LAST_STAFF_METRICS_COUNTS.get(int(guild.id))
        _LAST_STAFF_METRICS_COUNTS[int(guild.id)] = int(count)
        if previous != int(count):
            print(f"👮 Staff metrics synced ({count})")
        return True
    except Exception as e:
        print("❌ Staff metrics sync failed:", repr(e))
        return False


def _metrics_guild_concurrency() -> int:
    try:
        value = int(os.getenv("DANK_METRICS_GUILD_CONCURRENCY", "4"))
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value, 12))


async def _sync_metrics_for_guild(guild: discord.Guild) -> bool:
    if bot.is_closed():
        return False
    # Maintain role-before-staff ordering for a guild; only independent
    # guilds run concurrently. Existing retry and chunking logic is reused.
    roles_ok = await update_role_metrics(guild)
    staff_ok = await update_staff_metrics(guild)
    return roles_ok and staff_ok


async def metrics_loop() -> None:
    await bot.wait_until_ready()
    print("📡 Metrics sync worker started")
    # Avoid the startup stampede with ticket automation and activity recovery.
    await asyncio.sleep(random.uniform(0.0, 10.0))

    while not bot.is_closed():
        consecutive_failures = 0
        halted_on_failures = False

        async def _sync_one(guild: discord.Guild) -> bool:
            nonlocal consecutive_failures, halted_on_failures
            if halted_on_failures:
                return False
            ok = await _sync_metrics_for_guild(guild)
            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    # An unavailable database should not generate thousands
                    # of per-guild retries every five minutes. Next cycle
                    # probes again; no metrics are marked persisted.
                    halted_on_failures = True
            return ok

        result = await run_bounded_guild_sweep(
            list(bot.guilds),
            _sync_one,
            concurrency=_metrics_guild_concurrency(),
            on_error=lambda guild, exc: print(
                "❌ Metrics guild sync failed "
                f"guild={getattr(guild, 'id', '?')}: {type(exc).__name__}: {exc}"
            ),
        )
        print(
            "📊 metrics sync cycle "
            f"guilds={result.total} ok={result.succeeded} failed={result.failed} "
            f"concurrency={result.concurrency} elapsed_s={result.seconds:.2f} "
            f"halted_on_failures={halted_on_failures}"
        )
        # Local counters only: no telemetry queries or additional egress.
        cfg_metrics = guild_config_cache_snapshot().get("egress", {})
        print(
            "📉 database traffic optimization "
            f"config={cfg_metrics} unchanged_metric_upserts={dict(_METRICS_SKIPPED)}"
        )
        # No overlapping passes or zero-delay retry loops after an overrun.
        await asyncio.sleep(
            max(10.0, SYNC_INTERVAL - result.seconds)
            + random.uniform(0.0, 10.0)
        )


def start_metrics_worker() -> None:
    global _METRICS_TASK

    if _METRICS_TASK is not None and not _METRICS_TASK.done():
        return

    if not claim_startup_flag("metrics_sync_worker"):
        return

    _METRICS_TASK = bot.loop.create_task(metrics_loop())
