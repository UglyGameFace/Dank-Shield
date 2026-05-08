from __future__ import annotations

"""Discord API safety guard for production stability.

This guard addresses the exact log patterns seen in production:

- repeated audit-log 429s
- transient Discord 5xx/no-healthy-upstream errors while sending modlogs
- bursty channel edits during ticket close/rename flows

It does not change business rules. It only serializes and retries Discord API
calls in places where Discord itself is telling us to slow down.
"""

import asyncio
import os
import time
from collections import defaultdict
from typing import Any, AsyncIterator, DefaultDict, Optional

import discord

_PATCHED = False
_ORIGINAL_GUILD_AUDIT_LOGS = None
_ORIGINAL_TEXT_CHANNEL_SEND = None
_ORIGINAL_THREAD_SEND = None
_ORIGINAL_DM_SEND = None
_ORIGINAL_TEXT_CHANNEL_EDIT = None
_ORIGINAL_VOICE_CHANNEL_EDIT = None
_ORIGINAL_STAGE_CHANNEL_EDIT = None

_AUDIT_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_AUDIT_LAST_CALL: dict[int, float] = {}
_AUDIT_LAST_RATE_LIMIT: dict[int, float] = {}

_CHANNEL_EDIT_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_CHANNEL_LAST_EDIT: dict[int, float] = {}


def _log(message: str) -> None:
    try:
        print(f"🧯 discord_api_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ discord_api_safety {message}")
    except Exception:
        pass


def _env_float(name: str, default: float) -> float:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            return float(default)
        return max(0.0, float(raw))
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            return int(default)
        return max(0, int(raw))
    except Exception:
        return int(default)


def _audit_cooldown_seconds() -> float:
    return _env_float("DANK_AUDIT_LOG_COOLDOWN_SECONDS", 6.0)


def _audit_after_429_cooldown_seconds() -> float:
    return _env_float("DANK_AUDIT_LOG_429_COOLDOWN_SECONDS", 45.0)


def _channel_edit_gap_seconds() -> float:
    return _env_float("DANK_CHANNEL_EDIT_GAP_SECONDS", 1.5)


def _send_retry_attempts() -> int:
    return max(1, min(5, _env_int("DANK_DISCORD_SEND_RETRY_ATTEMPTS", 3)))


def _is_retryable_discord_error(error: BaseException) -> bool:
    try:
        status = int(getattr(error, "status", 0) or 0)
        if status in {429, 500, 502, 503, 504}:
            return True
    except Exception:
        pass

    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "no healthy upstream",
            "service unavailable",
            "temporarily unavailable",
            "gateway timeout",
            "bad gateway",
            "rate limited",
            "429",
            "503",
            "502",
            "504",
        )
    )


def _retry_after(error: BaseException, fallback: float) -> float:
    try:
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            return max(float(retry_after), float(fallback))
    except Exception:
        pass
    return float(fallback)


async def _sleep_until_allowed(last_map: dict[int, float], key: int, gap_seconds: float) -> None:
    try:
        now = time.monotonic()
        last = float(last_map.get(int(key), 0.0) or 0.0)
        wait_for = (last + float(gap_seconds)) - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
    except Exception:
        pass


async def _guarded_audit_logs(self: discord.Guild, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
    """Serialize audit-log requests per guild and back off after 429s.

    discord.py's method returns an async iterator. This async generator preserves
    that behavior while protecting every caller across the bot.
    """
    if _ORIGINAL_GUILD_AUDIT_LOGS is None:
        return

    guild_id = int(getattr(self, "id", 0) or 0)
    lock = _AUDIT_LOCKS[guild_id]

    async with lock:
        now = time.monotonic()
        last_429 = float(_AUDIT_LAST_RATE_LIMIT.get(guild_id, 0.0) or 0.0)
        if last_429 > 0:
            wait_429 = (last_429 + _audit_after_429_cooldown_seconds()) - now
            if wait_429 > 0:
                _warn(f"audit-log cooldown after 429 guild={guild_id} wait={wait_429:.1f}s")
                await asyncio.sleep(wait_429)

        await _sleep_until_allowed(_AUDIT_LAST_CALL, guild_id, _audit_cooldown_seconds())
        _AUDIT_LAST_CALL[guild_id] = time.monotonic()

        try:
            iterator = _ORIGINAL_GUILD_AUDIT_LOGS(self, *args, **kwargs)
            async for entry in iterator:
                yield entry
        except discord.HTTPException as e:
            status = int(getattr(e, "status", 0) or 0)
            if status == 429 or "429" in repr(e):
                _AUDIT_LAST_RATE_LIMIT[guild_id] = time.monotonic()
                _warn(f"audit-log 429 captured guild={guild_id}; callers will use fallback/cached data")
                return
            raise


async def _retrying_send(original, self: Any, *args: Any, **kwargs: Any) -> Any:
    attempts = _send_retry_attempts()
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        try:
            return await original(self, *args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt >= attempts or not _is_retryable_discord_error(e):
                raise
            wait_for = _retry_after(e, min(2.0 * attempt, 8.0))
            channel_id = getattr(self, "id", "unknown")
            _warn(f"send retry channel={channel_id} attempt={attempt}/{attempts} wait={wait_for:.1f}s error={type(e).__name__}")
            await asyncio.sleep(wait_for)

    if last_error is not None:
        raise last_error
    return None


async def _retrying_channel_edit(original, self: Any, *args: Any, **kwargs: Any) -> Any:
    channel_id = int(getattr(self, "id", 0) or 0)
    lock = _CHANNEL_EDIT_LOCKS[channel_id]
    async with lock:
        await _sleep_until_allowed(_CHANNEL_LAST_EDIT, channel_id, _channel_edit_gap_seconds())
        _CHANNEL_LAST_EDIT[channel_id] = time.monotonic()

        attempts = 3
        last_error: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return await original(self, *args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt >= attempts or not _is_retryable_discord_error(e):
                    raise
                wait_for = _retry_after(e, min(2.0 * attempt, 8.0))
                _warn(f"channel edit retry channel={channel_id} attempt={attempt}/{attempts} wait={wait_for:.1f}s error={type(e).__name__}")
                await asyncio.sleep(wait_for)
        if last_error is not None:
            raise last_error
    return None


def _patch_send_method(cls: Any, attr_name: str = "send") -> None:
    original = getattr(cls, attr_name, None)
    if not callable(original) or getattr(original, "_discord_api_safety_wrapped", False):
        return

    async def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return await _retrying_send(original, self, *args, **kwargs)

    try:
        setattr(_wrapped, "_discord_api_safety_wrapped", True)
    except Exception:
        pass
    setattr(cls, attr_name, _wrapped)


def _patch_edit_method(cls: Any, attr_name: str = "edit") -> None:
    original = getattr(cls, attr_name, None)
    if not callable(original) or getattr(original, "_discord_api_safety_wrapped", False):
        return

    async def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return await _retrying_channel_edit(original, self, *args, **kwargs)

    try:
        setattr(_wrapped, "_discord_api_safety_wrapped", True)
    except Exception:
        pass
    setattr(cls, attr_name, _wrapped)


def install_discord_api_safety() -> None:
    global _PATCHED, _ORIGINAL_GUILD_AUDIT_LOGS
    if _PATCHED:
        return

    original_audit = getattr(discord.Guild, "audit_logs", None)
    if callable(original_audit) and not getattr(original_audit, "_discord_api_safety_wrapped", False):
        _ORIGINAL_GUILD_AUDIT_LOGS = original_audit
        try:
            setattr(_guarded_audit_logs, "_discord_api_safety_wrapped", True)
        except Exception:
            pass
        discord.Guild.audit_logs = _guarded_audit_logs  # type: ignore[method-assign]

    for cls in (discord.TextChannel, discord.Thread, discord.DMChannel):
        try:
            _patch_send_method(cls)
        except Exception as e:
            _warn(f"failed patching {cls}.send: {e!r}")

    for cls in (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel):
        try:
            _patch_edit_method(cls)
        except Exception as e:
            _warn(f"failed patching {cls}.edit: {e!r}")

    _PATCHED = True
    _log("active; audit logs serialized, Discord sends retried, channel edits queued")


install_discord_api_safety()

__all__ = ["install_discord_api_safety"]
