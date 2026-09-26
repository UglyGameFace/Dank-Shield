from __future__ import annotations

"""General-purpose server Live Captions UI.

This surface deliberately reuses the single CommunityVoiceCaptionManager and
hardened per-speaker DAVE receive path. It does not create a second voice
receiver, consent store, or transcription engine.
"""

from typing import Any, Optional

import discord

from ..community_voice_caption_runtime import (
    ensure_community_voice_caption_manager,
    live_captions_enabled,
    server_caption_scope_id,
)
from ..community_voice_receive import VoiceReceiveUnavailable, voice_receive_capability
from ..interaction_guard import safe_defer_interaction
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS, private_menu_lifecycle_text
from .public_owner_authority import (
    interaction_has_administrator_authority,
    interaction_has_manage_guild_authority,
    interaction_is_actual_guild_owner,
)


def _staff_authorized(interaction: discord.Interaction) -> bool:
    return bool(
        interaction_is_actual_guild_owner(interaction)
        or interaction_has_administrator_authority(interaction)
        or interaction_has_manage_guild_authority(interaction)
    )


def _current_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
    return channel if isinstance(channel, discord.VoiceChannel) else None


async def _defer_update(interaction: discord.Interaction) -> None:
    if await safe_defer_interaction(
        interaction,
        ephemeral=False,
        action_name="server_live_captions_component",
    ):
        return
    raise RuntimeError("Live Captions interaction acknowledgement failed")


async def _followup(interaction: discord.Interaction, content: str) -> None:
    await interaction.followup.send(
        content,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _edit_original(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    await interaction.edit_original_response(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "Open your own `/dank home` panel to control your Live Captions consent.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return False


def build_server_live_captions_embed(
    interaction: discord.Interaction,
) -> discord.Embed:
    guild = interaction.guild
    if guild is None:
        return discord.Embed(
            title="📝 Live Captions",
            description="Live Captions are available inside Discord servers.",
            color=discord.Color.blurple(),
        )

    manager = ensure_community_voice_caption_manager(interaction.client)
    capability = voice_receive_capability()
    enabled = live_captions_enabled()
    general_sid = server_caption_scope_id(int(guild.id))
    general = manager.status(general_sid)
    guild_status = manager.status_for_guild(int(guild.id))
    current_voice = _current_voice_channel(interaction)

    if not enabled:
        headline = (
            "🟡 **Installed, validation locked.** The live DAVE receive path is deployed, "
            "but transcription remains disabled until the real Discord soak test is completed. "
            "No voice audio is captured while this lock is active."
        )
    elif not capability.available:
        headline = f"🔴 **Host not ready.** {capability.reason}."
    elif bool(general.get("active")):
        headline = "🟢 **General Live Captions are running in this server.**"
    elif bool(guild_status.get("active")):
        headline = (
            "🟠 **Another Live Captions session already owns this server's voice receiver.** "
            "Only one caption receiver may run per server at a time."
        )
    else:
        headline = (
            "🟢 **Ready for general server use.** A server owner/admin/Manage Server member "
            "can join a voice channel and start captions from this panel."
        )

    embed = discord.Embed(
        title="📝 Dank Shield Live Captions",
        description=(
            f"{headline}\n\n"
            "This works with ordinary Discord voice channels. It does **not** require a Community Hub gaming session."
        ),
        color=discord.Color.blurple(),
    )

    if bool(general.get("active")):
        voice_id = int(general.get("voice_channel_id") or 0)
        destination_id = int(general.get("destination_channel_id") or 0)
        opted = {
            int(value)
            for value in (general.get("opted_in_user_ids") or [])
            if str(value).isdigit()
        }
        user_opted = int(interaction.user.id) in opted
        embed.add_field(
            name="Current session",
            value=(
                f"Voice: <#{voice_id}>\n"
                f"Captions: <#{destination_id}>\n"
                f"Opted-in speakers: **{len(opted)}**\n"
                f"Your voice: **{'opted in' if user_opted else 'not opted in'}**"
            ),
            inline=False,
        )
    elif bool(guild_status.get("active")):
        voice_id = int(guild_status.get("voice_channel_id") or 0)
        scope_kind = str(guild_status.get("scope_kind") or "")
        owner_name = "Community Hub Live Captions" if scope_kind == "community_hub" else "another Live Captions session"
        embed.add_field(
            name="Voice receiver in use",
            value=f"{owner_name} currently owns <#{voice_id}>. End that caption session before starting general captions.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Voice target",
            value=(
                f"You are currently in <#{int(current_voice.id)}>."
                if current_voice is not None
                else "Join the voice channel you want captioned before pressing **Start / Stop Captions**."
            ),
            inline=False,
        )

    embed.add_field(
        name="How it works",
        value=(
            "1. Staff joins the voice channel and presses **Start / Stop Captions**.\n"
            "2. Each participant opens **/dank home → Live Captions**.\n"
            "3. Each participant presses **Caption My Voice** for their own voice only.\n"
            "4. Captions post in the text channel where staff started the session."
        ),
        inline=False,
    )
    embed.add_field(
        name="Speaker isolation & privacy",
        value=(
            "Discord speakers stay isolated before transcription. Overlapping users are not mixed together. "
            "Opting out immediately blocks new audio and purges that speaker's buffered/queued/in-flight caption audio. "
            "Opted-in audio is sent to OpenAI's transcription API for speech-to-text; Dank Shield itself does not save it. "
            "If a microphone already captures a TV, game audio, or another person in the same room, that sound is already part "
            "of that Discord user's source stream."
        ),
        inline=False,
    )
    embed.add_field(name="Control lifetime", value=private_menu_lifecycle_text(), inline=False)
    return embed


class ServerLiveCaptionsView(_OwnedView):
    @discord.ui.button(
        label="Start / Stop Captions",
        emoji="🎙️",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:server:control:v1",
        row=0,
    )
    async def control(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "Live Captions can only run inside a server.")
        if not _staff_authorized(interaction):
            return await _followup(
                interaction,
                "Only the server owner, an administrator, or someone with Manage Server can start or stop general Live Captions.",
            )

        manager = ensure_community_voice_caption_manager(interaction.client)
        sid = server_caption_scope_id(int(guild.id))
        status = manager.status(sid)

        if bool(status.get("active")):
            await manager.stop(sid, announce=True)
            await _edit_original(
                interaction,
                embed=build_server_live_captions_embed(interaction),
                view=ServerLiveCaptionsView(self.owner_id),
            )
            return await _followup(
                interaction,
                "✅ General Live Captions stopped. All speaker consent and buffered caption audio were cleared.",
            )

        other = manager.status_for_guild(int(guild.id))
        if bool(other.get("active")):
            return await _followup(
                interaction,
                "❌ Another Live Captions session already owns this server's voice receiver. End it before starting general captions.",
            )

        voice = _current_voice_channel(interaction)
        if voice is None:
            return await _followup(
                interaction,
                "Join the ordinary voice channel you want captioned, then press **Start / Stop Captions** again.",
            )
        destination_id = int(interaction.channel_id or 0)
        if destination_id <= 0:
            return await _followup(
                interaction,
                "Open this panel from the text channel where captions need to appear.",
            )

        try:
            await manager.start_server(
                guild_id=int(guild.id),
                voice_channel_id=int(voice.id),
                destination_channel_id=destination_id,
            )
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _edit_original(
            interaction,
            embed=build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )
        await _followup(
            interaction,
            "✅ General Live Captions started. Nobody is transcribed automatically. Each speaker must open **/dank home → Live Captions** and press **Caption My Voice**.",
        )

    @discord.ui.button(
        label="Caption My Voice",
        emoji="📝",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:consent:v1",
        row=0,
    )
    async def consent(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "Live Captions can only run inside a server.")

        manager = ensure_community_voice_caption_manager(interaction.client)
        sid = server_caption_scope_id(int(guild.id))
        status = manager.status(sid)
        if not bool(status.get("active")):
            other = manager.status_for_guild(int(guild.id))
            if bool(other.get("active")) and str(other.get("scope_kind") or "") == "community_hub":
                return await _followup(
                    interaction,
                    "Community Hub Live Captions are running instead. Use that Community Hub session's **Caption My Voice** control.",
                )
            return await _followup(
                interaction,
                "General Live Captions are not running in this server right now.",
            )

        voice = _current_voice_channel(interaction)
        target_voice_id = int(status.get("voice_channel_id") or 0)
        if voice is None or int(voice.id) != target_voice_id:
            return await _followup(
                interaction,
                f"Join <#{target_voice_id}> before opting your voice into this caption session.",
            )

        try:
            enabled = await manager.toggle_consent(sid, int(interaction.user.id))
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _edit_original(
            interaction,
            embed=build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )
        if enabled:
            return await _followup(
                interaction,
                "✅ Your voice is opted in. Your Discord speaker stream stays separate from other users before it is sent to OpenAI's transcription API. Dank Shield itself does not save the audio.",
            )
        await _followup(
            interaction,
            "✅ Your voice is opted out. New audio is blocked and your buffered/queued/in-flight caption audio was purged.",
        )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:refresh:v1",
        row=0,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="Dank Shield Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:home:v1",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        from .public_command_surface_v2 import CompactDankHomeView, _home_embed

        await _edit_original(
            interaction,
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
        )

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:server:close:v1",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            await _edit_original(
                interaction,
                content="Live Captions panel closed.",
                embed=None,
                view=None,
            )


async def open_server_live_captions(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    if interaction.guild is None:
        if interaction.response.is_done():
            return await interaction.followup.send(
                "Live Captions are available inside Discord servers.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return await interaction.response.send_message(
            "Live Captions are available inside Discord servers.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    embed = build_server_live_captions_embed(interaction)
    view = ServerLiveCaptionsView(int(interaction.user.id))
    if replace_message:
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if interaction.response.is_done():
        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


__all__ = [
    "ServerLiveCaptionsView",
    "build_server_live_captions_embed",
    "open_server_live_captions",
]
