from __future__ import annotations

"""Fix /dank setup SpamGuard buttons.

The service-mode setup view originally tried to call public_spam_group.spam_panel
and public_spam_group.spam_status directly. After @spam_group.command decorates
those functions, they are Discord app-command objects, not normal callables.

/dank spam panel works because Discord invokes the command callback internally,
but the setup button needs a plain helper function. public_spam_group now exports
open_spamguard_panel() and show_spamguard_status(); this guard points setup at
those helpers.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ setup_spamguard_button_fix {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_spamguard_button_fix {message}")
    except Exception:
        pass


async def _fixed_call_spam_group(interaction: discord.Interaction, target: str) -> None:
    try:
        from stoney_verify.commands_ext import public_spam_group

        helper_name = "open_spamguard_panel" if str(target).lower() == "panel" else "show_spamguard_status"
        helper = getattr(public_spam_group, helper_name, None)
        if callable(helper):
            await helper(interaction)
            return

        # Fallback for older deployments where the helper has not loaded yet.
        command_name = "spam_panel" if str(target).lower() == "panel" else "spam_status"
        command = getattr(public_spam_group, command_name, None)
        callback = getattr(command, "callback", None)
        if callable(callback):
            await callback(interaction)
            return
    except Exception as e:
        _warn(f"SpamGuard setup action failed target={target}: {e!r}")

    content = (
        "❌ SpamGuard setup action is unavailable. "
        "Try `/dank spam panel`. If that works, redeploy once more so setup loads the updated helper."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def install_setup_spamguard_button_fix() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_service_modes

        setup_service_modes._call_spam_group = _fixed_call_spam_group  # type: ignore[attr-defined]
        _PATCHED = True
        _log("patched setup service buttons to call SpamGuard helpers")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


install_setup_spamguard_button_fix()


__all__ = ["install_setup_spamguard_button_fix"]
