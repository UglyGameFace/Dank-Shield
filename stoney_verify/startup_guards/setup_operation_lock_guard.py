from __future__ import annotations

"""Serialize dangerous setup operations.

Production owners will click buttons twice. Mobile users will tap twice. Discord
will sometimes feel delayed. Setup operations that create roles/channels or write
config need a clear per-server lock so Dank Shield does not create confusing
followups or race setup state.

This guard focuses on the risky setup paths:
- Auto-Fix Missing Defaults / customized missing-name repair
- guided setup config saves
- guided setup category-menu writes

The operations stay idempotent, but duplicate clicks now get a clear message
instead of running a second copy at the same time.
"""

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

import discord

_SETUP_LOCKS: Dict[Tuple[int, str], asyncio.Lock] = {}
_RECENT: Dict[Tuple[int, int, str, str], float] = {}
_RECENT_SECONDS = 3.5


def _log(message: str) -> None:
    try:
        print(f"✅ setup_operation_lock_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_operation_lock_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _guild_id_from_interaction(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
    except Exception:
        return 0


def _user_id_from_interaction(interaction: discord.Interaction | None) -> int:
    try:
        return int(getattr(getattr(interaction, "user", None), "id", 0) or 0)
    except Exception:
        return 0


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))[:500]
    except Exception:
        return str(value)[:500]


def _prune_recent() -> None:
    try:
        now = time.monotonic()
        expired = [key for key, until in _RECENT.items() if until <= now]
        for key in expired[:200]:
            _RECENT.pop(key, None)
    except Exception:
        pass


def _lock_for(guild_id: int, action: str) -> asyncio.Lock:
    key = (int(guild_id), str(action))
    lock = _SETUP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SETUP_LOCKS[key] = lock
    return lock


async def _send_busy(interaction: discord.Interaction, action_label: str) -> None:
    content = (
        f"⏳ **{action_label}** is already running for this server. "
        "Wait a moment, then run the health check before pressing it again."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _send_duplicate(interaction: discord.Interaction, action_label: str) -> None:
    content = f"✅ That **{action_label}** click was already handled. Blocked the duplicate tap."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _run_locked(
    *,
    interaction: discord.Interaction,
    action: str,
    action_label: str,
    fingerprint: str,
    coro_factory: Callable[[], Awaitable[Any]],
) -> Any:
    _prune_recent()
    guild_id = _guild_id_from_interaction(interaction)
    user_id = _user_id_from_interaction(interaction)
    recent_key = (guild_id, user_id, str(action), str(fingerprint))
    now = time.monotonic()

    if _RECENT.get(recent_key, 0.0) > now:
        await _send_duplicate(interaction, action_label)
        return None

    lock = _lock_for(guild_id, action)
    if lock.locked():
        await _send_busy(interaction, action_label)
        return None

    async with lock:
        _RECENT[recent_key] = time.monotonic() + _RECENT_SECONDS
        return await coro_factory()


def _wrap_repair_specs(module: Any) -> bool:
    original = getattr(module, "_repair_specs", None)
    if not callable(original) or getattr(original, "_setup_operation_lock_wrapped", False):
        return False

    async def wrapped_repair_specs(interaction: discord.Interaction, specs: list[Any], *args: Any, **kwargs: Any):
        fp = _fingerprint([getattr(spec, "key", str(spec)) for spec in specs or []]) + _fingerprint(kwargs.get("custom_names") or {})
        return await _run_locked(
            interaction=interaction,
            action="setup_auto_repair",
            action_label="setup repair",
            fingerprint=fp,
            coro_factory=lambda: original(interaction, specs, *args, **kwargs),
        )

    setattr(wrapped_repair_specs, "_setup_operation_lock_wrapped", True)
    module._repair_specs = wrapped_repair_specs
    return True


def _wrap_save_config(module: Any, label: str) -> bool:
    original = getattr(module, "_save_config", None)
    if not callable(original) or getattr(original, "_setup_operation_lock_wrapped", False):
        return False

    async def wrapped_save_config(interaction: discord.Interaction, payload: dict[str, Any], *args: Any, **kwargs: Any):
        return await _run_locked(
            interaction=interaction,
            action=f"{label}_save_config",
            action_label="setup save",
            fingerprint=_fingerprint(payload),
            coro_factory=lambda: original(interaction, payload, *args, **kwargs),
        )

    setattr(wrapped_save_config, "_setup_operation_lock_wrapped", True)
    module._save_config = wrapped_save_config
    return True


def _wrap_seed_categories(module: Any) -> bool:
    original = getattr(module, "_seed_recommended_categories", None)
    if not callable(original) or getattr(original, "_setup_operation_lock_wrapped", False):
        return False

    async def wrapped_seed(guild: discord.Guild, *args: Any, **kwargs: Any):
        lock = _lock_for(int(guild.id), "setup_seed_categories")
        if lock.locked():
            return [], [], "Ticket menu setup is already running for this server. Wait a moment, then refresh."
        async with lock:
            return await original(guild, *args, **kwargs)

    setattr(wrapped_seed, "_setup_operation_lock_wrapped", True)
    module._seed_recommended_categories = wrapped_seed
    return True


def apply() -> bool:
    wrapped = 0

    try:
        from ..commands_ext import public_setup_assistant as assistant
        if _wrap_repair_specs(assistant):
            wrapped += 1
    except Exception as e:
        _warn(f"could not patch public_setup_assistant: {e!r}")

    try:
        from ..commands_ext import public_setup_start as start
        if _wrap_save_config(start, "start"):
            wrapped += 1
    except Exception as e:
        _warn(f"could not patch public_setup_start: {e!r}")

    try:
        from ..commands_ext import public_setup_solid as solid
        if _wrap_save_config(solid, "solid"):
            wrapped += 1
        if _wrap_seed_categories(solid):
            wrapped += 1
    except Exception as e:
        _warn(f"could not patch public_setup_solid: {e!r}")

    _log(f"installed setup operation locks wrapped={wrapped}")
    return wrapped > 0


apply()

__all__ = ["apply"]
