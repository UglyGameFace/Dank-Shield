from __future__ import annotations

"""Private preview and non-persistent temporary test delivery for stickies."""

from typing import Any, Optional

import discord

from stoney_verify.community_tools_runtime import sticky_embed, sticky_poll_embed
from stoney_verify.community_tools_service import StickyConfig, StickyPoll

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _manage_messages(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return bool(
        isinstance(member, discord.Member)
        and (member.guild_permissions.administrator or member.guild_permissions.manage_messages)
    )


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


class StickyPreviewTestView(discord.ui.View):
    def __init__(self, owner_id: int, config: StickyConfig, poll: Optional[StickyPoll]) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.config = config
        self.poll = poll

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "❌ Open your own Sticky Messages panel to run a test.",
            ephemeral=True,
            allowed_mentions=_ALLOWED_MENTIONS,
        )
        return False

    @discord.ui.button(label="Post 30s Test", emoji="🧪", style=discord.ButtonStyle.primary)
    async def post_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_messages(interaction):
            return await interaction.response.send_message(
                "❌ Temporary sticky tests require **Manage Messages**.",
                ephemeral=True,
                allowed_mentions=_ALLOWED_MENTIONS,
            )
        channel = _text_channel(interaction)
        if channel is None:
            return await interaction.response.send_message(
                "❌ Run the test inside a normal text channel.",
                ephemeral=True,
                allowed_mentions=_ALLOWED_MENTIONS,
            )

        try:
            if self.config.mode == "plain":
                await channel.send(
                    content=self.config.content,
                    allowed_mentions=_ALLOWED_MENTIONS,
                    delete_after=30,
                )
            elif self.config.mode == "embed":
                await channel.send(
                    embed=sticky_embed(self.config),
                    allowed_mentions=_ALLOWED_MENTIONS,
                    delete_after=30,
                )
            elif self.config.mode == "poll" and self.poll is not None:
                await channel.send(
                    content="🧪 **Sticky poll test** — voting is disabled in temporary tests.",
                    embed=sticky_poll_embed(self.poll),
                    allowed_mentions=_ALLOWED_MENTIONS,
                    delete_after=30,
                )
            else:
                return await interaction.response.send_message(
                    "❌ This sticky is missing preview data.",
                    ephemeral=True,
                    allowed_mentions=_ALLOWED_MENTIONS,
                )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message(
                "❌ Dank Shield could not post the temporary test in this channel.",
                ephemeral=True,
                allowed_mentions=_ALLOWED_MENTIONS,
            )

        note = "✅ Temporary test posted for 30 seconds. It did **not** move or replace the real sticky."
        if self.config.use_webhook:
            note += " The test uses Dank Shield's identity; the live sticky will still use your configured custom sender."
        await interaction.response.send_message(note, ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS)


async def show_sticky_preview(
    interaction: discord.Interaction,
    config: Optional[StickyConfig],
    poll: Optional[StickyPoll],
) -> None:
    if not _manage_messages(interaction):
        return await interaction.response.send_message(
            "❌ Sticky preview requires **Manage Messages**.",
            ephemeral=True,
            allowed_mentions=_ALLOWED_MENTIONS,
        )
    if config is None:
        return await interaction.response.send_message(
            "ℹ️ Create a sticky first, then come back here to preview it before changing the live message.",
            ephemeral=True,
            allowed_mentions=_ALLOWED_MENTIONS,
        )

    content = "👁️ **Private sticky preview** — only you can see this."
    embed: Optional[discord.Embed] = None
    if config.mode == "plain":
        content += f"\n\n{config.content}"
    elif config.mode == "embed":
        embed = sticky_embed(config)
    elif config.mode == "poll" and poll is not None:
        content += "\nVoting buttons are intentionally disabled in preview/test mode."
        embed = sticky_poll_embed(poll)
    else:
        content += "\n\n⚠️ Preview data is incomplete for this sticky."

    if config.use_webhook:
        content += f"\n\n🎭 **Live sender:** {config.sender_name or 'Dank Shield'}"

    payload: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
        "view": StickyPreviewTestView(int(interaction.user.id), config, poll),
    }
    if embed is not None:
        payload["embed"] = embed
    await interaction.response.send_message(**payload)


__all__ = ["StickyPreviewTestView", "show_sticky_preview"]
