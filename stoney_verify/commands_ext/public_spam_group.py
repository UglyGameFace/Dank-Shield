from __future__ import annotations

"""Public /stoney spam command family.

Boring/professional command strategy:
- Keep spam guard controls, but do not expose /spam_guard and /spam_guard_status
  as separate top-level commands.
- Capture the existing working callbacks, attach them under /stoney spam, then
  remove the legacy top-level aliases from the local command tree before sync.
"""

import inspect
from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from .public_setup_group import stoney_group


_REGISTERED = False
_LEGACY_COMMANDS: dict[str, app_commands.Command[Any, ..., Any]] = {}

spam_group = app_commands.Group(
    name="spam",
    description="Spam guard controls and status.",
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


async def _call_legacy_command(interaction: discord.Interaction, legacy_name: str) -> None:
    if not await _staff_only(interaction):
        return

    command = _LEGACY_COMMANDS.get(legacy_name)
    if command is None:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ Spam guard command callback was not found. "
                    "Restart after the spam guard module is loaded, or check the spam guard registration logs."
                ),
                "ephemeral": True,
            },
        )

    callback = getattr(command, "callback", None)
    if not callable(callback):
        return await reply_once(interaction, {"content": "❌ Spam guard callback is unavailable.", "ephemeral": True})

    try:
        result = callback(interaction)
        if inspect.isawaitable(result):
            await result
    except TypeError as e:
        # The legacy callback probably changed signature. Keep this graceful so a
        # stale command surface does not crash all public command registration.
        await reply_once(
            interaction,
            {
                "content": (
                    "❌ Spam guard callback signature changed and needs a wrapper update.\n"
                    f"`{type(e).__name__}: {str(e)[:300]}`"
                ),
                "ephemeral": True,
            },
        )
    except Exception as e:
        await reply_once(
            interaction,
            {
                "content": f"❌ Spam guard command failed: `{type(e).__name__}: {str(e)[:300]}`",
                "ephemeral": True,
            },
        )


@spam_group.command(name="panel", description="Open the interactive spam guard control panel.")
async def spam_panel(interaction: discord.Interaction) -> None:
    await _call_legacy_command(interaction, "spam_guard")


@spam_group.command(name="status", description="Show spam guard status and persistence diagnostics.")
async def spam_status(interaction: discord.Interaction) -> None:
    await _call_legacy_command(interaction, "spam_guard_status")


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
        return

    removed: list[str] = []
    for legacy_name in ("spam_guard", "spam_guard_status"):
        if _capture_and_remove_legacy(tree, legacy_name):
            removed.append(legacy_name)

    try:
        if stoney_group.get_command("spam") is None:
            stoney_group.add_command(spam_group)
            print("✅ public_spam_group: attached /stoney spam commands")
        else:
            print("✅ public_spam_group: /stoney spam already attached")
    except Exception as e:
        print(f"⚠️ public_spam_group failed attaching /stoney spam: {repr(e)}")
        raise

    if removed:
        try:
            print(f"🧹 public_spam_group removed legacy top-level spam commands: {removed}")
        except Exception:
            pass

    _REGISTERED = True


__all__ = ["register_public_spam_group_commands", "spam_group"]
