from __future__ import annotations

"""
Runtime shard/scale readiness guard.

Imported by main.py before stoney_verify.app.

This does not change the bot class yet. It adds shard/guild pressure logging and
warnings so the bot tells us when the current single-process/non-sharded shape is
getting dangerous.

Why this matters for 500-1000+ servers:
- Gateway load increases with guild count, member events, voice events, and slash sync.
- Startup jobs must be shard-aware before multi-process sharding.
- The codebase currently creates a regular commands.Bot, not AutoShardedBot.
- We need visibility and safe thresholds before flipping architecture.
"""

import asyncio
import builtins
import os
import sys
import time
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()
_LAST_LOG_MONOTONIC = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = str(os.getenv(name, "") or "").strip().lower()
        if not raw:
            return bool(default)
        return raw in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _log(message: str) -> None:
    try:
        print(f"🛰️ runtime_shard_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_shard_safety {message}")
    except Exception:
        pass


def _bot_shard_snapshot(bot: Any) -> dict[str, Any]:
    guilds = list(getattr(bot, "guilds", []) or [])
    shard_ids: set[int | str] = set()
    unavailable = 0
    approx_members = 0

    for guild in guilds:
        try:
            sid = getattr(guild, "shard_id", None)
            shard_ids.add(sid if sid is not None else "none")
        except Exception:
            shard_ids.add("unknown")

        try:
            if bool(getattr(guild, "unavailable", False)):
                unavailable += 1
        except Exception:
            pass

        try:
            approx_members += int(getattr(guild, "member_count", 0) or 0)
        except Exception:
            pass

    shard_count = getattr(bot, "shard_count", None)
    shard_id = getattr(bot, "shard_id", None)
    latency = getattr(bot, "latency", None)

    return {
        "guild_count": len(guilds),
        "unavailable_guilds": unavailable,
        "approx_members": approx_members,
        "bot_class": bot.__class__.__name__,
        "bot_shard_count": shard_count,
        "bot_shard_id": shard_id,
        "observed_shards": sorted([str(x) for x in shard_ids]),
        "latency_ms": int(float(latency or 0.0) * 1000) if latency is not None else 0,
        "auto_shard_enabled_env": _env_bool("DISCORD_AUTO_SHARD", False),
        "warn_guild_threshold": _env_int("STONEY_SHARD_WARN_GUILDS", 400),
        "critical_guild_threshold": _env_int("STONEY_SHARD_CRITICAL_GUILDS", 900),
    }


def shard_scale_snapshot() -> dict[str, Any]:
    try:
        globals_mod = sys.modules.get("stoney_verify.globals")
        bot = getattr(globals_mod, "bot", None)
        if bot is None:
            return {"ok": False, "reason": "bot_not_loaded"}
        return {"ok": True, **_bot_shard_snapshot(bot)}
    except Exception as e:
        return {"ok": False, "reason": repr(e)}


def _log_snapshot(bot: Any, *, reason: str, force: bool = False) -> None:
    global _LAST_LOG_MONOTONIC

    now = time.monotonic()
    if not force and (now - _LAST_LOG_MONOTONIC) < 300.0:
        return
    _LAST_LOG_MONOTONIC = now

    snap = _bot_shard_snapshot(bot)
    guild_count = int(snap.get("guild_count", 0) or 0)
    warn_at = int(snap.get("warn_guild_threshold", 400) or 400)
    critical_at = int(snap.get("critical_guild_threshold", 900) or 900)
    bot_class = str(snap.get("bot_class") or "")

    message = (
        f"scale snapshot reason={reason} class={bot_class} "
        f"guilds={guild_count} unavailable={snap.get('unavailable_guilds')} "
        f"members≈{snap.get('approx_members')} "
        f"shard_count={snap.get('bot_shard_count')} shard_id={snap.get('bot_shard_id')} "
        f"observed_shards={snap.get('observed_shards')} latency_ms={snap.get('latency_ms')}"
    )

    if guild_count >= critical_at:
        _warn(
            message
            + " status=critical; migrate to AutoShardedBot/multi-process shards before adding more servers"
        )
    elif guild_count >= warn_at:
        _warn(
            message
            + " status=warning; start AutoShardedBot testing and command consolidation now"
        )
    else:
        _log(message + " status=ok")

    if bot_class == "Bot" and guild_count >= warn_at:
        _warn(
            "regular commands.Bot is still active at high guild count. "
            "Next architecture step: env-gated AutoShardedBot switch."
        )


def _patch_globals(module: Any) -> None:
    module_name = str(getattr(module, "__name__", "") or "")
    if module_name in _PATCHED_MODULES:
        return

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    try:
        setattr(module, "shard_scale_snapshot", shard_scale_snapshot)
    except Exception:
        pass

    async def _periodic_shard_scale_logger() -> None:
        await asyncio.sleep(15.0)
        while True:
            try:
                _log_snapshot(bot, reason="periodic", force=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _warn(f"periodic shard scale logger failed: {e!r}")
            await asyncio.sleep(300.0)

    try:
        @bot.listen("on_ready")
        async def _runtime_shard_safety_on_ready() -> None:
            try:
                _log_snapshot(bot, reason="on_ready", force=True)
            except Exception as e:
                _warn(f"on_ready shard snapshot failed: {e!r}")

            try:
                task = getattr(bot, "_runtime_shard_safety_logger_task", None)
                if task is None or task.done():
                    task = asyncio.create_task(
                        _periodic_shard_scale_logger(),
                        name="runtime-shard-scale-logger",
                    )
                    setattr(bot, "_runtime_shard_safety_logger_task", task)
            except Exception as e:
                _warn(f"failed starting periodic shard logger: {e!r}")
    except Exception as e:
        _warn(f"failed registering shard on_ready listener: {e!r}")

    _PATCHED_MODULES.add(module_name)
    _log(f"patched {module_name}; shard/scale readiness logger active")


def _maybe_patch_loaded_modules() -> None:
    try:
        module = sys.modules.get("stoney_verify.globals")
        if module is not None:
            _patch_globals(module)
    except Exception as e:
        _warn(f"globals patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    try:
        if name == "stoney_verify.globals" or name.endswith(".globals"):
            target = sys.modules.get("stoney_verify.globals") or sys.modules.get(name)
            if target is not None:
                _patch_globals(target)
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")

    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; shard/scale readiness guard active")


__all__ = ["shard_scale_snapshot"]
