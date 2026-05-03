from __future__ import annotations

"""
Simple per-guild service presets.

Admins should not need to understand raw DB flags. This module provides a tiny,
readable preset layer that turns product choices into guild_configs service
flags.
"""

import asyncio
import os
from typing import Any, Dict, Optional

from ..globals import get_supabase, reset_supabase
from ..runtime_limits import db_guild_limit, jitter_sleep
from .guild_config import clear_guild_config_cache


DEFAULT_TABLE_NAME = "guild_configs"
DEFAULT_DB_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_ATTEMPTS = 5

SERVICE_PRESETS: Dict[str, Dict[str, bool]] = {
    "tickets": {
        "tickets_enabled": True,
        "verification_enabled": False,
        "voice_verification_enabled": False,
        "moderation_enabled": False,
    },
    "tickets_modlog": {
        "tickets_enabled": True,
        "verification_enabled": False,
        "voice_verification_enabled": False,
        "moderation_enabled": True,
    },
    "verification": {
        "tickets_enabled": False,
        "verification_enabled": True,
        "voice_verification_enabled": False,
        "moderation_enabled": False,
    },
    "voice_verification": {
        "tickets_enabled": False,
        "verification_enabled": False,
        "voice_verification_enabled": True,
        "moderation_enabled": False,
    },
    "verification_plus_voice": {
        "tickets_enabled": False,
        "verification_enabled": True,
        "voice_verification_enabled": True,
        "moderation_enabled": False,
    },
    "full": {
        "tickets_enabled": True,
        "verification_enabled": True,
        "voice_verification_enabled": True,
        "moderation_enabled": True,
    },
}

PRESET_LABELS: Dict[str, str] = {
    "tickets": "Tickets only",
    "tickets_modlog": "Tickets + Modlog",
    "verification": "ID verification only",
    "voice_verification": "Voice verification only",
    "verification_plus_voice": "ID + Voice verification",
    "full": "Full Stoney suite",
}


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


async def _sleep_backoff(attempt: int, *, guild_id: int | str) -> None:
    await jitter_sleep(
        base_seconds=min(0.35 * (2 ** max(0, attempt - 1)), 2.5),
        max_jitter_seconds=0.35,
        guild_id=guild_id,
    )


def _upsert_service_preset_sync(guild_id: int, preset_key: str, flags: Dict[str, bool]) -> Dict[str, Any]:
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase client unavailable")

    payload: Dict[str, Any] = {
        "guild_id": str(int(guild_id)),
        **flags,
        "setup_completed": False,
        "setup_source": f"service_preset:{preset_key}",
        "setup_notes": (
            f"Service preset selected: {PRESET_LABELS.get(preset_key, preset_key)}. "
            "Run setup-health and configure only the channels/roles required by the selected services."
        ),
    }

    res = sb.table(_table_name()).upsert(payload, on_conflict="guild_id").execute()
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], dict):
        return dict(rows[0])
    return payload


async def save_service_preset(guild_id: int | str, preset_key: str) -> Dict[str, Any]:
    gid = int(str(guild_id))
    key = str(preset_key or "").strip().lower()
    flags = SERVICE_PRESETS.get(key)
    if flags is None:
        raise ValueError(f"Unknown service preset: {preset_key}")

    last_error: Optional[Exception] = None
    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        try:
            async with db_guild_limit(gid, label="service_preset"):
                row = await asyncio.wait_for(
                    asyncio.to_thread(_upsert_service_preset_sync, gid, key, flags),
                    timeout=DEFAULT_DB_TIMEOUT_SECONDS,
                )
                clear_guild_config_cache(gid)
                return {
                    "ok": True,
                    "guild_id": str(gid),
                    "preset_key": key,
                    "preset_label": PRESET_LABELS.get(key, key),
                    "flags": dict(flags),
                    "row": row,
                }
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < DEFAULT_MAX_ATTEMPTS:
                try:
                    reset_supabase()
                except Exception:
                    pass
                await _sleep_backoff(attempt, guild_id=gid)
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Service preset save failed without captured exception")


def preset_summary(preset_key: str) -> str:
    key = str(preset_key or "").strip().lower()
    flags = SERVICE_PRESETS.get(key, {})
    enabled = []
    if flags.get("tickets_enabled"):
        enabled.append("Tickets")
    if flags.get("verification_enabled"):
        enabled.append("ID verification")
    if flags.get("voice_verification_enabled"):
        enabled.append("Voice verification")
    if flags.get("moderation_enabled"):
        enabled.append("Modlog/moderation")
    return ", ".join(enabled) if enabled else "No services"


__all__ = ["PRESET_LABELS", "SERVICE_PRESETS", "preset_summary", "save_service_preset"]
