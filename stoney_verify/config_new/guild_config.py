from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import discord

from .. import globals as app_globals
from ..globals import bot, get_supabase, reset_supabase


@dataclass(frozen=True)
class GuildConfig:
    guild_id: int
    raw: Dict[str, Any]
    source: str = "default"

    def get_int(self, *keys: str, default: int = 0) -> int:
        for key in keys:
            value = self.raw.get(key)
            parsed = _safe_int(value, 0)
            if parsed > 0:
                return parsed
        return default

    def get_str(self, *keys: str, default: str = "") -> str:
        for key in keys:
            value = _safe_str(self.raw.get(key)).strip()
            if value:
                return value
        return default

    def get_bool(self, *keys: str, default: bool = False) -> bool:
        for key in keys:
            if key in self.raw:
                return _safe_bool(self.raw.get(key), default)
        return default


_CONFIG_CACHE: Dict[int, Tuple[float, GuildConfig]] = {}
_CONFIG_LOCKS: Dict[int, asyncio.Lock] = {}

_DEFAULT_CACHE_TTL_SECONDS = 60.0
_DEFAULT_TABLE_NAME = "guild_configs"


# ============================================================
# Basic helpers
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


def _cache_ttl_seconds() -> float:
    value = _safe_int(os.getenv("STONEY_GUILD_CONFIG_CACHE_TTL_SECONDS", ""), 0)
    if value <= 0:
        try:
            value = _safe_int(getattr(app_globals, "STONEY_GUILD_CONFIG_CACHE_TTL_SECONDS", 0), 0)
        except Exception:
            value = 0
    if value <= 0:
        return _DEFAULT_CACHE_TTL_SECONDS
    return float(max(5, min(value, 3600)))


def _table_name() -> str:
    raw = os.getenv("STONEY_GUILD_CONFIG_TABLE", "").strip()
    if raw:
        return raw
    try:
        raw = str(getattr(app_globals, "STONEY_GUILD_CONFIG_TABLE", "") or "").strip()
        if raw:
            return raw
    except Exception:
        pass
    return _DEFAULT_TABLE_NAME


def _configured_owner_guild_id() -> int:
    """
    Owner/home guild compatibility guard.

    Legacy single-server env globals are allowed only for this guild. This keeps
    the existing Stoney Balonney server working during migration while blocking
    those same IDs from leaking into customer/test guilds.

    Preferred production env:
      STONEY_OWNER_GUILD_ID=1357215261001912320

    Fallbacks are accepted only for compatibility.
    """
    for key in (
        "STONEY_OWNER_GUILD_ID",
        "STONEY_HOME_GUILD_ID",
        "OWNER_GUILD_ID",
        "HOME_GUILD_ID",
    ):
        value = _safe_int(os.getenv(key, ""), 0)
        if value > 0:
            return value

    try:
        value = _safe_int(getattr(app_globals, "STONEY_OWNER_GUILD_ID", 0), 0)
        if value > 0:
            return value
    except Exception:
        pass

    try:
        value = _safe_int(getattr(app_globals, "GUILD_ID", 0), 0)
        if value > 0:
            return value
    except Exception:
        pass

    value = _safe_int(os.getenv("GUILD_ID", ""), 0)
    if value > 0:
        return value

    return 0


def _legacy_globals_allowed_for_guild(guild_id: int) -> bool:
    gid = _safe_int(guild_id, 0)
    owner_gid = _configured_owner_guild_id()
    return gid > 0 and owner_gid > 0 and gid == owner_gid


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


def _env_guild_override_int(base_name: str, guild_id: int, default: int = 0) -> int:
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return default

    for key in (
        f"{base_name}_{gid}",
        f"{base_name}__{gid}",
        f"GUILD_{gid}_{base_name}",
    ):
        value = _safe_int(os.getenv(key, ""), 0)
        if value > 0:
            return value

    return default


def _legacy_global_int(name: str, default: int = 0) -> int:
    try:
        value = getattr(app_globals, name, None)
        parsed = _safe_int(value, 0)
        if parsed > 0:
            return parsed
    except Exception:
        pass

    try:
        parsed = _safe_int(os.getenv(name, ""), 0)
        if parsed > 0:
            return parsed
    except Exception:
        pass

    return default


def _migration_int(base_name: str, guild_id: int) -> int:
    """
    Resolve temporary migration config safely.

    Order:
      1. explicit per-guild env override for this guild;
      2. legacy global env/app value only when this guild is the owner/home guild;
      3. 0 for every other guild.
    """
    gid = _safe_int(guild_id, 0)
    explicit = _env_guild_override_int(base_name, gid, 0)
    if explicit > 0:
        return explicit

    if _legacy_globals_allowed_for_guild(gid):
        return _legacy_global_int(base_name, 0)

    legacy = _legacy_global_int(base_name, 0)
    if legacy > 0:
        print(
            f"⚠️ guild_config refusing legacy global {base_name} for guild={gid}; "
            f"owner_guild={_configured_owner_guild_id() or 'unset'}"
        )
    return 0


def clear_guild_config_cache(guild_id: Optional[int] = None) -> None:
    if guild_id is None:
        _CONFIG_CACHE.clear()
        return
    _CONFIG_CACHE.pop(int(guild_id), None)


async def _get_lock(guild_id: int) -> asyncio.Lock:
    key = int(guild_id)
    lock = _CONFIG_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CONFIG_LOCKS[key] = lock
    return lock


# ============================================================
# DB loading
# ============================================================

def _load_config_row_sync(guild_id: int) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return None

    table = _table_name()
    res = (
        sb.table(table)
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], dict):
        return dict(rows[0])
    return None


async def _load_config_row(guild_id: int) -> Optional[Dict[str, Any]]:
    last_error: Optional[Exception] = None
    for attempt in range(1, 6):
        try:
            return await asyncio.to_thread(_load_config_row_sync, int(guild_id))
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < 5:
                try:
                    reset_supabase()
                except Exception:
                    pass
                await asyncio.sleep(min(0.35 * (2 ** (attempt - 1)), 2.5))
                continue
            raise
    if last_error:
        raise last_error
    return None


def _fallback_raw_from_env(guild_id: int) -> Dict[str, Any]:
    gid = int(guild_id)

    # Env fallback rules:
    # - Explicit per-guild env overrides are always allowed for that guild.
    # - Legacy single-server global IDs are allowed only for STONEY_OWNER_GUILD_ID / home guild.
    # - Other guilds must use DB config or setup discovery, never the owner's IDs.
    return {
        "guild_id": str(gid),
        "modlog_channel_id": _migration_int("MODLOG_CHANNEL_ID", gid),
        "transcripts_channel_id": _migration_int("TRANSCRIPTS_CHANNEL_ID", gid),
        "ticket_category_id": _migration_int("TICKET_CATEGORY_ID", gid),
        "ticket_archive_category_id": _migration_int("TICKET_ARCHIVE_CATEGORY_ID", gid),
        "verify_channel_id": _migration_int("VERIFY_CHANNEL_ID", gid),
        "vc_verify_channel_id": _migration_int("VC_VERIFY_CHANNEL_ID", gid),
        "vc_verify_queue_channel_id": _migration_int("VC_VERIFY_QUEUE_CHANNEL_ID", gid),
        "unverified_role_id": _migration_int("UNVERIFIED_ROLE_ID", gid),
        "verified_role_id": _migration_int("VERIFIED_ROLE_ID", gid),
        "resident_role_id": _migration_int("RESIDENT_ROLE_ID", gid),
        "staff_role_id": _migration_int("STAFF_ROLE_ID", gid),
        "source": "env_fallback_owner_guarded",
    }


async def get_guild_config(guild_id: int, *, force_refresh: bool = False) -> GuildConfig:
    gid = int(guild_id)
    now = time.monotonic()
    ttl = _cache_ttl_seconds()

    if not force_refresh:
        cached = _CONFIG_CACHE.get(gid)
        if cached is not None:
            created_at, cfg = cached
            if (now - created_at) <= ttl:
                return cfg

    lock = await _get_lock(gid)
    async with lock:
        if not force_refresh:
            cached = _CONFIG_CACHE.get(gid)
            if cached is not None:
                created_at, cfg = cached
                if (time.monotonic() - created_at) <= ttl:
                    return cfg

        row: Optional[Dict[str, Any]] = None
        source = "db"
        try:
            row = await _load_config_row(gid)
        except Exception as e:
            print(f"⚠️ guild_config load failed guild={gid}: {repr(e)}")

        if not isinstance(row, dict):
            row = _fallback_raw_from_env(gid)
            source = str(row.get("source") or "env_fallback_owner_guarded")

        cfg = GuildConfig(guild_id=gid, raw=row, source=source)
        _CONFIG_CACHE[gid] = (time.monotonic(), cfg)
        return cfg


# ============================================================
# Discord object resolution
# ============================================================

def _same_guild_text_channel(candidate: Any, guild: discord.Guild) -> bool:
    try:
        return (
            isinstance(candidate, discord.TextChannel)
            and getattr(candidate, "guild", None) is not None
            and int(candidate.guild.id) == int(guild.id)
        )
    except Exception:
        return False


def _same_guild_category(candidate: Any, guild: discord.Guild) -> bool:
    try:
        return (
            isinstance(candidate, discord.CategoryChannel)
            and getattr(candidate, "guild", None) is not None
            and int(candidate.guild.id) == int(guild.id)
        )
    except Exception:
        return False


def _same_guild_role(candidate: Any, guild: discord.Guild) -> bool:
    try:
        return (
            isinstance(candidate, discord.Role)
            and getattr(candidate, "guild", None) is not None
            and int(candidate.guild.id) == int(guild.id)
        )
    except Exception:
        return False


def _find_text_channel_by_names(guild: discord.Guild, names: Iterable[str], contains: Iterable[str]) -> Optional[discord.TextChannel]:
    exact = {str(n).strip().lower() for n in names if str(n).strip()}
    fuzzy = tuple(str(n).strip().lower() for n in contains if str(n).strip())

    try:
        for channel in guild.text_channels:
            name = str(channel.name or "").strip().lower()
            if name in exact:
                return channel
    except Exception:
        pass

    try:
        for channel in guild.text_channels:
            name = str(channel.name or "").strip().lower()
            if any(term in name for term in fuzzy):
                return channel
    except Exception:
        pass

    return None


def _find_category_by_names(guild: discord.Guild, names: Iterable[str], contains: Iterable[str]) -> Optional[discord.CategoryChannel]:
    exact = {str(n).strip().lower() for n in names if str(n).strip()}
    fuzzy = tuple(str(n).strip().lower() for n in contains if str(n).strip())

    try:
        for category in guild.categories:
            name = str(category.name or "").strip().lower()
            if name in exact:
                return category
    except Exception:
        pass

    try:
        for category in guild.categories:
            name = str(category.name or "").strip().lower()
            if any(term in name for term in fuzzy):
                return category
    except Exception:
        pass

    return None


async def resolve_configured_text_channel(
    guild: discord.Guild,
    *config_keys: str,
    fallback_names: Iterable[str] = (),
    fallback_contains: Iterable[str] = (),
    force_refresh: bool = False,
    label: str = "text_channel",
) -> Optional[discord.TextChannel]:
    cfg = await get_guild_config(int(guild.id), force_refresh=force_refresh)
    candidate_ids = []
    seen = set()

    for key in config_keys:
        cid = cfg.get_int(key, default=0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            candidate_ids.append(cid)

    for cid in candidate_ids:
        try:
            cached = guild.get_channel(cid)
            if _same_guild_text_channel(cached, guild):
                return cached
        except Exception:
            pass

        try:
            bot_cached = bot.get_channel(cid)
            if isinstance(bot_cached, discord.TextChannel):
                if int(bot_cached.guild.id) != int(guild.id):
                    print(
                        f"⚠️ guild_config {label} id={cid} belongs to different guild "
                        f"expected={guild.id} actual={bot_cached.guild.id} source={cfg.source}"
                    )
                    continue
                return bot_cached
        except Exception:
            pass

        try:
            fetched = await guild.fetch_channel(cid)
            if _same_guild_text_channel(fetched, guild):
                return fetched
            if isinstance(fetched, discord.TextChannel):
                print(
                    f"⚠️ guild_config {label} id={cid} fetched from different guild "
                    f"expected={guild.id} actual={getattr(getattr(fetched, 'guild', None), 'id', 'unknown')} source={cfg.source}"
                )
        except discord.InvalidData as e:
            print(f"⚠️ guild_config {label} id={cid} invalid for guild={guild.id}: {repr(e)}")
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ guild_config failed resolving {label} id={cid} guild={guild.id}: {repr(e)}")

    fallback = _find_text_channel_by_names(guild, fallback_names, fallback_contains)
    if fallback is not None:
        return fallback

    print(f"⚠️ guild_config could not resolve {label} guild={guild.id} keys={config_keys} ids={candidate_ids}")
    return None


async def resolve_configured_category(
    guild: discord.Guild,
    *config_keys: str,
    fallback_names: Iterable[str] = (),
    fallback_contains: Iterable[str] = (),
    force_refresh: bool = False,
    label: str = "category",
) -> Optional[discord.CategoryChannel]:
    cfg = await get_guild_config(int(guild.id), force_refresh=force_refresh)

    for key in config_keys:
        cid = cfg.get_int(key, default=0)
        if cid <= 0:
            continue
        try:
            cached = guild.get_channel(cid)
            if _same_guild_category(cached, guild):
                return cached
        except Exception:
            pass
        print(f"⚠️ guild_config category id={cid} not valid for guild={guild.id} label={label} source={cfg.source}")

    fallback = _find_category_by_names(guild, fallback_names, fallback_contains)
    if fallback is not None:
        return fallback

    print(f"⚠️ guild_config could not resolve {label} guild={guild.id} keys={config_keys}")
    return None


async def resolve_configured_role(
    guild: discord.Guild,
    *config_keys: str,
    force_refresh: bool = False,
    label: str = "role",
) -> Optional[discord.Role]:
    cfg = await get_guild_config(int(guild.id), force_refresh=force_refresh)

    for key in config_keys:
        rid = cfg.get_int(key, default=0)
        if rid <= 0:
            continue
        try:
            role = guild.get_role(rid)
            if _same_guild_role(role, guild):
                return role
        except Exception:
            pass
        print(f"⚠️ guild_config role id={rid} not valid for guild={guild.id} label={label} source={cfg.source}")

    print(f"⚠️ guild_config could not resolve {label} guild={guild.id} keys={config_keys}")
    return None
