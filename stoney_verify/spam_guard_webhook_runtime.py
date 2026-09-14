from __future__ import annotations

"""High-confidence webhook message burst suppression for SpamGuard.

Webhook lifecycle mutations remain owned by AntiNuke. This layer only recognizes
and suppresses an already-active webhook that begins producing an extreme burst.
"""

import time
from typing import Any

import discord

from . import abuse_burst_detector as detector
from . import spam_guard

_INSTALL_FLAG = "_dank_spam_guard_webhook_runtime_installed"
_COOLDOWN_SECONDS = 30.0
_BLOCK_UNTIL: dict[tuple[int, int], float] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


async def _enabled(guild_id: int) -> bool:
    try:
        settings = await spam_guard.get_spam_settings(int(guild_id))
    except Exception:
        return False
    return bool(settings.get("enabled"))


def _cooling(guild_id: int, webhook_id: int) -> bool:
    key = (int(guild_id), int(webhook_id))
    now = time.monotonic()
    until = float(_BLOCK_UNTIL.get(key, 0.0) or 0.0)
    if until <= now:
        _BLOCK_UNTIL.pop(key, None)
        return False
    return True


def _arm_cooldown(guild_id: int, webhook_id: int) -> None:
    _BLOCK_UNTIL[(int(guild_id), int(webhook_id))] = time.monotonic() + _COOLDOWN_SECONDS


async def _suppress(message: Any) -> bool:
    remover = getattr(message, "delete", None)
    if not callable(remover):
        return False
    try:
        await remover()
        return True
    except Exception:
        return False


async def _on_message(message: Any) -> None:
    guild = getattr(message, "guild", None)
    webhook_id = _safe_int(getattr(message, "webhook_id", 0), 0)
    if guild is None or webhook_id <= 0:
        return
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    if guild_id <= 0 or not await _enabled(guild_id):
        return

    triggered, messages = detector.record_webhook(message)
    if not triggered:
        return
    if _cooling(guild_id, webhook_id):
        await _suppress(message)
        return
    _arm_cooldown(guild_id, webhook_id)

    removed = 0
    for found in messages:
        if await _suppress(found):
            removed += 1
    print(
        "🚨 SpamGuard webhook burst suppressed "
        f"guild={guild_id} webhook={webhook_id} removed={removed} "
        f"threshold={detector.WEBHOOK_MESSAGE_THRESHOLD}/{int(detector.WEBHOOK_WINDOW_SECONDS)}s"
    )


def install_spam_guard_webhook_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False
    adder(_on_message, "on_message")
    setattr(bot, _INSTALL_FLAG, True)
    print("🛡️ SpamGuard webhook burst suppression active")
    return True


__all__ = ["install_spam_guard_webhook_runtime"]
