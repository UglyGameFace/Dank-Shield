from __future__ import annotations

"""Guild/channel aggregate message burst protection for SpamGuard."""

import time
from collections import defaultdict
from typing import Any

import discord

from . import abuse_burst_detector as detector
from . import spam_guard

_INSTALL_FLAG = "_dank_spam_guard_channel_flood_runtime_installed"
_COOLDOWN_SECONDS = 30.0
_BLOCK_UNTIL: dict[tuple[int, int], float] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


async def _settings(guild_id: int) -> dict[str, Any] | None:
    try:
        settings = dict(await spam_guard.get_spam_settings(int(guild_id)))
    except Exception:
        return None
    return settings if bool(settings.get("enabled")) else None


def _exempt(member: Any, settings: dict[str, Any]) -> bool:
    if member is None:
        return True
    uid = _safe_int(getattr(member, "id", 0), 0)
    if str(uid) in {str(value) for value in list(settings.get("exempt_user_ids") or [])}:
        return True
    try:
        if spam_guard._member_has_any_role(member, list(settings.get("exempt_role_ids") or [])):  # noqa: SLF001
            return True
    except Exception:
        pass
    try:
        return bool(spam_guard._is_staffish(member))  # noqa: SLF001
    except Exception:
        return False


def _fingerprint(message: Any) -> str:
    try:
        return spam_guard._normalize_message_content(str(getattr(message, "content", "") or ""))  # noqa: SLF001
    except Exception:
        return str(getattr(message, "content", "") or "").strip().lower()[:250]


def _cooling(guild_id: int, channel_id: int) -> bool:
    key = (int(guild_id), int(channel_id))
    now = time.monotonic()
    until = float(_BLOCK_UNTIL.get(key, 0.0) or 0.0)
    if until <= now:
        _BLOCK_UNTIL.pop(key, None)
        return False
    return True


def _arm(guild_id: int, channel_id: int) -> None:
    _BLOCK_UNTIL[(int(guild_id), int(channel_id))] = time.monotonic() + _COOLDOWN_SECONDS


async def _remove(message: Any) -> bool:
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
    author = getattr(message, "author", None)
    channel = getattr(message, "channel", None)
    if guild is None or author is None or channel is None:
        return
    if bool(getattr(author, "bot", False)) or _safe_int(getattr(message, "webhook_id", 0), 0) > 0:
        return

    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    channel_id = _safe_int(getattr(channel, "id", 0), 0)
    settings = await _settings(guild_id)
    if settings is None or _exempt(author, settings):
        return

    triggered, rows = detector.record_channel(message, fingerprint=_fingerprint(message))
    if not triggered:
        return
    if _cooling(guild_id, channel_id):
        await _remove(message)
        return
    _arm(guild_id, channel_id)

    by_actor: dict[int, list[tuple[float, int, str, Any, Any]]] = defaultdict(list)
    removed = 0
    for row in rows:
        by_actor[row[1]].append(row)
        if not _exempt(row[4], settings) and await _remove(row[3]):
            removed += 1

    actions: list[str] = []
    for actor_id, events in sorted(by_actor.items(), key=lambda item: len(item[1]), reverse=True)[:5]:
        member = events[-1][4]
        if len(events) < 2 or _exempt(member, settings):
            continue
        try:
            action, _case = await spam_guard._apply_mode_action(  # noqa: SLF001
                guild=guild,
                member=member,
                settings=settings,
                reason="SpamGuard: coordinated multi-account channel flood",
            )
            actions.append(f"{actor_id}:{action}")
        except Exception as exc:
            actions.append(f"{actor_id}:error-{type(exc).__name__}")

    print(
        "🚨 SpamGuard coordinated channel burst contained "
        f"guild={guild_id} channel={channel_id} messages={len(rows)} actors={len(by_actor)} "
        f"removed={removed} actions={','.join(actions) if actions else '-'}"
    )


def install_spam_guard_channel_flood_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False
    adder(_on_message, "on_message")
    setattr(bot, _INSTALL_FLAG, True)
    print("🛡️ SpamGuard coordinated channel flood protection active")
    return True


__all__ = ["install_spam_guard_channel_flood_runtime"]
