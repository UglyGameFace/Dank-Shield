from __future__ import annotations

"""
Per-guild ticket configuration guard.

This replaces the old root-level runtime_guild_config_ticket_patch.py.

Purpose:
- keep public/beta deployments from relying on one env-only TICKET_CATEGORY_ID / STAFF_ROLE_ID
- make ticket creation prefer guild_configs for category/staff/transcript settings
- make close/reopen lifecycle movement prefer guild_configs for active/archive categories
- make startup ticket sync/backfill prefer guild_configs for active/archive categories
- keep old env/default behavior as a safe fallback

This is still a conservative compatibility guard while the older ticket modules are
being cleaned up, but it now lives inside tickets_new where ticket configuration
behavior belongs.
"""

import asyncio
import builtins
import inspect
import re
import sys
import time
from typing import Any, Optional

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_MODULES: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧭 guild_config_ticket_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_config_ticket_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _guild_from_any(*values: Any) -> Any:
    for value in values:
        try:
            if value is None:
                continue
            if hasattr(value, "id") and hasattr(value, "get_channel") and hasattr(value, "roles"):
                return value
            guild = getattr(value, "guild", None)
            if guild is not None and hasattr(guild, "id"):
                return guild
            if hasattr(value, "channel"):
                channel = getattr(value, "channel", None)
                guild = getattr(channel, "guild", None)
                if guild is not None and hasattr(guild, "id"):
                    return guild
        except Exception:
            continue
    return None


def _guild_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    direct = _guild_from_any(
        kwargs.get("guild"),
        kwargs.get("channel"),
        kwargs.get("owner"),
        kwargs.get("member"),
        kwargs.get("interaction"),
    )
    if direct is not None:
        return direct
    return _guild_from_any(*args)


def _cached_config_for_guild_id(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_cached_guild_config

        return get_cached_guild_config(int(guild_id))
    except Exception:
        return None


async def _config_for_guild(guild: Any, *, timeout: float = 3.0) -> Any:
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    if guild_id <= 0:
        return None

    try:
        from stoney_verify.guild_config import get_guild_config

        return await asyncio.wait_for(get_guild_config(guild_id), timeout=timeout)
    except Exception as e:
        _warn(f"guild config fetch failed guild={guild_id}; using cached/env fallback: {e!r}")
        return _cached_config_for_guild_id(guild_id)


def _cfg_int(cfg: Any, name: str, default: int = 0) -> int:
    return _safe_int(getattr(cfg, name, default), default)


def _cfg_first_int(cfg: Any, *names: str, default: int = 0) -> int:
    for name in names:
        value = _cfg_int(cfg, name, 0)
        if value > 0:
            return value
    return int(default)


def _cfg_str(cfg: Any, name: str, default: str = "") -> str:
    return _safe_str(getattr(cfg, name, default), default)


def _resolve_category_by_id(guild: Any, category_id: int) -> Optional[Any]:
    category_id = _safe_int(category_id, 0)
    if category_id <= 0 or guild is None:
        return None
    try:
        channel = guild.get_channel(category_id)
        if channel is not None and hasattr(channel, "channels") and hasattr(channel, "guild"):
            return channel
    except Exception:
        return None
    return None


def _configured_active_category(guild: Any) -> Optional[Any]:
    cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
    cfg_category_id = _cfg_int(cfg, "ticket_category_id", 0)
    if cfg_category_id > 0:
        return _resolve_category_by_id(guild, cfg_category_id)
    return None


def _configured_archive_category(guild: Any) -> Optional[Any]:
    cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
    cfg_archive_id = _cfg_first_int(
        cfg,
        "ticket_archive_category_id",
        "ticket_archived_category_id",
        "archived_ticket_category_id",
        "archive_ticket_category_id",
        "closed_ticket_category_id",
        "closed_tickets_category_id",
        default=0,
    )
    if cfg_archive_id > 0:
        return _resolve_category_by_id(guild, cfg_archive_id)
    return None


def _configured_transcript_channel_id(guild: Any) -> int:
    cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
    return _cfg_int(cfg, "transcripts_channel_id", 0)


def _configured_ticket_prefix(guild: Any) -> str:
    cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
    return (_cfg_str(cfg, "ticket_prefix", "ticket") or "ticket").strip().lower()


def _signature_accepts(original: Any, name: str) -> bool:
    try:
        sig = inspect.signature(original)
        if name in sig.parameters:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except Exception:
        return False


def _maybe_set_kwarg(original: Any, kwargs: dict[str, Any], name: str, value: Any) -> None:
    if value is None:
        return
    if name in kwargs and kwargs.get(name):
        return
    if _signature_accepts(original, name):
        kwargs[name] = value


def _patch_ticket_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:guild_config_ticket_guard_v1"
    if patch_key in _PATCHED_MODULES:
        return

    original_resolve_parent = getattr(module, "_resolve_ticket_parent_category", None)
    if callable(original_resolve_parent) and not getattr(original_resolve_parent, "_guild_config_wrapped", False):
        def _resolve_ticket_parent_category(guild: Any, explicit_parent_category_id: Any = None) -> Any:
            explicit_id = _safe_int(explicit_parent_category_id, 0)
            if explicit_id > 0:
                explicit = _resolve_category_by_id(guild, explicit_id)
                if explicit is not None:
                    return explicit

            configured = _configured_active_category(guild)
            if configured is not None:
                return configured

            return original_resolve_parent(guild, explicit_parent_category_id)

        try:
            setattr(_resolve_ticket_parent_category, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_ticket_parent_category", _resolve_ticket_parent_category)

    original_resolve_active = getattr(module, "_resolve_active_ticket_category", None)
    if callable(original_resolve_active) and not getattr(original_resolve_active, "_guild_config_wrapped", False):
        def _resolve_active_ticket_category(guild: Any) -> Any:
            configured = _configured_active_category(guild)
            if configured is not None:
                return configured
            return original_resolve_active(guild)

        try:
            setattr(_resolve_active_ticket_category, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_active_ticket_category", _resolve_active_ticket_category)

    original_resolve_archive = getattr(module, "_resolve_archive_category", None)
    if callable(original_resolve_archive) and not getattr(original_resolve_archive, "_guild_config_wrapped", False):
        def _resolve_archive_category(guild: Any) -> Any:
            configured = _configured_archive_category(guild)
            if configured is not None:
                return configured
            return original_resolve_archive(guild)

        try:
            setattr(_resolve_archive_category, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_archive_category", _resolve_archive_category)

    original_staff_check = getattr(module, "_actor_is_elevated_staff", None)
    if callable(original_staff_check) and not getattr(original_staff_check, "_guild_config_wrapped", False):
        def _actor_is_elevated_staff(actor: Any) -> bool:
            try:
                if original_staff_check(actor):
                    return True
            except Exception:
                pass

            try:
                guild = getattr(actor, "guild", None)
                cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
                staff_role_id = _cfg_int(cfg, "staff_role_id", 0)
                if staff_role_id > 0:
                    return any(_safe_int(getattr(role, "id", 0), 0) == staff_role_id for role in getattr(actor, "roles", []) or [])
            except Exception:
                return False
            return False

        try:
            setattr(_actor_is_elevated_staff, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_actor_is_elevated_staff", _actor_is_elevated_staff)

    original_create = getattr(module, "create_ticket_channel", None)
    if callable(original_create) and not getattr(original_create, "_guild_config_wrapped", False):
        async def _create_ticket_channel_with_guild_config(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            guild = _guild_from_call(args, kwargs)
            guild_id = _safe_int(getattr(guild, "id", 0), 0)
            cfg = await _config_for_guild(guild) if guild_id > 0 else None
            injected_keys: set[str] = set()

            if cfg is not None:
                category_id = _cfg_int(cfg, "ticket_category_id", 0)
                archive_category_id = _cfg_int(cfg, "ticket_archive_category_id", 0)
                staff_role_id = _cfg_int(cfg, "staff_role_id", 0)
                transcripts_channel_id = _cfg_int(cfg, "transcripts_channel_id", 0)
                ticket_prefix = _cfg_str(cfg, "ticket_prefix", "ticket") or "ticket"

                for name, value in (
                    ("parent_category_id", category_id),
                    ("explicit_parent_category_id", category_id),
                    ("ticket_category_id", category_id),
                    ("ticket_archive_category_id", archive_category_id),
                    ("archive_category_id", archive_category_id),
                    ("closed_ticket_category_id", archive_category_id),
                    ("staff_role_id", staff_role_id),
                    ("transcripts_channel_id", transcripts_channel_id),
                    ("transcript_channel_id", transcripts_channel_id),
                    ("ticket_prefix", ticket_prefix),
                ):
                    before = dict(kwargs)
                    _maybe_set_kwarg(original_create, kwargs, name, value)
                    if kwargs != before:
                        injected_keys.add(name)

                before = dict(kwargs)
                if staff_role_id > 0:
                    _maybe_set_kwarg(original_create, kwargs, "staff_role_ids", [staff_role_id])
                if kwargs != before:
                    injected_keys.add("staff_role_ids")

            try:
                result = await original_create(*args, **kwargs)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if guild_id > 0 and cfg is not None:
                    _log(
                        "ticket create used per-guild config "
                        f"guild={guild_id} source={getattr(cfg, 'source', 'unknown')} "
                        f"category={_cfg_int(cfg, 'ticket_category_id', 0)} "
                        f"archive={_cfg_int(cfg, 'ticket_archive_category_id', 0)} "
                        f"staff_role={_cfg_int(cfg, 'staff_role_id', 0)} "
                        f"elapsed_ms={elapsed_ms}"
                    )
                return result
            except TypeError as e:
                if "unexpected keyword argument" not in repr(e).lower():
                    raise
                _warn(f"ticket create kwarg compatibility retry guild={guild_id}: {e!r}")
                retry_kwargs = {k: v for k, v in kwargs.items() if k not in injected_keys}
                return await original_create(*args, **retry_kwargs)

        try:
            setattr(_create_ticket_channel_with_guild_config, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "create_ticket_channel", _create_ticket_channel_with_guild_config)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}; ticket creation + close/reopen categories now prefer guild_configs")


def _patch_ticket_sync_service(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:guild_config_ticket_sync_guard_v1"
    if patch_key in _PATCHED_MODULES:
        return

    original_resolve_archive = getattr(module, "_resolve_archive_category", None)
    if callable(original_resolve_archive) and not getattr(original_resolve_archive, "_guild_config_wrapped", False):
        def _resolve_archive_category(guild: Any) -> Any:
            configured = _configured_archive_category(guild)
            if configured is not None:
                return configured
            return original_resolve_archive(guild)

        try:
            setattr(_resolve_archive_category, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_resolve_archive_category", _resolve_archive_category)

    original_discover = getattr(module, "_discover_ticket_categories", None)
    if callable(original_discover) and not getattr(original_discover, "_guild_config_wrapped", False):
        def _discover_ticket_categories(guild: Any) -> list[Any]:
            categories: list[Any] = []
            seen: set[int] = set()

            for configured in (_configured_active_category(guild), _configured_archive_category(guild)):
                try:
                    if configured is None:
                        continue
                    cid = int(getattr(configured, "id", 0) or 0)
                    if cid > 0 and cid not in seen:
                        categories.append(configured)
                        seen.add(cid)
                except Exception:
                    continue

            try:
                for category in list(original_discover(guild) or []):
                    cid = int(getattr(category, "id", 0) or 0)
                    if cid > 0 and cid not in seen:
                        categories.append(category)
                        seen.add(cid)
            except Exception as e:
                _warn(f"ticket sync category fallback discovery failed guild={getattr(guild, 'id', None)}: {e!r}")

            return categories

        try:
            setattr(_discover_ticket_categories, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_discover_ticket_categories", _discover_ticket_categories)

    original_is_ticket_channel = getattr(module, "_is_ticket_channel", None)
    if callable(original_is_ticket_channel) and not getattr(original_is_ticket_channel, "_guild_config_wrapped", False):
        def _is_ticket_channel(channel: Any) -> bool:
            try:
                transcript_id = _configured_transcript_channel_id(getattr(channel, "guild", None))
                if transcript_id > 0 and int(getattr(channel, "id", 0) or 0) == transcript_id:
                    return False
            except Exception:
                pass

            try:
                if original_is_ticket_channel(channel):
                    return True
            except Exception:
                pass

            try:
                guild = getattr(channel, "guild", None)
                prefix = re.escape(_configured_ticket_prefix(guild) or "ticket")
                name = str(getattr(channel, "name", "") or "").strip().lower()
                topic = str(getattr(channel, "topic", "") or "").strip().lower()
                if re.match(rf"^({prefix}|closed)-(\d+)$", name, re.I):
                    return True
                if "owner_id=" in topic and ("ticket_number=" in topic or "category=" in topic):
                    return True
                if "requester_id=" in topic and "ticket_number=" in topic:
                    return True
            except Exception:
                pass

            return False

        try:
            setattr(_is_ticket_channel, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_is_ticket_channel", _is_ticket_channel)

    original_candidates = getattr(module, "_candidate_ticket_channels", None)
    if callable(original_candidates) and not getattr(original_candidates, "_guild_config_wrapped", False):
        def _candidate_ticket_channels(guild: Any) -> list[Any]:
            out: list[Any] = []
            seen: set[int] = set()
            transcript_id = _configured_transcript_channel_id(guild)

            try:
                categories = getattr(module, "_discover_ticket_categories", original_discover)(guild)
                for category in list(categories or []):
                    for channel in list(getattr(category, "text_channels", []) or []):
                        cid = int(getattr(channel, "id", 0) or 0)
                        if cid <= 0 or cid in seen:
                            continue
                        if transcript_id > 0 and cid == transcript_id:
                            continue
                        seen.add(cid)
                        out.append(channel)
            except Exception as e:
                _warn(f"ticket sync configured category channel scan failed guild={getattr(guild, 'id', None)}: {e!r}")

            try:
                for channel in list(original_candidates(guild) or []):
                    cid = int(getattr(channel, "id", 0) or 0)
                    if cid <= 0 or cid in seen:
                        continue
                    if transcript_id > 0 and cid == transcript_id:
                        continue
                    seen.add(cid)
                    out.append(channel)
            except Exception as e:
                _warn(f"ticket sync legacy candidate fallback failed guild={getattr(guild, 'id', None)}: {e!r}")

            return out

        try:
            setattr(_candidate_ticket_channels, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "_candidate_ticket_channels", _candidate_ticket_channels)

    original_sync = getattr(module, "sync_active_ticket_channels_for_guild", None)
    if callable(original_sync) and not getattr(original_sync, "_guild_config_wrapped", False):
        async def _sync_active_ticket_channels_for_guild_with_config(guild: Any, *args: Any, **kwargs: Any) -> Any:
            cfg = await _config_for_guild(guild) if _safe_int(getattr(guild, "id", 0), 0) > 0 else None
            if cfg is not None:
                _log(
                    "ticket sync using per-guild config "
                    f"guild={getattr(guild, 'id', None)} source={getattr(cfg, 'source', 'unknown')} "
                    f"active_category={_cfg_int(cfg, 'ticket_category_id', 0)} "
                    f"archive_category={_cfg_int(cfg, 'ticket_archive_category_id', 0)} "
                    f"transcripts={_cfg_int(cfg, 'transcripts_channel_id', 0)}"
                )
            return await original_sync(guild, *args, **kwargs)

        try:
            setattr(_sync_active_ticket_channels_for_guild_with_config, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "sync_active_ticket_channels_for_guild", _sync_active_ticket_channels_for_guild_with_config)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}; startup ticket sync now prefers guild_configs")


def _maybe_patch_loaded_modules() -> None:
    try:
        module = sys.modules.get("stoney_verify.tickets_new.service")
        if module is not None:
            _patch_ticket_service(module)
    except Exception as e:
        _warn(f"loaded ticket service patch failed: {e!r}")

    try:
        module = sys.modules.get("stoney_verify.tickets_new.sync_service")
        if module is not None:
            _patch_ticket_sync_service(module)
    except Exception as e:
        _warn(f"loaded ticket sync patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_ticket_service(target)

        if name == "stoney_verify.tickets_new.sync_service" or name.endswith("tickets_new.sync_service"):
            target = sys.modules.get("stoney_verify.tickets_new.sync_service") or sys.modules.get(name)
            if target is not None:
                _patch_ticket_sync_service(target)

        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; per-guild ticket category/staff/archive config guard active")


__all__ = []
