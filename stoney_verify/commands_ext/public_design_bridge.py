from __future__ import annotations

"""Bridge from setup/manage menus into Dank Design.

This is intentionally small. The actual design engine still lives in the design
studio service/command modules. Setup should only route users to design tools,
not duplicate all font/separator/category-frame controls.
"""

import discord


async def open_design_studio_from_setup(interaction: discord.Interaction) -> None:
    """Open Dank Design beside Setup without replacing the Setup screen."""

    try:
        from stoney_verify.commands_ext import public_design_studio as design

        if not await design._require_design_permission(interaction):  # type: ignore[attr-defined]
            return

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        options = await design._load_design_options(int(guild.id))  # type: ignore[attr-defined]
        embed = design._home_embed(guild, options)  # type: ignore[attr-defined]
        embed.title = "🎨 Dank Design Studio"
        embed.add_field(
            name="Opened from Setup",
            value=(
                "Your **Server Design** setup page is still open underneath this panel, "
                "so you can return to Setup without losing your place.\n\n"
                "Use Dank Design for fonts, separators, category frames, emojis, exact "
                "format rules, preview/apply, mismatch repair, and rollback."
            ),
            inline=False,
        )

        view = design.DesignHomeView(options)  # type: ignore[attr-defined]
        kwargs = {
            "embed": embed,
            "view": view,
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
        kwargs = {
            "embed": embed,
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)


__all__ = ["open_design_studio_from_setup"]
