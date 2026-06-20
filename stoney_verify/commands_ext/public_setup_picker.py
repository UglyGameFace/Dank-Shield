from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .common import safe_defer
from .public_setup_config_writer import apply_public_setup_writer_patch, upsert_guild_config
from .public_setup_group import (
    _add_validation_summary,
    _channel_value,
    _config_embed,
    _require_setup_permission,
    _role_value,
    _send_blocked_setup,
    _utc_iso,
    _validate_log_setup,
    _validate_ticket_setup,
    _validate_verify_setup,
    get_guild_config,
    invalidate_guild_config,
    dank_group,
)


# ============================================================
# public_setup_picker.py
# ------------------------------------------------------------
# Interactive production-safe setup wizard.
#
# This uses Discord's native ChannelSelect and RoleSelect components so server
# admins do not need to paste long IDs or fight styled channel/role names.
# Every save still runs the same validation used by the normal setup commands.
# ============================================================


_TEXT_TYPES = [discord.ChannelType.text, discord.ChannelType.news]
_CATEGORY_TYPES = [discord.ChannelType.category]
_VOICE_TYPES = [discord.ChannelType.voice]
_stage_type = getattr(discord.ChannelType, "stage_voice", None)
if _stage_type is not None:
    _VOICE_TYPES.append(_stage_type)


def _safe_id(value: Any) -> int:
    try:
        return int(getattr(value, "id", value) or 0)
    except Exception:
        return 0


def _channel(guild: discord.Guild, value: Any) -> Optional[discord.abc.GuildChannel]:
    cid = _safe_id(value)
    return guild.get_channel(cid) if cid > 0 else None


def _text_channel(guild: discord.Guild, value: Any) -> Optional[discord.TextChannel]:
    ch = _channel(guild, value)
    return ch if isinstance(ch, discord.TextChannel) else None


def _category(guild: discord.Guild, value: Any) -> Optional[discord.CategoryChannel]:
    ch = _channel(guild, value)
    return ch if isinstance(ch, discord.CategoryChannel) else None


def _voice_channel(guild: discord.Guild, value: Any) -> Optional[discord.VoiceChannel]:
    ch = _channel(guild, value)
    if isinstance(ch, discord.VoiceChannel):
        return ch
    stage_cls = getattr(discord, "StageChannel", None)
    if stage_cls is not None and isinstance(ch, stage_cls):
        return ch  # type: ignore[return-value]
    return None


def _role(guild: discord.Guild, value: Any) -> Optional[discord.Role]:
    rid = _safe_id(value)
    return guild.get_role(rid) if rid > 0 else None


def _line(guild: discord.Guild, label: str, value: Any, *, role: bool = False) -> str:
    obj = _role(guild, value) if role else _channel(guild, value)
    if obj is not None:
        return f"**{label}:** {obj.mention} (`{int(obj.id)}`)"
    return f"**{label}:** Not set"


def _selected_id(values: list[Any]) -> Optional[str]:
    if not values:
        return None
    try:
        return str(int(values[0].id))
    except Exception:
        return None


class OwnerOnlyView(discord.ui.View):
    def __init__(self, *, owner_id: int, timeout: float = 600.0) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ This setup picker belongs to the admin who opened it.", ephemeral=True)
            return False
        return await _require_setup_permission(interaction)


class ChannelPick(discord.ui.ChannelSelect):
    def __init__(self, state: dict[str, Any], key: str, placeholder: str, channel_types: list[discord.ChannelType], *, row: int, required: bool = True) -> None:
        super().__init__(placeholder=placeholder, channel_types=channel_types, min_values=1 if required else 0, max_values=1, row=row)
        self.state = state
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state[self.key] = _selected_id(list(self.values))
        await interaction.response.defer()


class RolePick(discord.ui.RoleSelect):
    def __init__(self, state: dict[str, Any], key: str, placeholder: str, *, row: int, required: bool = True) -> None:
        super().__init__(placeholder=placeholder, min_values=1 if required else 0, max_values=1, row=row)
        self.state = state
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state[self.key] = _selected_id(list(self.values))
        await interaction.response.defer()


async def _edit_or_send(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
    except Exception:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def _home_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Dank Shield Setup Picker",
        description=(
            "Use the buttons below to configure this server with Discord dropdowns.\n\n"
            "No copied IDs. No fighting long or styled names. Every save still validates before writing."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Verification",
        value="\n".join(
            [
                _line(guild, "Verify text", getattr(cfg, "verify_channel_id", 0)),
                _line(guild, "VC verify", getattr(cfg, "vc_verify_channel_id", 0)),
                _line(guild, "Unverified", getattr(cfg, "unverified_role_id", 0), role=True),
                _line(guild, "Verified", getattr(cfg, "verified_role_id", 0), role=True),
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Logs",
        value="\n".join(
            [
                _line(guild, "Modlog", getattr(cfg, "modlog_channel_id", 0)),
                _line(guild, "Raid/security", getattr(cfg, "raidlog_channel_id", 0)),
                _line(guild, "Join/exit", getattr(cfg, "join_log_channel_id", 0)),
            ]
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • Config source: {getattr(cfg, 'source', 'unknown')}")
    return embed


class SetupPickerHome(OwnerOnlyView):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.cfg = cfg

    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.primary, emoji="🎫", row=0)
    async def tickets(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = TicketPicker(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Verification", style=discord.ButtonStyle.primary, emoji="✅", row=0)
    async def verification(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = VerifyPicker(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="VC Verify", style=discord.ButtonStyle.secondary, emoji="🎙️", row=1)
    async def vc_verify(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = VCPicker(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="🗃️", row=1)
    async def logs(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = LogPicker(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=2)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        self.cfg = cfg
        await _edit_or_send(interaction, embed=_home_embed(guild, cfg), view=self)


class TicketPicker(OwnerOnlyView):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.state = {
            "ticket_category_id": getattr(cfg, "ticket_category_id", None),
            "ticket_archive_category_id": getattr(cfg, "ticket_archive_category_id", None),
            "staff_role_id": getattr(cfg, "staff_role_id", None),
            "transcripts_channel_id": getattr(cfg, "transcripts_channel_id", None),
        }
        self.add_item(ChannelPick(self.state, "ticket_category_id", "Open ticket category", _CATEGORY_TYPES, row=0))
        self.add_item(ChannelPick(self.state, "ticket_archive_category_id", "Archive/closed category", _CATEGORY_TYPES, row=1, required=False))
        self.add_item(RolePick(self.state, "staff_role_id", "Ticket staff role", row=2))
        self.add_item(ChannelPick(self.state, "transcripts_channel_id", "Transcript text channel", _TEXT_TYPES, row=3, required=False))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title="🎫 Ticket Setup Picker", description="Pick values, then press **Save Tickets**.", color=discord.Color.blurple())
        embed.add_field(name="Selected", value="\n".join([
            _line(guild, "Open category", self.state.get("ticket_category_id")),
            _line(guild, "Archive category", self.state.get("ticket_archive_category_id")),
            _line(guild, "Staff role", self.state.get("staff_role_id"), role=True),
            _line(guild, "Transcripts", self.state.get("transcripts_channel_id")),
        ]), inline=False)
        return embed

    @discord.ui.button(label="Save Tickets", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await safe_defer(interaction, ephemeral=True)
        ticket_category = _category(guild, self.state.get("ticket_category_id"))
        archive_category = _category(guild, self.state.get("ticket_archive_category_id"))
        staff_role = _role(guild, self.state.get("staff_role_id"))
        transcripts = _text_channel(guild, self.state.get("transcripts_channel_id"))
        blockers = []
        if ticket_category is None:
            blockers.append("Open ticket category is required.")
        if staff_role is None:
            blockers.append("Ticket staff role is required.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 Ticket Setup Blocked", blockers, [], [])
        assert ticket_category is not None and staff_role is not None
        validation_blockers, warnings, ok = _validate_ticket_setup(guild, ticket_category, staff_role, archive_category, transcripts)
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Ticket Setup Blocked", validation_blockers, warnings, ok)
        await upsert_guild_config(guild.id, {
            "ticket_category_id": _channel_value(ticket_category),
            "ticket_archive_category_id": _channel_value(archive_category),
            "staff_role_id": _role_value(staff_role),
            "vc_staff_role_id": _role_value(staff_role),
            "transcripts_channel_id": _channel_value(transcripts),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        })
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Ticket Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHome(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=_home_embed(guild, cfg), view=view)


class VerifyPicker(OwnerOnlyView):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.state = {
            "verify_channel_id": getattr(cfg, "verify_channel_id", None),
            "unverified_role_id": getattr(cfg, "unverified_role_id", None),
            "verified_role_id": getattr(cfg, "verified_role_id", None),
            "resident_role_id": getattr(cfg, "resident_role_id", None),
        }
        self.add_item(ChannelPick(self.state, "verify_channel_id", "Verify/start text channel", _TEXT_TYPES, row=0))
        self.add_item(RolePick(self.state, "unverified_role_id", "Unverified role", row=1))
        self.add_item(RolePick(self.state, "verified_role_id", "Verified role", row=2))
        self.add_item(RolePick(self.state, "resident_role_id", "Resident/member role", row=3, required=False))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title="✅ Verification Setup Picker", description="Pick the verify text channel and core roles.", color=discord.Color.blurple())
        embed.add_field(name="Selected", value="\n".join([
            _line(guild, "Verify text", self.state.get("verify_channel_id")),
            _line(guild, "Unverified", self.state.get("unverified_role_id"), role=True),
            _line(guild, "Verified", self.state.get("verified_role_id"), role=True),
            _line(guild, "Resident", self.state.get("resident_role_id"), role=True),
        ]), inline=False)
        return embed

    @discord.ui.button(label="Save Verification", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await safe_defer(interaction, ephemeral=True)
        verify_channel = _text_channel(guild, self.state.get("verify_channel_id"))
        unverified = _role(guild, self.state.get("unverified_role_id"))
        verified = _role(guild, self.state.get("verified_role_id"))
        resident = _role(guild, self.state.get("resident_role_id"))
        cfg = await get_guild_config(guild.id, refresh=True)
        vc_verify = _voice_channel(guild, getattr(cfg, "vc_verify_channel_id", 0))
        vc_queue = _text_channel(guild, getattr(cfg, "vc_verify_queue_channel_id", 0))
        blockers = []
        if verify_channel is None:
            blockers.append("Verify text channel is required.")
        if unverified is None:
            blockers.append("Unverified role is required.")
        if verified is None:
            blockers.append("Verified role is required.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", blockers, [], [])
        assert verify_channel is not None and unverified is not None and verified is not None
        validation_blockers, warnings, ok = _validate_verify_setup(guild, verify_channel, unverified, verified, resident, vc_verify, vc_queue)
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", validation_blockers, warnings, ok)
        await upsert_guild_config(guild.id, {
            "verify_channel_id": _channel_value(verify_channel),
            "unverified_role_id": _role_value(unverified),
            "verified_role_id": _role_value(verified),
            "resident_role_id": _role_value(resident),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        })
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Verification Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHome(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=_home_embed(guild, cfg), view=view)


class VCPicker(OwnerOnlyView):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.state = {
            "vc_verify_channel_id": getattr(cfg, "vc_verify_channel_id", None),
            "vc_verify_queue_channel_id": getattr(cfg, "vc_verify_queue_channel_id", None),
        }
        self.add_item(ChannelPick(self.state, "vc_verify_channel_id", "VC verify voice/stage channel", _VOICE_TYPES, row=0, required=False))
        self.add_item(ChannelPick(self.state, "vc_verify_queue_channel_id", "VC queue/status text channel", _TEXT_TYPES, row=1, required=False))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title="🎙️ VC Verification Picker", description="Pick the voice channel and VC queue/status text channel.", color=discord.Color.blurple())
        embed.add_field(name="Selected", value="\n".join([
            _line(guild, "VC verify", self.state.get("vc_verify_channel_id")),
            _line(guild, "VC queue/status", self.state.get("vc_verify_queue_channel_id")),
        ]), inline=False)
        return embed

    @discord.ui.button(label="Save VC Verify", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await safe_defer(interaction, ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        verify_channel = _text_channel(guild, getattr(cfg, "verify_channel_id", 0))
        unverified = _role(guild, getattr(cfg, "unverified_role_id", 0))
        verified = _role(guild, getattr(cfg, "verified_role_id", 0))
        resident = _role(guild, getattr(cfg, "resident_role_id", 0))
        vc_verify = _voice_channel(guild, self.state.get("vc_verify_channel_id"))
        vc_queue = _text_channel(guild, self.state.get("vc_verify_queue_channel_id"))
        blockers = []
        if verify_channel is None or unverified is None or verified is None:
            blockers.append("Save Verification first so the required verification channel and roles are configured.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 VC Verification Setup Blocked", blockers, [], [])
        assert verify_channel is not None and unverified is not None and verified is not None
        validation_blockers, warnings, ok = _validate_verify_setup(guild, verify_channel, unverified, verified, resident, vc_verify, vc_queue)
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 VC Verification Setup Blocked", validation_blockers, warnings, ok)
        await upsert_guild_config(guild.id, {
            "vc_verify_channel_id": _channel_value(vc_verify),
            "vc_verify_queue_channel_id": _channel_value(vc_queue),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        })
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ VC Verification Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHome(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=_home_embed(guild, cfg), view=view)


class LogPicker(OwnerOnlyView):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.state = {
            "modlog_channel_id": getattr(cfg, "modlog_channel_id", None),
            "raidlog_channel_id": getattr(cfg, "raidlog_channel_id", None),
            "join_log_channel_id": getattr(cfg, "join_log_channel_id", None),
            "force_verify_log_channel_id": getattr(cfg, "force_verify_log_channel_id", None),
        }
        self.add_item(ChannelPick(self.state, "modlog_channel_id", "Modlog text channel", _TEXT_TYPES, row=0))
        self.add_item(ChannelPick(self.state, "raidlog_channel_id", "Raid/security log text channel", _TEXT_TYPES, row=1, required=False))
        self.add_item(ChannelPick(self.state, "join_log_channel_id", "Join/exit log text channel", _TEXT_TYPES, row=2, required=False))
        self.add_item(ChannelPick(self.state, "force_verify_log_channel_id", "Forced verification log text channel", _TEXT_TYPES, row=3, required=False))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title="🗃️ Log Setup Picker", description="Pick log channels. Reusing one channel for multiple log types is allowed.", color=discord.Color.blurple())
        embed.add_field(name="Selected", value="\n".join([
            _line(guild, "Modlog", self.state.get("modlog_channel_id")),
            _line(guild, "Raid/security", self.state.get("raidlog_channel_id")),
            _line(guild, "Join/exit", self.state.get("join_log_channel_id")),
            _line(guild, "Forced verification", self.state.get("force_verify_log_channel_id")),
        ]), inline=False)
        return embed

    @discord.ui.button(label="Save Logs", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await safe_defer(interaction, ephemeral=True)
        modlog = _text_channel(guild, self.state.get("modlog_channel_id"))
        raidlog = _text_channel(guild, self.state.get("raidlog_channel_id"))
        joinlog = _text_channel(guild, self.state.get("join_log_channel_id"))
        force_verify = _text_channel(guild, self.state.get("force_verify_log_channel_id"))
        if modlog is None:
            return await _send_blocked_setup(interaction, "🚫 Log Setup Blocked", ["Modlog text channel is required."], [], [])
        validation_blockers, warnings, ok = _validate_log_setup(guild, modlog, raidlog, joinlog, force_verify)
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Log Setup Blocked", validation_blockers, warnings, ok)
        await upsert_guild_config(guild.id, {
            "modlog_channel_id": _channel_value(modlog),
            "raidlog_channel_id": _channel_value(raidlog),
            "join_log_channel_id": _channel_value(joinlog),
            "force_verify_log_channel_id": _channel_value(force_verify),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        })
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Log Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHome(owner_id=self.owner_id, cfg=cfg)
        await _edit_or_send(interaction, embed=_home_embed(guild, cfg), view=view)


@dank_group.command(name="setup-picker", description="Open an interactive setup wizard with channel and role dropdowns.")
async def setup_picker(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
    cfg = await get_guild_config(guild.id, refresh=True)
    view = SetupPickerHome(owner_id=int(interaction.user.id), cfg=cfg)
    await interaction.followup.send(embed=_home_embed(guild, cfg), view=view, ephemeral=True)


def register_public_setup_picker_commands(bot: Any, tree: Any) -> None:
    # The command is attached to dank_group by the decorator above.
    # public_setup_group registers the shared /dank group with the tree.
    _ = bot
    _ = tree
    apply_public_setup_writer_patch()
    try:
        print("✅ public_setup_picker: attached /dank setup-picker interactive dropdown wizard")
    except Exception:
        pass


__all__ = ["register_public_setup_picker_commands"]
