from __future__ import annotations

"""Public /dank spam command family.

Command strategy:
- Keep SpamGuard public controls under /dank spam.
- Do not expose /spam_guard and /spam_guard_status as top-level public commands.
- Send public users to the same simple setup-native SpamGuard page used by
  /dank setup, so there are not two conflicting SpamGuard screens.

Important setup rule:
- If an older deployed setup page still shows an "Advanced Standalone Panel"
  button, do not duplicate the same panel again. That button is deprecated and
  should be removed from setup_service_modes.py when the full owner file can be
  rewritten safely. Until then, this owner command refuses to spawn duplicate
  setup cards from that stale button.
"""

import inspect
from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from .public_setup_group import dank_group


_REGISTERED = False
_LEGACY_COMMANDS: dict[str, app_commands.Command[Any, ..., Any]] = {}

spam_group = app_commands.Group(
    name="spam",
    description="Simple SpamGuard setup and status.",
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


def _looks_like_setup_spamguard_message(interaction: discord.Interaction) -> bool:
    """Return True when a stale setup button called this helper.

    The bad button lives on the setup-native SpamGuard card. Pressing it should
    not post a second copy of the same card. We detect that source from the
    message embed rather than relying on a fragile custom_id.
    """
    try:
        message = getattr(interaction, "message", None)
        embeds = list(getattr(message, "embeds", []) or [])
        for embed in embeds:
            title = str(getattr(embed, "title", "") or "").lower()
            description = str(getattr(embed, "description", "") or "").lower()
            footer_text = str(getattr(getattr(embed, "footer", None), "text", "") or "").lower()
            if "spamguard setup" in title and "/dank setup" in (description + " " + footer_text):
                return True
    except Exception:
        pass
    return False


async def _send_stale_advanced_notice(interaction: discord.Interaction) -> None:
    content = (
        "🛡️ **Advanced Standalone Panel was removed from setup.**\n"
        "Use the buttons already on this SpamGuard Setup page. They are the production-safe controls.\n\n"
        "For now: **Enable Actual Guard**, **Use Timeout Mode**, **External Only**, **Allow Own Invites**, and **Watch Verified** are the recommended setup."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


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


async def _call_legacy_spamguard_command(interaction: discord.Interaction, legacy_name: str) -> None:
    callback, source = _resolve_spamguard_callback(legacy_name)
    if callback is None:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ SpamGuard advanced callback was not found.\n"
                    "Use `/dank setup` → **Services** → **SpamGuard Setup** instead.\n"
                    f"`source={source}`"
                ),
                "ephemeral": True,
            },
        )

    try:
        await _invoke_callback(interaction, callback)
    except Exception as e:
        await reply_once(
            interaction,
            {
                "content": f"❌ Advanced SpamGuard command failed from `{source}`: `{type(e).__name__}: {str(e)[:300]}`",
                "ephemeral": True,
            },
        )


async def _open_setup_native_spamguard(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.startup_guards import setup_service_modes

        builder = getattr(setup_service_modes, "build_spamguard_setup_embed", None)
        view_cls = getattr(setup_service_modes, "SpamGuardSetupView", None)
        if not callable(builder) or view_cls is None:
            return False
        if interaction.guild is None:
            return False

        embed = await builder(interaction.guild)
        view = view_cls()
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        return False


async def open_spamguard_panel(interaction: discord.Interaction) -> None:
    """Open the simple setup-native SpamGuard page."""
    if not await _staff_only(interaction):
        return
    if _looks_like_setup_spamguard_message(interaction):
        await _send_stale_advanced_notice(interaction)
        return
    if await _open_setup_native_spamguard(interaction):
        return
    await _call_legacy_spamguard_command(interaction, "spam_guard")


async def show_spamguard_status(interaction: discord.Interaction) -> None:
    """Show the simple setup-native SpamGuard status page."""
    if not await _staff_only(interaction):
        return
    if _looks_like_setup_spamguard_message(interaction):
        await _send_stale_advanced_notice(interaction)
        return
    if await _open_setup_native_spamguard(interaction):
        return
    await _call_legacy_spamguard_command(interaction, "spam_guard_status")


@spam_group.command(name="panel", description="Open simple SpamGuard setup.")
async def spam_panel(interaction: discord.Interaction) -> None:
    await open_spamguard_panel(interaction)


@spam_group.command(name="status", description="Show simple SpamGuard status and fixes.")
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
        return

    removed: list[str] = []
    for legacy_name in ("spam_guard", "spam_guard_status"):
        if _capture_and_remove_legacy(tree, legacy_name):
            removed.append(legacy_name)

    try:
        if dank_group.get_command("spam") is None:
            dank_group.add_command(spam_group)
            print("✅ public_spam_group: attached /dank spam simple setup commands")
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

    _REGISTERED = True


__all__ = [
    "register_public_spam_group_commands",
    "spam_group",
    "spam_panel",
    "spam_status",
    "open_spamguard_panel",
    "show_spamguard_status",
]
