from __future__ import annotations

"""UI-first ticket command centers for the compact public command surface.

This module deliberately invokes the already-registered canonical command objects
instead of reimplementing ticket behavior. That keeps claim/owner authorization,
lifecycle repair, transcript/delete safety, category governance, and existing
server compatibility owned by their current production modules.
"""

from typing import Any, Optional

import discord

from ..ui.current_channel import current_text_channel

from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from discord import app_commands

from stoney_verify.interaction_guard import run_guarded_interaction
from .common import _staff_check
from ..tickets_new.service import authorize_ticket_action


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except Exception:
        try:
            await interaction.followup.send(**payload)
        except Exception:
            pass


async def _replace(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    payload = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(**payload)
        elif interaction.message is not None:
            await interaction.response.edit_message(**payload)
        else:
            await interaction.response.send_message(**payload, ephemeral=True)
    except Exception:
        await _private(interaction, embed=embed, view=view)


async def _invoke(command: Any, interaction: discord.Interaction, /, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("The canonical ticket action is unavailable.")
    return await callback(interaction, *args, **kwargs)


async def _require_staff(interaction: discord.Interaction) -> bool:
    try:
        if _staff_check(interaction):
            return True
    except Exception:
        pass
    await _private(interaction, "❌ Staff only.")
    return False


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own ticket control panel to use these controls.")
        return False


def _text_channel(guild: Optional[discord.Guild], channel_id: int) -> Optional[discord.TextChannel]:
    if guild is None or int(channel_id or 0) <= 0:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


def _current_or_selected(interaction: discord.Interaction, selected_id: int = 0) -> Optional[discord.TextChannel]:
    selected = _text_channel(interaction.guild, selected_id)
    if selected is not None:
        return selected
    return interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None


def _ticket_center_embed(channel: Optional[discord.TextChannel]) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Current Ticket Center",
        description=(
            "Every normal ticket action lives here. Pick another ticket channel if needed, "
            "then choose an action. Existing claim-first rules, guild-owner authority, "
            "transcript safeguards, and lifecycle repair still apply."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Target ticket",
        value=(f"{channel.mention}\n`{channel.id}`" if channel else "No ticket channel selected."),
        inline=False,
    )
    embed.add_field(
        name="Fast actions",
        value=(
            "Info • Claim/Unclaim • Transfer • Priority • Close/Reopen • Transcript • "
            "Access • Rename • Lock/Unlock • Notes/Macros through the persistent ticket panel • Delete"
        ),
        inline=False,
    )
    embed.set_footer(text="/ticket • actions stay guarded at click time")
    return embed


def _ticket_action_name(command_name: str) -> str:
    return {
        "info": "view_info",
        "owner": "view_info",
        "access": "view_info",
        "add": "access",
        "remove": "access",
        "rename": "rename",
        "lock": "lock",
        "unlock": "unlock",
    }.get(command_name, command_name)


async def _authorize_ticket_command(
    interaction: discord.Interaction,
    *,
    command_name: str,
    channel: discord.TextChannel,
) -> bool:
    if command_name == "claim":
        return True
    try:
        from . import ticket_admin as legacy

        row = await legacy._refresh_ticket_row(channel)
    except Exception:
        row = None
    if not isinstance(row, dict):
        await _private(interaction, "❌ That channel is not a recognized Dank Shield ticket.")
        return False
    try:
        decision = await authorize_ticket_action(
            channel_id=int(channel.id),
            actor=interaction.user,
            action=_ticket_action_name(command_name),
            row=row,
        )
    except Exception as exc:
        await _private(
            interaction,
            f"❌ Ticket authorization is temporarily unavailable. Nothing changed. `{type(exc).__name__}`",
        )
        return False
    if decision.allowed:
        return True
    await _private(interaction, f"❌ {decision.message}")
    return False


async def _run_ticket_command(
    interaction: discord.Interaction,
    *,
    name: str,
    channel: discord.TextChannel,
    **kwargs: Any,
) -> None:
    async def action() -> None:
        await _run_ticket_command_action(
            interaction,
            name=name,
            channel=channel,
            **kwargs,
        )

    await run_guarded_interaction(
        interaction,
        action,
        defer=True,
        ephemeral=True,
        action_name=f"ticket.center.{name}",
        error_title="❌ Ticket action stopped unexpectedly",
        error_guidance=(
            "Refresh or reopen the Ticket Operations Center and inspect the ticket's current state "
            "before retrying. Use the Error ID in `/dank diagnostics` if this keeps happening."
        ),
    )


async def _run_ticket_command_action(
    interaction: discord.Interaction,
    *,
    name: str,
    channel: discord.TextChannel,
    **kwargs: Any,
) -> None:
    if not await _require_staff(interaction):
        return
    if not await _authorize_ticket_command(interaction, command_name=name, channel=channel):
        return
    from .public_ticket_group import ticket_group

    command = ticket_group.get_command(name)
    if command is None:
        await _private(interaction, f"❌ Ticket action **{name}** is unavailable.")
        return
    try:
        await _invoke(command, interaction, channel=channel, **kwargs)
    except TypeError:
        # Commands with a required positional value are invoked by their dedicated
        # picker/modal flows and should never land here.
        await _private(interaction, f"❌ Ticket action **{name}** needs more information.")


class TicketChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, parent: "TicketActionCenterView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Optional: choose a different ticket channel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:ticket:center:channel:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        channel = interaction.guild.get_channel(int(getattr(selected, "id", 0) or 0))
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose a text channel from this server.")
        self.parent_view.channel_id = int(channel.id)
        await interaction.response.edit_message(
            embed=_ticket_center_embed(channel),
            view=self.parent_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TicketActionSelect(discord.ui.Select):
    def __init__(self, parent: "TicketActionCenterView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Ticket Info", value="info", emoji="ℹ️", description="Status, owner, assignment and ticket details"),
            discord.SelectOption(label="Claim", value="claim", emoji="🎯", description="Claim this active ticket"),
            discord.SelectOption(label="Unclaim", value="unclaim", emoji="↩️", description="Remove the current assignment"),
            discord.SelectOption(label="Transfer", value="transfer", emoji="🔁", description="Choose another staff member"),
            discord.SelectOption(label="Set Priority", value="priority", emoji="🚦", description="Low, Medium, High, or Urgent"),
            discord.SelectOption(label="Close", value="close", emoji="🔒", description="Close/archive without deleting"),
            discord.SelectOption(label="Reopen", value="reopen", emoji="🔓", description="Restore a closed ticket"),
            discord.SelectOption(label="Post Transcript", value="transcript", emoji="🧾", description="Generate/post a transcript now"),
            discord.SelectOption(label="Add Member Access", value="add", emoji="➕", description="Choose a member to add"),
            discord.SelectOption(label="Remove Member Access", value="remove", emoji="➖", description="Choose a member to remove"),
            discord.SelectOption(label="Rename", value="rename", emoji="✏️", description="Only where canonical numbering allows it"),
            discord.SelectOption(label="Lock Owner Replies", value="lock", emoji="🔐", description="Keep ticket visible but stop owner replies"),
            discord.SelectOption(label="Unlock Owner Replies", value="unlock", emoji="🔓", description="Restore owner replies on an open ticket"),
            discord.SelectOption(label="Show Owner", value="owner", emoji="👑", description="Resolve the ticket owner"),
            discord.SelectOption(label="Show Access", value="access", emoji="👥", description="Inspect explicit ticket access"),
            discord.SelectOption(label="Delete Ticket", value="delete", emoji="🗑️", description="Transcript-first guarded deletion"),
        ]
        super().__init__(
            placeholder="Choose a ticket action…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank:ticket:center:action:v1",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = str(self.values[0] if self.values else "").strip()
        channel = self.parent_view.target(interaction)
        if channel is None:
            return await _private(interaction, "❌ Run `/ticket` inside a ticket or choose a ticket channel first.")
        if action in {"info", "claim", "unclaim", "lock", "unlock", "owner", "access"}:
            return await _run_ticket_command(interaction, name=action, channel=channel)
        if action in {"transfer", "add", "remove"}:
            view = TicketMemberActionView(
                owner_id=self.parent_view.owner_id,
                channel_id=channel.id,
                action=action,
            )
            return await _replace(interaction, embed=view.embed(interaction.guild), view=view)
        if action == "priority":
            view = TicketPriorityView(self.parent_view.owner_id, channel.id)
            return await _replace(interaction, embed=view.embed(interaction.guild), view=view)
        if action in {"close", "reopen", "transcript"}:
            return await interaction.response.send_modal(
                TicketReasonModal(
                    owner_id=self.parent_view.owner_id,
                    channel_id=channel.id,
                    action=action,
                )
            )
        if action == "rename":
            return await interaction.response.send_modal(
                TicketRenameModal(self.parent_view.owner_id, channel.id)
            )
        if action == "delete":
            return await interaction.response.send_modal(
                TicketDeleteModal(self.parent_view.owner_id, channel.id)
            )
        await _private(interaction, "❌ That ticket action is unavailable.")


class TicketActionCenterView(_OwnedView):
    def __init__(self, owner_id: int, *, channel_id: int = 0) -> None:
        self.channel_id = int(channel_id or 0)
        super().__init__(owner_id)
        self.add_item(TicketChannelPicker(self))
        self.add_item(TicketActionSelect(self))

    def target(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        return _current_or_selected(interaction, self.channel_id)

    @discord.ui.button(label="Staff Panel", emoji="🛠️", style=discord.ButtonStyle.secondary, row=2)
    async def staff_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.target(interaction)
        if channel is None:
            return await _private(interaction, "❌ Select a ticket channel first.")
        from .public_ticket_intake_group import intake_post_actions
        await _invoke(intake_post_actions, interaction, channel=channel)

    @discord.ui.button(label="Owner History", emoji="🧾", style=discord.ButtonStyle.secondary, row=2)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.target(interaction)
        if channel is None:
            return await _private(interaction, "❌ Select a ticket channel first.")
        from .public_tickets_group import tickets_history
        await _invoke(tickets_history, interaction, channel=channel)

    @discord.ui.button(label="Activity", emoji="📜", style=discord.ButtonStyle.secondary, row=2)
    async def activity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.target(interaction)
        if channel is None:
            return await _private(interaction, "❌ Select a ticket channel first.")
        from .public_tickets_group import tickets_activity
        await _invoke(tickets_activity, interaction, channel=channel)

    @discord.ui.button(label="Ticket Operations", emoji="🎛️", style=discord.ButtonStyle.primary, row=2)
    async def operations(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=_operations_embed(),
            view=TicketOperationsView(self.owner_id),
        )

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=3)
    async def use_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = current_text_channel(interaction, require_bot_send=False)
        if channel is None:
            return await _private(interaction, "❌ Open Ticket Center inside the target ticket text channel.")
        self.channel_id = channel.id
        await interaction.response.edit_message(
            embed=_ticket_center_embed(channel), view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.target(interaction)
        await interaction.response.edit_message(
            embed=_ticket_center_embed(channel),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TicketMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent: "TicketMemberActionView") -> None:
        self.parent_view = parent
        labels = {"transfer": "staff member", "add": "member to add", "remove": "member to remove"}
        super().__init__(
            placeholder=f"Choose the {labels.get(parent.action, 'member')}…",
            min_values=1,
            max_values=1,
            custom_id=f"dank:ticket:center:member:{parent.action}:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        channel = _text_channel(interaction.guild, self.parent_view.channel_id)
        if not isinstance(member, discord.Member) or channel is None:
            return await _private(interaction, "❌ The selected member or ticket is no longer available.")
        if not await _require_staff(interaction):
            return
        if not await _authorize_ticket_command(
            interaction,
            command_name=self.parent_view.action,
            channel=channel,
        ):
            return
        from .public_ticket_group import ticket_group
        command = ticket_group.get_command(self.parent_view.action)
        if command is None:
            return await _private(interaction, "❌ That ticket action is unavailable.")
        await _invoke(command, interaction, member=member, channel=channel)


class TicketMemberActionView(_OwnedView):
    def __init__(self, *, owner_id: int, channel_id: int, action: str) -> None:
        self.channel_id = int(channel_id)
        self.action = str(action)
        super().__init__(owner_id)
        self.add_item(TicketMemberSelect(self))

    def embed(self, guild: Optional[discord.Guild]) -> discord.Embed:
        channel = _text_channel(guild, self.channel_id)
        label = {"transfer": "Transfer Ticket", "add": "Add Ticket Access", "remove": "Remove Ticket Access"}.get(self.action, "Ticket Member Action")
        return discord.Embed(
            title=f"👤 {label}",
            description=f"Target: {channel.mention if channel else '`missing`'}\nChoose one member. All normal ticket guards are re-checked.",
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _text_channel(interaction.guild, self.channel_id)
        view = TicketActionCenterView(self.owner_id, channel_id=self.channel_id)
        await _replace(interaction, embed=_ticket_center_embed(channel), view=view)


class TicketPrioritySelect(discord.ui.Select):
    def __init__(self, parent: "TicketPriorityView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose ticket priority…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Low", value="low", emoji="🟢"),
                discord.SelectOption(label="Medium", value="medium", emoji="🔵"),
                discord.SelectOption(label="High", value="high", emoji="🟠"),
                discord.SelectOption(label="Urgent", value="urgent", emoji="🔴"),
            ],
            custom_id="dank:ticket:center:priority:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction.guild, self.parent_view.channel_id)
        if channel is None:
            return await _private(interaction, "❌ That ticket channel no longer exists.")
        if not await _require_staff(interaction):
            return
        if not await _authorize_ticket_command(interaction, command_name="priority", channel=channel):
            return
        from .public_ticket_group import ticket_group
        command = ticket_group.get_command("priority")
        if command is None:
            return await _private(interaction, "❌ Ticket priority is unavailable.")
        value = str(self.values[0])
        choice = app_commands.Choice(name=value.title(), value=value)
        await _invoke(command, interaction, priority=choice, channel=channel)


class TicketPriorityView(_OwnedView):
    def __init__(self, owner_id: int, channel_id: int) -> None:
        self.channel_id = int(channel_id)
        super().__init__(owner_id)
        self.add_item(TicketPrioritySelect(self))

    def embed(self, guild: Optional[discord.Guild]) -> discord.Embed:
        channel = _text_channel(guild, self.channel_id)
        return discord.Embed(
            title="🚦 Ticket Priority",
            description=f"Target: {channel.mention if channel else '`missing`'}\nChoose the new priority.",
            color=discord.Color.blurple(),
        )


class TicketReasonModal(discord.ui.Modal):
    def __init__(self, *, owner_id: int, channel_id: int, action: str) -> None:
        super().__init__(title=f"Ticket {action.title()}", timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id)
        self.action = str(action)
        self.reason = discord.ui.TextInput(
            label="Reason (optional)",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        channel = _text_channel(interaction.guild, self.channel_id)
        if channel is None:
            return await _private(interaction, "❌ That ticket channel no longer exists.")
        await _run_ticket_command(
            interaction,
            name=self.action,
            channel=channel,
            reason=str(self.reason.value or "").strip() or None,
        )


class TicketRenameModal(discord.ui.Modal, title="Rename Ticket"):
    name_input = discord.ui.TextInput(label="New channel name", max_length=100)

    def __init__(self, owner_id: int, channel_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        channel = _text_channel(interaction.guild, self.channel_id)
        if channel is None:
            return await _private(interaction, "❌ That ticket channel no longer exists.")
        if not await _require_staff(interaction):
            return
        if not await _authorize_ticket_command(interaction, command_name="rename", channel=channel):
            return
        from .public_ticket_group import ticket_group
        command = ticket_group.get_command("rename")
        if command is None:
            return await _private(interaction, "❌ Ticket rename is unavailable.")
        await _invoke(command, interaction, name=str(self.name_input.value), channel=channel)


class TicketDeleteModal(discord.ui.Modal, title="Delete Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why is this ticket being deleted?",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )
    force = discord.ui.TextInput(
        label="Force open delete? Type YES only for emergency",
        placeholder="NO",
        required=False,
        max_length=3,
    )

    def __init__(self, owner_id: int, channel_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        channel = _text_channel(interaction.guild, self.channel_id)
        if channel is None:
            return await _private(interaction, "❌ That ticket channel no longer exists.")
        force = str(self.force.value or "").strip().upper() == "YES"
        reason = str(self.reason.value or "").strip() or None
        if force and not reason:
            return await _private(interaction, "❌ Emergency open-ticket deletion requires a reason.")
        await _run_ticket_command(
            interaction,
            name="delete",
            channel=channel,
            reason=reason,
            force_open_delete=force,
        )


def _operations_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎛️ Ticket Operations Center",
        description=(
            "Queues, history, current-ticket controls, public panel setup, routing tests, "
            "and custom category administration are all here. The old separate ticket "
            "command families are implementation details, not something staff must memorize."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Queues", value="Active • Unassigned • Mine • Recently Closed • Overdue", inline=False)
    embed.add_field(name="Lookup", value="Find by ticket number • User history • Current ticket history/activity", inline=False)
    embed.add_field(name="Setup", value="Public panel • Intake/routing • Managed/custom categories", inline=False)
    embed.set_footer(text="/tickets • one staff doorway")
    return embed


async def _run_queue(interaction: discord.Interaction, name: str) -> None:
    if not await _require_staff(interaction):
        return
    from .public_tickets_group import tickets_group
    command = tickets_group.get_command(name)
    if command is None:
        return await _private(interaction, f"❌ Ticket view **{name}** is unavailable.")
    await _invoke(command, interaction)


class TicketHistoryUserSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="User ticket history…",
            min_values=1,
            max_values=1,
            custom_id="dank:tickets:center:user_history:v1",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_staff(interaction):
            return
        selected = self.values[0] if self.values else None
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        if not isinstance(member, discord.Member):
            return await _private(interaction, "❌ That user is not currently a server member.")
        from .public_tickets_group import tickets_group
        command = tickets_group.get_command("for-user")
        if command is None:
            return await _private(interaction, "❌ User ticket history is unavailable.")
        await _invoke(command, interaction, member=member)


class TicketOperationsView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(TicketHistoryUserSelect())

    @discord.ui.button(label="Active", emoji="🎫", style=discord.ButtonStyle.primary, row=0)
    async def active(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _run_queue(interaction, "open")

    @discord.ui.button(label="Unassigned", emoji="📭", style=discord.ButtonStyle.secondary, row=0)
    async def unassigned(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _run_queue(interaction, "unassigned")

    @discord.ui.button(label="Mine", emoji="🧑‍💼", style=discord.ButtonStyle.secondary, row=0)
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _run_queue(interaction, "mine")

    @discord.ui.button(label="Recent Closed", emoji="🗃️", style=discord.ButtonStyle.secondary, row=0)
    async def recent(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _run_queue(interaction, "recent-closed")

    @discord.ui.button(label="Overdue", emoji="⏰", style=discord.ButtonStyle.secondary, row=0)
    async def overdue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _run_queue(interaction, "overdue")

    @discord.ui.button(label="Find Ticket", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def find_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(TicketFindModal(self.owner_id))

    @discord.ui.button(label="Current Ticket", emoji="🛠️", style=discord.ButtonStyle.primary, row=1)
    async def current(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        view = TicketActionCenterView(self.owner_id, channel_id=int(getattr(channel, "id", 0) or 0))
        await _replace(interaction, embed=_ticket_center_embed(channel), view=view)

    @discord.ui.button(label="Public Panel", emoji="📌", style=discord.ButtonStyle.secondary, row=1)
    async def panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TicketPanelToolsView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)

    @discord.ui.button(label="Intake & Routing", emoji="🧭", style=discord.ButtonStyle.secondary, row=1)
    async def intake(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TicketIntakeToolsView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)

    @discord.ui.button(label="Categories", emoji="🗂️", style=discord.ButtonStyle.secondary, row=1)
    async def categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TicketCategoryToolsView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)

    @discord.ui.button(label="Staff Alerts", emoji="🔔", style=discord.ButtonStyle.primary, row=3)
    async def staff_alerts(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_ticket_notification_manager(interaction):
            return
        await interaction.response.defer()
        view = await TicketNotificationSettingsView.create(self.owner_id, interaction.guild)
        await _replace(interaction, embed=await view.embed(interaction.guild), view=view)


class TicketFindModal(discord.ui.Modal, title="Find Ticket"):
    ticket_number = discord.ui.TextInput(label="Ticket number", placeholder="218", max_length=10)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can use it.")
        try:
            number = int(str(self.ticket_number.value).strip().lstrip("#"))
        except Exception:
            return await _private(interaction, "❌ Enter a valid ticket number.")
        from .public_tickets_group import tickets_group
        command = tickets_group.get_command("find")
        if command is None:
            return await _private(interaction, "❌ Ticket lookup is unavailable.")
        await _invoke(command, interaction, ticket_number=number)



async def _require_ticket_notification_manager(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if (
        interaction.guild is not None
        and isinstance(member, discord.Member)
        and (member.guild_permissions.manage_guild or member.guild_permissions.administrator)
    ):
        return True
    await _private(interaction, "❌ Manage Server permission is required to configure staff ticket pings.")
    return False


class TicketNotificationCategorySelect(discord.ui.Select):
    def __init__(self, parent: "TicketNotificationSettingsView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="All tickets (default)", value="*", description="Fallback staff roles"),
        ]
        for slug, name in parent.categories[:23]:
            options.append(discord.SelectOption(label=name[:100], value=slug))
        options.append(discord.SelectOption(label="Other custom category…", value=":custom:", emoji="✏️"))
        super().__init__(
            placeholder="Choose a category before selecting its staff roles…",
            min_values=1, max_values=1, options=options,
            custom_id="dank:tickets:notifications:category:v1", row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_ticket_notification_manager(interaction):
            return
        value = str(self.values[0])
        if value == ":custom:":
            return await interaction.response.send_modal(
                TicketNotificationCustomCategoryModal(self.parent_view)
            )
        self.parent_view.category = value
        await interaction.response.defer()
        await _replace(
            interaction,
            embed=await self.parent_view.embed(interaction.guild),
            view=self.parent_view,
        )


class TicketNotificationCustomCategoryModal(discord.ui.Modal, title="Custom Ticket Alert Category"):
    slug = discord.ui.TextInput(
        label="Existing ticket category slug", placeholder="account_access", max_length=80,
    )

    def __init__(self, parent: "TicketNotificationSettingsView") -> None:
        super().__init__(timeout=600)
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_ticket_notification_manager(interaction):
            return
        from ..tickets_new.open_notifications import category_key
        self.parent_view.category = category_key(self.slug.value)
        await interaction.response.defer(ephemeral=True)
        await _private(
            interaction,
            f"Selected category **{self.parent_view.category}**. Choose its staff roles in the Staff Alerts panel.",
        )


class TicketNotificationRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "TicketNotificationSettingsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose up to 5 staff roles to ping…",
            min_values=1, max_values=5,
            custom_id="dank:tickets:notifications:roles:v1", row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_ticket_notification_manager(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        selected = [
            guild.get_role(int(value.id))
            for value in self.values
            if int(getattr(value, "id", 0) or 0) > 0
        ]
        roles = [role for role in selected if isinstance(role, discord.Role) and not role.is_default()]
        if not roles:
            return await _private(interaction, "❌ Choose an existing staff role, not @everyone.")
        await interaction.response.defer()
        from ..tickets_new.open_notifications import save_settings
        patch = (
            {"role_ids": [role.id for role in roles]}
            if self.parent_view.category == "*"
            else {"category_key": self.parent_view.category, "category_role_ids": [role.id for role in roles]}
        )
        try:
            await save_settings(guild.id, patch)
        except Exception as exc:
            return await _private(interaction, f"❌ Settings were not saved: {type(exc).__name__}. Reopen Staff Alerts.")
        await _replace(interaction, embed=await self.parent_view.embed(guild), view=self.parent_view)


async def _save_ticket_staff_alert_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    parent: "TicketNotificationSettingsView",
) -> None:
    if not await _require_ticket_notification_manager(interaction):
        return
    guild = interaction.guild
    if guild is None or not isinstance(guild.get_channel(int(channel.id)), discord.TextChannel):
        return await _private(interaction, "❌ Choose a server text channel.")
    from ..tickets_new.open_notifications import _private_for_staff
    if not _private_for_staff(channel):
        return await _private(
            interaction,
            "❌ Staff alert channels must hide View Channel from @everyone. "
            "Open this manager in a private staff channel or choose another.",
        )
    me = guild.me
    perms = channel.permissions_for(me) if me else None
    if perms is None or not (perms.view_channel and perms.send_messages):
        return await _private(interaction, "❌ Dank Shield needs View Channel and Send Messages there.")
    await interaction.response.defer()
    from ..workers.ticket_automation_worker import upsert_ticket_automation_settings
    if not await upsert_ticket_automation_settings(
        guild.id, {"staff_alert_channel_id": str(channel.id)},
    ):
        return await _private(interaction, "❌ Alert channel was not saved. Check database availability.")
    await _replace(interaction, embed=await parent.embed(guild), view=parent)


class TicketNotificationChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "TicketNotificationSettingsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Optional: private staff alert channel…",
            min_values=1, max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:tickets:notifications:channel:v1", row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = guild.get_channel(int(self.values[0].id)) if guild and self.values else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose a text channel in this server.")
        await _save_ticket_staff_alert_channel(interaction, channel, self.parent_view)



class TicketNotificationSettingsView(_OwnedView):
    def __init__(self, owner_id: int, categories: list[tuple[str, str]]) -> None:
        super().__init__(owner_id)
        self.category = "*"
        self.categories = categories
        self.add_item(TicketNotificationCategorySelect(self))
        self.add_item(TicketNotificationRoleSelect(self))
        self.add_item(TicketNotificationChannelSelect(self))

    @classmethod
    async def create(
        cls, owner_id: int, guild: discord.Guild,
    ) -> "TicketNotificationSettingsView":
        from .public_ticket_panel_clean import _load_rows, _row_slug, _row_name
        from ..tickets_new.open_notifications import category_key
        try:
            rows, _warning = await _load_rows(guild)
        except Exception:
            rows = []
        seen: set[str] = set()
        categories: list[tuple[str, str]] = []
        for row in rows:
            slug = category_key(_row_slug(row))
            if slug not in seen:
                seen.add(slug)
                categories.append((slug, _row_name(row)))
        return cls(owner_id, categories)

    async def embed(self, guild: discord.Guild) -> discord.Embed:
        from ..tickets_new.open_notifications import load_settings, selected_role_ids
        from ..workers.ticket_automation_worker import get_ticket_automation_settings
        settings, available = await load_settings(guild.id)
        auto = await get_ticket_automation_settings(guild.id)
        defaults = []
        from .public_ticket_panel_clean import _staff_role
        staff = await _staff_role(guild)
        if staff is not None:
            defaults.append(int(staff.id))
        effective = selected_role_ids(settings, self.category, defaults)
        role_mentions = [f"<@&{role_id}>" for role_id in effective if guild.get_role(role_id)]
        name = (
            "All tickets (default)"
            if self.category == "*"
            else next((label for slug, label in self.categories if slug == self.category), self.category)
        )
        destination = int(auto.get("staff_alert_channel_id") or 0)
        embed = discord.Embed(
            title="🔔 New Ticket Staff Alerts",
            description=(
                "Each new ticket can ping the selected staff roles **once**. "
                "Opening an existing ticket and ghost/test tickets do not re-ping.\n"
                "Choose a category, then roles; choose a private alert channel or leave it unset "
                "to ping inside the ticket. Independent of the ticket automation worker."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Enabled",
            value="Yes" if settings["enabled"] else "No",
            inline=True,
        )
        embed.add_field(name="Editing", value=name, inline=True)
        embed.add_field(
            name="Roles for selection",
            value=" ".join(role_mentions) or ("Muted for this category" if self.category != "*" else "No staff role configured"),
            inline=False,
        )
        embed.add_field(
            name="Alert destination",
            value=f"<#{destination}>" if destination else "Each private ticket",
            inline=False,
        )
        if not available:
            embed.add_field(
                name="Storage unavailable",
                value="Notifications are safely paused until per-server settings can be read. Changes cannot be saved.",
                inline=False,
            )
        embed.set_footer(text="Manage Server permission required • @everyone never pinged")
        return embed

    async def _change(self, interaction: discord.Interaction, patch: dict[str, Any]) -> None:
        if not await _require_ticket_notification_manager(interaction):
            return
        await interaction.response.defer()
        from ..tickets_new.open_notifications import save_settings
        try:
            await save_settings(interaction.guild.id, patch)
        except Exception as exc:
            return await _private(interaction, f"❌ Staff alert settings were not saved: {type(exc).__name__}.")
        await _replace(interaction, embed=await self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=4)
    async def use_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = current_text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Open Staff Alerts inside a private staff text channel.")
        await _save_ticket_staff_alert_channel(interaction, channel, self)

    @discord.ui.button(label="Enable", emoji="🔔", style=discord.ButtonStyle.success, row=3)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change(interaction, {"enabled": True})

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.secondary, row=3)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change(interaction, {"enabled": False})

    @discord.ui.button(label="Reset Roles", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
    async def reset_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        patch = (
            {"role_ids": []}
            if self.category == "*"
            else {"category_key": self.category, "remove_category": True}
        )
        await self._change(interaction, patch)

    @discord.ui.button(label="Clear Channel", emoji="🧹", style=discord.ButtonStyle.secondary, row=3)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_ticket_notification_manager(interaction):
            return
        await interaction.response.defer()
        from ..workers.ticket_automation_worker import upsert_ticket_automation_settings
        if not await upsert_ticket_automation_settings(interaction.guild.id, {"staff_alert_channel_id": None}):
            return await _private(interaction, "❌ Staff alert channel could not be cleared.")
        await _replace(interaction, embed=await self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _replace(interaction, embed=_operations_embed(), view=TicketOperationsView(self.owner_id))


class TicketTopicCleanupConfirmView(_OwnedView):
    """Second-step confirmation for one explicitly previewed, bounded batch."""

    def __init__(self, owner_id: int, guild_id: int, channel_ids: tuple[int, ...]) -> None:
        super().__init__(owner_id, timeout=300)
        self.guild_id = int(guild_id)
        self.channel_ids = channel_ids
        self.used = False

    @discord.ui.button(label="Clean Verified Topics", emoji="🧹", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.used:
            return await _private(interaction, "This topic repair was already submitted.")
        guild = interaction.guild
        member = interaction.user
        if not await _require_staff(interaction):
            return
        if (
            guild is None or int(guild.id) != self.guild_id
            or not isinstance(member, discord.Member)
            or not (member.guild_permissions.manage_guild or member.guild_permissions.administrator)
        ):
            return await _private(interaction, "❌ Only a server manager can repair ticket topics.")
        # Recheck after awaited authorization: two quick clicks must not apply
        # the same batch concurrently or report a cancelled active repair.
        if self.used:
            return await _private(interaction, "This repair was already submitted or cancelled.")
        self.used = True
        button.disabled = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        from ..tickets_new.topic_repair import repair_verified_topics

        result = await repair_verified_topics(guild, self.channel_ids)
        await _private(
            interaction,
            "Ticket topic repair finished. "
            f"Cleaned: **{result['cleaned']}** • "
            f"Skipped (not safely verified): **{result['skipped']}** • "
            f"Failed: **{result['failed']}**. "
            "Nothing else was modified.",
        )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.used:
            return await _private(interaction, "This cleanup was already submitted or cancelled.")
        self.used = True
        for item in self.children:
            item.disabled = True
        await _private(interaction, "Cancelled. No ticket topics were changed.")


class TicketPanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "TicketPanelToolsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Optional: choose where to post the public ticket panel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:tickets:panel:channel:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        channel = interaction.guild.get_channel(int(getattr(selected, "id", 0) or 0))
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose a text channel.")
        self.parent_view.channel_id = int(channel.id)
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class TicketPanelToolsView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        self.channel_id = 0
        super().__init__(owner_id)
        self.add_item(TicketPanelChannelSelect(self))

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title="📌 Public Ticket Panel",
            description=(
                "Post or repair the user-facing **Create Ticket** panel. Choose a channel "
                "above or leave it unset to use the saved panel/support channel."
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=1)
    async def use_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        channel = current_text_channel(interaction, require_bot_embed=True)
        if channel is None:
            return await _private(interaction, "❌ Open Public Ticket Panel in a text channel "
                                  "where Dank Shield can post embeds, or use the picker.")
        self.channel_id = channel.id
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Post / Update Panel", emoji="🎫", style=discord.ButtonStyle.primary, row=1)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_ticket_panel_clean import _post_panel
        await _post_panel(interaction, _text_channel(interaction.guild, self.channel_id))

    @discord.ui.button(label="Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_ticket_panel_clean import _send_health
        await _send_health(interaction)

    @discord.ui.button(label="Review Old Topics", emoji="🔎", style=discord.ButtonStyle.secondary, row=2)
    async def review_topics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        guild = interaction.guild
        member = interaction.user
        if (
            guild is None or not isinstance(member, discord.Member)
            or not (member.guild_permissions.manage_guild or member.guild_permissions.administrator)
        ):
            return await _private(interaction, "❌ Only a server manager can review topic cleanup.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        from ..tickets_new.topic_repair import preview_legacy_topics

        report = await preview_legacy_topics(guild)
        count = len(report.eligible_ids)
        summary = (
            f"**{count}** ticket topics verified for safe cleanup (max 10 per batch). "
            f"Checked **{report.candidates_checked}** legacy topics; "
            f"skipped **{report.skipped}** due to missing or conflicting DB records. "
            + ("More channels remain for another preview. " if report.remaining_unchecked else "")
        )
        if count == 0:
            return await _private(
                interaction,
                summary + "No changes were made. Check the saved ticket categories if nothing was scanned.",
            )
        await _private(
            interaction,
            summary + "Press **Clean Verified Topics** to approve this specific batch. "
            "Each topic and its stored owner will be checked again before editing.",
            view=TicketTopicCleanupConfirmView(
                int(member.id), int(guild.id), report.eligible_ids
            ),
        )

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_operations_embed(), view=TicketOperationsView(self.owner_id))


class TicketIntakeActionSelect(discord.ui.Select):
    def __init__(self, parent: "TicketIntakeToolsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose an intake/routing tool…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Category Inventory", value="categories", emoji="🗂️"),
                discord.SelectOption(label="Intake Status", value="status", emoji="📡"),
                discord.SelectOption(label="Test Reason Match", value="match", emoji="🧪"),
                discord.SelectOption(label="Preview Category", value="preview", emoji="🔎"),
                discord.SelectOption(label="Post Staff Actions", value="post-actions", emoji="🛠️"),
            ],
            custom_id="dank:tickets:intake:action:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = str(self.values[0])
        from . import public_ticket_intake_group as intake
        if action == "categories":
            return await _invoke(intake.intake_categories, interaction)
        if action == "status":
            return await _invoke(intake.intake_status, interaction)
        if action == "match":
            return await interaction.response.send_modal(IntakeMatchModal(self.parent_view.owner_id))
        if action == "preview":
            return await interaction.response.send_modal(IntakePreviewModal(self.parent_view.owner_id))
        if action == "post-actions":
            view = TicketIntakePostActionsView(self.parent_view.owner_id)
            return await _replace(interaction, embed=view.embed(), view=view)


class TicketIntakeToolsView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(TicketIntakeActionSelect(self))

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title="🧭 Ticket Intake & Routing",
            description="Inspect category routing, test a reason before users hit it, preview categories, or post the staff actions panel.",
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_operations_embed(), view=TicketOperationsView(self.owner_id))


class IntakeMatchModal(discord.ui.Modal, title="Test Ticket Routing"):
    reason = discord.ui.TextInput(label="Ticket reason to test", style=discord.TextStyle.paragraph, max_length=600)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        from .public_ticket_intake_group import intake_match
        await _invoke(intake_match, interaction, reason=str(self.reason.value))


class IntakePreviewModal(discord.ui.Modal, title="Preview Ticket Category"):
    slug = discord.ui.TextInput(label="Category slug", placeholder="support", max_length=80)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        from .public_ticket_intake_group import intake_preview
        await _invoke(intake_preview, interaction, slug=str(self.slug.value))


class IntakeActionsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "TicketIntakePostActionsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose an active ticket channel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:tickets:intake:post_actions:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        channel = interaction.guild.get_channel(int(getattr(selected, "id", 0) or 0))
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose an active ticket channel.")
        from .public_ticket_intake_group import intake_post_actions
        await _invoke(intake_post_actions, interaction, channel=channel)


class TicketIntakePostActionsView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(IntakeActionsChannelSelect(self))

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title="🛠️ Post Ticket Staff Actions",
            description="Choose the active ticket channel that should receive the persistent staff actions panel.",
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=1)
    async def use_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        channel = current_text_channel(interaction, require_bot_embed=True)
        if channel is None:
            return await _private(interaction, "❌ Open this menu in the active ticket text channel "
                                  "or use the picker for another ticket.")
        from .public_ticket_intake_group import intake_post_actions
        await _invoke(intake_post_actions, interaction, channel=channel)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TicketIntakeToolsView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)


class TicketCategoryActionSelect(discord.ui.Select):
    def __init__(self, parent: "TicketCategoryToolsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a ticket-category action…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Sync Managed Catalog", value="sync", emoji="🔄", description="Repair/update built-in definitions; does not enable all"),
                discord.SelectOption(label="Create Custom Category", value="create", emoji="➕"),
                discord.SelectOption(label="Edit Custom Category", value="edit", emoji="✏️"),
                discord.SelectOption(label="Delete Custom Category", value="delete", emoji="🗑️"),
                discord.SelectOption(label="Set Custom Default", value="set-default", emoji="⭐"),
                discord.SelectOption(label="Reorder Custom Category", value="reorder", emoji="↕️"),
                discord.SelectOption(label="Replace Custom Keywords", value="keywords", emoji="🏷️"),
            ],
            custom_id="dank:tickets:categories:action:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = str(self.values[0])
        from . import public_ticket_category_group as categories
        if action == "sync":
            return await _invoke(categories.category_sync, interaction)
        modal_map = {
            "create": CategoryCreateModal,
            "edit": CategoryEditModal,
            "delete": CategorySlugModal,
            "set-default": CategorySlugModal,
            "reorder": CategoryReorderModal,
            "keywords": CategoryKeywordsModal,
        }
        cls = modal_map.get(action)
        if cls is None:
            return await _private(interaction, "❌ That category action is unavailable.")
        if cls is CategorySlugModal:
            return await interaction.response.send_modal(cls(self.parent_view.owner_id, action=action))
        return await interaction.response.send_modal(cls(self.parent_view.owner_id))


class TicketCategoryToolsView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(TicketCategoryActionSelect(self))

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title="🗂️ Ticket Category Manager",
            description=(
                "Use **Choose Built-ins** to turn supported Dank Shield ticket categories on or off for this server. "
                "Use the action menu for catalog repair and custom server categories. "
                "**Sync Managed Catalog** updates stored definitions; it does not enable every category."
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Choose Built-ins", emoji="📚", style=discord.ButtonStyle.primary, row=1)
    async def builtins(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from ..startup_guards import ticket_category_setup_guard

        await ticket_category_setup_guard.open_category_setup_manager(
            interaction,
            context="tickets",
        )

    @discord.ui.button(label="View Inventory", emoji="👀", style=discord.ButtonStyle.secondary, row=1)
    async def inventory(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_ticket_intake_group import intake_categories
        await _invoke(intake_categories, interaction)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_operations_embed(), view=TicketOperationsView(self.owner_id))


class CategoryCreateModal(discord.ui.Modal, title="Create Custom Ticket Category"):
    name_input = discord.ui.TextInput(label="Display name", max_length=100)
    slug = discord.ui.TextInput(label="Slug", placeholder="billing-help", max_length=80)
    intake_type = discord.ui.TextInput(label="Intake type", placeholder="general", max_length=40)
    description_input = discord.ui.TextInput(label="Description", required=False, max_length=500, style=discord.TextStyle.paragraph)
    keywords = discord.ui.TextInput(label="Keywords, comma separated", required=False, max_length=500)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        from .public_ticket_category_group import category_create
        await _invoke(
            category_create,
            interaction,
            name=str(self.name_input.value),
            slug=str(self.slug.value),
            intake_type=str(self.intake_type.value),
            description=str(self.description_input.value or "") or None,
            keywords=str(self.keywords.value or "") or None,
            is_default=False,
            sort_order=None,
        )


class CategoryEditModal(discord.ui.Modal, title="Edit Custom Ticket Category"):
    slug = discord.ui.TextInput(label="Existing custom slug", max_length=80)
    name_input = discord.ui.TextInput(label="New name (blank = unchanged)", required=False, max_length=100)
    intake_type = discord.ui.TextInput(label="New intake type (blank = unchanged)", required=False, max_length=40)
    description_input = discord.ui.TextInput(label="New description (blank = unchanged)", required=False, max_length=500, style=discord.TextStyle.paragraph)
    keywords = discord.ui.TextInput(label="New keywords (blank = unchanged)", required=False, max_length=500)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)

    @staticmethod
    def _optional(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        from .public_ticket_category_group import category_edit
        await _invoke(
            category_edit,
            interaction,
            slug=str(self.slug.value),
            name=self._optional(self.name_input.value),
            intake_type=self._optional(self.intake_type.value),
            description=self._optional(self.description_input.value),
            keywords=self._optional(self.keywords.value),
            is_default=None,
            sort_order=None,
        )


class CategorySlugModal(discord.ui.Modal):
    def __init__(self, owner_id: int, *, action: str) -> None:
        title = "Delete Custom Category" if action == "delete" else "Set Custom Default"
        super().__init__(title=title, timeout=600)
        self.owner_id = int(owner_id)
        self.action = action
        self.slug = discord.ui.TextInput(label="Custom category slug", max_length=80)
        self.add_item(self.slug)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        from . import public_ticket_category_group as categories
        command = categories.category_delete if self.action == "delete" else categories.category_set_default
        await _invoke(command, interaction, slug=str(self.slug.value))


class CategoryReorderModal(discord.ui.Modal, title="Reorder Custom Ticket Category"):
    slug = discord.ui.TextInput(label="Custom category slug", max_length=80)
    sort_order = discord.ui.TextInput(label="Sort order", placeholder="100", max_length=8)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        try:
            order = int(str(self.sort_order.value).strip())
        except Exception:
            return await _private(interaction, "❌ Sort order must be a whole number.")
        from .public_ticket_category_group import category_reorder
        await _invoke(category_reorder, interaction, slug=str(self.slug.value), sort_order=order)


class CategoryKeywordsModal(discord.ui.Modal, title="Replace Custom Category Keywords"):
    slug = discord.ui.TextInput(label="Custom category slug", max_length=80)
    keywords = discord.ui.TextInput(label="Keywords, comma separated", max_length=1000, style=discord.TextStyle.paragraph)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the manager who opened this form can submit it.")
        from .public_ticket_category_group import category_keywords
        await _invoke(category_keywords, interaction, slug=str(self.slug.value), keywords=str(self.keywords.value))


async def open_current_ticket_center(interaction: discord.Interaction) -> None:
    if not await _require_staff(interaction):
        return
    channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    view = TicketActionCenterView(int(interaction.user.id), channel_id=int(getattr(channel, "id", 0) or 0))
    await _private(interaction, embed=_ticket_center_embed(channel), view=view)


async def open_ticket_operations_center(interaction: discord.Interaction) -> None:
    if not await _require_staff(interaction):
        return
    await _private(
        interaction,
        embed=_operations_embed(),
        view=TicketOperationsView(int(interaction.user.id)),
    )


__all__ = [
    "TicketActionCenterView",
    "TicketOperationsView",
    "open_current_ticket_center",
    "open_ticket_operations_center",
]
