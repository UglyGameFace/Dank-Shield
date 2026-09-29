from __future__ import annotations

"""Shared private-panel lifecycle for /role and its child surfaces.

The important distinction is interaction origin:
- slash/modal/private actions may need a fresh ephemeral response;
- component/select interactions must defer as a message update, otherwise
  Discord creates another ephemeral panel and leaves the old controls behind.
"""

from typing import Optional

import discord


async def defer_panel(interaction: discord.Interaction) -> None:
    """Acknowledge without multiplying panels."""
    if interaction.response.is_done():
        return
    if interaction.message is not None:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


async def replace_panel(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.edit_message(**payload)
        else:
            await interaction.response.send_message(**payload, ephemeral=True)
        return
    await interaction.edit_original_response(**payload)


async def close_panel(
    interaction: discord.Interaction,
    *,
    fallback_text: str = "Roles & Profiles closed.",
) -> None:
    """Actually dismiss the private interaction message when Discord allows it."""
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.defer()
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await interaction.delete_original_response()
        return
    except discord.NotFound:
        return
    except discord.HTTPException:
        pass
    try:
        await interaction.edit_original_response(
            content=fallback_text,
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.NotFound, discord.HTTPException):
        return


class CloseRolePanelButton(discord.ui.Button):
    def __init__(self, *, row: int = 4, label: str = "Close") -> None:
        super().__init__(
            label=label,
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await close_panel(interaction)


class BackToRoleCenterButton(discord.ui.Button):
    def __init__(self, *, row: int = 4, label: str = "Roles & Profiles") -> None:
        super().__init__(
            label=label,
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .public_role_center import open_roles_profiles_center

        await open_roles_profiles_center(interaction)


__all__ = [
    "BackToRoleCenterButton",
    "CloseRolePanelButton",
    "close_panel",
    "defer_panel",
    "replace_panel",
]
