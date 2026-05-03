from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, Optional

import discord

from .globals import bot, claim_startup_flag
from .config_new.provisioning import (
    ensure_guild_config_row,
    ensure_guild_config_rows_for_bot,
)
from .tickets_new.panel_bootstrap import (
    bootstrap_panel_system_for_guild,
    panel_bootstrap_status,
    start_panel_bootstrap_once,
    start_panel_bootstrap_worker,
)


# ============================================================
# panel_bootstrap_runtime.py
# ------------------------------------------------------------
# Runtime wiring for DB-backed guild bootstrap/self-heal.
#
# Responsibilities:
# - ensure every guild gets a Supabase guild_configs row
# - start panel bootstrap worker after the bot is ready
# - run isolated bootstrap when the bot joins a new guild
#
# Safety:
# - no per-server .env IDs are required
# - .env is only used for generic deployment toggles
# - does not copy owner/global env IDs into new servers
# - does not create Discord roles/channels here
# - does not post panels automatically unless panel bootstrap is explicitly configured to seed
# - one bad guild cannot break other guilds or gateway startup
# ============================================================


_RUNTIME_REGISTERED = False
_RUNTIME_STARTED = False
_PROVISIONING_STARTED = False
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


def _guild_config_provisioning_enabled() -> bool:
    return _env_true("STONEY_GUILD_CONFIG_AUTO_PROVISION", True)


def _guild_config_provisioning_on_join_enabled() -> bool:
    return _env_true("STONEY_GUILD_CONFIG_PROVISION_ON_JOIN", True)


def _guild_config_provisioning_on_ready_enabled() -> bool:
    return _env_true("STONEY_GUILD_CONFIG_PROVISION_ON_READY", True)


def _guild_config_provisioning_concurrency() -> int:
    return max(1, min(_env_int("STONEY_GUILD_CONFIG_PROVISION_CONCURRENCY", 4), 20))


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
# Guild config provisioning
# ============================================================

async def _provision_existing_guilds_once() -> None:
    global _PROVISIONING_STARTED

    if _PROVISIONING_STARTED:
        return
    _PROVISIONING_STARTED = True

    if not _guild_config_provisioning_enabled():
        _debug("guild config provisioning disabled by STONEY_GUILD_CONFIG_AUTO_PROVISION=false")
        return

    if not _guild_config_provisioning_on_ready_enabled():
        _debug("startup guild config provisioning disabled")
        return

    try:
        summary = await ensure_guild_config_rows_for_bot(
            bot,
            source="startup_backfill",
            max_concurrency=_guild_config_provisioning_concurrency(),
        )
        _debug(
            "guild config startup provisioning complete "
            f"guilds={summary.get('guilds')} created={summary.get('created')} "
            f"existing={summary.get('existing')} failed={summary.get('failed')}"
        )
    except Exception as e:
        print("⚠️ panel_bootstrap_runtime guild config startup provisioning failed:", repr(e))


async def _provision_joined_guild(guild: discord.Guild) -> None:
    if not _guild_config_provisioning_enabled():
        return
    if not _guild_config_provisioning_on_join_enabled():
        return

    try:
        result = await ensure_guild_config_row(
            guild,
            source="bot_join",
            log_prefix="guild_config_provision_join",
        )
        _debug(
            "guild join config provisioning complete "
            f"guild={getattr(guild, 'id', 'unknown')} ok={result.get('ok')} "
            f"created={result.get('created')} source={result.get('source')}"
        )
    except Exception as e:
        print(
            "⚠️ panel_bootstrap_runtime guild join config provisioning failed "
            f"guild={getattr(guild, 'id', 'unknown')}: {repr(e)}"
        )


# ============================================================
# Startup worker
# ============================================================

async def _start_panel_bootstrap_after_ready() -> None:
    global _RUNTIME_STARTED

    if _RUNTIME_STARTED:
        return

    if not claim_startup_flag("panel_bootstrap_runtime"):
        _RUNTIME_STARTED = True
        _debug("startup already claimed elsewhere; skipping duplicate start")
        return

    _RUNTIME_STARTED = True

    await _wait_until_ready_safely()

    # Always provision guild_configs rows before panel bootstrap reads setup.
    await _provision_existing_guilds_once()

    if not _bootstrap_enabled():
        _debug("panel bootstrap disabled by STONEY_PANEL_BOOTSTRAP_ENABLED=false")
        return

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
    try:
        delay = _guild_join_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        # First create the guild_configs row. This does not copy owner IDs.
        await _provision_joined_guild(guild)

        if not _bootstrap_enabled():
            return

        if not _bootstrap_on_guild_join_enabled():
            return

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
            "guild_config_provisioning_started": _PROVISIONING_STARTED,
            "guild_config_auto_provision": _guild_config_provisioning_enabled(),
            "guild_config_provision_on_ready": _guild_config_provisioning_on_ready_enabled(),
            "guild_config_provision_on_join": _guild_config_provisioning_on_join_enabled(),
            "guild_config_provision_concurrency": _guild_config_provisioning_concurrency(),
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
