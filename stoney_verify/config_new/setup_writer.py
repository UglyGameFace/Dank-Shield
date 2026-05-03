from __future__ import annotations

"""
Simple guild setup writer.

Admins should not need to copy raw Discord IDs into env vars or Supabase.
This module accepts Discord-selected channels/categories/roles and writes only
those selected values into guild_configs.

It intentionally avoids overwriting unrelated config fields. If a guild has
chosen tickets-only, saving verification channels will not enable verification;
service enablement stays controlled by /setup-services.
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

ALLOWED_SETUP_FIELDS = {
    "modlog_channel_id",
    "transcripts_channel_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "verify_channel_id",
    "vc_verify_channel_id",
    "vc_verify_queue_channel_id",
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "staff_role_id",
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


def _clean_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        ident = getattr(value, "id", value)
        text = str(ident or "").strip()
        if not text or text == "0" or not text.isdigit():
            return None
        return text
    except Exception:
        return None


def _upsert_setup_targets_sync(guild_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase client unavailable")

    payload: Dict[str, Any] = {
        "guild_id": str(int(guild_id)),
        "setup_completed": False,
        "setup_source": "setup_targets_command",
        "setup_notes": "Setup targets were saved from Discord channel/role picker selections. Run setup-health to verify selected services.",
    }

    for key, value in fields.items():
        if key not in ALLOWED_SETUP_FIELDS:
            continue
        cleaned = _clean_id(value)
        if cleaned:
            payload[key] = cleaned

    if len(payload) <= 4:
        return {"guild_id": str(int(guild_id)), "updated": False, "reason": "no_valid_fields"}

    res = sb.table(_table_name()).upsert(payload, on_conflict="guild_id").execute()
    rows = getattr(res, "data", None) or []
    if rows and isinstance(rows[0], dict):
        return dict(rows[0])
    return payload


async def save_setup_targets(guild_id: int | str, **fields: Any) -> Dict[str, Any]:
    gid = int(str(guild_id))
    filtered = {key: value for key, value in fields.items() if key in ALLOWED_SETUP_FIELDS and _clean_id(value)}

    if not filtered:
        return {
            "ok": False,
            "guild_id": str(gid),
            "updated": False,
            "reason": "No valid channels/categories/roles were selected.",
            "fields": {},
        }

    last_error: Optional[Exception] = None
    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        try:
            async with db_guild_limit(gid, label="setup_targets"):
                row = await asyncio.wait_for(
                    asyncio.to_thread(_upsert_setup_targets_sync, gid, filtered),
                    timeout=DEFAULT_DB_TIMEOUT_SECONDS,
                )
                clear_guild_config_cache(gid)
                return {
                    "ok": True,
                    "guild_id": str(gid),
                    "updated": True,
                    "fields": {key: _clean_id(value) for key, value in filtered.items()},
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
    raise RuntimeError("Setup target save failed without captured exception")


__all__ = ["ALLOWED_SETUP_FIELDS", "save_setup_targets"]
