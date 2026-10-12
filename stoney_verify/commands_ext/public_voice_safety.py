from __future__ import annotations

"""Opt-in Voice Safety review controls underneath /dank protection.

This UI does not install a second listener or transcription pipeline. Voice
moderation is a single-guild-session, staff-alert-only use of existing DAVE
captions infrastructure; no automated punishment is enabled.
"""

import discord

from ..community_voice_caption_runtime import (
    ensure_community_voice_caption_manager,
    live_captions_enabled,
    voice_safety_host_enabled,
    voice_safety_scope_id,
)
from ..community_voice_receive import VoiceReceiveUnavailable, voice_receive_capability
from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_owner_authority import (
    interaction_has_administrator_authority,
    interaction_has_manage_guild_authority,
    interaction_is_actual_guild_owner,
)


VOICE_SAFETY_ENABLED = "voice_safety_enabled"
VOICE_SAFETY_VC = "voice_safety_voice_channel_id"
VOICE_SAFETY_STAFF = "voice_safety_staff_channel_id"
VOICE_SAFETY_NOTICE = "voice_safety_notice_channel_id"
_REGISTERED = False


def _staff(interaction: discord.Interaction) -> bool:
    return bool(
        interaction_is_actual_guild_owner(interaction)
        or interaction_has_administrator_authority(interaction)
        or interaction_has_manage_guild_authority(interaction)
    )


def _id(config: dict, key: str) -> int:
    try:
        return int(config.get(key) or 0)
    except (TypeError, ValueError):
        return 0


async def _save(interaction: discord.Interaction, patch: dict) -> dict:
    guild = interaction.guild
    if guild is None or not _staff(interaction):
        raise PermissionError("Manage Server or Administrator is required.")
    await upsert_guild_config(int(guild.id), {
        "__config_write_mode": "explicit_override",
        "__config_write_source": "voice_safety_setup",
        **patch,
    })
    invalidate_guild_config(int(guild.id))
    saved = await get_guild_config(int(guild.id), refresh=True)
    for key, value in patch.items():
        if saved.get(key) != value and str(saved.get(key)) != str(value):
            raise RuntimeError(f"Could not verify saved Voice Safety setting: {key}")
    return saved


def _channel(guild: discord.Guild, channel_id: int, expected: type) -> object | None:
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, expected) else None


def _readiness(guild: discord.Guild, config: dict) -> tuple[bool, str]:
    if not voice_safety_host_enabled() or not live_captions_enabled():
        return False, "Host is validation-locked. DAVE receive soak and both host feature gates are required."
    cap = voice_receive_capability()
    if not cap.available:
        return False, f"Voice receiver unavailable: {cap.reason}"
    voice = _channel(guild, _id(config, VOICE_SAFETY_VC), discord.VoiceChannel)
    staff = _channel(guild, _id(config, VOICE_SAFETY_STAFF), discord.TextChannel)
    notice = _channel(guild, _id(config, VOICE_SAFETY_NOTICE), discord.TextChannel)
    if voice is None or staff is None or notice is None:
        return False, "Select a VC, a private staff-alert text channel, and a member-visible notice text channel."
    if staff.permissions_for(guild.default_role).view_channel:
        return False, "Staff alerts are publicly visible. Select a channel hidden from @everyone."
    if not notice.permissions_for(guild.default_role).view_channel:
        return False, "The notice channel must be visible to @everyone."
    me = guild.me
    if me is None or not (voice.permissions_for(me).connect and voice.permissions_for(me).view_channel):
        return False, "Dank Shield needs Connect and View Channel permissions in the monitored VC."
    for channel in (staff, notice):
        perms = channel.permissions_for(me)
        if not perms.view_channel or not perms.send_messages:
            return False, "Dank Shield needs View Channel and Send Messages in both text channels."
    return True, "Configured, but monitoring only starts when staff press Start."


async def _embed(interaction: discord.Interaction) -> discord.Embed:
    guild = interaction.guild
    cfg = await get_guild_config(int(guild.id), refresh=True)
    manager = ensure_community_voice_caption_manager(interaction.client)
    status = manager.status_for_guild(int(guild.id))
    ready, reason = _readiness(guild, cfg)
    running = status.get("active") and status.get("scope_kind") == "voice_safety"
    voice = _id(cfg, VOICE_SAFETY_VC)
    staff = _id(cfg, VOICE_SAFETY_STAFF)
    notice = _id(cfg, VOICE_SAFETY_NOTICE)
    embed = discord.Embed(
        title="🛡️ Voice Safety · Staff Review",
        description=(
            "Optional, consented AI conduct review for one voice channel. "
            "Only members who explicitly opt in are transcribed. Google Gemini receives "
            "their voice and text to assess conduct. No automated mutes, kicks or bans. "
            "Staff must verify every finding. Not a guarantee against already-spoken words."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Configuration",
        value=(
            f"Configured: {'Yes' if cfg.get(VOICE_SAFETY_ENABLED) else 'No'}\n"
            f"Voice: {f'<#{voice}>' if voice else 'Not selected'}\n"
            f"Private staff alerts: {f'<#{staff}>' if staff else 'Not selected'}\n"
            f"Public notice: {f'<#{notice}>' if notice else 'Not selected'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Live status",
        value=(
            f"{'🟢 Reviewing opted-in speakers' if running else '⚪ Not running'}\n"
            f"{'Another session owns this server receiver.' if status.get('active') and not running else reason}\n"
            f"Active consented speakers: {len(status.get('opted_in_user_ids') or []) if running else 0}\n"
            f"Advisory alerts: {status.get('voice_safety_alerts', 0) if running else 0}"
        ),
        inline=False,
    )
    embed.set_footer(text="Explicit member consent • staff-only advisory alerts • no stored audio")
    return embed


class VoiceSafetyConsentView(discord.ui.View):
    """Persistent public consent buttons on the posted member notice."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _change(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("Voice Safety consent only works inside a server.", ephemeral=True)
        if getattr(interaction.user, "bot", False):
            return await interaction.followup.send("Bots cannot consent to Voice Safety.", ephemeral=True)
        if enabled:
            cfg = await get_guild_config(int(guild.id), refresh=True)
            if not bool(cfg.get(VOICE_SAFETY_ENABLED)) or _id(cfg, VOICE_SAFETY_NOTICE) != int(interaction.channel_id):
                return await interaction.followup.send("This Voice Safety notice is inactive. Ask staff to post a new one.", ephemeral=True)
            if not (voice_safety_host_enabled() and live_captions_enabled()):
                return await interaction.followup.send("Voice Safety is currently disabled on this host.", ephemeral=True)
        manager = ensure_community_voice_caption_manager(interaction.client)
        try:
            consented = await manager.set_voice_safety_consent(int(guild.id), int(interaction.user.id), enabled)
        except VoiceReceiveUnavailable as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        if consented:
            return await interaction.followup.send(
                "✅ You explicitly opted in to this server's Voice Safety review. "
                "Only your speech in the selected VC during an active session can be transcribed by Gemini "
                "and evaluated for staff-only incident alerts. This is separate from Live Captions consent. "
                "Press Opt Out to revoke consent. Gemini Free Tier content may be used to improve Google's products.",
                ephemeral=True,
            )
        await interaction.followup.send(
            "✅ Opted out. Your voice admission and queued buffers were revoked if monitoring was active. "
            "Any incident alerts already delivered to staff must be handled under server retention policy.",
            ephemeral=True,
        )

    @discord.ui.button(label="Opt In: Review My Voice", style=discord.ButtonStyle.success, custom_id="dank:voice_safety:consent_on:v1")
    async def opt_in(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change(interaction, True)

    @discord.ui.button(label="Opt Out", style=discord.ButtonStyle.secondary, custom_id="dank:voice_safety:consent_off:v1")
    async def opt_out(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change(interaction, False)


class VoiceSafetyChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        key, types, label = {
            "voice": (VOICE_SAFETY_VC, [discord.ChannelType.voice], "Monitored voice channel"),
            "staff": (VOICE_SAFETY_STAFF, [discord.ChannelType.text], "Private staff-alert channel"),
            "notice": (VOICE_SAFETY_NOTICE, [discord.ChannelType.text], "Member-visible notice channel"),
        }[kind]
        self.config_key = key
        super().__init__(
            placeholder=label,
            min_values=1,
            max_values=1,
            channel_types=types,
            custom_id=f"dank:voice_safety:select:{kind}:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if not _staff(interaction):
            return await interaction.followup.send("Manage Server is required.", ephemeral=True)
        manager = ensure_community_voice_caption_manager(interaction.client)
        status = manager.status_for_guild(int(interaction.guild_id))
        if status.get("active") and status.get("scope_kind") == "voice_safety":
            return await interaction.followup.send("Stop active Voice Safety before changing channels.", ephemeral=True)
        try:
            await _save(interaction, {self.config_key: int(self.values[0].id)})
            await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(int(interaction.user.id)))
        except Exception:
            return await interaction.followup.send("Could not verify the selected channel. No new review was started.", ephemeral=True)


class VoiceSafetyStaffView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)
        self.add_item(VoiceSafetyChannelSelect("voice"))
        self.add_item(VoiceSafetyChannelSelect("staff"))
        self.add_item(VoiceSafetyChannelSelect("notice"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id and _staff(interaction):
            return True
        await interaction.response.send_message("Open your own /dank protection Voice Safety panel.", ephemeral=True)
        return False

    @discord.ui.button(label="Enable / Disable Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        cfg = await get_guild_config(int(guild.id), refresh=True)
        manager = ensure_community_voice_caption_manager(interaction.client)
        if manager.status_for_guild(int(guild.id)).get("scope_kind") == "voice_safety":
            await manager.stop(voice_safety_scope_id(int(guild.id)))
        try:
            await _save(interaction, {VOICE_SAFETY_ENABLED: not bool(cfg.get(VOICE_SAFETY_ENABLED))})
        except Exception:
            return await interaction.followup.send("Failed to confirm saved setup state.", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))

    @discord.ui.button(label="Start / Stop Review", emoji="🛡️", style=discord.ButtonStyle.primary, row=3)
    async def control(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        manager = ensure_community_voice_caption_manager(interaction.client)
        status = manager.status_for_guild(int(guild.id))
        if status.get("active"):
            if status.get("scope_kind") != "voice_safety":
                return await interaction.followup.send("Another voice/captions session owns this receiver. Stop it first.", ephemeral=True)
            await manager.stop(voice_safety_scope_id(int(guild.id)))
            await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))
            return await interaction.followup.send("✅ Voice Safety stopped. Existing notices cannot start new sessions.", ephemeral=True)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        if not bool(cfg.get(VOICE_SAFETY_ENABLED)):
            return await interaction.followup.send("Enable setup first. Monitoring is off by default.", ephemeral=True)
        ready, reason = _readiness(guild, cfg)
        if not ready:
            return await interaction.followup.send(f"❌ {reason}", ephemeral=True)
        try:
            await manager.start_voice_safety(
                guild_id=int(guild.id),
                voice_channel_id=_id(cfg, VOICE_SAFETY_VC),
                staff_alert_channel_id=_id(cfg, VOICE_SAFETY_STAFF),
                notice_channel_id=_id(cfg, VOICE_SAFETY_NOTICE),
            )
        except VoiceReceiveUnavailable as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))
        await interaction.followup.send(
            "✅ Consent-only Voice Safety running. A member notice was posted publicly. "
            "Only opted-in voices are evaluated; staff alerts are advisory, not automatic punishments.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))


async def open_voice_safety_center(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not _staff(interaction):
        if interaction.response.is_done():
            return await interaction.followup.send("Manage Server is required.", ephemeral=True)
        return await interaction.response.send_message("Manage Server is required.", ephemeral=True)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        embed=await _embed(interaction),
        view=VoiceSafetyStaffView(int(interaction.user.id)),
    )


def register_voice_safety_consent(bot: object) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    bot.add_view(VoiceSafetyConsentView())
    _REGISTERED = True
