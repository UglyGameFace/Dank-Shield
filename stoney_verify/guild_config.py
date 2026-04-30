from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord

from .globals import (
    get_supabase,
    reset_supabase,
    now_utc,

    # Safe fallback values only.
    GUILD_ID,
    VERIFY_CHANNEL_ID,
    VC_VERIFY_CHANNEL_ID,
    VC_VERIFY_QUEUE_CHANNEL_ID,
    TICKET_CATEGORY_ID,
    TRANSCRIPTS_CHANNEL_ID,
    MODLOG_CHANNEL_ID,
    RAIDLOG_CHANNEL_ID,
    JOIN_LOG_CHANNEL_ID,
    FORCE_VERIFY_LOG_CHANNEL_ID,

    UNVERIFIED_ROLE_ID,
    VERIFIED_ROLE_ID,
    RESIDENT_ROLE_ID,
    STAFF_ROLE_ID,
    VC_STAFF_ROLE_ID,
)


# ============================================================
# guild_config.py
# ------------------------------------------------------------
# Per-server config resolver.
#
# Design:
# - DB per-guild config is authoritative.
# - Discord runtime discovery is used when useful.
# - .env values are FALLBACK ONLY.
# - Missing config should not crash public servers.
# - Backward compatible with callers using refresh=...
#
# This lets Stoney Verify run across many servers without
# requiring every server owner to edit your deployment .env.
# ============================================================

GUILD_CONFIG_TABLE = "guild_config"

_CACHE_TTL_SECONDS = 60
_DB_MAX_ATTEMPTS = 5

_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_CONFIG_CACHE_TS: Dict[str, datetime] = {}


# ============================================================
# Helpers
# ============================================================

def _debug(message: str) -> None:
    try:
        print(f"🧩 guild_config {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _snowflake_str(value: Any) -> Optional[str]:
    num = _safe_int(value, 0)
    if num <= 0:
        return None
    return str(num)


def _now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _cache_key(guild_id: Any) -> str:
    return str(_safe_int(guild_id, 0))


def clear_guild_config_cache(guild_id: Optional[Any] = None) -> None:
    if guild_id is None:
        _CONFIG_CACHE.clear()
        _CONFIG_CACHE_TS.clear()
        return

    key = _cache_key(guild_id)
    _CONFIG_CACHE.pop(key, None)
    _CONFIG_CACHE_TS.pop(key, None)


def _cache_valid(guild_id: Any) -> bool:
    key = _cache_key(guild_id)
    ts = _CONFIG_CACHE_TS.get(key)
    if ts is None:
        return False
    try:
        return (_now() - ts).total_seconds() <= _CACHE_TTL_SECONDS
    except Exception:
        return False


def _fallback_guild_id(guild_id: Any = None) -> int:
    explicit = _safe_int(guild_id, 0)
    if explicit > 0:
        return explicit
    return _safe_int(GUILD_ID, 0)


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
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor, max_attempts: int = _DB_MAX_ATTEMPTS):
    last_error: Optional[Exception] = None

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
                    f"⚠️ guild_config {op_name}: transient DB error "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise

    if last_error is not None:
        raise last_error
    return None


async def _run_db(op_name: str, executor):
    return await asyncio.to_thread(_execute_db_op, op_name, executor)


# ============================================================
# Default / fallback config
# ============================================================

def env_fallback_guild_config(guild_id: Any = None) -> Dict[str, Any]:
    """
    Build fallback config from .env globals.

    IMPORTANT:
    These are fallback values only. They should not be treated as
    authoritative for public multi-server installs.
    """
    gid = _fallback_guild_id(guild_id)

    return {
        "guild_id": str(gid) if gid > 0 else "",
        "source": "env_fallback",

        "verify_channel_id": _snowflake_str(VERIFY_CHANNEL_ID),
        "vc_verify_channel_id": _snowflake_str(VC_VERIFY_CHANNEL_ID),
        "vc_verify_queue_channel_id": _snowflake_str(VC_VERIFY_QUEUE_CHANNEL_ID),

        "ticket_category_id": _snowflake_str(TICKET_CATEGORY_ID),
        "transcripts_channel_id": _snowflake_str(TRANSCRIPTS_CHANNEL_ID),

        "modlog_channel_id": _snowflake_str(MODLOG_CHANNEL_ID),
        "raidlog_channel_id": _snowflake_str(RAIDLOG_CHANNEL_ID),
        "join_log_channel_id": _snowflake_str(JOIN_LOG_CHANNEL_ID),
        "force_verify_log_channel_id": _snowflake_str(FORCE_VERIFY_LOG_CHANNEL_ID),

        "unverified_role_id": _snowflake_str(UNVERIFIED_ROLE_ID),
        "verified_role_id": _snowflake_str(VERIFIED_ROLE_ID),
        "resident_role_id": _snowflake_str(RESIDENT_ROLE_ID),
        "staff_role_id": _snowflake_str(STAFF_ROLE_ID),
        "vc_staff_role_id": _snowflake_str(VC_STAFF_ROLE_ID),

        "use_env_fallbacks": True,
        "allow_runtime_discovery": True,
        "created_at": None,
        "updated_at": None,
        "raw": {},
    }


def _normalize_config_row(row: Optional[Dict[str, Any]], guild_id: Any = None) -> Dict[str, Any]:
    fallback = env_fallback_guild_config(guild_id)
    src = dict(row or {})

    use_env_fallbacks = _safe_bool(src.get("use_env_fallbacks"), True)
    allow_runtime_discovery = _safe_bool(src.get("allow_runtime_discovery"), True)

    def pick_id(key: str) -> Optional[str]:
        db_value = _snowflake_str(src.get(key))
        if db_value:
            return db_value
        if use_env_fallbacks:
            return fallback.get(key)
        return None

    gid = _safe_int(src.get("guild_id"), _fallback_guild_id(guild_id))
    if gid <= 0:
        gid = _fallback_guild_id(guild_id)

    # app.py currently checks source.startswith("supabase:") for configured
    # public startup scope. Keep that compatibility.
    source = f"supabase:{GUILD_CONFIG_TABLE}" if src else "env_fallback"

    return {
        "guild_id": str(gid) if gid > 0 else "",
        "source": source,

        "verify_channel_id": pick_id("verify_channel_id"),
        "vc_verify_channel_id": pick_id("vc_verify_channel_id"),
        "vc_verify_queue_channel_id": pick_id("vc_verify_queue_channel_id"),

        "ticket_category_id": pick_id("ticket_category_id"),
        "transcripts_channel_id": pick_id("transcripts_channel_id"),

        "modlog_channel_id": pick_id("modlog_channel_id"),
        "raidlog_channel_id": pick_id("raidlog_channel_id"),
        "join_log_channel_id": pick_id("join_log_channel_id"),
        "force_verify_log_channel_id": pick_id("force_verify_log_channel_id"),

        "unverified_role_id": pick_id("unverified_role_id"),
        "verified_role_id": pick_id("verified_role_id"),
        "resident_role_id": pick_id("resident_role_id"),
        "staff_role_id": pick_id("staff_role_id"),
        "vc_staff_role_id": pick_id("vc_staff_role_id"),

        "use_env_fallbacks": use_env_fallbacks,
        "allow_runtime_discovery": allow_runtime_discovery,
        "created_at": src.get("created_at"),
        "updated_at": src.get("updated_at"),
        "raw": src,
    }


def _db_get_guild_config_sync(guild_id: Any) -> Dict[str, Any]:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)

    sb = get_supabase()
    if sb is None:
        return env_fallback_guild_config(gid)

    def _read():
        return (
            sb.table(GUILD_CONFIG_TABLE)
            .select("*")
            .eq("guild_id", str(gid))
            .limit(1)
            .execute()
        )

    try:
        res = _execute_db_op(f"read guild_config guild={gid}", _read)
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return _normalize_config_row(dict(rows[0]), gid)
    except Exception as e:
        _debug(f"DB config read failed guild={gid}: {repr(e)}")

    return env_fallback_guild_config(gid)


async def get_guild_config(
    guild_id: Any,
    *,
    force_refresh: bool = False,
    refresh: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Get effective guild config.

    Supports both:
    - force_refresh=True
    - refresh=True

    The refresh alias exists because app.py/startup guards already call it.
    """
    if refresh is not None:
        force_refresh = bool(refresh)

    gid = _fallback_guild_id(guild_id)
    key = _cache_key(gid)

    if not force_refresh and _cache_valid(gid):
        cached = _CONFIG_CACHE.get(key)
        if cached:
            return dict(cached)

    config = await _run_db(
        f"get guild config async guild={gid}",
        lambda: _db_get_guild_config_sync(gid),
    )

    _CONFIG_CACHE[key] = dict(config)
    _CONFIG_CACHE_TS[key] = _now()
    return dict(config)


def _db_upsert_guild_config_sync(guild_id: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)

    sb = get_supabase()
    if sb is None:
        return env_fallback_guild_config(gid)

    allowed_keys = {
        "verify_channel_id",
        "vc_verify_channel_id",
        "vc_verify_queue_channel_id",
        "ticket_category_id",
        "transcripts_channel_id",
        "modlog_channel_id",
        "raidlog_channel_id",
        "join_log_channel_id",
        "force_verify_log_channel_id",
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "staff_role_id",
        "vc_staff_role_id",
        "use_env_fallbacks",
        "allow_runtime_discovery",
    }

    payload: Dict[str, Any] = {
        "guild_id": str(gid),
        "updated_at": _now().isoformat(),
    }

    for key, value in dict(patch or {}).items():
        if key not in allowed_keys:
            continue

        if key in {"use_env_fallbacks", "allow_runtime_discovery"}:
            payload[key] = _safe_bool(value, True)
        else:
            payload[key] = _snowflake_str(value)

    def _write():
        return (
            sb.table(GUILD_CONFIG_TABLE)
            .upsert(payload, on_conflict="guild_id")
            .execute()
        )

    try:
        _execute_db_op(f"upsert guild_config guild={gid}", _write)
    except Exception as e:
        _debug(f"DB config upsert failed guild={gid}: {repr(e)}")

    return _db_get_guild_config_sync(gid)


async def upsert_guild_config(guild_id: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    gid = _fallback_guild_id(guild_id)
    config = await _run_db(
        f"upsert guild config async guild={gid}",
        lambda: _db_upsert_guild_config_sync(gid, patch),
    )

    clear_guild_config_cache(gid)
    _CONFIG_CACHE[_cache_key(gid)] = dict(config)
    _CONFIG_CACHE_TS[_cache_key(gid)] = _now()
    return dict(config)


# ============================================================
# Discord runtime discovery helpers
# ============================================================

def _find_role_by_names(guild: discord.Guild, names: list[str]) -> Optional[discord.Role]:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for role in guild.roles:
            role_name = str(role.name or "").lower().strip()
            if role_name in wanted:
                return role

        for role in guild.roles:
            role_name = str(role.name or "").lower().strip()
            if any(w in role_name for w in wanted):
                return role
    except Exception:
        return None
    return None


def _find_text_channel_by_names(guild: discord.Guild, names: list[str]) -> Optional[discord.TextChannel]:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for ch in guild.text_channels:
            ch_name = str(ch.name or "").lower().strip()
            if ch_name in wanted:
                return ch

        for ch in guild.text_channels:
            ch_name = str(ch.name or "").lower().strip()
            if any(w in ch_name for w in wanted):
                return ch
    except Exception:
        return None
    return None


def _find_category_by_names(guild: discord.Guild, names: list[str]) -> Optional[discord.CategoryChannel]:
    wanted = [n.lower().strip() for n in names if n.strip()]
    try:
        for cat in guild.categories:
            cat_name = str(cat.name or "").lower().strip()
            if cat_name in wanted:
                return cat

        for cat in guild.categories:
            cat_name = str(cat.name or "").lower().strip()
            if any(w in cat_name for w in wanted):
                return cat
    except Exception:
        return None
    return None


async def discover_runtime_guild_config(guild: discord.Guild) -> Dict[str, Any]:
    """
    Best-effort runtime discovery.

    This does NOT create roles/channels. It only discovers obvious existing ones.
    Setup commands can save the result into guild_config if server owners want.
    """
    config = await get_guild_config(guild.id)
    if not _safe_bool(config.get("allow_runtime_discovery"), True):
        return config

    discovered: Dict[str, Any] = {}

    if not config.get("staff_role_id"):
        role = _find_role_by_names(guild, ["staff", "ticket staff", "mod", "moderator", "admin", "support"])
        if role:
            discovered["staff_role_id"] = str(role.id)

    if not config.get("verified_role_id"):
        role = _find_role_by_names(guild, ["verified"])
        if role:
            discovered["verified_role_id"] = str(role.id)

    if not config.get("unverified_role_id"):
        role = _find_role_by_names(guild, ["unverified", "not verified", "pending"])
        if role:
            discovered["unverified_role_id"] = str(role.id)

    if not config.get("resident_role_id"):
        role = _find_role_by_names(guild, ["resident"])
        if role:
            discovered["resident_role_id"] = str(role.id)

    if not config.get("modlog_channel_id"):
        ch = _find_text_channel_by_names(guild, ["mod-log", "modlog", "logs", "staff-log", "staff-logs"])
        if ch:
            discovered["modlog_channel_id"] = str(ch.id)

    if not config.get("transcripts_channel_id"):
        ch = _find_text_channel_by_names(guild, ["transcripts", "ticket-transcripts", "ticket-logs"])
        if ch:
            discovered["transcripts_channel_id"] = str(ch.id)

    if not config.get("verify_channel_id"):
        ch = _find_text_channel_by_names(guild, ["verify", "verification", "vc-verify"])
        if ch:
            discovered["verify_channel_id"] = str(ch.id)

    if not config.get("ticket_category_id"):
        cat = _find_category_by_names(guild, ["tickets", "support", "verification tickets"])
        if cat:
            discovered["ticket_category_id"] = str(cat.id)

    if not discovered:
        return config

    merged = dict(config)
    merged.update({k: v for k, v in discovered.items() if v})
    merged["source"] = f"{config.get('source', 'unknown')}+runtime_discovery"
    return merged


async def save_runtime_discovered_config(guild: discord.Guild) -> Dict[str, Any]:
    discovered = await discover_runtime_guild_config(guild)

    patch = {
        key: discovered.get(key)
        for key in (
            "verify_channel_id",
            "vc_verify_channel_id",
            "vc_verify_queue_channel_id",
            "ticket_category_id",
            "transcripts_channel_id",
            "modlog_channel_id",
            "raidlog_channel_id",
            "join_log_channel_id",
            "force_verify_log_channel_id",
            "unverified_role_id",
            "verified_role_id",
            "resident_role_id",
            "staff_role_id",
            "vc_staff_role_id",
        )
        if discovered.get(key)
    }

    if not patch:
        return discovered

    return await upsert_guild_config(guild.id, patch)


# ============================================================
# Convenience getters
# ============================================================

async def get_config_id(guild_id: Any, key: str, *, default: int = 0) -> int:
    config = await get_guild_config(guild_id)
    return _safe_int(config.get(key), default)


async def get_verify_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "verify_channel_id")


async def get_vc_verify_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "vc_verify_channel_id")


async def get_vc_verify_queue_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "vc_verify_queue_channel_id")


async def get_ticket_category_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "ticket_category_id")


async def get_transcripts_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "transcripts_channel_id")


async def get_modlog_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "modlog_channel_id")


async def get_raidlog_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "raidlog_channel_id")


async def get_join_log_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "join_log_channel_id")


async def get_force_verify_log_channel_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "force_verify_log_channel_id")


async def get_staff_role_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "staff_role_id")


async def get_vc_staff_role_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "vc_staff_role_id")


async def get_verified_role_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "verified_role_id")


async def get_unverified_role_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "unverified_role_id")


async def get_resident_role_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "resident_role_id")


async def config_summary_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    config = await discover_runtime_guild_config(guild)

    return {
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "source": config.get("source"),
        "use_env_fallbacks": _safe_bool(config.get("use_env_fallbacks"), True),
        "allow_runtime_discovery": _safe_bool(config.get("allow_runtime_discovery"), True),

        "verify_channel_id": config.get("verify_channel_id"),
        "vc_verify_channel_id": config.get("vc_verify_channel_id"),
        "vc_verify_queue_channel_id": config.get("vc_verify_queue_channel_id"),

        "ticket_category_id": config.get("ticket_category_id"),
        "transcripts_channel_id": config.get("transcripts_channel_id"),

        "modlog_channel_id": config.get("modlog_channel_id"),
        "raidlog_channel_id": config.get("raidlog_channel_id"),
        "join_log_channel_id": config.get("join_log_channel_id"),
        "force_verify_log_channel_id": config.get("force_verify_log_channel_id"),

        "unverified_role_id": config.get("unverified_role_id"),
        "verified_role_id": config.get("verified_role_id"),
        "resident_role_id": config.get("resident_role_id"),
        "staff_role_id": config.get("staff_role_id"),
        "vc_staff_role_id": config.get("vc_staff_role_id"),
    }
