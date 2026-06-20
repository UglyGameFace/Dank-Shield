from __future__ import annotations

"""
Public startup-scope guard.

This replaces the old root-level runtime_public_startup_scope_patch.py.

It keeps public deployments using global commands only by default and clears
stale beta guild-scoped slash commands that can cause duplicates. It is still a
startup guard, but it now lives inside the application package instead of the
repo root.
"""

import asyncio
import builtins
import os
import sys
from typing import Any

import discord

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")

if not hasattr(builtins, "_stoney_public_startup_scope_state"):
    setattr(
        builtins,
        "_stoney_public_startup_scope_state",
        {
            "native_patched_module_ids": set(),
            "cleared_beta_guilds": set(),
            "logged_keys": set(),
            "hook_installed": False,
        },
    )

_STATE: dict[str, Any] = getattr(builtins, "_stoney_public_startup_scope_state")
_NATIVE_PATCHED_MODULE_IDS: set[int] = _STATE.setdefault("native_patched_module_ids", set())
_CLEARED_BETA_GUILDS: set[int] = _STATE.setdefault("cleared_beta_guilds", set())
_LOGGED_KEYS: set[str] = _STATE.setdefault("logged_keys", set())


def _log_once(key: str, message: str) -> None:
    try:
        clean = str(key or message).strip().lower()
        if clean in _LOGGED_KEYS:
            return
        _LOGGED_KEYS.add(clean)
        print(f"🌐 public_startup_scope {message}")
    except Exception:
        pass


def _log(message: str) -> None:
    try:
        print(f"🌐 public_startup_scope {message}")
    except Exception:
        pass


def _warn_once(key: str, message: str) -> None:
    try:
        clean = str(key or message).strip().lower()
        if clean in _LOGGED_KEYS:
            return
        _LOGGED_KEYS.add(clean)
        print(f"⚠️ public_startup_scope {message}")
    except Exception:
        pass


def _env_str(name: str, default: str = "") -> str:
    try:
        value = os.getenv(name)
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _public_scope_enabled() -> bool:
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("DANK_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_bool("DANK_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_bool("DANK_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _sync_beta_guild_commands_enabled() -> bool:
    return _env_bool("DANK_SYNC_BETA_GUILD_COMMANDS", False)


def _clear_beta_guild_commands_enabled() -> bool:
    return _env_bool("DANK_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT", True)


def _guild_object(guild_id: int) -> discord.Object:
    return discord.Object(id=int(guild_id))


async def _clear_stale_beta_guild_commands(module: Any, guild_id: int) -> None:
    if guild_id <= 0 or guild_id in _CLEARED_BETA_GUILDS:
        return
    if not _public_scope_enabled() or _sync_beta_guild_commands_enabled() or not _clear_beta_guild_commands_enabled():
        return

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    _CLEARED_BETA_GUILDS.add(guild_id)
    guild_obj = _guild_object(guild_id)

    try:
        bot.tree.clear_commands(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        _log(
            "cleared stale beta guild slash commands to prevent duplicate commands "
            f"guild={guild_id} remaining={len(synced)}"
        )
    except Exception as e:
        _warn_once(
            f"clear-beta-guild:{guild_id}",
            f"failed clearing stale beta guild slash commands guild={guild_id}: {e!r}",
        )


async def _sync_beta_guild_commands_if_requested(module: Any, guild_id: int) -> None:
    if guild_id <= 0:
        return

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    if not _sync_beta_guild_commands_enabled():
        await _clear_stale_beta_guild_commands(module, guild_id)
        _log(
            "beta guild slash sync skipped; using global commands only "
            f"guild={guild_id} set DANK_SYNC_BETA_GUILD_COMMANDS=true for dev-only instant guild sync"
        )
        return

    guild_obj = _guild_object(guild_id)
    try:
        bot.tree.copy_global_to(guild=guild_obj)
        _log(f"copied global commands to beta guild tree guild={guild_id}")
    except Exception as e:
        _warn_once(f"copy-beta-guild:{guild_id}", f"copy_global_to beta guild failed guild={guild_id}: {e!r}")

    try:
        synced_guild = await bot.tree.sync(guild=guild_obj)
        _log(f"beta guild slash sync complete guild={guild_id} commands={len(synced_guild)}")
    except Exception as e:
        _warn_once(f"sync-beta-guild:{guild_id}", f"beta guild slash sync failed guild={guild_id}: {e!r}")


def _native_app_scope_active(module: Any) -> bool:
    try:
        return bool(getattr(module, "_NATIVE_PUBLIC_STARTUP_SCOPE", False))
    except Exception:
        return False


def _patch_native_app(module: Any) -> None:
    module_id = id(module)
    if module_id in _NATIVE_PATCHED_MODULE_IDS:
        return

    async def _native_sync_beta_guild_commands_if_requested(guild_id_int: int) -> None:
        await _sync_beta_guild_commands_if_requested(module, int(guild_id_int or 0))

    try:
        setattr(_native_sync_beta_guild_commands_if_requested, "_public_startup_scope_patch", True)
    except Exception:
        pass

    setattr(module, "_sync_beta_guild_commands_if_requested", _native_sync_beta_guild_commands_if_requested)
    _NATIVE_PATCHED_MODULE_IDS.add(module_id)
    _log_once(
        f"native-patched:{module_id}",
        "native app public startup scope detected; beta guild duplicate-command guard active",
    )


def _patch_app(module: Any) -> None:
    if module is None:
        return
    if _native_app_scope_active(module):
        _patch_native_app(module)


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.app")
        if module is not None:
            _patch_app(module)
    except Exception as e:
        _warn_once("loaded-app-patch", f"loaded app patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.app" or name.endswith("stoney_verify.app"):
            target = sys.modules.get("stoney_verify.app") or sys.modules.get(name)
            if target is not None:
                _patch_app(target)
        else:
            _maybe_patch_loaded()
    except Exception as e:
        _warn_once(f"post-import:{name}", f"post-import patch failed for {name}: {e!r}")
    return module


try:
    if not bool(_STATE.get("hook_installed")):
        builtins.__import__ = _safe_import
        _STATE["hook_installed"] = True
        _log_once("fallback-guard-loaded", "loaded; fallback guard active")
    else:
        _log_once("fallback-guard-already-loaded", "fallback guard already active; skipped duplicate import hook")
except Exception as e:
    _warn_once("install-import-hook", f"failed installing import hook: {e!r}")

_maybe_patch_loaded()
