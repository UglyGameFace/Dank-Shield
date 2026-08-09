from __future__ import annotations

"""UI-first ticket command centers for the compact public command surface.

This module deliberately invokes the already-registered canonical command objects
instead of reimplementing ticket behavior. That keeps claim/owner authorization,
lifecycle repair, transcript/delete safety, category governance, and existing
server compatibility owned by their current production modules.
"""

from typing import Any, Optional

import discord
from discord import app_commands

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
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
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
    if not await _require_staff(interaction):
        return
    if not await _authorize_ticket_command(interaction, command_name=name, channel=channel):
        return
    from .public_ticket_group import ticket_group

    command = ticket_group.get_command(name)
    if command is None:
        return await _private(interaction, f"❌ Ticket action **{name}** is unavailable.")
    try:
        await _invoke(command, interaction, channel=channel, **kwargs)
    except TypeError:
        # Commands with a required positional value are invoked by their dedicated
        # picker/modal flows and should never land here.
        await _private(interaction, f"❌ Ticket action **{name}** needs more information.")
    except Exception as exc:
        await _private(interaction, f"❌ Ticket action failed safely: `{type(exc).__name__}: {exc}`")


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
                discord.SelectOption(label="Sync Managed Catalog", value="sync", emoji="🔄", description="Restore/update Dank Shield-managed categories"),
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
                "Managed Dank Shield categories stay globally governed. Custom server categories can be "
                "created, edited, deleted, reordered, given a local default, and assigned routing keywords here."
            ),
            color=discord.Color.blurple(),
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
