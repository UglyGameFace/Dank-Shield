from __future__ import annotations

"""
Runtime per-guild ticket configuration patch.

Purpose:
- keep public/beta deployments from relying on one env-only TICKET_CATEGORY_ID / STAFF_ROLE_ID
- make ticket creation prefer guild_configs for category/staff/transcript settings
- make close/reopen lifecycle movement prefer guild_configs for active/archive categories
- keep the old env values as a safe local fallback

This is intentionally defensive and import-hook based because several legacy modules
still read globals directly while the codebase is being moved toward permanent
per-guild configuration.
"""

import asyncio
import builtins
import inspect
import sys
import time
from typing import Any, Optional

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🧭 runtime_guild_config_ticket_patch {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_guild_config_ticket_patch {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
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
            # discord.Guild-like
            if hasattr(value, "id") and hasattr(value, "get_channel") and hasattr(value, "roles"):
                return value
            # discord.Member/TextChannel/Interaction-like
            guild = getattr(value, "guild", None)
            if guild is not None and hasattr(guild, "id"):
                return guild
            # interaction namespace may hide guild under .guild
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
        # Avoid importing discord here; CategoryChannel has channels + guild + id.
        if channel is not None and hasattr(channel, "channels") and hasattr(channel, "guild"):
            return channel
    except Exception:
        return None
    return None


def _signature_accepts(original: Any, name: str) -> bool:
    try:
        sig = inspect.signature(original)
        if name in sig.parameters:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except Exception:
        # Unknown callable shape: do not risk injecting extra kwargs.
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
    patch_key = f"{module_name}:guild_config_ticket_patch_v2"
    if patch_key in _PATCHED_MODULES:
        return

    # ------------------------------------------------------------------
    # Sync helpers: use cached per-guild config where legacy code expects
    # a synchronous category resolver.
    # ------------------------------------------------------------------
    original_resolve_parent = getattr(module, "_resolve_ticket_parent_category", None)
    if callable(original_resolve_parent) and not getattr(original_resolve_parent, "_guild_config_wrapped", False):
        def _resolve_ticket_parent_category(guild: Any, explicit_parent_category_id: Any = None) -> Any:
            explicit_id = _safe_int(explicit_parent_category_id, 0)
            if explicit_id > 0:
                explicit = _resolve_category_by_id(guild, explicit_id)
                if explicit is not None:
                    return explicit

            cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
            cfg_category_id = _cfg_int(cfg, "ticket_category_id", 0)
            if cfg_category_id > 0:
                configured = _resolve_category_by_id(guild, cfg_category_id)
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
            cfg = _cached_config_for_guild_id(_safe_int(getattr(guild, "id", 0), 0))
            cfg_category_id = _cfg_int(cfg, "ticket_category_id", 0)
            if cfg_category_id > 0:
                configured = _resolve_category_by_id(guild, cfg_category_id)
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
                configured = _resolve_category_by_id(guild, cfg_archive_id)
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

    # ------------------------------------------------------------------
    # Async create wrapper: fetch per-guild config before legacy creation,
    # then pass only kwargs the legacy function supports.
    # ------------------------------------------------------------------
    original_create = getattr(module, "create_ticket_channel", None)
    if callable(original_create) and not getattr(original_create, "_guild_config_wrapped", False):
        async def _create_ticket_channel_with_guild_config(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            guild = _guild_from_call(args, kwargs)
            guild_id = _safe_int(getattr(guild, "id", 0), 0)
            cfg = await _config_for_guild(guild) if guild_id > 0 else None

            if cfg is not None:
                category_id = _cfg_int(cfg, "ticket_category_id", 0)
                archive_category_id = _cfg_int(cfg, "ticket_archive_category_id", 0)
                staff_role_id = _cfg_int(cfg, "staff_role_id", 0)
                transcripts_channel_id = _cfg_int(cfg, "transcripts_channel_id", 0)
                ticket_prefix = _cfg_str(cfg, "ticket_prefix", "ticket") or "ticket"

                # Common current/legacy names. Each is injected only if the
                # target callable supports it, so this stays safe across refactors.
                if category_id > 0:
                    _maybe_set_kwarg(original_create, kwargs, "parent_category_id", category_id)
                    _maybe_set_kwarg(original_create, kwargs, "explicit_parent_category_id", category_id)
                    _maybe_set_kwarg(original_create, kwargs, "ticket_category_id", category_id)

                if archive_category_id > 0:
                    _maybe_set_kwarg(original_create, kwargs, "ticket_archive_category_id", archive_category_id)
                    _maybe_set_kwarg(original_create, kwargs, "archive_category_id", archive_category_id)
                    _maybe_set_kwarg(original_create, kwargs, "closed_ticket_category_id", archive_category_id)

                if staff_role_id > 0:
                    _maybe_set_kwarg(original_create, kwargs, "staff_role_ids", [staff_role_id])
                    _maybe_set_kwarg(original_create, kwargs, "staff_role_id", staff_role_id)

                if transcripts_channel_id > 0:
                    _maybe_set_kwarg(original_create, kwargs, "transcripts_channel_id", transcripts_channel_id)
                    _maybe_set_kwarg(original_create, kwargs, "transcript_channel_id", transcripts_channel_id)

                if ticket_prefix:
                    _maybe_set_kwarg(original_create, kwargs, "ticket_prefix", ticket_prefix)

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
                # If a legacy function rejected a kwarg despite signature support,
                # retry once with only the user's original kwargs.
                if "unexpected keyword argument" not in repr(e).lower():
                    raise
                _warn(f"ticket create kwarg compatibility retry guild={guild_id}: {e!r}")
                return await original_create(*args, **{k: v for k, v in kwargs.items() if k not in {
                    "parent_category_id",
                    "explicit_parent_category_id",
                    "ticket_category_id",
                    "ticket_archive_category_id",
                    "archive_category_id",
                    "closed_ticket_category_id",
                    "staff_role_ids",
                    "staff_role_id",
                    "transcripts_channel_id",
                    "transcript_channel_id",
                    "ticket_prefix",
                }})

        try:
            setattr(_create_ticket_channel_with_guild_config, "_guild_config_wrapped", True)
        except Exception:
            pass
        setattr(module, "create_ticket_channel", _create_ticket_channel_with_guild_config)

    _PATCHED_MODULES.add(patch_key)
    _log(f"patched {module_name}; ticket creation + close/reopen categories now prefer guild_configs")


def _maybe_patch_loaded_modules() -> None:
    try:
        module = sys.modules.get("stoney_verify.tickets_new.service")
        if module is not None:
            _patch_ticket_service(module)
    except Exception as e:
        _warn(f"loaded-module patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_ticket_service(target)
        _maybe_patch_loaded_modules()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_maybe_patch_loaded_modules()
_log("loaded; per-guild ticket category/staff/archive config guard active")
