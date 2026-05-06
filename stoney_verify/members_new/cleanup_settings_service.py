from __future__ import annotations

"""Member cleanup settings for Dank Shield.

The cleanup system defaults to confirmed/manual operation. This service stores
per-guild preferences for the cleanup queue while keeping safe defaults if the
optional settings table is missing.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

try:
    from stoney_verify.globals import get_supabase, now_utc
except Exception:
    get_supabase = None  # type: ignore

    def now_utc() -> datetime:  # type: ignore
        return datetime.now(timezone.utc)


SETTINGS_TABLE = "member_cleanup_settings"
_MEMORY_SETTINGS: dict[int, "MemberCleanupSettings"] = {}


@dataclass(frozen=True)
class MemberCleanupSettings:
    guild_id: int
    require_queue_confirmation: bool = True
    allow_low_confidence_queue: bool = False
    default_queue_limit: int = 10
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None
    persisted: bool = False
    storage_note: str = "safe defaults"

    @property
    def mode_label(self) -> str:
        return "Confirmation required" if self.require_queue_confirmation else "Auto-process queue"

    @property
    def low_confidence_label(self) -> str:
        return "Allowed in queue" if self.allow_low_confidence_queue else "Manual review only"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)
    except Exception:
        return bool(default)


def _safe_dt(value: Any) -> Optional[datetime]:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value).strip().replace("Z", "+00:00")
            if not raw:
                return None
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _default_settings(guild_id: int, *, note: str = "safe defaults") -> MemberCleanupSettings:
    memory = _MEMORY_SETTINGS.get(int(guild_id))
    if memory is not None:
        return replace(memory, storage_note=note or memory.storage_note)
    return MemberCleanupSettings(guild_id=int(guild_id), storage_note=note)


def _select_settings_sync(guild_id: int) -> tuple[Optional[dict[str, Any]], bool, str]:
    if get_supabase is None:
        return None, False, "Supabase unavailable; using memory/default cleanup settings."
    sb = get_supabase()
    if sb is None:
        return None, False, "Supabase unavailable; using memory/default cleanup settings."
    try:
        resp = sb.table(SETTINGS_TABLE).select("*").eq("guild_id", str(int(guild_id))).limit(1).execute()
        rows = getattr(resp, "data", None) or []
        if not rows:
            try:
                resp = sb.table(SETTINGS_TABLE).select("*").eq("guild_id", int(guild_id)).limit(1).execute()
                rows = getattr(resp, "data", None) or []
            except Exception:
                pass
        if rows and isinstance(rows[0], Mapping):
            return dict(rows[0]), True, "persistent"
        return None, True, "persistent; no row yet"
    except Exception:
        return None, False, f"Optional `{SETTINGS_TABLE}` table was not readable; using memory/default cleanup settings."


def _write_settings_sync(settings: MemberCleanupSettings) -> tuple[bool, str]:
    if get_supabase is None:
        return False, "Supabase unavailable; cleanup settings saved in memory only."
    sb = get_supabase()
    if sb is None:
        return False, "Supabase unavailable; cleanup settings saved in memory only."
    try:
        payload = {
            "guild_id": str(int(settings.guild_id)),
            "require_queue_confirmation": bool(settings.require_queue_confirmation),
            "allow_low_confidence_queue": bool(settings.allow_low_confidence_queue),
            "default_queue_limit": max(1, min(int(settings.default_queue_limit), 20)),
            "updated_by": str(int(settings.updated_by)) if settings.updated_by else None,
            "updated_at": now_utc().isoformat(),
        }
        sb.table(SETTINGS_TABLE).upsert(payload, on_conflict="guild_id").execute()
        return True, "persistent"
    except Exception:
        return False, f"Optional `{SETTINGS_TABLE}` table was not writable; cleanup settings saved in memory only."


async def get_cleanup_settings(guild_id: int) -> MemberCleanupSettings:
    import asyncio

    row, persisted_ok, note = await asyncio.to_thread(_select_settings_sync, int(guild_id))
    if not persisted_ok:
        return _default_settings(int(guild_id), note=note)
    if not row:
        return _default_settings(int(guild_id), note=note)
    return MemberCleanupSettings(
        guild_id=int(guild_id),
        require_queue_confirmation=_safe_bool(row.get("require_queue_confirmation"), True),
        allow_low_confidence_queue=_safe_bool(row.get("allow_low_confidence_queue"), False),
        default_queue_limit=max(1, min(_safe_int(row.get("default_queue_limit"), 10), 20)),
        updated_by=_safe_int(row.get("updated_by"), 0) or None,
        updated_at=_safe_dt(row.get("updated_at")),
        persisted=True,
        storage_note=note,
    )


async def save_cleanup_settings(settings: MemberCleanupSettings) -> MemberCleanupSettings:
    import asyncio

    safe = MemberCleanupSettings(
        guild_id=int(settings.guild_id),
        require_queue_confirmation=bool(settings.require_queue_confirmation),
        allow_low_confidence_queue=bool(settings.allow_low_confidence_queue),
        default_queue_limit=max(1, min(int(settings.default_queue_limit), 20)),
        updated_by=int(settings.updated_by) if settings.updated_by else None,
        updated_at=now_utc(),
        persisted=False,
        storage_note="memory-only",
    )
    _MEMORY_SETTINGS[int(safe.guild_id)] = safe
    persisted, note = await asyncio.to_thread(_write_settings_sync, safe)
    return replace(safe, persisted=bool(persisted), storage_note=note)


async def update_cleanup_settings(
    guild_id: int,
    *,
    actor_id: Optional[int] = None,
    require_queue_confirmation: Optional[bool] = None,
    allow_low_confidence_queue: Optional[bool] = None,
    default_queue_limit: Optional[int] = None,
) -> MemberCleanupSettings:
    current = await get_cleanup_settings(int(guild_id))
    updated = MemberCleanupSettings(
        guild_id=int(guild_id),
        require_queue_confirmation=current.require_queue_confirmation if require_queue_confirmation is None else bool(require_queue_confirmation),
        allow_low_confidence_queue=current.allow_low_confidence_queue if allow_low_confidence_queue is None else bool(allow_low_confidence_queue),
        default_queue_limit=current.default_queue_limit if default_queue_limit is None else max(1, min(int(default_queue_limit), 20)),
        updated_by=int(actor_id) if actor_id else current.updated_by,
        updated_at=now_utc(),
        persisted=False,
        storage_note=current.storage_note,
    )
    return await save_cleanup_settings(updated)


__all__ = [
    "MemberCleanupSettings",
    "get_cleanup_settings",
    "save_cleanup_settings",
    "update_cleanup_settings",
]
