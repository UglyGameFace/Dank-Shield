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


def _embed(snapshot: ActionSnapshot) -> discord.Embed:
    count = len(snapshot.items)
    if snapshot.unavailable_sources:
        head = "⚠️ Some sources unavailable"
        color = discord.Color.gold()
    elif count:
        head = f"📥 {count} item{'s' if count != 1 else ''} to review"
        color = discord.Color.blurple()
    else:
        head = "✅ No outstanding items found"
        color = discord.Color.green()
    embed = discord.Embed(
        title="📋 Action & Reminder Center",
        description=(
            f"**Home › Action & Reminder Center**\n{head}\n\n"
            "A private, on-demand view of your server requirements. "
            "The owning feature decides whether each requirement is complete."
        ),
        color=color,
    )
    badges = {"required": "🔴 Required", "recommended": "🟡 Recommended",
              "announcement": "🔵 Announcement"}
    for item in snapshot.items:
        embed.add_field(
            name=f"{badges[item.severity]} • {item.title}",
            value=item.description,
            inline=False,
        )
    if snapshot.unavailable_sources:
        embed.add_field(
            name="⏳ Not checked",
            value=(
                "Couldn't confirm: **" + ", ".join(snapshot.unavailable_sources)
                + "**. Don't assume those requirements are finished. Try Refresh."
            ),
            inline=False,
        )
    embed.add_field(
        name="📩 Reminder delivery",
        value=(
            "Existing Verification Reminders: **"
            + ("enabled" if snapshot.verification_reminders_enabled else "not enabled or unavailable")
            + "**. No additional messages are sent by this inbox. "
            "Other action reminders are not yet scheduled."
        ),
        inline=False,
    )
    embed.set_footer(text="Private • Read-only overview • No auto-pings or permission changes")
    return embed


class ActionCenterView(discord.ui.View):
    def __init__(self, owner_id: int, snapshot: ActionSnapshot, *, is_staff: bool) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)
        self.guild_id = snapshot.guild_id
        routes = {item.route for item in snapshot.items}
        if "verification" not in routes:
            self.remove_item(self.verify)
        if "member_setup" not in routes:
            self.remove_item(self.setup)
        if not is_staff:
            self.remove_item(self.staff_reminders)
            self.remove_item(self.staff_setup)
            self.remove_item(self.staff_builder)

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

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_action_center(interaction)

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


async def open_action_center(interaction: discord.Interaction) -> None:
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
    await interaction.edit_original_response(
        content=None,
        embed=_embed(snapshot),
        view=ActionCenterView(member.id, snapshot, is_staff=scoped_interaction_is_staff(interaction)),
        allowed_mentions=discord.AllowedMentions.none(),
    )
