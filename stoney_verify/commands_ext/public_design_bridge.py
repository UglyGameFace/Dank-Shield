from __future__ import annotations

"""Bridge from setup/manage menus into Dank Design.

This is intentionally small. The actual design engine still lives in the design
studio service/command modules. Setup should only route users to design tools,
not duplicate all font/separator/category-frame controls.
"""

from typing import Any

import discord


async def open_design_studio_from_setup(interaction: discord.Interaction) -> None:
    """Open Dank Design from a setup/manage button."""

    try:
        from stoney_verify.startup_guards import server_design_studio_command_guard as design

        if not await design._require_design_permission(interaction):  # type: ignore[attr-defined]
            return

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        options = await design._load_design_options(int(guild.id))  # type: ignore[attr-defined]
        embed = design._home_embed(guild, options)  # type: ignore[attr-defined]
        embed.title = "🎨 Dank Design Studio"
        embed.add_field(
            name="Opened from Setup",
            value=(
                "Use this for fonts, separators, category frames, emojis, exact format rules, "
                "preview/apply, mismatch repair, and rollback."
            ),
            inline=False,
        )

        view = design.DesignHomeView(options)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=embed, view=view)
    except Exception as exc:
        embed = discord.Embed(
            title="Dank Design Did Not Open",
            description=(
                f"Error: `{type(exc).__name__}: {str(exc)[:220]}`\n\n"
                "Nothing was changed. Try `/dank design` directly while this route is repaired."
            ),
            color=discord.Color.orange(),
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            raise


__all__ = ["open_design_studio_from_setup"]
