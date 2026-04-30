from __future__ import annotations

"""
Native ticket lifecycle wiring patch.

The clean helpers live in:
    stoney_verify.tickets_new.lifecycle_categories

This shim wires BOTH of these call paths into the native helpers:
- stoney_verify.tickets_new.service
- stoney_verify.commands_ext.ticket_admin

Why both matter:
The public /ticket close command still calls commands_ext.ticket_admin helpers.
If only tickets_new.service is patched, /ticket close can mark the DB row closed
but fail to move the Discord channel to the archive category. That is the exact
legacy split-brain this guard closes.
"""

import builtins
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()
_PATCHING = False


def _log(message: str) -> None:
    try:
        print(f"🎫 runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


def _permission_snapshot(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unavailable"
        perms = channel.permissions_for(me)
        missing: list[str] = []
        for label, ok in (
            ("View Channel", getattr(perms, "view_channel", False)),
            ("Manage Channels", getattr(perms, "manage_channels", False)),
            ("Read Message History", getattr(perms, "read_message_history", False)),
            ("Send Messages", getattr(perms, "send_messages", False)),
        ):
            if not ok:
                missing.append(label)
        return "missing=" + ", ".join(missing) if missing else "permissions=ok"
    except Exception as e:
        return f"permission_snapshot_failed={type(e).__name__}"


async def _move_archive(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_archive_category

        result = await move_ticket_to_archive_category(channel)
        if result.already_correct:
            _log(
                "archive move already correct "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        else:
            _log(
                "archive move complete "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        return True
    except discord.Forbidden as e:
        _warn(
            "archive lifecycle move forbidden "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} {_permission_snapshot(channel)} raw={e!r}"
        )
        return False
    except Exception as e:
        _warn(
            "archive lifecycle move failed "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} error={e!r}"
        )
        return False


async def _move_active(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_active_category

        result = await move_ticket_to_active_category(channel)
        if result.already_correct:
            _log(
                "active move already correct "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        else:
            _log(
                "active move complete "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        return True
    except discord.Forbidden as e:
        _warn(
            "active lifecycle move forbidden "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} {_permission_snapshot(channel)} raw={e!r}"
        )
        return False
    except Exception as e:
        _warn(
            "active lifecycle move failed "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} error={e!r}"
        )
        return False


def _patch_lifecycle_module(module: Any) -> None:
    global _PATCHING

    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:native_lifecycle_categories_v2"
    if key in _PATCHED or _PATCHING:
        return

    _PATCHING = True
    try:
        patched_any = False

        original_archive = getattr(module, "_move_ticket_to_archive_if_configured", None)
        if callable(original_archive) and not getattr(original_archive, "_native_lifecycle_wrapped", False):
            async def _move_ticket_to_archive_if_configured_native(channel: discord.TextChannel) -> bool:
                moved = await _move_archive(channel)
                if moved:
                    return True
                try:
                    legacy_moved = bool(await original_archive(channel))
                    if not legacy_moved:
                        _warn(
                            "archive move returned false after native+legacy attempts "
                            f"module={module_name} guild={channel.guild.id} channel={channel.id} current_category={getattr(getattr(channel, 'category', None), 'name', None)!r}"
                        )
                    return legacy_moved
                except Exception as e:
                    _warn(f"legacy archive fallback failed module={module_name} channel={getattr(channel, 'id', None)}: {e!r}")
                    return False

            try:
                setattr(_move_ticket_to_archive_if_configured_native, "_native_lifecycle_wrapped", True)
            except Exception:
                pass
            setattr(module, "_move_ticket_to_archive_if_configured", _move_ticket_to_archive_if_configured_native)
            patched_any = True

        original_active = getattr(module, "_move_ticket_to_active_if_configured", None)
        if callable(original_active) and not getattr(original_active, "_native_lifecycle_wrapped", False):
            async def _move_ticket_to_active_if_configured_native(channel: discord.TextChannel) -> bool:
                moved = await _move_active(channel)
                if moved:
                    return True
                try:
                    legacy_moved = bool(await original_active(channel))
                    if not legacy_moved:
                        _warn(
                            "active move returned false after native+legacy attempts "
                            f"module={module_name} guild={channel.guild.id} channel={channel.id} current_category={getattr(getattr(channel, 'category', None), 'name', None)!r}"
                        )
                    return legacy_moved
                except Exception as e:
                    _warn(f"legacy active fallback failed module={module_name} channel={getattr(channel, 'id', None)}: {e!r}")
                    return False

            try:
                setattr(_move_ticket_to_active_if_configured_native, "_native_lifecycle_wrapped", True)
            except Exception:
                pass
            setattr(module, "_move_ticket_to_active_if_configured", _move_ticket_to_active_if_configured_native)
            patched_any = True

        if patched_any:
            _PATCHED.add(key)
            _log(f"patched {module_name}; close/reopen category movement now uses native lifecycle helpers")
    finally:
        _PATCHING = False


def _patch_loaded() -> None:
    for module_name in (
        "stoney_verify.tickets_new.service",
        "stoney_verify.commands_ext.ticket_admin",
    ):
        try:
            module = sys.modules.get(module_name)
            if module is not None:
                _patch_lifecycle_module(module)
        except Exception as e:
            _warn(f"loaded lifecycle patch failed module={module_name}: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name in {"stoney_verify.tickets_new.service", "stoney_verify.commands_ext.ticket_admin"} or name.endswith("tickets_new.service") or name.endswith("commands_ext.ticket_admin"):
            for module_name in ("stoney_verify.tickets_new.service", "stoney_verify.commands_ext.ticket_admin", name):
                target = sys.modules.get(module_name)
                if target is not None:
                    _patch_lifecycle_module(target)
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; native ticket lifecycle movement wiring active")
