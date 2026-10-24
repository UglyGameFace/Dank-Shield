from __future__ import annotations

"""Member-owned, private Future Member Setup reminder preference controls."""

import asyncio
from typing import Mapping

import discord

from stoney_verify.action_member_consent import (
    MemberReminderConsent, read_member_consent, save_member_consent,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_action_builder import _defer, _msg, _replace


def _embed(consent: MemberReminderConsent, *, status: str | None = None) -> discord.Embed:
    enabled = consent.member_setup_dm
    e = discord.Embed(
        title="🔔 My Action Reminder Preferences",
        description=(
            "**Your choices apply only to this server.**\n\n"
            "**Automatic Member Setup DMs:** "
            + ("✅ You opted in" if enabled else "🔕 Off")
            + "\n\n"
            "If this server enables the automation, these DMs remind you about "
            "published Member Setup changes you haven't completed.\n\n"
            "**Important:** Your opt-in alone doesn't activate sending for the "
            "server. Verification reminders are separate and automatically "
            "cover unverified members when enabled by server staff."
        ),
        color=discord.Color.blurple() if enabled else discord.Color.light_grey(),
    )
    if status is not None:
        e.add_field(name="🛡️ Current Member Setup DM status", value=status, inline=False)
    e.set_footer(text="Private • Default off • Change your choice anytime")
    return e


# Display only allowlisted interpretations from the canonical, read-only
# readiness adapter. Never expose database exception text or another member's
# state, and never confuse "due in preview" with permission to send.
_STATUS_EXPLANATIONS = {
    "exempt": "Server owners and members with Administrator permission are exempt from Member Setup reminders.",
    "opted_out": "Future Setup DMs are disabled for you in this server.",
    "excluded": "This server's Action Builder draft does not include Member Setup reminders.",
    "complete": "No incomplete published Member Setup requirement is currently recorded.",
    "stale": "The published Member Setup revision or safety settings changed. Check again later.",
    "preview_due": "This requirement looks due in the latest safety preview; the server switch controls automatic delivery.",
    "waiting": "The initial wait or retry cooldown has not elapsed.",
    "quiet": "The server's configured quiet hours prevent delivery now.",
    "exhausted": "The per-revision reminder attempt limit has been reached.",
    "capped": "The server's daily reminder allowance has been reached.",
    "blocked": "A Member Setup or timing prerequisite has not been satisfied.",
    "unavailable": "The authoritative Member Setup information is unavailable.",
    "ledger_unavailable": "Private reminder attempt history could not be verified.",
}


async def _read_my_setup_dm_status(member: discord.Member) -> str:
    """One member, one guild, no claims, no sends and no global member scan."""
    from stoney_verify.member_setup_service import load_guild_setup_state
    from stoney_verify.action_delivery_readiness import inspect_member_setup_readiness

    state = await asyncio.wait_for(
        load_guild_setup_state(member.guild.id, refresh=True), timeout=8.0,
    )
    if not isinstance(state, Mapping):
        raise RuntimeError("Member Setup source unavailable")
    revision = state.get("current_revision")
    if type(revision) is not int or revision < 0:
        raise RuntimeError("Invalid published Member Setup revision")
    if revision == 0:
        return "No published Member Setup revision can be checked."
    result = await asyncio.wait_for(
        inspect_member_setup_readiness(member, expected_revision=revision),
        timeout=20.0,
    )
    explanation = _STATUS_EXPLANATIONS.get(result.status)
    if explanation is None:
        return "Eligibility could not be verified."
    message = f"**{result.status.replace('_', ' ').title()}** • {explanation}"
    if result.next_at is not None:
        message += f"\nNext policy check: <t:{int(result.next_at.timestamp())}:F>"
    return message + "\n\nEligibility is a preview, not a send authorization."


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

    @discord.ui.button(label="Check Setup DM Status", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or interaction.guild_id != member.guild.id:
            return await _msg(interaction, "Open your own reminder status inside this server.")
        await _defer(interaction)
        try:
            status = await _read_my_setup_dm_status(member)
            # Reread the member's own preference so panel buttons are current.
            consent = await read_member_consent(self.guild_id, self.owner_id)
        except Exception:
            return await _msg(interaction, "Setup DM status could not be verified. No reminder is authorized.")
        await _replace(
            interaction, embed=_embed(consent, status=status),
            view=MemberActionPreferencesView(self.owner_id, self.guild_id, consent),
        )

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
