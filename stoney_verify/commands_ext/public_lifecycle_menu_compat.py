from __future__ import annotations

"""Keep the existing Welcome/Exit mega menu aligned with compact-v2 commands.

DS-WELCOME-EXIT-023 already built the working lifecycle menu. This compatibility
layer changes only the two navigation items that would otherwise advertise or
recreate retired `/dank welcome ...` upload shortcuts: Uploads & Advanced and
Refresh. The underlying Studios, save paths, previews, and live runtimes remain
unchanged.
"""

from typing import Any

import discord

_INSTALLED = False


class CompactLifecycleAssetsButton(discord.ui.Button):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            label="Uploads & Advanced",
            emoji="📎",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:lifecycle:compact_assets:v1",
            row=3,
        )
        self.owner_id = int(owner_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await interaction.response.send_message(
                "❌ Open your own Welcome, Join & Exit panel to use these controls.",
                ephemeral=True,
            )
        from .public_command_surface_v2 import CardAssetView, _asset_embed

        await interaction.response.edit_message(
            embed=_asset_embed(),
            view=CardAssetView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class CompactLifecycleRefreshButton(discord.ui.Button):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:lifecycle:compact_refresh:v1",
            row=3,
        )
        self.owner_id = int(owner_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await interaction.response.send_message(
                "❌ Open your own Welcome, Join & Exit panel to use these controls.",
                ephemeral=True,
            )
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ Use this inside a server.",
                ephemeral=True,
            )
        from ..guild_config import get_guild_config
        from .. import welcome_setup_ui

        config = await get_guild_config(guild.id, refresh=True)
        await interaction.response.edit_message(
            embed=await welcome_setup_ui._welcome_embed(guild, config),
            view=build_compact_welcome_setup_view(
                owner_id=self.owner_id,
                config=config,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


def build_compact_welcome_setup_view(*, owner_id: int, config: Any) -> discord.ui.View:
    from .. import welcome_setup_ui

    view = welcome_setup_ui.WelcomeSetupView(owner_id=int(owner_id), config=config)
    for item in list(view.children):
        label = str(getattr(item, "label", "") or "")
        if label in {"Uploads & Advanced", "Refresh"}:
            view.remove_item(item)
    view.add_item(CompactLifecycleAssetsButton(int(owner_id)))
    view.add_item(CompactLifecycleRefreshButton(int(owner_id)))
    return view


async def open_compact_welcome_setup(interaction: discord.Interaction) -> None:
    from .public_setup_group import _require_setup_permission
    from ..guild_config import get_guild_config
    from .. import welcome_setup_ui

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await welcome_setup_ui._send_private(
            interaction,
            content="❌ Use this inside a server.",
        )
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        config = await get_guild_config(guild.id, refresh=True)
        await interaction.followup.send(
            embed=await welcome_setup_ui._welcome_embed(guild, config),
            view=build_compact_welcome_setup_view(
                owner_id=int(interaction.user.id),
                config=config,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not open Welcome, Join & Exit: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def install_lifecycle_menu_compat() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    from .. import welcome_setup_ui

    welcome_setup_ui.open_welcome_setup = open_compact_welcome_setup
    _INSTALLED = True
    print(
        "✅ public_lifecycle_menu_compat aligned Welcome/Exit mega menu with /dank upload"
    )
    return True


__all__ = [
    "build_compact_welcome_setup_view",
    "install_lifecycle_menu_compat",
    "open_compact_welcome_setup",
]
