from __future__ import annotations

"""Member-first Toke controls and normal Discord-composer media capture."""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

import discord

from stoney_verify import toke_sessions
from stoney_verify.share_router_media_network import is_safe_media_download_url

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
# Voice events can arrive in bursts when several members join. A simple
# "ignore anything within five seconds" gate loses the *last* update, leaving
# the card inaccurate until someone manually presses Refresh.
_VOICE_PENDING: dict[int, set[int]] = {}
_VOICE_DEPARTED: dict[int, dict[int, set[int]]] = {}
_VOICE_WORKERS: dict[int, asyncio.Task[None]] = {}
_VOICE_DEBOUNCE_SECONDS = 2.0
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
    voice_channel_id: int = 0


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


def _sesh_people(
    guild: discord.Guild, session: Mapping[str, Any]
) -> tuple[list[tuple[int, tuple[str, ...]]], discord.abc.GuildChannel | None, int]:
    """One row per member, even when they cheered, plan to join and are in VC.

    Resolve the live VC membership separately from the reaction list. We never
    interpret a Cheers click as evidence that somebody joined voice.
    """
    starter_id = int(session.get("starter_id", 0) or 0)
    cheers = {int(uid) for uid in session.get("cheers_ids", []) if int(uid) > 0}
    going = {int(uid) for uid in session.get("going_ids", []) if int(uid) > 0}
    voice = guild.get_channel(int(session.get("voice_channel_id", 0) or 0))
    is_voice = isinstance(voice, (discord.VoiceChannel, discord.StageChannel))
    voice_ids = {
        int(member.id) for member in (getattr(voice, "members", []) or [])
        if int(getattr(member, "id", 0)) > 0
    } if is_voice else set()

    ordered: list[int] = []
    # Include the host first, even before they tap Cheers. Everyone else
    # appears only after interacting; unrelated VC occupants stay out of the
    # session-specific roster while the VC occupancy count stays truthful.
    for uid in [starter_id, *session.get("cheers_ids", []), *session.get("going_ids", [])]:
        uid = int(uid)
        if uid > 0 and uid not in ordered:
            ordered.append(uid)
    people: list[tuple[int, tuple[str, ...]]] = []
    for uid in ordered:
        labels = []
        if uid == starter_id:
            labels.append("Host")
        if uid in cheers:
            labels.append("💨 Cheers")
        if uid in going:
            labels.append("🙋 Joining")
        if uid in voice_ids:
            labels.append("🔊 In VC")
        people.append((uid, tuple(labels)))
    return people, voice if is_voice else None, len(voice_ids)


_STATUS_ICONS = {
    "Host": "👑",
    "💨 Cheers": "💨",
    "🙋 Joining": "🙋",
    "🔊 In VC": "🔊",
}


def _sesh_member_lines(
    people: list[tuple[int, tuple[str, ...]]], *,
    starter_id: int, limit: int = 3, host_named_in_header: bool = False,
) -> str:
    """Compact mobile-first preview: roles first, one name or mention after.

    The full roster lives behind Who's Coming. Never concatenate multiple
    duplicate labels onto a heavily decorated Discord nickname.
    """
    rendered: list[str] = []
    for uid, statuses in people[:limit]:
        icons = "".join(_STATUS_ICONS[s] for s in statuses if s in _STATUS_ICONS)
        subject = "**Host**" if host_named_in_header and uid == starter_id else f"<@{uid}>"
        rendered.append(f"{icons}  {subject}".strip())
    if len(people) > limit:
        rendered.append(f"**+{len(people) - limit} more** · See **Who's Coming?**")
    return "\n".join(rendered) if rendered else "Nobody yet"


def render_card(guild: discord.Guild, session: Mapping[str, Any], original: discord.Embed) -> discord.Embed:
    """Render a genuinely closed card, or a concise participant dashboard."""
    embed = original.copy()
    embed.clear_fields()
    cheer_ids = {int(uid) for uid in session.get("cheers_ids", []) if int(uid) > 0}
    going_ids = {int(uid) for uid in session.get("going_ids", []) if int(uid) > 0}
    active = bool(session.get("active", False))
    historical_cheers = cheer_ids | {int(uid) for uid in session.get("past_cheers_ids", [])}
    historical_going = going_ids | {int(uid) for uid in session.get("past_going_ids", [])}
    expiry = float(session.get("expires_at", 0) or 0)
    if not active or expiry <= time.time():
        ended_at = int(session.get("closed_at") or expiry or time.time())
        reason = str(session.get("close_reason") or "expired")
        embed.title = "💨 Toke Time · Sesh Ended"
        if embed.description:
            embed.description = embed.description.replace(
                "is calling the sesh crowd.", "hosted this sesh."
            )
        embed.add_field(
            name="✅ This sesh has ended",
            value=(
                f"Closed at <t:{ended_at}:f>."
                + (" The time limit was reached." if reason == "expired" else " The host or staff ended it.")
                + "\nStart a new `/toke` when you're ready. This announcement is no longer taking responses."
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Final responses",
            value=f"💨 Cheers **{len(historical_cheers)}**  ·  🙋 Joining **{len(historical_going)}**",
            inline=False,
        )
        embed.color = discord.Color.dark_grey()
        return embed

    people, voice, voice_count = _sesh_people(guild, session)
    attending = sum("🔊 In VC" in statuses for _uid, statuses in people)
    visible = bool(session.get("show_member_names", True))
    voice_id = int(session.get("voice_channel_id", 0) or 0)
    channel_text = voice.mention if voice is not None else ("No VC selected" if not voice_id else "VC unavailable")
    voice_status = str(voice_count) if voice is not None else ("Not selected" if not voice_id else "Unavailable")
    embed.add_field(
        name="📊 Sesh activity",
        value=(
            f"💨 **Cheers: {len(cheer_ids)}**   ·   🙋 **Joining: {len(going_ids)}**\n"
            f"🔊 **VC occupants: {voice_status}**   ·   **Sesh attendees: {attending if voice is not None else 'Unknown'}**\n"
            f"{channel_text}"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name=f"👥 People • {len(people)}",
        value=(
            _sesh_member_lines(
                people, starter_id=int(session.get("starter_id", 0)),
                host_named_in_header=True,
            ) if visible else "Participant names are private in this server."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="⏳ Session",
        value=(
            f"Scheduled end: <t:{int(expiry)}:f>\n"
            f"Counts last checked: <t:{int(time.time())}:t>  ·  Tap **Refresh** for current status."
        ),
        inline=False,
    )
    embed.color = discord.Color.green()
    return embed


async def reconcile_live_attendance(
    guild: discord.Guild, session: Mapping[str, Any], *,
    departed_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Use the actual selected VC, not the cached response list, for attendance.

    An unavailable/removed VC is not evidence everybody departed. In that
    case keep RSVPs, but the card shows no verified VC attendance.
    """
    channel = guild.get_channel(int(session.get("voice_channel_id", 0) or 0))
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        return dict(session)
    present = {int(member.id) for member in (getattr(channel, "members", []) or [])}
    current = await toke_sessions.reconcile_voice(
        int(session["guild_id"]), int(session["message_id"]),
        present, departed_ids=departed_ids,
    )
    return current if current is not None else dict(session)


async def refresh_card(bot: Any, session: Mapping[str, Any], interaction: discord.Interaction | None = None, *, departed_ids: set[int] | None = None) -> bool:
    """Reconcile from current store state, then acknowledge terminal delivery.

    Never edit Discord using a stale active=True snapshot (the old expiry bug).
    """
    guild = bot.get_guild(int(session["guild_id"]))
    if guild is None:
        return False
    channel = guild.get_channel(int(session["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return False
    current = await toke_sessions.get(int(session["guild_id"]), int(session["message_id"]))
    if current is None:
        return False
    if current.get("active", False):
        current = await reconcile_live_attendance(guild, current, departed_ids=departed_ids)
    if interaction is not None and interaction.message is not None:
        message = interaction.message
    else:
        try:
            message = await channel.fetch_message(int(session["message_id"]))
        except discord.NotFound:
            if not current.get("active", False):
                await toke_sessions.mark_closed_synced(int(session["guild_id"]), int(session["message_id"]))
            return False
    if current.get("media_layout") in ("thumbnail_v2", "gallery_v2"):
        view = TokeMediaLayout(
            guild, current, intro=_media_intro(message, current),
        )
        await message.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
        if not current.get("active", False):
            await toke_sessions.mark_closed_synced(
                int(current["guild_id"]), int(current["message_id"])
            )
        return True
    existing = message.embeds or []
    if not existing:
        return False
    active = bool(current.get("active", False))
    view = (
        TokeCheersView(
            int(current["starter_id"]), int(current["starter_role_id"]),
            voice_channel_id=int(current.get("voice_channel_id", 0) or 0),
            active=True, guild_id=int(current["guild_id"]), message_id=int(current["message_id"]),
        ) if active else None
    )
    await message.edit(
        embed=render_card(guild, current, existing[0]),
        view=view, allowed_mentions=discord.AllowedMentions.none(),
    )
    if not active:
        await toke_sessions.mark_closed_synced(int(current["guild_id"]), int(current["message_id"]))
    return True


def _media_intro(message: discord.Message, session: Mapping[str, Any]) -> str:
    """Retain the original member note without putting message text into storage."""
    def text_displays(node: Any):
        if getattr(node, "content", None):
            yield str(node.content)
        for nested in getattr(node, "children", ()) or ():
            yield from text_displays(nested)

    for component in getattr(message, "components", ()) or ():
        for content in text_displays(component):
            if content.startswith("## 💨 Toke Time"):
                return content
    # Only a defensive fallback for a malformed/older Discord component payload.
    return (
        "## 💨 Toke Time\n"
        f"<@&{int(session['notify_role_id'])}>\n"
        f"<@{int(session['starter_id'])}> is calling the sesh crowd."
    )


class TokeMediaLayout(discord.ui.LayoutView):
    """One full-width inline media item in the editable V2 session card.

    V2 MediaGallery keeps the uploaded GIF or photo inside the same container
    during attendance updates. It avoids the duplicate native attachment that
    occurred when patching attachment:// embeds. Legacy embed cards are unchanged.
    """

    def __init__(
        self, guild: discord.Guild, session: Mapping[str, Any], *,
        intro: str | None = None, allow_pending_controls: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        filename = str(session.get("media_filename", "") or "")
        if not filename or "/" in filename or "\\\\" in filename:
            raise ValueError("Toke media attachment filename is invalid.")
        intro = intro or (
            "## 💨 Toke Time\n"
            f"<@&{int(session['notify_role_id'])}>\n"
            f"<@{int(session['starter_id'])}> is calling the sesh crowd."
        )
        card = render_card(guild, session, discord.Embed(title="💨 Toke Time"))
        active = bool(session.get("active", False)) and float(session.get("expires_at", 0) or 0) > time.time()
        if not active:
            if "## 💨 Toke Time · Sesh Ended" not in intro:
                intro = intro.replace("## 💨 Toke Time", "## 💨 Toke Time · Sesh Ended", 1)
            intro = intro.replace("is calling the sesh crowd.", "hosted this sesh.")
        container = discord.ui.Container(
            discord.ui.TextDisplay(intro[:1200]),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(f"attachment://{filename}", description="Toke session artwork")
            ),
            discord.ui.TextDisplay(
                "\n\n".join(f"**{field.name}**\n{field.value}" for field in card.fields)[:2400]
            ),
            accent_color=discord.Color.green() if active else discord.Color.dark_grey(),
        )
        if active or allow_pending_controls:
            actions = TokeCheersView(
                int(session["starter_id"]), int(session["starter_role_id"]),
                voice_channel_id=int(session.get("voice_channel_id", 0) or 0),
                active=True, guild_id=int(session["guild_id"]),
                message_id=int(session["message_id"]),
            )
            controls: list[discord.ui.Button] = []
            for old in actions.children:
                if not isinstance(old, discord.ui.Button):
                    continue
                if old.style is discord.ButtonStyle.link:
                    controls.append(discord.ui.Button(
                        label=old.label, emoji=old.emoji, style=old.style, url=old.url
                    ))
                    continue
                button = discord.ui.Button(
                    label=old.label, emoji=old.emoji, style=old.style,
                    custom_id=old.custom_id,
                )
                async def callback(interaction: discord.Interaction, source: discord.ui.Button = old) -> None:
                    await source.callback(interaction)
                button.callback = callback
                controls.append(button)
            container.add_item(discord.ui.ActionRow(*controls[:4]))
            container.add_item(discord.ui.ActionRow(*controls[4:]))
        self.add_item(container)


class TokeRosterView(discord.ui.View):
    def __init__(self, guild: discord.Guild, session: Mapping[str, Any], page: int = 0) -> None:
        super().__init__(timeout=180)
        self.guild = guild
        self.session = dict(session)
        self.page = max(0, int(page))
        people, _, _ = _sesh_people(guild, session)
        self.lines = [
            f"{index}. " + "".join(_STATUS_ICONS[s] for s in statuses if s in _STATUS_ICONS) + f"  <@{uid}>"
            for index, (uid, statuses) in enumerate(people, start=1)
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
        ).set_footer(
            text=f"Page {self.page+1}/{self.pages} • 👑 Host · 💨 Cheers · 🙋 Joining · 🔊 In VC"
        )

    async def _turn_page(self, interaction: discord.Interaction, direction: int) -> None:
        await interaction.response.defer()
        latest = await toke_sessions.get(
            int(self.session["guild_id"]), int(self.session["message_id"])
        )
        if not latest or not latest.get("active", False):
            return await interaction.edit_original_response(
                content="This sesh has ended.", embed=None, view=None
            )
        latest = await reconcile_live_attendance(self.guild, latest)
        view = TokeRosterView(self.guild, latest, self.page + direction)
        await interaction.edit_original_response(embed=view.embed(), view=view)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="dank:toke:roster:prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._turn_page(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="dank:toke:roster:next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._turn_page(interaction, 1)


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

    async def _respond(self, interaction: discord.Interaction, action: str, *, session_message_id: int | None = None, already_deferred: bool = False) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        message_id = int(session_message_id or getattr(interaction.message, "id", 0) or 0)
        if guild is None or member is None or not message_id:
            if already_deferred:
                return await interaction.edit_original_response(content="This sesh is no longer available.")
            return await interaction.response.send_message("This sesh is no longer available.", ephemeral=True)
        # Acknowledge before file I/O or Discord edits: avoids the dreaded
        # "Dank Shield didn't respond in time" on busy Discloud instances.
        if not already_deferred:
            await interaction.response.defer(ephemeral=True, thinking=True)
        card_interaction = None if session_message_id is not None else interaction
        try:
            session = await toke_sessions.get(guild.id, message_id)
            if not session:
                return await interaction.edit_original_response(
                    content="This sesh is no longer available. Use /toke to start another."
                )
            if not session.get("active", False):
                # A previously expired card may still show old buttons while a
                # background Discord edit was delayed. Fix the public card now.
                try:
                    await refresh_card(interaction.client, session, card_interaction)
                except (discord.HTTPException, ValueError, OSError) as exc:
                    print(f"[toke] closed-card click sync failed guild={guild.id}: {type(exc).__name__}: {exc}")
                return await interaction.edit_original_response(
                    content="✅ This sesh has already ended. Start a new /toke to invite everyone."
                )
            if action == "end":
                if member.id != int(session["starter_id"]) and not member.guild_permissions.manage_guild:
                    return await interaction.edit_original_response(
                        content="Only the starter or server managers can end this sesh."
                    )
            else:
                role_ids = {int(session["starter_role_id"]), int(session["notify_role_id"])}
                if not any(int(role.id) in role_ids for role in member.roles):
                    return await interaction.edit_original_response(
                        content="Join the server's configured Toke community or notification role first."
                    )
            updated, error = await toke_sessions.react(
                guild.id, message_id, member.id, action,
                max_interested=int(session.get("max_interested", 100)),
            )
            if error:
                if updated is None:
                    closed = await toke_sessions.get(guild.id, message_id)
                    if closed and not closed.get("active", False):
                        try:
                            await refresh_card(interaction.client, closed, card_interaction)
                        except (discord.HTTPException, ValueError, OSError):
                            pass
                return await interaction.edit_original_response(content=f"⚠️ {error}")
            try:
                await refresh_card(interaction.client, updated, card_interaction)
            except (discord.HTTPException, ValueError, OSError) as exc:
                print(f"[toke] card edit retry queued guild={guild.id} message={message_id} error={type(exc).__name__}")
                if action == "end":
                    return await interaction.edit_original_response(
                        content="✅ Sesh ended. The public card will update when Discord accepts the edit."
                    )
                return await interaction.edit_original_response(
                    content="✅ Your response was saved. The card refresh is delayed; use Refresh in a moment."
                )
        except (OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"⚠️ Could not save your sesh response: {type(exc).__name__}."
            )
        await interaction.edit_original_response(content={
            "cheers": "💨 Cheers recorded.",
            "going": "🙋 You're on the joining list. Use Join VC when ready.",
            "withdraw": "✅ You were removed from the interested and joining lists.",
            "end": "✅ Sesh ended. This card is now closed.",
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            session = await toke_sessions.get(guild.id, msg.id)
            if not session or not session.get("active", False):
                return await interaction.edit_original_response(
                    content="✅ This sesh has ended. Run /toke to start a new one."
                )
            if not session.get("show_member_names", True):
                return await interaction.edit_original_response(
                    content="This server keeps participant names private."
                )
            session = await reconcile_live_attendance(guild, session)
            view = TokeRosterView(guild, session)
            await interaction.edit_original_response(embed=view.embed(), view=view)
        except (discord.HTTPException, OSError, ValueError) as exc:
            await interaction.edit_original_response(
                content=f"⚠️ Could not open the member list: {type(exc).__name__}."
            )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:toke:v2:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.message is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            session = await toke_sessions.get(interaction.guild.id, interaction.message.id)
            if not session:
                return await interaction.edit_original_response(
                    content="This sesh is no longer available."
                )
            updated = await refresh_card(interaction.client, session, interaction)
            if not session.get("active", False):
                return await interaction.edit_original_response(
                    content="✅ This sesh has ended. The public card is now closed."
                    if updated else "✅ This sesh has ended. The card will update shortly."
                )
            # Return the freshly sampled numbers to the member as well as
            # editing the public card. Discord Android may cache an existing
            # embed; this ephemeral interaction gives an authoritative
            # snapshot even when the old card hasn't repainted yet.
            latest = await toke_sessions.get(interaction.guild.id, interaction.message.id)
            if latest and latest.get("active", False):
                latest = await reconcile_live_attendance(interaction.guild, latest)
            if not latest or not latest.get("active", False):
                return await interaction.edit_original_response(
                    content="✅ This sesh has ended. The public card is closing."
                )
            people, voice, voice_count = _sesh_people(interaction.guild, latest)
            attending = sum("🔊 In VC" in statuses for _uid, statuses in people)
            cheers = len({int(uid) for uid in latest.get("cheers_ids", [])})
            joining = len({int(uid) for uid in latest.get("going_ids", [])})
            clock = int(time.time())
            voice_id = int(latest.get("voice_channel_id", 0) or 0)
            voice_status = str(voice_count) if voice is not None else ("Not selected" if not voice_id else "Unavailable")
            await interaction.edit_original_response(
                content=(
                    f"✅ **Sesh status checked <t:{clock}:t>**\n"
                    f"💨 Cheers: **{cheers}** · 🙋 Joining: **{joining}** · "
                    f"🔊 VC occupants: **{voice_status}** · "
                    f"Sesh attendees: **{attending if voice is not None else 'Unknown'}**\n"
                    f"Scheduled end: <t:{int(latest['expires_at'])}:f>"
                    + (f" · {voice.mention}" if voice is not None else "")
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, OSError, ValueError) as exc:
            await interaction.edit_original_response(
                content=f"⚠️ Could not refresh the card: {type(exc).__name__}."
            )

    @discord.ui.button(label="Manage", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank:toke:v2:manage", row=1)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.message is None:
            return await interaction.response.send_message("This sesh is unavailable.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            session = await toke_sessions.get(interaction.guild.id, interaction.message.id)
        except (OSError, ValueError):
            return await interaction.edit_original_response(content="The sesh manager is temporarily unavailable.")
        if not session or not session.get("active", False):
            return await interaction.edit_original_response(content="This sesh has ended.")
        if interaction.user.id != int(session["starter_id"]) and not getattr(interaction.user.guild_permissions, "manage_guild", False):
            return await interaction.edit_original_response(content="Only the host or server managers can manage this sesh.")
        await interaction.edit_original_response(
            content="Manage this sesh. Extensions do not ping members again.",
            view=TokeManageView(interaction.user.id, interaction.guild.id, interaction.message.id),
        )

    @discord.ui.button(label="End Sesh", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="dank:toke:v2:end", row=1)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.message is None:
            return await interaction.response.send_message("This sesh is no longer available.", ephemeral=True)
        member = interaction.user
        if member.id != self.starter_id and not getattr(member.guild_permissions, "manage_guild", False):
            return await interaction.response.send_message(
                "Only the starter or server managers can end this sesh.", ephemeral=True,
            )
        await interaction.response.send_message(
            "End this sesh for everyone? Responses will close immediately.",
            view=TokeEndConfirmView(self, member.id, interaction.message.id),
            ephemeral=True,
        )


class TokeManageView(discord.ui.View):
    """Private host/staff extension controls, with click-time authorization."""

    def __init__(self, actor_id: int, guild_id: int, message_id: int) -> None:
        super().__init__(timeout=120)
        self.actor_id = int(actor_id)
        self.guild_id = int(guild_id)
        self.message_id = int(message_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Open your own sesh manager.", ephemeral=True)
            return False
        return True

    async def _extend(self, interaction: discord.Interaction, minutes: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            session = await toke_sessions.get(self.guild_id, self.message_id)
        except (OSError, ValueError):
            return await interaction.edit_original_response(
                content="The sesh manager is temporarily unavailable.", view=None,
            )
        if not session or not session.get("active", False):
            return await interaction.edit_original_response(content="This sesh has ended.", view=None)
        if interaction.user.id != int(session["starter_id"]) and not getattr(interaction.user.guild_permissions, "manage_guild", False):
            return await interaction.edit_original_response(content="You can no longer manage this sesh.", view=None)
        try:
            updated, error = await toke_sessions.extend(self.guild_id, self.message_id, minutes=minutes)
            if error:
                return await interaction.edit_original_response(content=f"⚠️ {error}", view=None)
            if updated is None:
                return await interaction.edit_original_response(content="This sesh has ended.", view=None)
        except (OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"⚠️ Could not save the extension: {type(exc).__name__}.", view=None,
            )
        try:
            refreshed = await refresh_card(interaction.client, updated)
        except (discord.HTTPException, OSError, ValueError):
            refreshed = False
        await interaction.edit_original_response(
            content=(
                f"✅ Sesh extended. New end: <t:{int(updated['expires_at'])}:f>."
                + ("" if refreshed else " The public card refresh is delayed.")
            ),
            view=None,
        )

    @discord.ui.button(label="+15 minutes", style=discord.ButtonStyle.primary)
    async def fifteen(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._extend(interaction, 15)

    @discord.ui.button(label="+30 minutes", style=discord.ButtonStyle.primary)
    async def thirty(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._extend(interaction, 30)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Sesh manager closed.", view=None)


class TokeEndConfirmView(discord.ui.View):
    """Owner-only confirmation, with authorization rechecked on submission."""

    def __init__(self, source: TokeCheersView, actor_id: int, message_id: int) -> None:
        super().__init__(timeout=60)
        self.source = source
        self.actor_id = int(actor_id)
        self.message_id = int(message_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("This is not your confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, End Sesh", emoji="🛑", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer()
        try:
            await self.source._respond(
                interaction, "end", session_message_id=self.message_id, already_deferred=True,
            )
        except (discord.HTTPException, OSError, ValueError) as exc:
            await interaction.edit_original_response(
                content=f"⚠️ Could not end the sesh: {type(exc).__name__}.", view=None,
            )
        else:
            await interaction.edit_original_response(view=None)
        finally:
            self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        self.stop()
        await interaction.response.edit_message(content="Sesh stays active.", view=None)


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
    # O(1) path for the bot's high-volume, global on_message listener.
    key = (int(guild_id), int(user_id))
    pending = _PENDING.get(key)
    if pending is not None and pending.deadline <= time.monotonic():
        _PENDING.pop(key, None)
        return None
    return pending


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


def begin_capture(
    guild_id: int, user_id: int, channel_id: int, destination_id: int,
    note: str, *, voice_channel_id: int = 0,
) -> TokeComposeView:
    _PENDING[(int(guild_id), int(user_id))] = PendingToke(
        int(guild_id), int(user_id), int(channel_id), int(destination_id),
        note, time.monotonic() + CAPTURE_SECONDS,
        voice_channel_id=int(voice_channel_id),
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


def media_url_from_message(message: discord.Message, *, allow_public_page: bool = False) -> str:
    """Prefer durable embedded media; accept a sole HTTPS link during /toke capture."""
    from stoney_verify.commands_ext.public_toke import _is_stable_provider_media_url

    candidates: list[str] = []
    for embed in (message.embeds or []):
        # Discord GIF picker can expose a playable public image here, even
        # when message.content contains a separate Tenor viewing-page link.
        for candidate in (
            getattr(getattr(embed, "image", None), "url", None),
            getattr(getattr(embed, "video", None), "url", None),
            getattr(getattr(embed, "thumbnail", None), "url", None),
            getattr(embed, "url", None),
        ):
            if _is_gif_like_url(candidate):
                candidates.append(str(candidate))
    for found in re.findall(r"https?://[^\s<>]+", str(message.content or "")):
        if _is_gif_like_url(found):
            candidates.append(found.rstrip(".,"))

    # Preserve inline display whenever the media host is independent of the
    # member's temporary message (which is deleted after publishing).
    for candidate in candidates:
        if _is_stable_provider_media_url(candidate):
            return candidate
    if candidates:
        return candidates[0]
    if allow_public_page:
        # A pending /toke composer expects an image. A single public HTTPS URL
        # can be resolved by the canonical publisher, even for a new site
        # without a familiar GIF suffix. Other chat is never intercepted.
        text = str(message.content or "").strip().strip("<>")
        if text.startswith("https://") and is_safe_media_download_url(text):
            return text
    return ""


def _has_gif_media(message: discord.Message, *, allow_public_page: bool = False) -> bool:
    from stoney_verify.commands_ext.public_toke import _attachment_is_supported_media
    if any(_attachment_is_supported_media(a) for a in (message.attachments or [])):
        return True
    return bool(media_url_from_message(message, allow_public_page=allow_public_page))


async def _on_toke_media_message(message: discord.Message) -> None:
    if message.guild is None or getattr(message.author, "bot", False) or message.webhook_id is not None:
        return
    pending = pending_for(message.guild.id, message.author.id)
    if pending is None or pending.claimed or message.channel.id != pending.channel_id:
        return
    if not _has_gif_media(message, allow_public_page=True):
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


async def _flush_toke_voice_updates(guild: discord.Guild, bot: Any) -> None:
    """Coalesce VC churn, then refresh the *final* observed membership.

    Exactly one worker per guild. If more events arrive while a card is being
    edited, run another pass rather than losing the trailing member change.
    """
    gid = int(guild.id)
    try:
        while _VOICE_PENDING.get(gid):
            await asyncio.sleep(_VOICE_DEBOUNCE_SECONDS)
            affected = _VOICE_PENDING.pop(gid, set())
            departed = _VOICE_DEPARTED.pop(gid, {})
            if not affected:
                continue
            for session in await toke_sessions.all_active(gid):
                if int(session.get("voice_channel_id", 0) or 0) not in affected:
                    continue
                try:
                    departing_members = departed.get(int(session.get("voice_channel_id", 0) or 0), set())
                    if departing_members:
                        await refresh_card(bot, session, departed_ids=departing_members)
                    else:
                        await refresh_card(bot, session)
                except (discord.HTTPException, ValueError, OSError) as exc:
                    print(
                        f"[toke] voice refresh error guild={gid} "
                        f"message={session.get('message_id')} error={type(exc).__name__}"
                    )
    finally:
        _VOICE_WORKERS.pop(gid, None)


async def _on_toke_voice_state(
    member: discord.Member, before: discord.VoiceState, after: discord.VoiceState, *, bot: Any
) -> None:
    if member.guild is None:
        return
    affected = {int(ch.id) for ch in (before.channel, after.channel) if ch is not None}
    if not affected:
        return
    gid = int(member.guild.id)
    _VOICE_PENDING.setdefault(gid, set()).update(affected)
    previous = getattr(before, "channel", None)
    current = getattr(after, "channel", None)
    if previous is not None and (current is None or int(current.id) != int(previous.id)):
        _VOICE_DEPARTED.setdefault(gid, {}).setdefault(int(previous.id), set()).add(int(member.id))
    worker = _VOICE_WORKERS.get(gid)
    if worker is None or worker.done():
        _VOICE_WORKERS[gid] = asyncio.create_task(_flush_toke_voice_updates(member.guild, bot))


async def _expire_and_refresh(bot: Any) -> None:
    while not bot.is_closed():
        try:
            current = time.monotonic()
            for key, pending in list(_PENDING.items()):
                if pending.deadline <= current:
                    _PENDING.pop(key, None)
            # Persistent queue includes pre-existing expired cards, explicit
            # End Sesh actions and any failed Discord edits from prior boots.
            for session in await toke_sessions.pending_closed():
                synced = False
                try:
                    synced = await refresh_card(bot, session)
                except (discord.HTTPException, ValueError, OSError) as exc:
                    print(
                        f"[toke] terminal card retry guild={session.get('guild_id')} "
                        f"message={session.get('message_id')} error={type(exc).__name__}"
                    )
                if not synced:
                    await toke_sessions.defer_closed_retry(
                        int(session["guild_id"]), int(session["message_id"])
                    )
        except (OSError, ValueError) as exc:
            print(f"[toke] session cleanup failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(15)


async def restore_active_views(bot: Any) -> int:
    count = 0
    for session in await toke_sessions.recoverable_views():
        gid, mid = int(session["guild_id"]), int(session["message_id"])
        if (gid, mid) in _REGISTERED or bot.get_guild(gid) is None:
            continue
        view = (
            TokeMediaLayout(
                bot.get_guild(gid), session, allow_pending_controls=True,
            ) if session.get("media_layout") in ("thumbnail_v2", "gallery_v2")
            else TokeCheersView(
                int(session["starter_id"]), int(session["starter_role_id"]),
                voice_channel_id=int(session.get("voice_channel_id", 0)),
                guild_id=gid, message_id=mid, active=True,
            )
        )
        bot.add_view(view, message_id=mid)
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
