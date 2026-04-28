from __future__ import annotations

"""
Runtime public startup-scope guard.

Why this exists:
- app.py was originally written for one beta guild and uses GUILD_ID for slash
  command sync, departed-member reconciliation, and startup ticket sync.
- public/beta production needs global slash commands plus per-guild startup
  maintenance that only touches servers with a saved guild_configs row.

Safety rules:
- public/minimal/production profiles sync global commands so invited servers can
  actually see /stoney, /ticket, /tickets, etc.
- if GUILD_ID is still set for beta convenience, the guard may also sync that
  guild for immediate command updates.
- startup maintenance loops over cached guilds, but in public mode it only runs
  for guilds whose config source is supabase:guild_configs. This prevents env
  fallback IDs from being accidentally applied to another server.

Important implementation detail:
- app.py registers its on_ready listener with discord.py during import. Replacing
  module globals is not enough once the function object is already registered in
  bot.extra_events. This guard therefore also replaces the registered app.py
  on_ready listener with a public-scope-safe equivalent before bot.run().
"""

import asyncio
import builtins
import os
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False
_LISTENER_PATCHED = False
_SKIPPED_UNCONFIGURED_GUILDS: set[int] = set()


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


async def _resolve_env_guild(bot: Any) -> discord.Guild | None:
    guild_id = _env_int("GUILD_ID", 0)
    if guild_id <= 0:
        return None
    try:
        guild = bot.get_guild(guild_id)
        if guild is not None:
            return guild
    except Exception:
        pass
    try:
        await bot.fetch_guild(guild_id)
        return bot.get_guild(guild_id)
    except Exception as e:
        _warn(f"could not resolve env GUILD_ID={guild_id}: {e!r}")
        return None


async def _guild_config_source(guild_id: int, *, refresh: bool = False) -> str:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await asyncio.wait_for(get_guild_config(int(guild_id), refresh=refresh), timeout=4.0)
        return str(getattr(cfg, "source", "") or "")
    except Exception as e:
        _warn(f"config source check failed guild={guild_id}: {e!r}")
        return ""


async def _configured_startup_guilds(bot: Any) -> list[discord.Guild]:
    public_scope = _public_scope_enabled()
    max_guilds = max(1, _env_int("STONEY_STARTUP_MAX_GUILDS", 50))

    if public_scope:
        cached = _unique_guilds(list(getattr(bot, "guilds", []) or []))[:max_guilds]
        configured: list[discord.Guild] = []
        for guild in cached:
            gid = int(getattr(guild, "id", 0) or 0)
            source = await _guild_config_source(gid, refresh=False)
            if source.startswith("supabase:"):
                configured.append(guild)
            elif gid not in _SKIPPED_UNCONFIGURED_GUILDS:
                _SKIPPED_UNCONFIGURED_GUILDS.add(gid)
                _log(f"skipping startup maintenance for unconfigured guild={gid} source={source or 'unknown'}")
        return configured

    env_guild = await _resolve_env_guild(bot)
    if env_guild is not None:
        return [env_guild]

    return _unique_guilds(list(getattr(bot, "guilds", []) or []))[:max_guilds]


def _guild_object(guild_id: int) -> discord.Object:
    return discord.Object(id=int(guild_id))


async def _sync_beta_guild_commands_if_requested(module: Any, guild_id: int) -> None:
    if guild_id <= 0:
        return
    if not _env_bool("STONEY_SYNC_BETA_GUILD_COMMANDS", True):
        return

    bot = getattr(module, "bot", None)
    if bot is None:
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


def _patch_app(module: Any) -> None:
    global _PATCHED

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
            try:
                print("🧩 local global commands:", [c.name for c in bot.tree.get_commands()])
            except Exception:
                pass

            public_scope = _public_scope_enabled()
            guild_id = _env_int("GUILD_ID", 0)

            if public_scope:
                if _env_bool("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False) and not bool(getattr(module, "_DID_GLOBAL_COMMAND_CLEANUP", False)):
                    setattr(module, "_DID_GLOBAL_COMMAND_CLEANUP", True)
                    try:
                        bot.tree.clear_commands(guild=None)
                        await bot.tree.sync()
                        _log("cleared old global Discord application commands")
                    except Exception as e:
                        _warn(f"global command cleanup failed: {e!r}")

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

        guilds = await _resolve_runtime_guilds()
        if not guilds:
            print("⚠️ Skipping departed reconcile: no configured guilds resolved.")
            return

        for guild in guilds:
            try:
                print(f"🧹 Running departed-member reconciliation guild={guild.id}...")
                summary_departed = await helper(guild)
                print("✅ Departed reconciliation complete:", summary_departed)
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

        guilds = await _resolve_runtime_guilds()
        if not guilds:
            print("⚠️ Skipping startup ticket sync: no configured guilds resolved.")
            return

        for guild in guilds:
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

    _replace_app_on_ready_listener(module)

    if not _PATCHED:
        _PATCHED = True
        _log("patched stoney_verify.app startup scope: global commands + configured multi-guild maintenance")


def _make_public_on_ready(module: Any) -> Any:
    async def _public_on_ready() -> None:
        try:
            bot = getattr(module, "bot", None)
            print(f"🤖 Bot ready: {getattr(bot, 'user', None)}")

            await module._run_slash_maintenance_once()
            await module._maybe_resume_kick_timers_once()
            await module._start_legacy_actions_api_once()
            await module._start_new_api_once()
            await module._start_workers_once()
            await module._run_permission_self_check_once()

            module._ensure_startup_background_runner()
        except Exception as e:
            print("❌ public startup-scope on_ready listener failed:", repr(e))
            try:
                traceback = _ORIGINAL_IMPORT("traceback")
                traceback.print_exc()
            except Exception:
                pass

    try:
        _public_on_ready.__name__ = "on_ready"
        _public_on_ready.__qualname__ = "on_ready"
        _public_on_ready.__module__ = getattr(module, "__name__", "stoney_verify.app")
    except Exception:
        pass
    return _public_on_ready


def _replace_app_on_ready_listener(module: Any) -> None:
    global _LISTENER_PATCHED
    if _LISTENER_PATCHED:
        return

    bot = getattr(module, "bot", None)
    if bot is None:
        return

    try:
        public_listener = _make_public_on_ready(module)
        replaced = 0

        extra_events = getattr(bot, "extra_events", None)
        if isinstance(extra_events, dict):
            listeners = list(extra_events.get("on_ready") or [])
            new_listeners: list[Any] = []
            for listener in listeners:
                listener_module = str(getattr(listener, "__module__", "") or "")
                listener_name = str(getattr(listener, "__name__", "") or "")
                if listener_module == getattr(module, "__name__", "stoney_verify.app") and listener_name == "on_ready":
                    new_listeners.append(public_listener)
                    replaced += 1
                else:
                    new_listeners.append(listener)

            if replaced:
                extra_events["on_ready"] = new_listeners
            else:
                try:
                    bot.listen("on_ready")(public_listener)
                    replaced = 1
                except Exception as e:
                    _warn(f"could not append public on_ready listener: {e!r}")

        setattr(module, "on_ready", public_listener)
        _LISTENER_PATCHED = True
        _log(f"replaced registered app on_ready listener for public startup scope replaced={replaced}")
    except Exception as e:
        _warn(f"failed to replace app on_ready listener: {e!r}")


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
_log("loaded; public startup scope guard active")
