from __future__ import annotations

"""Reaction and voice churn burst protection using existing SpamGuard policy."""

import time
from typing import Any

import discord

from . import abuse_burst_detector as detector
from . import spam_guard

_INSTALL_FLAG = "_dank_spam_guard_interaction_flood_runtime_installed"
_COOLDOWN_SECONDS = 30.0
_BLOCK_UNTIL: dict[tuple[str, int, int], float] = {}


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


def _cooling(kind: str, guild_id: int, actor_id: int) -> bool:
    key = (str(kind), int(guild_id), int(actor_id))
    now = time.monotonic()
    until = float(_BLOCK_UNTIL.get(key, 0.0) or 0.0)
    if until <= now:
        _BLOCK_UNTIL.pop(key, None)
        return False
    return True


def _arm(kind: str, guild_id: int, actor_id: int) -> None:
    _BLOCK_UNTIL[(str(kind), int(guild_id), int(actor_id))] = time.monotonic() + _COOLDOWN_SECONDS


async def _apply(guild: Any, member: Any, settings: dict[str, Any], reason: str) -> str:
    try:
        action, _case = await spam_guard._apply_mode_action(  # noqa: SLF001
            guild=guild,
            member=member,
            settings=settings,
            reason=reason,
        )
        return str(action)
    except Exception as exc:
        return f"error-{type(exc).__name__}"


async def _remove_current_reaction(bot: discord.Client, payload: Any, member: Any) -> None:
    try:
        channel = bot.get_channel(_safe_int(getattr(payload, "channel_id", 0), 0))
    except Exception:
        channel = None
    fetcher = getattr(channel, "fetch_message", None) if channel is not None else None
    if not callable(fetcher):
        return
    try:
        message = await fetcher(_safe_int(getattr(payload, "message_id", 0), 0))
        await message.remove_reaction(getattr(payload, "emoji", None), member)
    except Exception:
        return


async def _on_raw_reaction_add(bot: discord.Client, payload: Any) -> None:
    guild_id = _safe_int(getattr(payload, "guild_id", 0), 0)
    actor_id = _safe_int(getattr(payload, "user_id", 0), 0)
    if guild_id <= 0 or actor_id <= 0:
        return
    try:
        if getattr(bot, "user", None) is not None and actor_id == int(bot.user.id):
            return
        guild = bot.get_guild(guild_id)
    except Exception:
        guild = None
    if guild is None:
        return
    member = getattr(payload, "member", None) or guild.get_member(actor_id)
    settings = await _settings(guild_id)
    if settings is None or _exempt(member, settings):
        return
    if not detector.record_reaction(guild_id, actor_id):
        return

    await _remove_current_reaction(bot, payload, member)
    if _cooling("reaction", guild_id, actor_id):
        return
    _arm("reaction", guild_id, actor_id)
    action = await _apply(guild, member, settings, "SpamGuard: rapid reaction burst")
    print(
        "🚨 SpamGuard reaction burst contained "
        f"guild={guild_id} user={actor_id} action={action} "
        f"threshold={detector.REACTION_THRESHOLD}/{int(detector.REACTION_WINDOW_SECONDS)}s"
    )


async def _on_voice_state_update(member: Any, before: Any, after: Any) -> None:
    guild = getattr(member, "guild", None)
    if guild is None or getattr(before, "channel", None) == getattr(after, "channel", None):
        return
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    actor_id = _safe_int(getattr(member, "id", 0), 0)
    settings = await _settings(guild_id)
    if settings is None or _exempt(member, settings):
        return
    if not detector.record_voice(guild_id, actor_id):
        return
    if _cooling("voice", guild_id, actor_id):
        return
    _arm("voice", guild_id, actor_id)
    action = await _apply(guild, member, settings, "SpamGuard: repeated voice channel churn")
    print(
        "🚨 SpamGuard voice churn contained "
        f"guild={guild_id} user={actor_id} action={action} "
        f"threshold={detector.VOICE_TRANSITION_THRESHOLD}/{int(detector.VOICE_WINDOW_SECONDS)}s"
    )


def install_spam_guard_interaction_flood_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False

    async def raw_reaction(payload: Any) -> None:
        await _on_raw_reaction_add(bot, payload)

    adder(raw_reaction, "on_raw_reaction_add")
    adder(_on_voice_state_update, "on_voice_state_update")
    setattr(bot, _INSTALL_FLAG, True)
    print("🛡️ SpamGuard reaction/voice flood protection active")
    return True


__all__ = ["install_spam_guard_interaction_flood_runtime"]
