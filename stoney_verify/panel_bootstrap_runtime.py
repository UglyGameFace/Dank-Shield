from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, Optional

import discord

from .globals import bot, claim_startup_flag
from .tickets_new.panel_bootstrap import (
    bootstrap_panel_system_for_guild,
    panel_bootstrap_status,
    start_panel_bootstrap_once,
    start_panel_bootstrap_worker,
)


# ============================================================
# panel_bootstrap_runtime.py
# ------------------------------------------------------------
# Runtime wiring for the DB-backed panel bootstrap/self-heal
# system.
#
# Why this file exists:
# - app.py is already large and owns many startup concerns
# - this module can be imported once and safely registers listeners
# - avoids needing to rewrite the full app.py just to start the worker
#
# Safety:
# - no per-server .env IDs are required
# - .env is only used for generic deployment toggles
# - does not create Discord roles/channels
# - does not post panels automatically
# - does not enable destructive automation by default
# - guild join handling is isolated so one bad guild cannot break others
# ============================================================


_RUNTIME_REGISTERED = False
_RUNTIME_STARTED = False
_GUILD_JOIN_TASKS: set[asyncio.Task] = set()


# ============================================================
# Env helpers
# ============================================================

def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_false(name: str, default: bool = False) -> bool:
    return not _env_true(name, not default)


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return int(default)
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _debug(message: str) -> None:
    try:
        print(f"🧩 panel_bootstrap_runtime {message}")
    except Exception:
        pass


def _deployment_mode() -> str:
    raw = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_true("STONEY_PRODUCTION_MODE", False):
        return "production"
    if _env_true("STONEY_PUBLIC_MODE", False):
        return "public"
    return "development"


def _command_profile() -> str:
    return _env_str("STONEY_COMMAND_PROFILE", "public").lower()


def _public_like_mode() -> bool:
    return (
        _deployment_mode() in {"public", "prod", "production"}
        or _command_profile() in {"public", "minimal"}
    )


def _bootstrap_enabled() -> bool:
    return _env_true("STONEY_PANEL_BOOTSTRAP_ENABLED", True)


def _bootstrap_recurring_enabled() -> bool:
    return _env_true("STONEY_PANEL_BOOTSTRAP_RECURRING", True)


def _bootstrap_on_guild_join_enabled() -> bool:
    return _env_true("STONEY_PANEL_BOOTSTRAP_ON_GUILD_JOIN", True)


def _bootstrap_save_discovery_enabled() -> bool:
    return _env_true("STONEY_PANEL_BOOTSTRAP_SAVE_DISCOVERY", True)


def _bootstrap_seed_default_panel_enabled() -> bool:
    return _env_true("STONEY_PANEL_BOOTSTRAP_SEED_DEFAULT_PANEL", True)


def _bootstrap_interval_seconds() -> int:
    return max(300, _env_int("STONEY_PANEL_BOOTSTRAP_INTERVAL_SECONDS", 1800))


def _guild_join_delay_seconds() -> float:
    return float(max(0, _env_int("STONEY_PANEL_BOOTSTRAP_GUILD_JOIN_DELAY_SECONDS", 8)))


# ============================================================
# Task tracking
# ============================================================

def _track_task(task: asyncio.Task, *, label: str) -> None:
    try:
        def _done_callback(done: asyncio.Task) -> None:
            try:
                exc = done.exception()
                if exc is not None:
                    print(f"⚠️ Background task failed [{label}]: {repr(exc)}")
            except asyncio.CancelledError:
                _debug(f"background task cancelled [{label}]")
            except Exception:
                pass
            finally:
                try:
                    _GUILD_JOIN_TASKS.discard(done)
                except Exception:
                    pass

        task.add_done_callback(_done_callback)
    except Exception:
        pass


async def _wait_until_ready_safely(timeout_seconds: int = 90) -> None:
    try:
        wait_until_ready = getattr(bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            await asyncio.wait_for(wait_until_ready(), timeout=max(1, int(timeout_seconds)))
    except asyncio.TimeoutError:
        _debug("wait_until_ready timed out; continuing cautiously")
    except Exception as e:
        _debug(f"wait_until_ready skipped: {repr(e)}")


# ============================================================
# Startup worker
# ============================================================

async def _start_panel_bootstrap_after_ready() -> None:
    global _RUNTIME_STARTED

    if _RUNTIME_STARTED:
        return

    if not _bootstrap_enabled():
        _RUNTIME_STARTED = True
        _debug("startup disabled by STONEY_PANEL_BOOTSTRAP_ENABLED=false")
        return

    if not claim_startup_flag("panel_bootstrap_runtime"):
        _RUNTIME_STARTED = True
        _debug("startup already claimed elsewhere; skipping duplicate start")
        return

    _RUNTIME_STARTED = True

    await _wait_until_ready_safely()

    save_discovery = _bootstrap_save_discovery_enabled()
    seed_default_panel = _bootstrap_seed_default_panel_enabled()

    try:
        if _bootstrap_recurring_enabled():
            task = start_panel_bootstrap_worker(
                bot,
                interval_seconds=_bootstrap_interval_seconds(),
                save_discovery=save_discovery,
                seed_default_panel=seed_default_panel,
            )

            if task is not None:
                _debug(
                    "recurring worker requested "
                    f"public_like={_public_like_mode()} "
                    f"interval={_bootstrap_interval_seconds()} "
                    f"save_discovery={save_discovery} "
                    f"seed_default_panel={seed_default_panel}"
                )
            else:
                _debug("recurring worker was not started")
            return

        task = start_panel_bootstrap_once(
            bot,
            save_discovery=save_discovery,
            seed_default_panel=seed_default_panel,
        )

        if task is not None:
            _debug(
                "one-shot startup bootstrap requested "
                f"public_like={_public_like_mode()} "
                f"save_discovery={save_discovery} "
                f"seed_default_panel={seed_default_panel}"
            )
        else:
            _debug("one-shot startup bootstrap was not scheduled")

    except Exception as e:
        print("⚠️ panel_bootstrap_runtime startup failed:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


# ============================================================
# Guild join bootstrap
# ============================================================

async def _bootstrap_joined_guild(guild: discord.Guild) -> None:
    if not _bootstrap_enabled():
        return

    if not _bootstrap_on_guild_join_enabled():
        return

    try:
        delay = _guild_join_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        result = await bootstrap_panel_system_for_guild(
            guild,
            save_discovery=_bootstrap_save_discovery_enabled(),
            seed_default_panel=_bootstrap_seed_default_panel_enabled(),
        )

        ok = bool(result.get("ok"))
        _debug(
            "guild join bootstrap complete "
            f"guild={getattr(guild, 'id', 'unknown')} "
            f"ok={ok} "
            f"default_panel_created={result.get('default_panel_created')} "
            f"rules_repaired={result.get('rules_repaired')}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(
            "⚠️ panel_bootstrap_runtime guild join bootstrap failed "
            f"guild={getattr(guild, 'id', 'unknown')}: {repr(e)}"
        )


def _schedule_joined_guild_bootstrap(guild: discord.Guild) -> None:
    try:
        task = asyncio.create_task(
            _bootstrap_joined_guild(guild),
            name=f"panel_bootstrap_joined_guild_{getattr(guild, 'id', 'unknown')}",
        )
        _GUILD_JOIN_TASKS.add(task)
        _track_task(task, label=f"panel_bootstrap_joined_guild_{getattr(guild, 'id', 'unknown')}")
    except Exception as e:
        print(
            "⚠️ Failed scheduling joined guild panel bootstrap "
            f"guild={getattr(guild, 'id', 'unknown')}: {repr(e)}"
        )


# ============================================================
# Public API
# ============================================================

def panel_bootstrap_runtime_status() -> dict[str, Any]:
    status = panel_bootstrap_status()

    status.update(
        {
            "runtime_registered": _RUNTIME_REGISTERED,
            "runtime_started": _RUNTIME_STARTED,
            "bootstrap_enabled": _bootstrap_enabled(),
            "recurring_enabled": _bootstrap_recurring_enabled(),
            "on_guild_join_enabled": _bootstrap_on_guild_join_enabled(),
            "save_discovery": _bootstrap_save_discovery_enabled(),
            "seed_default_panel": _bootstrap_seed_default_panel_enabled(),
            "interval_seconds": _bootstrap_interval_seconds(),
            "public_like_mode": _public_like_mode(),
            "guild_join_tasks": len(_GUILD_JOIN_TASKS),
        }
    )

    return status


def register_panel_bootstrap_runtime() -> None:
    global _RUNTIME_REGISTERED

    if _RUNTIME_REGISTERED:
        _debug("runtime already registered; skipping duplicate registration")
        return

    _RUNTIME_REGISTERED = True

    @bot.listen("on_ready")
    async def _panel_bootstrap_runtime_on_ready() -> None:
        try:
            await _start_panel_bootstrap_after_ready()
        except Exception as e:
            print("⚠️ panel bootstrap runtime on_ready failed:", repr(e))
            try:
                traceback.print_exc()
            except Exception:
                pass

    @bot.listen("on_guild_join")
    async def _panel_bootstrap_runtime_on_guild_join(guild: discord.Guild) -> None:
        try:
            _schedule_joined_guild_bootstrap(guild)
        except Exception as e:
            print(
                "⚠️ panel bootstrap runtime on_guild_join failed "
                f"guild={getattr(guild, 'id', 'unknown')}: {repr(e)}"
            )

    _debug("runtime listeners registered")


register_panel_bootstrap_runtime()
