from __future__ import annotations

from typing import Any, Dict, Optional

import asyncio
from datetime import datetime, timedelta, timezone

from ..globals import get_supabase, now_utc


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        raw = _safe_str(value)
        if not raw:
            return None
        raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _settings_table():
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_creation_settings")


def _blacklist_table():
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_user_blacklist")


def _last_ticket_created_at_sync(guild_id: int, user_id: int) -> Optional[datetime]:
    sb = get_supabase()
    if not sb:
        return None

    try:
        res = (
            sb.table("tickets")
            .select("created_at")
            .eq("guild_id", str(int(guild_id)))
            .or_(f"owner_id.eq.{int(user_id)},user_id.eq.{int(user_id)}")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return _parse_iso(rows[0].get("created_at"))
    except Exception:
        return None
    return None


def _count_recent_tickets_sync(guild_id: int, user_id: int, minutes: int) -> int:
    sb = get_supabase()
    if not sb:
        return 0

    if minutes <= 0:
        return 0

    since = now_utc() - timedelta(minutes=int(minutes))
    try:
        res = (
            sb.table("tickets")
            .select("id", count="exact")
            .eq("guild_id", str(int(guild_id)))
            .or_(f"owner_id.eq.{int(user_id)},user_id.eq.{int(user_id)}")
            .gte("created_at", since.isoformat())
            .execute()
        )
        return int(getattr(res, "count", 0) or 0)
    except Exception:
        return 0


def _get_settings_sync(guild_id: int) -> Dict[str, Any]:
    try:
        res = (
            _settings_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return {}
    return {}


async def get_ticket_creation_settings(guild_id: int) -> Dict[str, Any]:
    return await asyncio.to_thread(_get_settings_sync, guild_id)


def _get_blacklist_row_sync(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _blacklist_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return None
    return None


async def get_ticket_blacklist_row(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_get_blacklist_row_sync, guild_id, user_id)


def _upsert_settings_sync(guild_id: int, patch: Dict[str, Any]) -> bool:
    payload = {"guild_id": str(int(guild_id)), **patch}
    try:
        _settings_table().upsert(payload, on_conflict="guild_id").execute()
        return True
    except Exception:
        return False


async def upsert_ticket_creation_settings(guild_id: int, patch: Dict[str, Any]) -> bool:
    return await asyncio.to_thread(_upsert_settings_sync, guild_id, patch)


def _upsert_blacklist_sync(guild_id: int, user_id: int, patch: Dict[str, Any]) -> bool:
    payload = {
        "guild_id": str(int(guild_id)),
        "user_id": str(int(user_id)),
        **patch,
    }
    try:
        _blacklist_table().upsert(payload, on_conflict="guild_id,user_id").execute()
        return True
    except Exception:
        return False


async def upsert_ticket_blacklist(guild_id: int, user_id: int, patch: Dict[str, Any]) -> bool:
    return await asyncio.to_thread(_upsert_blacklist_sync, guild_id, user_id, patch)


def _delete_blacklist_sync(guild_id: int, user_id: int) -> bool:
    try:
        _blacklist_table().delete().eq("guild_id", str(int(guild_id))).eq("user_id", str(int(user_id))).execute()
        return True
    except Exception:
        return False


async def delete_ticket_blacklist(guild_id: int, user_id: int) -> bool:
    return await asyncio.to_thread(_delete_blacklist_sync, guild_id, user_id)


async def evaluate_ticket_creation_guardrails(
    *,
    guild_id: int,
    user_id: int,
) -> Dict[str, Any]:
    settings = await get_ticket_creation_settings(guild_id)
    blacklist = await get_ticket_blacklist_row(guild_id, user_id)

    if blacklist and bool(blacklist.get("is_blocked", True)):
        return {
            "ok": False,
            "reason": _safe_str(blacklist.get("reason"), "You are blocked from creating tickets."),
            "source": "blacklist",
            "settings": settings,
            "blacklist": blacklist,
        }

    cooldown_seconds = max(0, _safe_int(settings.get("cooldown_seconds"), 0))
    max_tickets_per_window = max(0, _safe_int(settings.get("max_tickets_per_window"), 0))
    window_minutes = max(0, _safe_int(settings.get("window_minutes"), 0))

    if cooldown_seconds > 0:
        last_created = await asyncio.to_thread(_last_ticket_created_at_sync, guild_id, user_id)
        if last_created is not None:
            remaining = cooldown_seconds - int((now_utc() - last_created).total_seconds())
            if remaining > 0:
                return {
                    "ok": False,
                    "reason": f"You are on ticket cooldown. Try again in about {remaining} second(s).",
                    "source": "cooldown",
                    "remaining_seconds": remaining,
                    "settings": settings,
                    "blacklist": blacklist,
                }

    if max_tickets_per_window > 0 and window_minutes > 0:
        count = await asyncio.to_thread(_count_recent_tickets_sync, guild_id, user_id, window_minutes)
        if count >= max_tickets_per_window:
            return {
                "ok": False,
                "reason": f"You reached the ticket creation limit for the last {window_minutes} minute(s).",
                "source": "limit",
                "recent_count": count,
                "settings": settings,
                "blacklist": blacklist,
            }

    return {
        "ok": True,
        "reason": "",
        "source": "allow",
        "settings": settings,
        "blacklist": blacklist,
    }
