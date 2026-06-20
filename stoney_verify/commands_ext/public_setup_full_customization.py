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
    embed.add_field(name="Next", value="Pick another item, press **Back to Setup**, or run **Run Health Check**.", inline=False)
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


async def _back_to_setup(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    try:
        mod = _setup_module()
        builder = getattr(mod, "_build_main_setup_payload", None)
        if callable(builder):
            embed, view = await builder(guild)
            return await _edit_setup(interaction, embed=embed, view=view)
    except Exception as e:
        _warn(f"back to setup failed: {e!r}")
    await interaction.response.send_message("✅ Saved. Run `/dank setup` again to return to the setup screen.", ephemeral=True)


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


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
                (blockers if require_manage else warnings).append(f"Bot role is not above {role.mention}. Move Dank Shield higher in Server Settings → Roles.")
        except Exception:
            pass

    return not blockers, blockers + warnings


def _channel_warnings(guild: discord.Guild, channel: Any, *, need_files: bool = False) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    me = _bot_member(guild)
    if me is None:
        blockers.append("Bot member could not be resolved for channel permission checks.")
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
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_full_custom:back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _back_to_setup(interaction)


class FullChooseExistingView(SetupBackView):
    @discord.ui.button(label="Roles", emoji="👥", style=discord.ButtonStyle.primary, custom_id="stoney_full_custom:roles", row=0)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="👥 Customize Roles",
            description="Pick the exact roles this server uses. Names do not matter; the saved role IDs are what Dank Shield uses.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Included", value="Server-control, ticket staff, Pending / Unverified, Verified, Member / Resident, and VC staff fallback.", inline=False)
        await interaction.response.edit_message(embed=embed, view=RoleCustomizationPageOne())

    @discord.ui.button(label="Discord Categories", emoji="📁", style=discord.ButtonStyle.primary, custom_id="stoney_full_custom:categories", row=0)
    async def categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="📁 Customize Discord Categories",
            description="Pick the actual Discord channel categories Dank Shield should use. These are not the logical ticket routing categories.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=DiscordCategoryCustomizationView())

    @discord.ui.button(label="Channels", emoji="💬", style=discord.ButtonStyle.primary, custom_id="stoney_full_custom:channels", row=1)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="💬 Customize Public + Verification Channels",
            description="Pick the text/voice channels users and staff interact with.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=ChannelCustomizationPageOne())

    @discord.ui.button(label="Logs + Status", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_full_custom:logs", row=1)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧾 Customize Logs + Status",
            description="Pick where Dank Shield sends moderation, security, join/leave, transcript, and health/status messages.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=LogStatusCustomizationView())

    @discord.ui.button(label="Behavior Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_full_custom:behavior", row=2)
    async def behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(BehaviorSettingsModal())


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
        await _send_saved(interaction, title="✅ Saved Setup Role", description=f"Saved {_mention(role)} for `{', '.join(self.columns + self.also_same)}`.", warnings=messages if messages else None)


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
        channel = self.values[0]
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        ok, messages = _channel_warnings(guild, channel, need_files=self.need_files)
        if not ok:
            embed = discord.Embed(title="🚫 Channel Not Saved", description="\n".join(f"• {x}" for x in messages), color=discord.Color.red())
            embed.add_field(name="What To Do", value="Fix the listed permissions, then pick this channel again.", inline=False)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        payload = {column: _snowflake(channel) for column in self.columns + self.also_same}
        await _save_config(interaction, payload, source=f"/dank setup full customization channel picker: {self.placeholder}")
        await _send_saved(interaction, title="✅ Saved Setup Channel", description=f"Saved {_mention(channel)} for `{', '.join(self.columns + self.also_same)}`.")


class RoleCustomizationPageOne(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveRoleSelect(placeholder="Server-control / bot manager role", columns=("server_control_role_id",), also_same=("control_role_id", "perm_role_id", "bot_manager_role_id"), require_manage=False, row=0))
        self.add_item(SaveRoleSelect(placeholder="Ticket staff / support role", columns=("staff_role_id",), also_same=("vc_staff_role_id",), require_manage=False, row=1))
        self.add_item(SaveRoleSelect(placeholder="Pending / Unverified role", columns=("unverified_role_id",), require_manage=True, row=2))
        self.add_item(SaveRoleSelect(placeholder="Verified role", columns=("verified_role_id",), require_manage=True, row=3))

    @discord.ui.button(label="More Roles", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="stoney_full_custom:roles_more", row=4)
    async def more_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="👥 More Role Settings", description="Pick optional/fallback roles.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=RoleCustomizationPageTwo())


class RoleCustomizationPageTwo(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveRoleSelect(placeholder="Member / Resident / full-access role", columns=("resident_role_id",), also_same=("member_role_id",), require_manage=True, row=0))
        self.add_item(SaveRoleSelect(placeholder="Voice verification staff role override", columns=("vc_staff_role_id",), require_manage=False, row=1))
        self.add_item(SaveRoleSelect(placeholder="Additional server-control role override", columns=("control_role_id",), also_same=("server_control_role_id",), require_manage=False, row=2))


class DiscordCategoryCustomizationView(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Start / welcome Discord category", columns=("start_category_id",), also_same=("welcome_category_id",), channel_types=[discord.ChannelType.category], row=0))
        self.add_item(SaveChannelSelect(placeholder="Open tickets Discord category", columns=("ticket_category_id",), channel_types=[discord.ChannelType.category], row=1))
        self.add_item(SaveChannelSelect(placeholder="Closed/archive tickets Discord category", columns=("ticket_archive_category_id",), also_same=("ticket_closed_category_id",), channel_types=[discord.ChannelType.category], row=2))
        self.add_item(SaveChannelSelect(placeholder="Staff tools / management Discord category", columns=("management_category_id",), also_same=("staff_tools_category_id",), channel_types=[discord.ChannelType.category], row=3))


class ChannelCustomizationPageOne(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Welcome text channel", columns=("welcome_channel_id",), channel_types=[discord.ChannelType.text], row=0))
        self.add_item(SaveChannelSelect(placeholder="Verify text channel", columns=("verify_channel_id",), channel_types=[discord.ChannelType.text], row=1))
        self.add_item(SaveChannelSelect(placeholder="Support / ticket panel text channel", columns=("ticket_panel_channel_id",), also_same=("support_channel_id",), channel_types=[discord.ChannelType.text], row=2))
        self.add_item(SaveChannelSelect(placeholder="VC verification voice channel", columns=("vc_verify_channel_id",), channel_types=[discord.ChannelType.voice], row=3))

    @discord.ui.button(label="More Channels", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="stoney_full_custom:channels_more", row=4)
    async def more_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="💬 More Channel Settings", description="Pick queue/status/helper channels.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=ChannelCustomizationPageTwo())


class ChannelCustomizationPageTwo(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="VC verification queue/status text channel", columns=("vc_verify_queue_channel_id",), channel_types=[discord.ChannelType.text], row=0))
        self.add_item(SaveChannelSelect(placeholder="General support text channel fallback", columns=("support_channel_id",), also_same=("ticket_panel_channel_id",), channel_types=[discord.ChannelType.text], row=1))
        self.add_item(SaveChannelSelect(placeholder="Bot health/status text channel", columns=("health_channel_id",), also_same=("status_channel_id", "bot_status_channel_id"), channel_types=[discord.ChannelType.text], row=2))


class LogStatusCustomizationView(SetupBackView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Ticket transcripts channel", columns=("transcripts_channel_id",), channel_types=[discord.ChannelType.text], row=0, need_files=True))
        self.add_item(SaveChannelSelect(placeholder="Moderation log channel", columns=("modlog_channel_id",), also_same=("raidlog_channel_id", "raid_log_channel_id", "force_verify_log_channel_id"), channel_types=[discord.ChannelType.text], row=1))
        self.add_item(SaveChannelSelect(placeholder="Join / leave log channel", columns=("join_log_channel_id",), also_same=("join_exit_log_channel_id",), channel_types=[discord.ChannelType.text], row=2))
        self.add_item(SaveChannelSelect(placeholder="Bot status / uptime channel", columns=("status_channel_id",), also_same=("bot_status_channel_id", "uptime_channel_id"), channel_types=[discord.ChannelType.text], row=3))


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


def _patch_module(module: Any) -> None:
    try:
        setattr(module, "ChooseExistingView", FullChooseExistingView)
    except Exception:
        pass
    try:
        setattr(module, "ChooseExistingMenuView", FullChooseExistingView)
    except Exception:
        pass
    try:
        setattr(module, "VerificationRolesPickerView", RoleCustomizationPageOne)
        setattr(module, "TicketBasicsPickerView", DiscordCategoryCustomizationView)
        setattr(module, "VerificationChannelsPickerView", ChannelCustomizationPageOne)
        setattr(module, "LogsStatusPickerView", LogStatusCustomizationView)
    except Exception:
        pass


def install_full_customization() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    patched_any = False
    for module_name in ("public_setup_solid", "public_setup_start"):
        try:
            module = __import__(f"stoney_verify.commands_ext.{module_name}", fromlist=["*"])
            _patch_module(module)
            patched_any = True
        except Exception as e:
            _warn(f"could not patch {module_name}: {e!r}")
    _PATCHED = patched_any
    if patched_any:
        _log("full setup customization picker flow active")
    return patched_any


def register_public_setup_full_customization_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    install_full_customization()
    _REGISTERED = True


__all__ = ["register_public_setup_full_customization_commands", "install_full_customization"]
