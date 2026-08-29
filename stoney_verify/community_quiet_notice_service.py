from __future__ import annotations

"""Persistent configuration for server-wide quiet-activity notices."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .community_tools_service import (
    CommunityStorageUnavailable,
    InvalidCommunityToolValue,
    normalize_https_url,
    utc_now,
)
from .globals import get_supabase

QUIET_NOTICE_TABLE = "dank_quiet_notices"
DEFAULT_INACTIVITY_SECONDS = 2 * 60 * 60
MIN_INACTIVITY_SECONDS = 5 * 60
MAX_INACTIVITY_SECONDS = 7 * 24 * 60 * 60
MAX_QUIET_CONTENT = 1800
MAX_PARTNER_NAME = 100


@dataclass(frozen=True)
class QuietNoticeConfig:
    guild_id: int
    channel_id: int
    enabled: bool = True
    content: str = ""
    inactivity_seconds: int = DEFAULT_INACTIVITY_SECONDS
    partner_name: str = ""
    partner_url: str = ""
    auto_clear: bool = True
    last_activity_at: Optional[datetime] = None
    last_notice_message_id: Optional[int] = None
    last_notice_sent_at: Optional[datetime] = None
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None


_LOCKS: dict[int, asyncio.Lock] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _safe_dt(value: Any) -> Optional[datetime]:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _clean_text(value: Any, *, maximum: int, allow_empty: bool = True) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not allow_empty and not text:
        raise InvalidCommunityToolValue("This field cannot be empty.")
    if len(text) > maximum:
        raise InvalidCommunityToolValue(f"Keep this field at {maximum} characters or fewer.")
    return text


def normalize_quiet_notice(config: QuietNoticeConfig) -> QuietNoticeConfig:
    guild_id = _safe_int(config.guild_id)
    channel_id = _safe_int(config.channel_id)
    if guild_id <= 0 or channel_id <= 0:
        raise InvalidCommunityToolValue("Quiet notices need a valid server and destination channel.")

    content = _clean_text(config.content, maximum=MAX_QUIET_CONTENT, allow_empty=False)
    partner_name = _clean_text(config.partner_name, maximum=MAX_PARTNER_NAME)
    partner_url = normalize_https_url(config.partner_url)
    inactivity_seconds = max(
        MIN_INACTIVITY_SECONDS,
        min(_safe_int(config.inactivity_seconds, DEFAULT_INACTIVITY_SECONDS), MAX_INACTIVITY_SECONDS),
    )

    return replace(
        config,
        guild_id=guild_id,
        channel_id=channel_id,
        enabled=bool(config.enabled),
        content=content,
        inactivity_seconds=inactivity_seconds,
        partner_name=partner_name,
        partner_url=partner_url,
        auto_clear=bool(config.auto_clear),
        last_activity_at=_safe_dt(config.last_activity_at),
        last_notice_message_id=_safe_int(config.last_notice_message_id, 0) or None,
        last_notice_sent_at=_safe_dt(config.last_notice_sent_at),
        updated_by=_safe_int(config.updated_by, 0) or None,
        updated_at=_safe_dt(config.updated_at),
    )


def _require_supabase() -> Any:
    sb = get_supabase()
    if sb is None:
        raise CommunityStorageUnavailable("Supabase is not configured.")
    return sb


def _row_to_quiet_notice(row: Mapping[str, Any]) -> QuietNoticeConfig:
    return normalize_quiet_notice(
        QuietNoticeConfig(
            guild_id=_safe_int(row.get("guild_id")),
            channel_id=_safe_int(row.get("channel_id")),
            enabled=_safe_bool(row.get("enabled"), True),
            content=str(row.get("content") or ""),
            inactivity_seconds=_safe_int(row.get("inactivity_seconds"), DEFAULT_INACTIVITY_SECONDS),
            partner_name=str(row.get("partner_name") or ""),
            partner_url=str(row.get("partner_url") or ""),
            auto_clear=_safe_bool(row.get("auto_clear"), True),
            last_activity_at=_safe_dt(row.get("last_activity_at")),
            last_notice_message_id=_safe_int(row.get("last_notice_message_id"), 0) or None,
            last_notice_sent_at=_safe_dt(row.get("last_notice_sent_at")),
            updated_by=_safe_int(row.get("updated_by"), 0) or None,
            updated_at=_safe_dt(row.get("updated_at")),
        )
    )


def _get_sync(guild_id: int) -> Optional[QuietNoticeConfig]:
    try:
        resp = _require_supabase().table(QUIET_NOTICE_TABLE).select("*").eq("guild_id", int(guild_id)).limit(1).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{QUIET_NOTICE_TABLE}` is unavailable.") from exc
    rows = getattr(resp, "data", None) or []
    if not rows or not isinstance(rows[0], Mapping):
        return None
    return _row_to_quiet_notice(rows[0])


def _list_sync(*, enabled_only: bool = False) -> list[QuietNoticeConfig]:
    try:
        query = _require_supabase().table(QUIET_NOTICE_TABLE).select("*")
        if enabled_only:
            query = query.eq("enabled", True)
        resp = query.order("guild_id").execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{QUIET_NOTICE_TABLE}` is unavailable.") from exc
    rows = getattr(resp, "data", None) or []
    return [_row_to_quiet_notice(row) for row in rows if isinstance(row, Mapping)]


def _save_sync(config: QuietNoticeConfig) -> QuietNoticeConfig:
    safe = normalize_quiet_notice(config)
    payload = {
        "guild_id": int(safe.guild_id),
        "channel_id": int(safe.channel_id),
        "enabled": bool(safe.enabled),
        "content": safe.content,
        "inactivity_seconds": int(safe.inactivity_seconds),
        "partner_name": safe.partner_name or None,
        "partner_url": safe.partner_url or None,
        "auto_clear": bool(safe.auto_clear),
        "last_activity_at": safe.last_activity_at.isoformat() if safe.last_activity_at else None,
        "last_notice_message_id": int(safe.last_notice_message_id) if safe.last_notice_message_id else None,
        "last_notice_sent_at": safe.last_notice_sent_at.isoformat() if safe.last_notice_sent_at else None,
        "updated_by": int(safe.updated_by) if safe.updated_by else None,
        "updated_at": utc_now().isoformat(),
    }
    try:
        resp = _require_supabase().table(QUIET_NOTICE_TABLE).upsert(payload, on_conflict="guild_id").execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{QUIET_NOTICE_TABLE}` is not writable.") from exc
    rows = getattr(resp, "data", None) or []
    return _row_to_quiet_notice(rows[0]) if rows and isinstance(rows[0], Mapping) else replace(safe, updated_at=utc_now())


def _delete_sync(guild_id: int) -> None:
    try:
        _require_supabase().table(QUIET_NOTICE_TABLE).delete().eq("guild_id", int(guild_id)).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{QUIET_NOTICE_TABLE}` is not writable.") from exc


async def get_quiet_notice(guild_id: int) -> Optional[QuietNoticeConfig]:
    return await asyncio.to_thread(_get_sync, int(guild_id))


async def list_quiet_notices(*, enabled_only: bool = False) -> list[QuietNoticeConfig]:
    return await asyncio.to_thread(_list_sync, enabled_only=enabled_only)


async def save_quiet_notice(config: QuietNoticeConfig) -> QuietNoticeConfig:
    lock = _LOCKS.setdefault(int(config.guild_id), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_save_sync, config)


async def set_quiet_notice_enabled(
    guild_id: int,
    enabled: bool,
    *,
    actor_id: Optional[int] = None,
    reset_activity_on_enable: bool = False,
) -> Optional[QuietNoticeConfig]:
    lock = _LOCKS.setdefault(int(guild_id), asyncio.Lock())
    async with lock:
        current = await asyncio.to_thread(_get_sync, int(guild_id))
        if current is None:
            return None
        activity_at = utc_now() if enabled and reset_activity_on_enable else current.last_activity_at
        return await asyncio.to_thread(
            _save_sync,
            replace(
                current,
                enabled=bool(enabled),
                last_activity_at=activity_at,
                last_notice_message_id=None if enabled and reset_activity_on_enable else current.last_notice_message_id,
                last_notice_sent_at=None if enabled and reset_activity_on_enable else current.last_notice_sent_at,
                updated_by=int(actor_id) if actor_id else current.updated_by,
            ),
        )


async def record_quiet_activity(
    guild_id: int,
    *,
    activity_at: Optional[datetime] = None,
    clear_delivery: bool = False,
) -> Optional[QuietNoticeConfig]:
    lock = _LOCKS.setdefault(int(guild_id), asyncio.Lock())
    async with lock:
        current = await asyncio.to_thread(_get_sync, int(guild_id))
        if current is None:
            return None
        observed = _safe_dt(activity_at) or utc_now()
        existing = current.last_activity_at
        if existing is not None and existing > observed:
            observed = existing
        return await asyncio.to_thread(
            _save_sync,
            replace(
                current,
                last_activity_at=observed,
                last_notice_message_id=None if clear_delivery else current.last_notice_message_id,
                last_notice_sent_at=None if clear_delivery else current.last_notice_sent_at,
            ),
        )


async def update_quiet_delivery(
    guild_id: int,
    *,
    message_id: Optional[int],
    sent_at: Optional[datetime] = None,
) -> Optional[QuietNoticeConfig]:
    lock = _LOCKS.setdefault(int(guild_id), asyncio.Lock())
    async with lock:
        current = await asyncio.to_thread(_get_sync, int(guild_id))
        if current is None:
            return None
        return await asyncio.to_thread(
            _save_sync,
            replace(
                current,
                last_notice_message_id=int(message_id) if message_id else None,
                last_notice_sent_at=(sent_at or utc_now()) if message_id else None,
            ),
        )


async def clear_quiet_delivery(guild_id: int) -> Optional[QuietNoticeConfig]:
    return await update_quiet_delivery(int(guild_id), message_id=None)


async def delete_quiet_notice(guild_id: int) -> None:
    lock = _LOCKS.setdefault(int(guild_id), asyncio.Lock())
    async with lock:
        await asyncio.to_thread(_delete_sync, int(guild_id))


__all__ = [
    "DEFAULT_INACTIVITY_SECONDS",
    "MAX_INACTIVITY_SECONDS",
    "MIN_INACTIVITY_SECONDS",
    "QuietNoticeConfig",
    "clear_quiet_delivery",
    "delete_quiet_notice",
    "get_quiet_notice",
    "list_quiet_notices",
    "normalize_quiet_notice",
    "record_quiet_activity",
    "save_quiet_notice",
    "set_quiet_notice_enabled",
    "update_quiet_delivery",
]
