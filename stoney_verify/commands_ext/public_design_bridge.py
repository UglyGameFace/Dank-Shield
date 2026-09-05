from __future__ import annotations

"""Bridge from Setup into the single public Dank Design Studio owner."""

import discord


async def open_design_studio_from_setup(interaction: discord.Interaction) -> None:
    """Open the same Studio hub used by /dank design without duplicating UI."""

    try:
        from stoney_verify.commands_ext import public_design_studio_v2 as design

        if not await design._require_design_permission(interaction):  # type: ignore[attr-defined]
            return

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        options = await design._load_design_options(int(guild.id))  # type: ignore[attr-defined]
        embed = design._home_embed(guild, options)  # type: ignore[attr-defined]
        embed.add_field(
            name="Opened from Setup",
            value=(
                "This is the exact same Dank Design Studio used by `/dank design`. "
                "Setup no longer maintains a competing design screen."
            ),
            inline=False,
        )
        kwargs = {
            "embed": embed,
            "view": design.DesignHomeView(options),  # type: ignore[attr-defined]
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception as exc:
        embed = discord.Embed(
            title="Dank Design Did Not Open",
            description=(
                f"Error: `{type(exc).__name__}: {str(exc)[:220]}`\n\n"
                "Nothing was changed and your Setup page was left in place. "
                "Try `/dank design` directly while this route is repaired."
            ),
            color=discord.Color.orange(),
        )
        kwargs = {"embed": embed, "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)


__all__ = ["open_design_studio_from_setup"]
