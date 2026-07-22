from __future__ import annotations

import discord

from . import setup_legacy_voice_cleanup as cleanup
from .commands_ext import public_setup_solid as solid


async def _build_review_embed(
    guild: discord.Guild,
    *,
    result_message: str = "",
) -> tuple[discord.Embed, cleanup.LegacyVoiceCleanupPreview]:
    preview = await cleanup.find_legacy_voice_cleanup_candidates(guild)

    embed = discord.Embed(
        title="🎙️ Review Old Voice Verify Items",
        description=(
            "This is only for legacy Voice Verify channels created before Dank Shield "
            "started saving exact ownership IDs. Nothing is deleted just by opening this screen."
        ),
        color=discord.Color.orange(),
    )

    if result_message:
        embed.add_field(
            name="Last Action",
            value=result_message[:1024],
            inline=False,
        )

    if preview.blocked_reason:
        embed.add_field(
            name="Not Available Yet",
            value=preview.blocked_reason,
            inline=False,
        )
    elif preview.has_candidates:
        lines: list[str] = []
        if preview.voice_id > 0:
            channel = guild.get_channel(preview.voice_id)
            lines.append(
                f"🎙️ Voice channel: {getattr(channel, 'mention', f'ID {preview.voice_id}')}"
            )
        if preview.queue_id > 0:
            channel = guild.get_channel(preview.queue_id)
            lines.append(
                f"🧾 Staff-request channel: {getattr(channel, 'mention', f'ID {preview.queue_id}')}"
            )
        embed.add_field(
            name="Exact Legacy Candidates",
            value="\n".join(lines)[:1024],
            inline=False,
        )
        embed.add_field(
            name="Safety Rules",
            value=(
                "• Only the exact channels listed above are eligible.\n"
                "• The voice channel is kept if anyone is connected.\n"
                "• The staff-request channel is kept if it has history or cannot be inspected.\n"
                "• If duplicate exact-default channels exist, Dank Shield refuses to guess.\n"
                "• No unrelated channel is touched."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Nothing to Remove",
            value=(
                "No single unambiguous legacy Voice Verify default is available for cleanup. "
                "Dank Shield will not guess based on similar names."
            ),
            inline=False,
        )

    if preview.notes:
        embed.add_field(
            name="Notes",
            value="\n".join(preview.notes)[:1024],
            inline=False,
        )

    embed.set_footer(
        text="Verification • legacy cleanup requires an explicit owner action"
    )
    return embed, preview


class LegacyVoiceCleanupReviewView(discord.ui.View):
    def __init__(
        self,
        *,
        voice_id: int = 0,
        queue_id: int = 0,
        can_remove: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.voice_id = int(voice_id or 0)
        self.queue_id = int(queue_id or 0)
        self.remove_items.disabled = not bool(can_remove)

    @discord.ui.button(
        label="Remove Listed Items",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_legacy_voice:remove",
        row=0,
    )
    async def remove_items(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        await solid._safe_defer_update(interaction)
        result = await cleanup.remove_legacy_voice_cleanup_candidates(
            guild,
            expected_voice_id=self.voice_id,
            expected_queue_id=self.queue_id,
            actor=interaction.user,
        )
        embed, preview = await _build_review_embed(
            guild,
            result_message=result,
        )
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=LegacyVoiceCleanupReviewView(
                voice_id=preview.voice_id,
                queue_id=preview.queue_id,
                can_remove=preview.has_candidates,
            ),
        )

    @discord.ui.button(
        label="Back to Verification",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_legacy_voice:back",
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext import public_setup_recommend as recommend

        await recommend._open_advanced_verification(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_legacy_voice:home",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext import public_setup_recommend as recommend

        await recommend._home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_legacy_voice:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext import public_setup_recommend as recommend

        await recommend._close_setup(interaction)


async def open_legacy_voice_cleanup_review(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    await solid._safe_defer_update(interaction)
    embed, preview = await _build_review_embed(guild)
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=LegacyVoiceCleanupReviewView(
            voice_id=preview.voice_id,
            queue_id=preview.queue_id,
            can_remove=preview.has_candidates,
        ),
    )


__all__ = [
    "LegacyVoiceCleanupReviewView",
    "open_legacy_voice_cleanup_review",
]
