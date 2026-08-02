from __future__ import annotations

"""Durable, deduplicated Invite Shield statistics.

A deleted Discord message can be seen by create, edit, and fallback scan paths.
The database event ledger makes one message one durable stats event across
concurrent listeners, shards, and process restarts.  The legacy guild-config
counter remains the display compatibility surface, but the dedicated ledger is
the source of truth whenever its migration is available.
"""

import asyncio
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from .globals import bot, get_supabase, reset_supabase
from .guild_config import (
    GUILD_CONFIG_TABLE_FALLBACKS,
    clear_guild_config_cache,
    get_guild_config,
    upsert_guild_config,
)

STATS_TABLE = "dank_invite_block_stats"
EVENT_TABLE = "dank_invite_block_events"
RECORD_RPC = "record_dank_invite_block_event"
COUNTS_KEY = "security_stats_counts"
FALLBACK_EVENTS_KEY = "security_stats_invite_event_hashes"

_MAX_FALLBACK_EVENT_HASHES = 1024
_RECENT_EVENT_TTL_SECONDS = 30 * 60
_REFRESH_COALESCE_SECONDS = 12.0
_RETRY_BASE_SECONDS = 5.0
_RETRY_MAX_SECONDS = 60.0

_GUILD_LOCKS: dict[int, asyncio.Lock] = {}
_RECENT_EVENTS: dict[str, tuple[float, int]] = {}
_PENDING: dict[str, "PendingInviteEvent"] = {}
_REFRESH_TASKS: dict[int, asyncio.Task[Any]] = {}
_LAST_REFRESH_AT: dict[int, float] = {}
_RETRY_TASK: Optional[asyncio.Task[Any]] = None
_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"🔗 durable_invite_stats {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ durable_invite_stats {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _mapping(value: Any) -> dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def _rows(response: Any) -> list[dict[str, Any]]:
    try:
        data = getattr(response, "data", None)
        if isinstance(data, Mapping):
            return [dict(data)]
        if isinstance(data, list):
            return [dict(row) for row in data if isinstance(row, Mapping)]
    except Exception:
        pass
    return []


def _is_retryable_db_error(error: BaseException) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
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
    )


def _rpc_or_table_missing(error: BaseException) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "pgrst202",
            "pgrst205",
            "could not find the function",
            "could not find the table",
            "schema cache",
            "undefinedtable",
            "undefinedfunction",
            "does not exist",
        )
    )


def _execute_with_retry(name: str, executor, max_attempts: int = 5):
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as exc:
            last_error = exc
            if _is_retryable_db_error(exc) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                delay = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
                delay += random.uniform(0.05, 0.25)
                _warn(
                    f"{name} transient failure attempt={attempt}/{max_attempts} "
                    f"error={type(exc).__name__}: {str(exc)[:220]}"
                )
                time.sleep(delay)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{name} failed without an exception")


def _lock_for(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _GUILD_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _GUILD_LOCKS[gid] = lock
    return lock


def _normalize_counts(value: Any) -> dict[str, int]:
    raw = _mapping(value)
    return {
        "spam_blocked": max(0, _safe_int(raw.get("spam_blocked"), 0)),
        "invites_blocked": max(0, _safe_int(raw.get("invites_blocked"), 0)),
        "timeouts_issued": max(0, _safe_int(raw.get("timeouts_issued"), 0)),
        "quarantines": max(0, _safe_int(raw.get("quarantines"), 0)),
    }


def blocked_invite_count(decision: Any) -> int:
    """Count unique invite codes that policy actually approved for deletion."""

    values = list(getattr(decision, "blocked_codes", None) or [])
    if not values:
        values = list(getattr(decision, "codes", None) or [])
    unique = {
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    }
    return max(1, len(unique))


def event_hash_for_message(message: Any) -> str:
    guild_id = _safe_int(getattr(getattr(message, "guild", None), "id", 0), 0)
    channel_id = _safe_int(getattr(getattr(message, "channel", None), "id", 0), 0)
    message_id = _safe_int(getattr(message, "id", 0), 0)
    raw = f"invite-delete:{guild_id}:{channel_id}:{message_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_for_decision(decision: Any) -> str:
    source = str(getattr(decision, "source", "invite-policy") or "invite-policy").strip()
    rule = str(getattr(decision, "rule_id", "") or "").strip()
    return f"{source}:{rule}"[:180]


def _outbox_path() -> Path:
    root = str(os.getenv("DANK_RUNTIME_STATE_DIR") or ".dank_runtime").strip()
    return Path(root) / "invite_stats_outbox.json"


def _persist_outbox() -> None:
    path = _outbox_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [event.to_json() for event in _PENDING.values()]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except Exception as exc:
        _warn(f"could not persist retry outbox: {type(exc).__name__}: {exc}")


def _load_outbox() -> None:
    path = _outbox_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in list(data or []):
            event = PendingInviteEvent.from_json(raw)
            if event is not None:
                _PENDING[event.event_hash] = event
        if _PENDING:
            _log(f"restored pending_events={len(_PENDING)} from retry outbox")
    except Exception as exc:
        _warn(f"could not load retry outbox: {type(exc).__name__}: {exc}")


@dataclass(slots=True, frozen=True)
class InviteStatWriteResult:
    event_hash: str
    blocked_count: int
    invites_blocked: int
    applied: bool
    persisted: bool
    queued: bool
    backend: str


@dataclass(slots=True)
class PendingInviteEvent:
    event_hash: str
    guild_id: int
    blocked_count: int
    seed_count: int
    source: str
    attempts: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "event_hash": self.event_hash,
            "guild_id": int(self.guild_id),
            "blocked_count": int(self.blocked_count),
            "seed_count": int(self.seed_count),
            "source": self.source,
            "attempts": int(self.attempts),
        }

    @classmethod
    def from_json(cls, value: Any) -> Optional["PendingInviteEvent"]:
        raw = _mapping(value)
        event_hash = str(raw.get("event_hash") or "").strip()
        guild_id = _safe_int(raw.get("guild_id"), 0)
        blocked_count = max(1, _safe_int(raw.get("blocked_count"), 1))
        if len(event_hash) != 64 or guild_id <= 0:
            return None
        return cls(
            event_hash=event_hash,
            guild_id=guild_id,
            blocked_count=blocked_count,
            seed_count=max(0, _safe_int(raw.get("seed_count"), 0)),
            source=str(raw.get("source") or "retry")[:180],
            attempts=max(0, _safe_int(raw.get("attempts"), 0)),
        )


def _rpc_result(response: Any) -> tuple[bool, int]:
    rows = _rows(response)
    if not rows:
        raise RuntimeError(f"{RECORD_RPC} returned no row")
    row = rows[0]
    applied_raw = row.get("applied")
    if isinstance(applied_raw, bool):
        applied = applied_raw
    else:
        applied = str(applied_raw or "").strip().lower() in {"1", "true", "yes", "on"}
    count = _safe_int(row.get("invites_blocked"), -1)
    if count < 0:
        raise RuntimeError(f"{RECORD_RPC} returned invalid count: {row!r}")
    return applied, count


def _record_with_rpc_sync(event: PendingInviteEvent) -> InviteStatWriteResult:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client unavailable")
    response = sb.rpc(
        RECORD_RPC,
        {
            "p_event_hash": event.event_hash,
            "p_guild_id": str(event.guild_id),
            "p_blocked_count": int(event.blocked_count),
            "p_seed_count": int(event.seed_count),
            "p_source": event.source,
        },
    ).execute()
    applied, count = _rpc_result(response)
    return InviteStatWriteResult(
        event_hash=event.event_hash,
        blocked_count=event.blocked_count,
        invites_blocked=count,
        applied=applied,
        persisted=True,
        queued=False,
        backend="event_ledger_rpc",
    )


def _fetch_config_row_sync(sb: Any, table_name: str, guild_id: int) -> Optional[dict[str, Any]]:
    response = (
        sb.table(table_name)
        .select("guild_id,settings,updated_at")
        .eq("guild_id", str(guild_id))
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    return rows[0] if rows else None


def _fallback_event_hashes(settings: Mapping[str, Any]) -> list[str]:
    raw = settings.get(FALLBACK_EVENTS_KEY)
    values = list(raw or []) if isinstance(raw, (list, tuple)) else []
    result: list[str] = []
    for value in values:
        token = str(value or "").strip().lower()
        if len(token) == 64 and token not in result:
            result.append(token)
    return result[-_MAX_FALLBACK_EVENT_HASHES:]


def _record_with_config_cas_sync(event: PendingInviteEvent, max_attempts: int = 24) -> InviteStatWriteResult:
    """Migration-safe fallback using a conditional guild-config JSON update.

    The dedicated RPC remains the production source of truth.  This path avoids
    losing counts during a rolling deployment before PostgREST sees the new RPC.
    """

    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client unavailable")

    last_error: Optional[BaseException] = None
    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        for attempt in range(1, max_attempts + 1):
            try:
                row = _fetch_config_row_sync(sb, table_name, event.guild_id)
                if row is None:
                    settings = {
                        COUNTS_KEY: {
                            "spam_blocked": 0,
                            "invites_blocked": int(event.seed_count) + int(event.blocked_count),
                            "timeouts_issued": 0,
                            "quarantines": 0,
                        },
                        FALLBACK_EVENTS_KEY: [event.event_hash],
                    }
                    try:
                        response = sb.table(table_name).upsert(
                            {"guild_id": str(event.guild_id), "settings": settings},
                            on_conflict="guild_id",
                        ).execute()
                    except TypeError:
                        response = sb.table(table_name).upsert(
                            {"guild_id": str(event.guild_id), "settings": settings}
                        ).execute()
                    verified = _fetch_config_row_sync(sb, table_name, event.guild_id)
                    verified_settings = _mapping((verified or {}).get("settings"))
                    hashes = _fallback_event_hashes(verified_settings)
                    counts = _normalize_counts(verified_settings.get(COUNTS_KEY))
                    if event.event_hash in hashes:
                        return InviteStatWriteResult(
                            event_hash=event.event_hash,
                            blocked_count=event.blocked_count,
                            invites_blocked=counts["invites_blocked"],
                            applied=True,
                            persisted=True,
                            queued=False,
                            backend="guild_config_cas",
                        )
                    _ = response
                    time.sleep(min(0.04 * attempt, 0.5))
                    continue

                settings = _mapping(row.get("settings"))
                hashes = _fallback_event_hashes(settings)
                counts = _normalize_counts(settings.get(COUNTS_KEY))
                counts["invites_blocked"] = max(
                    counts["invites_blocked"],
                    int(event.seed_count),
                )
                if event.event_hash in hashes:
                    return InviteStatWriteResult(
                        event_hash=event.event_hash,
                        blocked_count=event.blocked_count,
                        invites_blocked=counts["invites_blocked"],
                        applied=False,
                        persisted=True,
                        queued=False,
                        backend="guild_config_cas",
                    )

                counts["invites_blocked"] += int(event.blocked_count)
                hashes.append(event.event_hash)
                settings[COUNTS_KEY] = counts
                settings[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]

                query = (
                    sb.table(table_name)
                    .update({"settings": settings})
                    .eq("guild_id", str(event.guild_id))
                )
                updated_at = row.get("updated_at")
                if updated_at is not None:
                    query = query.eq("updated_at", updated_at)
                response = query.select("settings,updated_at").execute()
                rows = _rows(response)
                if rows:
                    saved_settings = _mapping(rows[0].get("settings"))
                    saved_hashes = _fallback_event_hashes(saved_settings)
                    saved_counts = _normalize_counts(saved_settings.get(COUNTS_KEY))
                    if event.event_hash in saved_hashes:
                        return InviteStatWriteResult(
                            event_hash=event.event_hash,
                            blocked_count=event.blocked_count,
                            invites_blocked=saved_counts["invites_blocked"],
                            applied=True,
                            persisted=True,
                            queued=False,
                            backend="guild_config_cas",
                        )
                time.sleep(min((0.04 * attempt) + random.uniform(0.01, 0.08), 0.75))
            except Exception as exc:
                last_error = exc
                if _rpc_or_table_missing(exc):
                    break
                if _is_retryable_db_error(exc):
                    time.sleep(min(0.15 * attempt, 1.5))
                    continue
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No compatible guild-config table accepted invite stats CAS")


def _write_event_sync(event: PendingInviteEvent) -> InviteStatWriteResult:
    try:
        return _execute_with_retry(
            "record invite block event RPC",
            lambda: _record_with_rpc_sync(event),
            max_attempts=5,
        )
    except Exception as exc:
        if not _rpc_or_table_missing(exc):
            raise
        _warn(
            "durable invite stats RPC is not visible yet; using atomic-ish "
            f"guild-config CAS fallback error={type(exc).__name__}: {str(exc)[:180]}"
        )
        return _execute_with_retry(
            "record invite block event config CAS",
            lambda: _record_with_config_cas_sync(event),
            max_attempts=3,
        )


def _read_durable_count_sync(guild_id: int) -> Optional[int]:
    sb = get_supabase()
    if sb is None:
        return None
    response = (
        sb.table(STATS_TABLE)
        .select("invites_blocked")
        .eq("guild_id", str(guild_id))
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    if not rows:
        return None
    return max(0, _safe_int(rows[0].get("invites_blocked"), 0))


async def read_invites_blocked(guild_id: int) -> Optional[int]:
    """Read the dedicated durable total for the visible stats display."""

    gid = int(guild_id)
    if gid <= 0:
        return None
    try:
        return await asyncio.to_thread(
            _execute_with_retry,
            "read durable invite count",
            lambda: _read_durable_count_sync(gid),
            3,
        )
    except Exception as exc:
        if not _rpc_or_table_missing(exc):
            _warn(
                f"durable count read failed guild={gid} "
                f"error={type(exc).__name__}: {str(exc)[:180]}"
            )
        return None


async def _legacy_invite_count(guild_id: int) -> int:
    try:
        config = await get_guild_config(guild_id, refresh=True)
        return _normalize_counts(config.get(COUNTS_KEY))["invites_blocked"]
    except Exception as exc:
        _warn(
            f"legacy count read failed guild={guild_id} "
            f"error={type(exc).__name__}: {str(exc)[:180]}"
        )
        return 0


async def _sync_compatibility_count(guild_id: int, durable_count: int) -> None:
    try:
        config = await get_guild_config(guild_id, refresh=True)
        counts = _normalize_counts(config.get(COUNTS_KEY))
        desired = max(counts["invites_blocked"], int(durable_count))
        if desired != counts["invites_blocked"]:
            counts["invites_blocked"] = desired
            await upsert_guild_config(guild_id, {COUNTS_KEY: counts})
        else:
            clear_guild_config_cache(guild_id)
        _schedule_display_refresh(guild_id)
    except Exception as exc:
        _warn(
            f"compatibility count sync failed guild={guild_id} count={durable_count} "
            f"error={type(exc).__name__}: {str(exc)[:220]}"
        )


def _prune_recent(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else float(now)
    expired = [
        event_hash
        for event_hash, (saved_at, _count) in _RECENT_EVENTS.items()
        if current - saved_at > _RECENT_EVENT_TTL_SECONDS
    ]
    for event_hash in expired:
        _RECENT_EVENTS.pop(event_hash, None)


def _schedule_display_refresh(guild_id: int) -> None:
    gid = int(guild_id)
    existing = _REFRESH_TASKS.get(gid)
    if existing is not None and not existing.done():
        return

    async def worker() -> None:
        try:
            elapsed = time.monotonic() - float(_LAST_REFRESH_AT.get(gid, 0.0))
            delay = max(0.75, _REFRESH_COALESCE_SECONDS - elapsed)
            await asyncio.sleep(delay)
            guild = bot.get_guild(gid)
            if guild is None:
                return
            from . import security_stats

            await security_stats.refresh_security_stats_display(guild, force=True)
            _LAST_REFRESH_AT[gid] = time.monotonic()
        except Exception as exc:
            _warn(
                f"display refresh failed guild={gid} "
                f"error={type(exc).__name__}: {str(exc)[:180]}"
            )
        finally:
            _REFRESH_TASKS.pop(gid, None)

    try:
        _REFRESH_TASKS[gid] = asyncio.get_running_loop().create_task(worker())
    except Exception as exc:
        _warn(f"could not schedule display refresh guild={gid}: {type(exc).__name__}: {exc}")


def _queue_pending(event: PendingInviteEvent) -> None:
    existing = _PENDING.get(event.event_hash)
    if existing is None or event.blocked_count > existing.blocked_count:
        _PENDING[event.event_hash] = event
    _persist_outbox()
    _ensure_retry_task()


async def _retry_pending_loop() -> None:
    global _RETRY_TASK
    try:
        while _PENDING:
            await asyncio.sleep(_RETRY_BASE_SECONDS)
            for event_hash, event in list(_PENDING.items()):
                try:
                    result = await asyncio.to_thread(_write_event_sync, event)
                    _PENDING.pop(event_hash, None)
                    _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
                    _persist_outbox()
                    await _sync_compatibility_count(event.guild_id, result.invites_blocked)
                    _log(
                        f"retry persisted guild={event.guild_id} event={event_hash[:12]} "
                        f"blocked={event.blocked_count} total={result.invites_blocked} backend={result.backend}"
                    )
                except Exception as exc:
                    event.attempts += 1
                    if event.attempts in {1, 3, 10} or event.attempts % 25 == 0:
                        _warn(
                            f"retry still pending guild={event.guild_id} event={event_hash[:12]} "
                            f"attempts={event.attempts} error={type(exc).__name__}: {str(exc)[:180]}"
                        )
            if _PENDING:
                highest_attempt = max(event.attempts for event in _PENDING.values())
                extra = min(_RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * max(0, highest_attempt - 1))
                if extra > _RETRY_BASE_SECONDS:
                    await asyncio.sleep(extra - _RETRY_BASE_SECONDS)
    finally:
        _RETRY_TASK = None


def _ensure_retry_task() -> None:
    global _RETRY_TASK
    if not _PENDING:
        return
    if _RETRY_TASK is not None and not _RETRY_TASK.done():
        return
    try:
        _RETRY_TASK = asyncio.get_running_loop().create_task(_retry_pending_loop())
    except Exception:
        pass


async def record_deleted_invite_decision(message: Any, decision: Any) -> InviteStatWriteResult:
    guild_id = _safe_int(getattr(getattr(message, "guild", None), "id", 0), 0)
    if guild_id <= 0:
        raise ValueError("Invite stats require a guild message")

    event_hash = event_hash_for_message(message)
    blocked_count = blocked_invite_count(decision)
    _prune_recent()
    recent = _RECENT_EVENTS.get(event_hash)
    if recent is not None:
        return InviteStatWriteResult(
            event_hash=event_hash,
            blocked_count=blocked_count,
            invites_blocked=recent[1],
            applied=False,
            persisted=True,
            queued=False,
            backend="recent_event_cache",
        )

    async with _lock_for(guild_id):
        recent = _RECENT_EVENTS.get(event_hash)
        if recent is not None:
            return InviteStatWriteResult(
                event_hash=event_hash,
                blocked_count=blocked_count,
                invites_blocked=recent[1],
                applied=False,
                persisted=True,
                queued=False,
                backend="recent_event_cache",
            )

        seed_count = await _legacy_invite_count(guild_id)
        event = PendingInviteEvent(
            event_hash=event_hash,
            guild_id=guild_id,
            blocked_count=blocked_count,
            seed_count=seed_count,
            source=_source_for_decision(decision),
        )
        try:
            result = await asyncio.to_thread(_write_event_sync, event)
        except Exception as exc:
            _queue_pending(event)
            _warn(
                f"write queued guild={guild_id} event={event_hash[:12]} blocked={blocked_count} "
                f"error={type(exc).__name__}: {str(exc)[:220]}"
            )
            return InviteStatWriteResult(
                event_hash=event_hash,
                blocked_count=blocked_count,
                invites_blocked=seed_count,
                applied=False,
                persisted=False,
                queued=True,
                backend="retry_outbox",
            )

        _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
        _PENDING.pop(event_hash, None)
        _persist_outbox()
        await _sync_compatibility_count(guild_id, result.invites_blocked)
        _log(
            f"recorded guild={guild_id} event={event_hash[:12]} blocked={blocked_count} "
            f"total={result.invites_blocked} applied={result.applied} backend={result.backend}"
        )
        return result


async def reconcile_guild(guild_id: int) -> Optional[int]:
    gid = int(guild_id)
    if gid <= 0:
        return None
    try:
        count = await asyncio.to_thread(
            _execute_with_retry,
            "read durable invite count",
            lambda: _read_durable_count_sync(gid),
            3,
        )
    except Exception as exc:
        if not _rpc_or_table_missing(exc):
            _warn(
                f"reconcile read failed guild={gid} "
                f"error={type(exc).__name__}: {str(exc)[:180]}"
            )
        return None
    if count is None:
        return None
    await _sync_compatibility_count(gid, count)
    return count


async def _on_ready() -> None:
    _ensure_retry_task()
    guilds = list(getattr(bot, "guilds", []) or [])
    for guild in guilds:
        try:
            await reconcile_guild(int(guild.id))
        except Exception as exc:
            _warn(
                f"startup reconcile failed guild={getattr(guild, 'id', 0)} "
                f"error={type(exc).__name__}: {str(exc)[:180]}"
            )


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    _load_outbox()
    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_ready") or [])
        if not any(
            getattr(listener, "__module__", "") == __name__
            and getattr(listener, "__name__", "") == "_on_ready"
            for listener in existing
        ):
            bot.add_listener(_on_ready, "on_ready")
        _INSTALLED = True
        _log("active; atomic event ledger, retry outbox, and display reconciliation enabled")
        return True
    except Exception as exc:
        _warn(f"listener install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = [
    "InviteStatWriteResult",
    "PendingInviteEvent",
    "blocked_invite_count",
    "event_hash_for_message",
    "install",
    "read_invites_blocked",
    "reconcile_guild",
    "record_deleted_invite_decision",
]
