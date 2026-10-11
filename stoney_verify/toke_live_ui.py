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
        result.append(discord.utils.escape_markdown(discord.utils.escape_mentions(name))[:75])
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
    await message.edit(embed=render_card(guild, session, existing[0]), view=view)


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


def _has_gif_media(message: discord.Message) -> bool:
    if any(
        str(getattr(a, "content_type", "") or "").lower().startswith("image/")
        or str(getattr(a, "filename", "") or "").lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".webp"))
        for a in (message.attachments or [])
    ):
        return True
    raw = str(message.content or "")
    if re.search(r"https?://\S*(?:tenor\.com|giphy\.com|discordapp\.(?:net|com)|\.gif|\.webp)", raw, flags=re.I):
        return True
    return any(
        re.search(r"tenor|giphy|\.(?:gif|png|jpe?g|webp)", str(getattr(e, "url", "") or ""), re.I)
        for e in (message.embeds or [])
    )


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


async def _on_toke_voice_state(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
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
        bot = member.guild._state._get_client() if hasattr(member.guild._state, "_get_client") else None
        if bot is None:
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

    bot.add_listener(_on_toke_media_message, "on_message")
    bot.add_listener(_on_toke_voice_state, "on_voice_state_update")
    bot.add_listener(on_ready, "on_ready")
