from __future__ import annotations

"""
Spam Guard cleanup hardening.

The core spam guard already detects hacked-account bursts and calls
`_select_cleanup_refs` to decide which recent messages should be removed. In a
real burst, only deleting the exact trigger message is not good enough: spam can
land across multiple channels before the enforcement action finishes.

This patch keeps the existing detection/enforcement path, widens cleanup
selection when a high-confidence rule fires, and adds a small post-detection
history sweep that removes leftover messages from the same offending account.

Safety rules:
- never deletes messages from unrelated users
- only runs while Spam Guard is enabled for that guild
- skips bots, webhooks, staff-ish members, and channels the bot cannot manage
- caps deletes per offender/burst
"""

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

import discord

_PATCHED = False
_LISTENER_REGISTERED = False
_ORIGINAL_SELECT_CLEANUP_REFS = None

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@(everyone|here)\b", re.IGNORECASE)

SWEEP_WINDOW_SECONDS = 90
SWEEP_TRIGGER_SECONDS = 18
SWEEP_RECENT_ALL_USER_SECONDS = 45
SWEEP_MAX_TRACKED_PER_USER = 120
SWEEP_MAX_DELETE_PER_BURST = 50
SWEEP_MAX_CHANNELS_PER_BURST = 16
SWEEP_HISTORY_LIMIT_PER_CHANNEL = 75
SWEEP_COOLDOWN_SECONDS = 4.0

_BURST_WINDOWS: Dict[Tuple[int, int], Deque[Dict[str, Any]]] = {}
_SWEEP_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}
_LAST_SWEEP_AT: Dict[Tuple[int, int], float] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _row_identity(row: Dict[str, Any]) -> Tuple[int, int]:
    return (_safe_int(row.get("channel_id"), 0), _safe_int(row.get("message_id"), 0))


def _row_evidence(row: Dict[str, Any]) -> Set[str]:
    raw = row.get("evidence")
    if isinstance(raw, set):
        return {str(x) for x in raw if str(x).strip()}
    if isinstance(raw, list):
        return {str(x) for x in raw if str(x).strip()}
    return set()


def _has_any_suspicious_evidence(row: Dict[str, Any]) -> bool:
    evidence = _row_evidence(row)
    if evidence.intersection({"blocked_invite", "invite_url", "non_invite_url", "everyone_ping"}):
        return True
    try:
        if _safe_int(row.get("invite_count"), 0) > 0:
            return True
        if _safe_int(row.get("non_invite_url_count"), 0) > 0:
            return True
    except Exception:
        pass
    return False


def _hardened_select_cleanup_refs(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    original = _ORIGINAL_SELECT_CLEANUP_REFS
    if not callable(original):
        return []

    selected = list(original(*args, **kwargs) or [])

    recent_messages = list(kwargs.get("recent_messages") or [])
    delete_limit = _safe_int(kwargs.get("delete_limit"), 0)
    if delete_limit <= 0 or not recent_messages:
        return selected[: max(0, delete_limit)]

    fired_invite_rule = bool(kwargs.get("fired_invite_rule"))
    fired_duplicate_rule = bool(kwargs.get("fired_duplicate_rule"))
    fired_everyone_rule = bool(kwargs.get("fired_everyone_rule"))
    fired_url_rule = bool(kwargs.get("fired_url_rule"))
    fired_channel_flood_rule = bool(kwargs.get("fired_channel_flood_rule"))
    current_norm = str(kwargs.get("current_norm") or "")

    high_confidence = any(
        (
            fired_invite_rule,
            fired_duplicate_rule,
            fired_everyone_rule,
            fired_url_rule,
            fired_channel_flood_rule,
        )
    )
    if not high_confidence:
        return selected[:delete_limit]

    ordered = sorted(
        [r for r in recent_messages if isinstance(r, dict)],
        key=lambda x: float(x.get("ts", 0.0) or 0.0),
        reverse=True,
    )

    seen: Set[Tuple[int, int]] = set()
    hardened: List[Dict[str, Any]] = []

    def add(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            ident = _row_identity(row)
            if ident == (0, 0) or ident in seen:
                continue
            seen.add(ident)
            hardened.append(row)
            if len(hardened) >= delete_limit:
                return

    # Keep the original function's exact priority first.
    add([r for r in selected if isinstance(r, dict)])

    # Then add any directly suspicious rows the original selector missed.
    if len(hardened) < delete_limit:
        add([r for r in ordered if _has_any_suspicious_evidence(r)])

    # Duplicate spam often has little evidence besides matching normalized body.
    if len(hardened) < delete_limit and current_norm:
        add([r for r in ordered if str(r.get("norm") or "") == current_norm])

    # For high-confidence compromise behavior, sweep the rest of that user's
    # recent window up to the configured limit. The window is already keyed by
    # guild/user in spam_guard, so this should not touch unrelated users.
    if len(hardened) < delete_limit:
        add(ordered)

    return hardened[:delete_limit]


def _now_ts() -> float:
    return time.monotonic()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _message_created_ts(message: discord.Message) -> float:
    try:
        created = message.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return float(created.timestamp())
    except Exception:
        return float(time.time())


def _normalize_content(content: str) -> str:
    text = str(content or "").lower()
    text = INVITE_RE.sub("<invite>", text)
    text = URL_RE.sub("<url>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:250]


def _message_is_everyone_ping(message: discord.Message) -> bool:
    try:
        if bool(getattr(message, "mention_everyone", False)):
            return True
    except Exception:
        pass
    try:
        return bool(MENTION_RE.search(str(message.content or "")))
    except Exception:
        return False


def _evidence_for_message(message: discord.Message) -> Set[str]:
    content = str(getattr(message, "content", "") or "")
    evidence: Set[str] = set()
    if _message_is_everyone_ping(message):
        evidence.add("everyone_ping")
    if INVITE_RE.search(content):
        evidence.add("invite_url")
    urls = URL_RE.findall(content)
    if urls:
        evidence.add("url")
        if any(not INVITE_RE.search(u) for u in urls):
            evidence.add("non_invite_url")
    return evidence


def _is_suspicious_row(row: Dict[str, Any]) -> bool:
    evidence = _row_evidence(row)
    if evidence.intersection({"everyone_ping", "invite_url", "url", "non_invite_url", "blocked_invite"}):
        return True
    content = str(row.get("content") or "")
    return bool(INVITE_RE.search(content) or URL_RE.search(content) or MENTION_RE.search(content))


def _is_staffish(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        perms = member.guild_permissions
        return bool(
            perms.administrator
            or perms.manage_guild
            or perms.manage_channels
            or perms.manage_messages
            or perms.kick_members
            or perms.ban_members
            or perms.moderate_members
        )
    except Exception:
        return False


def _spam_guard_enabled(guild_id: int) -> bool:
    try:
        from .. import spam_guard

        getter = getattr(spam_guard, "_fast_settings_for_ui", None)
        if callable(getter):
            settings = getter(int(guild_id)) or {}
            return bool(settings.get("enabled", settings.get("spam_blocker_enabled", False)))
    except Exception:
        pass
    return False


def _channel_manageable(channel: Any, guild: discord.Guild) -> bool:
    try:
        me = guild.me
        if me is None or not isinstance(channel, discord.abc.GuildChannel):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.read_message_history and perms.manage_messages)
    except Exception:
        return False


def _make_row(message: discord.Message) -> Dict[str, Any]:
    evidence = _evidence_for_message(message)
    return {
        "message": message,
        "guild_id": int(message.guild.id) if message.guild else 0,
        "author_id": int(message.author.id),
        "channel_id": int(message.channel.id),
        "message_id": int(message.id),
        "content": str(getattr(message, "content", "") or ""),
        "norm": _normalize_content(str(getattr(message, "content", "") or "")),
        "evidence": list(sorted(evidence)),
        "ts": _now_ts(),
        "created_ts": _message_created_ts(message),
    }


def _prune_window(window: Deque[Dict[str, Any]]) -> None:
    cutoff = _now_ts() - float(SWEEP_WINDOW_SECONDS)
    while window and float(window[0].get("ts", 0.0) or 0.0) < cutoff:
        window.popleft()
    while len(window) > SWEEP_MAX_TRACKED_PER_USER:
        window.popleft()


def _trigger_reason(window: Iterable[Dict[str, Any]], current: Dict[str, Any]) -> Optional[str]:
    now = _now_ts()
    recent = [r for r in window if (now - float(r.get("ts", 0.0) or 0.0)) <= SWEEP_TRIGGER_SECONDS]
    if not recent:
        return None

    suspicious = [r for r in recent if _is_suspicious_row(r)]
    if not suspicious:
        return None

    everyone_count = sum(1 for r in recent if "everyone_ping" in _row_evidence(r))
    invite_count = sum(1 for r in recent if "invite_url" in _row_evidence(r) or "blocked_invite" in _row_evidence(r))
    url_count = sum(1 for r in recent if _row_evidence(r).intersection({"url", "non_invite_url", "invite_url", "blocked_invite"}))
    channels = {int(r.get("channel_id") or 0) for r in recent if int(r.get("channel_id") or 0) > 0}

    current_norm = str(current.get("norm") or "")
    duplicate_count = 0
    if current_norm:
        duplicate_count = sum(1 for r in recent if str(r.get("norm") or "") == current_norm)

    if everyone_count >= 2:
        return "everyone_burst"
    if invite_count >= 2:
        return "invite_burst"
    if url_count >= 3:
        return "url_burst"
    if url_count >= 2 and len(channels) >= 2:
        return "multi_channel_url_burst"
    if duplicate_count >= 3 and _is_suspicious_row(current):
        return "duplicate_suspicious_burst"
    if len(suspicious) >= 2 and len(channels) >= 2:
        return "multi_channel_suspicious_burst"

    return None


def _candidate_rows_for_delete(window: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = _now_ts()
    rows = [r for r in window if (now - float(r.get("ts", 0.0) or 0.0)) <= SWEEP_WINDOW_SECONDS]
    rows.sort(key=lambda r: float(r.get("ts", 0.0) or 0.0), reverse=True)

    selected: List[Dict[str, Any]] = []
    seen: Set[Tuple[int, int]] = set()

    def add(batch: Iterable[Dict[str, Any]]) -> None:
        for row in batch:
            ident = _row_identity(row)
            if ident == (0, 0) or ident in seen:
                continue
            seen.add(ident)
            selected.append(row)
            if len(selected) >= SWEEP_MAX_DELETE_PER_BURST:
                return

    add([r for r in rows if _is_suspicious_row(r)])
    if len(selected) < SWEEP_MAX_DELETE_PER_BURST:
        add([r for r in rows if (now - float(r.get("ts", 0.0) or 0.0)) <= SWEEP_RECENT_ALL_USER_SECONDS])
    return selected[:SWEEP_MAX_DELETE_PER_BURST]


async def _delete_message_object(message: discord.Message) -> bool:
    try:
        guild = message.guild
        if guild is None or not _channel_manageable(message.channel, guild):
            return False
        await message.delete(reason="Stoney Spam Guard burst cleanup sweep")
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False
    except Exception:
        return False


async def _history_candidates(
    *,
    guild: discord.Guild,
    author_id: int,
    channel_ids: Iterable[int],
) -> List[discord.Message]:
    cutoff_dt = _utc_now() - timedelta(seconds=SWEEP_WINDOW_SECONDS)
    recent_all_cutoff = _utc_now() - timedelta(seconds=SWEEP_RECENT_ALL_USER_SECONDS)
    out: List[discord.Message] = []
    seen: Set[int] = set()

    for channel_id in list(dict.fromkeys(int(x) for x in channel_ids if int(x) > 0))[:SWEEP_MAX_CHANNELS_PER_BURST]:
        channel = guild.get_channel(int(channel_id))
        if channel is None or not _channel_manageable(channel, guild):
            continue
        history = getattr(channel, "history", None)
        if not callable(history):
            continue
        try:
            async for msg in channel.history(limit=SWEEP_HISTORY_LIMIT_PER_CHANNEL, after=cutoff_dt, oldest_first=False):
                try:
                    if int(msg.id) in seen or int(msg.author.id) != int(author_id):
                        continue
                    if _evidence_for_message(msg) or msg.created_at >= recent_all_cutoff:
                        seen.add(int(msg.id))
                        out.append(msg)
                    if len(out) >= SWEEP_MAX_DELETE_PER_BURST:
                        return out
                except Exception:
                    continue
        except discord.Forbidden:
            continue
        except discord.HTTPException:
            continue
        except Exception:
            continue

    return out


async def _sweep_recent_messages(
    *,
    guild: discord.Guild,
    author_id: int,
    reason: str,
) -> None:
    key = (int(guild.id), int(author_id))
    lock = _SWEEP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SWEEP_LOCKS[key] = lock

    async with lock:
        now = _now_ts()
        last = float(_LAST_SWEEP_AT.get(key, 0.0) or 0.0)
        if (now - last) < SWEEP_COOLDOWN_SECONDS:
            return
        _LAST_SWEEP_AT[key] = now

        window = _BURST_WINDOWS.get(key) or deque()
        _prune_window(window)
        cached_rows = _candidate_rows_for_delete(window)
        channel_ids = {int(r.get("channel_id") or 0) for r in cached_rows if int(r.get("channel_id") or 0) > 0}
        channel_ids.update(int(r.get("channel_id") or 0) for r in window if int(r.get("channel_id") or 0) > 0)

        deleted = 0
        seen_message_ids: Set[int] = set()

        # Delete cached message objects first. These are most reliable because
        # they come directly from on_message before Discord may evict cache.
        for row in cached_rows:
            if deleted >= SWEEP_MAX_DELETE_PER_BURST:
                break
            msg = row.get("message")
            if not isinstance(msg, discord.Message):
                continue
            try:
                if int(msg.id) in seen_message_ids or int(msg.author.id) != int(author_id):
                    continue
                seen_message_ids.add(int(msg.id))
            except Exception:
                continue
            if await _delete_message_object(msg):
                deleted += 1

        # Then sweep recent channel history for leftovers from the same author.
        if deleted < SWEEP_MAX_DELETE_PER_BURST and channel_ids:
            history_messages = await _history_candidates(
                guild=guild,
                author_id=author_id,
                channel_ids=channel_ids,
            )
            for msg in history_messages:
                if deleted >= SWEEP_MAX_DELETE_PER_BURST:
                    break
                try:
                    if int(msg.id) in seen_message_ids:
                        continue
                    seen_message_ids.add(int(msg.id))
                except Exception:
                    continue
                if await _delete_message_object(msg):
                    deleted += 1

        if deleted > 0:
            try:
                print(
                    "🧹 public_spam_cleanup_hardening swept "
                    f"guild={guild.id} user={author_id} deleted={deleted} channels={len(channel_ids)} reason={reason}"
                )
            except Exception:
                pass


def _should_track_message(message: discord.Message) -> bool:
    try:
        if message.guild is None:
            return False
        if message.author.bot:
            return False
        if getattr(message, "webhook_id", None):
            return False
        if isinstance(message.author, discord.Member) and _is_staffish(message.author):
            return False
        if not _spam_guard_enabled(int(message.guild.id)):
            return False
        return True
    except Exception:
        return False


async def _post_enforcement_sweep_listener(message: discord.Message) -> None:
    if not _should_track_message(message):
        return

    try:
        guild = message.guild
        if guild is None:
            return
        key = (int(guild.id), int(message.author.id))
        window = _BURST_WINDOWS.get(key)
        if window is None:
            window = deque()
            _BURST_WINDOWS[key] = window

        row = _make_row(message)
        window.append(row)
        _prune_window(window)

        reason = _trigger_reason(window, row)
        if not reason:
            return

        # Let the core Spam Guard enforcement start first, then sweep leftovers.
        await asyncio.sleep(0.85)
        await _sweep_recent_messages(guild=guild, author_id=int(message.author.id), reason=reason)
    except Exception as e:
        try:
            print(f"⚠️ public_spam_cleanup_hardening sweep listener failed: {repr(e)}")
        except Exception:
            pass


def apply_spam_cleanup_hardening() -> bool:
    global _PATCHED, _ORIGINAL_SELECT_CLEANUP_REFS
    if _PATCHED:
        return True
    try:
        from .. import spam_guard

        original = getattr(spam_guard, "_select_cleanup_refs", None)
        if not callable(original):
            print("⚠️ public_spam_cleanup_hardening: spam_guard._select_cleanup_refs missing; skipped")
            return False
        if getattr(original, "_stoney_hardened_cleanup", False):
            _PATCHED = True
            return True

        _ORIGINAL_SELECT_CLEANUP_REFS = original
        setattr(_hardened_select_cleanup_refs, "_stoney_hardened_cleanup", True)
        spam_guard._select_cleanup_refs = _hardened_select_cleanup_refs  # type: ignore[attr-defined]
        _PATCHED = True
        print("✅ public_spam_cleanup_hardening: burst cleanup selection widened")
        return True
    except Exception as e:
        try:
            print(f"⚠️ public_spam_cleanup_hardening failed: {repr(e)}")
        except Exception:
            pass
        return False


def register_public_spam_cleanup_hardening(bot, tree) -> None:
    global _LISTENER_REGISTERED
    _ = tree
    apply_spam_cleanup_hardening()

    if _LISTENER_REGISTERED:
        return
    try:
        bot.add_listener(_post_enforcement_sweep_listener, "on_message")
        _LISTENER_REGISTERED = True
        print("✅ public_spam_cleanup_hardening: post-enforcement history sweep listener registered")
    except Exception as e:
        try:
            print(f"⚠️ public_spam_cleanup_hardening listener registration failed: {repr(e)}")
        except Exception:
            pass


__all__ = ["register_public_spam_cleanup_hardening", "apply_spam_cleanup_hardening"]
