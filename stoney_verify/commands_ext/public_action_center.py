from __future__ import annotations

"""Mobile-first private Action & Reminder Center: one view, existing owners."""

import discord

from stoney_verify.action_center_service import ActionSnapshot, load_member_actions
from stoney_verify.guild_config import get_guild_config
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_staff_scope import scoped_interaction_is_staff


async def _feedback(interaction: discord.Interaction, content: str) -> None:
    data = {"content": content, "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(**data)
    else:
        await interaction.response.send_message(**data)


_ACTION_SECTIONS = {
    "my": ("Find Verification", "Review Member Setup", "My Reminder Preferences"),
    "staff": ("Verification Reminder Settings", "Member Setup Manager",
              "Action Builder (Draft)", "Notification Systems"),
}
_ACTION_COMMON = ("Refresh", "Home", "Close")


def _embed(snapshot: ActionSnapshot, *, section: str = "my") -> discord.Embed:
    count = len(snapshot.items)
    degraded = bool(snapshot.unavailable_sources)
    embed = discord.Embed(
        title="📋 Action Center • " + ("Staff Tools" if section == "staff" else "My Actions"),
        description=(
            f"**{'⚠️ Some sources unavailable' if degraded else f'{count} item(s) needing review' if count else '✅ No outstanding items found'}**\n"
            "Open a specific action below. This private panel never sends reminders automatically."
        ),
        color=discord.Color.gold() if degraded else discord.Color.blurple() if count else discord.Color.green(),
    )
    if section == "staff":
        embed.add_field(
            name="Delivery status",
            value=(
                "Verification reminders: **"
                + ("Enabled" if snapshot.verification_reminders_enabled else "Disabled or unavailable")
                + "**\nChoose a staff tool below to configure its own settings."
            ),
            inline=False,
        )
    else:
        badges = {"required": "🔴", "recommended": "🟡", "announcement": "🔵"}
        for item in snapshot.items[:3]:
            embed.add_field(
                name=f"{badges[item.severity]} {item.title}",
                value=item.description,
                inline=False,
            )
        if count > 3:
            embed.add_field(name="Other items", value=f"{count - 3} more items require review.", inline=False)
    if degraded:
        embed.add_field(
            name="⚠️ Not checked",
            value=("Unavailable: " + ", ".join(snapshot.unavailable_sources))[:900]
                  + ". Don't assume requirements are finished.",
            inline=False,
        )
    embed.set_footer(text="Private • Source permissions checked by the owning feature")
    return embed


class _ActionSectionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild_id: int, section: str) -> None:
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)
        super().__init__(
            placeholder="Choose member or staff actions",
            options=[
                discord.SelectOption(label="My Actions", value="my", default=section == "my"),
                discord.SelectOption(label="Staff Tools", value="staff", default=section == "staff"),
            ],
            row=0, custom_id="dank:actions:section:v2",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if (int(interaction.user.id) != self.owner_id
                or int(interaction.guild_id or 0) != self.guild_id
                or not scoped_interaction_is_staff(interaction)):
            return await _feedback(interaction, "Open your own staff Action Center.")
        await open_action_center(interaction, section=self.values[0])


class ActionCenterView(discord.ui.View):
    def __init__(
        self, owner_id: int, snapshot: ActionSnapshot, *,
        is_staff: bool, section: str = "my",
    ) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)
        self.guild_id = snapshot.guild_id
        self.section = section if section == "staff" and is_staff else "my"
        routes = {item.route for item in snapshot.items}
        if "verification" not in routes:
            self.remove_item(self.verify)
        if "member_setup" not in routes:
            self.remove_item(self.setup)
        if not is_staff:
            self.remove_item(self.staff_reminders)
            self.remove_item(self.staff_setup)
            self.remove_item(self.staff_builder)
            self.remove_item(self.staff_notification_directory)
        existing = {str(getattr(item, "label", "")): item for item in self.children}
        self.clear_items()
        if is_staff:
            self.add_item(_ActionSectionSelect(self.owner_id, self.guild_id, self.section))
        for index, name in enumerate((*_ACTION_SECTIONS[self.section], *_ACTION_COMMON)):
            item = existing.get(name)
            if item is None:
                continue
            item.row = (1 if is_staff else 0) + index // 4
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            int(interaction.user.id) != self.owner_id
            or interaction.guild_id != self.guild_id
        ):
            await _feedback(interaction, "Open your own Action Center in this server.")
            return False
        return True

    @discord.ui.button(label="Find Verification", emoji="✅", style=discord.ButtonStyle.primary, row=0)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _feedback(interaction, "Open this in your server.")
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        # Fresh click-time guild config/channel permissions, never a guessed
        # channel by name. Discord itself applies permission checks to links.
        try:
            cfg = await get_guild_config(guild.id, refresh=True)
            cid = int(cfg.get("verify_channel_id") or 0)
            channel = guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(member).view_channel:
                return await _feedback(interaction, "Your verification channel isn't accessible. Ask server staff.")
        except (ValueError, TypeError, OSError, RuntimeError):
            return await _feedback(interaction, "Couldn't verify the channel. Ask staff or try refreshing.")
        return await _feedback(
            interaction,
            f"✅ Open **{discord.utils.escape_markdown(channel.name)}** to use this server's existing verification controls:\n"
            f"https://discord.com/channels/{guild.id}/{channel.id}",
        )

    @discord.ui.button(label="Review Member Setup", emoji="🧭", style=discord.ButtonStyle.primary, row=0)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_member_setup import open_member_setup
        await open_member_setup(interaction, replace_message=True)

    @discord.ui.button(label="Verification Reminder Settings", emoji="📩", style=discord.ButtonStyle.secondary, row=1)
    async def staff_reminders(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not scoped_interaction_is_staff(interaction):
            return await _feedback(interaction, "Server staff only.")
        from .public_verify_reminder_ui import open_reminder_settings
        await open_reminder_settings(interaction, self.owner_id)

    @discord.ui.button(label="Member Setup Manager", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def staff_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not scoped_interaction_is_staff(interaction):
            return await _feedback(interaction, "Server staff only.")
        from .public_member_setup import open_member_setup_admin
        await open_member_setup_admin(interaction)

    @discord.ui.button(label="Action Builder (Draft)", emoji="🛠️", style=discord.ButtonStyle.secondary, row=1)
    async def staff_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not scoped_interaction_is_staff(interaction):
            return await _feedback(interaction, "Server staff only.")
        from .public_action_builder import open_action_builder
        await open_action_builder(interaction)

    @discord.ui.button(label="Notification Systems", emoji="🧭", style=discord.ButtonStyle.secondary, row=1)
    async def staff_notification_directory(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not scoped_interaction_is_staff(interaction):
            return await _feedback(interaction, "Server staff only.")
        from .public_action_notification_directory import open_notification_owner_directory
        await open_notification_owner_directory(interaction)

    @discord.ui.button(label="My Reminder Preferences", emoji="🔔", style=discord.ButtonStyle.secondary, row=2)
    async def my_preferences(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_preferences import open_member_action_preferences
        await open_member_action_preferences(interaction)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_action_center(interaction, section=self.section)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_command_surface_v2 import _open_home
        await _open_home(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content="Action Center closed.", embed=None, view=None)
        else:
            await interaction.response.edit_message(content="Action Center closed.", embed=None, view=None)


async def open_action_center(interaction: discord.Interaction, *, section: str = "my") -> None:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if interaction.guild is None or member is None:
        return await _feedback(interaction, "Action Center is available inside a server.")
    # Discord has a short acknowledgement deadline; do this BEFORE DB reads.
    if not interaction.response.is_done():
        if interaction.message is None:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()
    snapshot = await load_member_actions(member)
    if snapshot.user_id != member.id or snapshot.guild_id != member.guild.id:
        return await _feedback(interaction, "Could not safely load your account's actions.")
    is_staff = scoped_interaction_is_staff(interaction)
    selected = section if section == "staff" and is_staff else "my"
    await interaction.edit_original_response(
        content=None,
        embed=_embed(snapshot, section=selected),
        view=ActionCenterView(member.id, snapshot, is_staff=is_staff, section=selected),
        allowed_mentions=discord.AllowedMentions.none(),
    )
