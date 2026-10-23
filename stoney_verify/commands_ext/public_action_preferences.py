from __future__ import annotations

"""Member-owned, private Future Member Setup reminder preference controls."""

import discord

from stoney_verify.action_member_consent import (
    MemberReminderConsent, read_member_consent, save_member_consent,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_action_builder import _defer, _msg, _replace


def _embed(consent: MemberReminderConsent) -> discord.Embed:
    enabled = consent.member_setup_dm
    e = discord.Embed(
        title="🔔 My Action Reminder Preferences",
        description=(
            "**Your choices apply only to this server.**\n\n"
            "**Future Member Setup DMs:** "
            + ("✅ You opted in" if enabled else "🔕 Off")
            + "\n\n"
            "These would remind you to review a newly published Member Setup "
            "revision that you still haven't completed.\n\n"
            "**Important:** Automatic Action Center reminders are **not live**. "
            "This preference does not activate a scheduler, change verification, "
            "or send a message. Verification reminders have separate controls."
        ),
        color=discord.Color.blurple() if enabled else discord.Color.light_grey(),
    )
    e.set_footer(text="Private • Default off • Change your choice anytime")
    return e


class MemberActionPreferencesView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int, state: MemberReminderConsent) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)
        self.enable.disabled = state.member_setup_dm
        self.stop.disabled = not state.member_setup_dm

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild_id != self.guild_id
            or int(interaction.user.id) != self.owner_id
            or not isinstance(interaction.user, discord.Member)
        ):
            await _msg(interaction, "Open your own reminder preferences inside this server.")
            return False
        return True

    async def _save(self, interaction: discord.Interaction, *, enabled: bool) -> None:
        await _defer(interaction)
        try:
            confirmed = await save_member_consent(
                self.guild_id, self.owner_id, enabled=enabled,
            )
        except Exception:
            return await _msg(
                interaction,
                (
                    "Couldn't confirm that **Stop Setup DMs** was saved. "
                    "Your previous opt-in may still be stored. Retry the setting. "
                    "Automatic Action Center DMs are not currently active."
                    if not enabled else
                    "Couldn't verify your opt-in. Future reminder delivery is not authorized. "
                    "Try again."
                ),
            )
        await _replace(
            interaction, embed=_embed(confirmed),
            view=MemberActionPreferencesView(self.owner_id, self.guild_id, confirmed),
        )

    @discord.ui.button(label="Enable Future Setup DMs", emoji="🔔", style=discord.ButtonStyle.primary, row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save(interaction, enabled=True)

    @discord.ui.button(label="Stop Setup DMs", emoji="🔕", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save(interaction, enabled=False)

    @discord.ui.button(label="Back to Action Inbox", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_center import open_action_center
        await open_action_center(interaction)


async def open_member_action_preferences(interaction: discord.Interaction) -> None:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if interaction.guild is None or member is None:
        return await _msg(interaction, "Open your reminder preferences in a server.")
    await _defer(interaction)
    try:
        state = await read_member_consent(interaction.guild.id, member.id)
    except Exception:
        return await _msg(
            interaction, "Reminder preferences couldn't be verified. Future delivery remains blocked.",
        )
    await _replace(
        interaction, embed=_embed(state),
        view=MemberActionPreferencesView(member.id, interaction.guild.id, state),
    )
