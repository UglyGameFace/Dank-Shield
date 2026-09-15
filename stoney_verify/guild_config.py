from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

import discord

from .globals import (
    get_supabase,
    reset_supabase,
    now_utc,
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


# Canonical per-server configuration owner.
#
# Reads, writes, cache behavior, public-server isolation, runtime Discord-ID
# validation, and storage-shape compatibility live here. Feature modules may
# keep compatibility facades, but they must not implement a second persistence
# engine or mutate this module at import time.

GUILD_CONFIG_TABLE = (os.getenv("DANK_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
GUILD_CONFIG_TABLE_FALLBACKS = tuple(
    dict.fromkeys(
        name
        for name in (GUILD_CONFIG_TABLE, "guild_configs", "guild_config")
        if name
    )
)

_CACHE_TTL_SECONDS = 60
_DB_MAX_ATTEMPTS = 5

_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_CONFIG_CACHE_TS: Dict[str, datetime] = {}

_JSON_CONFIG_KEYS = {"settings", "config", "metadata", "meta"}
_BASE_WRITE_KEYS = {"guild_id", "updated_at", "created_at"}
_CONTROL_KEYS = {
    "__config_write_mode",
    "__config_write_source",
    "__config_write_reason",
    "__config_write_actor_id",
    "__config_write_allow_keys",
    "__config_write_dry_run",
    "__config_write_invalidate_completion",
}
_OVERWRITE_MODES = {"setup_builder", "explicit_override", "force"}
_FILL_ONLY_MODES = {"fill_missing", "runtime_discovery", "auto_discover", "auto_create"}
_ALLOWED_MODES = _OVERWRITE_MODES | _FILL_ONLY_MODES
_PROTECTED_CONFIG_KEYS = {
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "member_role_id",
    "staff_role_id",
    "vc_staff_role_id",
    "server_control_role_id",
    "control_role_id",
    "perm_role_id",
    "bot_manager_role_id",
    "verify_channel_id",
    "vc_verify_channel_id",
    "vc_verify_queue_channel_id",
    "welcome_channel_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "ticket_closed_category_id",
    "ticket_panel_channel_id",
    "support_channel_id",
    "start_category_id",
    "welcome_category_id",
    "management_category_id",
    "staff_tools_category_id",
    "transcripts_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "raid_log_channel_id",
    "join_log_channel_id",
    "join_exit_log_channel_id",
    "force_verify_log_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
    "ticket_prefix",
    "verify_kick_hours",
    "use_env_fallbacks",
    "allow_runtime_discovery",
}
_COMPLETION_METADATA_KEYS = {
    "setup_completed",
    "setup_completed_at",
    "setup_completed_by_id",
    "setup_completed_by_name",
    "setup_completion_invalidated_at",
    "setup_completion_invalidated_reason",
}

_ROLE_KEYS: tuple[str, ...] = (
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "staff_role_id",
    "vc_staff_role_id",
)
_TEXT_CHANNEL_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "vc_verify_queue_channel_id",
    "transcripts_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
    "force_verify_log_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
)
_VOICE_CHANNEL_KEYS: tuple[str, ...] = ("vc_verify_channel_id",)
_CATEGORY_KEYS: tuple[str, ...] = ("ticket_category_id", "ticket_archive_category_id")


class GuildRuntimeConfig(dict):
    """Dict-backed runtime config with attribute access."""

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
            return bool(
                source.startswith("unconfigured:")
                or source.startswith("unavailable:")
                or source.startswith("env_fallback")
            )
        except Exception:
            return True


def _debug(message: str) -> None:
    try:
        print(f"🧩 guild_config {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_config {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
    return bool(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _snowflake_str(value: Any) -> Optional[str]:
    num = _safe_int(value, 0)
    return str(num) if num > 0 else None


def _now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _cache_key(guild_id: Any) -> str:
    return str(_safe_int(guild_id, 0))


def public_config_isolation_enabled() -> bool:
    try:
        explicit = os.getenv("DANK_PUBLIC_CONFIG_ISOLATION")
        if explicit is not None and str(explicit).strip():
            return _safe_bool(explicit, True)

        deployment = str(os.getenv("DANK_DEPLOYMENT_MODE") or "").strip().lower()
        if deployment in {"public", "prod", "production"}:
            return True
        if _safe_bool(os.getenv("DANK_PUBLIC_MODE"), False):
            return True
        if _safe_bool(os.getenv("DANK_PRODUCTION_MODE"), False):
            return True
        return not _safe_bool(os.getenv("DANK_DISABLE_PUBLIC_CONFIG_ISOLATION"), False)
    except Exception:
        return True


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
        keys = sorted(_CONFIG_CACHE.keys())
        return {
            "size": len(_CONFIG_CACHE),
            "keys": keys,
            "cached_guilds": len(keys),
            "ttl_seconds": _CACHE_TTL_SECONDS,
            "table": GUILD_CONFIG_TABLE,
            "table_order": list(GUILD_CONFIG_TABLE_FALLBACKS),
            "public_config_isolation": public_config_isolation_enabled(),
        }
    except Exception:
        return {
            "size": 0,
            "keys": [],
            "cached_guilds": 0,
            "ttl_seconds": _CACHE_TTL_SECONDS,
            "table": GUILD_CONFIG_TABLE,
            "public_config_isolation": True,
        }


def _cache_valid(guild_id: Any) -> bool:
    ts = _CONFIG_CACHE_TS.get(_cache_key(guild_id))
    if ts is None:
        return False
    try:
        return (_now() - ts).total_seconds() <= _CACHE_TTL_SECONDS
    except Exception:
        return False


def _fallback_guild_id(guild_id: Any = None) -> int:
    explicit = _safe_int(guild_id, 0)
    return explicit if explicit > 0 else _safe_int(GUILD_ID, 0)


def _allow_env_fallback_for_guild(guild_id: Any) -> bool:
    if not public_config_isolation_enabled():
        return True
    gid = _safe_int(guild_id, 0)
    home_gid = _safe_int(GUILD_ID, 0)
    if home_gid <= 0:
        return False
    return gid == home_gid


def env_fallback_allowed_for_guild(guild_id: Any) -> bool:
    """Public compatibility wrapper for per-guild env fallback policy."""
    return _allow_env_fallback_for_guild(guild_id)


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
    return bool(
        "pgrst205" in text
        or "could not find the table" in text
        or "schema cache" in text
        or "undefinedtable" in text
        or ("relation" in text and "does not exist" in text)
    )


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    time.sleep(base + random.uniform(0.05, 0.25))


def _execute_db_op(op_name: str, executor, max_attempts: int = _DB_MAX_ATTEMPTS):
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as exc:
            last_error = exc
            if _is_missing_table_error(exc):
                raise
            if _is_retryable_db_error(exc) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                _warn(f"{op_name}: transient DB error {attempt}/{max_attempts}: {exc!r}")
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
        return dict(value) if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def _merge_row_settings(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    raw = _mapping_dict(row)
    for key in ("settings", "config", "metadata", "meta"):
        nested = _mapping_dict(raw.get(key))
        if nested:
            merged.update(nested)
    for key, value in raw.items():
        if key not in _JSON_CONFIG_KEYS and value is not None:
            merged[key] = value
    return merged


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


def _fallback_config_for_read_state(guild_id: Any, *, source: str) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    cfg = env_fallback_guild_config(gid)
    if not _allow_env_fallback_for_guild(gid):
        for key in list(cfg.keys()):
            if key != "guild_id" and key.endswith("_id"):
                cfg[key] = None
        cfg["use_env_fallbacks"] = False
    cfg["source"] = str(source or "unavailable:unknown")
    return cfg


def get_cached_guild_config(guild_id: Any) -> GuildRuntimeConfig:
    """Return the last resolved config without performing database I/O.

    Cache consumers such as ticket sync call this only after an async refresh.
    On a cache miss, return the same isolation-safe fallback shape rather than
    raising or leaking another guild's deployment-level Discord IDs.
    """
    gid = _fallback_guild_id(guild_id)
    cached = _CONFIG_CACHE.get(_cache_key(gid))
    if cached:
        return GuildRuntimeConfig(cached)
    if _allow_env_fallback_for_guild(gid):
        return env_fallback_guild_config(gid)
    return _fallback_config_for_read_state(gid, source="unconfigured:cache_miss")


def _normalize_config_row(
    row: Optional[Mapping[str, Any]],
    guild_id: Any = None,
    *,
    table_name: Optional[str] = None,
) -> GuildRuntimeConfig:
    fallback = env_fallback_guild_config(guild_id)
    raw = _mapping_dict(row)
    src = _merge_row_settings(raw)
    db_allows_env = _safe_bool(src.get("use_env_fallbacks"), True)
    use_env_fallbacks = bool(db_allows_env and _allow_env_fallback_for_guild(guild_id))
    allow_runtime_discovery = _safe_bool(src.get("allow_runtime_discovery"), True)

    def pick_id(key: str) -> Optional[str]:
        db_value = _snowflake_str(src.get(key))
        if db_value:
            return db_value
        return fallback.get(key) if use_env_fallbacks else None

    def pick_text(key: str, default: str = "") -> str:
        text = _safe_str(src.get(key), "")
        if text:
            return text
        return _safe_str(fallback.get(key), default) if use_env_fallbacks else default

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
    for key, value in src.items():
        if key not in cfg and value is not None:
            cfg[key] = value
    return cfg


def _fetch_existing_row_sync(table_name: str, guild_id: int) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return None

    def _read():
        return sb.table(table_name).select("*").eq("guild_id", str(int(guild_id))).limit(1).execute()

    res = _execute_db_op(f"fetch existing {table_name} guild={guild_id}", _read)
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], Mapping):
        return dict(rows[0])
    return None


def _db_get_guild_config_sync(guild_id: Any) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)
    sb = get_supabase()
    if sb is None:
        return _fallback_config_for_read_state(gid, source="unavailable:supabase_none")

    read_failed = False
    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            existing = _fetch_existing_row_sync(table_name, gid)
            if existing:
                return _normalize_config_row(existing, gid, table_name=table_name)
        except Exception as exc:
            if not _is_missing_table_error(exc):
                read_failed = True
                _debug(f"DB config read failed table={table_name} guild={gid}: {exc!r}")
            continue

    if read_failed:
        return _fallback_config_for_read_state(gid, source="unavailable:db_read_failed")
    if _allow_env_fallback_for_guild(gid):
        return env_fallback_guild_config(gid)
    return _fallback_config_for_read_state(gid, source="unconfigured:isolated_public_fallback")


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

    config = await _run_db(f"get guild config async guild={gid}", lambda: _db_get_guild_config_sync(gid))
    source = _safe_str(config.get("source"), "unknown").lower()
    if source.startswith("unavailable:"):
        cached = _CONFIG_CACHE.get(key)
        if cached:
            _debug(f"DB config unavailable guild={gid}; preserving stale cached config")
            return GuildRuntimeConfig(cached)
        return GuildRuntimeConfig(config)

    _CONFIG_CACHE[key] = dict(config)
    _CONFIG_CACHE_TS[key] = _now()
    return GuildRuntimeConfig(config)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    text = str(value).strip()
    return text == "" or text == "0" or text.lower() in {"none", "null"}


def _normalize_write_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key.endswith("_id"):
        try:
            number = int(str(value).strip())
            return str(number) if number > 0 else None
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def _same_value(left: Any, right: Any) -> bool:
    if _is_empty(left) and _is_empty(right):
        return True
    return str(left).strip() == str(right).strip()


def _is_protected_key(key: str) -> bool:
    return bool(
        key in _PROTECTED_CONFIG_KEYS
        or key.endswith("_role_id")
        or key.endswith("_channel_id")
        or key.endswith("_category_id")
    )


def _read_row_value(row: Optional[Mapping[str, Any]], key: str) -> Any:
    if not isinstance(row, Mapping):
        return None
    if key in row and row.get(key) is not None:
        return row.get(key)
    for json_key in ("settings", "config", "metadata", "meta"):
        nested = row.get(json_key)
        if isinstance(nested, Mapping) and key in nested:
            return nested.get(key)
    return None


def _write_mode(patch: Mapping[str, Any]) -> str:
    mode = _safe_str(patch.get("__config_write_mode"), "").lower()
    return mode if mode in _ALLOWED_MODES else "explicit_override"


def _write_source(patch: Mapping[str, Any]) -> str:
    return _safe_str(patch.get("__config_write_source"), "guild_config")[:300]


def _allowed_write_keys(patch: Mapping[str, Any]) -> set[str]:
    raw = patch.get("__config_write_allow_keys")
    try:
        if isinstance(raw, str):
            return {part.strip() for part in raw.split(",") if part.strip()}
        if isinstance(raw, (list, tuple, set)):
            return {str(part).strip() for part in raw if str(part).strip()}
    except Exception:
        pass
    return set()


def _completion_aware_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    final = dict(patch)
    if not _safe_bool(final.get("__config_write_invalidate_completion"), False):
        return final
    if "setup_completed" in final:
        return final
    functional_keys = [
        str(key)
        for key in final
        if str(key) not in _CONTROL_KEYS
        and str(key) not in _BASE_WRITE_KEYS
        and str(key) not in _COMPLETION_METADATA_KEYS
        and not str(key).startswith("config_last_")
    ]
    if functional_keys:
        final["setup_completed"] = False
        final["setup_completion_invalidated_at"] = _now().isoformat()
    return final


def _filter_safe_updates(
    existing: Optional[Mapping[str, Any]],
    patch: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], str, str]:
    mode = _write_mode(patch)
    source = _write_source(patch)
    allow_keys = _allowed_write_keys(patch)
    clean: dict[str, Any] = {}
    blocked: list[str] = []
    changed: list[str] = []

    for raw_key, raw_value in dict(patch).items():
        key = str(raw_key)
        if key in _CONTROL_KEYS:
            continue
        value = _normalize_write_value(key, raw_value)
        if value is None:
            continue
        if not _is_protected_key(key):
            clean[key] = value
            continue

        old_value = _read_row_value(existing, key)
        if _is_empty(old_value):
            clean[key] = value
            changed.append(f"{key}=set")
            continue
        if _same_value(old_value, value):
            clean[key] = value
            continue
        if mode in _OVERWRITE_MODES or key in allow_keys:
            clean[key] = value
            changed.append(f"{key}: {old_value} -> {value}")
            continue
        blocked.append(f"{key}: kept existing {old_value}, blocked attempted {value}")

    if clean:
        clean.setdefault("config_last_write_mode", mode)
        clean.setdefault("config_last_write_source", source)
        clean.setdefault("config_last_write_at", _now().isoformat())
        if blocked:
            clean.setdefault("config_last_blocked_overwrite", " | ".join(blocked)[:1000])
    if blocked:
        _warn(f"blocked config overwrite mode={mode} source={source} blocked={blocked[:8]}")
    return clean, blocked, changed, mode, source


def _settings_payload_update(
    original: Optional[Mapping[str, Any]],
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if isinstance(original, Mapping):
        for key in ("settings", "config", "metadata", "meta"):
            nested = original.get(key)
            if isinstance(nested, Mapping):
                for nested_key, nested_value in nested.items():
                    if nested_key not in _CONTROL_KEYS and nested_value is not None:
                        base[str(nested_key)] = nested_value
        for key, value in original.items():
            if key not in _JSON_CONFIG_KEYS and key not in _CONTROL_KEYS and value is not None:
                base[str(key)] = value
    for key, value in dict(updates).items():
        if key not in _CONTROL_KEYS and value is not None:
            base[str(key)] = value
    return base


def _known_flat_payload(
    existing: Optional[Mapping[str, Any]],
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(existing, Mapping):
        return {}
    columns = {str(key) for key in existing.keys()}
    return {
        str(key): value
        for key, value in dict(updates).items()
        if str(key) in columns
        and str(key) not in _JSON_CONFIG_KEYS
        and str(key) not in _BASE_WRITE_KEYS
        and str(key) not in _CONTROL_KEYS
    }


def _candidate_write_payloads(
    guild_id: int,
    updates: Mapping[str, Any],
    existing: Optional[Mapping[str, Any]] = None,
) -> list[Dict[str, Any]]:
    settings = _settings_payload_update(existing, updates)
    flat_updates = _known_flat_payload(existing, updates)
    base = {"guild_id": str(int(guild_id)), "updated_at": _now().isoformat()}

    json_keys = ("settings", "config", "metadata", "meta")
    json_updates: dict[str, Any] = {}
    if isinstance(existing, Mapping):
        for json_key in json_keys:
            if json_key in existing:
                json_updates[json_key] = settings
    else:
        json_updates["settings"] = settings
        json_updates["config"] = settings

    direct_updates = {
        str(key): value
        for key, value in dict(updates).items()
        if str(key) not in _CONTROL_KEYS and value is not None
    }
    candidates: list[Dict[str, Any]] = [
        {**base, **json_updates, **flat_updates},
    ]
    for json_key in json_keys:
        if json_key in json_updates:
            candidates.append({**base, json_key: settings, **flat_updates})
    if flat_updates:
        candidates.append({**base, **flat_updates})
    candidates.append({**base, **direct_updates})

    unique: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for payload in candidates:
        marker = repr(payload)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(payload)
    return unique


def _db_upsert_guild_config_sync(guild_id: Any, patch: Mapping[str, Any]) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)
    sb = get_supabase()
    if sb is None:
        return _fallback_config_for_read_state(gid, source="unavailable:supabase_none")

    normalized_patch = _completion_aware_patch(patch)
    last_error: Optional[Exception] = None

    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            existing = _fetch_existing_row_sync(table_name, gid)
        except Exception as exc:
            last_error = exc
            if _is_missing_table_error(exc):
                continue
            existing = None

        safe_updates, blocked, changed, mode, source = _filter_safe_updates(existing, normalized_patch)
        if _safe_bool(normalized_patch.get("__config_write_dry_run"), False):
            preview = _settings_payload_update(existing, safe_updates)
            preview["_dry_run"] = True
            preview["_blocked_overwrites"] = blocked
            preview["_allowed_changes"] = changed
            preview["_config_write_mode"] = mode
            preview["_config_write_source"] = source
            return GuildRuntimeConfig(preview)
        if not safe_updates:
            if existing:
                return _normalize_config_row(existing, gid, table_name=table_name)
            continue

        for payload in _candidate_write_payloads(gid, safe_updates, existing):
            clean_payload = {
                key: value
                for key, value in payload.items()
                if value is not None and key not in _CONTROL_KEYS
            }
            if not clean_payload:
                continue

            def _write(
                table_name: str = table_name,
                clean_payload: Dict[str, Any] = clean_payload,
                existing: Optional[Mapping[str, Any]] = existing,
            ):
                if existing:
                    return sb.table(table_name).update(clean_payload).eq("guild_id", str(gid)).execute()
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
            except Exception as exc:
                last_error = exc
                if _is_missing_table_error(exc):
                    break
                continue

    if last_error is not None:
        _debug(f"DB config upsert failed guild={gid}: {last_error!r}")
    return _db_get_guild_config_sync(gid)


def upsert_guild_config_sync(guild_id: Any, patch: Mapping[str, Any]) -> GuildRuntimeConfig:
    config = _db_upsert_guild_config_sync(guild_id, patch)
    gid = _fallback_guild_id(guild_id)
    source = _safe_str(config.get("source"), "unknown").lower()
    if gid > 0 and not source.startswith("unavailable:"):
        clear_guild_config_cache(gid)
        _CONFIG_CACHE[_cache_key(gid)] = dict(config)
        _CONFIG_CACHE_TS[_cache_key(gid)] = _now()
    return GuildRuntimeConfig(config)


async def upsert_guild_config(guild_id: Any, patch: Mapping[str, Any]) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    key = _cache_key(gid)
    previous = _CONFIG_CACHE.get(key)
    config = await _run_db(
        f"upsert guild config async guild={gid}",
        lambda: _db_upsert_guild_config_sync(gid, patch),
    )
    source = _safe_str(config.get("source"), "unknown").lower()
    if source.startswith("unavailable:"):
        if previous:
            _debug(f"DB config write unavailable guild={gid}; preserving prior cached config")
            return GuildRuntimeConfig(previous)
        return GuildRuntimeConfig(config)

    clear_guild_config_cache(gid)
    _CONFIG_CACHE[key] = dict(config)
    _CONFIG_CACHE_TS[key] = _now()
    return GuildRuntimeConfig(config)


def _settings_payload_without_keys(
    original: Optional[Mapping[str, Any]],
    clear_keys: Iterable[str],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    settings = _settings_payload_update(original, {})
    for key in {str(item).strip() for item in clear_keys if str(item).strip()}:
        settings.pop(key, None)
    for key, value in dict(metadata).items():
        if value is not None:
            settings[str(key)] = value
    return settings


def clear_guild_config_keys_sync(
    guild_id: Any,
    keys: Iterable[str],
    *,
    source: str = "guild config reconciliation",
    actor: Any = None,
) -> GuildRuntimeConfig:
    gid = _fallback_guild_id(guild_id)
    if gid <= 0:
        return env_fallback_guild_config(guild_id)
    sb = get_supabase()
    if sb is None:
        return _fallback_config_for_read_state(gid, source="unavailable:supabase_none")

    clear_keys = {
        str(key).strip()
        for key in keys
        if str(key).strip()
        and str(key).strip() not in _CONTROL_KEYS
        and str(key).strip() not in _BASE_WRITE_KEYS
        and str(key).strip() not in _JSON_CONFIG_KEYS
    }
    if not clear_keys:
        return _db_get_guild_config_sync(gid)

    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        try:
            existing = _fetch_existing_row_sync(table_name, gid)
        except Exception as exc:
            if _is_missing_table_error(exc):
                continue
            raise
        if not existing:
            continue

        stamp = _now().isoformat()
        metadata: dict[str, Any] = {
            "config_last_write_mode": "explicit_override",
            "config_last_write_source": str(source or "guild config reconciliation")[:300],
            "config_last_write_at": stamp,
        }
        source_l = str(source or "").lower()
        if "setup" in source_l:
            metadata["setup_completed"] = False
            metadata["setup_completion_invalidated_at"] = stamp
        if actor is not None:
            metadata["configured_by_id"] = str(getattr(actor, "id", "") or "")
            metadata["configured_by_name"] = str(actor)

        settings = _settings_payload_without_keys(existing, clear_keys, metadata)
        columns = {str(key) for key in existing.keys()}
        flat_clear = {key: None for key in clear_keys if key in columns}
        flat_metadata = {key: value for key, value in metadata.items() if key in columns}
        json_updates: dict[str, Any] = {}
        for json_key in ("settings", "config", "metadata", "meta"):
            if json_key in columns:
                json_updates[json_key] = settings
        payload = {**json_updates, **flat_clear, **flat_metadata, "updated_at": stamp}

        def _write():
            return sb.table(table_name).update(payload).eq("guild_id", str(gid)).execute()

        _execute_db_op(f"clear config keys {table_name} guild={gid}", _write)
        clear_guild_config_cache(gid)
        refreshed = _fetch_existing_row_sync(table_name, gid)
        if refreshed:
            config = _normalize_config_row(refreshed, gid, table_name=table_name)
            _CONFIG_CACHE[_cache_key(gid)] = dict(config)
            _CONFIG_CACHE_TS[_cache_key(gid)] = _now()
            return config
        return _normalize_config_row(payload, gid, table_name=table_name)

    return _db_get_guild_config_sync(gid)


async def clear_guild_config_keys(
    guild_id: Any,
    keys: Iterable[str],
    *,
    source: str = "guild config reconciliation",
    actor: Any = None,
) -> GuildRuntimeConfig:
    return await asyncio.to_thread(
        clear_guild_config_keys_sync,
        guild_id,
        tuple(keys),
        source=source,
        actor=actor,
    )


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if stage is not None:
        items.append(stage)
    return tuple(items)


def _valid_channel(guild: discord.Guild, resource_id: int, expected: str) -> bool:
    try:
        channel = guild.get_channel(int(resource_id))
    except Exception:
        channel = None
    if channel is None:
        return False
    if expected == "text":
        return isinstance(channel, discord.TextChannel)
    if expected == "voice":
        return isinstance(channel, _voice_types())
    if expected == "category":
        return isinstance(channel, discord.CategoryChannel)
    return False


def _invalid_saved_ids(guild: discord.Guild, config: Mapping[str, Any]) -> dict[str, str]:
    invalid: dict[str, str] = {}
    for key in _ROLE_KEYS:
        resource_id = _safe_int(config.get(key), 0)
        if resource_id > 0 and guild.get_role(resource_id) is None:
            invalid[key] = str(resource_id)
    for key in _TEXT_CHANNEL_KEYS:
        resource_id = _safe_int(config.get(key), 0)
        if resource_id > 0 and not _valid_channel(guild, resource_id, "text"):
            invalid[key] = str(resource_id)
    for key in _VOICE_CHANNEL_KEYS:
        resource_id = _safe_int(config.get(key), 0)
        if resource_id > 0 and not _valid_channel(guild, resource_id, "voice"):
            invalid[key] = str(resource_id)
    for key in _CATEGORY_KEYS:
        resource_id = _safe_int(config.get(key), 0)
        if resource_id > 0 and not _valid_channel(guild, resource_id, "category"):
            invalid[key] = str(resource_id)
    return invalid


def _find_role_by_names(guild: discord.Guild, names: list[str]) -> Optional[discord.Role]:
    wanted = [name.lower().strip() for name in names if name.strip()]
    try:
        for role in guild.roles:
            role_name = str(role.name or "").lower().strip()
            if role_name in wanted:
                return role
        for role in guild.roles:
            role_name = str(role.name or "").lower().strip()
            if any(name in role_name for name in wanted):
                return role
    except Exception:
        pass
    return None


def _find_text_channel_by_names(
    guild: discord.Guild,
    names: list[str],
) -> Optional[discord.TextChannel]:
    wanted = [name.lower().strip() for name in names if name.strip()]
    try:
        for channel in guild.text_channels:
            channel_name = str(channel.name or "").lower().strip()
            if channel_name in wanted:
                return channel
        for channel in guild.text_channels:
            channel_name = str(channel.name or "").lower().strip()
            if any(name in channel_name for name in wanted):
                return channel
    except Exception:
        pass
    return None


def _find_category_by_names(
    guild: discord.Guild,
    names: list[str],
) -> Optional[discord.CategoryChannel]:
    wanted = [name.lower().strip() for name in names if name.strip()]
    try:
        for category in guild.categories:
            category_name = str(category.name or "").lower().strip()
            if category_name in wanted:
                return category
        for category in guild.categories:
            category_name = str(category.name or "").lower().strip()
            if any(name in category_name for name in wanted):
                return category
    except Exception:
        pass
    return None


def _apply_runtime_discovery(guild: discord.Guild, cfg: dict[str, Any]) -> dict[str, Any]:
    discovered: dict[str, Any] = {}
    if not cfg.get("staff_role_id"):
        role = _find_role_by_names(guild, ["staff", "ticket staff", "mod", "moderator", "admin", "support"])
        if role:
            discovered["staff_role_id"] = str(role.id)
            discovered["vc_staff_role_id"] = str(role.id)
    if not cfg.get("verified_role_id"):
        role = _find_role_by_names(guild, ["verified", "member", "resident"])
        if role:
            discovered["verified_role_id"] = str(role.id)
    if not cfg.get("unverified_role_id"):
        role = _find_role_by_names(guild, ["unverified", "not verified", "pending"])
        if role:
            discovered["unverified_role_id"] = str(role.id)
    if not cfg.get("resident_role_id"):
        role = _find_role_by_names(guild, ["resident"])
        if role:
            discovered["resident_role_id"] = str(role.id)
    if not cfg.get("modlog_channel_id"):
        channel = _find_text_channel_by_names(guild, ["mod-log", "modlog", "logs", "staff-log", "staff-logs"])
        if channel:
            discovered["modlog_channel_id"] = str(channel.id)
    if not cfg.get("transcripts_channel_id"):
        channel = _find_text_channel_by_names(guild, ["transcripts", "ticket-transcripts", "ticket-logs"])
        if channel:
            discovered["transcripts_channel_id"] = str(channel.id)
    if not cfg.get("verify_channel_id"):
        channel = _find_text_channel_by_names(guild, ["verify", "verification", "unverified-chat"])
        if channel:
            discovered["verify_channel_id"] = str(channel.id)
    if not cfg.get("ticket_category_id"):
        category = _find_category_by_names(guild, ["tickets", "support", "verification tickets"])
        if category:
            discovered["ticket_category_id"] = str(category.id)
    if discovered:
        cfg.update(discovered)
        cfg["runtime_discovered_fields"] = sorted(discovered.keys())
    return cfg


async def discover_runtime_guild_config(guild: discord.Guild) -> GuildRuntimeConfig:
    config = await get_guild_config(guild.id)
    cfg = dict(config)
    invalid = _invalid_saved_ids(guild, cfg)
    if invalid:
        for key in invalid:
            cfg[key] = None
        cfg["invalid_saved_config_ids"] = dict(invalid)
        cfg["source"] = f"{cfg.get('source', 'unknown')}+validated_invalid_ids"
        try:
            await clear_guild_config_keys(
                guild.id,
                invalid.keys(),
                source="runtime guild config validation",
            )
        except Exception as exc:
            _warn(f"failed purging invalid saved IDs guild={guild.id}: {exc!r}")

    if _safe_bool(cfg.get("allow_runtime_discovery"), True):
        cfg = _apply_runtime_discovery(guild, cfg)
        if cfg.get("runtime_discovered_fields"):
            cfg["source"] = f"{cfg.get('source', 'unknown')}+runtime_discovery"
    return GuildRuntimeConfig(cfg)


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
    patch["__config_write_mode"] = "runtime_discovery"
    patch["__config_write_source"] = "guild_config.save_runtime_discovered_config"
    return await upsert_guild_config(guild.id, patch)


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
    "public_config_isolation_enabled",
    "env_fallback_allowed_for_guild",
    "clear_guild_config_cache",
    "invalidate_guild_config",
    "invalidate_config_cache",
    "guild_config_cache_snapshot",
    "env_fallback_guild_config",
    "get_cached_guild_config",
    "get_guild_config",
    "upsert_guild_config_sync",
    "upsert_guild_config",
    "clear_guild_config_keys_sync",
    "clear_guild_config_keys",
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
