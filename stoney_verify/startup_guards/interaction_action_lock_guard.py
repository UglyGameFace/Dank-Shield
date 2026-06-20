from __future__ import annotations

"""Dank Shield Interaction Action Lock Guard.

Central duplicate button/select action protection.

Phase 1 safety:
- Default mode is observe.
- Observe mode logs duplicate in-flight clicks but does not block.
- Block mode can be enabled later with:
  DANK_SHIELD_INTERACTION_ACTION_LOCK_MODE=block

This guard does not replace existing specific duplicate blockers yet.
It provides central evidence first, then we migrate old blockers safely.
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

import discord

_PATCHED = False


@dataclass
class _ActionLock:
    key: str
    trace: str
    created_at: float
    owner_user_id: int
    custom_id: str


_ACTIVE_LOCKS: dict[str, _ActionLock] = {}
_DUPLICATE_COUNTS: dict[str, int] = {}
_RECENT_RELEASES: dict[str, float] = {}
_COOLDOWN_COUNTS: dict[str, int] = {}


def _mode() -> str:
    value = os.getenv("DANK_SHIELD_INTERACTION_ACTION_LOCK_MODE", "observe").strip().lower()
    if value in {"off", "disabled", "false", "0", "none"}:
        return "off"
    if value in {"block", "enforce", "on", "true", "1"}:
        return "block"
    return "observe"


def _enabled() -> bool:
    return _mode() != "off"


def _ttl_seconds() -> float:
    try:
        return max(3.0, float(os.getenv("DANK_SHIELD_INTERACTION_ACTION_LOCK_TTL_SECONDS", "45")))
    except Exception:
        return 45.0


def _cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("DANK_SHIELD_INTERACTION_ACTION_COOLDOWN_SECONDS", "2.5")))
    except Exception:
        return 2.5


def _trace_id(interaction: discord.Interaction | None = None) -> str:
    try:
        if interaction is not None:
            iid = int(getattr(interaction, "id", 0) or 0)
            if iid:
                return str(iid)[-8:]
    except Exception:
        pass
    return str(int(time.time() * 1000))[-8:]


def _interaction_data(interaction: discord.Interaction | None) -> Mapping[str, Any]:
    try:
        data = getattr(interaction, "data", None)
        if isinstance(data, Mapping):
            return data
    except Exception:
        pass
    return {}


def _custom_id(interaction: discord.Interaction | None, item: Any = None) -> str:
    from_item = getattr(item, "custom_id", None)
    if from_item:
        return str(from_item)

    data = _interaction_data(interaction)
    return str(data.get("custom_id") or "unknown_component")


def _component_type(interaction: discord.Interaction | None) -> str:
    data = _interaction_data(interaction)
    return str(data.get("component_type") or "")


def _message_id(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "message", None), "id", 0) or 0)
    except Exception:
        return 0


def _guild_id(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
    except Exception:
        return 0


def _channel_id(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "channel", None), "id", 0) or 0)
    except Exception:
        return 0


def _user_id(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "user", None), "id", 0) or 0)
    except Exception:
        return 0


def _response_done(interaction: discord.Interaction | None) -> bool:
    if interaction is None:
        return False
    try:
        return bool(interaction.response.is_done())
    except Exception:
        return False


def _safe(value: Any, limit: int = 180) -> str:
    try:
        text = str(value).replace("\n", "\\n").replace("\r", "\\r")
    except Exception:
        text = "<unprintable>"
    return text[:limit]


def _lock_key(interaction: discord.Interaction | None, item: Any = None) -> str:
    """Build a safe duplicate-action key.

    Default scope:
    same guild + same channel + same message + same user + same component

    This prevents double-clicking the same button without blocking other users
    or unrelated buttons.
    """

    return ":".join(
        [
            str(_guild_id(interaction)),
            str(_channel_id(interaction)),
            str(_message_id(interaction)),
            str(_user_id(interaction)),
            _custom_id(interaction, item),
        ]
    )


def _cleanup_expired() -> None:
    now = time.monotonic()
    ttl = _ttl_seconds()
    cooldown = _cooldown_seconds()

    expired = [key for key, lock in _ACTIVE_LOCKS.items() if now - lock.created_at > ttl]
    for key in expired:
        _ACTIVE_LOCKS.pop(key, None)
        _DUPLICATE_COUNTS.pop(key, None)

    old_recent = [key for key, released_at in _RECENT_RELEASES.items() if now - released_at > cooldown]
    for key in old_recent:
        _RECENT_RELEASES.pop(key, None)
        _COOLDOWN_COUNTS.pop(key, None)


def _log(event: str, interaction: discord.Interaction | None, item: Any = None, **details: Any) -> None:
    try:
        fields = {
            "event": event,
            "mode": _mode(),
            "trace": _trace_id(interaction),
            "guild": _guild_id(interaction),
            "channel": _channel_id(interaction),
            "message": _message_id(interaction),
            "user": _user_id(interaction),
            "custom_id": _safe(_custom_id(interaction, item)),
            "component_type": _safe(_component_type(interaction)),
            "response_done": _response_done(interaction),
        }
        fields.update(details)
        print("🧱 dank_interaction_lock " + " ".join(f"{k}={_safe(v)}" for k, v in fields.items()))
    except Exception:
        pass


def _try_acquire(interaction: discord.Interaction | None, item: Any = None) -> tuple[bool, str, str]:
    if interaction is None:
        return True, "", "acquired"

    _cleanup_expired()

    key = _lock_key(interaction, item)
    existing = _ACTIVE_LOCKS.get(key)

    if existing is not None:
        _DUPLICATE_COUNTS[key] = _DUPLICATE_COUNTS.get(key, 1) + 1
        _log(
            "duplicate_in_flight",
            interaction,
            item,
            owner_trace=existing.trace,
            duplicate_count=_DUPLICATE_COUNTS[key],
        )
        return False, key, "in_flight"

    released_at = _RECENT_RELEASES.get(key)
    cooldown = _cooldown_seconds()

    if released_at is not None and cooldown > 0:
        age = time.monotonic() - released_at
        if age <= cooldown:
            _COOLDOWN_COUNTS[key] = _COOLDOWN_COUNTS.get(key, 1) + 1
            _log(
                "duplicate_cooldown",
                interaction,
                item,
                duplicate_count=_COOLDOWN_COUNTS[key],
                cooldown_s=cooldown,
                age_ms=int(age * 1000),
            )
            return False, key, "cooldown"

    _ACTIVE_LOCKS[key] = _ActionLock(
        key=key,
        trace=_trace_id(interaction),
        created_at=time.monotonic(),
        owner_user_id=_user_id(interaction),
        custom_id=_custom_id(interaction, item),
    )
    _DUPLICATE_COUNTS[key] = 1
    _log("acquired", interaction, item)
    return True, key, "acquired"


def _release(interaction: discord.Interaction | None, key: str, item: Any = None) -> None:
    if not key:
        return

    lock = _ACTIVE_LOCKS.pop(key, None)
    count = _DUPLICATE_COUNTS.pop(key, 0)

    if lock is not None:
        _RECENT_RELEASES[key] = time.monotonic()
        _log("released", interaction, item, total_count=count, cooldown_s=_cooldown_seconds())


async def _send_already_running(interaction: discord.Interaction | None, reason: str = "in_flight") -> None:
    if interaction is None:
        return

    if reason == "cooldown":
        message = "⏳ This action was just used. Please wait a moment."
    else:
        message = "⏳ This action is already running. Please wait a moment."

    try:
        if _response_done(interaction):
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


def _extract_view_args(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> tuple[Any, discord.Interaction | None]:
    item = None
    interaction = None

    # discord.py View._scheduled_task commonly receives: self, item, interaction
    for value in args:
        if isinstance(value, discord.Interaction):
            interaction = value
        elif item is None and value is not None and not isinstance(value, discord.ui.View):
            if hasattr(value, "callback") or hasattr(value, "custom_id"):
                item = value

    for value in kwargs.values():
        if isinstance(value, discord.Interaction):
            interaction = value
        elif item is None and value is not None:
            if hasattr(value, "callback") or hasattr(value, "custom_id"):
                item = value

    return item, interaction


def _patch_view_scheduled_task() -> bool:
    view_cls = discord.ui.View

    if getattr(view_cls, "_dank_shield_action_lock_wrapped", False):
        return False

    original = getattr(view_cls, "_scheduled_task", None)
    if original is None:
        return False

    async def locked_scheduled_task(self: discord.ui.View, *args: Any, **kwargs: Any) -> Any:
        if not _enabled():
            return await original(self, *args, **kwargs)

        item, interaction = _extract_view_args(args, kwargs)

        acquired = True
        key = ""
        reason = "acquired"

        try:
            acquired, key, reason = _try_acquire(interaction, item)

            if not acquired and _mode() == "block":
                await _send_already_running(interaction, reason)
                return None

            # Observe mode intentionally calls original even for duplicates.
            return await original(self, *args, **kwargs)
        except Exception as exc:
            _log("guard_exception_fail_open", interaction, item, error=f"{type(exc).__name__}: {_safe(exc, 220)}")
            raise
        finally:
            if acquired:
                _release(interaction, key, item)

    setattr(view_cls, "_dank_shield_action_lock_original_scheduled_task", original)
    setattr(view_cls, "_dank_shield_action_lock_wrapped", True)
    setattr(view_cls, "_scheduled_task", locked_scheduled_task)
    return True


def apply() -> bool:
    global _PATCHED

    if _PATCHED:
        return True

    try:
        patched = {
            "view_scheduled_task": _patch_view_scheduled_task(),
        }
        _PATCHED = True
        print(f"✅ Dank Shield Interaction Action Lock Guard active; mode={_mode()} patched={patched}")
        return True
    except Exception as exc:
        print(f"⚠️ Dank Shield Interaction Action Lock Guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
