from __future__ import annotations

"""Owned service entrypoints for Dank Shield Protection Center.

These functions are intentionally separate from slash-command registration so
/dank setup Feature Centers can call product behavior directly instead of
calling decorated app_commands.Command objects.
"""

import discord


async def open_protection_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_protection_center as protection

    if not await protection._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await protection._send_ephemeral(interaction, "❌ This must be used inside a server.")

    cfg = await protection.get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await protection._load_spam_settings(int(guild.id))
    embed = protection._protection_embed(guild, cfg, spam, spam_source)
    view = protection.ProtectionCenterView(author_id=int(interaction.user.id))
    await protection._send_ephemeral(interaction, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_protection_center"]
