# stoney_verify/channel_cleanup.py
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from .globals import *  # noqa: F401,F403


# ============================================================
# Channel cleanup policy model
# ------------------------------------------------------------
# Supports:
#
# 1) Global Python config (preferred if you want it in code):
#
#    CHANNEL_CLEANUP_RULES = {
#        "147000000000000001": {
#            "enabled": True,
#            "max_age_hours": 24,
#            "interval_minutes": 60,
#            "skip_pinned": True,
#            "label": "unverified-chat",
#        },
#        "147000000000000002": {
#            "enabled": True,
#            "max_age_hours": 168,
#            "interval_minutes": 360,
#            "skip_pinned": True,
#            "label": "screenshots",
#        },
#    }
#
# 2) ENV JSON override:
#
#    CHANNEL_CLEANUP_RULES_JSON='{
#      "147000000000000001": {
#        "enabled": true,
#        "max_age_hours": 24,
#        "interval_minutes": 60,
#        "skip_pinned": true,
#        "label": "unverified-chat"
#      }
#    }'
#
# 3) Simple fallback envs:
#
#    UNVERIFIED_CHAT_CHANNEL_ID=147000000000000001
#    UNVERIFIED_CHAT_MAX_AGE_HOURS=24
#    UNVERIFIED_CHAT_CLEANUP_INTERVAL_MINUTES=60
#
#    EXTRA_CLEANUP_CHANNEL_IDS=147...,148...,149...
#    EXTRA_CLEANUP_MAX_AGE_HOURS=24
#    EXTRA_CLEANUP_INTERVAL_MINUTES=60
#
# Notes:
# - Channel IDs are globally unique, so we key by channel_id only.
# - Messages newer than 14 days are bulk-deleted in batches where possible.
# - Messages older than 14 days are deleted one-by-one.
# ============================================================


DEFAULT_CLEANUP_INTERVAL_MINUTES = 60
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_SKIP_PINNED = True
BULK_DELETE_MAX_AGE_DAYS = 14
INDIVIDUAL_DELETE_PAUSE_EVERY = 5
INDIVIDUAL_DELETE_PAUSE_SECONDS = 1.0
WORKER_TICK_SECONDS = 60


# ============================================================
# Runtime state
# ============================================================
_CHANNEL_CLEANUP_TASK: Optional[asyncio.Task] = None
_CHANNEL_CLEANUP_LAST_RUN: Dict[int, datetime] = {}
_CHANNEL_CLEANUP_LOCKS: Dict[int, asyncio.Lock] = {}


# ============================================================
# Small helpers
# ============================================================
def _utc_now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return float(str(value).strip())
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _channel_lock(channel_id: int) -> asyncio.Lock:
    cid = int(channel_id)
    lock = _CHANNEL_CLEANUP_LOCKS.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _CHANNEL_CLEANUP_LOCKS[cid] = lock
    return lock


def _normalize_rule(channel_id: int, raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "channel_id": int(channel_id),
        "enabled": _as_bool(raw.get("enabled", True), True),
        "max_age_hours": max(0.1, _as_float(raw.get("max_age_hours", DEFAULT_MAX_AGE_HOURS), DEFAULT_MAX_AGE_HOURS)),
        "interval_minutes": max(1, _as_int(raw.get("interval_minutes", DEFAULT_CLEANUP_INTERVAL_MINUTES), DEFAULT_CLEANUP_INTERVAL_MINUTES)),
        "skip_pinned": _as_bool(raw.get("skip_pinned", DEFAULT_SKIP_PINNED), DEFAULT_SKIP_PINNED),
        "label": str(raw.get("label") or f"channel-{channel_id}"),
    }


def _merge_rule_map(base: Dict[int, Dict[str, Any]], incoming: Dict[int, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = dict(base)
    for cid, rule in incoming.items():
        out[int(cid)] = dict(rule)
    return out


# ============================================================
# Policy loading
# ============================================================
def _load_rules_from_python_globals() -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}

    try:
        raw = globals().get("CHANNEL_CLEANUP_RULES")
        if not isinstance(raw, dict):
            return out

        for k, v in raw.items():
            cid = _as_int(k, 0)
            if cid <= 0 or not isinstance(v, dict):
                continue
            out[cid] = _normalize_rule(cid, v)
    except Exception:
        pass

    return out


def _load_rules_from_json_env() -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    raw = os.getenv("CHANNEL_CLEANUP_RULES_JSON", "").strip()
    if not raw:
        return out

    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"⚠️ channel_cleanup: failed parsing CHANNEL_CLEANUP_RULES_JSON: {repr(e)}")
        return out

    try:
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                cid = _as_int(k, 0)
                if cid <= 0 or not isinstance(v, dict):
                    continue
                out[cid] = _normalize_rule(cid, v)
            return out

        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                cid = _as_int(item.get("channel_id"), 0)
                if cid <= 0:
                    continue
                out[cid] = _normalize_rule(cid, item)
            return out
    except Exception as e:
        print(f"⚠️ channel_cleanup: invalid cleanup JSON shape: {repr(e)}")

    return out


def _load_rules_from_simple_envs() -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}

    # Dedicated unverified-chat shortcut
    try:
        uv_cid = _as_int(
            globals().get("UNVERIFIED_CHAT_CHANNEL_ID", 0) or os.getenv("UNVERIFIED_CHAT_CHANNEL_ID", "0"),
            0,
        )
        if uv_cid > 0:
            out[uv_cid] = _normalize_rule(
                uv_cid,
                {
                    "enabled": True,
                    "max_age_hours": _as_float(
                        os.getenv(
                            "UNVERIFIED_CHAT_MAX_AGE_HOURS",
                            str(globals().get("UNVERIFIED_CHAT_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS)),
                        ),
                        DEFAULT_MAX_AGE_HOURS,
                    ),
                    "interval_minutes": _as_int(
                        os.getenv(
                            "UNVERIFIED_CHAT_CLEANUP_INTERVAL_MINUTES",
                            str(globals().get("UNVERIFIED_CHAT_CLEANUP_INTERVAL_MINUTES", DEFAULT_CLEANUP_INTERVAL_MINUTES)),
                        ),
                        DEFAULT_CLEANUP_INTERVAL_MINUTES,
                    ),
                    "skip_pinned": _as_bool(
                        os.getenv(
                            "UNVERIFIED_CHAT_SKIP_PINNED",
                            str(globals().get("UNVERIFIED_CHAT_SKIP_PINNED", DEFAULT_SKIP_PINNED)),
                        ),
                        DEFAULT_SKIP_PINNED,
                    ),
                    "label": "unverified-chat",
                },
            )
    except Exception:
        pass

    # Generic extra channels with shared defaults
    try:
        raw_ids = os.getenv("EXTRA_CLEANUP_CHANNEL_IDS", "").strip()
        if raw_ids:
            shared_hours = _as_float(
                os.getenv("EXTRA_CLEANUP_MAX_AGE_HOURS", str(DEFAULT_MAX_AGE_HOURS)),
                DEFAULT_MAX_AGE_HOURS,
            )
            shared_interval = _as_int(
                os.getenv("EXTRA_CLEANUP_INTERVAL_MINUTES", str(DEFAULT_CLEANUP_INTERVAL_MINUTES)),
                DEFAULT_CLEANUP_INTERVAL_MINUTES,
            )
            shared_skip_pinned = _as_bool(
                os.getenv("EXTRA_CLEANUP_SKIP_PINNED", str(DEFAULT_SKIP_PINNED)),
                DEFAULT_SKIP_PINNED,
            )

            for part in raw_ids.split(","):
                cid = _as_int(part, 0)
                if cid <= 0:
                    continue
                if cid not in out:
                    out[cid] = _normalize_rule(
                        cid,
                        {
                            "enabled": True,
                            "max_age_hours": shared_hours,
                            "interval_minutes": shared_interval,
                            "skip_pinned": shared_skip_pinned,
                            "label": f"extra-{cid}",
                        },
                    )
    except Exception:
        pass

    return out


def get_channel_cleanup_rules() -> Dict[int, Dict[str, Any]]:
    """
    Final resolved cleanup rules.

    Merge order:
    1) Python globals CHANNEL_CLEANUP_RULES
    2) Simple env shortcuts
    3) JSON env override (highest priority)
    """
    rules: Dict[int, Dict[str, Any]] = {}
    rules = _merge_rule_map(rules, _load_rules_from_python_globals())
    rules = _merge_rule_map(rules, _load_rules_from_simple_envs())
    rules = _merge_rule_map(rules, _load_rules_from_json_env())
    return rules


def get_channel_cleanup_rule(channel_id: int) -> Optional[Dict[str, Any]]:
    try:
        return get_channel_cleanup_rules().get(int(channel_id))
    except Exception:
        return None


# ============================================================
# Channel resolution
# ============================================================
async def resolve_text_channel_by_id(channel_id: int) -> Optional[discord.TextChannel]:
    cid = int(channel_id)
    if cid <= 0:
        return None

    try:
        ch = bot.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        guilds = list(getattr(bot, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            fetched = await guild.fetch_channel(cid)
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except Exception:
            continue

    return None


# ============================================================
# Permission checks
# ============================================================
async def _resolve_bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass

    try:
        if getattr(bot, "user", None):
            fetched = await guild.fetch_member(bot.user.id)  # type: ignore[arg-type]
            if isinstance(fetched, discord.Member):
                return fetched
    except Exception:
        pass

    return None


async def _can_cleanup_channel(channel: discord.TextChannel) -> Tuple[bool, str]:
    try:
        me = await _resolve_bot_member(channel.guild)
        if not me:
            return False, "Bot member missing."

        perms = channel.permissions_for(me)

        if not perms.view_channel:
            return False, "Missing View Channel."
        if not perms.read_message_history:
            return False, "Missing Read Message History."
        if not perms.manage_messages:
            return False, "Missing Manage Messages."

        return True, ""
    except Exception as e:
        return False, f"Permission check error: {e}"


# ============================================================
# Cleanup engine
# ============================================================
async def cleanup_text_channel(
    channel: discord.TextChannel,
    *,
    max_age_hours: float,
    skip_pinned: bool = True,
    dry_run: bool = False,
    hard_delete_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Delete messages older than max_age_hours from a text channel.

    Returns a stats dict.
    """
    stats: Dict[str, Any] = {
        "channel_id": int(channel.id),
        "channel_name": str(channel.name),
        "deleted": 0,
        "bulk_deleted": 0,
        "individually_deleted": 0,
        "scanned": 0,
        "skipped_pinned": 0,
        "eligible": 0,
        "errors": 0,
        "dry_run": bool(dry_run),
        "max_age_hours": float(max_age_hours),
    }

    max_age_hours = max(0.1, float(max_age_hours))
    cutoff = _utc_now() - timedelta(hours=max_age_hours)
    bulk_cutoff = _utc_now() - timedelta(days=BULK_DELETE_MAX_AGE_DAYS)

    ok, why = await _can_cleanup_channel(channel)
    if not ok:
        stats["error"] = why
        return stats

    bulk_bucket: List[discord.Message] = []
    single_bucket: List[discord.Message] = []

    try:
        async for msg in channel.history(limit=None, before=cutoff, oldest_first=False):
            stats["scanned"] += 1

            if skip_pinned and getattr(msg, "pinned", False):
                stats["skipped_pinned"] += 1
                continue

            stats["eligible"] += 1

            if hard_delete_limit is not None and stats["eligible"] > int(hard_delete_limit):
                break

            created_at = getattr(msg, "created_at", None)
            if created_at is None:
                single_bucket.append(msg)
                continue

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            if created_at >= bulk_cutoff:
                bulk_bucket.append(msg)
                if len(bulk_bucket) >= 100:
                    if dry_run:
                        stats["bulk_deleted"] += len(bulk_bucket)
                        stats["deleted"] += len(bulk_bucket)
                    else:
                        try:
                            deleted = await channel.delete_messages(bulk_bucket)
                            count = len(deleted) if deleted is not None else len(bulk_bucket)
                            stats["bulk_deleted"] += count
                            stats["deleted"] += count
                        except Exception as e:
                            stats["errors"] += 1
                            print(
                                f"⚠️ channel_cleanup bulk delete failed "
                                f"channel={channel.id} count={len(bulk_bucket)} error={repr(e)}"
                            )
                    bulk_bucket = []
            else:
                single_bucket.append(msg)

        if bulk_bucket:
            if dry_run:
                stats["bulk_deleted"] += len(bulk_bucket)
                stats["deleted"] += len(bulk_bucket)
            else:
                try:
                    deleted = await channel.delete_messages(bulk_bucket)
                    count = len(deleted) if deleted is not None else len(bulk_bucket)
                    stats["bulk_deleted"] += count
                    stats["deleted"] += count
                except Exception as e:
                    stats["errors"] += 1
                    print(
                        f"⚠️ channel_cleanup bulk tail delete failed "
                        f"channel={channel.id} count={len(bulk_bucket)} error={repr(e)}"
                    )

        if single_bucket:
            for idx, msg in enumerate(single_bucket, start=1):
                if dry_run:
                    stats["individually_deleted"] += 1
                    stats["deleted"] += 1
                else:
                    try:
                        await msg.delete()
                        stats["individually_deleted"] += 1
                        stats["deleted"] += 1
                    except Exception as e:
                        stats["errors"] += 1
                        print(
                            f"⚠️ channel_cleanup single delete failed "
                            f"channel={channel.id} message={getattr(msg, 'id', None)} error={repr(e)}"
                        )

                if idx % INDIVIDUAL_DELETE_PAUSE_EVERY == 0:
                    try:
                        await asyncio.sleep(INDIVIDUAL_DELETE_PAUSE_SECONDS)
                    except Exception:
                        pass

    except Exception as e:
        stats["errors"] += 1
        stats["error"] = repr(e)

    return stats


async def cleanup_channel_by_id(
    channel_id: int,
    *,
    max_age_hours: Optional[float] = None,
    skip_pinned: Optional[bool] = None,
    dry_run: bool = False,
    hard_delete_limit: Optional[int] = None,
) -> Dict[str, Any]:
    channel = await resolve_text_channel_by_id(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return {
            "channel_id": int(channel_id),
            "deleted": 0,
            "errors": 1,
            "error": "Channel not found or is not a text channel.",
            "dry_run": bool(dry_run),
        }

    rule = get_channel_cleanup_rule(int(channel_id)) or {}

    return await cleanup_text_channel(
        channel,
        max_age_hours=float(max_age_hours if max_age_hours is not None else rule.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)),
        skip_pinned=bool(skip_pinned if skip_pinned is not None else rule.get("skip_pinned", DEFAULT_SKIP_PINNED)),
        dry_run=bool(dry_run),
        hard_delete_limit=hard_delete_limit,
    )


async def run_channel_cleanup_once(*, dry_run: bool = False) -> Dict[str, Any]:
    """
    Run cleanup one time for all configured channels, ignoring interval scheduling.
    """
    rules = get_channel_cleanup_rules()
    summary: Dict[str, Any] = {
        "channels_seen": 0,
        "channels_cleaned": 0,
        "deleted": 0,
        "errors": 0,
        "results": [],
        "dry_run": bool(dry_run),
    }

    for cid, rule in rules.items():
        summary["channels_seen"] += 1

        if not _as_bool(rule.get("enabled", True), True):
            continue

        lock = _channel_lock(cid)
        if lock.locked():
            continue

        async with lock:
            result = await cleanup_channel_by_id(
                int(cid),
                max_age_hours=float(rule.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)),
                skip_pinned=bool(rule.get("skip_pinned", DEFAULT_SKIP_PINNED)),
                dry_run=bool(dry_run),
            )
            summary["results"].append(result)
            summary["deleted"] += int(result.get("deleted", 0) or 0)
            summary["errors"] += int(result.get("errors", 0) or 0)
            summary["channels_cleaned"] += 1

    return summary


# ============================================================
# Scheduled worker
# ============================================================
def _channel_due(rule: Dict[str, Any], channel_id: int) -> bool:
    try:
        interval_minutes = max(1, int(rule.get("interval_minutes", DEFAULT_CLEANUP_INTERVAL_MINUTES)))
        last = _CHANNEL_CLEANUP_LAST_RUN.get(int(channel_id))
        if last is None:
            return True
        return (_utc_now() - last) >= timedelta(minutes=interval_minutes)
    except Exception:
        return True


async def _run_due_cleanups_once() -> None:
    rules = get_channel_cleanup_rules()
    if not rules:
        return

    for cid, rule in rules.items():
        try:
            channel_id = int(cid)
        except Exception:
            continue

        if not _as_bool(rule.get("enabled", True), True):
            continue

        if not _channel_due(rule, channel_id):
            continue

        lock = _channel_lock(channel_id)
        if lock.locked():
            continue

        async with lock:
            try:
                result = await cleanup_channel_by_id(
                    channel_id,
                    max_age_hours=float(rule.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)),
                    skip_pinned=bool(rule.get("skip_pinned", DEFAULT_SKIP_PINNED)),
                    dry_run=False,
                )
                _CHANNEL_CLEANUP_LAST_RUN[channel_id] = _utc_now()

                deleted = int(result.get("deleted", 0) or 0)
                errors = int(result.get("errors", 0) or 0)

                print(
                    f"🧹 channel_cleanup: channel={channel_id} "
                    f"label={rule.get('label')} deleted={deleted} errors={errors}"
                )
            except Exception as e:
                _CHANNEL_CLEANUP_LAST_RUN[channel_id] = _utc_now()
                print(
                    f"⚠️ channel_cleanup: scheduled cleanup failed "
                    f"channel={channel_id} error={repr(e)}"
                )


async def _channel_cleanup_loop() -> None:
    print("🧹 channel_cleanup: worker started")

    while True:
        try:
            await _run_due_cleanups_once()
        except asyncio.CancelledError:
            print("🛑 channel_cleanup: worker cancelled")
            raise
        except Exception as e:
            print(f"⚠️ channel_cleanup: worker loop error: {repr(e)}")

        try:
            await asyncio.sleep(WORKER_TICK_SECONDS)
        except asyncio.CancelledError:
            print("🛑 channel_cleanup: worker sleep cancelled")
            raise


async def ensure_channel_cleanup_worker_started() -> bool:
    """
    Start the background cleanup worker once.
    Returns True if running (new or already running).
    """
    global _CHANNEL_CLEANUP_TASK

    try:
        existing = _CHANNEL_CLEANUP_TASK
        if existing and not existing.done():
            return True
    except Exception:
        pass

    try:
        task = asyncio.create_task(_channel_cleanup_loop())
        _CHANNEL_CLEANUP_TASK = task
        try:
            if hasattr(bot, "_channel_cleanup_task"):
                bot._channel_cleanup_task = task  # type: ignore[attr-defined]
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"⚠️ channel_cleanup: failed to start worker: {repr(e)}")
        return False


async def stop_channel_cleanup_worker() -> bool:
    global _CHANNEL_CLEANUP_TASK

    task = _CHANNEL_CLEANUP_TASK
    if not task or task.done():
        return False

    try:
        task.cancel()
    except Exception:
        return False

    _CHANNEL_CLEANUP_TASK = None
    return True


def channel_cleanup_worker_running() -> bool:
    try:
        return bool(_CHANNEL_CLEANUP_TASK and not _CHANNEL_CLEANUP_TASK.done())
    except Exception:
        return False


# ============================================================
# Human-readable helper for later slash commands
# ============================================================
def format_channel_cleanup_rules() -> List[str]:
    lines: List[str] = []
    rules = get_channel_cleanup_rules()

    for cid, rule in sorted(rules.items(), key=lambda item: int(item[0])):
        try:
            lines.append(
                f"- channel_id={int(cid)} "
                f"enabled={bool(rule.get('enabled', True))} "
                f"max_age_hours={rule.get('max_age_hours')} "
                f"interval_minutes={rule.get('interval_minutes')} "
                f"skip_pinned={bool(rule.get('skip_pinned', DEFAULT_SKIP_PINNED))} "
                f"label={rule.get('label')}"
            )
        except Exception:
            continue

    return lines


__all__ = [
    "get_channel_cleanup_rules",
    "get_channel_cleanup_rule",
    "resolve_text_channel_by_id",
    "cleanup_text_channel",
    "cleanup_channel_by_id",
    "run_channel_cleanup_once",
    "ensure_channel_cleanup_worker_started",
    "stop_channel_cleanup_worker",
    "channel_cleanup_worker_running",
    "format_channel_cleanup_rules",
]