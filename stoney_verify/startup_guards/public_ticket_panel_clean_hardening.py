from __future__ import annotations

"""Runtime hardening for the clean public ticket panel.

This guard patches the single live public ticket panel module in-place at import
startup. It exists because ``public_ticket_panel_clean.py`` is the current live
panel path and should be folded directly after production verification.

Fixes:
- ticket numbers must never restart at 0001 while old ticket/closed channels or
  DB rows exist.
- the public category menu should stay clean, but still support server-specific
  categories that are intentionally useful, such as COD lobby support.
- clicking Create Ticket repeatedly should not spawn duplicate ephemeral category
  menus for the same member.
"""

import asyncio
import re
import time
from datetime import timezone
from typing import Any, Dict, List, Tuple

import discord

_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}
_MENU_OPEN_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}
_MENU_SESSION_UNTIL: Dict[Tuple[int, int], float] = {}
_MENU_SESSION_SECONDS = 45.0

_TICKET_NAME_RE = re.compile(r"^(?:ticket|closed)-(\d{1,8})$", re.I)
_TOPIC_NUMBER_RE = re.compile(r"(?:^|[;\s])ticket_number=(\d{1,8})(?:$|[;\s])", re.I)

_ALLOWED_MENU_KEYS = {"verification", "support", "cod_services", "report", "appeal", "bug", "question"}
_MENU_PRIORITY = {
    "verification": 0,
    "support": 1,
    "cod_services": 2,
    "report": 3,
    "appeal": 4,
    "bug": 5,
    "question": 6,
}
_MENU_LABELS = {
    "verification": "Verification",
    "support": "Support",
    "cod_services": "Call of Duty Services",
    "report": "Report a Member",
    "appeal": "Appeal",
    "bug": "Bug Report",
    "question": "Other Question",
}
_MENU_DESCRIPTIONS = {
    "verification": "Help with verification or approval issues.",
    "support": "General help from staff.",
    "cod_services": "Older COD lobby/service questions up to Black Ops 3.",
    "report": "Report scams, abuse, spam, raids, or rule breaks.",
    "appeal": "Appeal a ban, timeout, mute, or access restriction.",
    "bug": "Report a bot or server workflow issue.",
    "question": "Ask something that does not fit the other options.",
}
_DEFAULT_PUBLIC_ROWS: Tuple[Dict[str, Any], ...] = (
    {"slug": "verification", "name": "Verification", "description": _MENU_DESCRIPTIONS["verification"], "sort_order": 10},
    {"slug": "support", "name": "Support", "description": _MENU_DESCRIPTIONS["support"], "sort_order": 20, "is_default": True},
    {"slug": "cod_services", "name": "Call of Duty Services", "description": _MENU_DESCRIPTIONS["cod_services"], "sort_order": 25},
    {"slug": "report", "name": "Report a Member", "description": _MENU_DESCRIPTIONS["report"], "sort_order": 30},
    {"slug": "appeal", "name": "Appeal", "description": _MENU_DESCRIPTIONS["appeal"], "sort_order": 40},
    {"slug": "bug", "name": "Bug Report", "description": _MENU_DESCRIPTIONS["bug"], "sort_order": 50},
    {"slug": "question", "name": "Other Question", "description": _MENU_DESCRIPTIONS["question"], "sort_order": 60},
)


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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


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


def _menu_open_lock(guild_id: int, user_id: int) -> asyncio.Lock:
    key = (int(guild_id), int(user_id))
    lock = _MENU_OPEN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MENU_OPEN_LOCKS[key] = lock
    return lock


def _prune_menu_sessions() -> None:
    try:
        now = time.monotonic()
        expired = [key for key, until in _MENU_SESSION_UNTIL.items() if until <= now]
        for key in expired[:100]:
            _MENU_SESSION_UNTIL.pop(key, None)
            _MENU_OPEN_LOCKS.pop(key, None)
    except Exception:
        pass


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


def _raw_menu_text(row: Dict[str, Any]) -> str:
    parts = [
        row.get("slug"),
        row.get("category_slug"),
        row.get("name"),
        row.get("display_name"),
        row.get("button_label"),
        row.get("title"),
        row.get("description"),
        row.get("intake_type"),
    ]
    return " ".join(_safe_str(part).lower().replace("_", "-") for part in parts if _safe_str(part))


def _canonical_menu_key(panel_mod: Any, row: Dict[str, Any]) -> str:
    text = _raw_menu_text(row)

    if any(token in text for token in (
        "cod", "call of duty", "black ops", "bo1", "bo2", "bo3",
        "world at war", "waw", "modern warfare", "warzone", "zombies",
        "lobby", "lobbies", "modded", "unlock", "recovery",
    )):
        return "cod_services"

    if "verif" in text or "unverified" in text or "approval" in text:
        return "verification"
    if any(token in text for token in ("support", "general", "help")):
        return "support"
    if any(token in text for token in ("report", "scam", "raid", "abuse", "harass", "rule")):
        return "report"
    if any(token in text for token in ("appeal", "ban", "mute", "timeout", "unban", "blacklist")):
        return "appeal"
    if any(token in text for token in ("bug", "technical", "broken", "error", "glitch")):
        return "bug"
    if any(token in text for token in ("question", "other", "custom")):
        return "question"

    try:
        key = _safe_str(panel_mod._canon(row)).replace("-", "_")
        if key in _ALLOWED_MENU_KEYS:
            return key
    except Exception:
        pass

    return ""


def _menu_label(panel_mod: Any, key: str, row: Dict[str, Any]) -> str:
    if key in _MENU_LABELS:
        return _MENU_LABELS[key]
    try:
        return panel_mod._row_name(row)
    except Exception:
        return _safe_str(row.get("name"), "Support")[:100]


def _menu_description(panel_mod: Any, key: str, row: Dict[str, Any]) -> str:
    if key in _MENU_DESCRIPTIONS:
        return _MENU_DESCRIPTIONS[key]
    try:
        return panel_mod._row_desc(row)
    except Exception:
        return _safe_str(row.get("description"), "Open a support ticket.")[:100]


def _clean_public_rows(panel_mod: Any, raw: Any) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}

    for item in raw or []:
        if not isinstance(item, dict):
            continue
        if item.get("is_enabled") is False:
            continue

        key = _canonical_menu_key(panel_mod, item)
        if key not in _ALLOWED_MENU_KEYS:
            continue

        existing = by_key.get(key)
        if existing is None or panel_mod._row_sort(item) < panel_mod._row_sort(existing):
            row = dict(item)
            row["slug"] = key
            row["name"] = _menu_label(panel_mod, key, row)
            row["button_label"] = row["name"]
            row["description"] = _menu_description(panel_mod, key, row)
            row["sort_order"] = min(_safe_int(row.get("sort_order"), 999), (_MENU_PRIORITY.get(key, 99) + 1) * 10)
            by_key[key] = row

    # Fill any missing standard rows from the public defaults so the menu never
    # becomes half-old/half-new. COD Services is intentionally included here for
    # Stoney Balonney's older-COD lobby support flow.
    for default in _DEFAULT_PUBLIC_ROWS:
        key = _canonical_menu_key(panel_mod, default)
        if key in _ALLOWED_MENU_KEYS and key not in by_key:
            by_key[key] = dict(default)

    rows = list(by_key.values())
    rows.sort(key=lambda row: (_MENU_PRIORITY.get(_canonical_menu_key(panel_mod, row), 99), panel_mod._row_sort(row), _safe_str(row.get("name")).lower()))
    return rows[:25]


async def _load_rows(panel_mod: Any, guild: discord.Guild) -> Tuple[List[Dict[str, Any]], str]:
    sb = panel_mod._sb()
    if sb is None:
        return list(_DEFAULT_PUBLIC_ROWS), "Supabase client unavailable; using fallback categories."

    def sync() -> Tuple[List[Dict[str, Any]], str]:
        try:
            res = sb.table("ticket_categories").select("*").eq("guild_id", str(guild.id)).execute()
            raw = getattr(res, "data", None) or []
            found = _clean_public_rows(panel_mod, raw)
            if found:
                return found, ""
            return list(_DEFAULT_PUBLIC_ROWS), "No clean ticket menu rows found; using fallback categories."
        except Exception as e:
            return list(_DEFAULT_PUBLIC_ROWS), f"Could not read ticket_categories: {type(e).__name__}: {panel_mod._short(e, 220)}"

    return await panel_mod._to_thread(sync, (list(_DEFAULT_PUBLIC_ROWS), "Could not read ticket categories."))


async def _send_duplicate_menu_notice(interaction: discord.Interaction) -> None:
    content = "You already have a ticket type menu open. Use that menu, or dismiss it before opening another one."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _handle_panel_button(panel_mod: Any, original_handler: Any, interaction: discord.Interaction) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    if guild is None or member is None:
        return await original_handler(interaction)

    _prune_menu_sessions()
    key = (int(guild.id), int(member.id))
    now = time.monotonic()

    if _MENU_SESSION_UNTIL.get(key, 0.0) > now:
        return await _send_duplicate_menu_notice(interaction)

    lock = _menu_open_lock(guild.id, member.id)
    if lock.locked():
        return await _send_duplicate_menu_notice(interaction)

    async with lock:
        now = time.monotonic()
        if _MENU_SESSION_UNTIL.get(key, 0.0) > now:
            return await _send_duplicate_menu_notice(interaction)

        await original_handler(interaction)
        _MENU_SESSION_UNTIL[key] = time.monotonic() + _MENU_SESSION_SECONDS


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    if getattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", False):
        return True

    try:
        original_handler = getattr(panel_mod, "_handle_panel_button", None)
        if not callable(original_handler):
            _warn("public_ticket_panel_clean._handle_panel_button is not callable")
            return False

        panel_mod._ticket_num = _ticket_num
        panel_mod._rows = lambda raw: _clean_public_rows(panel_mod, raw)
        panel_mod._next_number = lambda guild, parent: _next_number(panel_mod, guild, parent)
        panel_mod._load_rows = lambda guild: _load_rows(panel_mod, guild)
        panel_mod._handle_panel_button = lambda interaction: _handle_panel_button(panel_mod, original_handler, interaction)
        setattr(panel_mod, "_PUBLIC_TICKET_PANEL_CLEAN_HARDENED", True)
        _log("patched clean panel number allocator, menu rows, COD option, and picker dedupe")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
