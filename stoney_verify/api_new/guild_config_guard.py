from __future__ import annotations

"""
Structured API per-guild config guard.

This replaces the old root-level runtime_api_guild_config_patch.py.

It is intentionally conservative for stability: keep the existing API server code
working, but make its ticket category helpers prefer per-guild guild_configs over
single-server env IDs. Long-term, this logic should be folded directly into
api_new/server.py when that file is cleaned up in a dedicated pass.
"""

import builtins
import sys
from typing import Any, Optional

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧭 api_guild_config_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ api_guild_config_guard {message}")
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


def _guild_id(guild: Any) -> int:
    return _safe_int(getattr(guild, "id", 0), 0)


def _cached_cfg(guild: Any) -> Any:
    gid = _guild_id(guild)
    if gid <= 0:
        return None
    try:
        from stoney_verify.guild_config import get_cached_guild_config

        return get_cached_guild_config(gid)
    except Exception:
        return None


def _resolve_category_by_id(guild: Any, category_id: int) -> Optional[Any]:
    category_id = _safe_int(category_id, 0)
    if guild is None or category_id <= 0:
        return None
    try:
        channel = guild.get_channel(category_id)
        if channel is not None and hasattr(channel, "channels") and hasattr(channel, "guild"):
            return channel
    except Exception:
        return None
    return None


def _cfg_category_id(cfg: Any, *names: str) -> int:
    for name in names:
        try:
            value = _safe_int(getattr(cfg, name, 0), 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _patch_api_server(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:api_guild_config_guard_v1"
    if key in _PATCHED:
        return

    original_active = getattr(module, "_resolve_active_ticket_category", None)
    if callable(original_active) and not getattr(original_active, "_api_guild_config_wrapped", False):
        def _resolve_active_ticket_category(guild: Any) -> Optional[Any]:
            cfg = _cached_cfg(guild)
            configured_id = _cfg_category_id(cfg, "ticket_category_id", "tickets_category_id", "support_category_id")
            if configured_id > 0:
                configured = _resolve_category_by_id(guild, configured_id)
                if configured is not None:
                    return configured
            return original_active(guild)

        try:
            setattr(_resolve_active_ticket_category, "_api_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_active_ticket_category", _resolve_active_ticket_category)

    original_archive = getattr(module, "_resolve_archive_category", None)
    if callable(original_archive) and not getattr(original_archive, "_api_guild_config_wrapped", False):
        def _resolve_archive_category(guild: Any) -> Optional[Any]:
            cfg = _cached_cfg(guild)
            configured_id = _cfg_category_id(
                cfg,
                "ticket_archive_category_id",
                "ticket_archived_category_id",
                "archive_ticket_category_id",
                "archived_ticket_category_id",
                "closed_ticket_category_id",
            )
            if configured_id > 0:
                configured = _resolve_category_by_id(guild, configured_id)
                if configured is not None:
                    return configured
            return original_archive(guild)

        try:
            setattr(_resolve_archive_category, "_api_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_archive_category", _resolve_archive_category)

    original_health = getattr(module, "health", None)
    if callable(original_health) and not getattr(original_health, "_api_guild_config_wrapped", False):
        async def _health(request: Any):
            try:
                from stoney_verify.guild_config import guild_config_cache_snapshot

                response = await original_health(request)
                _ = guild_config_cache_snapshot()
                return response
            except Exception:
                return await original_health(request)

        try:
            setattr(_health, "_api_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "health", _health)

    _PATCHED.add(key)
    _log(f"patched {module_name}; structured API category lifecycle now prefers guild_configs")


def _maybe_patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.api_new.server")
        if module is not None:
            _patch_api_server(module)
    except Exception as e:
        _warn(f"loaded-module patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.api_new.server" or name.endswith("api_new.server"):
            target = sys.modules.get("stoney_verify.api_new.server") or sys.modules.get(name)
            if target is not None:
                _patch_api_server(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded()
_log("loaded; structured API per-guild config guard active")


__all__ = []
