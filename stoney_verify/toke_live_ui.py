from __future__ import annotations

"""Member-first Toke controls and normal Discord-composer media capture."""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

import discord

from stoney_verify import toke_sessions

SETTINGS_KEY = "toke_meetup_settings_v1"
CAPTURE_SECONDS = 90
_DEFAULTS = {
    "session_minutes": 60,
    "user_cooldown_minutes": 15,
    "guild_cooldown_minutes": 5,
    "max_interested": 100,
    "show_member_names": True,
    "voice_channel_id": 0,
}
_PENDING: dict[tuple[int, int], "PendingToke"] = {}
_REGISTERED: set[tuple[int, int]] = set()
_VOICE_REFRESH: dict[int, float] = {}
_STARTED_BOTS: set[int] = set()
_LOOP_TASK: asyncio.Task[None] | None = None


@dataclass
class PendingToke:
    guild_id: int
    user_id: int
    channel_id: int
    destination_id: int
    note: str
    deadline: float
    claimed: bool = False


def settings_from_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    raw = cfg.get(SETTINGS_KEY, {})
    if not isinstance(raw, Mapping):
        raw = {}
    result = dict(_DEFAULTS)
    for key, low, high in (
        ("session_minutes", 15, 240),
        ("user_cooldown_minutes", 0, 1440),
        ("guild_cooldown_minutes", 0, 240),
        ("max_interested", 1, 250),
    ):
        try:
            val = int(raw.get(key, result[key]))
        except (TypeError, ValueError):
            val = int(result[key])
        result[key] = max(low, min(val, high))
    result["show_member_names"] = raw.get("show_member_names", True) is not False
    try:
        result["voice_channel_id"] = max(0, int(raw.get("voice_channel_id", 0) or 0))
    except (TypeError, ValueError):
        result["voice_channel_id"] = 0
    return result


def choose_voice(guild: discord.Guild, member: discord.Member, settings: Mapping[str, Any]) -> int:
    current = getattr(getattr(member, "voice", None), "channel", None)
    if isinstance(current, (discord.VoiceChannel, discord.StageChannel)):
        return int(current.id)
    configured = guild.get_channel(int(settings.get("voice_channel_id", 0) or 0))
    return int(configured.id) if isinstance(configured, (discord.VoiceChannel, discord.StageChannel)) else 0


def _display_members(guild: discord.Guild, identifiers: list[int], limit: int = 10) -> str:
    result = []
    for member_id in identifiers[:limit]:
        member = guild.get_member(int(member_id))
        name = member.display_name if member is not None else f"Member {member_id}"
        safe_name = discord.utils.escape_markdown(discord.utils.escape_mentions(name))[:55]
        result.append(f"{safe_name} (<@{int(member_id)}>)")
    if len(identifiers) > limit:
        result.append(f"and {len(identifiers) - limit} more (tap Who's Coming)")
    return ", ".join(result) if result else "Nobody yet"


def render_card(guild: discord.Guild, session: Mapping[str, Any], original: discord.Embed) -> discord.Embed:
    embed = original.copy()
    embed.clear_fields()
    cheers = [int(x) for x in session.get("cheers_ids", [])]
    going = [int(x) for x in session.get("going_ids", [])]
    visible = bool(session.get("show_member_names", True))
    embed.add_field(
        name=f"💨 Cheers • {len(cheers)}",
        value=_display_members(guild, cheers) if visible else "Participant names are private in this server.",
        inline=False,
    )
    embed.add_field(
        name=f"🙋 Planning to join • {len(going)}",
        value=_display_members(guild, going) if visible else "Names hidden by server settings.",
        inline=False,
    )
    voice = guild.get_channel(int(session.get("voice_channel_id", 0) or 0))
    if isinstance(voice, (discord.VoiceChannel, discord.StageChannel)):
        in_voice = list(getattr(voice, "members", []) or [])
        listed = [int(member.id) for member in in_voice]
        embed.add_field(
            name=f"🔊 In voice now • {len(in_voice)}",
            value=(f"{voice.mention}\n" + (_display_members(guild, listed) if visible else "Names hidden by server settings."))[:1024],
            inline=False,
        )
    else:
        embed.add_field(name="🔊 Voice", value="No voice channel selected. Cheers does not mean someone is in VC.", inline=False)
    active = bool(session.get("active", False))
    embed.add_field(
        name="Session",
        value=(
            f"Ends <t:{int(session.get('expires_at', 0))}:R> • "
            "Cheers = interested; I'm Joining = planning to come."
        ) if active else "This sesh has ended. Start a new /toke to invite everyone again.",
        inline=False,
    )
    embed.color = discord.Color.green() if active else discord.Color.dark_grey()
    return embed


async def refresh_card(bot: Any, session: Mapping[str, Any], interaction: discord.Interaction | None = None) -> None:
    guild = bot.get_guild(int(session["guild_id"]))
    if guild is None:
        return
    channel = guild.get_channel(int(session["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return
    if interaction is not None and interaction.message is not None:
        message = interaction.message
    else:
        message = await channel.fetch_message(int(session["message_id"]))
    existing = (message.embeds or [])
    if not existing:
        return
    view = TokeCheersView(
        int(session["starter_id"]), int(session["starter_role_id"]),
        voice_channel_id=int(session.get("voice_channel_id", 0) or 0),
        active=bool(session.get("active", False)),
        guild_id=int(session["guild_id"]), message_id=int(session["message_id"]),
    )
    await message.edit(embed=render_card(guild, session, existing[0]), view=view, allowed_mentions=discord.AllowedMentions.none())


class TokeRosterView(discord.ui.View):
    def __init__(self, guild: discord.Guild, session: Mapping[str, Any], page: int = 0) -> None:
        super().__init__(timeout=180)
        self.guild = guild
        self.session = dict(session)
        self.page = max(0, int(page))
        self.lines = [
            f"{i+1}. {discord.utils.escape_markdown(discord.utils.escape_mentions((guild.get_member(int(uid)).display_name if guild.get_member(int(uid)) else 'Member ' + str(uid))))[:80]}"
            + (" • joining" if int(uid) in set(int(x) for x in session.get("going_ids", [])) else " • cheers")
            for i, uid in enumerate(session.get("cheers_ids", []))
        ]
        self.pages = max(1, (len(self.lines) + 14) // 15)
        self.page = min(self.page, self.pages - 1)
        for component in self.children:
            if isinstance(component, discord.ui.Button) and component.custom_id == "dank:toke:roster:prev":
                component.disabled = self.page == 0
            if isinstance(component, discord.ui.Button) and component.custom_id == "dank:toke:roster:next":
                component.disabled = self.page + 1 >= self.pages

    def embed(self) -> discord.Embed:
        start = self.page * 15
        return discord.Embed(
            title="💨 Who's Coming?",
            description="\n".join(self.lines[start:start+15]) or "Nobody has responded yet.",
            color=discord.Color.green(),
        ).set_footer(text=f"Page {self.page+1}/{self.pages} • People in VC are listed separately on the session card.")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="dank:toke:roster:prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TokeRosterView(self.guild, self.session, self.page - 1)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="dank:toke:roster:next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = TokeRosterView(self.guild, self.session, self.page + 1)
        await interaction.response.edit_message(embed=view.embed(), view=view)


class TokeCheersView(discord.ui.View):
    def __init__(
        self, starter_id: int, stoner_role_id: int, *,
        voice_channel_id: int = 0, active: bool = True,
        guild_id: int = 0, message_id: int = 0,
    ) -> None:
        super().__init__(timeout=None)
        self.starter_id = int(starter_id)
        self.stoner_role_id = int(stoner_role_id)
        self.guild_id, self.message_id = int(guild_id), int(message_id)
        if voice_channel_id and guild_id:
            self.add_item(discord.ui.Button(
                label="Join VC", emoji="🔊",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{int(guild_id)}/{int(voice_channel_id)}",
                row=1,
            ))
        if not active:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.style is not discord.ButtonStyle.link:
                    item.disabled = True

    async def _respond(self, interaction: discord.Interaction, action: str) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None or interaction.message is None:
            return await interaction.response.send_message("This sesh is no longer available.", ephemeral=True)
        session = await toke_sessions.get(guild.id, interaction.message.id)
        if not session or not session.get("active"):
            return await interaction.response.send_message("This sesh has ended or expired.", ephemeral=True)
        if action == "end":
            if member.id != int(session["starter_id"]) and not member.guild_permissions.manage_guild:
                return await interaction.response.send_message("Only the starter or server managers can end this sesh.", ephemeral=True)
        else:
            role_ids = {int(session["starter_role_id"]), int(session["notify_role_id"])}
            if not any(int(role.id) in role_ids for role in member.roles):
                return await interaction.response.send_message("Join the server's configured Toke community or notification role first.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            updated, error = await toke_sessions.react(
                guild.id, interaction.message.id, member.id, action,
                max_interested=int(session.get("max_interested", 100)),
            )
            if error:
                return await interaction.edit_original_response(content=f"⚠️ {error}")
            await refresh_card(interaction.client, updated, interaction)
        except (OSError, ValueError, discord.HTTPException) as exc:
            return await interaction.edit_original_response(content=f"⚠️ Could not update the sesh. Try again: {type(exc).__name__}")
        await interaction.edit_original_response(content={
            "cheers": "💨 Cheers recorded. Others can see you're interested.",
            "going": "🙋 You're on the joining list. Use Join VC when ready.",
            "withdraw": "✅ You were removed from the interested and joining lists.",
            "end": "✅ Sesh ended.",
        }[action])

    @discord.ui.button(label="Cheers", emoji="💨", style=discord.ButtonStyle.success, custom_id="dank:toke:v2:cheers", row=0)
    async def cheers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._respond(interaction, "cheers")

    @discord.ui.button(label="I'm Joining", emoji="🙋", style=discord.ButtonStyle.primary, custom_id="dank:toke:v2:going", row=0)
    async def going(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._respond(interaction, "going")

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.secondary, custom_id="dank:toke:v2:withdraw", row=0)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._respond(interaction, "withdraw")

    @discord.ui.button(label="Who's Coming?", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="dank:toke:v2:roster", row=0)
    async def roster(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, msg = interaction.guild, interaction.message
        if guild is None or msg is None:
            return
        session = await toke_sessions.get(guild.id, msg.id)
        if not session:
            return await interaction.response.send_message("This session is no longer available.", ephemeral=True)
        if not session.get("show_member_names", True):
            return await interaction.response.send_message("This server keeps participant names private.", ephemeral=True)
        view = TokeRosterView(guild, session)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:toke:v2:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.message is None:
            return
        session = await toke_sessions.get(interaction.guild.id, interaction.message.id)
        if not session:
            return await interaction.response.send_message("No active sesh was found.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await refresh_card(interaction.client, session, interaction)
        except discord.HTTPException:
            return await interaction.edit_original_response(content="Discord could not refresh this card.")
        await interaction.edit_original_response(content="✅ Updated who's interested and who's in VC.")

    @discord.ui.button(label="End Sesh", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="dank:toke:v2:end", row=1)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._respond(interaction, "end")


async def save_toke_settings(guild_id: int, user_id: int, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on guild-config persistence failures; never claim a false save."""
    from stoney_verify.guild_config import get_guild_config, upsert_guild_config
    payload = settings_from_config({SETTINGS_KEY: settings})
    await upsert_guild_config(
        guild_id,
        {
            SETTINGS_KEY: payload,
            "__config_write_mode": "explicit_override",
            "__config_write_source": "toke_live_settings",
            "__config_write_actor_id": str(user_id),
            "__config_write_allow_keys": [SETTINGS_KEY],
        },
    )
    stored = await get_guild_config(guild_id, force_refresh=True)
    actual = settings_from_config(stored)
    if actual != payload:
        raise RuntimeError("Guild settings could not be verified after saving. Nothing was confirmed.")
    return actual


def _setting_embed(settings: Mapping[str, Any]) -> discord.Embed:
    voice_id = int(settings.get("voice_channel_id", 0) or 0)
    return discord.Embed(
        title="💨 Toke Sesh Settings",
        description=(
            "These settings apply only to this Discord server. "
            "Starter and notification roles remain in Community & Pings.\n\n"
            f"**Session lifetime:** {settings['session_minutes']} minutes\n"
            f"**Member cooldown:** {settings['user_cooldown_minutes']} minutes\n"
            f"**Server cooldown:** {settings['guild_cooldown_minutes']} minutes\n"
            f"**Maximum interested:** {settings['max_interested']}\n"
            f"**Show participant names:** {'Yes' if settings['show_member_names'] else 'No'}\n"
            f"**Default voice:** {f'<#{voice_id}>' if voice_id else 'None (use starter voice if connected)'}"
        ),
        color=discord.Color.green(),
    )


class TokeSettingsModal(discord.ui.Modal, title="Toke Meetup Settings"):
    lifetime = discord.ui.TextInput(label="Sesh duration (15-240 min)", max_length=3, default="60")
    user_cooldown = discord.ui.TextInput(label="Member cooldown (0-1440 min)", max_length=4, default="15")
    guild_cooldown = discord.ui.TextInput(label="Server cooldown (0-240 min)", max_length=3, default="5")
    maximum = discord.ui.TextInput(label="Maximum interested (1-250)", max_length=3, default="100")
    names = discord.ui.TextInput(label="Show participant names (yes/no)", max_length=3, default="yes")

    def __init__(self, owner_id: int, saved: Mapping[str, Any]) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.lifetime.default = str(saved["session_minutes"])
        self.user_cooldown.default = str(saved["user_cooldown_minutes"])
        self.guild_cooldown.default = str(saved["guild_cooldown_minutes"])
        self.maximum.default = str(saved["max_interested"])
        self.names.default = "yes" if saved["show_member_names"] else "no"
        self.voice_id = int(saved.get("voice_channel_id", 0))  # Refreshed before saving.

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if (
            interaction.guild is None or member.id != self.owner_id
            or not getattr(member.guild_permissions, "manage_guild", False)
        ):
            return await interaction.response.send_message("Manage Server is required for Toke settings.", ephemeral=True)
        values = [
            (str(self.lifetime.value), 15, 240, "duration"),
            (str(self.user_cooldown.value), 0, 1440, "member cooldown"),
            (str(self.guild_cooldown.value), 0, 240, "server cooldown"),
            (str(self.maximum.value), 1, 250, "participant limit"),
        ]
        nums = []
        for raw, low, high, name in values:
            try:
                number = int(raw)
            except ValueError:
                return await interaction.response.send_message(f"{name} must be a whole number.", ephemeral=True)
            if not low <= number <= high:
                return await interaction.response.send_message(f"{name} must be between {low} and {high}.", ephemeral=True)
            nums.append(number)
        choice = str(self.names.value or "").strip().casefold()
        if choice not in {"yes", "no"}:
            return await interaction.response.send_message("Enter yes or no for participant names.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            fresh = settings_from_config(await _get_live_config(interaction.guild.id))
            actual = await save_toke_settings(interaction.guild.id, member.id, {
                "session_minutes": nums[0],
                "user_cooldown_minutes": nums[1],
                "guild_cooldown_minutes": nums[2],
                "max_interested": nums[3],
                "show_member_names": choice == "yes",
                "voice_channel_id": int(fresh["voice_channel_id"]),
            })
        except Exception as exc:
            return await interaction.edit_original_response(content=f"❌ Settings not saved: {type(exc).__name__}: {exc}")
        await interaction.edit_original_response(content="✅ Toke settings saved and verified.", embed=_setting_embed(actual))


class TokeSettingsView(discord.ui.View):
    def __init__(self, owner_id: int, settings: Mapping[str, Any]) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.settings = dict(settings)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Open your own Toke Settings panel.", ephemeral=True)
            return False
        if not getattr(getattr(interaction.user, "guild_permissions", None), "manage_guild", False):
            await interaction.response.send_message("Manage Server is required.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit Settings", emoji="⚙️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(TokeSettingsModal(self.owner_id, self.settings))

    @discord.ui.button(label="Choose Default VC", emoji="🔊", style=discord.ButtonStyle.secondary)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView
        guild = interaction.guild
        if guild is None:
            return

        async def picked(item_interaction: discord.Interaction, channel: Any) -> None:
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return await item_interaction.response.send_message("Choose a voice channel.", ephemeral=True)
            await item_interaction.response.defer(ephemeral=True, thinking=True)
            cfg = await _get_live_config(guild.id)
            settings = settings_from_config(cfg)
            settings["voice_channel_id"] = int(channel.id)
            try:
                actual = await save_toke_settings(guild.id, item_interaction.user.id, settings)
            except Exception as exc:
                return await item_interaction.edit_original_response(content=f"❌ Could not save Toke voice channel: {exc}")
            await item_interaction.edit_original_response(content="✅ Default Toke VC saved.", embed=_setting_embed(actual), view=TokeSettingsView(self.owner_id, actual))

        view = DankGuildResourceBrowserView(
            guild=guild, author_id=self.owner_id,
            resource_kinds=("voice", "stage"), on_pick=picked,
            custom_id="dank:toke:default_voice",
            title="Choose Default Toke VC",
            placeholder="Choose a voice channel…",
            on_home=lambda back: open_toke_settings(back, replace=True),
        )
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Clear Default VC", emoji="🧹", style=discord.ButtonStyle.secondary)
    async def clear_voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer(ephemeral=True, thinking=True)
        cfg = await _get_live_config(interaction.guild.id)
        new_settings = settings_from_config(cfg)
        new_settings["voice_channel_id"] = 0
        try:
            actual = await save_toke_settings(interaction.guild.id, interaction.user.id, new_settings)
        except Exception as exc:
            return await interaction.edit_original_response(content=f"❌ Could not clear VC: {exc}")
        await interaction.edit_original_response(content="✅ Cleared default VC.", embed=_setting_embed(actual), view=TokeSettingsView(self.owner_id, actual))

    @discord.ui.button(label="Back to Community & Pings", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext.public_community_pings import _open_manager_message
        await _open_manager_message(interaction, owner_id=self.owner_id)


async def _get_live_config(guild_id: int) -> Mapping[str, Any]:
    from stoney_verify.guild_config import get_guild_config
    return await get_guild_config(guild_id, refresh=True)


async def open_toke_settings(interaction: discord.Interaction, *, replace: bool = True) -> None:
    guild = interaction.guild
    if guild is None or not getattr(getattr(interaction.user, "guild_permissions", None), "manage_guild", False):
        return await interaction.response.send_message("Manage Server is required.", ephemeral=True)
    if not interaction.response.is_done():
        if replace and interaction.message is not None:
            await interaction.response.defer()
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = await _get_live_config(guild.id)
    settings = settings_from_config(cfg)
    view = TokeSettingsView(interaction.user.id, settings)
    if replace and interaction.message is not None and not interaction.response.is_done():
        await interaction.response.edit_message(embed=_setting_embed(settings), view=view)
    elif interaction.response.is_done():
        await interaction.edit_original_response(embed=_setting_embed(settings), view=view)
    else:
        await interaction.response.send_message(embed=_setting_embed(settings), view=view, ephemeral=True)


def pending_for(guild_id: int, user_id: int) -> PendingToke | None:
    now = time.monotonic()
    for key, pending in list(_PENDING.items()):
        if pending.deadline <= now:
            _PENDING.pop(key, None)
    return _PENDING.get((int(guild_id), int(user_id)))


class TokeComposeView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=CAPTURE_SECONDS)
        self.user_id, self.guild_id = int(user_id), int(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is someone else's Toke draft.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Post Without Media", emoji="💨", style=discord.ButtonStyle.success)
    async def post_plain(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        pending = pending_for(self.guild_id, self.user_id)
        if pending is None or pending.claimed:
            return await interaction.response.send_message("Your draft expired. Use /toke again.", ephemeral=True)
        pending.claimed = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from stoney_verify.commands_ext.public_toke import publish_toke_from_draft
            posted = await publish_toke_from_draft(interaction.guild, interaction.user, pending, None)
            await interaction.edit_original_response(content=f"✅ Toke session started: {posted.jump_url}")
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ Could not start Toke: {type(exc).__name__}. Please retry /toke.")
        finally:
            _PENDING.pop((self.guild_id, self.user_id), None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        _PENDING.pop((self.guild_id, self.user_id), None)
        await interaction.response.edit_message(content="Toke draft cancelled.", view=None)


def begin_capture(guild_id: int, user_id: int, channel_id: int, destination_id: int, note: str) -> TokeComposeView:
    _PENDING[(int(guild_id), int(user_id))] = PendingToke(
        int(guild_id), int(user_id), int(channel_id), int(destination_id),
        note, time.monotonic() + CAPTURE_SECONDS,
    )
    return TokeComposeView(user_id, guild_id)


def _is_gif_like_url(value: Any) -> bool:
    url = str(value or "").strip()
    return bool(
        re.match(r"^https?://", url, re.I)
        and re.search(
            r"(?:tenor\.com|giphy\.com|discordapp\.(?:net|com)|\.(?:gif|png|jpe?g|webp)(?:[?#]|$))",
            url, re.I,
        )
    )


def media_url_from_message(message: discord.Message) -> str:
    """Handle normal Tenor/Giphy message URLs plus mobile embed-only GIFs."""
    for found in re.findall(r"https?://[^\s<>]+", str(message.content or "")):
        if _is_gif_like_url(found):
            return found.rstrip(".,")
    for embed in (message.embeds or []):
        for candidate in (
            getattr(embed, "url", None),
            getattr(getattr(embed, "image", None), "url", None),
            getattr(getattr(embed, "video", None), "url", None),
            getattr(getattr(embed, "thumbnail", None), "url", None),
        ):
            if _is_gif_like_url(candidate):
                return str(candidate)
    return ""


def _has_gif_media(message: discord.Message) -> bool:
    from stoney_verify.commands_ext.public_toke import _attachment_is_supported_media
    if any(_attachment_is_supported_media(a) for a in (message.attachments or [])):
        return True
    return bool(media_url_from_message(message))


async def _on_toke_media_message(message: discord.Message) -> None:
    if message.guild is None or getattr(message.author, "bot", False) or message.webhook_id is not None:
        return
    pending = pending_for(message.guild.id, message.author.id)
    if pending is None or pending.claimed or message.channel.id != pending.channel_id:
        return
    if not _has_gif_media(message):
        return  # Don't steal unrelated regular chat while a draft is pending.
    pending.claimed = True
    try:
        from stoney_verify.commands_ext.public_toke import publish_toke_from_draft
        sent = await publish_toke_from_draft(message.guild, message.author, pending, message)
    except Exception as exc:
        try:
            await message.author.send(f"❌ Toke could not post your media: {type(exc).__name__}. Your original GIF was kept.")
        except discord.HTTPException:
            pass
    else:
        try:
            await message.delete()  # Only after the complete Toke card was sent.
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await message.author.send(f"💨 Your Toke call is ready: {sent.jump_url}")
        except discord.HTTPException:
            pass
    finally:
        _PENDING.pop((pending.guild_id, pending.user_id), None)


async def _on_toke_voice_state(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState, *, bot: Any) -> None:
    if member.guild is None:
        return
    affected = {int(ch.id) for ch in (before.channel, after.channel) if ch is not None}
    if not affected:
        return
    now = time.monotonic()
    if now - _VOICE_REFRESH.get(member.guild.id, 0) < 5:
        return
    _VOICE_REFRESH[member.guild.id] = now
    for session in await toke_sessions.all_active(member.guild.id):
        if int(session.get("voice_channel_id", 0)) not in affected:
            continue
        try:
            await refresh_card(bot, session)
        except (discord.HTTPException, ValueError, OSError):
            continue


async def _expire_and_refresh(bot: Any) -> None:
    while not bot.is_closed():
        try:
            expired = await toke_sessions.end_expired()
            for session in expired:
                try:
                    await refresh_card(bot, session)
                except (discord.HTTPException, ValueError, OSError):
                    pass
        except (OSError, ValueError) as exc:
            print(f"[toke] session cleanup failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(120)


async def restore_active_views(bot: Any) -> int:
    count = 0
    for session in await toke_sessions.all_active():
        gid, mid = int(session["guild_id"]), int(session["message_id"])
        if (gid, mid) in _REGISTERED or bot.get_guild(gid) is None:
            continue
        bot.add_view(
            TokeCheersView(
                int(session["starter_id"]), int(session["starter_role_id"]),
                voice_channel_id=int(session.get("voice_channel_id", 0)),
                guild_id=gid, message_id=mid,
            ),
            message_id=mid,
        )
        _REGISTERED.add((gid, mid))
        count += 1
    return count


def register_toke_runtime(bot: Any) -> None:
    """Install one capture/voice listener and a restart-safe view restoration."""
    ident = id(bot)
    if ident in _STARTED_BOTS:
        return
    _STARTED_BOTS.add(ident)

    async def on_ready() -> None:
        global _LOOP_TASK
        try:
            count = await restore_active_views(bot)
            print(f"[toke] restored persistent views={count}")
        except (OSError, ValueError) as exc:
            print(f"[toke] persistence failed: {type(exc).__name__}: {exc}")
        if _LOOP_TASK is None or _LOOP_TASK.done():
            _LOOP_TASK = asyncio.create_task(_expire_and_refresh(bot))

    async def voice_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        await _on_toke_voice_state(member, before, after, bot=bot)

    bot.add_listener(_on_toke_media_message, "on_message")
    bot.add_listener(voice_update, "on_voice_state_update")
    bot.add_listener(on_ready, "on_ready")
