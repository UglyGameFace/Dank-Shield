from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc, reset_supabase
from ..verify_ui import post_or_replace_verify_ui
from .repository import (
    add_internal_note as repo_add_internal_note,
    assign_ticket as repo_assign_ticket,
    attach_transcript_to_ticket as repo_attach_transcript_to_ticket,
    create_ticket_record as repo_create_ticket_record,
    find_open_ticket_for_owner as repo_find_open_ticket_for_owner,
    get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
    list_internal_notes as repo_list_internal_notes,
    list_open_tickets_for_guild as repo_list_open_tickets_for_guild,
    mark_ticket_closed as repo_mark_ticket_closed,
    mark_ticket_deleted as repo_mark_ticket_deleted,
    reopen_ticket as repo_reopen_ticket,
    safe_optional_update_by_channel_id as repo_safe_optional_update_by_channel_id,
    set_ticket_priority as repo_set_ticket_priority,
    sync_ticket_record_from_channel as repo_sync_ticket_record_from_channel,
    transfer_ticket as repo_transfer_ticket,
    unclaim_ticket as repo_unclaim_ticket,
)
from .orphan_safety import cleanup_unpersisted_ticket_channel
from ..services.server_design_ticket_naming import build_ticket_channel_name

try:
    from .event_service import (
        log_ticket_claimed,
        log_ticket_closed,
        log_ticket_created,
        log_ticket_deleted,
        log_ticket_note_added,
        log_ticket_priority_updated,
        log_ticket_reopened,
        log_ticket_transcript_attached,
        log_ticket_transferred,
        log_ticket_unclaimed,
    )
except Exception:
    async def log_ticket_created(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_claimed(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_unclaimed(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_transferred(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_priority_updated(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_note_added(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_closed(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_reopened(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_deleted(*args, **kwargs) -> bool:  # type: ignore
        return False

    async def log_ticket_transcript_attached(*args, **kwargs) -> bool:  # type: ignore
        return False


_TICKET_CREATION_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}
_TICKET_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}
_TICKET_MUTATION_LOCKS: Dict[str, asyncio.Lock] = {}
_TRANSCRIPT_ATTACH_LOCKS: Dict[str, asyncio.Lock] = {}
_LOCK_LAST_USED: Dict[str, float] = {}
_LOCK_CLEANUP_INTERVAL_SECONDS = 600
_LAST_LOCK_CLEANUP_AT = 0.0
_GLOBAL_FALLBACK_WARNINGS: set[tuple[str, str, str]] = set()

TICKET_NUM_RE = re.compile(r"^(?:ticket|closed)-(\d+)$", re.I)

_VALID_TICKET_PRIORITIES = {"low", "medium", "high", "urgent"}
_VALID_TICKET_STATUSES = {"open", "claimed", "closed", "deleted"}


def _service_debug(msg: str) -> None:
    try:
        print(f"🧩 ticket_service {msg}")
    except Exception:
        pass


def _warn_global_ticket_fallback(
    *,
    guild_id: Optional[int],
    kind: str,
    key: str,
    value: Any,
) -> None:
    try:
        gid = str(int(guild_id)) if guild_id else "unknown"
    except Exception:
        gid = "unknown"

    warn_key = (gid, str(kind), str(key))
    if warn_key in _GLOBAL_FALLBACK_WARNINGS:
        return
    _GLOBAL_FALLBACK_WARNINGS.add(warn_key)

    try:
        print(
            "⚠️ ticket_service legacy global fallback used "
            f"kind={kind} guild={gid} key={key} value={value!r}. "
            "Prefer GuildContext/per-guild config before public release."
        )
    except Exception:
        pass


def _touch_lock_key(key: str) -> None:
    try:
        _LOCK_LAST_USED[str(key)] = time.monotonic()
    except Exception:
        pass


def _cleanup_stale_locks_if_needed() -> None:
    global _LAST_LOCK_CLEANUP_AT

    now_mono = time.monotonic()
    if (now_mono - _LAST_LOCK_CLEANUP_AT) < _LOCK_CLEANUP_INTERVAL_SECONDS:
        return

    _LAST_LOCK_CLEANUP_AT = now_mono

    try:
        for gid, lock in list(_TICKET_NUMBER_LOCKS.items()):
            key = f"number:{gid}"
            if lock.locked():
                continue
            last_used = float(_LOCK_LAST_USED.get(key, 0.0) or 0.0)
            if last_used > 0.0 and (now_mono - last_used) > _LOCK_CLEANUP_INTERVAL_SECONDS:
                _TICKET_NUMBER_LOCKS.pop(gid, None)
                _LOCK_LAST_USED.pop(key, None)

        for pair, lock in list(_TICKET_CREATION_LOCKS.items()):
            key = f"create:{pair[0]}:{pair[1]}"
            if lock.locked():
                continue
            last_used = float(_LOCK_LAST_USED.get(key, 0.0) or 0.0)
            if last_used > 0.0 and (now_mono - last_used) > _LOCK_CLEANUP_INTERVAL_SECONDS:
                _TICKET_CREATION_LOCKS.pop(pair, None)
                _LOCK_LAST_USED.pop(key, None)

        for bucket_name, bucket in (
            ("mutate", _TICKET_MUTATION_LOCKS),
            ("transcript", _TRANSCRIPT_ATTACH_LOCKS),
        ):
            for channel_key, lock in list(bucket.items()):
                key = f"{bucket_name}:{channel_key}"
                if lock.locked():
                    continue
                last_used = float(_LOCK_LAST_USED.get(key, 0.0) or 0.0)
                if last_used > 0.0 and (now_mono - last_used) > _LOCK_CLEANUP_INTERVAL_SECONDS:
                    bucket.pop(channel_key, None)
                    _LOCK_LAST_USED.pop(key, None)
    except Exception:
        pass


def _ticket_number_lock(guild_id: int) -> asyncio.Lock:
    _cleanup_stale_locks_if_needed()
    gid = int(guild_id)
    lock = _TICKET_NUMBER_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _TICKET_NUMBER_LOCKS[gid] = lock
    _touch_lock_key(f"number:{gid}")
    return lock


def _guild_user_ticket_creation_lock(guild_id: int, user_id: int) -> asyncio.Lock:
    _cleanup_stale_locks_if_needed()
    key = (int(guild_id), int(user_id))
    lock = _TICKET_CREATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TICKET_CREATION_LOCKS[key] = lock
    _touch_lock_key(f"create:{key[0]}:{key[1]}")
    return lock


def _channel_lock(bucket: Dict[str, asyncio.Lock], bucket_name: str, channel_id: int | str) -> asyncio.Lock:
    _cleanup_stale_locks_if_needed()
    key = str(channel_id)
    lock = bucket.get(key)
    if lock is None:
        lock = asyncio.Lock()
        bucket[key] = lock
    _touch_lock_key(f"{bucket_name}:{key}")
    return lock


def _ticket_mutation_lock(channel_id: int | str) -> asyncio.Lock:
    return _channel_lock(_TICKET_MUTATION_LOCKS, "mutate", channel_id)


def _transcript_attach_lock(channel_id: int | str) -> asyncio.Lock:
    return _channel_lock(_TRANSCRIPT_ATTACH_LOCKS, "transcript", channel_id)


def _counter_table_name() -> str:
    return "ticket_counters"


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_opt_text(value: Any) -> Optional[str]:
    try:
        text = str(value or "").strip()
        return text or None
    except Exception:
        return None


def _safe_opt_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_ticket_priority(value: Any, default: str = "medium") -> str:
    text = _safe_str(value).strip().lower()
    return text if text in _VALID_TICKET_PRIORITIES else default


def _normalize_ticket_status(value: Any, default: str = "open") -> str:
    text = _safe_str(value).strip().lower()
    aliases = {
        "active": "open",
        "reopened": "open",
    }
    text = aliases.get(text, text)
    return text if text in _VALID_TICKET_STATUSES else default


def _sb() -> Any:
    try:
        return get_supabase()
    except Exception:
        return None


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
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
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor, max_attempts: int = 5):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise

    raise last_error


async def _run_db_op(op_name: str, executor, max_attempts: int = 5):
    return await asyncio.to_thread(_execute_db_op, op_name, executor, max_attempts)


async def _ticket_row_for_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(channel_id)
        if isinstance(row, dict):
            return row
    except Exception as e:
        _service_debug(f"ticket-row lookup failed channel={channel_id} error={repr(e)}")
    return None


async def _readback_ticket_state(channel_id: int | str) -> Tuple[Optional[Dict[str, Any]], str]:
    row = await _ticket_row_for_channel_id(channel_id)
    return row, _ticket_status(row)


async def _sync_channel_name_in_db(
    channel: discord.TextChannel,
    *,
    ticket_number: Optional[int] = None,
) -> None:
    payload: Dict[str, Any] = {"channel_name": channel.name}
    if ticket_number is not None:
        payload["ticket_number"] = int(ticket_number)

    try:
        await repo_safe_optional_update_by_channel_id(channel.id, payload)
    except Exception:
        pass


async def _resolve_text_channel_from_row(
    guild: discord.Guild,
    row: Optional[Dict[str, Any]],
) -> Optional[discord.TextChannel]:
    if not isinstance(row, dict):
        return None

    channel_id = 0
    try:
        channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    except Exception:
        channel_id = 0

    if channel_id <= 0:
        return None

    try:
        existing_channel = guild.get_channel(channel_id)
        if isinstance(existing_channel, discord.TextChannel):
            return existing_channel
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(channel_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


async def _repair_stale_open_ticket_row(
    *,
    row: Dict[str, Any],
    owner: discord.Member,
) -> None:
    existing_channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    if existing_channel_id <= 0:
        return

    try:
        await repo_mark_ticket_deleted(
            channel_id=existing_channel_id,
            reason="Stale open ticket row repaired during ticket creation",
        )
        print(
            f"🧹 Repaired stale open ticket row for owner={owner.id} "
            f"missing_channel={existing_channel_id}"
        )
    except Exception as e:
        print(f"⚠️ Failed repairing stale open ticket row {existing_channel_id}: {repr(e)}")


def _actor_id(actor: Optional[discord.Member | discord.User]) -> Optional[int]:
    try:
        if actor is None:
            return None
        return int(actor.id)
    except Exception:
        return None


def _actor_name(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
    try:
        if actor is None:
            return None
        return str(actor)
    except Exception:
        return None


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "unknown"
    try:
        return _normalize_ticket_status(row.get("status"), "unknown")
    except Exception:
        return "unknown"


def _ticket_claimed_by_id(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("assigned_to", "claimed_by"):
        try:
            value = int(str(row.get(key) or "0") or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_owner_id(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("user_id", "owner_id", "requester_id"):
        try:
            value = int(str(row.get(key) or "0") or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_is_openish(row: Optional[Dict[str, Any]]) -> bool:
    return _ticket_status(row) in {"open", "claimed"}


def _ticket_is_deleted(row: Optional[Dict[str, Any]]) -> bool:
    return _ticket_status(row) == "deleted"


def _ticket_is_closed(row: Optional[Dict[str, Any]]) -> bool:
    return _ticket_status(row) == "closed"


def _row_is_ghost(row: Optional[Dict[str, Any]], default: bool = False) -> bool:
    if not isinstance(row, dict):
        return default
    try:
        return _safe_bool(row.get("is_ghost"), default)
    except Exception:
        return default


def _actor_is_elevated_staff(actor: Optional[discord.Member | discord.User]) -> bool:
    try:
        if actor is None:
            return False
        if not isinstance(actor, discord.Member):
            return False
        if actor.guild_permissions.administrator:
            return True
        if actor.guild_permissions.manage_channels:
            return True
        if actor.guild_permissions.manage_guild:
            return True

        staff_role_id = _safe_int(globals().get("STAFF_ROLE_ID"), 0)
        if staff_role_id and any(int(r.id) == staff_role_id for r in actor.roles):
            return True
    except Exception:
        return False
    return False


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        try:
            value = _safe_int(globals().get(key), 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_active_category_id() -> int:
    try:
        return _safe_int(globals().get("TICKET_CATEGORY_ID"), 0)
    except Exception:
        return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).strip().lower()
    if not text:
        return False

    markers = (
        "archive",
        "archived",
        "ticket archive",
        "tickets archive",
        "archived tickets",
        "closed tickets",
    )
    return any(marker in text for marker in markers)


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        ch = guild.get_channel(int(category_id))
        if isinstance(ch, discord.CategoryChannel):
            return ch
    except Exception:
        pass
    return None


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _resolve_active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_active_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit
    return None


def _channel_is_in_category(
    channel: discord.TextChannel,
    category: Optional[discord.CategoryChannel],
) -> bool:
    try:
        if category is None:
            return False
        return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
    except Exception:
        return False


async def _move_ticket_to_archive_if_configured(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category is None:
        return False

    if _channel_is_in_category(channel, archive_category):
        return True

    try:
        await channel.edit(
            category=archive_category,
            sync_permissions=False,
            reason="Ticket closed -> move to archive category",
        )
        return True
    except Exception as e:
        print(f"⚠️ Failed moving ticket {channel.id} to archive category: {repr(e)}")
        return False


async def _move_ticket_to_active_if_configured(channel: discord.TextChannel) -> bool:
    active_category = _resolve_active_ticket_category(channel.guild)
    if active_category is None:
        return False

    if _channel_is_in_category(channel, active_category):
        return True

    try:
        await channel.edit(
            category=active_category,
            sync_permissions=False,
            reason="Ticket reopened -> move to active category",
        )
        return True
    except Exception as e:
        print(f"⚠️ Failed moving ticket {channel.id} to active category: {repr(e)}")
        return False


def _channel_lifecycle_location(channel: discord.TextChannel) -> str:
    try:
        if _channel_is_in_category(channel, _resolve_archive_category(channel.guild)):
            return f"archive:{channel.category.name if channel.category else 'unknown'}"
        if channel.category is not None:
            return f"category:{channel.category.name}"
    except Exception:
        pass
    return "uncategorized"


def _priority_rank(value: Any) -> int:
    normalized = _normalize_ticket_priority(value, "medium")
    ranks = {
        "urgent": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return ranks.get(normalized, 2)


def _normalize_queue_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)

    try:
        out["ticket_status"] = _ticket_status(row)
    except Exception:
        out["ticket_status"] = "unknown"

    try:
        out["claimed_by_id"] = _ticket_claimed_by_id(row)
    except Exception:
        out["claimed_by_id"] = 0

    try:
        out["owner_user_id"] = _ticket_owner_id(row)
    except Exception:
        out["owner_user_id"] = 0

    try:
        out["is_unclaimed"] = bool(out["ticket_status"] == "open" and out["claimed_by_id"] <= 0)
    except Exception:
        out["is_unclaimed"] = False

    try:
        out["is_claimed"] = bool(out["ticket_status"] == "claimed" and out["claimed_by_id"] > 0)
    except Exception:
        out["is_claimed"] = False

    try:
        out["priority_rank"] = _priority_rank(out.get("priority"))
    except Exception:
        out["priority_rank"] = 2

    return out


def _category_metadata_payload(
    *,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    category_override: bool = False,
    category_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "matched_category_id": _safe_opt_text(matched_category_id),
        "matched_category_name": _safe_opt_text(matched_category_name),
        "matched_category_slug": _safe_opt_text(matched_category_slug),
        "matched_intake_type": _safe_opt_text(matched_intake_type),
        "matched_category_reason": _safe_opt_text(matched_category_reason),
        "matched_category_score": _safe_opt_int(matched_category_score),
        "category_override": bool(category_override),
        "category_id": _safe_opt_text(category_id),
    }
    return payload


async def _cancel_verification_wait_timers_safe(guild_id: int, owner_id: int) -> bool:
    try:
        from ..commands import cancel_verification_wait_timers_for_member
    except Exception:
        return False

    try:
        return bool(await cancel_verification_wait_timers_for_member(int(guild_id), int(owner_id)))
    except Exception as e:
        print(
            f"⚠️ Failed cancelling verification wait timers "
            f"guild={guild_id} owner={owner_id}: {repr(e)}"
        )
        return False


def _title_for_ticket(owner: discord.abc.User, category: str, is_ghost: bool) -> str:
    base_name = (
        getattr(owner, "display_name", None)
        or getattr(owner, "name", None)
        or str(owner)
    )
    prefix = "[GHOST] " if is_ghost else ""
    return f"{prefix}{category.title()} - {base_name}"[:180]


def _extract_ticket_number_from_name(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    m = TICKET_NUM_RE.match(str(name).strip().lower())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _format_ticket_channel_name(number: int, closed: bool = False) -> str:
    prefix = "closed" if closed else "ticket"
    return f"{prefix}-{int(number):04d}"


async def _server_design_ticket_options(guild_id: int) -> Optional[Dict[str, Any]]:
    """Load Dank Design options only when the server has actually saved them."""

    try:
        from ..guild_config import get_guild_config

        cfg = await get_guild_config(int(guild_id), refresh=True)
        raw = cfg.get("server_design_studio_options") if isinstance(cfg, dict) else None
        return dict(raw) if isinstance(raw, dict) and raw else None
    except Exception:
        return None


async def _format_ticket_channel_name_for_guild(
    guild: discord.Guild,
    number: int,
    *,
    closed: bool = False,
    parent: Optional[discord.CategoryChannel] = None,
) -> str:
    plain = _format_ticket_channel_name(number, closed=closed)
    options = await _server_design_ticket_options(int(guild.id))
    if not options:
        return plain

    try:
        parent_id = int(parent.id) if parent is not None else None
    except Exception:
        parent_id = None

    return build_ticket_channel_name(
        int(number),
        closed=closed,
        options=options,
        parent_category_id=parent_id,
    )


def _topic_for_ticket(
    *,
    owner_id: int,
    category: str,
    is_ghost: bool,
    ticket_number: int,
) -> str:
    return (
        f"owner_id={owner_id};"
        f"category={category};"
        f"ghost={str(bool(is_ghost)).lower()};"
        f"ticket_number={int(ticket_number)}"
    )


def _parse_ticket_number_from_topic(channel: discord.TextChannel) -> Optional[int]:
    try:
        topic = channel.topic or ""
        m = re.search(r"(?:^|;)ticket_number=(\d+)(?:;|$)", topic)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


def _parse_owner_id_from_topic(channel: discord.TextChannel) -> Optional[int]:
    try:
        topic = channel.topic or ""
        m = re.search(r"(?:^|;)owner_id=(\d+)(?:;|$)", topic)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


async def _resolve_owner_member_from_channel_or_row(
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> Optional[discord.Member]:
    owner_id = _ticket_owner_id(row)
    if owner_id <= 0:
        try:
            owner_id = _parse_owner_id_from_topic(channel) or 0
        except Exception:
            owner_id = 0

    if owner_id <= 0:
        return None

    try:
        member = channel.guild.get_member(owner_id)
        if member:
            return member
    except Exception:
        pass

    try:
        fetched = await channel.guild.fetch_member(owner_id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    return None


def _resolve_ticket_parent_category(
    guild: discord.Guild,
    explicit_parent_category_id: Optional[int] = None,
) -> Optional[discord.CategoryChannel]:
    ids_to_try: list[tuple[int, str, str]] = []

    if explicit_parent_category_id:
        ids_to_try.append((
            _safe_int(explicit_parent_category_id, 0),
            "explicit",
            "explicit_parent_category_id",
        ))

    try:
        env_id = _safe_int(globals().get("TICKET_CATEGORY_ID"), 0)
        if env_id:
            ids_to_try.append((env_id, "global", "TICKET_CATEGORY_ID"))
    except Exception:
        pass

    seen: set[int] = set()
    for cid, source, key in ids_to_try:
        if not cid or cid in seen:
            continue
        seen.add(cid)
        try:
            maybe = guild.get_channel(int(cid))
            if isinstance(maybe, discord.CategoryChannel):
                if source == "global":
                    _warn_global_ticket_fallback(
                        guild_id=int(guild.id),
                        kind="ticket_parent_category",
                        key=key,
                        value=cid,
                    )
                return maybe
        except Exception:
            continue

    return None


def _ticket_number_missing_error(exc: Exception) -> bool:
    text = repr(exc or "").lower()
    return (
        "ticket_number" in text
        and (
            "pgrst204" in text
            or "schema cache" in text
            or "column" in text
            or "does not exist"
        )
    )


def _db_max_ticket_number(guild_id: int) -> int:
    sb = _sb()
    if sb is None:
        return 0

    try:
        resp = (
            sb.table("tickets")
            .select("ticket_number")
            .eq("guild_id", str(guild_id))
            .order("ticket_number", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            return _safe_int(rows[0].get("ticket_number"), 0)
    except Exception as e:
        if _ticket_number_missing_error(e):
            return 0

    return 0


def _channel_scan_max_ticket_number(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
) -> int:
    max_num = 0

    try:
        candidates = list(parent.channels) if parent else list(guild.channels)
    except Exception:
        candidates = list(guild.channels)

    for ch in candidates:
        if not isinstance(ch, discord.TextChannel):
            continue

        n = _extract_ticket_number_from_name(ch.name)
        if n is not None and n > max_num:
            max_num = n
            continue

        n2 = _parse_ticket_number_from_topic(ch)
        if n2 is not None and n2 > max_num:
            max_num = n2

    return max_num


def _get_counter_row(guild_id: int) -> Optional[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        return None

    try:
        resp = (
            sb.table(_counter_table_name())
            .select("*")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0]
    except Exception:
        pass

    return None


def _ensure_counter_seed(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
) -> int:
    sb = _sb()
    if sb is None:
        return max(
            _db_max_ticket_number(int(guild.id)),
            _channel_scan_max_ticket_number(guild, parent=parent),
        )

    guild_id = int(guild.id)
    row = _get_counter_row(guild_id)
    historical = max(
        _db_max_ticket_number(guild_id),
        _channel_scan_max_ticket_number(guild, parent=parent),
    )

    if row:
        current = _safe_int(row.get("last_ticket_number"), 0)
        if historical > current:
            try:
                (
                    sb.table(_counter_table_name())
                    .update(
                        {
                            "last_ticket_number": int(historical),
                            "updated_at": _utc_iso(now_utc()),
                        }
                    )
                    .eq("guild_id", str(guild_id))
                    .eq("last_ticket_number", current)
                    .execute()
                )
                return historical
            except Exception:
                return max(current, historical)
        return current

    try:
        sb.table(_counter_table_name()).upsert(
            {
                "guild_id": str(guild_id),
                "last_ticket_number": int(historical),
                "updated_at": _utc_iso(now_utc()),
            },
            on_conflict="guild_id",
        ).execute()
    except TypeError:
        try:
            sb.table(_counter_table_name()).upsert(
                {
                    "guild_id": str(guild_id),
                    "last_ticket_number": int(historical),
                    "updated_at": _utc_iso(now_utc()),
                }
            ).execute()
        except Exception:
            pass
    except Exception:
        pass

    return historical


async def _reserve_next_ticket_number(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
    *,
    max_retries: int = 20,
) -> int:
    guild_id = int(guild.id)
    lock = _ticket_number_lock(guild_id)

    async with lock:
        sb = _sb()
        if sb is None:
            return max(
                _db_max_ticket_number(guild_id),
                _channel_scan_max_ticket_number(guild, parent=parent),
            ) + 1

        await _run_db_op(
            "seed ticket counter",
            lambda: _ensure_counter_seed(guild, parent=parent),
        )

        for attempt in range(1, max_retries + 1):
            row = await _run_db_op(
                "read ticket counter row",
                lambda: _get_counter_row(guild_id),
            )

            if not row:
                await _run_db_op(
                    "re-seed ticket counter",
                    lambda: _ensure_counter_seed(guild, parent=parent),
                )
                await asyncio.sleep(min(0.05 * attempt, 0.5))
                continue

            current = _safe_int(row.get("last_ticket_number"), 0)
            new_value = current + 1

            try:
                await _run_db_op(
                    "advance ticket counter",
                    lambda: (
                        sb.table(_counter_table_name())
                        .update(
                            {
                                "last_ticket_number": int(new_value),
                                "updated_at": _utc_iso(now_utc()),
                            }
                        )
                        .eq("guild_id", str(guild_id))
                        .eq("last_ticket_number", int(current))
                        .execute()
                    ),
                )

                verify = await _run_db_op(
                    "verify ticket counter",
                    lambda: _get_counter_row(guild_id),
                )
                if verify and _safe_int(verify.get("last_ticket_number"), 0) == new_value:
                    return new_value
            except Exception:
                pass

            await asyncio.sleep(min(0.05 * attempt, 0.5))

        row = await _run_db_op(
            "final read ticket counter row",
            lambda: _get_counter_row(guild_id),
        )
        if row:
            current = _safe_int(row.get("last_ticket_number"), 0)
            return current + 1

        return max(
            _db_max_ticket_number(guild_id),
            _channel_scan_max_ticket_number(guild, parent=parent),
        ) + 1


async def _next_ticket_number(
    guild: discord.Guild,
    parent: Optional[discord.CategoryChannel] = None,
) -> int:
    return await _reserve_next_ticket_number(guild, parent=parent)


def _build_overwrites(
    guild: discord.Guild,
    owner: discord.Member,
    *,
    staff_role_ids: Optional[list[int]] = None,
    closed: bool = False,
) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    owner_send = not closed

    overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=owner_send,
            attach_files=owner_send,
            embed_links=owner_send,
            read_message_history=True,
        ),
    }

    if staff_role_ids:
        for role_id in staff_role_ids:
            role = guild.get_role(int(role_id))
            if role is None:
                continue
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

    return overwrites


def _default_staff_role_ids(guild_id: Optional[int] = None) -> list[int]:
    ids: list[int] = []

    for key in ("STAFF_ROLE_ID", "MOD_ROLE_ID", "ADMIN_ROLE_ID"):
        try:
            value = globals().get(key)
            rid = _safe_int(value, 0)
            if rid and rid not in ids:
                ids.append(rid)
        except Exception:
            continue

    if ids:
        _warn_global_ticket_fallback(
            guild_id=guild_id,
            kind="ticket_staff_roles",
            key="STAFF_ROLE_ID/MOD_ROLE_ID/ADMIN_ROLE_ID",
            value=",".join(str(x) for x in ids),
        )

    return ids


async def _apply_closed_permissions(
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    *,
    staff_role_ids: Optional[list[int]] = None,
) -> None:
    guild = channel.guild
    overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    if owner:
        overwrites[owner] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            read_message_history=True,
        )

    roles = staff_role_ids or _default_staff_role_ids(guild_id=guild.id)
    for role_id in roles:
        role = guild.get_role(int(role_id))
        if role is None:
            continue
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        )

    try:
        await channel.edit(overwrites=overwrites, reason="Ticket closed; owner reply locked")
    except Exception as e:
        print(f"⚠️ Failed applying closed permissions for {channel.id}: {repr(e)}")


async def _apply_open_permissions(
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    *,
    staff_role_ids: Optional[list[int]] = None,
) -> None:
    if owner is None:
        return

    guild = channel.guild
    overwrites = _build_overwrites(
        guild,
        owner,
        staff_role_ids=staff_role_ids or _default_staff_role_ids(guild_id=guild.id),
        closed=False,
    )
    try:
        await channel.edit(overwrites=overwrites, reason="Ticket reopened; owner reply restored")
    except Exception as e:
        print(f"⚠️ Failed applying open permissions for {channel.id}: {repr(e)}")


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _should_auto_post_verification(owner: discord.Member, *, is_ghost: bool) -> bool:
    try:
        if is_ghost:
            return False
        if getattr(owner, "bot", False):
            return False

        uv_id = int(UNVERIFIED_ROLE_ID or 0)
        verified_id = int(VERIFIED_ROLE_ID or 0)
        resident_id = int(RESIDENT_ROLE_ID or 0)
        staff_id = int(STAFF_ROLE_ID or 0)

        if staff_id and _member_has_role_id(owner, staff_id):
            return False
        if verified_id and _member_has_role_id(owner, verified_id):
            return False
        if resident_id and _member_has_role_id(owner, resident_id):
            return False
        if uv_id and _member_has_role_id(owner, uv_id):
            return True

        return False
    except Exception:
        return False


async def _maybe_post_verification_ui(
    *,
    channel: discord.TextChannel,
    owner: discord.Member,
    is_ghost: bool,
) -> None:
    try:
        if not _should_auto_post_verification(owner, is_ghost=is_ghost):
            print(
                f"🧩 verification UI skipped -> ticket={channel.id} owner={owner.id} "
                f"ghost={is_ghost}"
            )
            return

        post_result = await post_or_replace_verify_ui(
            channel,
            requester_id=int(owner.id),
            reason="ticket_created",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=True,
        )

        print(
            f"🧩 verification UI result -> `{post_result or 'none'}` "
            f"ticket={channel.id} owner={owner.id}"
        )
    except Exception as e:
        print("⚠️ verification UI failed:", repr(e))


async def _post_or_replace_open_ticket_controls_safe(channel: discord.TextChannel) -> None:
    try:
        from ..transcripts import post_or_replace_open_ticket_controls
    except Exception as e:
        print(f"⚠️ open ticket controls import failed for {channel.id}: {repr(e)}")
        return

    try:
        await post_or_replace_open_ticket_controls(channel)
    except Exception as e:
        print(f"⚠️ open ticket controls update failed for {channel.id}: {repr(e)}")


async def _freeze_open_ticket_controls_safe(
    channel: discord.TextChannel,
    *,
    closed_by: Optional[discord.Member | discord.User] = None,
) -> None:
    try:
        from ..transcripts import _freeze_open_controls_message
    except Exception:
        return

    try:
        await _freeze_open_controls_message(channel, closed_by=closed_by)
    except Exception as e:
        print(f"⚠️ failed freezing open controls for {channel.id}: {repr(e)}")


async def _post_staff_closed_message_safe(
    channel: discord.TextChannel,
    *,
    closed_by: Optional[discord.Member | discord.User] = None,
) -> None:
    if closed_by is None:
        return

    try:
        from ..transcripts import _post_staff_closed_message
    except Exception:
        return

    try:
        await _post_staff_closed_message(channel, closed_by)
    except Exception as e:
        print(f"⚠️ failed posting staff closed panel for {channel.id}: {repr(e)}")


async def _freeze_staff_closed_message_safe(
    channel: discord.TextChannel,
    *,
    reopened_by: Optional[discord.Member | discord.User] = None,
) -> None:
    try:
        from ..transcripts import _find_staff_closed_message, _freeze_message_controls
    except Exception:
        return

    try:
        msg = await _find_staff_closed_message(channel)
        if not msg:
            return
        suffix = "🔓 Ticket reopened."
        if reopened_by is not None:
            try:
                suffix = f"🔓 Reopened by {reopened_by.mention}."
            except Exception:
                suffix = "🔓 Ticket reopened."
        await _freeze_message_controls(msg, content_suffix=suffix)
    except Exception as e:
        print(f"⚠️ failed freezing staff closed message for {channel.id}: {repr(e)}")


async def _refresh_open_ticket_ui(
    *,
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    is_ghost: bool,
) -> None:
    if isinstance(owner, discord.Member):
        try:
            await _maybe_post_verification_ui(
                channel=channel,
                owner=owner,
                is_ghost=is_ghost,
            )
        except Exception:
            pass

    try:
        await _post_or_replace_open_ticket_controls_safe(channel)
    except Exception:
        pass


def _row_channel_id(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)


def _safe_owner_mention(owner: Optional[discord.Member]) -> str:
    try:
        if isinstance(owner, discord.Member):
            mention = str(owner.mention or "").strip()
            if mention and "unknown-user" not in mention.lower():
                return mention
    except Exception:
        pass
    return ""


def _contains_bad_owner_placeholder(text: str) -> bool:
    lowered = _safe_str(text).lower()
    bad_markers = (
        "@unknown-user",
        "unknown-user",
        "<@0>",
        "<@!0>",
        "ticket owner: unknown",
    )
    return any(marker in lowered for marker in bad_markers)


def _build_default_opening_message(
    *,
    owner: discord.Member,
    category: str,
    channel_name: str,
) -> str:
    owner_mention = _safe_owner_mention(owner)
    welcome_prefix = f"{owner_mention} " if owner_mention else ""
    lines = [
        f"🎫 {welcome_prefix}welcome to your verification ticket.",
        f"Ticket: `{channel_name}`",
        f"Category: `{category}`",
        "Please complete verification using the panel below.",
    ]
    return "\n".join(lines)


def _sanitize_opening_message(
    *,
    opening_message: Optional[str],
    owner: discord.Member,
    category: str,
    channel_name: str,
) -> str:
    candidate = _safe_str(opening_message).strip()
    if not candidate:
        return _build_default_opening_message(
            owner=owner,
            category=category,
            channel_name=channel_name,
        )

    if _contains_bad_owner_placeholder(candidate):
        return _build_default_opening_message(
            owner=owner,
            category=category,
            channel_name=channel_name,
        )

    return candidate


def _canonical_ticket_category(category: str, is_ghost: bool) -> str:
    clean = _safe_str(category).strip().lower() or "support"
    return "ghost" if is_ghost else clean


def _channel_name_exists(
    guild: discord.Guild,
    *,
    channel_name: str,
    parent: Optional[discord.CategoryChannel] = None,
    exclude_channel_id: Optional[int] = None,
) -> bool:
    try:
        channels = list(parent.channels) if parent else list(guild.channels)
    except Exception:
        channels = list(guild.channels)

    for ch in channels:
        try:
            if exclude_channel_id and int(getattr(ch, "id", 0) or 0) == int(exclude_channel_id):
                continue
            if isinstance(ch, discord.TextChannel) and str(ch.name).lower() == str(channel_name).lower():
                return True
        except Exception:
            continue
    return False


async def _reserve_unique_ticket_number_and_name(
    guild: discord.Guild,
    *,
    parent: Optional[discord.CategoryChannel] = None,
    max_attempts: int = 25,
) -> Tuple[int, str]:
    for _ in range(max_attempts):
        number = await _next_ticket_number(guild, parent=parent)
        name = await _format_ticket_channel_name_for_guild(guild, number, closed=False, parent=parent)
        if not _channel_name_exists(guild, channel_name=name, parent=parent):
            return number, name

    fallback = max(
        _db_max_ticket_number(int(guild.id)),
        _channel_scan_max_ticket_number(guild, parent=parent),
    ) + 1
    return fallback, await _format_ticket_channel_name_for_guild(guild, fallback, closed=False, parent=parent)


async def _ensure_channel_identity(
    *,
    channel: discord.TextChannel,
    owner: discord.Member,
    category: str,
    is_ghost: bool,
    ticket_number: Optional[int],
    closed: bool = False,
) -> int:
    resolved_number = ticket_number or _extract_ticket_number_from_name(channel.name) or _parse_ticket_number_from_topic(channel) or 0

    if resolved_number <= 0:
        resolved_number = await _next_ticket_number(channel.guild, parent=channel.category)

    desired_topic = _topic_for_ticket(
        owner_id=int(owner.id),
        category=_canonical_ticket_category(category, is_ghost),
        is_ghost=is_ghost,
        ticket_number=resolved_number,
    )

    desired_name = await _format_ticket_channel_name_for_guild(
        channel.guild,
        resolved_number,
        closed=closed,
        parent=channel.category,
    )

    try:
        edits: Dict[str, Any] = {}
        if channel.topic != desired_topic:
            edits["topic"] = desired_topic
        if channel.name != desired_name and not _channel_name_exists(
            channel.guild,
            channel_name=desired_name,
            parent=channel.category,
            exclude_channel_id=channel.id,
        ):
            edits["name"] = desired_name
        if edits:
            await channel.edit(reason="Normalize ticket channel identity", **edits)
    except Exception as e:
        print(f"⚠️ Failed normalizing ticket channel identity for {channel.id}: {repr(e)}")

    await _sync_channel_name_in_db(channel, ticket_number=resolved_number)
    return resolved_number


async def _sync_existing_open_ticket_channel(
    *,
    channel: discord.TextChannel,
    owner: discord.Member,
    category: str,
    source: str,
    is_ghost: bool,
    clean_priority: str,
    category_meta: Dict[str, Any],
    opening_message: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    existing_row_before = await _ticket_row_for_channel_id(channel.id)
    current_status = _ticket_status(existing_row_before)
    if current_status not in {"open", "claimed"}:
        current_status = "open"

    normalized_ticket_number = await _ensure_channel_identity(
        channel=channel,
        owner=owner,
        category=category,
        is_ghost=is_ghost,
        ticket_number=_extract_ticket_number_from_name(channel.name) or _parse_ticket_number_from_topic(channel),
        closed=False,
    )

    try:
        await _apply_open_permissions(
            channel,
            owner,
            staff_role_ids=_default_staff_role_ids(guild_id=channel.guild.id),
        )
    except Exception:
        pass

    try:
        await _move_ticket_to_active_if_configured(channel)
    except Exception:
        pass

    try:
        row = await repo_sync_ticket_record_from_channel(
            channel=channel,
            owner_id=owner.id,
            username=str(owner),
            title=_title_for_ticket(owner, category, is_ghost),
            category=_canonical_ticket_category(category, is_ghost),
            status=current_status,
            priority=clean_priority,
            initial_message=_safe_str(opening_message or ""),
            ticket_number=normalized_ticket_number,
            is_ghost=is_ghost,
            source=source,
            matched_category_id=category_meta["matched_category_id"],
            matched_category_name=category_meta["matched_category_name"],
            matched_category_slug=category_meta["matched_category_slug"],
            matched_intake_type=category_meta["matched_intake_type"],
            matched_category_reason=category_meta["matched_category_reason"],
            matched_category_score=category_meta["matched_category_score"],
            category_override=bool(category_meta["category_override"]),
            category_id=category_meta["category_id"],
        )
        return row if isinstance(row, dict) else None
    except Exception as e:
        print(f"⚠️ Existing ticket sync failed for {channel.id}: {repr(e)}")
        return None


async def _safe_log_created(
    *,
    guild_id: int,
    owner: discord.Member,
    channel_id: int,
    ticket_row: Optional[Dict[str, Any]],
    source: str,
    ticket_number: int,
    category: str,
    is_ghost: bool,
    clean_priority: str,
    category_meta: Dict[str, Any],
    lifecycle_location: Optional[str] = None,
) -> None:
    try:
        await log_ticket_created(
            guild_id=guild_id,
            actor_user_id=owner.id,
            actor_name=str(owner),
            channel_id=channel_id,
            ticket_row=ticket_row,
            source=source,
            metadata={
                "ticket_number": ticket_number,
                "category": category,
                "is_ghost": bool(is_ghost),
                "priority": clean_priority,
                "matched_category_id": category_meta["matched_category_id"],
                "matched_category_name": category_meta["matched_category_name"],
                "matched_category_slug": category_meta["matched_category_slug"],
                "matched_intake_type": category_meta["matched_intake_type"],
                "matched_category_reason": category_meta["matched_category_reason"],
                "matched_category_score": category_meta["matched_category_score"],
                "category_override": bool(category_meta["category_override"]),
                "category_id": category_meta["category_id"],
                "lifecycle_location": lifecycle_location,
            },
        )
    except Exception as e:
        print(f"⚠️ Failed logging ticket_created for {channel_id}: {repr(e)}")


async def find_open_ticket_for_owner(
    *,
    guild_id: int | str,
    owner_id: int | str,
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return await repo_find_open_ticket_for_owner(
        guild_id=guild_id,
        owner_id=owner_id,
        category=category,
    )


async def create_ticket_channel(
    *,
    guild: discord.Guild,
    owner: discord.Member,
    category: str,
    source: str = "discord_button",
    is_ghost: bool = False,
    parent_category_id: Optional[int] = None,
    staff_role_ids: Optional[list[int]] = None,
    opening_message: Optional[str] = None,
    priority: str = "medium",
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    category_override: bool = False,
    category_id: Optional[str] = None,
) -> Optional[discord.TextChannel]:
    user_lock = _guild_user_ticket_creation_lock(guild.id, owner.id)
    clean_priority = _normalize_ticket_priority(priority, "medium")

    category_meta = _category_metadata_payload(
        matched_category_id=matched_category_id,
        matched_category_name=matched_category_name,
        matched_category_slug=matched_category_slug,
        matched_intake_type=matched_intake_type,
        matched_category_reason=matched_category_reason,
        matched_category_score=matched_category_score,
        category_override=category_override,
        category_id=category_id,
    )

    async with user_lock:
        existing_row = await repo_find_open_ticket_for_owner(
            guild_id=guild.id,
            owner_id=owner.id,
            category=None,
        )

        if existing_row:
            existing_channel = await _resolve_text_channel_from_row(guild, existing_row)
            if isinstance(existing_channel, discord.TextChannel):
                try:
                    cancelled = await _cancel_verification_wait_timers_safe(guild.id, owner.id)
                    if cancelled:
                        print(
                            f"⏹️ Cancelled verification wait timer(s) for owner={owner.id} "
                            f"because existing ticket {existing_channel.id} was reused."
                        )
                except Exception:
                    pass

                await _sync_existing_open_ticket_channel(
                    channel=existing_channel,
                    owner=owner,
                    category=_safe_str(existing_row.get("category") or category),
                    source=_safe_str(existing_row.get("source") or source),
                    is_ghost=_safe_bool(existing_row.get("is_ghost"), is_ghost),
                    clean_priority=_normalize_ticket_priority(existing_row.get("priority"), clean_priority),
                    category_meta={
                        "matched_category_id": _safe_opt_text(existing_row.get("matched_category_id")) or category_meta["matched_category_id"],
                        "matched_category_name": _safe_opt_text(existing_row.get("matched_category_name")) or category_meta["matched_category_name"],
                        "matched_category_slug": _safe_opt_text(existing_row.get("matched_category_slug")) or category_meta["matched_category_slug"],
                        "matched_intake_type": _safe_opt_text(existing_row.get("matched_intake_type")) or category_meta["matched_intake_type"],
                        "matched_category_reason": _safe_opt_text(existing_row.get("matched_category_reason")) or category_meta["matched_category_reason"],
                        "matched_category_score": _safe_opt_int(existing_row.get("matched_category_score")) or category_meta["matched_category_score"],
                        "category_override": bool(existing_row.get("category_override", category_meta["category_override"])),
                        "category_id": _safe_opt_text(existing_row.get("category_id")) or category_meta["category_id"],
                    },
                    opening_message=opening_message,
                )

                await _refresh_open_ticket_ui(
                    channel=existing_channel,
                    owner=owner,
                    is_ghost=_safe_bool(existing_row.get("is_ghost"), is_ghost),
                )
                _service_debug(
                    f"reused-existing-ticket guild={guild.id} owner={owner.id} channel={existing_channel.id}"
                )
                return existing_channel

            await _repair_stale_open_ticket_row(row=existing_row, owner=owner)

        parent = _resolve_ticket_parent_category(
            guild,
            explicit_parent_category_id=parent_category_id,
        )

        all_staff_role_ids = list(staff_role_ids or [])
        for rid in _default_staff_role_ids(guild_id=guild.id):
            if rid not in all_staff_role_ids:
                all_staff_role_ids.append(rid)

        ticket_number, channel_name = await _reserve_unique_ticket_number_and_name(
            guild,
            parent=parent,
        )
        overwrites = _build_overwrites(
            guild,
            owner,
            staff_role_ids=all_staff_role_ids,
            closed=False,
        )

        try:
            if not callable(getattr(guild, "create_text_channel", None)):
                raise TypeError("guild.create_text_channel is not callable")

            channel = await guild.create_text_channel(
                name=channel_name,
                category=parent,
                overwrites=overwrites,
                topic=_topic_for_ticket(
                    owner_id=owner.id,
                    category=_canonical_ticket_category(category, is_ghost),
                    is_ghost=is_ghost,
                    ticket_number=ticket_number,
                ),
                reason=f"Ticket created for {owner} ({owner.id})",
            )
        except Exception as e:
            print("❌ Failed to create ticket channel:", repr(e))
            return None

        try:
            cancelled = await _cancel_verification_wait_timers_safe(guild.id, owner.id)
            if cancelled:
                print(
                    f"⏹️ Cancelled verification wait timer(s) for owner={owner.id} "
                    f"after creating ticket {channel.id}."
                )
        except Exception:
            pass

        intro_message = _sanitize_opening_message(
            opening_message=opening_message,
            owner=owner,
            category=category,
            channel_name=channel_name,
        )

        try:
            await channel.send(
                intro_message,
                allowed_mentions=discord.AllowedMentions(
                    users=[owner],
                    roles=False,
                    everyone=False,
                ),
            )
        except Exception as e:
            print("⚠️ Failed sending opening ticket message:", repr(e))

        inserted = await repo_create_ticket_record(
            guild_id=guild.id,
            owner_id=owner.id,
            username=str(owner),
            title=_title_for_ticket(owner, category, is_ghost),
            channel_id=channel.id,
            discord_thread_id=channel.id,
            channel_name=channel.name,
            category=_canonical_ticket_category(category, is_ghost),
            status="open",
            priority=clean_priority,
            initial_message=intro_message,
            ticket_number=ticket_number,
            is_ghost=is_ghost,
            source=source,
            matched_category_id=category_meta["matched_category_id"],
            matched_category_name=category_meta["matched_category_name"],
            matched_category_slug=category_meta["matched_category_slug"],
            matched_intake_type=category_meta["matched_intake_type"],
            matched_category_reason=category_meta["matched_category_reason"],
            matched_category_score=category_meta["matched_category_score"],
            category_override=bool(category_meta["category_override"]),
            category_id=category_meta["category_id"],
        )

        if inserted is None:
            try:
                inserted = await repo_sync_ticket_record_from_channel(
                    channel=channel,
                    owner_id=owner.id,
                    username=str(owner),
                    title=_title_for_ticket(owner, category, is_ghost),
                    category=_canonical_ticket_category(category, is_ghost),
                    status="open",
                    priority=clean_priority,
                    initial_message=intro_message,
                    ticket_number=ticket_number,
                    is_ghost=is_ghost,
                    source=source,
                    matched_category_id=category_meta["matched_category_id"],
                    matched_category_name=category_meta["matched_category_name"],
                    matched_category_slug=category_meta["matched_category_slug"],
                    matched_intake_type=category_meta["matched_intake_type"],
                    matched_category_reason=category_meta["matched_category_reason"],
                    matched_category_score=category_meta["matched_category_score"],
                    category_override=bool(category_meta["category_override"]),
                    category_id=category_meta["category_id"],
                )
            except Exception as e:
                print(f"⚠️ Ticket fallback sync failed for {channel.id}: {repr(e)}")

        if inserted is None:
            cleaned = await cleanup_unpersisted_ticket_channel(
                channel,
                owner_id=owner.id,
                ticket_number=ticket_number,
                reason="Ticket creation rolled back because DB insert and fallback sync failed.",
            )
            print(
                "⚠️ Ticket channel created but DB insert/sync failed; "
                f"orphan_cleanup={'deleted' if cleaned else 'kept_or_failed'} "
                f"channel={channel.id} owner={owner.id} ticket_number={ticket_number}"
            )
            return None
        else:
            print(
                f"✅ Ticket row inserted/upserted → #{channel.name} ({channel.id}) "
                f"[ticket_number={ticket_number}] category={category!r} "
                f"matched_slug={category_meta['matched_category_slug']!r} "
                f"matched_name={category_meta['matched_category_name']!r} "
                f"matched_score={category_meta['matched_category_score']!r}"
            )

        ticket_row = inserted
        if ticket_row is None:
            ticket_row = await _ticket_row_for_channel_id(channel.id)

        await _sync_channel_name_in_db(channel, ticket_number=ticket_number)

        await _safe_log_created(
            guild_id=guild.id,
            owner=owner,
            channel_id=channel.id,
            ticket_row=ticket_row,
            source=source,
            ticket_number=ticket_number,
            category=category,
            is_ghost=is_ghost,
            clean_priority=clean_priority,
            category_meta=category_meta,
            lifecycle_location=_channel_lifecycle_location(channel),
        )

        await _refresh_open_ticket_ui(
            channel=channel,
            owner=owner,
            is_ghost=is_ghost,
        )
        _service_debug(
            f"create success guild={guild.id} owner={owner.id} channel={channel.id} ticket_number={ticket_number}"
        )
        return channel


async def sync_existing_ticket_channel(
    *,
    channel: discord.TextChannel,
    owner: Optional[discord.Member | discord.User],
    category: str,
    source: str = "sync",
    is_ghost: bool = False,
    status: str = "open",
    priority: str = "medium",
    initial_message: Optional[str] = None,
    matched_category_id: Optional[str] = None,
    matched_category_name: Optional[str] = None,
    matched_category_slug: Optional[str] = None,
    matched_intake_type: Optional[str] = None,
    matched_category_reason: Optional[str] = None,
    matched_category_score: Optional[int] = None,
    category_override: bool = False,
    category_id: Optional[str] = None,
) -> bool:
    owner_for_row = owner
    if owner_for_row is None:
        print("⚠️ sync_existing_ticket_channel called without owner; using bot placeholder.")
        owner_for_row = channel.guild.me or channel.guild.owner

    if owner_for_row is None:
        return False

    ticket_number = _extract_ticket_number_from_name(channel.name)
    if ticket_number is None:
        ticket_number = _parse_ticket_number_from_topic(channel)

    clean_priority = _normalize_ticket_priority(priority, "medium")
    clean_status = _normalize_ticket_status(status, "open")

    if isinstance(owner_for_row, discord.Member):
        ticket_number = await _ensure_channel_identity(
            channel=channel,
            owner=owner_for_row,
            category=category,
            is_ghost=is_ghost,
            ticket_number=ticket_number,
            closed=(clean_status == "closed"),
        )

    category_meta = _category_metadata_payload(
        matched_category_id=matched_category_id,
        matched_category_name=matched_category_name,
        matched_category_slug=matched_category_slug,
        matched_intake_type=matched_intake_type,
        matched_category_reason=matched_category_reason,
        matched_category_score=matched_category_score,
        category_override=category_override,
        category_id=category_id,
    )

    try:
        row = await repo_sync_ticket_record_from_channel(
            channel=channel,
            owner_id=owner_for_row.id,
            username=str(owner_for_row),
            title=_title_for_ticket(owner_for_row, category, is_ghost),
            category=_canonical_ticket_category(category, is_ghost),
            status=clean_status,
            priority=clean_priority,
            initial_message=initial_message or "",
            ticket_number=ticket_number,
            is_ghost=is_ghost,
            source=source,
            matched_category_id=category_meta["matched_category_id"],
            matched_category_name=category_meta["matched_category_name"],
            matched_category_slug=category_meta["matched_category_slug"],
            matched_intake_type=category_meta["matched_intake_type"],
            matched_category_reason=category_meta["matched_category_reason"],
            matched_category_score=category_meta["matched_category_score"],
            category_override=bool(category_meta["category_override"]),
            category_id=category_meta["category_id"],
        )
        ok = row is not None
    except Exception as e:
        print(f"❌ Failed syncing existing ticket row for {channel.id}: {repr(e)}")
        ok = False

    try:
        if owner is not None:
            await _cancel_verification_wait_timers_safe(channel.guild.id, owner.id)
        else:
            owner_id = _parse_owner_id_from_topic(channel)
            if owner_id:
                await _cancel_verification_wait_timers_safe(channel.guild.id, owner_id)
    except Exception:
        pass

    if ok:
        try:
            owner_member = owner if isinstance(owner, discord.Member) else None
            if clean_status in {"open", "claimed"}:
                await _apply_open_permissions(
                    channel,
                    owner_member,
                    staff_role_ids=_default_staff_role_ids(guild_id=channel.guild.id),
                )
                await _move_ticket_to_active_if_configured(channel)
                await _refresh_open_ticket_ui(
                    channel=channel,
                    owner=owner_member,
                    is_ghost=is_ghost,
                )
            elif clean_status == "closed":
                await _apply_closed_permissions(
                    channel,
                    owner_member,
                    staff_role_ids=_default_staff_role_ids(guild_id=channel.guild.id),
                )
                await _move_ticket_to_archive_if_configured(channel)
        except Exception:
            pass

        await _sync_channel_name_in_db(channel, ticket_number=ticket_number)

        print(
            f"✅ Existing ticket row synced → #{channel.name} ({channel.id}) "
            f"category={category!r} matched_slug={category_meta['matched_category_slug']!r}"
        )

    return ok


async def mark_ticket_closed(
    *,
    channel: discord.TextChannel,
    closed_by: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel.id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel.id)
        status_before = _ticket_status(row_before)

        if status_before == "deleted":
            _service_debug(f"close rejected channel={channel.id} reason=already-deleted")
            return False

        owner_member = await _resolve_owner_member_from_channel_or_row(channel, row_before)
        ticket_number = _extract_ticket_number_from_name(channel.name)
        if ticket_number is None:
            ticket_number = _parse_ticket_number_from_topic(channel)

        repo_close_ok = True
        if status_before != "closed":
            try:
                repo_close_ok = await repo_mark_ticket_closed(
                    channel_id=channel.id,
                    closed_by=getattr(closed_by, "id", None) if closed_by else None,
                    closed_by_name=_actor_name(closed_by),
                    reason=reason,
                )
            except Exception as e:
                print(f"⚠️ repo_mark_ticket_closed exception for {channel.id}: {repr(e)}")
                repo_close_ok = False

        if ticket_number is not None:
            new_name = await _format_ticket_channel_name_for_guild(
                channel.guild,
                ticket_number,
                closed=True,
                parent=channel.category,
            )
            try:
                if channel.name != new_name and not _channel_name_exists(
                    channel.guild,
                    channel_name=new_name,
                    parent=channel.category,
                    exclude_channel_id=channel.id,
                ):
                    await channel.edit(name=new_name, reason="Ticket closed")
            except Exception as e:
                print(f"⚠️ Failed renaming channel to {new_name}: {repr(e)}")

        try:
            await _apply_closed_permissions(
                channel,
                owner_member,
                staff_role_ids=_default_staff_role_ids(guild_id=channel.guild.id),
            )
        except Exception as e:
            print(f"⚠️ Failed applying closed permissions for {channel.id}: {repr(e)}")

        moved_to_archive = False
        try:
            moved_to_archive = await _move_ticket_to_archive_if_configured(channel)
        except Exception:
            moved_to_archive = False

        await _sync_channel_name_in_db(channel, ticket_number=ticket_number)

        try:
            await _freeze_open_ticket_controls_safe(channel, closed_by=closed_by)
        except Exception:
            pass

        try:
            await _post_staff_closed_message_safe(channel, closed_by=closed_by)
        except Exception:
            pass

        final_row, final_status = await _readback_ticket_state(channel.id)

        channel_looks_closed = False
        try:
            channel_looks_closed = str(channel.name or "").lower().startswith("closed-")
        except Exception:
            channel_looks_closed = False

        final_ok = bool(
            repo_close_ok
            or final_status == "closed"
            or channel_looks_closed
        )

        if final_ok:
            try:
                await log_ticket_closed(
                    guild_id=channel.guild.id,
                    actor_user_id=_actor_id(closed_by),
                    actor_name=_actor_name(closed_by),
                    channel_id=channel.id,
                    reason=reason,
                    ticket_row=final_row,
                    metadata={
                        "channel_name_after_close": channel.name,
                        "repo_close_ok": bool(repo_close_ok),
                        "final_status": final_status,
                        "channel_looks_closed": bool(channel_looks_closed),
                        "moved_to_archive": bool(moved_to_archive),
                        "lifecycle_location": _channel_lifecycle_location(channel),
                    },
                )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_closed for {channel.id}: {repr(e)}")

            print(
                f"✅ Ticket marked closed → #{channel.name} ({channel.id}) "
                f"[repo_close_ok={repo_close_ok} final_status={final_status} archive={moved_to_archive}]"
            )
            return True

        _service_debug(
            f"close unresolved channel={channel.id} "
            f"repo_close_ok={repo_close_ok} final_status={final_status} "
            f"channel_name={channel.name!r}"
        )
        return False


async def mark_ticket_deleted(
    *,
    channel_id: int | str,
    deleted_by: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel_id)
        status_before = _ticket_status(row_before)

        if status_before == "deleted":
            return True

        if status_before in {"open", "claimed"}:
            _service_debug(
                f"delete rejected channel={channel_id} reason=not-closed status={status_before}"
            )
            return False

        ok = await repo_mark_ticket_deleted(
            channel_id=channel_id,
            deleted_by=getattr(deleted_by, "id", None) if deleted_by else None,
            deleted_by_name=_actor_name(deleted_by),
            reason=reason or "Deleted",
        )

        final_row, final_status = await _readback_ticket_state(channel_id)
        final_ok = bool(ok or final_status == "deleted")

        if final_ok:
            try:
                guild_id = (
                    int(str(final_row.get("guild_id") or "0"))
                    if isinstance(final_row, dict) and final_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_deleted(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(deleted_by),
                        actor_name=_actor_name(deleted_by),
                        channel_id=channel_id,
                        reason=reason or "Deleted",
                        ticket_row=final_row,
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_deleted for {channel_id}: {repr(e)}")

            print(f"✅ Ticket marked deleted → {channel_id}")
            return True

        _service_debug(f"delete failed channel={channel_id} final_status={final_status}")
        return False


async def attach_transcript_to_ticket(
    *,
    channel_id: int | str,
    transcript_url: Optional[str],
    transcript_message_id: Optional[int | str],
    transcript_channel_id: Optional[int | str],
    actor: Optional[discord.Member | discord.User] = None,
) -> bool:
    lock = _transcript_attach_lock(channel_id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel_id)
        if isinstance(row_before, dict):
            same_url = _safe_str(row_before.get("transcript_url")) == _safe_str(transcript_url)
            same_msg = _safe_str(row_before.get("transcript_message_id")) == _safe_str(transcript_message_id)
            same_ch = _safe_str(row_before.get("transcript_channel_id")) == _safe_str(transcript_channel_id)
            if same_url and same_msg and same_ch:
                return True

        try:
            ok = await repo_attach_transcript_to_ticket(
                channel_id=channel_id,
                transcript_url=transcript_url,
                transcript_message_id=transcript_message_id,
                transcript_channel_id=transcript_channel_id,
                actor=actor,
            )

            final_row = await _ticket_row_for_channel_id(channel_id)
            row_url = _safe_str((final_row or {}).get("transcript_url"))
            row_msg = _safe_str((final_row or {}).get("transcript_message_id"))
            row_ch = _safe_str((final_row or {}).get("transcript_channel_id"))
            final_ok = bool(
                ok or (
                    row_url == _safe_str(transcript_url)
                    and row_msg == _safe_str(transcript_message_id)
                    and row_ch == _safe_str(transcript_channel_id)
                )
            )

            if final_ok:
                try:
                    guild_id = (
                        int(str(final_row.get("guild_id") or "0"))
                        if isinstance(final_row, dict) and final_row.get("guild_id")
                        else None
                    )
                    if guild_id:
                        await log_ticket_transcript_attached(
                            guild_id=guild_id,
                            actor_user_id=_actor_id(actor),
                            actor_name=_actor_name(actor),
                            channel_id=channel_id,
                            transcript_url=transcript_url,
                            transcript_message_id=transcript_message_id,
                            transcript_channel_id=transcript_channel_id,
                            ticket_row=final_row,
                        )
                except Exception as e:
                    print(f"⚠️ Failed logging transcript attach for {channel_id}: {repr(e)}")

                print(f"✅ Transcript metadata attached → {channel_id}")
                return True

            print(f"⚠️ Transcript metadata skipped → {channel_id}")
            return False
        except Exception as e:
            print(f"⚠️ Transcript metadata attach failed for {channel_id}: {repr(e)}")
            return False


async def assign_ticket(
    *,
    channel_id: int | str,
    staff_member: discord.Member | discord.User,
) -> bool:
    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row = await _ticket_row_for_channel_id(channel_id)

        if not row:
            _service_debug(f"assign rejected channel={channel_id} reason=no-row")
            return False

        if not _ticket_is_openish(row):
            _service_debug(
                f"assign rejected channel={channel_id} "
                f"reason=bad-status status={_ticket_status(row)}"
            )
            return False

        existing_claimed_by = _ticket_claimed_by_id(row)
        target_staff_id = _actor_id(staff_member) or 0

        if target_staff_id <= 0:
            _service_debug(f"assign rejected channel={channel_id} reason=invalid-staff")
            return False

        owner_id = _ticket_owner_id(row)
        if owner_id > 0 and owner_id == target_staff_id:
            _service_debug(
                f"assign rejected channel={channel_id} reason=staff-is-owner owner={owner_id}"
            )
            return False

        if existing_claimed_by > 0 and existing_claimed_by != target_staff_id:
            _service_debug(
                f"assign rejected channel={channel_id} "
                f"reason=already-claimed existing={existing_claimed_by} target={target_staff_id}"
            )
            return False

        if existing_claimed_by == target_staff_id and _ticket_status(row) == "claimed":
            _service_debug(
                f"assign noop channel={channel_id} "
                f"reason=already-claimed-by-same-staff staff={target_staff_id}"
            )
            return True

        ok = await repo_assign_ticket(
            channel_id=channel_id,
            staff_member=staff_member,
        )

        if ok:
            try:
                ticket_row = await _ticket_row_for_channel_id(channel_id)
                guild_id = (
                    int(str(ticket_row.get("guild_id") or "0"))
                    if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_claimed(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(staff_member),
                        actor_name=_actor_name(staff_member),
                        channel_id=channel_id,
                        ticket_row=ticket_row,
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_claimed for {channel_id}: {repr(e)}")

            _service_debug(f"assign success channel={channel_id} to={target_staff_id}")
        else:
            _service_debug(f"assign failed channel={channel_id} to={target_staff_id}")

        return ok


async def unclaim_ticket(
    *,
    channel_id: int | str,
    actor: Optional[discord.Member | discord.User] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row = await _ticket_row_for_channel_id(channel_id)

        if not row:
            _service_debug(f"unclaim rejected channel={channel_id} reason=no-row")
            return False

        if not _ticket_is_openish(row):
            _service_debug(
                f"unclaim rejected channel={channel_id} "
                f"reason=bad-status status={_ticket_status(row)}"
            )
            return False

        existing_claimed_by = _ticket_claimed_by_id(row)
        if existing_claimed_by <= 0:
            _service_debug(f"unclaim noop channel={channel_id} reason=not-claimed")
            return True

        actor_id = _actor_id(actor) or 0
        actor_is_elevated = _actor_is_elevated_staff(actor)

        if actor is not None and actor_id > 0:
            if actor_id != existing_claimed_by and not actor_is_elevated:
                _service_debug(
                    f"unclaim rejected channel={channel_id} "
                    f"reason=not-owner-of-claim actor={actor_id} claimed_by={existing_claimed_by}"
                )
                return False

        ok = await repo_unclaim_ticket(channel_id=channel_id)
        if ok:
            try:
                ticket_row = await _ticket_row_for_channel_id(channel_id)
                guild_id = (
                    int(str(ticket_row.get('guild_id') or '0'))
                    if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_unclaimed(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(actor),
                        actor_name=_actor_name(actor),
                        channel_id=channel_id,
                        ticket_row=ticket_row,
                        metadata={
                            "previous_claimed_by": existing_claimed_by,
                        },
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_unclaimed for {channel_id}: {repr(e)}")

            _service_debug(f"unclaim success channel={channel_id} previous={existing_claimed_by}")
        else:
            _service_debug(f"unclaim failed channel={channel_id}")

        return ok


async def transfer_ticket(
    *,
    channel_id: int | str,
    to_staff_member: discord.Member | discord.User,
    actor: Optional[discord.Member | discord.User] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row = await _ticket_row_for_channel_id(channel_id)

        if not row:
            _service_debug(f"transfer rejected channel={channel_id} reason=no-row")
            return False

        if not _ticket_is_openish(row):
            _service_debug(
                f"transfer rejected channel={channel_id} "
                f"reason=bad-status status={_ticket_status(row)}"
            )
            return False

        target_staff_id = _actor_id(to_staff_member) or 0
        if target_staff_id <= 0:
            _service_debug(f"transfer rejected channel={channel_id} reason=invalid-target")
            return False

        owner_id = _ticket_owner_id(row)
        if owner_id > 0 and owner_id == target_staff_id:
            _service_debug(
                f"transfer rejected channel={channel_id} reason=target-is-owner owner={owner_id}"
            )
            return False

        existing_claimed_by = _ticket_claimed_by_id(row)
        actor_id = _actor_id(actor) or 0
        actor_is_elevated = _actor_is_elevated_staff(actor)

        if existing_claimed_by > 0 and actor is not None:
            if actor_id != existing_claimed_by and not actor_is_elevated:
                _service_debug(
                    f"transfer rejected channel={channel_id} "
                    f"reason=actor-does-not-own-claim actor={actor_id} claimed_by={existing_claimed_by}"
                )
                return False

        if existing_claimed_by == target_staff_id and _ticket_status(row) == "claimed":
            _service_debug(
                f"transfer noop channel={channel_id} "
                f"reason=already-owned-by-target target={target_staff_id}"
            )
            return True

        ok = await repo_transfer_ticket(
            channel_id=channel_id,
            to_staff_member=to_staff_member,
        )
        if ok:
            try:
                ticket_row = await _ticket_row_for_channel_id(channel_id)
                guild_id = (
                    int(str(ticket_row.get("guild_id") or "0"))
                    if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_transferred(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(actor),
                        actor_name=_actor_name(actor),
                        target_user_id=None,
                        target_name=None,
                        channel_id=channel_id,
                        reason=f"Transferred to {to_staff_member}",
                        ticket_row=ticket_row,
                        metadata={
                            "previous_claimed_by": existing_claimed_by,
                            "transfer_to_user_id": _actor_id(to_staff_member),
                            "transfer_to_name": _actor_name(to_staff_member),
                        },
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_transferred for {channel_id}: {repr(e)}")

            _service_debug(
                f"transfer success channel={channel_id} "
                f"from={existing_claimed_by} to={target_staff_id}"
            )
        else:
            _service_debug(f"transfer failed channel={channel_id} to={target_staff_id}")
        return ok


async def set_ticket_priority(
    *,
    channel_id: int | str,
    priority: str,
    actor: Optional[discord.Member | discord.User] = None,
) -> bool:
    clean_priority = _normalize_ticket_priority(priority, "")
    if clean_priority not in _VALID_TICKET_PRIORITIES:
        _service_debug(f"set-priority rejected channel={channel_id} priority={_safe_str(priority)!r}")
        return False

    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel_id)
        if row_before and _ticket_status(row_before) == "deleted":
            _service_debug(f"set-priority rejected channel={channel_id} reason=deleted")
            return False

        if row_before and _normalize_ticket_priority(row_before.get("priority"), "medium") == clean_priority:
            return True

        ok = await repo_set_ticket_priority(
            channel_id=channel_id,
            priority=clean_priority,
        )
        if ok:
            try:
                ticket_row = await _ticket_row_for_channel_id(channel_id)
                guild_id = (
                    int(str(ticket_row.get("guild_id") or "0"))
                    if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_priority_updated(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(actor),
                        actor_name=_actor_name(actor),
                        channel_id=channel_id,
                        new_priority=clean_priority,
                        ticket_row=ticket_row,
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_priority_updated for {channel_id}: {repr(e)}")

            _service_debug(f"set-priority success channel={channel_id} priority={clean_priority}")
        else:
            _service_debug(f"set-priority failed channel={channel_id} priority={clean_priority}")
        return ok


async def add_internal_note(
    *,
    channel_id: int | str,
    author: discord.Member | discord.User,
    note: str,
    is_pinned: bool = False,
) -> bool:
    clean_note = _safe_str(note).strip()
    if not clean_note:
        return False

    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel_id)
        if row_before and _ticket_status(row_before) == "deleted":
            _service_debug(f"note rejected channel={channel_id} reason=deleted")
            return False

        ok = await repo_add_internal_note(
            channel_id=channel_id,
            author=author,
            note=clean_note,
            is_pinned=is_pinned,
        )

        if ok:
            try:
                ticket_row = await _ticket_row_for_channel_id(channel_id)
                guild_id = (
                    int(str(ticket_row.get("guild_id") or "0"))
                    if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_note_added(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(author),
                        actor_name=_actor_name(author),
                        channel_id=channel_id,
                        note_preview=clean_note[:200],
                        is_pinned=is_pinned,
                        ticket_row=ticket_row,
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_note_added for {channel_id}: {repr(e)}")

        return ok


async def list_internal_notes(
    *,
    channel_id: int | str,
    limit: int = 25,
) -> list[Dict[str, Any]]:
    return await repo_list_internal_notes(
        channel_id=channel_id,
        limit=limit,
    )


async def list_open_ticket_queue(
    *,
    guild_id: int | str,
) -> list[Dict[str, Any]]:
    try:
        rows = await repo_list_open_tickets_for_guild(
            guild_id=guild_id,
            category=None,
            statuses=["open", "claimed"],
        )
    except Exception as e:
        print("⚠️ list_open_ticket_queue failed:", repr(e))
        return []

    normalized = [_normalize_queue_row(r) for r in rows if isinstance(r, dict)]
    normalized.sort(
        key=lambda r: (
            0 if bool(r.get("is_unclaimed")) else 1,
            int(r.get("priority_rank") or 2),
            str(r.get("created_at") or ""),
        )
    )
    return normalized


async def list_unclaimed_tickets(
    *,
    guild_id: int | str,
) -> list[Dict[str, Any]]:
    rows = await list_open_ticket_queue(guild_id=guild_id)
    return [r for r in rows if bool(r.get("is_unclaimed"))]


async def list_claimed_tickets(
    *,
    guild_id: int | str,
) -> list[Dict[str, Any]]:
    rows = await list_open_ticket_queue(guild_id=guild_id)
    return [r for r in rows if bool(r.get("is_claimed"))]


async def list_tickets_claimed_by_staff(
    *,
    guild_id: int | str,
    staff_id: int | str,
) -> list[Dict[str, Any]]:
    target_staff_id = _safe_int(staff_id, 0)
    if target_staff_id <= 0:
        return []

    rows = await list_claimed_tickets(guild_id=guild_id)
    return [r for r in rows if int(r.get("claimed_by_id") or 0) == target_staff_id]


async def reopen_ticket(
    *,
    channel_id: int | str,
    actor: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel_id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel_id)
        status_before = _ticket_status(row_before)

        if status_before == "deleted":
            _service_debug(f"reopen rejected channel={channel_id} reason=deleted")
            return False

        if status_before == "open" and _ticket_claimed_by_id(row_before) <= 0:
            return True

        if status_before == "claimed":
            return True

        ok = await repo_reopen_ticket(
            channel_id=channel_id,
            reopened_by=getattr(actor, "id", None) if actor else None,
            reopened_by_name=_actor_name(actor),
            reason=reason,
        )

        final_row, final_status = await _readback_ticket_state(channel_id)
        final_ok = bool(ok or final_status in {"open", "claimed"})

        if final_ok:
            try:
                guild_id = (
                    int(str(final_row.get("guild_id") or "0"))
                    if isinstance(final_row, dict) and final_row.get("guild_id")
                    else None
                )
                if guild_id:
                    await log_ticket_reopened(
                        guild_id=guild_id,
                        actor_user_id=_actor_id(actor),
                        actor_name=_actor_name(actor),
                        channel_id=channel_id,
                        reason=reason,
                        ticket_row=final_row,
                    )
            except Exception as e:
                print(f"⚠️ Failed logging ticket_reopened for {channel_id}: {repr(e)}")

            print(f"✅ Ticket reopened → {channel_id}")
            return True

        _service_debug(f"reopen failed channel={channel_id} final_status={final_status}")
        return False


async def reopen_ticket_channel(
    *,
    channel: discord.TextChannel,
    owner: Optional[discord.Member] = None,
    staff_role_ids: Optional[list[int]] = None,
    actor: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> bool:
    lock = _ticket_mutation_lock(channel.id)

    async with lock:
        row_before = await _ticket_row_for_channel_id(channel.id)
        status_before = _ticket_status(row_before)

        if status_before == "deleted":
            _service_debug(f"reopen-channel rejected channel={channel.id} reason=deleted")
            return False

        ok = True
        if status_before not in {"open", "claimed"}:
            ok = await repo_reopen_ticket(
                channel_id=channel.id,
                reopened_by=getattr(actor, "id", None) if actor else None,
                reopened_by_name=_actor_name(actor),
                reason=reason,
            )

            if ok:
                try:
                    ticket_row = await _ticket_row_for_channel_id(channel.id)
                    guild_id = (
                        int(str(ticket_row.get("guild_id") or "0"))
                        if isinstance(ticket_row, dict) and ticket_row.get("guild_id")
                        else None
                    )
                    if guild_id:
                        await log_ticket_reopened(
                            guild_id=guild_id,
                            actor_user_id=_actor_id(actor),
                            actor_name=_actor_name(actor),
                            channel_id=channel.id,
                            reason=reason,
                            ticket_row=ticket_row,
                            metadata={
                                "lifecycle_location_before_refresh": _channel_lifecycle_location(channel),
                            },
                        )
                except Exception as e:
                    print(f"⚠️ Failed logging ticket_reopened for {channel.id}: {repr(e)}")

        if owner is None:
            owner = await _resolve_owner_member_from_channel_or_row(channel, row_before)

        ticket_number = _extract_ticket_number_from_name(channel.name)
        if ticket_number is None:
            ticket_number = _parse_ticket_number_from_topic(channel)

        if owner is not None:
            ticket_number = await _ensure_channel_identity(
                channel=channel,
                owner=owner,
                category=_safe_str((row_before or {}).get("category") or "support"),
                is_ghost=_row_is_ghost(row_before, False),
                ticket_number=ticket_number,
                closed=False,
            )
        elif ticket_number is not None:
            new_name = _format_ticket_channel_name(ticket_number, closed=False)
            try:
                if channel.name != new_name and not _channel_name_exists(
                    channel.guild,
                    channel_name=new_name,
                    parent=channel.category,
                    exclude_channel_id=channel.id,
                ):
                    await channel.edit(name=new_name, reason="Ticket reopened")
            except Exception as e:
                print(f"⚠️ Failed renaming reopened ticket to {new_name}: {repr(e)}")

        await _apply_open_permissions(
            channel,
            owner,
            staff_role_ids=staff_role_ids or _default_staff_role_ids(guild_id=guild.id),
        )

        moved_to_active = False
        try:
            moved_to_active = await _move_ticket_to_active_if_configured(channel)
        except Exception:
            moved_to_active = False

        await _sync_channel_name_in_db(channel, ticket_number=ticket_number)

        try:
            await _freeze_staff_closed_message_safe(channel, reopened_by=actor)
        except Exception:
            pass

        try:
            await _refresh_open_ticket_ui(
                channel=channel,
                owner=owner,
                is_ghost=_row_is_ghost(row_before, False),
            )
        except Exception:
            pass

        final_row, final_status = await _readback_ticket_state(channel.id)
        channel_looks_open = not str(channel.name or "").lower().startswith("closed-")
        final_ok = bool(ok and (final_status in {"open", "claimed"} or channel_looks_open))

        _service_debug(
            f"reopen-channel success channel={channel.id} moved_to_active={moved_to_active} "
            f"location={_channel_lifecycle_location(channel)} final_status={final_status}"
        )
        return final_ok


__all__ = [
    "find_open_ticket_for_owner",
    "create_ticket_channel",
    "sync_existing_ticket_channel",
    "mark_ticket_closed",
    "mark_ticket_deleted",
    "attach_transcript_to_ticket",
    "assign_ticket",
    "unclaim_ticket",
    "transfer_ticket",
    "set_ticket_priority",
    "add_internal_note",
    "list_internal_notes",
    "list_open_ticket_queue",
    "list_unclaimed_tickets",
    "list_claimed_tickets",
    "list_tickets_claimed_by_staff",
    "reopen_ticket",
    "reopen_ticket_channel",
]
