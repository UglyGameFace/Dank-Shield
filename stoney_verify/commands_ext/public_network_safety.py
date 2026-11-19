from __future__ import annotations

"""Private, owner-only Safety Network UI. Advisory-only and off by default."""

import asyncio
from typing import Any

import discord

from .. import network_safety as network
from ..ui.picker import DankPickerView, DankUserSelect, chunk_choices, make_choice
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
        await interaction.response.defer(ephemeral=True)
        try:
            current = await asyncio.to_thread(network.get_network_consent, interaction.guild.id)
            if not current or str(current.get("owner_id")) != str(interaction.guild.owner_id):
                # A neutral registration allows private owner reports without
                # silently enabling cross-server receiving or publishing.
                await asyncio.to_thread(
                    network.set_network_consent,
                    interaction.guild.id, interaction.guild.owner_id, interaction.guild.name,
                    publish=False, receive=False,
                )
        except Exception:
            await interaction.edit_original_response(
                content="Safety Network database is unavailable; no sharing or report changed."
            )
            return
        await _edit(interaction, SubjectPickerView(), discord.Embed(
            title="Review an account", description=(
                "Select a current member, or enter a departed/banned account ID. "
                "Your own incident notes stay private unless you explicitly enable Share incidents."
            ),
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


async def _ignore_user_pick(interaction: discord.Interaction, user: object) -> None:
    return None


class SubjectUserSelect(DankUserSelect):
    """House member picker for choosing the account to review.

    The callback keeps the exact original flow (owner gate, current-
    member check, then the account review panel); the framework
    wrapper's own callback only forwards the picked user. The panel
    is gated by _deny (physical server owner), so the framework
    owner check stays open (author_id=0), as before.
    """

    def __init__(self) -> None:
        super().__init__(
            author_id=0,
            on_pick=_ignore_user_pick,
            placeholder="Choose a current member",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.custom_id = "dank_network_safety:subject_user:v1"

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


class CategoryPickerView(DankPickerView):
    """House picker for the incident type of a new owner report.

    Picking a type opens the same NewEventModal the raw category
    select opened, behind the same owner gate. Back/Close returns
    to the account review panel. The flow is gated by _deny
    (physical server owner), so the framework owner check stays
    open (author_id=0), as before.
    """

    def __init__(self, subject: int) -> None:
        self.subject = int(subject)

        async def on_pick(interaction: discord.Interaction, value: str) -> None:
            if not await _deny(interaction):
                await interaction.response.send_modal(NewEventModal(self.subject, value))

        async def on_home(interaction: discord.Interaction) -> None:
            await _edit(interaction, SubjectActionsView(self.subject), _subject_embed(self.subject))

        super().__init__(
            author_id=0,
            choices=[make_choice(x.title(), x) for x in network.CATEGORIES],
            on_pick=on_pick,
            custom_id="dank_network_safety:category:v1",
            placeholder="Select the incident type",
            title="Record an owner report",
            timeout=300,
            on_home=on_home,
            on_cancel=on_home,
            home_label="Back",
        )


def _amend_reports_embed(active: bool) -> discord.Embed:
    return discord.Embed(
        title="Amend your server's incidents",
        description=("Select an original report. Corrections and withdrawals preserve a dated history. "
                     if active else "No active incidents from your server. This cannot change another server's reports."),
    )


class _OriginalReportPickerView(DankPickerView):
    """Paged house picker for a server's own reports on one account.

    The raw select silently truncated the list to the first 25
    reports; this picker pages every report with chunk_choices so
    older reports stay reachable. Picking opens the same amendment
    panel the raw select opened, behind the same owner gate.
    Back/Close returns to the amend panel.
    """

    def __init__(self, subject: int, reports: list[dict[str, Any]], *, page: int = 0) -> None:
        self.subject = int(subject)
        self.reports = list(reports or [])
        self._reports_by_id = {str(x["id"]): x for x in self.reports}
        all_choices = [
            make_choice(
                f"{str(x.get('category','other')).title()} • {str(x.get('occurred_at',''))[:10]}",
                str(x["id"]),
                description=str(x.get("reason", ""))[:90],
            )
            for x in self.reports
        ]
        pages = chunk_choices(all_choices) or [[]]
        page = max(0, min(int(page), len(pages) - 1))

        async def on_pick(interaction: discord.Interaction, value: str) -> None:
            if await _deny(interaction):
                return
            report = self._reports_by_id.get(value)
            if report:
                await _edit(interaction,
                            AmendmentActionsView(self.subject, report),
                            _subject_embed(self.subject))

        async def on_home(interaction: discord.Interaction) -> None:
            await _edit(interaction,
                        OriginalReportsView(self.subject, self.reports),
                        _amend_reports_embed(bool(self.reports)))

        super().__init__(
            author_id=0,
            choices=pages[page],
            on_pick=on_pick,
            custom_id=(
                f"dank_network_safety:original_reports:v1:{page}"
                if len(pages) > 1 else "dank_network_safety:original_reports:v1"
            ),
            placeholder=(
                f"Choose your server's report to amend • Page {page + 1}/{len(pages)}"
                if len(pages) > 1 else "Choose your server's report to amend"
            ),
            title="Amend your server's incidents",
            timeout=300,
            on_home=on_home,
            on_cancel=on_home,
            home_label="Back",
        )
        self.page = page
        self.pages = len(pages)
        self._all_reports = self.reports
        if len(pages) > 1:
            self.add_item(_OriginalReportPageButton(delta=-1, disabled=page <= 0))
            self.add_item(_OriginalReportPageButton(delta=1, disabled=page >= len(pages) - 1))

    def _for_page(self, page: int) -> "_OriginalReportPickerView":
        return _OriginalReportPickerView(self.subject, self._all_reports, page=page)


class _OriginalReportPageButton(discord.ui.Button):
    def __init__(self, *, delta: int, disabled: bool) -> None:
        super().__init__(
            label="Previous" if delta < 0 else "Next",
            emoji="◀️" if delta < 0 else "▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "dank_network_safety:original_reports:prev" if delta < 0
                else "dank_network_safety:original_reports:next"
            ),
            row=2,
            disabled=bool(disabled),
        )
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, _OriginalReportPickerView):
            return
        page = max(0, min(view.page + self.delta, view.pages - 1))
        await _edit(interaction, view._for_page(page), _amend_reports_embed(bool(view.reports)))


class _OriginalReportOpenButton(discord.ui.Button):
    """Swaps the amend panel for the report picker on the same message."""

    def __init__(self, subject: int, reports: list[dict[str, Any]]) -> None:
        super().__init__(
            label="Choose your server's report to amend…",
            style=discord.ButtonStyle.primary,
            custom_id="dank_network_safety:original_reports:open:v1",
            row=0,
        )
        self.subject = int(subject)
        self.reports = list(reports or [])

    async def callback(self, interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        message = getattr(interaction, "message", None)
        embeds = getattr(message, "embeds", None) if message is not None else None
        await _edit(
            interaction,
            _OriginalReportPickerView(self.subject, self.reports),
            embeds[0] if embeds else _amend_reports_embed(bool(self.reports)),
        )


class OriginalReportsView(discord.ui.View):
    def __init__(self, subject: int, reports: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        if reports:
            self.add_item(_OriginalReportOpenButton(subject, reports))

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


class NetworkReportPages(discord.ui.View):
    """Show each available reason in a small owner-only page, not a giant list."""

    def __init__(self, subject: int, reports: list[dict[str, Any]], page: int = 0) -> None:
        super().__init__(timeout=300)
        self.subject = subject
        self.reports = reports
        self.page = max(0, min(page, max(0, (len(reports) - 1) // 4)))
        self.previous.disabled = self.page <= 0
        self.next.disabled = (self.page + 1) * 4 >= len(self.reports)

    def embed(self) -> discord.Embed:
        e = discord.Embed(
            title="Cross-server advisory history",
            description=(f"Exact account `{self.subject}` • Page {self.page + 1} of "
                         f"{max(1, (len(self.reports) + 3) // 4)}.\n"
                         "Owner-attested reports are not proof; withdrawn reports are excluded. "
                         "Network history may be limited to 40 current reports."),
            color=discord.Color.blurple(),
        )
        chunk = self.reports[self.page * 4:self.page * 4 + 4]
        if not chunk:
            e.add_field(name="No active shared incidents", value=(
                "No current opted-in sources reported this ID, or receiving is OFF. "
                "An empty report is not proof of a clean history."
            ), inline=False)
        for rec in chunk:
            label = discord.utils.escape_mentions(discord.utils.escape_markdown(
                str(rec.get("source_label") or "Participating server")[:70]))
            reason = discord.utils.escape_mentions(discord.utils.escape_markdown(
                str(rec.get("reason") or "No reason supplied")[:350]))
            date = str(rec.get("occurred_at") or "")[:10] or "unknown date"
            category = str(rec.get("category") or "other").title()[:28]
            e.add_field(name=f"{label} • {category} • {date}", value=reason, inline=False)
        e.set_footer(text="Read-only advisory. No automatic action against this account.")
        return e

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            view = NetworkReportPages(self.subject, self.reports, self.page - 1)
            await _edit(interaction, view, view.embed())

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            view = NetworkReportPages(self.subject, self.reports, self.page + 1)
            await _edit(interaction, view, view.embed())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _deny(interaction):
            await _edit(interaction, SubjectActionsView(self.subject), _subject_embed(self.subject))


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
            view = NetworkReportPages(self.subject, rows)
            await interaction.edit_original_response(
                embed=view.embed(), view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
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
                embed=_amend_reports_embed(bool(active)),
                view=view, allowed_mentions=discord.AllowedMentions.none(),
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
