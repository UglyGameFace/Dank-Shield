from __future__ import annotations

"""Pure in-memory burst detectors for server abuse surfaces.

The detector owns no Discord moderation actions. Runtime adapters decide how an
identified burst is handled using existing SpamGuard/AntiNuke policy.
"""

import time
from collections import Counter, defaultdict, deque
from typing import Any, Deque, Optional

WEBHOOK_WINDOW_SECONDS = 5.0
WEBHOOK_MESSAGE_THRESHOLD = 18
CHANNEL_WINDOW_SECONDS = 6.0
CHANNEL_DUPLICATE_THRESHOLD = 14
CHANNEL_DUPLICATE_ACTORS = 4
CHANNEL_DUPLICATE_FINGERPRINT = 6
CHANNEL_VOLUME_THRESHOLD = 20
CHANNEL_VOLUME_ACTORS = 5
REACTION_WINDOW_SECONDS = 5.0
REACTION_THRESHOLD = 18
VOICE_WINDOW_SECONDS = 20.0
VOICE_TRANSITION_THRESHOLD = 8

_WEBHOOK_WINDOWS: dict[tuple[int, int], Deque[tuple[float, Any]]] = defaultdict(deque)
_CHANNEL_WINDOWS: dict[tuple[int, int], Deque[tuple[float, int, str, Any, Any]]] = defaultdict(deque)
_REACTION_WINDOWS: dict[tuple[int, int], Deque[float]] = defaultdict(deque)
_VOICE_WINDOWS: dict[tuple[int, int], Deque[float]] = defaultdict(deque)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _prune_times(window: Deque[float], now: float, seconds: float) -> None:
    cutoff = now - float(seconds)
    while window and window[0] < cutoff:
        window.popleft()


def record_webhook(message: Any, *, now: Optional[float] = None) -> tuple[bool, list[Any]]:
    stamp = time.monotonic() if now is None else float(now)
    guild_id = _safe_int(getattr(getattr(message, "guild", None), "id", 0), 0)
    webhook_id = _safe_int(getattr(message, "webhook_id", 0), 0)
    if guild_id <= 0 or webhook_id <= 0:
        return False, []
    key = (guild_id, webhook_id)
    window = _WEBHOOK_WINDOWS[key]
    cutoff = stamp - WEBHOOK_WINDOW_SECONDS
    while window and window[0][0] < cutoff:
        window.popleft()
    window.append((stamp, message))
    return len(window) >= WEBHOOK_MESSAGE_THRESHOLD, [row[1] for row in window]


def record_channel(
    message: Any,
    *,
    fingerprint: str,
    now: Optional[float] = None,
) -> tuple[bool, list[tuple[float, int, str, Any, Any]]]:
    stamp = time.monotonic() if now is None else float(now)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    channel_id = _safe_int(getattr(channel, "id", 0), 0)
    actor_id = _safe_int(getattr(author, "id", 0), 0)
    if guild_id <= 0 or channel_id <= 0 or actor_id <= 0:
        return False, []
    key = (guild_id, channel_id)
    window = _CHANNEL_WINDOWS[key]
    cutoff = stamp - CHANNEL_WINDOW_SECONDS
    while window and window[0][0] < cutoff:
        window.popleft()
    window.append((stamp, actor_id, str(fingerprint or "<empty>")[:250], message, author))

    actors = {row[1] for row in window}
    fingerprints = Counter(row[2] for row in window)
    repeated = max(fingerprints.values(), default=0)
    duplicate_attack = (
        len(window) >= CHANNEL_DUPLICATE_THRESHOLD
        and len(actors) >= CHANNEL_DUPLICATE_ACTORS
        and repeated >= CHANNEL_DUPLICATE_FINGERPRINT
    )
    volume_attack = len(window) >= CHANNEL_VOLUME_THRESHOLD and len(actors) >= CHANNEL_VOLUME_ACTORS
    return bool(duplicate_attack or volume_attack), list(window)


def record_reaction(guild_id: int, actor_id: int, *, now: Optional[float] = None) -> bool:
    stamp = time.monotonic() if now is None else float(now)
    key = (int(guild_id), int(actor_id))
    if min(key) <= 0:
        return False
    window = _REACTION_WINDOWS[key]
    _prune_times(window, stamp, REACTION_WINDOW_SECONDS)
    window.append(stamp)
    return len(window) >= REACTION_THRESHOLD


def record_voice(guild_id: int, actor_id: int, *, now: Optional[float] = None) -> bool:
    stamp = time.monotonic() if now is None else float(now)
    key = (int(guild_id), int(actor_id))
    if min(key) <= 0:
        return False
    window = _VOICE_WINDOWS[key]
    _prune_times(window, stamp, VOICE_WINDOW_SECONDS)
    window.append(stamp)
    return len(window) >= VOICE_TRANSITION_THRESHOLD


def reset_for_tests() -> None:
    _WEBHOOK_WINDOWS.clear()
    _CHANNEL_WINDOWS.clear()
    _REACTION_WINDOWS.clear()
    _VOICE_WINDOWS.clear()


__all__ = [
    "CHANNEL_DUPLICATE_ACTORS",
    "CHANNEL_DUPLICATE_FINGERPRINT",
    "CHANNEL_DUPLICATE_THRESHOLD",
    "CHANNEL_VOLUME_ACTORS",
    "CHANNEL_VOLUME_THRESHOLD",
    "CHANNEL_WINDOW_SECONDS",
    "REACTION_THRESHOLD",
    "REACTION_WINDOW_SECONDS",
    "VOICE_TRANSITION_THRESHOLD",
    "VOICE_WINDOW_SECONDS",
    "WEBHOOK_MESSAGE_THRESHOLD",
    "WEBHOOK_WINDOW_SECONDS",
    "record_channel",
    "record_reaction",
    "record_voice",
    "record_webhook",
    "reset_for_tests",
]
