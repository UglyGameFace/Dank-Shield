from __future__ import annotations

"""Public /dank spam command family.

Boring/professional command strategy:
- Keep SpamGuard controls grouped under /dank spam.
- Do not expose /spam_guard and /spam_guard_status as separate top-level commands.
- Prefer captured legacy callbacks when they exist.
- In public mode those legacy top-level commands may never be registered, so this
  wrapper also resolves the real callbacks directly from stoney_verify.spam_guard.
- Export normal helper functions for setup buttons. Discord app-command objects
  are not normal callables after decoration, so setup must not call the command
  object directly.
"""

import inspect
from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from .public_setup_group import stoney_group


_REGISTERED = False
_LEGACY_COMMANDS: dict[str, app_commands.Command[Any, ..., Any]] = {}
_SETUP_BUTTON_PATCHED = False

spam_group = app_commands.Group(
    name="spam",
    description="SpamGuard controls and status.",
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


def _callable_accepts_interaction(callback: Any) -> bool:
    try:
        signature = inspect.signature(callback)
        params = list(signature.parameters.values())
        if not params:
            return False
        return any(str(p.name).lower() in {"interaction", "ctx"} for p in params[:2])
    except Exception:
        return True


def _command_from_module_by_name(module: Any, legacy_name: str) -> Optional[app_commands.Command[Any, ..., Any]]:
    try:
        for value in vars(module).values():
            if isinstance(value, app_commands.Command) and not isinstance(value, app_commands.Group):
                if str(getattr(value, "name", "")).strip().lower() == legacy_name.lower():
                    return value
    except Exception:
        pass
    return None


def _callback_from_module_by_candidate(module: Any, legacy_name: str) -> Optional[Any]:
    candidates = {
        "spam_guard": (
            "spam_guard",
            "spam_guard_command",
            "spam_guard_panel",
            "open_spam_guard_panel",
            "show_spam_guard_panel",
            "spam_panel",
            "panel",
        ),
        "spam_guard_status": (
            "spam_guard_status",
            "spam_guard_status_command",
            "show_spam_guard_status",
            "spam_guard_diagnostics",
            "spam_status",
            "status",
        ),
    }.get(legacy_name, ())

    for name in candidates:
        try:
            value = getattr(module, name, None)
            if isinstance(value, app_commands.Command):
                callback = getattr(value, "callback", None)
                if callable(callback):
                    return callback
            if callable(value) and _callable_accepts_interaction(value):
                return value
        except Exception:
            continue
    return None


def _resolve_spamguard_callback(legacy_name: str) -> tuple[Optional[Any], str]:
    command = _LEGACY_COMMANDS.get(legacy_name)
    if command is not None:
        callback = getattr(command, "callback", None)
        if callable(callback):
            return callback, "captured_tree_command"

    try:
        from stoney_verify import spam_guard
    except Exception as e:
        return None, f"spam_guard_import_failed:{type(e).__name__}"

    module_command = _command_from_module_by_name(spam_guard, legacy_name)
    if module_command is not None:
        callback = getattr(module_command, "callback", None)
        if callable(callback):
            return callback, "module_app_command"

    module_callback = _callback_from_module_by_candidate(spam_guard, legacy_name)
    if callable(module_callback):
        return module_callback, "module_candidate"

    return None, "not_found"


async def _invoke_callback(interaction: discord.Interaction, callback: Any) -> None:
    try:
        result = callback(interaction)
    except TypeError:
        result = callback(interaction=interaction)
    if inspect.isawaitable(result):
        await result


async def _call_spamguard_command(interaction: discord.Interaction, legacy_name: str) -> None:
    if not await _staff_only(interaction):
        return

    callback, source = _resolve_spamguard_callback(legacy_name)
    if callback is None:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ SpamGuard panel/status callback was not found.\n"
                    "The grouped public command loaded, but the core spam_guard module did not expose the expected callback. "
                    "This needs a wrapper update, not a server setup change.\n"
                    f"`source={source}`"
                ),
                "ephemeral": True,
            },
        )

    try:
        await _invoke_callback(interaction, callback)
    except TypeError as e:
        await reply_once(
            interaction,
            {
                "content": (
                    "❌ SpamGuard callback signature changed and needs a wrapper update.\n"
                    f"`{type(e).__name__}: {str(e)[:300]}`"
                ),
                "ephemeral": True,
            },
        )
    except Exception as e:
        await reply_once(
            interaction,
            {
                "content": f"❌ SpamGuard command failed from `{source}`: `{type(e).__name__}: {str(e)[:300]}`",
                "ephemeral": True,
            },
        )


async def open_spamguard_panel(interaction: discord.Interaction) -> None:
    """Open the real SpamGuard panel from setup or /dank spam."""
    await _call_spamguard_command(interaction, "spam_guard")


async def show_spamguard_status(interaction: discord.Interaction) -> None:
    """Show SpamGuard status from setup or /dank spam."""
    await _call_spamguard_command(interaction, "spam_guard_status")


async def _setup_button_call_spam_group(interaction: discord.Interaction, target: str) -> None:
    if str(target).lower() == "panel":
        await open_spamguard_panel(interaction)
    else:
        await show_spamguard_status(interaction)


def _patch_setup_service_buttons() -> None:
    """Make /dank setup buttons call normal helpers, not decorated commands."""
    global _SETUP_BUTTON_PATCHED
    if _SETUP_BUTTON_PATCHED:
        return
    try:
        from stoney_verify.startup_guards import setup_service_modes

        setup_service_modes._call_spam_group = _setup_button_call_spam_group  # type: ignore[attr-defined]
        _SETUP_BUTTON_PATCHED = True
        print("✅ public_spam_group: setup SpamGuard buttons patched to helper path")
    except Exception as e:
        try:
            print(f"⚠️ public_spam_group could not patch setup SpamGuard buttons: {repr(e)}")
        except Exception:
            pass


@spam_group.command(name="panel", description="Open the interactive SpamGuard control panel.")
async def spam_panel(interaction: discord.Interaction) -> None:
    await open_spamguard_panel(interaction)


@spam_group.command(name="status", description="Show SpamGuard status and persistence diagnostics.")
async def spam_status(interaction: discord.Interaction) -> None:
    await show_spamguard_status(interaction)


def _capture_and_remove_legacy(tree: Any, name: str) -> bool:
    try:
        command = tree.get_command(name, guild=None)
    except Exception:
        command = None

    if isinstance(command, app_commands.Command) and not isinstance(command, app_commands.Group):
        _LEGACY_COMMANDS[name] = command

    removed = False
    try:
        if command is not None:
            tree.remove_command(name, guild=None)
            removed = True
    except Exception:
        removed = False

    return removed


def register_public_spam_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot
    if _REGISTERED:
        _patch_setup_service_buttons()
        return

    removed: list[str] = []
    for legacy_name in ("spam_guard", "spam_guard_status"):
        if _capture_and_remove_legacy(tree, legacy_name):
            removed.append(legacy_name)

    try:
        if stoney_group.get_command("spam") is None:
            stoney_group.add_command(spam_group)
            print("✅ public_spam_group: attached /dank spam commands")
        else:
            print("✅ public_spam_group: /dank spam already attached")
    except Exception as e:
        print(f"⚠️ public_spam_group failed attaching /dank spam: {repr(e)}")
        raise

    if removed:
        try:
            print(f"🧹 public_spam_group removed legacy top-level spam commands: {removed}")
        except Exception:
            pass

    _patch_setup_service_buttons()
    _REGISTERED = True


_patch_setup_service_buttons()


__all__ = [
    "register_public_spam_group_commands",
    "spam_group",
    "spam_panel",
    "spam_status",
    "open_spamguard_panel",
    "show_spamguard_status",
]
