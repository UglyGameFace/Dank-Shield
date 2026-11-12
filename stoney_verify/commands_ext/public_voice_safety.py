from __future__ import annotations

"""Opt-in Voice Safety review controls underneath /dank protection.

This UI does not install a second listener or transcription pipeline. Voice
moderation is a single-guild-session, staff-alert-only use of existing DAVE
captions infrastructure; no automated punishment is enabled.
"""

import asyncio
import os

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
from ..voice_safety_policy import (
    VOICE_SAFETY_REACTION_ALERT,
    VOICE_SAFETY_REACTION_KEY,
    VOICE_SAFETY_REACTION_SILENT,
    voice_safety_reaction_mode,
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
            + (
                "Verify the private Watch/Vosk worker, RivaL LFM3 authentication, "
                "individual voice consent, and adult-only deployment."
                if str(os.getenv("DANK_VOICE_SAFETY_BACKEND", "")).strip().lower() == "watch"
                else "Verify DAVE receive, paid Gemini data terms and adult-only deployment."
            ) + " Configuration is not proof that either service works."
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
    if voice is None or staff is None:
        return False, "Select accessible VC coverage and a private staff-alert channel."
    if staff.permissions_for(guild.default_role).view_channel:
        return False, "Staff alerts are publicly visible. Select a channel hidden from @everyone."
    me = guild.me
    if me is None or not (voice.permissions_for(me).connect and voice.permissions_for(me).view_channel):
        return False, "Dank Shield needs Connect and View Channel permissions in the monitored VC."
    perms = staff.permissions_for(me)
    if not perms.view_channel or not perms.send_messages:
        return False, "Dank Shield needs View Channel and Send Messages in the staff channel."
    return True, "Configured. Monitoring starts automatically when two humans join a monitored VC."


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
    reaction = voice_safety_reaction_mode(cfg.get(VOICE_SAFETY_REACTION_KEY))
    reaction_label = (
        "Alerts only" if reaction == VOICE_SAFETY_REACTION_ALERT else "Silent (log only)"
    )
    local_voice = str(os.getenv("DANK_VOICE_SAFETY_BACKEND", "")).strip().lower() == "watch"
    provider = (
        "Watch Yo Mouf (local Vosk) and RivaL Intelligence (local LFM3)"
        if local_voice else "Google Gemini"
    )
    embed = discord.Embed(
        title="🛡️ Voice Safety · Staff Review",
        description=(
            "AI conduct review of eligible voice channels once two humans join. "
            "One Dank Shield DAVE receiver covers one VC per server at a time. "
            "Membership in this server includes Voice Safety monitoring; only "
            "members with a standing explicit decline are excluded. Provider: " + provider + ". "
            "No automatic mutes, kicks or bans. Staff must verify every finding."
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
            f"Reaction on detection: {reaction_label}"
        ),
        inline=False,
    )
    checks = voice_safety_host_checks()
    labels = (
        (
            ("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", "DAVE captions switch"),
            ("DANK_VOICE_SAFETY_ENABLED", "Voice Safety switch"),
            ("DANK_VOICE_SAFETY_LOCAL_ENABLED", "Private local worker explicitly enabled"),
            ("DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED", "Adult-only usage confirmation"),
            ("DANK_WATCH_AUDIO_SECRET", "Private Watch audio secret configured"),
            ("DANK_RIVAL_VOICE_TOKEN", "RivaL local moderation token configured"),
        ) if local_voice else (
            ("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", "DAVE captions switch"),
            ("DANK_VOICE_SAFETY_ENABLED", "Voice Safety switch"),
            ("DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED", "Gemini paid-project confirmation"),
            ("DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED", "Adult-only usage confirmation"),
            ("GEMINI_API_KEY", "Gemini API key configured"),
        )
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
            f"{'🟢 Reviewing members' if running else '⚪ Not running'}\n"
            f"{'Another session owns this server receiver.' if status.get('active') and not running else reason}\n"
            f"Active monitored speakers: {len(status.get('opted_in_user_ids') or []) if running else 0}\n"
            f"Qualifying VCs: {len(eligible)}; waiting: {max(0, len(eligible) - (1 if running else 0))}\n"
            f"Advisory alerts: {status.get('voice_safety_alerts', 0) if running else 0}"
        ),
        inline=False,
    )
    if running:
        health = status.get("health") if isinstance(status.get("health"), dict) else {}
        embed.add_field(
            name="Voice receive health · this session",
            value=(
                f"PCM frames: {int(health.get('frames_routed') or 0)} routed"
                f" • pending: {int(health.get('dispatch_pending') or 0)}"
                f" • peak: {int(health.get('dispatch_pending_peak') or 0)}\n"
                f"Backpressure drops: {int(health.get('dispatch_backpressure_drops') or 0)}"
                f" • queue overflow: {int(status.get('queue_overflow') or 0)}"
                f" • processing skips: {int(status.get('provider_skipped') or 0)}\n"
                f"Receive recoveries: {int(status.get('receive_recoveries') or 0)}"
                f" • failures: {int(status.get('receive_recovery_failures') or 0)}"
            ),
            inline=False,
        )
    embed.set_footer(text="Membership includes monitoring • staff-only advisory alerts • no stored audio")
    return embed


class VoiceSafetyConsentView(discord.ui.View):
    """Persistent public consent button on the posted member notice.

    Membership is the opt-in: no click is needed to be covered. The only
    button is Decline & Exit Server, which withdraws the member and removes
    them from the server.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Decline & Exit Server", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="dank:voice_safety:consent_off:v1")
    async def opt_out(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Legacy Opt Out buttons still use this custom ID. The explicit
        # withdrawal is immediate; the user receives a 10-second chance to
        # accept before the voluntary server exit. No other member is kicked.
        from stoney_verify.verification_new.decline_exit import decline_and_exit
        await decline_and_exit(interaction, from_verify=False)


class VoiceSafetyEndedView(VoiceSafetyConsentView):
    """Old notices keep opt-out available without advertising a live session."""


_VOICE_SAFETY_CHANNEL_MODES = {
    "voice": (VOICE_SAFETY_VC, discord.VoiceChannel, "Monitored Voice Channel", ("voice",)),
    "staff": (VOICE_SAFETY_STAFF, discord.TextChannel, "Private Staff Alerts", ("text",)),
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
        return "Select the correct channel type. Voice monitoring requires a normal VC; alerts require a text channel."
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

    @discord.ui.button(label="Enable / Disable Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        cfg = await get_guild_config(int(guild.id), refresh=True)
        manager = ensure_community_voice_caption_manager(interaction.client)
        currently_enabled = bool(cfg.get(VOICE_SAFETY_ENABLED))

        from ..setup_engine.verification_modes import effective_verification_mode
        basic_verify = effective_verification_mode(guild, cfg) == "basic_button"

        async def refresh_panel(enabled: bool) -> bool:
            if not basic_verify:
                return True
            try:
                from ..startup_guards.basic_verify_panel_auto_refresh_guard import (
                    refresh_basic_verify_panel,
                )
                return await refresh_basic_verify_panel(
                    guild,
                    reason="voice safety setting changed",
                    voice_safety_enabled_override=enabled,
                )
            except Exception:
                return False

        if not currently_enabled and not await refresh_panel(True):
            # Do NOT turn on the config first. An incoming voice-state event
            # could automatically start monitoring before the existing public
            # verification message has the required disclosure.
            await interaction.edit_original_response(
                embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id)
            )
            return await interaction.followup.send(
                "❌ Voice Safety remains OFF: the existing verification panel "
                "could not be updated with its monitoring disclosure. Check "
                "Dank Shield's message permissions in your verification channel.",
                ephemeral=True,
            )

        try:
            await _save(interaction, {VOICE_SAFETY_ENABLED: not currently_enabled})
        except Exception:
            if not currently_enabled:
                restored = await refresh_panel(False)
                if not restored:
                    return await interaction.followup.send(
                        "❌ Voice Safety could not be enabled, and the verification "
                        "panel could not be restored. Monitoring remains disabled; "
                        "check the public message and its permissions.",
                        ephemeral=True,
                    )
            return await interaction.followup.send(
                "Failed to confirm saved Voice Safety setup state.", ephemeral=True
            )

        # Persist OFF before stopping the receiver so automatic startup cannot
        # start another safety session while the previous one is shutting down.
        receiver_stopped = True
        status = manager.status_for_guild(int(guild.id))
        if status.get("active") and status.get("scope_kind") == "voice_safety":
            try:
                await manager.stop(voice_safety_scope_id(int(guild.id)))
                status = manager.status_for_guild(int(guild.id))
                receiver_stopped = not (
                    status.get("active") and status.get("scope_kind") == "voice_safety"
                )
            except Exception:
                receiver_stopped = False

        # When disabling, remove the agreement AND decline button from the same
        # existing panel after the disabled setting is durably confirmed.
        panel_refreshed = True
        if currently_enabled:
            panel_refreshed = await refresh_panel(False)

        if not currently_enabled and receiver_stopped:
            # Enabling starts monitoring after the disclosure is visible.
            await manager.maybe_auto_start_voice_safety(guild)

        await interaction.edit_original_response(
            embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id)
        )
        if not receiver_stopped:
            await interaction.followup.send(
                "⚠️ Voice Safety setting was saved, but the active receiver "
                "could not be confirmed stopped. Check live status immediately.",
                ephemeral=True,
            )
        elif currently_enabled and basic_verify and not panel_refreshed:
            await interaction.followup.send(
                "⚠️ Voice Safety is OFF, but the old agreement could not be "
                "removed from the public verification panel. Check Dank Shield's "
                "message permissions and refresh the panel.",
                ephemeral=True,
            )

    @discord.ui.button(label="Start / Stop Review", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
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
            return await interaction.followup.send(
                "✅ Voice Safety stopped for this session. It starts again automatically "
                "when two humans are in a monitored VC — use Enable / Disable Setup to turn it off.",
                ephemeral=True,
            )
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
        try:
            await manager.start_voice_safety(
                guild_id=int(guild.id),
                voice_channel_id=int(candidates[0].id),
                staff_alert_channel_id=_id(cfg, VOICE_SAFETY_STAFF),
            )
        except VoiceReceiveUnavailable as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))
        await interaction.followup.send(
            f"✅ Voice Safety reviewing <#{int(candidates[0].id)}>. "
            "Only this VC is receiving audio from this bot identity; other qualifying rooms "
            "are waiting, not monitored. Membership includes monitoring; only a standing "
            "explicit decline excludes a member. Host safety gates still apply. "
            "Staff alerts are advisory only.",
            ephemeral=True,
        )

    @discord.ui.button(label="Reaction: Alerts / Silent", emoji="🔔", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_reaction(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        cfg = await get_guild_config(int(guild.id), refresh=True)
        current = voice_safety_reaction_mode(cfg.get(VOICE_SAFETY_REACTION_KEY))
        nxt = (
            VOICE_SAFETY_REACTION_SILENT
            if current == VOICE_SAFETY_REACTION_ALERT
            else VOICE_SAFETY_REACTION_ALERT
        )
        try:
            await _save(interaction, {VOICE_SAFETY_REACTION_KEY: nxt})
        except Exception:
            return await interaction.followup.send("Could not save the reaction setting. No setting was changed.", ephemeral=True)
        await interaction.edit_original_response(embed=await _embed(interaction), view=VoiceSafetyStaffView(self.owner_id))

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
