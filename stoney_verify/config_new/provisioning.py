from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import discord

from ..globals import get_supabase, reset_supabase
from .guild_config import clear_guild_config_cache


DEFAULT_TABLE_NAME = "guild_configs"
DEFAULT_DB_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_ATTEMPTS = 5


# ============================================================
# Helpers
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _table_name() -> str:
    raw = os.getenv("STONEY_GUILD_CONFIG_TABLE", "").strip()
    return raw or DEFAULT_TABLE_NAME


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "remoteprotocolerror",
            "localprotocolerror",
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
            "broken pipe",
            "readerror",
        )
    )


async def _sleep_backoff(attempt: int) -> None:
    await asyncio.sleep(min(0.35 * (2 ** max(0, attempt - 1)), 2.5))


def _guild_id(guild: discord.Guild | int | str) -> int:
    if isinstance(guild, discord.Guild):
        return int(guild.id)
    return _safe_int(guild, 0)


def _default_config_payload(guild: discord.Guild, *, source: str) -> Dict[str, Any]:
    # Keep this aligned with supabase/migrations/20260502_guild_configs.sql.
    # Do not include guild_name/icon/member_count unless the schema adds those columns.
    return {
        "guild_id": str(int(guild.id)),
        "setup_completed": False,
        "setup_source": source,
        "setup_notes": (
            "Auto-provisioned when Stoney Verify saw this guild. "
            "Run setup to save server-specific channels, roles, categories, and panels."
        ),
    }


# ============================================================
# Sync DB operations wrapped by async retry callers
# ============================================================

def _select_config_row_sync(guild_id: int) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return None

    res = (
        sb.table(_table_name())
        .select("guild_id, setup_completed, setup_source")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], dict):
        return dict(rows[0])
    return None


def _insert_config_row_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase client unavailable")

    # insert, not upsert: we intentionally avoid overwriting existing owner/admin config.
    res = sb.table(_table_name()).insert(payload).execute()
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], dict):
        return dict(rows[0])
    return dict(payload)


async def _with_retry(label: str, fn, *args, timeout_seconds: float = DEFAULT_DB_TIMEOUT_SECONDS, **kwargs):
    last_error: Optional[Exception] = None

    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=timeout_seconds,
            )
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < DEFAULT_MAX_ATTEMPTS:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ guild_config_provision {label} transient DB error "
                    f"attempt={attempt}/{DEFAULT_MAX_ATTEMPTS}: {repr(e)}"
                )
                await _sleep_backoff(attempt)
                continue
            raise

    if last_error:
        raise last_error
    return None


# ============================================================
# Public API
# ============================================================

async def ensure_guild_config_row(
    guild: discord.Guild,
    *,
    source: str = "startup_backfill",
    log_prefix: str = "guild_config_provision",
) -> Dict[str, Any]:
    """
    Ensure one guild_configs row exists for this guild.

    Safety rules:
    - Never copies owner/global env IDs into a new guild.
    - Never overwrites existing admin/setup config.
    - One bad guild/Supabase hiccup returns a structured failure instead of
      raising through the gateway event handler.
    """
    started = time.monotonic()
    gid = _guild_id(guild)
    if gid <= 0:
        return {"ok": False, "created": False, "reason": "invalid_guild_id"}

    try:
        existing = await _with_retry("select", _select_config_row_sync, gid)
        if isinstance(existing, dict):
            return {
                "ok": True,
                "created": False,
                "guild_id": str(gid),
                "source": existing.get("setup_source") or "existing",
                "setup_completed": bool(existing.get("setup_completed")),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }

        payload = _default_config_payload(guild, source=source)
        inserted = await _with_retry("insert", _insert_config_row_sync, payload)
        clear_guild_config_cache(gid)

        print(
            f"🧭 {log_prefix} created guild_configs row "
            f"guild={gid} source={source}"
        )
        return {
            "ok": True,
            "created": True,
            "guild_id": str(gid),
            "source": source,
            "row": inserted,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    except Exception as e:
        print(
            f"⚠️ {log_prefix} failed guild={gid} source={source}: {repr(e)}"
        )
        return {
            "ok": False,
            "created": False,
            "guild_id": str(gid),
            "source": source,
            "reason": repr(e),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


async def ensure_guild_config_rows_for_bot(
    bot_obj: Any,
    *,
    source: str = "startup_backfill",
    max_concurrency: int = 4,
) -> Dict[str, Any]:
    guilds = list(getattr(bot_obj, "guilds", []) or [])
    if not guilds:
        return {"ok": True, "guilds": 0, "created": 0, "existing": 0, "failed": 0, "results": []}

    sem = asyncio.Semaphore(max(1, int(max_concurrency or 1)))
    results = []

    async def _one(guild: discord.Guild) -> Dict[str, Any]:
        async with sem:
            return await ensure_guild_config_row(guild, source=source)

    tasks = [_one(guild) for guild in guilds if isinstance(guild, discord.Guild)]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

    normalized = []
    for item in results:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append({"ok": False, "reason": repr(item)})

    created = sum(1 for item in normalized if item.get("ok") and item.get("created"))
    existing = sum(1 for item in normalized if item.get("ok") and not item.get("created"))
    failed = sum(1 for item in normalized if not item.get("ok"))

    summary = {
        "ok": failed == 0,
        "guilds": len(guilds),
        "created": created,
        "existing": existing,
        "failed": failed,
        "results": normalized,
    }
    print(
        "🧭 guild_config_provision startup complete "
        f"guilds={summary['guilds']} created={created} existing={existing} failed={failed}"
    )
    return summary


__all__ = [
    "ensure_guild_config_row",
    "ensure_guild_config_rows_for_bot",
]
