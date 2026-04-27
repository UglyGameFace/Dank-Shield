from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import discord

from .common import safe_defer
from .public_setup_config_writer import apply_public_setup_writer_patch, upsert_guild_config
from .public_setup_group import (
    _add_validation_summary,
    _channel_value,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _role_value,
    _safe_str,
    _send_blocked_setup,
    _utc_iso,
    _validate_log_setup,
    _validate_ticket_setup,
    _validate_verify_setup,
    get_guild_config,
    invalidate_guild_config,
    stoney_group,
)


# ============================================================
# public_setup_picker.py
# ------------------------------------------------------------
# Interactive setup wizard for production/beta onboarding.
#
# This avoids forcing admins to paste channel IDs or fight slash
# command autocomplete. It uses Discord's native channel/role
# select menus, then still runs the same validation/refusal rules
# before writing anything to guild_configs.
# ============================================================


_SETUP_PICKER_COMMAND_ATTACHED = False


def _stage_channel_type() -> Optional[discord.ChannelType]:
    return getattr(discord.ChannelType, "stage_voice", None)


def _voice_channel_types() -> list[discord.ChannelType]:
    out = [discord.ChannelType.voice]
    stage = _stage_channel_type()
    if stage is not None:
        out.append(stage)
    return out


def _get_channel(guild: discord.Guild, channel_id: Any) -> Optional[discord.abc.GuildChannel]:
    try:
        cid = int(str(channel_id or "0"))
        return guild.get_channel(cid) if cid > 0 else None
    except Exception:
        return None


def _get_role(guild: discord.Guild, role_id: Any) -> Optional[discord.Role]:
    try:
        rid = int(str(role_id or "0"))
        return guild.get_role(rid) if rid > 0 else None
    except Exception:
        return None


def _text_channel(guild: discord.Guild, channel_id: Any) -> Optional[discord.TextChannel]:
    channel = _get_channel(guild, channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _category_channel(guild: discord.Guild, channel_id: Any) -> Optional[discord.CategoryChannel]:
    channel = _get_channel(guild, channel_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


def _voice_channel(guild: discord.Guild, channel_id: Any) -> Optional[discord.VoiceChannel]:
    channel = _get_channel(guild, channel_id)
    if isinstance(channel, discord.VoiceChannel):
        return channel
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None and isinstance(channel, stage_type):
        return channel  # type: ignore[return-value]
    return None


def _summary_line(guild: discord.Guild, label: str, value: Any, *, role: bool = False) -> str:
    resolved = _get_role(guild, value) if role else _get_channel(guild, value)
    if resolved is not None:
        return f"**{label}:** {resolved.mention}"
    return f"**{label}:** Not selected"


def _wizard_embed(guild: discord.Guild, cfg: Any, *, title: str = "🧭 Stoney Setup Picker") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=(
            "Pick a setup area below. This wizard uses Discord dropdowns, not pasted IDs.\n\n"
            "Every save still validates permissions, role hierarchy, and channel type before writing config."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current Verification",
        value="\n".join(
            [
                _summary_line(guild, "Verify start text channel", getattr(cfg, "verify_channel_id", 0)),
                _summary_line(guild, "VC verify channel", getattr(cfg, "vc_verify_channel_id", 0)),
                _summary_line(guild, "Unverified role", getattr(cfg, "unverified_role_id", 0), role=True),
                _summary_line(guild, "Verified role", getattr(cfg, "verified_role_id", 0), role=True),
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Current Logs",
        value="\n".join(
            [
                _summary_line(guild, "Modlog", getattr(cfg, "modlog_channel_id", 0)),
                _summary_line(guild, "Raid/security log", getattr(cfg, "raidlog_channel_id", 0)),
                _summary_line(guild, "Join/exit log", getattr(cfg, "join_log_channel_id", 0)),
            ]
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • Config source: {_safe_str(getattr(cfg, 'source', 'unknown'), 'unknown')}")
    return embed


async def _send_or_edit(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
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


class _OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message("❌ This setup picker belongs to the admin who opened it.", ephemeral=True)
        return False


class _ChannelPick(discord.ui.ChannelSelect):
    def __init__(self, state: Dict[str, Any], key: str, label: str, channel_types: Sequence[discord.ChannelType], *, required: bool = True, row: Optional[int] = None):
        super().__init__(
            placeholder=label,
            channel_types=list(channel_types),
            min_values=1 if required else 0,
            max_values=1,
            row=row,
        )
        self.state = state
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0] if self.values else None
        self.state[self.key] = str(int(value.id)) if value is not None else None
        await interaction.response.defer()


class _RolePick(discord.ui.RoleSelect):
    def __init__(self, state: Dict[str, Any], key: str, label: str, *, required: bool = True, row: Optional[int] = None):
        super().__init__(
            placeholder=label,
            min_values=1 if required else 0,
            max_values=1,
            row=row,
        )
        self.state = state
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0] if self.values else None
        self.state[self.key] = str(int(value.id)) if value is not None else None
        await interaction.response.defer()


class SetupPickerHomeView(_OwnerView):
    def __init__(self, owner_id: int, cfg: Any):
        super().__init__(owner_id)
        self.cfg = cfg

    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.primary, emoji="🎫", row=0)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        view = TicketSetupPickerView(self.owner_id, self.cfg)
        await _send_or_edit(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Verification", style=discord.ButtonStyle.primary, emoji="✅", row=0)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        view = VerifySetupPickerView(self.owner_id, self.cfg)
        await _send_or_edit(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="VC Verify", style=discord.ButtonStyle.secondary, emoji="🔊", row=0)
    async def vc_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        view = VoiceVerifySetupPickerView(self.owner_id, self.cfg)
        await _send_or_edit(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="🗃️", row=1)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        view = LogSetupPickerView(self.owner_id, self.cfg)
        await _send_or_edit(interaction, embed=view.embed(guild), view=view)

    @discord.ui.button(label="Refresh Summary", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        self.cfg = cfg
        await _send_or_edit(interaction, embed=_wizard_embed(guild, cfg), view=self)


class TicketSetupPickerView(_OwnerView):
    def __init__(self, owner_id: int, cfg: Any):
        super().__init__(owner_id)
        self.cfg = cfg
        self.state: Dict[str, Any] = {
            "ticket_category_id": getattr(cfg, "ticket_category_id", None),
            "ticket_archive_category_id": getattr(cfg, "ticket_archive_category_id", None),
            "staff_role_id": getattr(cfg, "staff_role_id", None),
            "transcripts_channel_id": getattr(cfg, "transcripts_channel_id", None),
        }
        self.add_item(_ChannelPick(self.state, "ticket_category_id", "Open ticket category", [discord.ChannelType.category], row=0))
        self.add_item(_ChannelPick(self.state, "ticket_archive_category_id", "Archive/closed ticket category", [discord.ChannelType.category], required=False, row=1))
        self.add_item(_RolePick(self.state, "staff_role_id", "Ticket staff role", row=2))
        self.add_item(_ChannelPick(self.state, "transcripts_channel_id", "Transcript text channel", [discord.ChannelType.text], required=False, row=3))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="🎫 Ticket Setup Picker",
            description="Choose the ticket categories, staff role, and transcript channel from dropdowns. Then press **Save Tickets**.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected / Existing Values",
            value="\n".join(
                [
                    _summary_line(guild, "Open ticket category", self.state.get("ticket_category_id")),
                    _summary_line(guild, "Archive category", self.state.get("ticket_archive_category_id")),
                    _summary_line(guild, "Staff role", self.state.get("staff_role_id"), role=True),
                    _summary_line(guild, "Transcripts", self.state.get("transcripts_channel_id")),
                ]
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Save Tickets", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        await safe_defer(interaction, ephemeral=True)

        ticket_category = _category_channel(guild, self.state.get("ticket_category_id"))
        archive_category = _category_channel(guild, self.state.get("ticket_archive_category_id"))
        staff_role = _get_role(guild, self.state.get("staff_role_id"))
        transcripts_channel = _text_channel(guild, self.state.get("transcripts_channel_id"))

        blockers: list[str] = []
        if ticket_category is None:
            blockers.append("Open ticket category is required.")
        if staff_role is None:
            blockers.append("Ticket staff role is required.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 Ticket Setup Blocked", blockers, [], [])

        assert ticket_category is not None
        assert staff_role is not None
        validation_blockers, warnings, ok = _validate_ticket_setup(guild, ticket_category, staff_role, archive_category, transcripts_channel)
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Ticket Setup Blocked", validation_blockers, warnings, ok)

        updates = {
            "ticket_category_id": _channel_value(ticket_category),
            "ticket_archive_category_id": _channel_value(archive_category),
            "staff_role_id": _role_value(staff_role),
            "vc_staff_role_id": _role_value(staff_role),
            "transcripts_channel_id": _channel_value(transcripts_channel),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
        await upsert_guild_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Ticket Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHomeView(self.owner_id, cfg)
        await _send_or_edit(interaction, embed=_wizard_embed(guild, cfg), view=view)


class VerifySetupPickerView(_OwnerView):
    def __init__(self, owner_id: int, cfg: Any):
        super().__init__(owner_id)
        self.cfg = cfg
        self.state: Dict[str, Any] = {
            "verify_channel_id": getattr(cfg, "verify_channel_id", None),
            "unverified_role_id": getattr(cfg, "unverified_role_id", None),
            "verified_role_id": getattr(cfg, "verified_role_id", None),
            "resident_role_id": getattr(cfg, "resident_role_id", None),
        }
        self.add_item(_ChannelPick(self.state, "verify_channel_id", "Verification start text channel", [discord.ChannelType.text], row=0))
        self.add_item(_RolePick(self.state, "unverified_role_id", "Unverified role", row=1))
        self.add_item(_RolePick(self.state, "verified_role_id", "Verified role", row=2))
        self.add_item(_RolePick(self.state, "resident_role_id", "Resident/member role", required=False, row=3))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Verification Setup Picker",
            description=(
                "Choose the public text channel where users start/read verification, then choose roles.\n"
                "VC verification is on the separate **VC Verify** picker."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected / Existing Values",
            value="\n".join(
                [
                    _summary_line(guild, "Verify start text channel", self.state.get("verify_channel_id")),
                    _summary_line(guild, "Unverified role", self.state.get("unverified_role_id"), role=True),
                    _summary_line(guild, "Verified role", self.state.get("verified_role_id"), role=True),
                    _summary_line(guild, "Resident role", self.state.get("resident_role_id"), role=True),
                ]
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Save Verification", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        await safe_defer(interaction, ephemeral=True)

        verify_channel = _text_channel(guild, self.state.get("verify_channel_id"))
        unverified_role = _get_role(guild, self.state.get("unverified_role_id"))
        verified_role = _get_role(guild, self.state.get("verified_role_id"))
        resident_role = _get_role(guild, self.state.get("resident_role_id"))
        cfg = await get_guild_config(guild.id, refresh=True)
        vc_verify_channel = _voice_channel(guild, getattr(cfg, "vc_verify_channel_id", 0))
        vc_queue_channel = _text_channel(guild, getattr(cfg, "vc_verify_queue_channel_id", 0))

        blockers: list[str] = []
        if verify_channel is None:
            blockers.append("Verification start text channel is required.")
        if unverified_role is None:
            blockers.append("Unverified role is required.")
        if verified_role is None:
            blockers.append("Verified role is required.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", blockers, [], [])

        assert verify_channel is not None
        assert unverified_role is not None
        assert verified_role is not None
        validation_blockers, warnings, ok = _validate_verify_setup(
            guild,
            verify_channel,
            unverified_role,
            verified_role,
            resident_role,
            vc_verify_channel,
            vc_queue_channel,
        )
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", validation_blockers, warnings, ok)

        updates = {
            "verify_channel_id": _channel_value(verify_channel),
            "unverified_role_id": _role_value(unverified_role),
            "verified_role_id": _role_value(verified_role),
            "resident_role_id": _role_value(resident_role),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
        await upsert_guild_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Verification Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHomeView(self.owner_id, cfg)
        await _send_or_edit(interaction, embed=_wizard_embed(guild, cfg), view=view)


class VoiceVerifySetupPickerView(_OwnerView):
    def __init__(self, owner_id: int, cfg: Any):
        super().__init__(owner_id)
        self.cfg = cfg
        self.state: Dict[str, Any] = {
            "vc_verify_channel_id": getattr(cfg, "vc_verify_channel_id", None),
            "vc_verify_queue_channel_id": getattr(cfg, "vc_verify_queue_channel_id", None),
        }
        self.add_item(_ChannelPick(self.state, "vc_verify_channel_id", "VC verify voice/stage channel", _voice_channel_types(), required=False, row=0))
        self.add_item(_ChannelPick(self.state, "vc_verify_queue_channel_id", "VC queue/status text channel", [discord.ChannelType.text], required=False, row=1))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="🔊 VC Verification Picker",
            description="Choose the voice/stage channel used for VC verification and the text channel used for VC status/requests.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected / Existing Values",
            value="\n".join(
                [
                    _summary_line(guild, "VC verify channel", self.state.get("vc_verify_channel_id")),
                    _summary_line(guild, "VC queue/status text channel", self.state.get("vc_verify_queue_channel_id")),
                ]
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Save VC Verify", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        await safe_defer(interaction, ephemeral=True)

        cfg = await get_guild_config(guild.id, refresh=True)
        verify_channel = _text_channel(guild, getattr(cfg, "verify_channel_id", 0))
        unverified_role = _get_role(guild, getattr(cfg, "unverified_role_id", 0))
        verified_role = _get_role(guild, getattr(cfg, "verified_role_id", 0))
        resident_role = _get_role(guild, getattr(cfg, "resident_role_id", 0))
        vc_verify_channel = _voice_channel(guild, self.state.get("vc_verify_channel_id"))
        vc_queue_channel = _text_channel(guild, self.state.get("vc_verify_queue_channel_id"))

        blockers: list[str] = []
        if verify_channel is None:
            blockers.append("Save the Verification picker first so the verify text channel is configured.")
        if unverified_role is None or verified_role is None:
            blockers.append("Save the Verification picker first so required verification roles are configured.")
        if blockers:
            return await _send_blocked_setup(interaction, "🚫 VC Verification Setup Blocked", blockers, [], [])

        assert verify_channel is not None
        assert unverified_role is not None
        assert verified_role is not None
        validation_blockers, warnings, ok = _validate_verify_setup(
            guild,
            verify_channel,
            unverified_role,
            verified_role,
            resident_role,
            vc_verify_channel,
            vc_queue_channel,
        )
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 VC Verification Setup Blocked", validation_blockers, warnings, ok)

        updates = {
            "vc_verify_channel_id": _channel_value(vc_verify_channel),
            "vc_verify_queue_channel_id": _channel_value(vc_queue_channel),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
        await upsert_guild_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ VC Verification Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHomeView(self.owner_id, cfg)
        await _send_or_edit(interaction, embed=_wizard_embed(guild, cfg), view=view)


class LogSetupPickerView(_OwnerView):
    def __init__(self, owner_id: int, cfg: Any):
        super().__init__(owner_id)
        self.cfg = cfg
        self.state: Dict[str, Any] = {
            "modlog_channel_id": getattr(cfg, "modlog_channel_id", None),
            "raidlog_channel_id": getattr(cfg, "raidlog_channel_id", None),
            "join_log_channel_id": getattr(cfg, "join_log_channel_id", None),
            "force_verify_log_channel_id": getattr(cfg, "force_verify_log_channel_id", None),
        }
        self.add_item(_ChannelPick(self.state, "modlog_channel_id", "Modlog text channel", [discord.ChannelType.text], row=0))
        self.add_item(_ChannelPick(self.state, "raidlog_channel_id", "Raid/security log text channel", [discord.ChannelType.text], required=False, row=1))
        self.add_item(_ChannelPick(self.state, "join_log_channel_id", "Join/exit log text channel", [discord.ChannelType.text], required=False, row=2))
        self.add_item(_ChannelPick(self.state, "force_verify_log_channel_id", "Forced verification log text channel", [discord.ChannelType.text], required=False, row=3))

    def embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="🗃️ Log Setup Picker",
            description="Choose log channels from dropdowns. You can reuse the same channel for multiple log types.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected / Existing Values",
            value="\n".join(
                [
                    _summary_line(guild, "Modlog", self.state.get("modlog_channel_id")),
                    _summary_line(guild, "Raid/security log", self.state.get("raidlog_channel_id")),
                    _summary_line(guild, "Join/exit log", self.state.get("join_log_channel_id")),
                    _summary_line(guild, "Forced verification log", self.state.get("force_verify_log_channel_id")),
                ]
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Save Logs", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        await safe_defer(interaction, ephemeral=True)

        modlog_channel = _text_channel(guild, self.state.get("modlog_channel_id"))
        raidlog_channel = _text_channel(guild, self.state.get("raidlog_channel_id"))
        join_log_channel = _text_channel(guild, self.state.get("join_log_channel_id"))
        force_verify_log_channel = _text_channel(guild, self.state.get("force_verify_log_channel_id"))

        if modlog_channel is None:
            return await _send_blocked_setup(interaction, "🚫 Log Setup Blocked", ["Modlog text channel is required."], [], [])

        validation_blockers, warnings, ok = _validate_log_setup(
            guild,
            modlog_channel,
            raidlog_channel,
            join_log_channel,
            force_verify_log_channel,
        )
        if validation_blockers:
            return await _send_blocked_setup(interaction, "🚫 Log Setup Blocked", validation_blockers, warnings, ok)

        updates = {
            "modlog_channel_id": _channel_value(modlog_channel),
            "raidlog_channel_id": _channel_value(raidlog_channel),
            "join_log_channel_id": _channel_value(join_log_channel),
            "force_verify_log_channel_id": _channel_value(force_verify_log_channel),
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
        await upsert_guild_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="✅ Log Setup Saved From Picker")
        _add_validation_summary(embed, warnings, ok)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        view = SetupPickerHomeView(self.owner_id, cfg)
        await _send_or_edit(interaction, embed=_wizard_embed(guild, cfg), view=view)


async def _setup_picker_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    cfg = await get_guild_config(guild.id, refresh=True)
    view = SetupPickerHomeView(int(interaction.user.id), cfg)
    await interaction.followup.send(embed=_wizard_embed(guild, cfg), view=view, ephemeral=True)


def _attach_setup_picker_command() -> None:
    global _SETUP_PICKER_COMMAND_ATTACHED
    if _SETUP_PICKER_COMMAND_ATTACHED:
        return

    try:
        existing = stoney_group.get_command("setup-picker")
    except Exception:
        existing = None
    if existing is not None:
        _SETUP_PICKER_COMMAND_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-picker",
        description="Open an interactive setup wizard with channel and role dropdowns.",
        callback=_setup_picker_callback,
    )
    stoney_group.add_command(command)
    _SETUP_PICKER_COMMAND_ATTACHED = True


apply_public_setup_writer_patch()
_attach_setup_picker_command()


def register_public_setup_picker_commands(bot, tree) -> None:
    _ = bot
    _ = tree
    apply_public_setup_writer_patch()
    _attach_setup_picker_command()
    try:
        print("✅ public_setup_picker: attached /stoney setup-picker interactive dropdown wizard")
    except Exception:
        pass


__all__ = ["register_public_setup_picker_commands"]
