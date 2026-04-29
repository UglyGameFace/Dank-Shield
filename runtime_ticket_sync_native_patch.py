from __future__ import annotations

"""
Temporary native ticket sync/backfill wiring patch.

The clean sync/backfill helpers now live in:
    stoney_verify.tickets_new.sync_categories

This shim wires the existing sync_service module through those helpers while the
large sync_service.py file is refactored safely in smaller native steps.
"""

import asyncio
import builtins
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧩 runtime_ticket_sync_native {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_sync_native {message}")
    except Exception:
        pass


async def _warm_guild_config(guild: Any) -> None:
    try:
        guild_id = int(getattr(guild, "id", 0) or 0)
        if guild_id <= 0:
            return
        from stoney_verify.guild_config import get_guild_config

        await asyncio.wait_for(get_guild_config(guild_id, refresh=True), timeout=5.0)
    except Exception as e:
        _warn(f"config warm failed guild={getattr(guild, 'id', None)}: {e!r}")


def _patch_sync_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:native_sync_categories_v1"
    if key in _PATCHED:
        return

    try:
        from stoney_verify.tickets_new import sync_categories as native
    except Exception as e:
        _warn(f"cannot import native sync_categories: {e!r}")
        return

    patched_any = False

    original_resolve_archive = getattr(module, "_resolve_archive_category", None)
    if callable(original_resolve_archive) and not getattr(original_resolve_archive, "_native_sync_wrapped", False):
        def _resolve_archive_category_native(guild: discord.Guild) -> Any:
            configured = native.archive_category_from_cache(guild)
            if configured is not None:
                return configured
            try:
                return original_resolve_archive(guild)
            except Exception:
                return None

        try:
            setattr(_resolve_archive_category_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_archive_category", _resolve_archive_category_native)
        patched_any = True

    original_channel_in_archive = getattr(module, "_channel_in_archive_category", None)
    if callable(original_channel_in_archive) and not getattr(original_channel_in_archive, "_native_sync_wrapped", False):
        def _channel_in_archive_category_native(channel: discord.TextChannel) -> bool:
            try:
                if native.channel_is_in_configured_archive(channel):
                    return True
            except Exception:
                pass
            try:
                return bool(original_channel_in_archive(channel))
            except Exception:
                return False

        try:
            setattr(_channel_in_archive_category_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_channel_in_archive_category", _channel_in_archive_category_native)
        patched_any = True

    original_lifecycle_location = getattr(module, "_channel_lifecycle_location", None)
    if callable(original_lifecycle_location) and not getattr(original_lifecycle_location, "_native_sync_wrapped", False):
        def _channel_lifecycle_location_native(channel: discord.TextChannel) -> str:
            try:
                return native.channel_lifecycle_location(channel)
            except Exception:
                try:
                    return str(original_lifecycle_location(channel))
                except Exception:
                    return "uncategorized"

        try:
            setattr(_channel_lifecycle_location_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_channel_lifecycle_location", _channel_lifecycle_location_native)
        patched_any = True

    original_discover = getattr(module, "_discover_ticket_categories", None)
    if callable(original_discover) and not getattr(original_discover, "_native_sync_wrapped", False):
        def _discover_ticket_categories_native(guild: discord.Guild) -> list[discord.CategoryChannel]:
            legacy_categories: list[Any] = []
            try:
                legacy_categories = list(original_discover(guild) or [])
            except Exception as e:
                _warn(f"legacy category discovery failed guild={getattr(guild, 'id', None)}: {e!r}")
            try:
                return native.merge_unique_categories(guild, legacy_categories)
            except Exception as e:
                _warn(f"native category merge failed guild={getattr(guild, 'id', None)}: {e!r}")
                return [c for c in legacy_categories if isinstance(c, discord.CategoryChannel)]

        try:
            setattr(_discover_ticket_categories_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_discover_ticket_categories", _discover_ticket_categories_native)
        patched_any = True

    original_is_ticket_channel = getattr(module, "_is_ticket_channel", None)
    if callable(original_is_ticket_channel) and not getattr(original_is_ticket_channel, "_native_sync_wrapped", False):
        def _is_ticket_channel_native(channel: discord.TextChannel) -> bool:
            try:
                if native.is_transcript_channel(channel):
                    return False
                if native.channel_looks_like_ticket(channel):
                    return True
            except Exception:
                pass
            try:
                return bool(original_is_ticket_channel(channel))
            except Exception:
                return False

        try:
            setattr(_is_ticket_channel_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_is_ticket_channel", _is_ticket_channel_native)
        patched_any = True

    original_candidates = getattr(module, "_candidate_ticket_channels", None)
    if callable(original_candidates) and not getattr(original_candidates, "_native_sync_wrapped", False):
        def _candidate_ticket_channels_native(guild: discord.Guild) -> list[discord.TextChannel]:
            legacy_categories: list[Any] = []
            legacy_channels: list[Any] = []
            try:
                legacy_categories = list(getattr(module, "_discover_ticket_categories", original_discover)(guild) or [])
            except Exception:
                legacy_categories = []
            try:
                legacy_channels = list(original_candidates(guild) or [])
            except Exception as e:
                _warn(f"legacy candidate fallback failed guild={getattr(guild, 'id', None)}: {e!r}")
            try:
                return native.candidate_ticket_channels(
                    guild,
                    extra_categories=legacy_categories,
                    extra_channels=legacy_channels,
                )
            except Exception as e:
                _warn(f"native candidate discovery failed guild={getattr(guild, 'id', None)}: {e!r}")
                return [c for c in legacy_channels if isinstance(c, discord.TextChannel)]

        try:
            setattr(_candidate_ticket_channels_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "_candidate_ticket_channels", _candidate_ticket_channels_native)
        patched_any = True

    original_sync = getattr(module, "sync_active_ticket_channels_for_guild", None)
    if callable(original_sync) and not getattr(original_sync, "_native_sync_wrapped", False):
        async def _sync_active_ticket_channels_for_guild_native(guild: discord.Guild, *args: Any, **kwargs: Any) -> Any:
            await _warm_guild_config(guild)
            try:
                cfg = native.sync_category_config_from_cache(guild)
                _log(
                    "startup sync using native category discovery "
                    f"guild={cfg.guild_id} active={cfg.active_category_id} archive={cfg.archive_category_id} "
                    f"transcripts={cfg.transcripts_channel_id} source={cfg.source}"
                )
            except Exception:
                pass
            return await original_sync(guild, *args, **kwargs)

        try:
            setattr(_sync_active_ticket_channels_for_guild_native, "_native_sync_wrapped", True)
        except Exception:
            pass
        setattr(module, "sync_active_ticket_channels_for_guild", _sync_active_ticket_channels_for_guild_native)
        patched_any = True

    if patched_any:
        _PATCHED.add(key)
        _log(f"patched {module_name}; startup ticket sync/backfill now uses native sync category helpers")


def _patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.tickets_new.sync_service")
        if module is not None:
            _patch_sync_service(module)
    except Exception as e:
        _warn(f"loaded sync service patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.sync_service" or name.endswith("tickets_new.sync_service"):
            target = sys.modules.get("stoney_verify.tickets_new.sync_service") or sys.modules.get(name)
            if target is not None:
                _patch_sync_service(target)
        else:
            _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; native ticket sync/backfill wiring active")
