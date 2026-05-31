from __future__ import annotations

"""Keep external/legacy ticket-bot history from hijacking Dank Shield numbering.

Some servers migrate from TicketTool, TicketsBot, Helper.gg, custom bots, or old
manual imports. Those historic DB rows can have high ticket_number values, but
Dank Shield should not jump its own sequence just because imported history says
#0218 once existed.

Trusted sequence sources:
- current ticket_counters.last_ticket_number
- current Discord channels named ticket-#### / closed-####
- current Discord channel topics containing ticket_number=####
- DB ticket rows only when their channel_id/thread_id points to a real current
  Dank Shield ticket/closed channel

External/imported/legacy DB-only history remains visible in diagnostics, but it
is not allowed to control the next Dank Shield ticket number.
"""

import asyncio
import re
from datetime import timezone
from typing import Any, Dict, List, Tuple

import discord

_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}
_TICKET_NAME_RE = re.compile(r"^(?:ticket|closed)-(\d{1,8})$", re.I)
_TOPIC_NUMBER_RE = re.compile(r"(?:^|[;\s])ticket_number=(\d{1,8})(?:$|[;\s])", re.I)


def _log(message: str) -> None:
    try:
        print(f"✅ external_ticket_history_sequence_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ external_ticket_history_sequence_guard: {message}")
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
    try:
        match = _TICKET_NAME_RE.match(str(name or "").strip())
        return _safe_int(match.group(1), 0) if match else 0
    except Exception:
        return 0


def _number_from_topic(topic: Any) -> int:
    try:
        match = _TOPIC_NUMBER_RE.search(str(topic or ""))
        return _safe_int(match.group(1), 0) if match else 0
    except Exception:
        return 0


def _is_current_dank_ticket_channel(channel: Any) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if _number_from_name(getattr(channel, "name", "")) > 0:
        return True
    if _number_from_topic(getattr(channel, "topic", "")) > 0:
        return True
    return False


def _channel_number(channel: Any) -> int:
    if not isinstance(channel, discord.TextChannel):
        return 0
    return max(_number_from_name(getattr(channel, "name", "")), _number_from_topic(getattr(channel, "topic", "")))


def _iter_text_channels(guild: discord.Guild, parent: discord.CategoryChannel | None = None) -> List[discord.TextChannel]:
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

    return out


def _highest_current_channel_number(guild: discord.Guild, parent: discord.CategoryChannel | None = None) -> int:
    highest = 0
    for ch in _iter_text_channels(guild, parent):
        highest = max(highest, _channel_number(ch))
    return highest


def _lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _NUMBER_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _NUMBER_LOCKS[gid] = lock
    return lock


def _read_counter_sync(panel_mod: Any, guild_id: int) -> int:
    sb = panel_mod._sb()
    if sb is None:
        return 0
    try:
        rows = getattr(
            sb.table("ticket_counters")
            .select("last_ticket_number")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute(),
            "data",
            None,
        ) or []
        if rows and isinstance(rows[0], dict):
            return _safe_int(rows[0].get("last_ticket_number"), 0)
    except Exception as e:
        _warn(f"counter read failed guild={guild_id}: {type(e).__name__}: {panel_mod._short(e, 180)}")
    return 0


def _read_db_rows_sync(panel_mod: Any, guild_id: int) -> List[Dict[str, Any]]:
    sb = panel_mod._sb()
    if sb is None:
        return []
    try:
        rows = getattr(
            sb.table("tickets")
            .select("ticket_number,channel_id,discord_thread_id,status")
            .eq("guild_id", str(guild_id))
            .limit(1000)
            .execute(),
            "data",
            None,
        ) or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    except Exception as e:
        _warn(f"ticket row read failed guild={guild_id}: {type(e).__name__}: {panel_mod._short(e, 180)}")
        return []


def _trusted_db_highest(guild: discord.Guild, rows: List[Dict[str, Any]]) -> tuple[int, int, int]:
    trusted_highest = 0
    imported_highest = 0
    imported_count = 0

    for row in rows:
        number = _safe_int(row.get("ticket_number"), 0)
        if number <= 0:
            continue

        channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
        channel = guild.get_channel(channel_id) if channel_id > 0 else None

        if _is_current_dank_ticket_channel(channel):
            trusted_highest = max(trusted_highest, number, _channel_number(channel))
        else:
            imported_highest = max(imported_highest, number)
            imported_count += 1

    return trusted_highest, imported_highest, imported_count


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
        _warn(f"counter persist failed guild={guild_id} number={number}: {type(e).__name__}: {panel_mod._short(e, 180)}")


async def _next_number(panel_mod: Any, guild: discord.Guild, parent: discord.CategoryChannel) -> int:
    async with _lock(int(guild.id)):
        channel_highest = _highest_current_channel_number(guild, parent)
        counter_highest = _safe_int(await panel_mod._to_thread(lambda: _read_counter_sync(panel_mod, int(guild.id)), 0), 0)
        rows = await panel_mod._to_thread(lambda: _read_db_rows_sync(panel_mod, int(guild.id)), [])
        trusted_db_highest, imported_highest, imported_count = _trusted_db_highest(guild, rows)

        number = max(channel_highest, counter_highest, trusted_db_highest, 0) + 1
        await panel_mod._to_thread(lambda: _persist_counter_sync(panel_mod, int(guild.id), int(number)), None)

        if imported_highest > number - 1:
            _log(
                f"ignored external/imported ticket history guild={guild.id} "
                f"imported_highest={imported_highest} imported_rows={imported_count} "
                f"trusted_channels={channel_highest} trusted_db={trusted_db_highest} counter={counter_highest} next={number}"
            )
        else:
            _log(
                f"allocated Dank Shield ticket number guild={guild.id} next={number} "
                f"channels={channel_highest} trusted_db={trusted_db_highest} counter={counter_highest}"
            )
        return int(number)


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    if getattr(panel_mod, "_EXTERNAL_TICKET_HISTORY_SEQUENCE_GUARD_APPLIED", False):
        return True

    try:
        panel_mod._next_number = lambda guild, parent: _next_number(panel_mod, guild, parent)
        setattr(panel_mod, "_EXTERNAL_TICKET_HISTORY_SEQUENCE_GUARD_APPLIED", True)
        _log("patched numbering to ignore external/imported DB-only ticket history")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
