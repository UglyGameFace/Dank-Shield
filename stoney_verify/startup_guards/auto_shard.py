from __future__ import annotations

"""
Env-gated AutoShardedBot startup guard.

This replaces the old root-level runtime_auto_shard_guard.py.

Disabled by default. When DISCORD_AUTO_SHARD=true, code that constructs
commands.Bot is routed to commands.AutoShardedBot before stoney_verify.globals
creates the shared bot instance.
"""

import os
from typing import Any

_PATCHED = False
_ORIGINAL_BOT: Any = None


def _log(message: str) -> None:
    try:
        print(f"🧭 auto_shard_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ auto_shard_guard {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = str(os.getenv(name, "") or "").strip().lower()
        if not raw:
            return bool(default)
        return raw in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int_or_none(name: str) -> int | None:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            return None
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def install_auto_shard_guard() -> None:
    global _PATCHED, _ORIGINAL_BOT

    if _PATCHED:
        return

    enabled = _env_bool("DISCORD_AUTO_SHARD", False)
    if not enabled:
        _log("loaded; AutoShardedBot switch available but disabled (set DISCORD_AUTO_SHARD=true to enable)")
        _PATCHED = True
        return

    try:
        from discord.ext import commands
    except Exception as e:
        _warn(f"discord.ext.commands import failed; cannot enable AutoShardedBot: {e!r}")
        _PATCHED = True
        return

    try:
        original_bot = commands.Bot
        auto_sharded_bot = commands.AutoShardedBot
    except Exception as e:
        _warn(f"commands.Bot/AutoShardedBot unavailable; cannot enable AutoShardedBot: {e!r}")
        _PATCHED = True
        return

    if getattr(original_bot, "_auto_shard_guard_wrapped", False) or getattr(original_bot, "_runtime_auto_shard_guard_wrapped", False):
        _PATCHED = True
        return

    shard_count = _env_int_or_none("DISCORD_SHARD_COUNT")

    class RuntimeAutoShardedBot(auto_sharded_bot):  # type: ignore[misc, valid-type]
        _auto_shard_guard_wrapped = True
        _runtime_auto_shard_guard_wrapped = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if shard_count is not None and "shard_count" not in kwargs:
                kwargs["shard_count"] = shard_count
            super().__init__(*args, **kwargs)
            try:
                _log(
                    "AutoShardedBot enabled "
                    f"class={self.__class__.__name__} "
                    f"shard_count={getattr(self, 'shard_count', None)} "
                    f"configured_shard_count={shard_count or 'auto'}"
                )
            except Exception:
                pass

    _ORIGINAL_BOT = original_bot
    commands.Bot = RuntimeAutoShardedBot  # type: ignore[assignment]
    _PATCHED = True
    _log(
        "patched discord.ext.commands.Bot -> AutoShardedBot "
        f"configured_shard_count={shard_count or 'auto'}"
    )


install_auto_shard_guard()


__all__ = ["install_auto_shard_guard"]
