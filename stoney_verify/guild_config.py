from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from .globals import (
    GUILD_ID,
    VERIFY_CHANNEL_ID,
    VC_VERIFY_CHANNEL_ID,
    VC_VERIFY_QUEUE_CHANNEL_ID,
    TICKET_CATEGORY_ID,
    TICKET_PREFIX,
    AUTO_DELETE_TICKET_SECONDS,
    TRANSCRIPTS_CHANNEL_ID,
    JOIN_LOG_CHANNEL_ID,
    TOKEN_TTL_MINUTES,
    VC_REQUEST_TTL_MINUTES,
    VERIFY_KICK_HOURS,
    VC_REQUEST_COOLDOWN_SECONDS,
    UNVERIFIED_ROLE_ID,
    VERIFIED_ROLE_ID,
    RESIDENT_ROLE_ID,
    STONER_ROLE_ID,
    DRUNKEN_ROLE_ID,
    STAFF_ROLE_ID,
    VC_STAFF_ROLE_ID,
    MODLOG_CHANNEL_ID,
    RAIDLOG_CHANNEL_ID,
    FORCE_VERIFY_LOG_CHANNEL_ID,
    ENABLE_OPTIONAL_ROLE_PROMPT,
    OPTIONAL_ROLE_AUTO_CLOSE_SECONDS,
    SINGLE_PANEL_MODE,
    TRANSCRIPT_PANEL_NAME,
    get_supabase,
)


# ============================================================
# guild_config.py
# ------------------------------------------------------------
# Public-scale per-guild configuration resolver.
#
# Why this exists:
# - globals.py is still the safe env fallback for your dev/beta server.
# - public bots cannot depend on one GUILD_ID / one set of channel ids.
# - every runtime path should eventually resolve config by guild_id.
#
# This module is intentionally defensive:
# - Supabase calls run off the Discord event loop via asyncio.to_thread.
# - missing DB/table/columns fall back to env config instead of crashing.
# - config is cached per guild with a short TTL.
# - rows may use either flat columns or a JSON settings/config column.
# ============================================================


DEFAULT_CONFIG_TABLE = "guild_configs"
DEFAULT_CACHE_TTL_SECONDS = 60.0

_WARNED_KEYS: set[str] = set()
_CONFIG_CACHE: dict[int, tuple[float, "GuildRuntimeConfig"]] = {}
_CONFIG_LOCKS: dict[int, asyncio.Lock] = {}
_CONFIG_LOCKS_GUARD = asyncio.Lock()


def _env_str(key: str, default: str = "") -> str:
    try:
        value = os.getenv(key)
        if value is None:
            return default
        value = str(value).strip()
        return value if value else default
    except Exception:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env_str(key)
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "y", "on"}


def _to_int(value: object, default: int = 0) -> int:
    if value is None:
        return int(default)
    try:
        if isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _to_str(value: object, default: str = "") -> str:
    if value is None:
        return str(default or "")
    try:
        text = str(value).strip()
        return text if text else str(default or "")
    except Exception:
        return str(default or "")


def _warn_once(key: str, message: str) -> None:
    try:
        clean = str(key or "").strip().lower()
        if clean in _WARNED_KEYS:
            return
        _WARNED_KEYS.add(clean)
        print(message)
    except Exception:
        pass


def _table_name() -> str:
    return _env_str("STONEY_GUILD_CONFIG_TABLE", DEFAULT_CONFIG_TABLE)


def _cache_ttl() -> float:
    return max(5.0, _env_float("STONEY_GUILD_CONFIG_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS))


def _pick(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return default


def _nested_settings(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("settings", "config", "metadata", "meta"):
        value = row.get(key)
        if isinstance(value, Mapping):
            merged.update(dict(value))
    merged.update(dict(row))
    return merged


@dataclass(frozen=True)
class GuildRuntimeConfig:
    guild_id: int

    verify_channel_id: int = 0
    vc_verify_channel_id: int = 0
    vc_verify_queue_channel_id: int = 0

    ticket_category_id: int = 0
    ticket_prefix: str = "ticket"
    auto_delete_ticket_seconds: int = 0
    transcripts_channel_id: int = 0
    transcript_panel_name: str = "Support"
    single_panel_mode: bool = True
    join_log_channel_id: int = 0

    token_ttl_minutes: int = 240
    vc_request_ttl_minutes: int = 240
    verify_kick_hours: int = 24
    vc_request_cooldown_seconds: int = 60

    unverified_role_id: int = 0
    verified_role_id: int = 0
    resident_role_id: int = 0
    stoner_role_id: int = 0
    drunken_role_id: int = 0
    staff_role_id: int = 0
    vc_staff_role_id: int = 0

    modlog_channel_id: int = 0
    raidlog_channel_id: int = 0
    force_verify_log_channel_id: int = 0

    enable_optional_role_prompt: bool = True
    optional_role_auto_close_seconds: int = 0

    source: str = "env"
    loaded_at_monotonic: float = 0.0

    @property
    def effective_verify_channel_id(self) -> int:
        return int(self.verify_channel_id or self.vc_verify_channel_id or 0)

    @property
    def effective_vc_staff_role_id(self) -> int:
        return int(self.vc_staff_role_id or self.staff_role_id or 0)

    def as_startup_summary(self) -> dict[str, object]:
        return {
            "guild": self.guild_id,
            "source": self.source,
            "verify_channel": self.effective_verify_channel_id,
            "vc_verify_channel": self.vc_verify_channel_id,
            "vc_verify_queue_channel": self.vc_verify_queue_channel_id,
            "ticket_category": self.ticket_category_id,
            "ticket_prefix": self.ticket_prefix,
            "unverified_role": self.unverified_role_id,
            "verified_role": self.verified_role_id,
            "staff_role": self.staff_role_id,
            "transcripts_channel": self.transcripts_channel_id,
            "verify_kick_hours": self.verify_kick_hours,
        }


def env_fallback_config(guild_id: int | str | None = None) -> GuildRuntimeConfig:
    gid = _to_int(guild_id, GUILD_ID)
    return GuildRuntimeConfig(
        guild_id=gid,
        verify_channel_id=VERIFY_CHANNEL_ID,
        vc_verify_channel_id=VC_VERIFY_CHANNEL_ID,
        vc_verify_queue_channel_id=VC_VERIFY_QUEUE_CHANNEL_ID,
        ticket_category_id=TICKET_CATEGORY_ID,
        ticket_prefix=TICKET_PREFIX or "ticket",
        auto_delete_ticket_seconds=AUTO_DELETE_TICKET_SECONDS,
        transcripts_channel_id=TRANSCRIPTS_CHANNEL_ID,
        transcript_panel_name=TRANSCRIPT_PANEL_NAME or "Support",
        single_panel_mode=bool(SINGLE_PANEL_MODE),
        join_log_channel_id=JOIN_LOG_CHANNEL_ID,
        token_ttl_minutes=TOKEN_TTL_MINUTES,
        vc_request_ttl_minutes=VC_REQUEST_TTL_MINUTES,
        verify_kick_hours=VERIFY_KICK_HOURS,
        vc_request_cooldown_seconds=VC_REQUEST_COOLDOWN_SECONDS,
        unverified_role_id=UNVERIFIED_ROLE_ID,
        verified_role_id=VERIFIED_ROLE_ID,
        resident_role_id=RESIDENT_ROLE_ID,
        stoner_role_id=STONER_ROLE_ID,
        drunken_role_id=DRUNKEN_ROLE_ID,
        staff_role_id=STAFF_ROLE_ID,
        vc_staff_role_id=VC_STAFF_ROLE_ID or STAFF_ROLE_ID,
        modlog_channel_id=MODLOG_CHANNEL_ID,
        raidlog_channel_id=RAIDLOG_CHANNEL_ID,
        force_verify_log_channel_id=FORCE_VERIFY_LOG_CHANNEL_ID,
        enable_optional_role_prompt=bool(ENABLE_OPTIONAL_ROLE_PROMPT),
        optional_role_auto_close_seconds=OPTIONAL_ROLE_AUTO_CLOSE_SECONDS,
        source="env",
        loaded_at_monotonic=time.monotonic(),
    )


def _apply_row_to_config(base: GuildRuntimeConfig, row: Mapping[str, Any]) -> GuildRuntimeConfig:
    data = _nested_settings(row)

    staff_role_id = _to_int(
        _pick(data, "staff_role_id", "support_role_id", "mod_role_id"),
        base.staff_role_id,
    )
    vc_staff_role_id = _to_int(
        _pick(data, "vc_staff_role_id", "voice_staff_role_id"),
        base.vc_staff_role_id or staff_role_id,
    )

    verify_channel_id = _to_int(
        _pick(data, "verify_channel_id", "verification_channel_id", "verify_channel"),
        base.verify_channel_id,
    )
    vc_verify_channel_id = _to_int(
        _pick(data, "vc_verify_channel_id", "voice_verify_channel_id", "vc_channel_id"),
        base.vc_verify_channel_id,
    )

    return replace(
        base,
        verify_channel_id=verify_channel_id or vc_verify_channel_id,
        vc_verify_channel_id=vc_verify_channel_id,
        vc_verify_queue_channel_id=_to_int(
            _pick(data, "vc_verify_queue_channel_id", "voice_verify_queue_channel_id"),
            base.vc_verify_queue_channel_id,
        ),
        ticket_category_id=_to_int(
            _pick(data, "ticket_category_id", "tickets_category_id", "support_category_id"),
            base.ticket_category_id,
        ),
        ticket_prefix=_to_str(_pick(data, "ticket_prefix", "ticket_channel_prefix"), base.ticket_prefix or "ticket"),
        auto_delete_ticket_seconds=_to_int(
            _pick(data, "auto_delete_ticket_seconds", "ticket_auto_delete_seconds"),
            base.auto_delete_ticket_seconds,
        ),
        transcripts_channel_id=_to_int(
            _pick(data, "transcripts_channel_id", "transcript_channel_id"),
            base.transcripts_channel_id,
        ),
        transcript_panel_name=_to_str(
            _pick(data, "transcript_panel_name", "panel_name"),
            base.transcript_panel_name or "Support",
        ),
        single_panel_mode=_truthy(_pick(data, "single_panel_mode"), base.single_panel_mode),
        join_log_channel_id=_to_int(_pick(data, "join_log_channel_id"), base.join_log_channel_id),
        token_ttl_minutes=_to_int(_pick(data, "token_ttl_minutes"), base.token_ttl_minutes),
        vc_request_ttl_minutes=_to_int(_pick(data, "vc_request_ttl_minutes"), base.vc_request_ttl_minutes),
        verify_kick_hours=_to_int(_pick(data, "verify_kick_hours"), base.verify_kick_hours),
        vc_request_cooldown_seconds=_to_int(
            _pick(data, "vc_request_cooldown_seconds"),
            base.vc_request_cooldown_seconds,
        ),
        unverified_role_id=_to_int(_pick(data, "unverified_role_id"), base.unverified_role_id),
        verified_role_id=_to_int(_pick(data, "verified_role_id"), base.verified_role_id),
        resident_role_id=_to_int(_pick(data, "resident_role_id"), base.resident_role_id),
        stoner_role_id=_to_int(_pick(data, "stoner_role_id"), base.stoner_role_id),
        drunken_role_id=_to_int(_pick(data, "drunken_role_id"), base.drunken_role_id),
        staff_role_id=staff_role_id,
        vc_staff_role_id=vc_staff_role_id or staff_role_id,
        modlog_channel_id=_to_int(_pick(data, "modlog_channel_id", "mod_log_channel_id"), base.modlog_channel_id),
        raidlog_channel_id=_to_int(_pick(data, "raidlog_channel_id", "raid_log_channel_id"), base.raidlog_channel_id),
        force_verify_log_channel_id=_to_int(
            _pick(data, "force_verify_log_channel_id"),
            base.force_verify_log_channel_id,
        ),
        enable_optional_role_prompt=_truthy(
            _pick(data, "enable_optional_role_prompt", "optional_role_prompt_enabled"),
            base.enable_optional_role_prompt,
        ),
        optional_role_auto_close_seconds=_to_int(
            _pick(data, "optional_role_auto_close_seconds"),
            base.optional_role_auto_close_seconds,
        ),
        source=f"supabase:{_table_name()}",
        loaded_at_monotonic=time.monotonic(),
    )


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    supabase = get_supabase()
    if supabase is None:
        return None

    table = _table_name()
    response = (
        supabase.table(table)
        .select("*")
        .eq("guild_id", str(guild_id))
        .limit(1)
        .execute()
    )

    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    first = rows[0]
    return dict(first) if isinstance(first, Mapping) else None


async def _lock_for_guild(guild_id: int) -> asyncio.Lock:
    async with _CONFIG_LOCKS_GUARD:
        lock = _CONFIG_LOCKS.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            _CONFIG_LOCKS[guild_id] = lock
        return lock


async def get_guild_config(guild_id: int | str | None, *, refresh: bool = False) -> GuildRuntimeConfig:
    gid = _to_int(guild_id, GUILD_ID)
    if gid <= 0:
        return env_fallback_config(gid)

    now = time.monotonic()
    ttl = _cache_ttl()

    cached = _CONFIG_CACHE.get(gid)
    if cached and not refresh:
        loaded_at, cfg = cached
        if now - loaded_at <= ttl:
            return cfg

    lock = await _lock_for_guild(gid)
    async with lock:
        cached = _CONFIG_CACHE.get(gid)
        now = time.monotonic()
        if cached and not refresh:
            loaded_at, cfg = cached
            if now - loaded_at <= ttl:
                return cfg

        base = env_fallback_config(gid)

        try:
            row = await asyncio.to_thread(_fetch_config_row_sync, gid)
        except Exception as e:
            _warn_once(
                f"guild-config-fetch:{gid}",
                f"⚠️ guild_config: using env fallback for guild={gid}; DB config fetch failed: {repr(e)}",
            )
            cfg = base
        else:
            if row:
                cfg = _apply_row_to_config(base, row)
            else:
                _warn_once(
                    f"guild-config-missing:{gid}",
                    f"ℹ️ guild_config: no DB config row for guild={gid}; using env fallback.",
                )
                cfg = base

        _CONFIG_CACHE[gid] = (time.monotonic(), cfg)
        return cfg


def get_cached_guild_config(guild_id: int | str | None) -> GuildRuntimeConfig:
    gid = _to_int(guild_id, GUILD_ID)
    cached = _CONFIG_CACHE.get(gid)
    if cached:
        return cached[1]
    return env_fallback_config(gid)


def invalidate_guild_config(guild_id: int | str | None = None) -> None:
    gid = _to_int(guild_id, 0)
    if gid > 0:
        _CONFIG_CACHE.pop(gid, None)
        return
    _CONFIG_CACHE.clear()


def guild_config_cache_snapshot() -> dict[str, object]:
    now = time.monotonic()
    return {
        "table": _table_name(),
        "ttl_seconds": _cache_ttl(),
        "cached_guilds": len(_CONFIG_CACHE),
        "guilds": {
            str(gid): {
                "source": cfg.source,
                "age_seconds": round(now - loaded_at, 3),
                "ticket_category": cfg.ticket_category_id,
                "staff_role": cfg.staff_role_id,
            }
            for gid, (loaded_at, cfg) in _CONFIG_CACHE.items()
        },
    }


__all__ = [
    "GuildRuntimeConfig",
    "env_fallback_config",
    "get_guild_config",
    "get_cached_guild_config",
    "invalidate_guild_config",
    "guild_config_cache_snapshot",
]
