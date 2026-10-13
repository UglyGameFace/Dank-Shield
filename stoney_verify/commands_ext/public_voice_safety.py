from __future__ import annotations

"""Opt-in Voice Safety review controls underneath /dank protection.

This UI does not install a second listener or transcription pipeline. Voice
moderation is a single-guild-session, staff-alert-only use of existing DAVE
captions infrastructure; no automated punishment is enabled.
"""

import asyncio

import discord

from ..community_voice_caption_runtime import (
    ensure_community_voice_caption_manager,
    live_captions_enabled,
    voice_safety_host_enabled,
    voice_safety_host_checks,
    voice_safety_scope_id,
)
from ..community_voice_receive import VoiceReceiveUnavailable, voice_receive_capability
from ..voice_safety_coverage import (
    ALL, SINGLE, COVERAGE_KEY, channel_in_scope, coverage_mode, qualifying_channels,
)
from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from ..ui.resource_browser import DankGuildResourceBrowserView
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
_RESTORE_TASK: asyncio.Task[None] | None = None


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
    checks = voice_safety_host_checks()
    missing = tuple(key for key, ready in checks.items() if not ready)
    if missing:
        return False, (
            "Host configuration incomplete: " + ", ".join(missing) + ". "
            "Enable each only after confirming DAVE receive, paid Gemini API data terms "
            "and eligible adult-only deployment. API key presence does not prove it works."
        )
    if not voice_safety_host_enabled() or not live_captions_enabled():
        return False, "Voice Safety remains host-locked; review your Discloud environment and restart."
    cap = voice_receive_capability()
    if not cap.available:
        return False, f"Voice receiver unavailable: {cap.reason}"
    if coverage_mode(config) == ALL:
        voice = next(
            (vc for vc in tuple(getattr(guild, "voice_channels", ()) or ())
             if channel_in_scope(guild, config, vc)),
            None,
        )
    else:
        voice = _channel(guild, _id(config, VOICE_SAFETY_VC), discord.VoiceChannel)
    staff = _channel(guild, _id(config, VOICE_SAFETY_STAFF), discord.TextChannel)
    notice = _channel(guild, _id(config, VOICE_SAFETY_NOTICE), discord.TextChannel)
    if voice is None or staff is None or notice is None:
        return False, "Select accessible VC coverage, a private staff-alert channel, and a member-visible notice channel."
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
    scope = coverage_mode(cfg)
    eligible = qualifying_channels(guild, cfg)
    staff = _id(cfg, VOICE_SAFETY_STAFF)
    notice = _id(cfg, VOICE_SAFETY_NOTICE)
    embed = discord.Embed(
        title="🛡️ Voice Safety · Staff Review",
        description=(
            "AI conduct review of eligible voice channels once two humans join. "
            "One receiver identity covers only one VC per server at a time; other "
            "qualifying rooms wait for capacity. Only members with a recorded "
            "agreement are transcribed. Google Gemini receives "
            "their voice and text to assess conduct. No automated mutes, kicks or bans. "
            "Staff must verify every finding. Not a guarantee against already-spoken words."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Configuration",
        value=(
            f"Configured: {'Yes' if cfg.get(VOICE_SAFETY_ENABLED) else 'No'}\n"
            f"Voice scope: {'All eligible VCs' if scope == ALL else 'One selected VC'}\n"
            f"Voice: {'Every accessible VC (2+ humans)' if scope == ALL else f'<#{voice}>' if voice else 'Not selected'}\n"
            f"Private staff alerts: {f'<#{staff}>' if staff else 'Not selected'}\n"
            f"Public notice: {f'<#{notice}>' if notice else 'Not selected'}"
        ),
        inline=False,
    )
    checks = voice_safety_host_checks()
    labels = (
        ("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", "DAVE captions switch"),
        ("DANK_VOICE_SAFETY_ENABLED", "Voice Safety switch"),
        ("DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED", "Gemini paid-project confirmation"),
        ("DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED", "Adult-only usage confirmation"),
        ("GEMINI_API_KEY", "Gemini API key configured"),
    )
    embed.add_field(
        name="Host readiness · Discloud",
        value="\n".join(
            f"{'✅' if checks.get(key) else '❌'} **{label}**"
            for key, label in labels
        ) + "\nThese checks show configuration only, not live DAVE/billing verification.",
        inline=False,
    )
    embed.add_field(
        name="Live status",
        value=(
            f"{'🟢 Reviewing opted-in speakers' if running else '⚪ Not running'}\n"
            f"{'Another session owns this server receiver.' if status.get('active') and not running else reason}\n"
            f"Active consented speakers: {len(status.get('opted_in_user_ids') or []) if running else 0}\n"
            f"Qualifying VCs: {len(eligible)}; waiting: {max(0, len(eligible) - (1 if running else 0))}\n"
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
            manager = ensure_community_voice_caption_manager(interaction.client)
            live = manager.status_for_guild(int(guild.id))
            if not live.get("active") or live.get("scope_kind") != "voice_safety":
                return await interaction.followup.send("This Voice Safety notice is no longer active. Staff must start a new review session.", ephemeral=True)
            if int(live.get("voice_safety_notice_message_id", 0) or 0) != int(
                getattr(getattr(interaction, "message", None), "id", 0) or 0
            ):
                return await interaction.followup.send(
                    "This is an older review notice. Use Opt In on the current Voice Safety notice.",
                    ephemeral=True,
                )
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
                "✅ Your one-time Voice Safety agreement is saved for this server. "
                "Your speech in the selected VC may be automatically transcribed by Gemini "
                "and evaluated for staff-only incident alerts. This is separate from Live Captions consent. "
                "This agreement remains until you withdraw it, even after you leave the VC. "
                "Choose Decline & Exit Server to withdraw consent and leave after a 10-second chance to change your mind. This feature requires an operator-confirmed paid Gemini API project and an adult-only monitored space.",
                ephemeral=True,
            )
        await interaction.followup.send(
            "✅ Your Voice Safety agreement was withdrawn. Active audio admission and queued buffers were revoked if monitoring was active. "
            "Any incident alerts already delivered to staff must be handled under server retention policy.",
            ephemeral=True,
        )

    @discord.ui.button(label="Agree Once: Voice Safety", style=discord.ButtonStyle.success, custom_id="dank:voice_safety:consent_on:v1")
    async def opt_in(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change(interaction, True)

    @discord.ui.button(label="Decline & Exit Server", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="dank:voice_safety:consent_off:v1")
    async def opt_out(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Legacy Opt Out buttons still use this custom ID. The explicit
        # withdrawal is immediate; the user receives a 10-second chance to
        # accept before the voluntary server exit. No other member is kicked.
        from stoney_verify.verification_new.decline_exit import decline_and_exit
        await decline_and_exit(interaction, from_verify=False)


class VoiceSafetyEndedView(VoiceSafetyConsentView):
    """Old notices keep opt-out available without advertising a live session."""

    def __init__(self) -> None:
        super().__init__()
        self.opt_in.disabled = True
        self.opt_in.label = "Review ended"


_VOICE_SAFETY_CHANNEL_MODES = {
    "voice": (VOICE_SAFETY_VC, discord.VoiceChannel, "Monitored Voice Channel", ("voice",)),
    "staff": (VOICE_SAFETY_STAFF, discord.TextChannel, "Private Staff Alerts", ("text",)),
    "notice": (VOICE_SAFETY_NOTICE, discord.TextChannel, "Public Member Notice", ("text",)),
}


async def _show_voice_safety_staff_panel(interaction: discord.Interaction, *, owner_id: int) -> None:
    """Return to the canonical manager in the same private Discord message."""
    payload = {
        "embed": await _embed(interaction),
        "view": VoiceSafetyStaffView(owner_id),
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    else:
        await interaction.response.edit_message(**payload)


def _voice_safety_channel_blocker(
    guild: discord.Guild, channel: object, kind: str, actor: discord.abc.User
) -> str:
    """Eligibility is checked on selection, not hidden from Search-Safe browsing."""
    mode = _VOICE_SAFETY_CHANNEL_MODES.get(kind)
    if mode is None or not isinstance(channel, mode[1]):
        return "Select the correct channel type. Voice monitoring requires a normal VC; alerts and notices require text channels."
    if guild.get_channel(int(channel.id)) is not channel:
        return "The selected channel is no longer in this server. Search again."
    me = guild.me
    if me is None:
        return "Dank Shield could not resolve its server permissions."
    bot_permissions = channel.permissions_for(me)
    if kind == "voice":
        if not (bot_permissions.view_channel and bot_permissions.connect):
            return "Dank Shield needs View Channel and Connect in the monitored voice channel."
        return ""
    everyone_can_view = bool(channel.permissions_for(guild.default_role).view_channel)
    if kind == "staff" and everyone_can_view:
        return "This channel is publicly visible to @everyone. Choose a private staff-only channel."
    if kind == "notice" and not everyone_can_view:
        return "This notice channel is hidden from @everyone. Choose a member-visible channel."
    if not (bot_permissions.view_channel and bot_permissions.send_messages):
        return "Dank Shield needs View Channel and Send Messages in the selected text channel."
    if kind == "staff" and not channel.permissions_for(actor).view_channel:
        return "You cannot view this staff channel. Select a channel accessible to your moderation team."
    return ""


async def _open_voice_safety_channel_browser(
    interaction: discord.Interaction, *, owner_id: int, kind: str
) -> None:
    """Use the shared Search-Safe, paginated channel browser, never ChannelSelect."""
    if int(getattr(interaction.user, "id", 0) or 0) != int(owner_id) or not _staff(interaction):
        return await interaction.response.send_message(
            "Open your own Voice Safety settings with Manage Server access.", ephemeral=True
        )
    guild = interaction.guild
    if guild is None or kind not in _VOICE_SAFETY_CHANNEL_MODES:
        return await interaction.response.send_message(
            "Voice Safety channel settings must be opened inside a server.", ephemeral=True
        )
    manager = ensure_community_voice_caption_manager(interaction.client)
    status = manager.status_for_guild(int(guild.id))
    if status.get("active") and status.get("scope_kind") == "voice_safety":
        return await interaction.response.send_message(
            "Stop active Voice Safety before changing its channels.", ephemeral=True
        )

    config_key, expected_type, title, kinds = _VOICE_SAFETY_CHANNEL_MODES[kind]

    async def back(back_interaction: discord.Interaction) -> None:
        await _show_voice_safety_staff_panel(back_interaction, owner_id=owner_id)

    async def picked(pick_interaction: discord.Interaction, resource: object) -> None:
        if (
            pick_interaction.guild is None
            or int(pick_interaction.guild.id) != int(guild.id)
            or int(pick_interaction.user.id) != int(owner_id)
            or not _staff(pick_interaction)
        ):
            return await pick_interaction.response.send_message(
                "You no longer have access to these Voice Safety settings.", ephemeral=True
            )
        live_status = ensure_community_voice_caption_manager(
            pick_interaction.client
        ).status_for_guild(int(guild.id))
        if live_status.get("active") and live_status.get("scope_kind") == "voice_safety":
            return await pick_interaction.response.send_message(
                "Stop the active Voice Safety session before changing channels.", ephemeral=True
            )
        blocker = _voice_safety_channel_blocker(guild, resource, kind, pick_interaction.user)
        if blocker:
            return await pick_interaction.response.send_message(
                f"❌ {blocker} No setting was changed.", ephemeral=True
            )
        await pick_interaction.response.defer()
        try:
            await _save(pick_interaction, {config_key: int(resource.id)})
        except Exception:
            return await pick_interaction.followup.send(
                "❌ Channel selection could not be saved and verified. Try again; monitoring was not started.",
                ephemeral=True,
            )
        await _show_voice_safety_staff_panel(pick_interaction, owner_id=owner_id)

    browser = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(owner_id),
        resource_kinds=kinds,
        on_pick=picked,
        custom_id=f"dank:voice_safety:browse:{kind}:v1",
        title=f"Voice Safety · {title}",
        placeholder="Choose a channel or press Search…",
        on_home=back,
        home_label="Voice Safety settings",
        empty_message=(
            "No channels matched. Try Search using a plain-language or styled name, "
            "a previous name, channel ID, or channel mention."
        ),
    )
    payload = {
        "embed": browser.embed(),
        "view": browser,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    else:
        await interaction.response.edit_message(**payload)


class VoiceSafetyStaffView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id and _staff(interaction):
            return True
        await interaction.response.send_message("Open your own /dank protection Voice Safety panel.", ephemeral=True)
        return False

    @discord.ui.button(label="Monitored Voice Channel", emoji="🔊", style=discord.ButtonStyle.secondary, row=0)
    async def choose_voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_voice_safety_channel_browser(interaction, owner_id=self.owner_id, kind="voice")

    @discord.ui.button(label="VC Coverage: Single / All", emoji="🌐", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_coverage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        manager = ensure_community_voice_caption_manager(interaction.client)
        if manager.status_for_guild(int(guild.id)).get("active"):
            return await interaction.followup.send(
                "Stop the active voice receiver before changing VC coverage.", ephemeral=True
            )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        new_mode = SINGLE if coverage_mode(cfg) == ALL else ALL
        try:
            await _save(interaction, {COVERAGE_KEY: new_mode})
        except Exception:
            return await interaction.followup.send(
                "Could not verify saved VC coverage. No setting was changed.", ephemeral=True
            )
        await interaction.edit_original_response(
            embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id)
        )

    @discord.ui.button(label="Private Staff Alerts", emoji="🔒", style=discord.ButtonStyle.secondary, row=0)
    async def choose_staff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_voice_safety_channel_browser(interaction, owner_id=self.owner_id, kind="staff")

    @discord.ui.button(label="Public Member Notice", emoji="📢", style=discord.ButtonStyle.secondary, row=0)
    async def choose_notice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_voice_safety_channel_browser(interaction, owner_id=self.owner_id, kind="notice")

    @discord.ui.button(label="Enable / Disable Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        cfg = await get_guild_config(int(guild.id), refresh=True)
        manager = ensure_community_voice_caption_manager(interaction.client)
        currently_enabled = bool(cfg.get(VOICE_SAFETY_ENABLED))
        if currently_enabled:
            manager.pause_voice_safety_auto_start(int(guild.id))
        if manager.status_for_guild(int(guild.id)).get("scope_kind") == "voice_safety":
            await manager.stop(voice_safety_scope_id(int(guild.id)))
        try:
            await _save(interaction, {VOICE_SAFETY_ENABLED: not currently_enabled})
            if not currently_enabled:
                manager.pause_voice_safety_auto_start(int(guild.id), False)
        except Exception:
            return await interaction.followup.send("Failed to confirm saved setup state.", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))

    @discord.ui.button(label="Start / Stop Review", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
    async def control(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        manager = ensure_community_voice_caption_manager(interaction.client)
        status = manager.status_for_guild(int(guild.id))
        if status.get("active"):
            if status.get("scope_kind") != "voice_safety":
                return await interaction.followup.send("Another voice/captions session owns this receiver. Stop it first.", ephemeral=True)
            manager.pause_voice_safety_auto_start(int(guild.id))
            await manager.stop(voice_safety_scope_id(int(guild.id)))
            try:
                await _save(interaction, {VOICE_SAFETY_ENABLED: False})
            except Exception:
                return await interaction.followup.send(
                    "⚠️ Voice Safety stopped on this process, but the Disable setting could not be saved. "
                    "Turn off DANK_VOICE_SAFETY_ENABLED on the host before restarting.",
                    ephemeral=True,
                )
            await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))
            return await interaction.followup.send("✅ Voice Safety stopped and automatic restart disabled until setup is enabled again.", ephemeral=True)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        if not bool(cfg.get(VOICE_SAFETY_ENABLED)):
            return await interaction.followup.send("Enable setup first. Monitoring is off by default.", ephemeral=True)
        ready, reason = _readiness(guild, cfg)
        if not ready:
            return await interaction.followup.send(f"❌ {reason}", ephemeral=True)
        candidates = qualifying_channels(guild, cfg)
        if not candidates:
            return await interaction.followup.send(
                "No eligible VC currently has at least two humans and bot Connect access.",
                ephemeral=True,
            )
        manager.pause_voice_safety_auto_start(int(guild.id), False)
        try:
            await manager.start_voice_safety(
                guild_id=int(guild.id),
                voice_channel_id=int(candidates[0].id),
                staff_alert_channel_id=_id(cfg, VOICE_SAFETY_STAFF),
                notice_channel_id=_id(cfg, VOICE_SAFETY_NOTICE),
            )
        except VoiceReceiveUnavailable as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))
        await interaction.followup.send(
            f"✅ Voice Safety reviewing <#{int(candidates[0].id)}>. "
            "Only this VC is receiving audio from this bot identity; other qualifying rooms "
            "are waiting, not monitored. Agreements and host safety gates still apply. "
            "Staff alerts are advisory only.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
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


async def _restore_voice_safety_when_ready(bot: object) -> None:
    global _RESTORE_TASK
    if _RESTORE_TASK is not None and not _RESTORE_TASK.done():
        return
    manager = ensure_community_voice_caption_manager(bot)
    _RESTORE_TASK = asyncio.create_task(
        manager.restore_voice_safety_after_ready(),
        name="dank-voice-safety-ready-restore",
    )


def register_voice_safety_consent(bot: object) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    bot.add_view(VoiceSafetyConsentView())

    async def on_ready_voice_safety() -> None:
        await _restore_voice_safety_when_ready(bot)

    bot.add_listener(on_ready_voice_safety, "on_ready")
    _REGISTERED = True
