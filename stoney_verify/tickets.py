from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import discord

from .globals import *


# ============================================================
# Stoney Verify ticket helpers
# ------------------------------------------------------------
# IMPORTANT:
# - This file is aligned to YOUR custom ticket architecture.
# - TicketTool is NOT the source of truth anymore.
# - Authoritative sources for ticket scope/ownership are:
#     1) your bot-created DB ticket rows
#     2) your bot-written channel topic metadata
#     3) your configured category + anonymous channel naming
#
# Public compatibility functions are intentionally preserved
# because verify_ui.py / commands.py / transcripts.py import them.
# ============================================================


# Cache: channel_id -> owner_id
_TICKET_OWNER_CACHE: Dict[int, int] = {}

# Cache: channel_id -> webhook_url (or bot-endpoint fallback)
_WEBHOOK_URL_CACHE: Dict[int, str] = {}

# Channel readiness locks
_CHANNEL_READY_LOCKS: Dict[int, asyncio.Lock] = {}


# Accept:
#   ticket-0001
#   closed-0001
#   ticket-123
#   closed-123
_ANON_TICKET_NAME_RE = re.compile(r"^(ticket|closed)-(\d{1,8})$", re.I)

# Topic style from tickets_new/service.py:
#   owner_id=123;category=verify;ghost=false;ticket_number=12
_OWNER_ID_TOPIC_RE = re.compile(r"(?:^|;)owner_id=(\d+)(?:;|$)", re.I)
_TICKET_NUM_TOPIC_RE = re.compile(r"(?:^|;)ticket_number=(\d+)(?:;|$)", re.I)

# Legacy/general snowflake scrape fallback
_ANY_SNOWFLAKE_RE = re.compile(r"\b(\d{15,22})\b")


# ============================================================
# Small local helpers
# ============================================================

def _env_str_local(key: str, default: str = "") -> str:
    try:
        return (os.getenv(key, default) or default).strip()
    except Exception:
        return default


def _env_bool_local(key: str, default: bool = False) -> bool:
    v = _env_str_local(key, "")
    if not v:
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int_list_local(key: str) -> List[int]:
    raw = _env_str_local(key, "")
    if not raw:
        return []
    out: List[int] = []
    for part in re.split(r"[,\s]+", raw.strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return out


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value).strip()
    except Exception:
        return default


def _get_lock(channel_id: int) -> asyncio.Lock:
    cid = int(channel_id)
    lock = _CHANNEL_READY_LOCKS.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _CHANNEL_READY_LOCKS[cid] = lock
    return lock


def _target_id(target: Any) -> int:
    try:
        return int(getattr(target, "id", 0) or 0)
    except Exception:
        return 0


# Back-compat for transcripts.py
def _overwrite_target_id(target: Any) -> int:
    return _target_id(target)


def _channel_name_matches_ticket_prefix(name: str) -> bool:
    """
    Supports your anonymous ticket names while still honoring TICKET_PREFIX.

    Examples:
      TICKET_PREFIX=ticket
      - ticket-0001      -> True
      - closed-0001      -> True
      - ticket-1234      -> True
      - verify-0001      -> False (unless prefix changed)
    """
    n = (name or "").strip().lower()
    if not n:
        return False

    prefix = _safe_str(globals().get("TICKET_PREFIX"), "ticket").lower() or "ticket"

    # Current active tickets
    if n.startswith(f"{prefix}-"):
        return True

    # Closed anonymous tickets should still be treated as valid ticket channels
    # for transcripts, audit, reopen, etc.
    if n.startswith("closed-"):
        return True

    # Strong anonymous pattern fallback
    if _ANON_TICKET_NAME_RE.match(n):
        return True

    return False


# commands.py older compatibility import
def _name_matches_ticket_prefix(name: str) -> bool:
    return _channel_name_matches_ticket_prefix(name)


def _extract_owner_id_from_topic(topic: str) -> int:
    """
    Prefer your current topic format:
      owner_id=123;category=verify;ghost=false;ticket_number=12

    Fallback to any snowflake in older/other topic strings.
    """
    t = (topic or "").strip()
    if not t:
        return 0

    m = _OWNER_ID_TOPIC_RE.search(t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    m = _ANY_SNOWFLAKE_RE.search(t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    return 0


def _extract_ticket_number_from_topic(topic: str) -> int:
    t = (topic or "").strip()
    if not t:
        return 0

    m = _TICKET_NUM_TOPIC_RE.search(t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    return 0


def _extract_ticket_number_from_name(name: str) -> int:
    n = (name or "").strip().lower()
    if not n:
        return 0

    m = _ANON_TICKET_NAME_RE.match(n)
    if not m:
        return 0

    try:
        return int(m.group(2))
    except Exception:
        return 0


def _configured_ticket_category_ids() -> set[int]:
    ids: set[int] = set()

    try:
        if TICKET_CATEGORY_ID and int(TICKET_CATEGORY_ID) != 0:
            ids.add(int(TICKET_CATEGORY_ID))
    except Exception:
        pass

    try:
        for x in _env_int_list_local("EXTRA_TICKET_CATEGORY_IDS"):
            ids.add(int(x))
    except Exception:
        pass

    try:
        for x in (AUTO_TICKET_CATEGORY_IDS or set()):  # type: ignore[name-defined]
            try:
                ids.add(int(x))
            except Exception:
                continue
    except Exception:
        pass

    return ids


def _guild_matches(channel: discord.TextChannel, row: Dict[str, Any]) -> bool:
    try:
        row_gid = _safe_int(row.get("guild_id"), 0)
        if not row_gid:
            return True
        return int(channel.guild.id) == row_gid
    except Exception:
        return True


def _ticket_row_owner_id(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("user_id") or row.get("requester_id"), 0)


def _channel_id_candidates(channel: discord.TextChannel) -> List[str]:
    """
    Support both columns currently seen in your project:
      - discord_thread_id
      - channel_id
    """
    cid = str(int(channel.id))
    return [cid]


# ============================================================
# Supabase ticket lookup helpers
# ============================================================

def _sb() -> Any:
    try:
        return get_supabase()
    except Exception:
        return None


def _fetch_ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    """
    Look up the authoritative ticket row for this Discord channel.

    We try both columns because your codebase uses both:
      - tickets.discord_thread_id
      - tickets.channel_id
    """
    if not isinstance(channel, discord.TextChannel) or not channel.guild:
        return None

    sb = _sb()
    if sb is None:
        return None

    cid = str(int(channel.id))
    gid = str(int(channel.guild.id))

    queries = [
        ("discord_thread_id", cid),
        ("channel_id", cid),
    ]

    for col, value in queries:
        try:
            res = (
                sb.table("tickets")
                .select("*")
                .eq(col, value)
                .limit(1)
                .execute()
            )
            data = getattr(res, "data", None) or []
            if not data:
                continue
            row = data[0]
            if isinstance(row, dict) and _guild_matches(channel, row):
                return row
        except Exception:
            continue

    # Slightly stronger fallback:
    # if direct channel lookup failed, use anonymous number from topic/name
    # and owner_id from topic to avoid false negatives during migrations.
    try:
        owner_id = _extract_owner_id_from_topic(channel.topic or "")
        ticket_num = _extract_ticket_number_from_topic(channel.topic or "") or _extract_ticket_number_from_name(channel.name or "")
        if owner_id and ticket_num:
            res = (
                sb.table("tickets")
                .select("*")
                .eq("guild_id", gid)
                .eq("user_id", str(owner_id))
                .limit(10)
                .execute()
            )
            data = getattr(res, "data", None) or []
            for row in data:
                if not isinstance(row, dict):
                    continue
                row_name = _safe_str(row.get("channel_name"))
                row_cid = _safe_str(row.get("channel_id"))
                row_tid = _safe_str(row.get("discord_thread_id"))
                if row_cid == cid or row_tid == cid:
                    return row
                if row_name and row_name.lower() == (channel.name or "").lower():
                    return row
    except Exception:
        pass

    return None


def _db_confirms_ticket_scope(channel: discord.TextChannel) -> bool:
    try:
        row = _fetch_ticket_row_for_channel(channel)
        return isinstance(row, dict)
    except Exception:
        return False


# ============================================================
# Ticket scope detection
# ============================================================

def looks_like_tickettool_ticket(channel: discord.abc.GuildChannel) -> bool:
    """
    Compatibility helper kept because other code may still call it.

    In your current architecture, this is no longer authoritative.
    We now interpret it loosely as:
      "does this channel look like a ticket-like channel?"
    """
    if isinstance(channel, discord.Thread):
        try:
            parent = channel.parent
            if isinstance(parent, discord.TextChannel):
                return looks_like_tickettool_ticket(parent)
        except Exception:
            pass
        return _channel_name_matches_ticket_prefix(channel.name or "")

    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        if _channel_name_matches_ticket_prefix(channel.name or ""):
            return True
    except Exception:
        pass

    try:
        topic = channel.topic or ""
        if _extract_owner_id_from_topic(topic) or _extract_ticket_number_from_topic(topic):
            return True
    except Exception:
        pass

    try:
        if _db_confirms_ticket_scope(channel):
            return True
    except Exception:
        pass

    try:
        for target, ow in (channel.overwrites or {}).items():
            if isinstance(target, discord.Member) and ow.view_channel is True:
                return True
    except Exception:
        pass

    return False


def is_verification_ticket_channel(
    ch: Union[discord.TextChannel, discord.Thread, discord.abc.GuildChannel]
) -> bool:
    """
    AUTHORITATIVE ticket scope gate for YOUR custom architecture.

    Order of truth:
      1) DB ticket row exists for this channel
      2) Topic metadata contains your bot-written ticket fields
      3) Channel is under configured ticket category and matches anonymous naming
      4) Safe fallback heuristics

    This is intentionally stricter than the old TicketTool-ish version,
    but it still keeps enough fallback behavior to avoid breaking old open tickets.
    """
    if isinstance(ch, discord.Thread):
        try:
            parent = ch.parent
            if isinstance(parent, discord.TextChannel) and is_verification_ticket_channel(parent):
                return True
        except Exception:
            pass

        try:
            return _channel_name_matches_ticket_prefix(ch.name or "")
        except Exception:
            return False

    if not isinstance(ch, discord.TextChannel):
        return False

    # 1) DB row is the strongest signal
    try:
        if _db_confirms_ticket_scope(ch):
            return True
    except Exception:
        pass

    # 2) Topic metadata written by your service is also authoritative
    try:
        topic = ch.topic or ""
        owner_id = _extract_owner_id_from_topic(topic)
        ticket_num = _extract_ticket_number_from_topic(topic)
        if owner_id and ticket_num:
            cat_ids = _configured_ticket_category_ids()
            if not cat_ids:
                return True
            if int(getattr(ch, "category_id", 0) or 0) in cat_ids:
                return True
    except Exception:
        pass

    # 3) Configured category + anonymous ticket channel naming
    try:
        cat_ids = _configured_ticket_category_ids()
        cat_id = int(getattr(ch, "category_id", 0) or 0)
        if cat_ids and cat_id in cat_ids and _channel_name_matches_ticket_prefix(ch.name or ""):
            return True
    except Exception:
        pass

    # 4) Controlled fallback for older channels / migration cases
    try:
        if _channel_name_matches_ticket_prefix(ch.name or ""):
            topic = ch.topic or ""
            if _extract_owner_id_from_topic(topic) or _candidate_owner_ids_from_overwrites(ch):
                return True
    except Exception:
        pass

    return False


# ============================================================
# Owner detection
# ============================================================

def _candidate_owner_ids_from_overwrites(channel: discord.TextChannel) -> List[int]:
    """
    Collect candidate member IDs from explicit channel overwrites.

    We exclude roles and later prefer non-bot, non-staff members.
    """
    ids: List[int] = []
    try:
        for target, ow in (channel.overwrites or {}).items():
            if ow.view_channel is not True:
                continue
            if isinstance(target, discord.Role):
                continue
            tid = _target_id(target)
            if tid:
                ids.append(tid)
    except Exception:
        pass
    return ids


async def find_ticket_owner_retry(
    channel: discord.TextChannel,
    tries: int = 6,
    delay: float = 1.0,
) -> Optional[discord.Member]:
    """
    Robust owner detection for YOUR custom ticket flow.

    Order:
      1) cache
      2) tickets table row (user_id)
      3) channel topic owner_id=...
      4) overwrite candidates (prefer non-staff)
      5) lightweight message scan fallback

    Returns:
      discord.Member if still in guild
      None otherwise
    """
    if not isinstance(channel, discord.TextChannel) or not channel.guild:
        return None

    cid = int(channel.id)

    for i in range(max(1, int(tries or 1))):
        try:
            # 1) Cache
            cached = _TICKET_OWNER_CACHE.get(cid)
            if cached:
                mem = channel.guild.get_member(int(cached))
                if mem:
                    return mem

            # 2) DB ticket row
            try:
                row = _fetch_ticket_row_for_channel(channel)
                if isinstance(row, dict):
                    owner_id = _ticket_row_owner_id(row)
                    if owner_id:
                        mem = channel.guild.get_member(int(owner_id))
                        if mem:
                            _TICKET_OWNER_CACHE[cid] = int(owner_id)
                            return mem
            except Exception:
                pass

            # 3) Topic metadata
            try:
                owner_id = _extract_owner_id_from_topic(channel.topic or "")
                if owner_id:
                    mem = channel.guild.get_member(int(owner_id))
                    if mem:
                        _TICKET_OWNER_CACHE[cid] = int(owner_id)
                        return mem
            except Exception:
                pass

            # 4) Overwrites
            cand_ids = _candidate_owner_ids_from_overwrites(channel)

            best: Optional[discord.Member] = None
            for uid in cand_ids:
                try:
                    if bot.user and int(uid) == int(bot.user.id):
                        continue
                except Exception:
                    pass

                mem = channel.guild.get_member(int(uid))
                if not mem or getattr(mem, "bot", False):
                    continue

                try:
                    if is_staff(mem):
                        continue
                except Exception:
                    pass

                best = mem
                break

            if best:
                _TICKET_OWNER_CACHE[cid] = int(best.id)
                return best

            # fallback: first non-bot member candidate
            for uid in cand_ids:
                mem = channel.guild.get_member(int(uid))
                if not mem or getattr(mem, "bot", False):
                    continue
                _TICKET_OWNER_CACHE[cid] = int(mem.id)
                return mem

            # 5) Lightweight message scan
            try:
                async for m in channel.history(limit=20, oldest_first=True):
                    oid = 0

                    txt = (m.content or "").strip()
                    if txt:
                        oid = _extract_owner_id_from_topic(txt)
                        if not oid:
                            try:
                                mm = _ANY_SNOWFLAKE_RE.search(txt)
                                if mm:
                                    oid = int(mm.group(1))
                            except Exception:
                                oid = 0

                    if not oid and m.embeds:
                        try:
                            e = m.embeds[0]
                            blob = " ".join(
                                [
                                    str(e.title or ""),
                                    str(e.description or ""),
                                    " ".join(
                                        [f"{str(f.name or '')} {str(f.value or '')}" for f in (e.fields or [])]
                                    ),
                                ]
                            )
                            oid = _extract_owner_id_from_topic(blob)
                            if not oid:
                                mm = _ANY_SNOWFLAKE_RE.search(blob)
                                if mm:
                                    oid = int(mm.group(1))
                        except Exception:
                            oid = 0

                    if oid:
                        mem = channel.guild.get_member(int(oid))
                        if mem:
                            _TICKET_OWNER_CACHE[cid] = int(oid)
                            return mem
            except Exception:
                pass

        except Exception:
            pass

        if i < tries - 1:
            try:
                await asyncio.sleep(max(0.0, float(delay)))
            except Exception:
                break

    return None


# ============================================================
# Channel readiness / scope readiness
# ============================================================

async def wait_for_channel_ready(
    channel: discord.TextChannel,
    timeout_s: int = 25,
) -> bool:
    """
    Wait until the bot can view and send in the channel.
    """
    if not isinstance(channel, discord.TextChannel) or not channel.guild:
        return False

    lock = _get_lock(channel.id)
    async with lock:
        start = now_utc()

        while True:
            try:
                me = channel.guild.me or (channel.guild.get_member(int(bot.user.id)) if bot.user else None)
                if not me:
                    return False

                perms = channel.permissions_for(me)
                if perms.view_channel and perms.send_messages:
                    return True
            except Exception:
                pass

            if (now_utc() - start).total_seconds() >= float(timeout_s or 25):
                return False

            try:
                await asyncio.sleep(1.0)
            except Exception:
                return False


async def _wait_for_ticket_to_enter_scope(
    guild: discord.Guild,
    channel_id: int,
    timeout_s: int = 25,
    poll_s: float = 1.0,
) -> bool:
    """
    Poll until the channel is recognized as one of YOUR ticket channels.
    """
    if not guild:
        return False

    start = now_utc()

    while True:
        ch = guild.get_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel) and is_verification_ticket_channel(ch):
            return True

        if (now_utc() - start).total_seconds() >= float(timeout_s or 25):
            return False

        try:
            await asyncio.sleep(max(0.5, float(poll_s or 1.0)))
        except Exception:
            return False


async def ensure_ticket_ready_and_scoped(
    guild: discord.Guild,
    channel_id: int,
    timeout_s: int = 30,
) -> Optional[discord.TextChannel]:
    """
    One-stop helper:
      - waits for channel to be recognized as a valid ticket
      - waits for bot send/view readiness
      - returns the text channel or None
    """
    try:
        ok_scope = await _wait_for_ticket_to_enter_scope(guild, channel_id, timeout_s=timeout_s)
        if not ok_scope:
            return None

        ch = guild.get_channel(int(channel_id))
        if not isinstance(ch, discord.TextChannel):
            return None

        ok_ready = await wait_for_channel_ready(ch, timeout_s=timeout_s)
        return ch if ok_ready else None
    except Exception:
        return None


# ============================================================
# Webhook / postback helpers
# ============================================================

def _parse_webhook_id_from_url(url: str) -> int:
    u = (url or "").strip()
    if not u:
        return 0
    m = re.search(r"/webhooks/(\d{15,25})/", u)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0
    return 0


def _bot_actions_api_base() -> str:
    return _env_str_local("BOT_ACTIONS_API_URL", "").rstrip("/")


def _make_bot_postback_url(channel: discord.TextChannel) -> str:
    """
    Non-empty fallback for Supabase webhook_url constraint.
    """
    base = _bot_actions_api_base()
    if not base:
        return f"bot://channel/{int(channel.id)}"
    return f"{base}/api/ticket/postback?channel_id={int(channel.id)}"


async def get_or_create_webhook(channel: discord.TextChannel) -> Optional[str]:
    """
    Return a webhook URL for this ticket channel when possible.
    Keeps the same behavior your existing verification flow expects.
    """
    if not isinstance(channel, discord.TextChannel) or not channel.guild:
        return None

    cached = _WEBHOOK_URL_CACHE.get(int(channel.id))
    if cached and _parse_webhook_id_from_url(cached):
        return cached

    me = channel.guild.me or (channel.guild.get_member(int(bot.user.id)) if bot.user else None)
    if not me:
        return None

    perms = channel.permissions_for(me)
    if not perms.manage_webhooks:
        return None

    try:
        hooks = await channel.webhooks()
        for h in hooks:
            try:
                if bot.user and h.user and int(h.user.id) == int(bot.user.id):
                    url = str(h.url or "")
                    if url:
                        _WEBHOOK_URL_CACHE[int(channel.id)] = url
                        return url
            except Exception:
                continue
    except Exception:
        pass

    webhook_name = _env_str_local("WEBHOOK_NAME", "Stoney Verify Upload")
    try:
        wh = await channel.create_webhook(name=webhook_name)
        url = str(wh.url or "")
        if url:
            _WEBHOOK_URL_CACHE[int(channel.id)] = url
            return url
    except Exception:
        return None

    return None


async def ensure_postback_url(channel: discord.TextChannel) -> str:
    """
    Always returns a NON-EMPTY string for storing in Supabase.

    Preference order:
      1) cached webhook/postback url
      2) real Discord webhook
      3) bot://channel/<id> or BOT_ACTIONS_API_URL endpoint fallback
    """
    cached = _WEBHOOK_URL_CACHE.get(int(channel.id))
    if cached:
        return cached

    url = await get_or_create_webhook(channel)
    if url:
        _WEBHOOK_URL_CACHE[int(channel.id)] = url
        return url

    fallback = _make_bot_postback_url(channel)
    _WEBHOOK_URL_CACHE[int(channel.id)] = fallback
    return fallback


# ============================================================
# Mod quick-actions button parsing (compat)
# ============================================================

def parse_mod_id(custom_id: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Compatibility wrapper preserved for older imports.

    If globals.py defines parse_mod_id (preferred), delegate to it.
    Otherwise parse the legacy formats used elsewhere in the bot.
    """
    try:
        a, uid_int, extra = globals()["parse_mod_id"](custom_id)  # type: ignore[index]
        uid_str = str(uid_int) if uid_int else None
        return a, uid_str, extra
    except Exception:
        pass

    cid = (custom_id or "").strip()
    if not cid:
        return None, None, ""

    # qa|action|uid|extra
    if "|" in cid:
        parts = cid.split("|")
        if len(parts) >= 3:
            action = (parts[1] or "").strip().lower() or None
            uid = (parts[2] or "").strip() or None
            extra = (parts[3] or "").strip() if len(parts) >= 4 else ""
            if action and uid and re.fullmatch(r"\d{15,22}", uid):
                return action, uid, extra

    # mod:action:uid[:extra]
    parts = [p for p in cid.split(":") if p != ""]
    if len(parts) >= 3 and parts[0] == "mod":
        action = (parts[1] or "").strip().lower() or None
        uid = (parts[2] or "").strip() or None
        extra = ":".join(parts[3:]).strip() if len(parts) >= 4 else ""
        if action and uid and re.fullmatch(r"\d{15,22}", uid):
            return action, uid, extra

    # action:uid
    if len(parts) == 2:
        action = (parts[0] or "").strip().lower() or None
        uid = (parts[1] or "").strip() or None
        if action and uid and re.fullmatch(r"\d{15,22}", uid):
            return action, uid, ""

    return None, None, ""


# ============================================================
# Cache maintenance
# ============================================================

def clear_ticket_caches(channel_id: int) -> None:
    try:
        _TICKET_OWNER_CACHE.pop(int(channel_id), None)
    except Exception:
        pass

    try:
        _WEBHOOK_URL_CACHE.pop(int(channel_id), None)
    except Exception:
        pass

    try:
        _CHANNEL_READY_LOCKS.pop(int(channel_id), None)
    except Exception:
        pass


def clear_ticket_owner_cache(channel_id: int) -> None:
    try:
        _TICKET_OWNER_CACHE.pop(int(channel_id), None)
    except Exception:
        pass


def clear_ticket_webhook_cache(channel_id: int) -> None:
    try:
        _WEBHOOK_URL_CACHE.pop(int(channel_id), None)
    except Exception:
        pass