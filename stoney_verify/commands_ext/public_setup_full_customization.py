from __future__ import annotations

"""Full customization layer for /dank setup.

This module keeps customization inside the normal /dank setup flow instead of
adding more public slash commands. It upgrades the "Choose Existing Items" path
so public server owners can map every important role/channel/category/behavior
setting with Discord pickers and simple modals.

This is intentionally an integration module, not a new public command surface:
- no new top-level slash commands
- no extra /dank children
- plugs into the existing public_setup_solid/public_setup_start setup cards
"""

from typing import Any, Optional

import discord

_REGISTERED = False
_PATCHED = False

JOIN_LEAVE_LOG_ALIASES: tuple[str, ...] = (
    "join_leave_channel_id",
    "member_join_leave_log_channel_id",
    "member_lifecycle_log_channel_id",
    "member_log_channel_id",
    "member_logs_channel_id",
    "join_log_channel_id",
    "join_exit_log_channel_id",
    "joinlog_channel_id",
    "joinleave_channel_id",
    "welcome_exit_channel_id",
    "welcome_exit_log_channel_id",
    "leave_log_channel_id",
    "welcome_leave_channel_id",
    "leave_channel_id",
)

STAFF_LOG_ALIASES: tuple[str, ...] = (
    "raidlog_channel_id",
    "raid_log_channel_id",
    "force_verify_log_channel_id",
    "staff_join_audit_channel_id",
    "member_audit_log_channel_id",
    "staff_log_channel_id",
    "staff_logs_channel_id",
    "audit_log_channel_id",
)


def _log(message: str) -> None:
    try:
        print(f"✅ public_setup_full_customization: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_setup_full_customization: {message}")
    except Exception:
        pass


def _snowflake(value: Any) -> str:
    return str(int(getattr(value, "id", value)))


def _mention(obj: Any) -> str:
    mention = getattr(obj, "mention", None)
    return str(mention) if mention else f"`{getattr(obj, 'name', obj)}`"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _short(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _setup_module() -> Any:
    try:
        from . import public_setup_solid as solid
        return solid
    except Exception:
        from . import public_setup_start as solid
        return solid


async def _require_setup_permission(interaction: discord.Interaction) -> bool:
    try:
        mod = _setup_module()
        checker = getattr(mod, "_require_setup_permission", None)
        if callable(checker):
            return bool(await checker(interaction))
    except Exception:
        pass
    try:
        from .public_setup_group import _require_setup_permission as checker
        return bool(await checker(interaction))
    except Exception:
        return False


async def _save_config(interaction: discord.Interaction, payload: dict[str, Any], *, source: str) -> None:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")

    from .public_setup_config_writer import upsert_guild_config
    from ..guild_config import invalidate_guild_config

    final = dict(payload)
    final.update(
        {
            "__config_write_mode": "explicit_override",
            "__config_write_source": source,
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
        }
    )
    await upsert_guild_config(guild.id, final)
    invalidate_guild_config(guild.id)


async def _send_saved(interaction: discord.Interaction, *, title: str, description: str, warnings: Optional[list[str]] = None) -> None:
    embed = discord.Embed(title=title, description=description[:4096], color=discord.Color.green())
    if warnings:
        embed.add_field(name="Warnings", value="\n".join(f"• {x}" for x in warnings)[:1024], inline=False)
    embed.add_field(
        name="Next",
        value=(
            "Choose another item, press **Back to All Features**, or use **Review Setup** from Manage Setup."
        ),
        inline=False,
    )
    try:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        await interaction.followup.send(embed=embed, ephemeral=True)


async def _edit_setup(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        pass



async def _back_to_all_features(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._open_advanced_settings(interaction)


async def _setup_home(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._home_edit(interaction)


async def _close_setup(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._close_setup(interaction)

def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


async def _resolve_selected_channel(guild: discord.Guild, value: Any) -> Any:
    """ChannelSelect can hand us a partial/resolved object. Convert it to the real guild channel."""
    try:
        cid = _safe_int(getattr(value, "id", value), 0)
        if cid <= 0:
            return value

        cached = guild.get_channel(cid)
        if cached is not None:
            return cached

        try:
            fetched = await guild.fetch_channel(cid)
            return fetched
        except Exception:
            return value
    except Exception:
        return value


def _role_manage_warning(guild: discord.Guild, role: discord.Role, *, require_manage: bool) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        if role.is_default():
            blockers.append("@everyone cannot be used for this setting.")
        if role.managed:
            blockers.append(f"{role.mention} is managed by an integration/bot and cannot be used here.")
    except Exception:
        pass

    me = _bot_member(guild)
    if me is None:
        blockers.append("Bot member could not be resolved for role checks.")
    else:
        try:
            if not me.guild_permissions.manage_roles:
                (blockers if require_manage else warnings).append("Bot is missing Manage Roles.")
            elif me.top_role <= role and not me.guild_permissions.administrator:
                (blockers if require_manage else warnings).append(f"Bot role is not above {role.mention}. Move Dank Shield's bot role above this role in Server Settings → Roles.")
        except Exception:
            pass

    return not blockers, blockers + warnings


def _channel_warnings(guild: discord.Guild, channel: Any, *, need_files: bool = False) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    me = _bot_member(guild)
    if me is None:
        blockers.append("Bot member could not be resolved for channel permission checks.")
        return False, blockers

    if not hasattr(channel, "permissions_for"):
        blockers.append("Selected item could not be resolved to a real Discord channel/category. Try again, or re-open setup and pick it once more.")
        return False, blockers

    try:
        perms = channel.permissions_for(me)
        if not perms.view_channel:
            blockers.append("Bot is missing View Channel.")
        if isinstance(channel, discord.TextChannel):
            if not perms.send_messages:
                blockers.append("Bot is missing Send Messages.")
            if not perms.embed_links:
                blockers.append("Bot is missing Embed Links.")
            if need_files and not perms.attach_files:
                blockers.append("Bot is missing Attach Files.")
        if isinstance(channel, discord.CategoryChannel):
            if not perms.manage_channels:
                blockers.append("Bot is missing Manage Channels for this category.")
        if isinstance(channel, discord.VoiceChannel):
            if not perms.connect:
                blockers.append("Bot is missing Connect.")
            if not perms.manage_channels:
                blockers.append("Bot is missing Manage Channels for this voice channel.")
    except Exception as e:
        blockers.append(f"Could not check permissions: {type(e).__name__}")

    return not blockers, blockers



class SetupBackView(discord.ui.View):
    """Parent, home, and close routes shared by customization pages."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:features",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _back_to_all_features(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:home",
        row=4,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _setup_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:close",
        row=4,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _close_setup(interaction)

class FullChooseExistingView(SetupBackView):
    @discord.ui.button(
        label="Access & Staff Roles",
        emoji="👥",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_full_custom:roles",
        row=0,
    )
    async def roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="👥 Choose Your Server Roles",
            description=(
                "Pick only the roles used by features you turned on. "
                "Role names can be anything."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Simple Verify",
            value=(
                "Choose the **Waiting** role and the "
                "**Approved Member** role."
            ),
            inline=False,
        )

        embed.add_field(
            name="Tickets",
            value=(
                "Choose the role for people who answer tickets."
            ),
            inline=False,
        )

        embed.add_field(
            name="Optional",
            value=(
                "Setup managers, full members, and a separate "
                "Voice Verify staff role can be chosen later."
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=RoleCustomizationPageOne(),
        )

    @discord.ui.button(
        label="Ticket Folders",
        emoji="📁",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_full_custom:categories",
        row=0,
    )
    async def categories(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="📁 Choose Ticket Folders",
            description=(
                "Discord calls these categories. Think of them as "
                "folders that hold channels."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Needed when Tickets are ON",
            value=(
                "Choose the folder where new tickets open."
            ),
            inline=False,
        )

        embed.add_field(
            name="Optional",
            value=(
                "You may also choose folders for closed tickets, "
                "welcome channels, and staff tools."
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=DiscordCategoryCustomizationView(),
        )

    @discord.ui.button(
        label="Member Channels",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_full_custom:channels",
        row=1,
    )
    async def channels(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="💬 Choose Member Channels",
            description=(
                "Choose only the channels needed by the features "
                "you turned on."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Simple Verify",
            value="Choose where members press **Verify**.",
            inline=False,
        )

        embed.add_field(
            name="Tickets",
            value=(
                "Choose where members see the "
                "**Create Ticket** panel."
            ),
            inline=False,
        )

        embed.add_field(
            name="Voice Verify",
            value=(
                "Choose the voice channel used for the staff check."
            ),
            inline=False,
        )

        embed.add_field(
            name="Optional",
            value=(
                "Welcome, join/leave, backup support, and bot-status "
                "channels remain available under the channel pages."
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=ChannelCustomizationPageOne(),
        )

    @discord.ui.button(
        label="Logs & Status",
        emoji="🧾",
        style=discord.ButtonStyle.primary,
        custom_id="stoney_full_custom:logs",
        row=1,
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="🧾 Choose Logs & Status Channels",
            description=(
                "Choose where staff should receive records and "
                "bot updates."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Available choices",
            value=(
                "• Ticket transcripts\n"
                "• Moderation and security logs\n"
                "• Join and leave logs\n"
                "• Bot status and uptime"
            ),
            inline=False,
        )

        embed.add_field(
            name="Simple rule",
            value=(
                "Skip a choice when your server does not use "
                "that feature."
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=LogStatusCustomizationView(),
        )

    @discord.ui.button(
        label="Optional Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_full_custom:behavior",
        row=2,
    )
    async def behavior(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        await interaction.response.send_modal(
            BehaviorSettingsModal()
        )


class SaveRoleSelect(discord.ui.RoleSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], also_same: tuple[str, ...] = (), require_manage: bool = True, row: int = 0) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)
        self.columns = columns
        self.also_same = also_same
        self.require_manage = require_manage

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        role = self.values[0]
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        ok, messages = _role_manage_warning(guild, role, require_manage=self.require_manage)
        if not ok:
            embed = discord.Embed(title="🚫 Role Not Saved", description="\n".join(f"• {x}" for x in messages), color=discord.Color.red())
            embed.add_field(name="What To Do", value="Pick a different role, or move Dank Shield's bot role above this role in Server Settings → Roles.", inline=False)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        payload = {column: _snowflake(role) for column in self.columns + self.also_same}
        await _save_config(interaction, payload, source=f"/dank setup full customization role picker: {self.placeholder}")
        await _send_saved(
            interaction,
            title="✅ Role Saved",
            description=(
                f"Saved {_mention(role)} as "
                f"**{str(self.placeholder or 'this role')}**."
            ),
            warnings=messages if messages else None,
        )


class SaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], channel_types: list[discord.ChannelType], also_same: tuple[str, ...] = (), row: int = 0, need_files: bool = False) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types, row=row)
        self.columns = columns
        self.also_same = also_same
        self.need_files = need_files

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        raw_channel = self.values[0]
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        channel = await _resolve_selected_channel(guild, raw_channel)
        ok, messages = _channel_warnings(guild, channel, need_files=self.need_files)
        if not ok:
            embed = discord.Embed(title="🚫 Channel Not Saved", description="\n".join(f"• {x}" for x in messages), color=discord.Color.red())
            embed.add_field(name="What To Do", value="Fix the listed permissions, then pick this channel again.", inline=False)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        payload = {column: _snowflake(channel) for column in self.columns + self.also_same}
        await _save_config(interaction, payload, source=f"/dank setup full customization channel picker: {self.placeholder}")
        await _send_saved(
            interaction,
            title="✅ Channel Saved",
            description=(
                f"Saved {_mention(channel)} as "
                f"**{str(self.placeholder or 'this channel')}**."
            ),
        )


class RoleCustomizationPageOne(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveRoleSelect(
                placeholder="Optional: role allowed to manage Dank Shield",
                columns=("server_control_role_id",),
                also_same=(
                    "control_role_id",
                    "perm_role_id",
                    "bot_manager_role_id",
                ),
                require_manage=False,
                row=0,
            )
        )

        self.add_item(
            SaveRoleSelect(
                placeholder="Tickets: role that answers tickets",
                columns=("staff_role_id",),
                also_same=("vc_staff_role_id",),
                require_manage=False,
                row=1,
            )
        )

        self.add_item(
            SaveRoleSelect(
                placeholder="Verify: waiting role for new members",
                columns=("unverified_role_id",),
                require_manage=True,
                row=2,
            )
        )

        self.add_item(
            SaveRoleSelect(
                placeholder="Verify: approved member role",
                columns=("verified_role_id",),
                require_manage=True,
                row=3,
            )
        )

    @discord.ui.button(
        label="Optional Roles",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_full_custom:roles_more",
        row=4,
    )
    async def more_roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="👥 Optional Roles",
            description=(
                "Most servers can skip this page.\n\n"
                "Use these only when your server has a separate "
                "full-member role, separate Voice Verify staff, "
                "or another Dank Shield manager role."
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=RoleCustomizationPageTwo(),
        )


class RoleCustomizationPageTwo(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveRoleSelect(
                placeholder="Optional: full member or resident role",
                columns=("resident_role_id",),
                also_same=("member_role_id",),
                require_manage=True,
                row=0,
            )
        )

        self.add_item(
            SaveRoleSelect(
                placeholder="Optional: separate Voice Verify staff role",
                columns=("vc_staff_role_id",),
                require_manage=False,
                row=1,
            )
        )

        self.add_item(
            SaveRoleSelect(
                placeholder="Optional: another Dank Shield manager role",
                columns=("control_role_id",),
                also_same=("server_control_role_id",),
                require_manage=False,
                row=2,
            )
        )

    @discord.ui.button(
        label="Back to Main Roles",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_full_custom:roles_first",
        row=4,
    )
    async def first_roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="👥 Choose Your Server Roles",
            description=(
                "Choose only the roles used by features "
                "you turned on."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Usually needed",
            value=(
                "• Tickets: staff role\n"
                "• Simple Verify: waiting role and approved role"
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=RoleCustomizationPageOne(),
        )


class DiscordCategoryCustomizationView(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveChannelSelect(
                placeholder="Optional: welcome or start folder",
                columns=("start_category_id",),
                also_same=("welcome_category_id",),
                channel_types=[discord.ChannelType.category],
                row=0,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Tickets: folder for new tickets",
                columns=("ticket_category_id",),
                channel_types=[discord.ChannelType.category],
                row=1,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Optional: folder for closed tickets",
                columns=("ticket_archive_category_id",),
                also_same=("ticket_closed_category_id",),
                channel_types=[discord.ChannelType.category],
                row=2,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Optional: folder for staff tools",
                columns=("management_category_id",),
                also_same=("staff_tools_category_id",),
                channel_types=[discord.ChannelType.category],
                row=3,
            )
        )


class ChannelCustomizationPageOne(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveChannelSelect(
                placeholder="Optional: welcome channel",
                columns=("welcome_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=0,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Verify: channel with the Verify button",
                columns=("verify_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=1,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Tickets: channel with Create Ticket panel",
                columns=("ticket_panel_channel_id",),
                also_same=("support_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=2,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Voice Verify: voice channel for the check",
                columns=("vc_verify_channel_id",),
                channel_types=[discord.ChannelType.voice],
                row=3,
            )
        )

    @discord.ui.button(
        label="Optional Channels",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_full_custom:channels_more",
        row=4,
    )
    async def more_channels(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="💬 Optional Channels",
            description=(
                "Most servers do not need every item here.\n\n"
                "Choose only channels used by features "
                "you turned on."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Available",
            value=(
                "• Voice Verify staff requests\n"
                "• Join and leave logs\n"
                "• Backup support channel\n"
                "• Bot status and uptime"
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=ChannelCustomizationPageTwo(),
        )


class ChannelCustomizationPageTwo(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveChannelSelect(
                placeholder="Voice Verify: staff request channel",
                columns=("vc_verify_queue_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=0,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Join and leave: staff log channel",
                columns=("join_leave_log_channel_id",),
                also_same=JOIN_LEAVE_LOG_ALIASES,
                channel_types=[discord.ChannelType.text],
                row=1,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Tickets: backup support channel",
                columns=("support_channel_id",),
                also_same=("ticket_panel_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=2,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Bot status: uptime and health channel",
                columns=("health_channel_id",),
                also_same=(
                    "status_channel_id",
                    "bot_status_channel_id",
                ),
                channel_types=[discord.ChannelType.text],
                row=3,
            )
        )

    @discord.ui.button(
        label="Back to Main Channels",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_full_custom:channels_first",
        row=4,
    )
    async def first_channels(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="💬 Choose Member Channels",
            description=(
                "Choose only the channels needed by the "
                "features you turned on."
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Usually needed",
            value=(
                "• Simple Verify: Verify channel\n"
                "• Tickets: Create Ticket panel channel\n"
                "• Voice Verify: voice channel"
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=ChannelCustomizationPageOne(),
        )


class LogStatusCustomizationView(SetupBackView):
    def __init__(self) -> None:
        super().__init__()

        self.add_item(
            SaveChannelSelect(
                placeholder="Tickets: saved transcript channel",
                columns=("transcripts_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=0,
                need_files=True,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Moderation and protection log channel",
                columns=("modlog_channel_id",),
                also_same=STAFF_LOG_ALIASES,
                channel_types=[discord.ChannelType.text],
                row=1,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Join and leave log channel",
                columns=("join_leave_log_channel_id",),
                also_same=JOIN_LEAVE_LOG_ALIASES,
                channel_types=[discord.ChannelType.text],
                row=2,
            )
        )

        self.add_item(
            SaveChannelSelect(
                placeholder="Bot status and uptime channel",
                columns=("status_channel_id",),
                also_same=(
                    "bot_status_channel_id",
                    "uptime_channel_id",
                ),
                channel_types=[discord.ChannelType.text],
                row=3,
            )
        )


class BehaviorSettingsModal(discord.ui.Modal, title="Setup Behavior Settings"):
    ticket_prefix = discord.ui.TextInput(label="Ticket channel prefix", placeholder="ticket", default="ticket", required=False, max_length=32)
    verify_kick_hours = discord.ui.TextInput(label="Pending verification kick hours", placeholder="24", default="24", required=False, max_length=4)
    notes = discord.ui.TextInput(label="Optional setup note", placeholder="Why you changed this, optional", required=False, max_length=200, style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        prefix = str(self.ticket_prefix.value or "ticket").strip().lower().replace(" ", "-")[:32] or "ticket"
        hours = _safe_int(self.verify_kick_hours.value, 24)
        if hours < 0 or hours > 720:
            return await interaction.response.send_message("❌ Pending verification kick hours must be between 0 and 720. Use 0 only if you intentionally disable timed kick behavior.", ephemeral=True)
        await _save_config(
            interaction,
            {
                "ticket_prefix": prefix,
                "verify_kick_hours": str(hours),
                "setup_note": _short(self.notes.value, 200),
            },
            source="/dank setup full customization behavior modal",
        )
        await _send_saved(
            interaction,
            title="✅ Saved Behavior Settings",
            description=f"Ticket prefix: `{prefix}`\nPending verification kick hours: `{hours}`",
        )



def install_full_customization() -> bool:
    """Compatibility entrypoint; integration is now explicit, not patched."""
    global _PATCHED
    _PATCHED = True
    return True


def register_public_setup_full_customization_commands(
    bot: Any,
    tree: Any,
) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    install_full_customization()
    _REGISTERED = True
    _log("direct full customization routes ready")

__all__ = [
    "register_public_setup_full_customization_commands",
    "install_full_customization",
    "FullChooseExistingView",
]
