from __future__ import annotations

"""Persistent state and validation for Dank Shield Community Tools."""

import asyncio
import ipaddress
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlparse, urlunparse

from .globals import get_supabase

STICKY_TABLE = "dank_stickies"
STICKY_POLL_TABLE = "dank_sticky_polls"
STICKY_BUNDLE_RPC = "save_dank_sticky_bundle"
POSTGREST_PAGE_SIZE = 500

DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_MESSAGE_THRESHOLD = 5
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 3600
MIN_MESSAGE_THRESHOLD = 1
MAX_MESSAGE_THRESHOLD = 100
MAX_STICKY_CONTENT = 1900
MAX_STICKY_TITLE = 256
MAX_SENDER_NAME = 80
MAX_POLL_QUESTION = 300
MAX_POLL_OPTION = 80
MAX_POLL_OPTIONS = 7
VALID_STICKY_MODES = frozenset({"plain", "embed", "poll"})
VALID_POLL_STATES = frozenset({"active", "paused", "ended"})


class CommunityStorageUnavailable(RuntimeError):
    """Raised when persistent Community Tools storage is unavailable."""


class InvalidCommunityToolValue(ValueError):
    """Raised when a Community Tools value cannot be used safely."""


@dataclass(frozen=True)
class StickyConfig:
    guild_id: int
    channel_id: int
    enabled: bool = True
    content: str = ""
    mode: str = "plain"
    title: str = ""
    color: int = 0x5865F2
    image_url: str = ""
    thumbnail_url: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    message_threshold: int = DEFAULT_MESSAGE_THRESHOLD
    use_webhook: bool = False
    sender_name: str = ""
    sender_avatar_url: str = ""
    last_message_id: Optional[int] = None
    last_sent_at: Optional[datetime] = None
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class StickyPoll:
    guild_id: int
    channel_id: int
    question: str
    options: tuple[str, ...]
    votes: Mapping[str, int]
    state: str = "active"
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None

    @property
    def total_votes(self) -> int:
        return len(self.votes)

    def counts(self) -> tuple[int, ...]:
        result = [0 for _ in self.options]
        for raw_index in self.votes.values():
            try:
                index = int(raw_index)
            except Exception:
                continue
            if 0 <= index < len(result):
                result[index] += 1
        return tuple(result)


_STICKY_LOCKS: dict[int, asyncio.Lock] = {}
_POLL_LOCKS: dict[int, asyncio.Lock] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def normalize_https_url(value: Any, *, allow_empty: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise InvalidCommunityToolValue("Enter a valid HTTPS URL.")
    if len(raw) > 1000:
        raise InvalidCommunityToolValue("That URL is too long.")

    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise InvalidCommunityToolValue("Only HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise InvalidCommunityToolValue("URLs containing credentials are not allowed.")
    try:
        if parsed.port is not None:
            raise InvalidCommunityToolValue("Custom URL ports are not allowed.")
    except ValueError as exc:
        raise InvalidCommunityToolValue("That URL has an invalid port.") from exc

    host = str(parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidCommunityToolValue("That URL does not contain a valid hostname.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise InvalidCommunityToolValue("Local/private URLs are not allowed.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise InvalidCommunityToolValue("Local/private IP addresses are not allowed.")

    return urlunparse(("https", host, parsed.path or "", "", parsed.query or "", parsed.fragment or ""))


def normalize_sticky(config: StickyConfig) -> StickyConfig:
    mode = str(config.mode or "plain").strip().lower()
    if mode not in VALID_STICKY_MODES:
        raise InvalidCommunityToolValue("Sticky mode must be plain, embed, or poll.")

    content = _clean_text(config.content, maximum=MAX_STICKY_CONTENT)
    title = _clean_text(config.title, maximum=MAX_STICKY_TITLE)
    image_url = normalize_https_url(config.image_url)
    thumbnail_url = normalize_https_url(config.thumbnail_url)
    sender_avatar_url = normalize_https_url(config.sender_avatar_url)
    sender_name = _clean_text(config.sender_name, maximum=MAX_SENDER_NAME)

    if mode == "plain" and not content:
        raise InvalidCommunityToolValue("Plain stickies need message text.")
    if mode == "embed" and not any((content, title, image_url, thumbnail_url)):
        raise InvalidCommunityToolValue("Embed stickies need text, a title, or an image.")
    if mode == "poll" and any((config.use_webhook, sender_name, sender_avatar_url)):
        # Poll buttons are owned by the bot message, not a custom webhook.
        sender_name = ""
        sender_avatar_url = ""

    return replace(
        config,
        guild_id=int(config.guild_id),
        channel_id=int(config.channel_id),
        enabled=bool(config.enabled),
        content=content,
        mode=mode,
        title=title,
        color=max(0, min(_safe_int(config.color, 0x5865F2), 0xFFFFFF)),
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        interval_seconds=max(
            MIN_INTERVAL_SECONDS,
            min(_safe_int(config.interval_seconds, DEFAULT_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS),
        ),
        message_threshold=max(
            MIN_MESSAGE_THRESHOLD,
            min(_safe_int(config.message_threshold, DEFAULT_MESSAGE_THRESHOLD), MAX_MESSAGE_THRESHOLD),
        ),
        use_webhook=bool(config.use_webhook and mode != "poll"),
        sender_name=sender_name,
        sender_avatar_url=sender_avatar_url,
        last_message_id=_safe_int(config.last_message_id, 0) or None,
        updated_by=_safe_int(config.updated_by, 0) or None,
    )


def normalize_poll(poll: StickyPoll) -> StickyPoll:
    question = _clean_text(poll.question, maximum=MAX_POLL_QUESTION, allow_empty=False)
    options: list[str] = []
    for raw in poll.options:
        value = _clean_text(raw, maximum=MAX_POLL_OPTION)
        if value and value.casefold() not in {item.casefold() for item in options}:
            options.append(value)
    if not 2 <= len(options) <= MAX_POLL_OPTIONS:
        raise InvalidCommunityToolValue(f"Polls need 2 to {MAX_POLL_OPTIONS} unique choices.")

    state = str(poll.state or "active").strip().lower()
    if state not in VALID_POLL_STATES:
        raise InvalidCommunityToolValue("Poll state must be active, paused, or ended.")

    votes: dict[str, int] = {}
    for user_id, raw_index in dict(poll.votes or {}).items():
        index = _safe_int(raw_index, -1)
        user_key = str(user_id).strip()
        if user_key and 0 <= index < len(options):
            votes[user_key] = index

    return replace(
        poll,
        guild_id=int(poll.guild_id),
        channel_id=int(poll.channel_id),
        question=question,
        options=tuple(options),
        votes=votes,
        state=state,
        updated_by=_safe_int(poll.updated_by, 0) or None,
    )


def _require_supabase() -> Any:
    sb = get_supabase()
    if sb is None:
        raise CommunityStorageUnavailable("Supabase is not configured.")
    return sb


def _row_to_sticky(row: Mapping[str, Any]) -> StickyConfig:
    return normalize_sticky(
        StickyConfig(
            guild_id=_safe_int(row.get("guild_id")),
            channel_id=_safe_int(row.get("channel_id")),
            enabled=_safe_bool(row.get("enabled"), True),
            content=str(row.get("content") or ""),
            mode=str(row.get("mode") or "plain"),
            title=str(row.get("title") or ""),
            color=_safe_int(row.get("color"), 0x5865F2),
            image_url=str(row.get("image_url") or ""),
            thumbnail_url=str(row.get("thumbnail_url") or ""),
            interval_seconds=_safe_int(row.get("interval_seconds"), DEFAULT_INTERVAL_SECONDS),
            message_threshold=_safe_int(row.get("message_threshold"), DEFAULT_MESSAGE_THRESHOLD),
            use_webhook=_safe_bool(row.get("use_webhook"), False),
            sender_name=str(row.get("sender_name") or ""),
            sender_avatar_url=str(row.get("sender_avatar_url") or ""),
            last_message_id=_safe_int(row.get("last_message_id"), 0) or None,
            last_sent_at=_safe_dt(row.get("last_sent_at")),
            updated_by=_safe_int(row.get("updated_by"), 0) or None,
            updated_at=_safe_dt(row.get("updated_at")),
        )
    )


def _row_to_poll(row: Mapping[str, Any]) -> StickyPoll:
    raw_options = row.get("options")
    if isinstance(raw_options, str):
        try:
            raw_options = json.loads(raw_options)
        except Exception:
            raw_options = []
    raw_votes = row.get("votes")
    if isinstance(raw_votes, str):
        try:
            raw_votes = json.loads(raw_votes)
        except Exception:
            raw_votes = {}
    return normalize_poll(
        StickyPoll(
            guild_id=_safe_int(row.get("guild_id")),
            channel_id=_safe_int(row.get("channel_id")),
            question=str(row.get("question") or ""),
            options=tuple(str(item) for item in (raw_options or [])),
            votes=dict(raw_votes or {}),
            state=str(row.get("state") or "active"),
            updated_by=_safe_int(row.get("updated_by"), 0) or None,
            updated_at=_safe_dt(row.get("updated_at")),
        )
    )


def _sticky_payload(config: StickyConfig) -> dict[str, Any]:
    safe = normalize_sticky(config)
    return {
        "guild_id": int(safe.guild_id),
        "channel_id": int(safe.channel_id),
        "enabled": bool(safe.enabled),
        "content": safe.content,
        "mode": safe.mode,
        "title": safe.title or None,
        "color": int(safe.color),
        "image_url": safe.image_url or None,
        "thumbnail_url": safe.thumbnail_url or None,
        "interval_seconds": int(safe.interval_seconds),
        "message_threshold": int(safe.message_threshold),
        "use_webhook": bool(safe.use_webhook),
        "sender_name": safe.sender_name or None,
        "sender_avatar_url": safe.sender_avatar_url or None,
        "last_message_id": int(safe.last_message_id) if safe.last_message_id else None,
        "last_sent_at": safe.last_sent_at.isoformat() if safe.last_sent_at else None,
        "updated_by": int(safe.updated_by) if safe.updated_by else None,
        "updated_at": utc_now().isoformat(),
    }


def _poll_payload(poll: StickyPoll) -> dict[str, Any]:
    safe = normalize_poll(poll)
    return {
        "guild_id": int(safe.guild_id),
        "channel_id": int(safe.channel_id),
        "question": safe.question,
        "options": list(safe.options),
        "votes": dict(safe.votes),
        "state": safe.state,
        "updated_by": int(safe.updated_by) if safe.updated_by else None,
        "updated_at": utc_now().isoformat(),
    }


def _get_sticky_sync(channel_id: int) -> Optional[StickyConfig]:
    try:
        resp = _require_supabase().table(STICKY_TABLE).select("*").eq("channel_id", int(channel_id)).limit(1).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_TABLE}` is unavailable.") from exc
    rows = getattr(resp, "data", None) or []
    if not rows or not isinstance(rows[0], Mapping):
        return None
    return _row_to_sticky(rows[0])


def _list_stickies_sync(*, guild_id: Optional[int] = None, enabled_only: bool = False) -> list[StickyConfig]:
    rows: list[Mapping[str, Any]] = []
    offset = 0
    try:
        while True:
            query = _require_supabase().table(STICKY_TABLE).select("*")
            if guild_id is not None:
                query = query.eq("guild_id", int(guild_id))
            if enabled_only:
                query = query.eq("enabled", True)
            resp = query.order("channel_id").range(offset, offset + POSTGREST_PAGE_SIZE - 1).execute()
            page = [row for row in (getattr(resp, "data", None) or []) if isinstance(row, Mapping)]
            rows.extend(page)
            if len(page) < POSTGREST_PAGE_SIZE:
                break
            offset += POSTGREST_PAGE_SIZE
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_TABLE}` is unavailable.") from exc
    return [_row_to_sticky(row) for row in rows]


def _save_sticky_sync(config: StickyConfig) -> StickyConfig:
    safe = normalize_sticky(config)
    payload = _sticky_payload(safe)
    try:
        resp = _require_supabase().table(STICKY_TABLE).upsert(payload, on_conflict="channel_id").execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_TABLE}` is not writable.") from exc
    rows = getattr(resp, "data", None) or []
    return _row_to_sticky(rows[0]) if rows and isinstance(rows[0], Mapping) else replace(safe, updated_at=utc_now())


def _delete_sticky_sync(channel_id: int) -> None:
    try:
        _require_supabase().table(STICKY_TABLE).delete().eq("channel_id", int(channel_id)).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_TABLE}` is not writable.") from exc


def _get_poll_sync(channel_id: int) -> Optional[StickyPoll]:
    try:
        resp = _require_supabase().table(STICKY_POLL_TABLE).select("*").eq("channel_id", int(channel_id)).limit(1).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_POLL_TABLE}` is unavailable.") from exc
    rows = getattr(resp, "data", None) or []
    if not rows or not isinstance(rows[0], Mapping):
        return None
    return _row_to_poll(rows[0])


def _save_poll_sync(poll: StickyPoll) -> StickyPoll:
    safe = normalize_poll(poll)
    payload = _poll_payload(safe)
    try:
        resp = _require_supabase().table(STICKY_POLL_TABLE).upsert(payload, on_conflict="channel_id").execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_POLL_TABLE}` is not writable.") from exc
    rows = getattr(resp, "data", None) or []
    return _row_to_poll(rows[0]) if rows and isinstance(rows[0], Mapping) else replace(safe, updated_at=utc_now())


def _delete_poll_sync(channel_id: int) -> None:
    try:
        _require_supabase().table(STICKY_POLL_TABLE).delete().eq("channel_id", int(channel_id)).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(f"`{STICKY_POLL_TABLE}` is not writable.") from exc


def _save_sticky_bundle_sync(
    config: StickyConfig,
    poll: Optional[StickyPoll],
) -> tuple[StickyConfig, Optional[StickyPoll]]:
    """Persist a sticky and its optional poll in one database transaction.

    The SQL RPC owns the transition so a process/network failure cannot leave a
    poll-mode sticky without poll state, or stale poll state behind after a
    plain/embed conversion.
    """
    safe_config = normalize_sticky(config)
    safe_poll: Optional[StickyPoll] = None
    if safe_config.mode == "poll":
        if poll is None:
            raise InvalidCommunityToolValue("Sticky poll state is required for poll mode.")
        safe_poll = normalize_poll(poll)
        if int(safe_poll.guild_id) != int(safe_config.guild_id) or int(safe_poll.channel_id) != int(safe_config.channel_id):
            raise InvalidCommunityToolValue("Sticky and poll must belong to the same server channel.")
    elif poll is not None:
        raise InvalidCommunityToolValue("Poll state can only be saved with a poll-mode sticky.")

    params = {
        "p_sticky": _sticky_payload(safe_config),
        "p_poll": _poll_payload(safe_poll) if safe_poll is not None else None,
    }
    try:
        resp = _require_supabase().rpc(STICKY_BUNDLE_RPC, params).execute()
    except Exception as exc:
        if isinstance(exc, CommunityStorageUnavailable):
            raise
        raise CommunityStorageUnavailable(
            f"Community Tools atomic save RPC `{STICKY_BUNDLE_RPC}` is unavailable. Apply the Community Tools hardening migration."
        ) from exc

    data = getattr(resp, "data", None)
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], Mapping):
        data = data[0]
    if not isinstance(data, Mapping):
        raise CommunityStorageUnavailable("Community Tools atomic save returned an invalid response.")

    sticky_row = data.get("sticky")
    poll_row = data.get("poll")
    if not isinstance(sticky_row, Mapping):
        raise CommunityStorageUnavailable("Community Tools atomic save did not return sticky state.")
    saved_config = _row_to_sticky(sticky_row)
    saved_poll = _row_to_poll(poll_row) if isinstance(poll_row, Mapping) else None
    if saved_config.mode == "poll" and saved_poll is None:
        raise CommunityStorageUnavailable("Community Tools atomic save did not return sticky-poll state.")
    return saved_config, saved_poll


async def get_sticky(channel_id: int) -> Optional[StickyConfig]:
    return await asyncio.to_thread(_get_sticky_sync, int(channel_id))


async def list_stickies(*, guild_id: Optional[int] = None, enabled_only: bool = False) -> list[StickyConfig]:
    return await asyncio.to_thread(_list_stickies_sync, guild_id=guild_id, enabled_only=enabled_only)


async def save_sticky(config: StickyConfig) -> StickyConfig:
    lock = _STICKY_LOCKS.setdefault(int(config.channel_id), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_save_sticky_sync, config)


async def save_sticky_bundle(
    config: StickyConfig,
    poll: Optional[StickyPoll] = None,
) -> tuple[StickyConfig, Optional[StickyPoll]]:
    channel_id = int(config.channel_id)
    sticky_lock = _STICKY_LOCKS.setdefault(channel_id, asyncio.Lock())
    poll_lock = _POLL_LOCKS.setdefault(channel_id, asyncio.Lock())
    async with sticky_lock:
        async with poll_lock:
            return await asyncio.to_thread(_save_sticky_bundle_sync, config, poll)


async def update_sticky_delivery(
    channel_id: int,
    *,
    message_id: Optional[int],
    sent_at: Optional[datetime] = None,
) -> Optional[StickyConfig]:
    lock = _STICKY_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        current = await asyncio.to_thread(_get_sticky_sync, int(channel_id))
        if current is None:
            return None
        return await asyncio.to_thread(
            _save_sticky_sync,
            replace(
                current,
                last_message_id=int(message_id) if message_id else None,
                last_sent_at=sent_at or utc_now(),
            ),
        )


async def set_sticky_enabled(channel_id: int, enabled: bool, *, actor_id: Optional[int] = None) -> Optional[StickyConfig]:
    lock = _STICKY_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        current = await asyncio.to_thread(_get_sticky_sync, int(channel_id))
        if current is None:
            return None
        return await asyncio.to_thread(
            _save_sticky_sync,
            replace(current, enabled=bool(enabled), updated_by=int(actor_id) if actor_id else current.updated_by),
        )


async def delete_sticky(channel_id: int) -> None:
    channel_key = int(channel_id)
    sticky_lock = _STICKY_LOCKS.setdefault(channel_key, asyncio.Lock())
    poll_lock = _POLL_LOCKS.setdefault(channel_key, asyncio.Lock())
    async with sticky_lock:
        async with poll_lock:
            # dank_sticky_polls has ON DELETE CASCADE from the sticky row.
            await asyncio.to_thread(_delete_sticky_sync, channel_key)


async def delete_sticky_poll(channel_id: int) -> None:
    lock = _POLL_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        await asyncio.to_thread(_delete_poll_sync, int(channel_id))


async def get_sticky_poll(channel_id: int) -> Optional[StickyPoll]:
    return await asyncio.to_thread(_get_poll_sync, int(channel_id))


async def save_sticky_poll(poll: StickyPoll) -> StickyPoll:
    lock = _POLL_LOCKS.setdefault(int(poll.channel_id), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_save_poll_sync, poll)


async def cast_sticky_poll_vote(channel_id: int, user_id: int, option_index: int) -> StickyPoll:
    lock = _POLL_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        poll = await asyncio.to_thread(_get_poll_sync, int(channel_id))
        if poll is None:
            raise InvalidCommunityToolValue("This sticky poll no longer exists.")
        if poll.state != "active":
            raise InvalidCommunityToolValue("This sticky poll is not accepting votes.")
        if not 0 <= int(option_index) < len(poll.options):
            raise InvalidCommunityToolValue("That poll choice no longer exists.")
        votes = dict(poll.votes)
        votes[str(int(user_id))] = int(option_index)
        return await asyncio.to_thread(_save_poll_sync, replace(poll, votes=votes))


async def set_sticky_poll_state(
    channel_id: int,
    state: str,
    *,
    actor_id: Optional[int] = None,
) -> StickyPoll:
    lock = _POLL_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        poll = await asyncio.to_thread(_get_poll_sync, int(channel_id))
        if poll is None:
            raise InvalidCommunityToolValue("This sticky poll no longer exists.")
        safe_state = str(state or "").strip().lower()
        if safe_state not in VALID_POLL_STATES:
            raise InvalidCommunityToolValue("Unsupported poll state.")
        return await asyncio.to_thread(
            _save_poll_sync,
            replace(poll, state=safe_state, updated_by=int(actor_id) if actor_id else poll.updated_by),
        )


async def reset_sticky_poll(channel_id: int, *, actor_id: Optional[int] = None) -> StickyPoll:
    lock = _POLL_LOCKS.setdefault(int(channel_id), asyncio.Lock())
    async with lock:
        poll = await asyncio.to_thread(_get_poll_sync, int(channel_id))
        if poll is None:
            raise InvalidCommunityToolValue("This sticky poll no longer exists.")
        return await asyncio.to_thread(
            _save_poll_sync,
            replace(poll, votes={}, state="active", updated_by=int(actor_id) if actor_id else poll.updated_by),
        )


__all__ = [
    "CommunityStorageUnavailable",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_MESSAGE_THRESHOLD",
    "InvalidCommunityToolValue",
    "MAX_INTERVAL_SECONDS",
    "MAX_MESSAGE_THRESHOLD",
    "MAX_POLL_OPTIONS",
    "MIN_INTERVAL_SECONDS",
    "MIN_MESSAGE_THRESHOLD",
    "POSTGREST_PAGE_SIZE",
    "STICKY_BUNDLE_RPC",
    "StickyConfig",
    "StickyPoll",
    "cast_sticky_poll_vote",
    "delete_sticky",
    "delete_sticky_poll",
    "get_sticky",
    "get_sticky_poll",
    "list_stickies",
    "normalize_https_url",
    "normalize_poll",
    "normalize_sticky",
    "reset_sticky_poll",
    "save_sticky",
    "save_sticky_bundle",
    "save_sticky_poll",
    "set_sticky_enabled",
    "set_sticky_poll_state",
    "update_sticky_delivery",
    "utc_now",
]
