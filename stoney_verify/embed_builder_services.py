from __future__ import annotations

"""Owned service entrypoints for Dank Shield Embed Builder.

The slash commands remain available, but setup Feature Centers can use these
services directly so the product is centralized and not command-object driven.
"""

from typing import Any, Optional

import discord


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


class EmbedDraftModal(discord.ui.Modal):
    def __init__(self, *, channel: discord.TextChannel) -> None:
        super().__init__(title="Draft Embed Message")
        self.channel = channel
        self.title_input = discord.ui.TextInput(label="Title", max_length=256, required=True, placeholder="Rules, Info, Announcement...")
        self.body_input = discord.ui.TextInput(label="Body", style=discord.TextStyle.paragraph, max_length=4000, required=True, placeholder="Write the message body here...")
        self.color_input = discord.ui.TextInput(label="Color name or hex", max_length=16, required=False, placeholder="blue, green, #43b581")
        self.footer_input = discord.ui.TextInput(label="Footer", max_length=2048, required=False, placeholder="Optional")
        for item in (self.title_input, self.body_input, self.color_input, self.footer_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_embed_group as embed_group
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

        if not await _require_setup_permission(interaction):
            return
        if interaction.guild is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
        missing = embed_group._missing_send_perms(self.channel, interaction.guild.me)
        if missing:
            return await _send_ephemeral(interaction, "❌ Dank Shield is missing in the target channel: " + ", ".join(missing))
        embed = embed_group._build_embed(
            title=str(self.title_input.value),
            body=str(self.body_input.value),
            color=str(self.color_input.value or ""),
            footer=str(self.footer_input.value or ""),
        )
        view = embed_group.EmbedConfirmView(target=self.channel, embed=embed, author_id=int(interaction.user.id))
        await _send_ephemeral(
            interaction,
            f"Preview for {self.channel.mention}. Nothing posts publicly until you press **Send Embed**.",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class EmbedBuilderChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose where this embed should post...",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank_setup_embed:channel",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

        if not await _require_setup_permission(interaction):
            return
        channel = self.values[0] if self.values else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "❌ Pick a normal text channel.")
        await interaction.response.send_modal(EmbedDraftModal(channel=channel))


class EmbedBuilderCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(EmbedBuilderChannelSelect())


async def open_embed_builder_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="📝 Embed Builder",
        description=(
            "Build rules, info, announcement, and instruction embeds from setup.\n\n"
            "Choose a target text channel below, fill out the draft, then confirm before anything posts publicly."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Safety", value="Nothing posts publicly until you press **Send Embed** on the preview.", inline=False)
    embed.set_footer(text="/dank setup • Feature Centers • Embed Builder")
    await _send_ephemeral(interaction, embed=embed, view=EmbedBuilderCenterView(), allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_embed_builder_center", "EmbedBuilderCenterView", "EmbedDraftModal"]
