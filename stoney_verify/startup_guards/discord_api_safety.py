from __future__ import annotations

"""Discord API safety guard for production stability.

This guard addresses the exact log patterns seen in production:

- repeated audit-log 429s
- transient Discord 5xx/no-healthy-upstream errors while sending modlogs
- bursty channel edits during ticket close/rename flows

It does not change business rules. It serializes and retries Discord API calls in
places where Discord itself is telling us to slow down. Security-critical AntiNuke
audit reads remain serialized and honor real 429 backoff, but they do not sit behind
the generic six-second spacing used for non-urgent audit lookups.
"""

import asyncio
import os
import time
from collections import defaultdict
from typing import Any, AsyncIterator, DefaultDict, Optional

import discord

_PATCHED = False
_ORIGINAL_GUILD_AUDIT_LOGS = None
_AUDIT_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_AUDIT_LAST_CALL: dict[int, float] = {}
_AUDIT_LAST_RATE_LIMIT: dict[int, float] = {}
_CHANNEL_EDIT_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_CHANNEL_LAST_EDIT: dict[int, float] = {}

# AntiNuke deliberately searches 50 recent entries for these high-risk actions.
# That request shape is the narrow contract that lets the existing API safety layer
# distinguish live security attribution from ordinary logging/diagnostic lookups
# without importing AntiNuke or creating another listener/runtime owner.
_SECURITY_PRIORITY_AUDIT_LIMIT = 50
_SECURITY_PRIORITY_AUDIT_ACTION_NAMES = frozenset(
    {
        "channel_create",
        "channel_update",
        "channel_delete",
        "role_delete",
        "ban",
        "kick",
        "member_prune",
        "role_update",
        "member_role_update",
        "member_update",
        "role_create",
        "bot_add",
        "webhook_create",
        "webhook_update",
        "webhook_delete",
    }
)


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


def _audit_action_name(action: Any) -> str:
    name = getattr(action, "name", None)
    if name:
        return str(name).strip().lower()
    text = str(action or "").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _is_security_priority_audit_request(kwargs: dict[str, Any]) -> bool:
    try:
        limit = int(kwargs.get("limit", 0) or 0)
    except Exception:
        limit = 0
    if limit < _SECURITY_PRIORITY_AUDIT_LIMIT:
        return False
    action_name = _audit_action_name(kwargs.get("action"))
    return action_name in _SECURITY_PRIORITY_AUDIT_ACTION_NAMES


async def _sleep_until_allowed(
    last_map: dict[int, float],
    key: int,
    gap_seconds: float,
) -> None:
    try:
        now = time.monotonic()
        last = float(last_map.get(int(key), 0.0) or 0.0)
        wait_for = (last + float(gap_seconds)) - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
    except Exception:
        pass


async def _guarded_audit_logs(
    self: discord.Guild,
    *args: Any,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Serialize audit requests and back off after 429s.

    ``_dank_priority`` remains an internal escape hatch for future security callers
    and is consumed here before the request reaches discord.py. Current AntiNuke
    requests are also recognized by their 50-entry high-risk-action request shape.
    Priority requests skip only our artificial generic spacing; they still share the
    guild lock and still honor the post-429 cooldown.
    """

    if _ORIGINAL_GUILD_AUDIT_LOGS is None:
        return

    explicit_priority = bool(kwargs.pop("_dank_priority", False))
    priority = explicit_priority or _is_security_priority_audit_request(kwargs)
    guild_id = int(getattr(self, "id", 0) or 0)
    lock = _AUDIT_LOCKS[guild_id]
    async with lock:
        now = time.monotonic()
        last_429 = float(_AUDIT_LAST_RATE_LIMIT.get(guild_id, 0.0) or 0.0)
        if last_429 > 0:
            wait_429 = (
                last_429 + _audit_after_429_cooldown_seconds()
            ) - now
            if wait_429 > 0:
                _warn(
                    "audit-log cooldown after 429 "
                    f"guild={guild_id} wait={wait_429:.1f}s"
                )
                await asyncio.sleep(wait_429)

        if not priority:
            await _sleep_until_allowed(
                _AUDIT_LAST_CALL,
                guild_id,
                _audit_cooldown_seconds(),
            )
        _AUDIT_LAST_CALL[guild_id] = time.monotonic()

        try:
            iterator = _ORIGINAL_GUILD_AUDIT_LOGS(self, *args, **kwargs)
            async for entry in iterator:
                yield entry
        except discord.HTTPException as e:
            status = int(getattr(e, "status", 0) or 0)
            if status == 429 or "429" in repr(e):
                _AUDIT_LAST_RATE_LIMIT[guild_id] = time.monotonic()
                _warn(
                    "audit-log 429 captured "
                    f"guild={guild_id}; callers will use fallback/cached data"
                )
                return
            raise


async def _retrying_send(
    original,
    self: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
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
            _warn(
                "send retry "
                f"channel={channel_id} attempt={attempt}/{attempts} "
                f"wait={wait_for:.1f}s error={type(e).__name__}"
            )
            await asyncio.sleep(wait_for)
    if last_error is not None:
        raise last_error
    return None


async def _retrying_channel_edit(
    original,
    self: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    channel_id = int(getattr(self, "id", 0) or 0)
    lock = _CHANNEL_EDIT_LOCKS[channel_id]
    async with lock:
        await _sleep_until_allowed(
            _CHANNEL_LAST_EDIT,
            channel_id,
            _channel_edit_gap_seconds(),
        )
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
                _warn(
                    "channel edit retry "
                    f"channel={channel_id} attempt={attempt}/{attempts} "
                    f"wait={wait_for:.1f}s error={type(e).__name__}"
                )
                await asyncio.sleep(wait_for)
        if last_error is not None:
            raise last_error
    return None


def _patch_send_method(cls: Any, attr_name: str = "send") -> None:
    original = getattr(cls, attr_name, None)
    if not callable(original) or getattr(
        original,
        "_discord_api_safety_wrapped",
        False,
    ):
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
    if not callable(original) or getattr(
        original,
        "_discord_api_safety_wrapped",
        False,
    ):
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
    if callable(original_audit) and not getattr(
        original_audit,
        "_discord_api_safety_wrapped",
        False,
    ):
        _ORIGINAL_GUILD_AUDIT_LOGS = original_audit
        try:
            setattr(
                _guarded_audit_logs,
                "_discord_api_safety_wrapped",
                True,
            )
        except Exception:
            pass
        discord.Guild.audit_logs = _guarded_audit_logs  # type: ignore[method-assign]

    for cls in (
        discord.TextChannel,
        discord.Thread,
        discord.DMChannel,
    ):
        try:
            _patch_send_method(cls)
        except Exception as e:
            _warn(f"failed patching {cls}.send: {e!r}")

    for cls in (
        discord.TextChannel,
        discord.VoiceChannel,
        discord.StageChannel,
        discord.CategoryChannel,
    ):
        try:
            _patch_edit_method(cls)
        except Exception as e:
            _warn(f"failed patching {cls}.edit: {e!r}")

    _PATCHED = True
    _log(
        "active; audit logs serialized, Discord sends retried, "
        "channel edits queued"
    )


install_discord_api_safety()

__all__ = ["install_discord_api_safety"]
