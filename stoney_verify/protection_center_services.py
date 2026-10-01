from __future__ import annotations

"""Owned service entrypoints for Dank Shield Protection Center.

These functions are intentionally separate from slash-command registration so
/dank setup Feature Centers can call product behavior directly instead of
calling decorated app_commands.Command objects.
"""

import discord


async def open_protection_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_protection_center as protection

    await protection._ack_protection_entry(interaction)
    if not await protection._require_setup_permission(interaction):
        return
    await protection._refresh_panel(
        interaction,
        content="🛡️ Protection Center opened.",
    )


__all__ = ["open_protection_center"]
