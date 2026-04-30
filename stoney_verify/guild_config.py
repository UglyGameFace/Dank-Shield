from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

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
# Critical compatibility goals:
# - Old public setup modules import GuildRuntimeConfig,
#   invalidate_guild_config, and guild_config_cache_snapshot.
# - Existing installs may already use public.guild_configs.
# - Newer panel code may try public.guild_config.
# - DB config remains authoritative, .env remains fallback only.
# ============================================================

GUILD_CONFIG_TABLE = (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
GUILD_CONFIG_TABLE_FALLBACKS = tuple(
    dict.fromkeys(
        name
        for name in (
            GUILD_CONFIG_TABLE,
            "guild_configs",
            "guild_config",
        )
        if name
    )
)

_CACHE_TTL_SECONDS = 60
_DB_MAX_ATTEMPTS = 5

_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_CONFIG_CACHE_TS: Dict[str, datetime] = {}


# ============================================================
# Compatibility wrapper
# ============================================================

class GuildRuntimeConfig(dict):
    """Dict-backed runtime config with attribute access.

    Older modules use ``cfg.staff_role_id`` while newer modules use
    ``cfg["staff_role_id"]`` / ``cfg.get(...)``. This supports both without
    forcing another risky refactor across every command module.
    """

    def __getattr__(self, key: str):
        try:
            return self.get(key)
        except Exception:
            return None

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    @property
    def is_unconfigured(self) -> bool:
        try:
            source = str(self.get("source") or "").strip().lower()
            if source.startswith("unconfigured:"):
                return True
            if source.startswith("env_fallback"):
                return True
            return False
        except Exception:
            return True


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


def invalidate_guild_config(guild_id: Optional[Any] = None) -> None:
    clear_guild_config_cache(guild_id)


def invalidate_config_cache(guild_id: Optional[Any] = None) -> None:
    clear_guild_config_cache(guild_id)


def guild_config_cache_snapshot() -> Dict[str, Any]:
    try:
        return {
            "size": len(_CONFIG_CACHE),
            "keys": sorted(_CONFIG_CACHE.keys()),
            "ttl_seconds": _CACHE_TTL_SECONDS,
            "table_order": list(GUILD_CONFIG_TABLE_FALLBACKS),
        }
    except Exception:
        return {"size": 0, "keys": [], "ttl_seconds": _CACHE_TTL_SECONDS}


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


def _is_missing_table_error(error: Exception) -> bool:
    text = repr(error).lower()
    return (
        "pgrst205" in text
        or "could not find the table" in text
        or "schema cache" in text
        or "undefinedtable" in text
        or "relation" in text and "does not exist" in text
    )


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
            if _is_missing_table_error(e):
                raise
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


def _mapping_dict(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def _merge_row_settings(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    raw = _mapping_dict(row)

    for key in ("settings", "config", "metadata", "meta"):
        nested = _mapping_dict(raw.get(key))
        if nested:
            merged.update(nested)

    for key, value in raw.items():
        if key in {"settings", "config", "metadata", "meta"}:
            continue
        if value is not None:
            merged[key] = value

    return merged


# ============================================================
# Default / fallback config
# ============================================================

def env_fallback_guild_config(guild_id: Any = None) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)

    return GuildRuntimeConfig(
        {
            "guild_id": str(gid) if gid > 0 else "",
            "source": "env_fallback",

            "verify_channel_id": _snowflake_str(VERIFY_CHANNEL_ID),
            "vc_verify_channel_id": _snowflake_str(VC_VERIFY_CHANNEL_ID),
            "vc_verify_queue_channel_id": _snowflake_str(VC_VERIFY_QUEUE_CHANNEL_ID),

            "ticket_category_id": _snowflake_str(TICKET_CATEGORY_ID),
            "ticket_archive_category_id": None,
            "transcripts_channel_id": _snowflake_str(TRANSCRIPTS_CHANNEL_ID),
            "ticket_prefix": "ticket",

            "status_channel_id": None,
            "bot_status_channel_id": None,
            "uptime_channel_id": None,
            "health_channel_id": None,

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
    )


def _normalize_config_row(
    row: Optional[Mapping[str, Any]],
    guild_id: Any = None,
    *,
    table_name: Optional[str] = None,
) -> GuildRuntimeConfig:
    fallback = env_fallback_guild_config(guild_id)
    raw = _mapping_dict(row)
    src = _merge_row_settings(raw)

    use_env_fallbacks = _safe_bool(src.get("use_env_fallbacks"), True)
    allow_runtime_discovery = _safe_bool(src.get("allow_runtime_discovery"), True)

    def pick_id(key: str) -> Optional[str]:
        db_value = _snowflake_str(src.get(key))
        if db_value:
            return db_value
        if use_env_fallbacks:
            return fallback.get(key)
        return None

    def pick_text(key: str, default: str = "") -> str:
        text = _safe_str(src.get(key), "")
        if text:
            return text
        return _safe_str(fallback.get(key), default)

    gid = _safe_int(src.get("guild_id"), _fallback_guild_id(guild_id))
    if gid <= 0:
        gid = _fallback_guild_id(guild_id)

    source_table = table_name or _safe_str(raw.get("_source_table"), GUILD_CONFIG_TABLE)
    source = f"supabase:{source_table}" if raw else "env_fallback"

    cfg = GuildRuntimeConfig(
        {
            "guild_id": str(gid) if gid > 0 else "",
            "source": source,

            "verify_channel_id": pick_id("verify_channel_id"),
            "vc_verify_channel_id": pick_id("vc_verify_channel_id"),
            "vc_verify_queue_channel_id": pick_id("vc_verify_queue_channel_id"),

            "ticket_category_id": pick_id("ticket_category_id"),
            "ticket_archive_category_id": pick_id("ticket_archive_category_id"),
            "transcripts_channel_id": pick_id("transcripts_channel_id"),
            "ticket_prefix": pick_text("ticket_prefix", "ticket") or "ticket",

            "status_channel_id": pick_id("status_channel_id"),
            "bot_status_channel_id": pick_id("bot_status_channel_id"),
            "uptime_channel_id": pick_id("uptime_channel_id"),
            "health_channel_id": pick_id("health_channel_id"),

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
            "created_at": src.get("created_at") or raw.get("created_at"),
            "updated_at": src.get("updated_at") or raw.get("updated_at"),
            "raw": raw,
        }
    )

    # Preserve extra saved setup fields so old/new commands do not lose data.
    for key, value in src.items():
        if key not in cfg and value is not None:
            cfg[key] = value

    return cfg


def _db_get_guild_config_sync(guild_id: Any) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)

    sb = get_supabase()
    if sb is None:
        return env_fallback_guild_config(gid)

    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        def _read(table_name: str = table_name):
            return (
                sb.table(table_name)
                .select("*")
                .eq("guild_id", str(gid))
                .limit(1)
                .execute()
            )

        try:
            res = _execute_db_op(f"read {table_name} guild={gid}", _read)
            rows = getattr(res, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return _normalize_config_row(dict(rows[0]), gid, table_name=table_name)
        except Exception as e:
            if not _is_missing_table_error(e):
                _debug(f"DB config read failed table={table_name} guild={gid}: {repr(e)}")
            continue

    return env_fallback_guild_config(gid)


async def get_guild_config(
    guild_id: Any,
    *,
    force_refresh: bool = False,
    refresh: Optional[bool] = None,
) -> GuildRuntimeConfig:
    if refresh is not None:
        force_refresh = bool(refresh)

    gid = _fallback_guild_id(guild_id)
    key = _cache_key(gid)

    if not force_refresh and _cache_valid(gid):
        cached = _CONFIG_CACHE.get(key)
        if cached:
            return GuildRuntimeConfig(cached)

    config = await _run_db(
        f"get guild config async guild={gid}",
        lambda: _db_get_guild_config_sync(gid),
    )

    _CONFIG_CACHE[key] = dict(config)
    _CONFIG_CACHE_TS[key] = _now()
    return GuildRuntimeConfig(config)


def _candidate_write_payloads(guild_id: int, updates: Mapping[str, Any], existing: Optional[Mapping[str, Any]] = None) -> list[Dict[str, Any]]:
    existing_settings = _merge_row_settings(existing)
    settings = dict(existing_settings)
    for key, value in dict(updates or {}).items():
        if value is not None:
            settings[str(key)] = value

    base = {
        "guild_id": str(int(guild_id)),
        "updated_at": _now().isoformat(),
    }

    direct = {**base, **{str(k): v for k, v in dict(updates or {}).items() if v is not None}}
    settings_payload = {**base, "settings": settings}
    config_payload = {**base, "config": settings}

    # Most current public setup code writes `settings`; keep that first.
    return [settings_payload, config_payload, direct]


def _fetch_existing_row_sync(table_name: str, guild_id: int) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return None

    def _read():
        return (
            sb.table(table_name)
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )

    res = _execute_db_op(f"fetch existing {table_name} guild={guild_id}", _read)
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], Mapping):
        return dict(rows[0])
    return None


def _db_upsert_guild_config_sync(guild_id: Any, patch: Mapping[str, Any]) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)

    sb = get_supabase()
    if sb is None:
        return env_fallback_guild_config(gid)

    last_error: Optional[Exception] = None

    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            existing = _fetch_existing_row_sync(table_name, gid)
        except Exception as e:
            last_error = e
            if _is_missing_table_error(e):
                continue
            existing = None

        for payload in _candidate_write_payloads(gid, patch, existing):
            clean_payload = {k: v for k, v in payload.items() if v is not None}

            def _write(table_name: str = table_name, clean_payload: Dict[str, Any] = clean_payload, existing: Optional[Mapping[str, Any]] = existing):
                if existing:
                    return (
                        sb.table(table_name)
                        .update(clean_payload)
                        .eq("guild_id", str(gid))
                        .execute()
                    )
                try:
                    return sb.table(table_name).upsert(clean_payload, on_conflict="guild_id").execute()
                except TypeError:
                    return sb.table(table_name).upsert(clean_payload).execute()

            try:
                _execute_db_op(f"upsert {table_name} guild={gid}", _write)
                refreshed = _fetch_existing_row_sync(table_name, gid)
                if refreshed:
                    return _normalize_config_row(refreshed, gid, table_name=table_name)
                clean_payload["_source_table"] = table_name
                return _normalize_config_row(clean_payload, gid, table_name=table_name)
            except Exception as e:
                last_error = e
                if _is_missing_table_error(e):
                    break
                continue

    if last_error is not None:
        _debug(f"DB config upsert failed guild={gid}: {repr(last_error)}")

    return _db_get_guild_config_sync(gid)


async def upsert_guild_config(guild_id: Any, patch: Mapping[str, Any]) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    config = await _run_db(
        f"upsert guild config async guild={gid}",
        lambda: _db_upsert_guild_config_sync(gid, patch),
    )

    clear_guild_config_cache(gid)
    _CONFIG_CACHE[_cache_key(gid)] = dict(config)
    _CONFIG_CACHE_TS[_cache_key(gid)] = _now()
    return GuildRuntimeConfig(config)


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


async def discover_runtime_guild_config(guild: discord.Guild) -> GuildRuntimeConfig:
    config = await get_guild_config(guild.id)
    if not _safe_bool(config.get("allow_runtime_discovery"), True):
        return config

    discovered: Dict[str, Any] = {}

    if not config.get("staff_role_id"):
        role = _find_role_by_names(guild, ["staff", "ticket staff", "mod", "moderator", "admin", "support", "dickheads"])
        if role:
            discovered["staff_role_id"] = str(role.id)
            discovered["vc_staff_role_id"] = str(role.id)

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
        ch = _find_text_channel_by_names(guild, ["mod-log", "modlog", "logs", "staff-log", "staff-logs", "🗃️mod-log"])
        if ch:
            discovered["modlog_channel_id"] = str(ch.id)

    if not config.get("transcripts_channel_id"):
        ch = _find_text_channel_by_names(guild, ["transcripts", "ticket-transcripts", "ticket-logs"])
        if ch:
            discovered["transcripts_channel_id"] = str(ch.id)

    if not config.get("verify_channel_id"):
        ch = _find_text_channel_by_names(guild, ["verify", "verification", "vc-verify", "unverified-chat"])
        if ch:
            discovered["verify_channel_id"] = str(ch.id)

    if not config.get("ticket_category_id"):
        cat = _find_category_by_names(guild, ["tickets", "support", "verification tickets"])
        if cat:
            discovered["ticket_category_id"] = str(cat.id)

    if not discovered:
        return config

    merged = GuildRuntimeConfig(config)
    merged.update({k: v for k, v in discovered.items() if v})
    merged["source"] = f"{config.get('source', 'unknown')}+runtime_discovery"
    return merged


async def save_runtime_discovered_config(guild: discord.Guild) -> GuildRuntimeConfig:
    discovered = await discover_runtime_guild_config(guild)

    patch = {
        key: discovered.get(key)
        for key in (
            "verify_channel_id",
            "vc_verify_channel_id",
            "vc_verify_queue_channel_id",
            "ticket_category_id",
            "ticket_archive_category_id",
            "transcripts_channel_id",
            "status_channel_id",
            "bot_status_channel_id",
            "uptime_channel_id",
            "health_channel_id",
            "modlog_channel_id",
            "raidlog_channel_id",
            "join_log_channel_id",
            "force_verify_log_channel_id",
            "unverified_role_id",
            "verified_role_id",
            "resident_role_id",
            "staff_role_id",
            "vc_staff_role_id",
            "ticket_prefix",
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


async def get_ticket_archive_category_id(guild_id: Any) -> int:
    return await get_config_id(guild_id, "ticket_archive_category_id")


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
        "ticket_archive_category_id": config.get("ticket_archive_category_id"),
        "transcripts_channel_id": config.get("transcripts_channel_id"),

        "status_channel_id": config.get("status_channel_id"),
        "bot_status_channel_id": config.get("bot_status_channel_id"),
        "uptime_channel_id": config.get("uptime_channel_id"),
        "health_channel_id": config.get("health_channel_id"),

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


__all__ = [
    "GuildRuntimeConfig",
    "GUILD_CONFIG_TABLE",
    "GUILD_CONFIG_TABLE_FALLBACKS",
    "clear_guild_config_cache",
    "invalidate_guild_config",
    "invalidate_config_cache",
    "guild_config_cache_snapshot",
    "env_fallback_guild_config",
    "get_guild_config",
    "upsert_guild_config",
    "discover_runtime_guild_config",
    "save_runtime_discovered_config",
    "config_summary_for_guild",
    "get_config_id",
    "get_verify_channel_id",
    "get_vc_verify_channel_id",
    "get_vc_verify_queue_channel_id",
    "get_ticket_category_id",
    "get_ticket_archive_category_id",
    "get_transcripts_channel_id",
    "get_modlog_channel_id",
    "get_raidlog_channel_id",
    "get_join_log_channel_id",
    "get_force_verify_log_channel_id",
    "get_staff_role_id",
    "get_vc_staff_role_id",
    "get_verified_role_id",
    "get_unverified_role_id",
    "get_resident_role_id",
]
