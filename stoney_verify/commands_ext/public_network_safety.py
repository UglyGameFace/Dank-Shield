from __future__ import annotations

"""Private, owner-only Safety Network UI. Advisory-only and off by default."""

import asyncio
from typing import Any

import discord

from .. import network_safety as network
from .public_setup_group import dank_group

_REGISTERED = False
_OWNER_ONLY = "Only the physical server owner can manage cross-server safety sharing."


def _owner(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    return bool(guild and int(getattr(interaction.user, "id", 0) or 0) ==
                int(getattr(guild, "owner_id", 0) or 0))


async def _deny(interaction: discord.Interaction) -> bool:
    if _owner(interaction):
        return False
    if interaction.response.is_done():
        await interaction.followup.send(_OWNER_ONLY, ephemeral=True)
    else:
        await interaction.response.send_message(_OWNER_ONLY, ephemeral=True)
    return True


async def _edit(interaction: discord.Interaction, view: discord.ui.View, embed: discord.Embed) -> None:
    kw = {"embed": embed, "view": view, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kw)
    else:
        await interaction.response.edit_message(**kw)


def _panel_embed(consent: dict[str, Any] | None) -> discord.Embed:
    e = discord.Embed(
        title="🛡️ Cross-Server Safety Network",
        description=(
            "Private, owner-controlled advisories about **the same Discord account** "
            "reported by other participating servers. **Off by default.**\n\n"
            "Receive: a private owner DM when an account joins with active reports.\n"
            "Share: other opted-in owners can see your server name and reasons.\n"
            "**Reports are not guilt, and no report triggers automatic punishment.**"
        ), color=discord.Color.blurple(),
    )
    enabled = bool(consent and consent.get("receive_enabled"))
    shared = bool(consent and consent.get("publish_enabled"))
    e.add_field(name="Receive private alerts", value="ON" if enabled else "OFF", inline=True)
    e.add_field(name="Publish own reports", value="ON" if shared else "OFF", inline=True)
    e.set_footer(text="Owner DMs may be blocked by Discord privacy settings. Historical AntiNuke entries are never auto-shared.")
    return e


async def _open_panel(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        consent = await asyncio.to_thread(network.get_network_consent, interaction.guild.id)
        await interaction.edit_original_response(
            embed=_panel_embed(consent), view=NetworkPanel(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        await interaction.edit_original_response(content="Safety Network preferences are unavailable. No sharing changed.")


class ShareConfirmModal(discord.ui.Modal, title="Confirm cross-server sharing"):
    confirm = discord.ui.TextInput(
        label="Enter SHARE to disclose future owner reports",
        placeholder="SHARE", max_length=8,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        if str(self.confirm).strip() != "SHARE":
            await interaction.response.send_message("Sharing remains OFF. Type SHARE exactly to opt in.", ephemeral=True)
            return
        await _set_flag(interaction, publish=True)


async def _set_flag(
    interaction: discord.Interaction, *, publish: bool | None = None,
    receive: bool | None = None,
) -> None:
    if await _deny(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    try:
        consent = await asyncio.to_thread(
            network.set_network_consent, guild.id, guild.owner_id, guild.name,
            publish=publish, receive=receive,
        )
        await interaction.edit_original_response(
            content="Saved. Sharing is voluntary; previously saved reports become visible only when publishing is enabled.",
            embed=_panel_embed(consent), view=NetworkPanel(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        await interaction.edit_original_response(content="Database did not confirm this change. Reopen and verify settings; do not assume sharing is enabled.")


class NetworkPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Receive alerts", style=discord.ButtonStyle.primary, emoji="🔔")
    async def receive(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        consent = await asyncio.to_thread(network.get_network_consent, interaction.guild.id)
        await _set_flag(interaction, receive=not bool(consent and consent.get("receive_enabled")))

    @discord.ui.button(label="Share incidents", style=discord.ButtonStyle.secondary, emoji="🔒")
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        consent = await asyncio.to_thread(network.get_network_consent, interaction.guild.id)
        if consent and consent.get("publish_enabled") is True:
            await _set_flag(interaction, publish=False)
        else:
            await interaction.response.send_modal(ShareConfirmModal())

    @discord.ui.button(label="Review account", style=discord.ButtonStyle.secondary, emoji="👤")
    async def account(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        await _edit(interaction, SubjectPickerView(), discord.Embed(
            title="Review an account", description="Select a current member, or enter a departed/banned account ID.",
            color=discord.Color.blurple(),
        ))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        await interaction.response.edit_message(content="Safety Network closed.", embed=None, view=None)


def _subject_embed(subject: int) -> discord.Embed:
    return discord.Embed(
        title="Safety Network • Account review",
        description=(f"Account: `{subject}`\nRecord this server's own evidence or review "
                     "advisories from participating servers. Only owner-attested claims are automatically available. "
                     "Do not confuse a report with verified identity or guilt."),
        color=discord.Color.blurple(),
    )


class SubjectIDModal(discord.ui.Modal, title="Enter a Discord account ID"):
    user_id = discord.ui.TextInput(label="Departed or banned account ID", min_length=15, max_length=22)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        try:
            subject = int(network._id(str(self.user_id)))
        except (ValueError, TypeError):
            await interaction.response.send_message("Enter an exact, valid Discord account ID.", ephemeral=True)
            return
        await _edit(interaction, SubjectActionsView(subject), _subject_embed(subject))


class SubjectUserSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Choose a current member", min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        selected = self.values[0] if self.values else None
        subject = int(getattr(selected, "id", 0) or 0)
        if interaction.guild.get_member(subject) is None:
            await interaction.response.send_message("Choose a current member or use the departed-account ID option.", ephemeral=True)
            return
        await _edit(interaction, SubjectActionsView(subject), _subject_embed(subject))


class SubjectPickerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.add_item(SubjectUserSelect())

    @discord.ui.button(label="Enter account ID", style=discord.ButtonStyle.secondary, row=1)
    async def by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await interaction.response.send_modal(SubjectIDModal())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            consent = await asyncio.to_thread(network.get_network_consent, interaction.guild.id)
            await _edit(interaction, NetworkPanel(), _panel_embed(consent))


class NewEventModal(discord.ui.Modal):
    def __init__(
        self, subject: int, category: str, *, action: str = "incident",
        parent_id: str | None = None,
    ) -> None:
        super().__init__(title=("Correct report" if action == "correction" else
                                "Withdraw report" if action == "withdrawal" else
                                "Record an owner report"), timeout=300)
        self.subject = subject
        self.category = category
        self.action = action
        self.parent_id = parent_id
        self.note = discord.ui.TextInput(
            label=("Why this report is withdrawn" if action == "withdrawal" else "Specific evidence and reason"),
            placeholder="Describe the incident without private messages or sensitive details",
            style=discord.TextStyle.paragraph,
            min_length=8, max_length=500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            report_id = await asyncio.to_thread(
                network.record_network_event,
                interaction.guild.id, interaction.guild.owner_id, self.subject,
                event_key=str(interaction.id), category=self.category,
                reason=str(self.note), action=self.action, parent_id=self.parent_id,
            )
            await interaction.edit_original_response(
                content=(f"Saved {self.action} record `{report_id}`. "
                         "This is owner-attested, not independently verified. "
                         "Sharing depends on this server's publication setting."),
                embed=_subject_embed(self.subject), view=SubjectActionsView(self.subject),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"No record confirmed. {type(exc).__name__}: review consent, original report ownership and database status."
            )


class CategorySelect(discord.ui.Select):
    def __init__(self, subject: int) -> None:
        self.subject = subject
        super().__init__(placeholder="Select the incident type",
                         options=[discord.SelectOption(label=x.title(), value=x) for x in network.CATEGORIES])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _deny(interaction):
            await interaction.response.send_modal(NewEventModal(self.subject, self.values[0]))


class CategoryPickerView(discord.ui.View):
    def __init__(self, subject: int) -> None:
        super().__init__(timeout=300)
        self.add_item(CategorySelect(subject))


class OriginalReportSelect(discord.ui.Select):
    def __init__(self, subject: int, reports: list[dict[str, Any]]) -> None:
        self.subject = subject
        self.reports = {str(x["id"]): x for x in reports[:25]}
        super().__init__(placeholder="Choose your server's report to amend",
                         options=[
                             discord.SelectOption(
                                 label=f"{str(x.get('category','other')).title()} • {str(x.get('occurred_at',''))[:10]}",
                                 description=str(x.get("reason", ""))[:90],
                                 value=str(x["id"]),
                             ) for x in reports[:25]
                         ])

    async def callback(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        report = self.reports.get(self.values[0])
        if report:
            await _edit(interaction,
                        AmendmentActionsView(self.subject, report),
                        _subject_embed(self.subject))


class OriginalReportsView(discord.ui.View):
    def __init__(self, subject: int, reports: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        if reports:
            self.add_item(OriginalReportSelect(subject, reports))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await _edit(interaction, SubjectPickerView(), discord.Embed(
                title="Account selection", description="Select a member again or enter their ID.",
            ))


class AmendmentActionsView(discord.ui.View):
    def __init__(self, subject: int, report: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.subject = subject
        self.report = report

    @discord.ui.button(label="Correct reason", style=discord.ButtonStyle.primary)
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await interaction.response.send_modal(NewEventModal(
                self.subject, str(self.report["category"]),
                action="correction", parent_id=str(self.report["id"])))

    @discord.ui.button(label="Withdraw report", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await interaction.response.send_modal(NewEventModal(
                self.subject, str(self.report["category"]),
                action="withdrawal", parent_id=str(self.report["id"])))


class SubjectActionsView(discord.ui.View):
    def __init__(self, subject: int) -> None:
        super().__init__(timeout=300)
        self.subject = subject

    @discord.ui.button(label="Add incident", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await _edit(interaction, CategoryPickerView(self.subject), _subject_embed(self.subject))

    @discord.ui.button(label="Review network", style=discord.ButtonStyle.secondary)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            rows = await network.reports_for_owner(interaction.guild, self.subject, interaction.client)
            detail = network.advisory_text(self.subject, rows) if rows else (
                "No shared active incident reports, or receiving is disabled. "
                "Only opted-in reporting servers can contribute.")
            embed = discord.Embed(title="Cross-server advisory history", description=detail[:3900])
            await interaction.edit_original_response(embed=embed, view=SubjectActionsView(self.subject),
                                                     allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.edit_original_response(
                content="Network reports unavailable; no negative finding is implied."
            )

    @discord.ui.button(label="Correct or withdraw", style=discord.ButtonStyle.secondary)
    async def amend(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _deny(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            rows = await asyncio.to_thread(network._load_subject_rows, self.subject)
            own = [r for r in rows if str(r.get("reporting_guild_id")) == str(interaction.guild.id)]
            active = network._reconcile_events(own)
            view = OriginalReportsView(self.subject, active)
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="Amend your server's incidents",
                    description=("Select an original report. Corrections and withdrawals preserve a dated history. "
                                 if active else "No active incidents from your server. This cannot change another server's reports."),
                ), view=view, allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            await interaction.edit_original_response(content="Own report history is unavailable. No change made.")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await _edit(interaction, SubjectPickerView(), discord.Embed(
                title="Account selection", description="Select a member again or enter their ID.",
            ))


@dank_group.command(
    name="safety-network",
    description="Owner-only shared incident advisories and privacy settings.",
)
async def safety_network(interaction: discord.Interaction) -> None:
    if await _deny(interaction):
        return
    await _open_panel(interaction)


async def _on_network_member_join(member: discord.Member) -> None:
    await network.alert_on_member_join(member, _BOT)


_BOT: Any = None


def register_public_network_safety(bot: Any, tree: Any) -> None:
    global _REGISTERED, _BOT
    _ = tree
    if _REGISTERED:
        return
    _BOT = bot
    bot.add_listener(_on_network_member_join, "on_member_join")
    _REGISTERED = True


__all__ = ["register_public_network_safety"]
