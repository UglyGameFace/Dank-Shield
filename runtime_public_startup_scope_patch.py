from __future__ import annotations

"""
Runtime public startup-scope guard.

This guard is intentionally still loaded from sitecustomize/main for stale hosts
and alternate entrypoints. The native implementation lives in stoney_verify.app,
but this patch still corrects one important production behavior:

- Public/global commands must not also be synced as guild commands by default.
  Discord will show duplicate slash commands when the same command exists both
  globally and as a guild-scoped command.
- For beta/dev speed, guild command sync can still be enabled explicitly with:
      STONEY_SYNC_BETA_GUILD_COMMANDS=true
- When guild sync is disabled, stale beta guild commands are cleared once by
  default so duplicate commands disappear after restart:
      STONEY_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT=true
"""

import asyncio
import builtins
import os
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULE_IDS: set[int] = set()
_NATIVE_PATCHED_MODULE_IDS: set[int] = set()
_LISTENER_PATCHED = False
_SKIPPED_UNCONFIGURED_GUILDS: set[int] = set()
_CLEARED_BETA_GUILDS: set[int] = set()


def _log(message: str) -> None:
    try:
        print(f"🌐 runtime_public_startup_scope {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_public_startup_scope {message}")
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


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = _env_str(name, "")
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _public_scope_enabled() -> bool:
    profile = _env_str("STONEY_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_bool("STONEY_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_bool("STONEY_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _sync_beta_guild_commands_enabled() -> bool:
    # Production-safe default: false. Guild-scoped copies cause duplicate slash
    # commands once global commands are also synced.
    return _env_bool("STONEY_SYNC_BETA_GUILD_COMMANDS", False)


def _clear_beta_guild_commands_enabled() -> bool:
    # Enabled by default because old beta guild commands are exactly what causes
    # duplicate slash commands after switching to the public/global command path.
    return _env_bool("STONEY_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT", True)


def _guild_object(guild_id: int) -> discord.Object:
    return discord.Object(id=int(guild_id))


def _unique_guilds(guilds: list[Any]) -> list[discord.Guild]:
    out: list[discord.Guild] = []
    seen: set[int] = set()
    for guild in guilds:
        try:
            gid = int(getattr(guild, "id", 0) or 0)
            if gid <= 0 or gid in seen:
                continue
            seen.add(gid)
            out.append(guild)
        except Exception:
            continue
    return sorted(out, key=lambda g: int(getattr(g, "id", 0) or 0))


async def _guild_config_source(guild_id: int, *, refresh: bool = False) -> str:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await asyncio.wait_for(get_guild_config(int(guild_id), refresh=refresh), timeout=4.0)
        return str(getattr(cfg, "source", "") or "")
    except Exception as e:
        _warn(f"config source check failed guild={guild_id}: {e!r}")
        return ""


async def _configured_startup_guilds(bot: Any) -> list[discord.Guild]:
    max_guilds = max(1, _env_int("STONEY_STARTUP_MAX_GUILDS", 50))

    if _public_scope_enabled():
        configured: list[discord.Guild] = []
        for guild in _unique_guilds(list(getattr(bot, "guilds", []) or []))[:max_guilds]:
            gid = int(getattr(guild, "id", 0) or 0)
            source = await _guild_config_source(gid, refresh=False)
            if source.startswith("supabase:"):
                configured.append(guild)
            elif gid not in _SKIPPED_UNCONFIGURED_GUILDS:
                _SKIPPED_UNCONFIGURED_GUILDS.add(gid)
                _log(f"skipping startup maintenance for unconfigured guild={gid} source={source or 'unknown'}")
        return configured

    guild_id = _env_int("GUILD_ID", 0)
    if guild_id > 0:
        try:
            guild = bot.get_guild(guild_id)
            if guild is not None:
                return [guild]
        except Exception:
            pass
    return _unique_guilds(list(getattr(bot, "guilds", []) or []))[:max_guilds]


async def _clear_stale_beta_guild_commands(module: Any, guild_id: int) -> None:
    if guild_id <= 0:
        return
    if guild_id in _CLEARED_BETA_GUILDS:
        return
    if not _public_scope_enabled():
        return
    if _sync_beta_guild_commands_enabled():
        return
    if not _clear_beta_guild_commands_enabled():
        return

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    guild_obj = _guild_object(guild_id)
    _CLEARED_BETA_GUILDS.add(guild_id)

    try:
        bot.tree.clear_commands(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        _log(
            "cleared stale beta guild slash commands to prevent duplicate commands "
            f"guild={guild_id} remaining={len(synced)}"
        )
    except Exception as e:
        _warn(f"failed clearing stale beta guild slash commands guild={guild_id}: {e!r}")


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
            f"guild={guild_id} set STONEY_SYNC_BETA_GUILD_COMMANDS=true for dev-only instant guild sync"
        )
        return

    guild_obj = _guild_object(guild_id)
    try:
        bot.tree.copy_global_to(guild=guild_obj)
        _log(f"copied global commands to beta guild tree guild={guild_id}")
    except Exception as e:
        _warn(f"copy_global_to beta guild failed guild={guild_id}: {e!r}")

    try:
        synced_guild = await bot.tree.sync(guild=guild_obj)
        _log(f"beta guild slash sync complete guild={guild_id} commands={len(synced_guild)}")
    except Exception as e:
        _warn(f"beta guild slash sync failed guild={guild_id}: {e!r}")


def _native_app_scope_active(module: Any) -> bool:
    try:
        return bool(getattr(module, "_NATIVE_PUBLIC_STARTUP_SCOPE", False))
    except Exception:
        return False


def _patch_native_app(module: Any) -> None:
    """Patch only the beta guild command sync behavior on modern app.py.

    Important: app.py can exist in sys.modules while it is still importing.
    Earlier versions of this guard marked the module as patched before app.py set
    _NATIVE_PUBLIC_STARTUP_SCOPE, which let the native default keep copying
    global commands into the beta guild. That causes duplicate slash commands.
    This function is therefore allowed to run later even if the module already
    received a legacy/partial patch during import.
    """
    module_id = id(module)
    if module_id in _NATIVE_PATCHED_MODULE_IDS:
        return

    async def _native_sync_beta_guild_commands_if_requested(guild_id_int: int) -> None:
        await _sync_beta_guild_commands_if_requested(module, int(guild_id_int or 0))

    try:
        setattr(_native_sync_beta_guild_commands_if_requested, "_runtime_public_startup_scope_patch", True)
    except Exception:
        pass

    setattr(module, "_sync_beta_guild_commands_if_requested", _native_sync_beta_guild_commands_if_requested)
    _NATIVE_PATCHED_MODULE_IDS.add(module_id)
    _log("native app public startup scope detected; beta guild duplicate-command guard active")


def _patch_legacy_app(module: Any) -> None:
    """Fallback for older app.py versions that do not include native public scope."""
    global _LISTENER_PATCHED

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    async def _resolve_runtime_guilds() -> list[discord.Guild]:
        guilds = await _configured_startup_guilds(bot)
        if not guilds:
            _warn("no configured runtime guilds found for startup maintenance")
        return guilds

    async def _resolve_runtime_guild() -> discord.Guild | None:
        guilds = await _resolve_runtime_guilds()
        return guilds[0] if guilds else None

    async def _run_slash_maintenance_once() -> None:
        if bool(getattr(module, "_DID_SLASH_MAINTENANCE", False)):
            return
        setattr(module, "_DID_SLASH_MAINTENANCE", True)

        try:
            public_scope = _public_scope_enabled()
            guild_id = _env_int("GUILD_ID", 0)

            if public_scope:
                synced_global = await bot.tree.sync()
                _log(f"global slash sync complete commands={len(synced_global)} mode=public")
                await _sync_beta_guild_commands_if_requested(module, guild_id)
                return

            if guild_id > 0:
                await _sync_beta_guild_commands_if_requested(module, guild_id)
            else:
                synced_global = await bot.tree.sync()
                _log(f"global slash sync complete commands={len(synced_global)} mode=single-instance")
        except Exception as e:
            print("❌ Slash maintenance failed:", repr(e))

    async def _maybe_run_departed_reconcile_once() -> None:
        if bool(getattr(module, "_DID_DEPARTED_RECONCILE", False)):
            return
        setattr(module, "_DID_DEPARTED_RECONCILE", True)
        helper = getattr(module, "_run_departed_reconciliation_for_guild", None)
        if helper is None:
            print("⚠️ Departed reconcile helper unavailable; skipping.")
            return
        for guild in await _resolve_runtime_guilds():
            try:
                print(f"🧹 Running departed-member reconciliation guild={guild.id}...")
                print("✅ Departed reconciliation complete:", await helper(guild))
            except Exception as e:
                print(f"❌ Departed reconcile failed guild={getattr(guild, 'id', 'unknown')}:", repr(e))

    async def _maybe_run_ticket_sync_once() -> None:
        if bool(getattr(module, "_DID_TICKET_SYNC", False)):
            return
        setattr(module, "_DID_TICKET_SYNC", True)
        sync_fn = getattr(module, "_sync_active_ticket_channels_for_guild", None)
        if sync_fn is None:
            print("⚠️ Ticket sync helper unavailable; skipping startup ticket sync.")
            return
        for guild in await _resolve_runtime_guilds():
            try:
                print(f"🎫 Running startup ticket sync/backfill guild={guild.id}...")
                summary = await sync_fn(
                    guild,
                    source="startup_ticket_sync",
                    include_closed_visible_channels=True,
                    dry_run=False,
                )
                print("✅ Startup ticket sync complete:", summary)
            except Exception as e:
                print(f"❌ Startup ticket sync failed guild={getattr(guild, 'id', 'unknown')}:", repr(e))

    setattr(module, "_resolve_runtime_guilds", _resolve_runtime_guilds)
    setattr(module, "_resolve_runtime_guild", _resolve_runtime_guild)
    setattr(module, "_run_slash_maintenance_once", _run_slash_maintenance_once)
    setattr(module, "_maybe_run_departed_reconcile_once", _maybe_run_departed_reconcile_once)
    setattr(module, "_maybe_run_ticket_sync_once", _maybe_run_ticket_sync_once)

    if _LISTENER_PATCHED:
        return

    async def _public_on_ready() -> None:
        try:
            _log(f"public startup maintenance ready bot={getattr(bot, 'user', None)}")
            await module._run_slash_maintenance_once()
            await module._maybe_resume_kick_timers_once()
            await module._start_legacy_actions_api_once()
            await module._start_new_api_once()
            await module._start_workers_once()
            module._ensure_startup_background_runner()
        except Exception as e:
            print("❌ public startup-scope on_ready listener failed:", repr(e))

    try:
        _public_on_ready.__name__ = "on_ready"
        _public_on_ready.__qualname__ = "on_ready"
        _public_on_ready.__module__ = getattr(module, "__name__", "stoney_verify.app")
    except Exception:
        pass

    try:
        extra_events = getattr(bot, "extra_events", None)
        replaced = 0
        if isinstance(extra_events, dict):
            listeners = list(extra_events.get("on_ready") or [])
            new_listeners: list[Any] = []
            for listener in listeners:
                listener_module = str(getattr(listener, "__module__", "") or "")
                listener_name = str(getattr(listener, "__name__", "") or "")
                if listener_module == getattr(module, "__name__", "stoney_verify.app") and listener_name == "on_ready":
                    new_listeners.append(_public_on_ready)
                    replaced += 1
                else:
                    new_listeners.append(listener)
            if replaced:
                extra_events["on_ready"] = new_listeners
            else:
                bot.listen("on_ready")(_public_on_ready)
                replaced = 1
        setattr(module, "on_ready", _public_on_ready)
        _LISTENER_PATCHED = True
        _log(f"replaced registered app on_ready listener for public startup scope replaced={replaced}")
    except Exception as e:
        _warn(f"failed to replace app on_ready listener: {e!r}")


def _patch_app(module: Any) -> None:
    module_id = id(module)

    if _native_app_scope_active(module):
        _patch_native_app(module)
        _PATCHED_MODULE_IDS.add(module_id)
        _log("loaded; public startup scope guard active")
        return

    if module_id in _PATCHED_MODULE_IDS:
        return

    _patch_legacy_app(module)
    _PATCHED_MODULE_IDS.add(module_id)
    _log("loaded; public startup scope guard active")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.app")
        if module is not None:
            _patch_app(module)
    except Exception as e:
        _warn(f"loaded app patch failed: {e!r}")


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
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded()
_log("loaded; fallback guard active")
