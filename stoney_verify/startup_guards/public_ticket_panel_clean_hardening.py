from __future__ import annotations

"""Runtime hardening for the clean public ticket panel.

This guard patches the single live public ticket panel module in-place at import
startup. It exists because ``public_ticket_panel_clean.py`` is the current live
panel path and should be folded directly after production verification.

Fixes:
- ticket numbers must never restart at 0001 while old ticket/closed channels or
  DB rows exist.
- the public category menu should stay TicketTool-simple and not show stale
  bootstrap/custom rows from older panel systems.
"""

import asyncio
import re
from datetime import timezone
from typing import Any, Dict, List, Tuple

import discord

_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}
_TICKET_NAME_RE = re.compile(r"^(?:ticket|closed)-(\d{1,8})$", re.I)
_TOPIC_NUMBER_RE = re.compile(r"(?:^|[;\s])ticket_number=(\d{1,8})(?:$|[;\s])", re.I)
_ALLOWED_MENU_KEYS = {"verification", "support", "report", "appeal", "bug", "question"}
_MENU_PRIORITY = {"verification": 0, "support": 1, "report": 2, "appeal": 3, "bug": 4, "question": 5}


def _log(message: str) -> None:
    try:
        print(f"✅ public_ticket_panel_clean_hardening: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_ticket_panel_clean_hardening: {message}")
    except Exception:
        pass


def _now_iso() -> str:
    return discord.utils.utcnow().astimezone(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _number_from_name(name: Any) -> int:
    text = str(name or "").strip().lower()
    if not text:
        return 0
    match = _TICKET_NAME_RE.match(text)
    if not match:
        return 0
    return _safe_int(match.group(1), 0)


def _number_from_topic(topic: Any) -> int:
    text = str(topic or "")
    if not text:
        return 0
    match = _TOPIC_NUMBER_RE.search(text)
    if not match:
        return 0
    return _safe_int(match.group(1), 0)


def _ticket_num(name: str) -> int:
    # Replacement for public_ticket_panel_clean._ticket_num.
    return _number_from_name(name)


def _number_lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _NUMBER_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _NUMBER_LOCKS[gid] = lock
    return lock


def _iter_unique_text_channels(guild: discord.Guild, parent: discord.CategoryChannel | None = None) -> List[discord.TextChannel]:
    seen: set[int] = set()
    out: List[discord.TextChannel] = []

    def add(ch: Any) -> None:
        try:
            if not isinstance(ch, discord.TextChannel):
                return
            cid = int(ch.id)
            if cid in seen:
                return
            seen.add(cid)
            out.append(ch)
        except Exception:
            return

    try:
        if isinstance(parent, discord.CategoryChannel):
            for ch in list(parent.text_channels):
                add(ch)
    except Exception:
        pass

    try:
        for ch in list(getattr(guild, "text_channels", []) or []):
            add(ch)
    except Exception:
        pass

    try:
        for cat in list(getattr(guild, "categories", []) or []):
            for ch in list(getattr(cat, "text_channels", []) or []):
                add(ch)
    except Exception:
        pass

    return out


def _highest_ticket_number_from_channels(guild: discord.Guild, parent: discord.CategoryChannel | None = None) -> int:
    highest = 0
    for ch in _iter_unique_text_channels(guild, parent):
        try:
            highest = max(
                highest,
                _number_from_name(getattr(ch, "name", "")),
                _number_from_topic(getattr(ch, "topic", "")),
            )
        except Exception:
            continue
    return highest


def _read_ticket_highest_sync(panel_mod: Any, guild_id: int) -> int:
    sb = panel_mod._sb()
    if sb is None:
        return 0
    try:
        data = getattr(
            sb.table("tickets")
            .select("ticket_number")
            .eq("guild_id", str(guild_id))
            .order("ticket_number", desc=True)
            .limit(1)
            .execute(),
            "data",
            None,
        ) or []
        if data and isinstance(data[0], dict):
            return _safe_int(data[0].get("ticket_number"), 0)
    except Exception as e:
        _warn(f"ticket highest read failed guild={guild_id}: {type(e).__name__}: {panel_mod._short(e, 220)}")
    return 0


def _read_counter_highest_sync(panel_mod: Any, guild_id: int) -> int:
    sb = panel_mod._sb()
    if sb is None:
        return 0
    try:
        data = getattr(
            sb.table("ticket_counters")
            .select("last_ticket_number")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute(),
            "data",
            None,
        ) or []
        if data and isinstance(data[0], dict):
            return _safe_int(data[0].get("last_ticket_number"), 0)
    except Exception as e:
        _warn(f"ticket counter read failed guild={guild_id}: {type(e).__name__}: {panel_mod._short(e, 220)}")
    return 0


def _persist_counter_sync(panel_mod: Any, guild_id: int, number: int) -> None:
    sb = panel_mod._sb()
    if sb is None:
        return
    try:
        sb.table("ticket_counters").upsert(
            {
                "guild_id": str(guild_id),
                "last_ticket_number": int(number),
                "updated_at": _now_iso(),
            },
            on_conflict="guild_id",
        ).execute()
    except Exception as e:
        _warn(f"ticket counter persist failed guild={guild_id} number={number}: {type(e).__name__}: {panel_mod._short(e, 220)}")


async def _next_number(panel_mod: Any, guild: discord.Guild, parent: discord.CategoryChannel) -> int:
    async with _number_lock(int(guild.id)):
        channel_highest = _highest_ticket_number_from_channels(guild, parent)
        db_highest = _safe_int(await panel_mod._to_thread(lambda: _read_ticket_highest_sync(panel_mod, int(guild.id)), 0), 0)
        counter_highest = _safe_int(await panel_mod._to_thread(lambda: _read_counter_highest_sync(panel_mod, int(guild.id)), 0), 0)

        number = max(channel_highest, db_highest, counter_highest, 0) + 1

        # Persist before channel creation. A skipped number is fine; duplicate
        # ticket numbers are not.
        await panel_mod._to_thread(lambda: _persist_counter_sync(panel_mod, int(guild.id), int(number)), None)

        try:
            _log(
                f"allocated ticket number guild={guild.id} number={number} "
                f"channels={channel_highest} tickets={db_highest} counter={counter_highest}"
            )
        except Exception:
            pass
        return int(number)


def _clean_public_rows(panel_mod: Any, raw: Any) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}

    for item in raw or []:
        if not isinstance(item, dict):
            continue
        if item.get("is_enabled") is False:
            continue

        key = panel_mod._canon(item)
        if key not in _ALLOWED_MENU_KEYS:
            continue

        existing = by_key.get(key)
        if existing is None or panel_mod._row_sort(item) < panel_mod._row_sort(existing):
            row = dict(item)
            row["slug"] = key
            row["name"] = panel_mod._canonical_label(key, str(row.get("name") or key))
            row["description"] = panel_mod._canonical_description(key, str(row.get("description") or ""))
            by_key[key] = row

    # Fill any missing standard rows from the boring defaults so the menu never
    # becomes half-old/half-new.
    for default in getattr(panel_mod, "DEFAULT_ROWS", ()):
        if not isinstance(default, dict):
            continue
        key = panel_mod._canon(default)
        if key in _ALLOWED_MENU_KEYS and key not in by_key:
            by_key[key] = dict(default)

    rows = list(by_key.values())
    rows.sort(key=lambda row: (_MENU_PRIORITY.get(panel_mod._canon(row), 99), panel_mod._row_sort(row), panel_mod._row_name(row).lower()))
    return rows[:25]


async def _load_rows(panel_mod: Any, guild: discord.Guild) -> Tuple[List[Dict[str, Any]], str]:
    sb = panel_mod._sb()
    if sb is None:
        return list(panel_mod.DEFAULT_ROWS), "Supabase client unavailable; using fallback categories."

    def sync() -> Tuple[List[Dict[str, Any]], str]:
        try:
            res = sb.table("ticket_categories").select("*").eq("guild_id", str(guild.id)).execute()
            raw = getattr(res, "data", None) or []
            found = _clean_public_rows(panel_mod, raw)
            if found:
                return found, ""
            return list(panel_mod.DEFAULT_ROWS), "No clean ticket menu rows found; using fallback categories."
        except Exception as e:
            return list(panel_mod.DEFAULT_ROWS), f"Could not read ticket_categories: {type(e).__name__}: {panel_mod._short(e, 220)}"

    return await panel_mod._to_thread(sync, (list(panel_mod.DEFAULT_ROWS), "Could not read ticket categories."))


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    if getattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", False):
        return True

    try:
        panel_mod._ticket_num = _ticket_num
        panel_mod._rows = lambda raw: _clean_public_rows(panel_mod, raw)
        panel_mod._next_number = lambda guild, parent: _next_number(panel_mod, guild, parent)
        panel_mod._load_rows = lambda guild: _load_rows(panel_mod, guild)
        setattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", True)
        _log("patched clean panel number allocator and public menu rows")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
