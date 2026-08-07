from __future__ import annotations

"""Navigation controls used by the DS-SETUP-020 custom service picker.

The entitlement compatibility view builds its own dynamic toggle layout, so it
cannot reuse the decorator-owned button instances from the legacy view class.
This module exposes equivalent standalone buttons without depending on private
class attributes that do not exist.
"""

from typing import Any

import discord

_PATCHED = False


class CustomServiceContinueButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Continue Setup",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            custom_id="dank_setup_custom:continue_quick",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
            return
        if not await fresh.solid._require_setup_permission(interaction):
            return

        await fresh.solid._safe_defer_update(interaction)
        state = await fresh._load_custom_state(guild.id)
        reconcile_note = await fresh._reconcile_voice_resources_if_disabled(
            guild,
            state,
            actor=interaction.user,
        )
        if reconcile_note and await fresh._open_legacy_voice_cleanup_if_needed(
            interaction,
            guild,
            reconcile_note,
            already_deferred=True,
        ):
            return
        await recommend._open_guided_setup(
            interaction,
            saved_message=reconcile_note,
        )


class CustomServiceBackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Back",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_custom:plans",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._open_choose_setup_type(interaction)


class CustomServiceHomeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Setup Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_custom:home",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._home_edit(interaction)


class CustomServiceCloseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id="dank_setup_custom:close",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._close_setup(interaction)


def install_custom_service_navigation_compat() -> bool:
    global _PATCHED

    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh

    classes: dict[str, type[discord.ui.Button]] = {
        "CustomServiceContinueButton": CustomServiceContinueButton,
        "CustomServiceBackButton": CustomServiceBackButton,
        "CustomServiceHomeButton": CustomServiceHomeButton,
        "CustomServiceCloseButton": CustomServiceCloseButton,
    }
    for name, button_class in classes.items():
        if not hasattr(fresh, name):
            setattr(fresh, name, button_class)

    _PATCHED = True
    return True


__all__ = [
    "CustomServiceBackButton",
    "CustomServiceCloseButton",
    "CustomServiceContinueButton",
    "CustomServiceHomeButton",
    "install_custom_service_navigation_compat",
]
