"""Private recurring campaign controls for the canonical Embed Studio editor.

Loaded lazily by the editor, so existing one-shot scheduling remains unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import discord

from .embed_studio_core import StudioValidationError
from .embed_studio_schedules import safe_scheduled_project
from .embed_studio_campaigns import (
    campaigns_enabled, parse_campaign, enqueue, list_campaigns, cancel,
)
from .embed_studio_ui import (
    StudioSession, _StudioView, _staff, _say, _menu, _target_channel,
    _post_blockers, _home,
)


class StudioCampaignModal(discord.ui.Modal, title="Repeat This Announcement"):
    def __init__(self, session: StudioSession, *, preview: dict[str, Any],
                 channel_id: int, post_key: str, review: Any) -> None:
        super().__init__(timeout=300)
        self.session, self.preview = session, deepcopy(preview)
        self.channel_id, self.post_key, self.review = channel_id, post_key, review
        self.used = False
        self.cadence = discord.ui.TextInput(
            label="Repeat schedule", placeholder="daily or weekly Monday",
            max_length=25, required=True)
        self.timezone_name = discord.ui.TextInput(
            label="IANA timezone", placeholder="America/New_York",
            max_length=90, required=True)
        self.local_time = discord.ui.TextInput(
            label="Local time (24-hour HH:MM)", placeholder="16:20",
            max_length=5, required=True)
        self.occurrences = discord.ui.TextInput(
            label="Maximum sends (1-365)", placeholder="7",
            max_length=3, required=True)
        for item in (self.cadence, self.timezone_name, self.local_time, self.occurrences):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (not campaigns_enabled() or not _staff(interaction)
                or interaction.guild is None
                or int(interaction.guild.id) != self.session.guild_id
                or int(interaction.user.id) != self.session.owner_id):
            return await _say(interaction, "Recurring campaigns are disabled or staff access changed.")
        if self.used or self.review.sent:
            return await _say(interaction, "This preview has already been used.")
        self.used = True
        if (self.session.project != self.preview
                or self.session.channel_id != self.channel_id
                or self.session.post_key != self.post_key or self.post_key):
            return await _say(interaction, "Preview changed or is editing a post. Reopen Preview.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = _target_channel(interaction, self.session)
            if channel is None or _post_blockers(interaction, channel):
                return await _say(interaction, "Destination or staff permissions changed.")
            raw_count = str(self.occurrences.value or "").strip()
            if not raw_count.isascii() or not raw_count.isdecimal():
                raise StudioValidationError("Maximum sends must be a whole number.")
            schedule = parse_campaign(
                str(self.cadence.value or ""),
                str(self.timezone_name.value or "").strip(),
                str(self.local_time.value or "").strip(),
                int(raw_count))
            project = safe_scheduled_project(self.preview)
            from .commands_ext.public_embed_group import _require_safe_embed_action
            if not await _require_safe_embed_action(interaction, action="send public embed"):
                return
            if (self.session.project != self.preview
                    or self.session.channel_id != self.channel_id
                    or self.session.post_key != self.post_key):
                return await _say(interaction, "Preview changed. No recurring campaign was created.")
            await enqueue(self.session.guild_id, self.channel_id,
                          self.session.owner_id, project, schedule)
        except StudioValidationError as exc:
            return await _say(interaction, f"Cannot repeat this announcement: {exc}")
        except Exception:
            return await _say(interaction, "Campaign storage unavailable. Nothing was queued.")
        self.review.sent = True
        await _say(interaction, (
            f"✅ Recurring campaign created for <#{self.channel_id}>. "
            f"Up to {schedule['max_occurrences']} {schedule['frequency']} sends "
            f"at {schedule['local_clock'][:5]} {schedule['timezone']}. "
            "Review or stop it in More Options → Recurring Campaigns. "
            "No message was posted immediately."
        ))


class StudioCampaignSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, rows: list[dict[str, Any]]) -> None:
        self.session = session
        options = [
            discord.SelectOption(
                label=f"{row['frequency']} • #{str(row['channel_id'])[:18]} • {str(row['next_fire_at'])[:18]}",
                value=str(row["id"]),
            ) for row in rows
        ] or [discord.SelectOption(label="No active recurring campaigns", value="none")]
        super().__init__(placeholder="Select a campaign to stop", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "No active recurring campaigns.")
        await _menu(interaction, heading="Stop this recurring campaign?",
                    description="Future sends will stop; posts already published remain.",
                    view=StopStudioCampaignView(self.session, self.values[0]))


class StudioCampaignsMenu(_StudioView):
    def __init__(self, session: StudioSession, rows: list[dict[str, Any]]) -> None:
        super().__init__(session)
        self.add_item(StudioCampaignSelect(session, rows))

    @discord.ui.button(label="Back to Studio", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


class StopStudioCampaignView(_StudioView):
    def __init__(self, session: StudioSession, campaign_id: str) -> None:
        super().__init__(session)
        self.campaign_id = campaign_id
        self.used = False

    @discord.ui.button(label="Stop Future Sends", emoji="🛑", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This stop request has already been submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            stopped = await cancel(self.session.guild_id, self.campaign_id)
        except Exception:
            return await _say(interaction, "Could not confirm cancellation; check recurring campaigns.")
        await _say(interaction, "✅ Future campaign sends stopped." if stopped
                   else "Campaign is no longer active.")

    @discord.ui.button(label="Keep Campaign", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await recurring_campaigns(interaction, self.session)


async def recurring_campaigns(interaction: discord.Interaction,
                              session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        rows = await list_campaigns(session.guild_id)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    except Exception:
        return await _say(interaction, "Recurring campaigns are unavailable. No changes were made.")
    await _menu(interaction, heading="🔁 Recurring Campaigns",
                description="Up to 20 active campaigns per server. "
                            "Each send rechecks staff and channel permissions. "
                            "Missed occurrences are never posted late.",
                view=StudioCampaignsMenu(session, rows))
