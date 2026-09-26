from __future__ import annotations

"""General-purpose server Live Captions UI.

This surface deliberately reuses the single CommunityVoiceCaptionManager and
hardened per-speaker DAVE receive path. It does not create a second voice
receiver, consent store, or transcription engine.
"""

from typing import Any, Optional

import discord

from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..ui.picker import DankChannelSelect
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


CAPTION_OUTPUT_CHANNEL_KEY = "live_captions_output_channel_id"
CAPTION_VOICE_SCOPE_KEY = "live_captions_voice_scope_mode"
CAPTION_ALLOWED_VOICE_CHANNELS_KEY = "live_captions_allowed_voice_channel_ids"
CAPTION_ALLOWED_VOICE_CATEGORIES_KEY = "live_captions_allowed_voice_category_ids"
CAPTION_EXCLUDED_VOICE_CHANNELS_KEY = "live_captions_excluded_voice_channel_ids"


def _id_set(value: Any) -> set[int]:
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        text = str(value or "").strip()
        if not text:
            return set()
        items = [part.strip() for part in text.replace(";", ",").split(",")]
    out: set[int] = set()
    for item in items:
        try:
            parsed = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out.add(parsed)
    return out


def _scope_mode(cfg: Any) -> str:
    value = str(getattr(cfg, "get", lambda *_: "all")(CAPTION_VOICE_SCOPE_KEY, "all") or "all").strip().lower()
    return "selected" if value == "selected" else "all"


def _configured_output_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    try:
        channel_id = int(str(cfg.get(CAPTION_OUTPUT_CHANNEL_KEY) or "0"))
    except (TypeError, ValueError, AttributeError):
        return None
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _voice_allowed_by_config(
    voice: discord.VoiceChannel,
    cfg: Any,
) -> tuple[bool, str]:
    voice_id = int(voice.id)
    excluded = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))
    if voice_id in excluded:
        return False, "That voice channel is explicitly excluded from Live Captions."

    if _scope_mode(cfg) == "all":
        return True, ""

    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    category_id = int(getattr(voice, "category_id", 0) or 0)
    if voice_id in allowed_channels or (category_id > 0 and category_id in allowed_categories):
        return True, ""
    return False, "That voice channel is not included in this server's selected Live Captions scope."


def _target_lines(guild: discord.Guild, ids: set[int], *, category: bool = False) -> str:
    if not ids:
        return "None"
    lines: list[str] = []
    for target_id in sorted(ids):
        target = guild.get_channel(target_id)
        if category and isinstance(target, discord.CategoryChannel):
            lines.append(f"• **{target.name}**")
        elif not category and isinstance(target, discord.VoiceChannel):
            lines.append(f"• {target.mention}")
        else:
            lines.append(f"• Missing/deleted ID `{target_id}`")
        if len(lines) >= 8:
            remaining = len(ids) - len(lines)
            if remaining > 0:
                lines.append(f"• …and {remaining} more")
            break
    return "\n".join(lines)


async def _save_caption_config(
    interaction: discord.Interaction,
    updates: dict[str, Any],
) -> Any:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("Live Captions setup must be used inside a server.")
    if not _staff_authorized(interaction):
        raise PermissionError("Manage Server or Administrator is required.")

    patch = {
        "__config_write_mode": "explicit_override",
        "__config_write_source": "live_captions_setup",
        **dict(updates),
    }
    await upsert_guild_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    fresh = await get_guild_config(int(guild.id), refresh=True)

    for key, expected in updates.items():
        actual = fresh.get(key)
        if isinstance(expected, list):
            if _id_set(actual) != _id_set(expected):
                raise RuntimeError(f"Live Captions setting {key} did not persist.")
        elif str(actual or "") != str(expected or ""):
            raise RuntimeError(f"Live Captions setting {key} did not persist.")
    return fresh


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


async def build_server_live_captions_embed(
    interaction: discord.Interaction,
) -> discord.Embed:
    guild = interaction.guild
    if guild is None:
        return discord.Embed(
            title="📝 Live Captions",
            description="Live Captions are available inside Discord servers.",
            color=discord.Color.blurple(),
        )

    cfg = await get_guild_config(int(guild.id), refresh=True)
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

    output = _configured_output_channel(guild, cfg)
    scope = _scope_mode(cfg)
    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    excluded_channels = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))
    if scope == "all":
        scope_text = f"All voice channels{f' except **{len(excluded_channels)}** excluded' if excluded_channels else ''}."
    else:
        scope_text = (
            f"Selected only: **{len(allowed_channels)}** voice channel(s) + "
            f"**{len(allowed_categories)}** voice categor{'y' if len(allowed_categories) == 1 else 'ies'}"
            f"{f' • **{len(excluded_channels)}** excluded' if excluded_channels else ''}."
        )
    embed.add_field(
        name="Server setup",
        value=(
            f"Output: {output.mention if output is not None else '**Not configured**'}\n"
            f"Voice access: {scope_text}\n"
            "Owners/admins can configure or create the output channel from **Setup**. "
            "Community Hub captions remain session-scoped and continue posting to their own session discussion/thread."
        ),
        inline=False,
    )

    embed.add_field(
        name="How it works",
        value=(
            "1. Staff joins the voice channel and presses **Start / Stop Captions**.\n"
            "2. Each participant opens **/dank home → Live Captions**.\n"
            "3. Each participant presses **Caption My Voice** for their own voice only.\n"
            "4. Captions post in the server's configured Live Captions output channel."
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
                embed=await build_server_live_captions_embed(interaction),
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

        cfg = await get_guild_config(int(guild.id), refresh=True)
        destination = _configured_output_channel(guild, cfg)
        if destination is None:
            return await _followup(
                interaction,
                "❌ Live Captions need an output channel first. Open **Setup** and choose an existing text channel or create **#live-captions**.",
            )
        allowed, reason = _voice_allowed_by_config(voice, cfg)
        if not allowed:
            return await _followup(interaction, f"❌ {reason}")

        bot_member = guild.me
        if bot_member is None:
            return await _followup(interaction, "❌ Dank Shield could not resolve its server member permissions.")
        output_perms = destination.permissions_for(bot_member)
        missing_output = [
            label
            for label, ok in (
                ("View Channel", output_perms.view_channel),
                ("Send Messages", output_perms.send_messages),
                ("Read Message History", output_perms.read_message_history),
            )
            if not ok
        ]
        if missing_output:
            return await _followup(
                interaction,
                f"❌ Dank Shield cannot use {destination.mention}: missing {', '.join(missing_output)}.",
            )

        try:
            await manager.start_server(
                guild_id=int(guild.id),
                voice_channel_id=int(voice.id),
                destination_channel_id=int(destination.id),
            )
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
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
            embed=await build_server_live_captions_embed(interaction),
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
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="Setup",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:setup:v1",
        row=1,
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message(
                "Only the server owner, an administrator, or someone with Manage Server can change Live Captions setup.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
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


async def build_server_live_captions_setup_embed(
    interaction: discord.Interaction,
) -> discord.Embed:
    guild = interaction.guild
    if guild is None:
        return discord.Embed(
            title="⚙️ Live Captions Setup",
            description="Live Captions setup is available inside Discord servers.",
            color=discord.Color.blurple(),
        )

    cfg = await get_guild_config(int(guild.id), refresh=True)
    output = _configured_output_channel(guild, cfg)
    mode = _scope_mode(cfg)
    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    excluded_channels = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))

    embed = discord.Embed(
        title="⚙️ Live Captions Setup",
        description=(
            "Configure ordinary server voice captions here. This does not change Community Hub caption routing: "
            "Hub sessions keep using their own session discussion/thread."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Caption output",
        value=(
            output.mention
            if output is not None
            else "Not configured. Choose an existing text channel or create the read-only **#live-captions** channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Voice scope",
        value=(
            "**All voice channels**"
            if mode == "all"
            else "**Selected voice channels/categories only**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Selected voice channels",
        value=_target_lines(guild, allowed_channels),
        inline=False,
    )
    embed.add_field(
        name="Selected voice categories",
        value=_target_lines(guild, allowed_categories, category=True),
        inline=False,
    )
    embed.add_field(
        name="Excluded voice channels",
        value=_target_lines(guild, excluded_channels),
        inline=False,
    )
    embed.add_field(
        name="How selection works",
        value=(
            "**All** lets any ordinary server VC start captions except explicit exclusions. "
            "**Selected** allows a VC when either that exact VC or its category is selected. "
            "Exclusions always win. This makes existing gaming categories such as squad rooms work without Dank Shield creating replacement VCs."
        ),
        inline=False,
    )
    return embed


class CaptionOutputPickerView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)

        async def _picked(
            interaction: discord.Interaction,
            channel: discord.abc.GuildChannel,
        ) -> None:
            if not _staff_authorized(interaction):
                return await _followup(interaction, "❌ Manage Server or Administrator is required.")
            if not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message(
                    "Pick a normal server text channel for caption output.",
                    ephemeral=True,
                )
            await _defer_update(interaction)
            try:
                await _save_caption_config(
                    interaction,
                    {CAPTION_OUTPUT_CHANNEL_KEY: str(int(channel.id))},
                )
            except Exception as exc:
                return await _followup(interaction, f"❌ Could not save the caption output channel: {type(exc).__name__}: {str(exc)[:180]}")
            await _edit_original(
                interaction,
                embed=await build_server_live_captions_setup_embed(interaction),
                view=ServerLiveCaptionsSetupView(self.owner_id),
            )
            await _followup(interaction, f"✅ Live Captions output set to {channel.mention}.")

        self.add_item(
            DankChannelSelect(
                author_id=owner_id,
                on_pick=_picked,
                placeholder="Choose the Live Captions output text channel…",
                channel_types=[discord.ChannelType.text],
                row=0,
            )
        )


class CaptionVoiceRulePickerView(_OwnedView):
    def __init__(self, owner_id: int, *, rule: str) -> None:
        super().__init__(owner_id)
        self.rule = rule

        if rule == "allowed_category":
            placeholder = "Add an allowed voice category…"
            channel_types = [discord.ChannelType.category]
        elif rule == "excluded_channel":
            placeholder = "Exclude a voice channel…"
            channel_types = [discord.ChannelType.voice]
        else:
            placeholder = "Add an allowed voice channel…"
            channel_types = [discord.ChannelType.voice]

        async def _picked(
            interaction: discord.Interaction,
            channel: discord.abc.GuildChannel,
        ) -> None:
            if not _staff_authorized(interaction):
                return await _followup(interaction, "❌ Manage Server or Administrator is required.")
            expected_category = self.rule == "allowed_category"
            if expected_category and not isinstance(channel, discord.CategoryChannel):
                return await interaction.response.send_message("Pick a voice category.", ephemeral=True)
            if not expected_category and not isinstance(channel, discord.VoiceChannel):
                return await interaction.response.send_message("Pick a voice channel.", ephemeral=True)

            await _defer_update(interaction)
            guild = interaction.guild
            if guild is None:
                return await _followup(interaction, "❌ This must be used inside a server.")
            cfg = await get_guild_config(int(guild.id), refresh=True)

            if self.rule == "allowed_category":
                key = CAPTION_ALLOWED_VOICE_CATEGORIES_KEY
            elif self.rule == "excluded_channel":
                key = CAPTION_EXCLUDED_VOICE_CHANNELS_KEY
            else:
                key = CAPTION_ALLOWED_VOICE_CHANNELS_KEY

            values = _id_set(cfg.get(key))
            values.add(int(channel.id))
            updates: dict[str, Any] = {key: [str(value) for value in sorted(values)]}
            if self.rule in {"allowed_channel", "allowed_category"}:
                updates[CAPTION_VOICE_SCOPE_KEY] = "selected"

            try:
                await _save_caption_config(interaction, updates)
            except Exception as exc:
                return await _followup(interaction, f"❌ Could not save the voice rule: {type(exc).__name__}: {str(exc)[:180]}")

            await _edit_original(
                interaction,
                embed=await build_server_live_captions_setup_embed(interaction),
                view=ServerLiveCaptionsSetupView(self.owner_id),
            )
            await _followup(interaction, "✅ Live Captions voice rule saved.")

        self.add_item(
            DankChannelSelect(
                author_id=owner_id,
                on_pick=_picked,
                placeholder=placeholder,
                channel_types=channel_types,
                row=0,
            )
        )


class ServerLiveCaptionsSetupView(_OwnedView):
    @discord.ui.button(
        label="Select Output Channel",
        emoji="📍",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:setup:output:v1",
        row=0,
    )
    async def output(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📍 Choose Caption Output",
                description="Choose the existing text channel where ordinary server Live Captions should post.",
                color=discord.Color.blurple(),
            ),
            view=CaptionOutputPickerView(self.owner_id),
        )

    @discord.ui.button(
        label="Create #live-captions",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="dank:captions:setup:create_output:v1",
        row=0,
    )
    async def create_output(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")

        channel = discord.utils.get(guild.text_channels, name="live-captions")
        created = False
        if channel is None:
            bot_member = guild.me
            if bot_member is None or not bot_member.guild_permissions.manage_channels:
                return await _followup(
                    interaction,
                    "❌ Dank Shield needs **Manage Channels** to create #live-captions. You can still use **Select Output Channel** with an existing channel.",
                )
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                ),
            }
            try:
                channel = await guild.create_text_channel(
                    name="live-captions",
                    overwrites=overwrites,
                    topic="Read-only Dank Shield Live Captions output for ordinary server voice channels.",
                    reason="Dank Shield Live Captions setup",
                )
                created = True
            except (discord.Forbidden, discord.HTTPException) as exc:
                return await _followup(interaction, f"❌ Could not create #live-captions: {type(exc).__name__}: {str(exc)[:180]}")

        try:
            await _save_caption_config(
                interaction,
                {CAPTION_OUTPUT_CHANNEL_KEY: str(int(channel.id))},
            )
        except Exception as exc:
            return await _followup(interaction, f"❌ Caption channel exists but could not be saved: {type(exc).__name__}: {str(exc)[:180]}")

        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        verb = "Created and saved" if created else "Reused and saved"
        await _followup(interaction, f"✅ {verb} {channel.mention}.")

    @discord.ui.button(
        label="Toggle All / Selected",
        emoji="🎚️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:scope:v1",
        row=1,
    )
    async def scope(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")
        cfg = await get_guild_config(int(guild.id), refresh=True)
        new_mode = "selected" if _scope_mode(cfg) == "all" else "all"
        try:
            await _save_caption_config(interaction, {CAPTION_VOICE_SCOPE_KEY: new_mode})
        except Exception as exc:
            return await _followup(interaction, f"❌ Could not save voice scope: {type(exc).__name__}: {str(exc)[:180]}")
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        await _followup(interaction, f"✅ Voice scope is now **{new_mode}**.")

    @discord.ui.button(
        label="Add Allowed VC",
        emoji="🔊",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:add_vc:v1",
        row=1,
    )
    async def add_vc(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🔊 Add Allowed Voice Channel",
                description="Pick one existing voice channel. Repeat this action to add more.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="allowed_channel"),
        )

    @discord.ui.button(
        label="Add Voice Category",
        emoji="🗂️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:add_category:v1",
        row=1,
    )
    async def add_category(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🗂️ Add Allowed Voice Category",
                description="Pick a category such as your gaming/squad-room category. Every voice channel inside it becomes eligible.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="allowed_category"),
        )

    @discord.ui.button(
        label="Exclude VC",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:exclude_vc:v1",
        row=2,
    )
    async def exclude_vc(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🚫 Exclude Voice Channel",
                description="Pick a voice channel that must never start ordinary server Live Captions. Exclusions override All and Selected modes.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="excluded_channel"),
        )

    @discord.ui.button(
        label="Clear Voice Rules",
        emoji="🧹",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:setup:clear_rules:v1",
        row=2,
    )
    async def clear_rules(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        try:
            await _save_caption_config(
                interaction,
                {
                    CAPTION_VOICE_SCOPE_KEY: "all",
                    CAPTION_ALLOWED_VOICE_CHANNELS_KEY: [],
                    CAPTION_ALLOWED_VOICE_CATEGORIES_KEY: [],
                    CAPTION_EXCLUDED_VOICE_CHANNELS_KEY: [],
                },
            )
        except Exception as exc:
            return await _followup(interaction, f"❌ Could not clear voice rules: {type(exc).__name__}: {str(exc)[:180]}")
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        await _followup(interaction, "✅ Voice rules cleared. Ordinary server Live Captions now allow all voice channels.")

    @discord.ui.button(
        label="Live Captions",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:setup:back:v1",
        row=3,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:setup_home:v1",
        row=3,
    )
    async def setup_home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")
        from . import public_setup_start

        embed, view = await public_setup_start._build_main_setup_payload(guild)
        await _edit_original(interaction, embed=embed, view=view)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:setup:close:v1",
        row=4,
    )
    async def close_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            await _edit_original(interaction, content="Live Captions setup closed.", embed=None, view=None)


async def open_server_live_captions_setup(
    interaction: discord.Interaction,
) -> None:
    if interaction.guild is None:
        if interaction.response.is_done():
            return await interaction.followup.send("Live Captions setup is available inside Discord servers.", ephemeral=True)
        return await interaction.response.send_message("Live Captions setup is available inside Discord servers.", ephemeral=True)
    if not _staff_authorized(interaction):
        if interaction.response.is_done():
            return await _followup(interaction, "❌ Manage Server or Administrator is required.")
        return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)

    if not interaction.response.is_done():
        await safe_defer_interaction(
            interaction,
            ephemeral=True,
            action_name="server_live_captions_setup_open",
        )
    await _edit_original(
        interaction,
        embed=await build_server_live_captions_setup_embed(interaction),
        view=ServerLiveCaptionsSetupView(int(interaction.user.id)),
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

    if not interaction.response.is_done():
        await safe_defer_interaction(
            interaction,
            ephemeral=not replace_message,
            action_name="server_live_captions_open",
        )

    embed = await build_server_live_captions_embed(interaction)
    view = ServerLiveCaptionsView(int(interaction.user.id))
    await _edit_original(interaction, content=None, embed=embed, view=view)


async def open_server_live_captions_command(
    interaction: discord.Interaction,
) -> None:
    await open_server_live_captions(interaction, replace_message=False)


__all__ = [
    "CaptionOutputPickerView",
    "CaptionVoiceRulePickerView",
    "ServerLiveCaptionsSetupView",
    "ServerLiveCaptionsView",
    "build_server_live_captions_embed",
    "build_server_live_captions_setup_embed",
    "open_server_live_captions",
    "open_server_live_captions_command",
    "open_server_live_captions_setup",
]
