from __future__ import annotations

"""Mobile-first Dank Shield Community Hub.

The public slash-command tree stays intentionally small. Community Hub is opened
from the main Dank Shield home panel and then uses buttons, selects, and modals.
All slow mutations acknowledge Discord first, re-authorize on the server side,
and run through the existing bot-wide operation queue.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import discord

from .. import community_hub_service as hub
from .. import community_external_lfg as external
from .. import community_free_lfg as free_lfg
from .. import community_lfg_atlas as atlas
from .. import community_hub_game_role_pings as game_pings
from ..guild_config import get_guild_config
from ..community_lfg_discovery import filter_discovery_games
from ..community_hub_runtime import ensure_community_hub_runtime
from ..community_voice_caption_runtime import (
    ensure_community_voice_caption_manager,
    live_captions_enabled,
)
from ..community_voice_receive import VoiceReceiveUnavailable, voice_receive_capability
from ..interaction_guard import safe_defer_interaction
from ..operation_queue import run_exclusive
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS, private_menu_lifecycle_text
from ..permission_repair_core import community_hub_oauth_url
from ..ui.resource_browser import DankGuildResourceBrowserView
from .public_owner_authority import (
    interaction_has_administrator_authority,
    interaction_has_manage_guild_authority,
    interaction_is_actual_guild_owner,
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _staff_authorized(interaction: discord.Interaction) -> bool:
    return bool(
        interaction_is_actual_guild_owner(interaction)
        or interaction_has_administrator_authority(interaction)
        or interaction_has_manage_guild_authority(interaction)
    )


_HUB_PERMISSION_LABELS = {
    "view_channel": "View Channels",
    "send_messages": "Send Messages",
    "send_messages_in_threads": "Send Messages in Threads",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
    "manage_threads": "Manage Threads",
    "manage_channels": "Manage Channels",
    "view_audit_log": "View Audit Log",
}


def _public_install_url(bot: Any) -> str:
    client_id = _safe_int(
        getattr(bot, "application_id", 0)
        or getattr(getattr(bot, "user", None), "id", 0),
        0,
    )
    return community_hub_oauth_url(client_id)


def _community_hub_required_permissions(settings: dict[str, Any]) -> tuple[str, ...]:
    names = [
        "view_channel",
        "send_messages",
        "embed_links",
        "read_message_history",
        "view_audit_log",
    ]
    if bool(settings.get("auto_create_thread", True)):
        names.extend(("send_messages_in_threads", "manage_threads"))
    if bool(settings.get("auto_create_voice", True)):
        names.append("manage_channels")
    return tuple(dict.fromkeys(names))


def _community_hub_readiness(
    guild: Optional[discord.Guild],
    settings: dict[str, Any],
) -> dict[str, Any]:
    if guild is None:
        return {
            "healthy": False,
            "guild_missing": ["Dank Shield is not installed in this server."],
            "channel_missing": [],
            "channel": None,
            "category_missing": [],
            "category": None,
            "reauthorize_url": "",
        }

    me = getattr(guild, "me", None)
    if me is None:
        return {
            "healthy": False,
            "guild_missing": ["Dank Shield could not resolve its server member."],
            "channel_missing": [],
            "channel": None,
            "category_missing": [],
            "category": None,
            "reauthorize_url": "",
        }

    required = _community_hub_required_permissions(settings)
    guild_perms = getattr(me, "guild_permissions", None)
    admin = bool(getattr(guild_perms, "administrator", False))
    missing = [
        name
        for name in required
        if not admin and not bool(getattr(guild_perms, name, False))
    ]

    channel = None
    channel_missing: list[str] = []
    channel_id = _safe_int(settings.get("hub_channel_id"), 0)
    if channel_id > 0:
        channel = guild.get_channel(channel_id)
        if channel is None:
            channel_missing = ["configured_channel_missing"]
        else:
            try:
                effective = channel.permissions_for(me)
                channel_required = [
                    "view_channel",
                    "send_messages",
                    "embed_links",
                    "read_message_history",
                ]
                if bool(settings.get("auto_create_thread", True)):
                    channel_required.extend(("send_messages_in_threads", "manage_threads"))
                channel_missing = [
                    name
                    for name in dict.fromkeys(channel_required)
                    if not bool(getattr(effective, "administrator", False))
                    and not bool(getattr(effective, name, False))
                ]
            except Exception:
                channel_missing = ["channel_permission_check"]

    category = None
    category_missing: list[str] = []
    if bool(settings.get("auto_create_voice", True)):
        category_id = _safe_int(settings.get("parent_category_id"), 0)
        if category_id > 0:
            candidate = guild.get_channel(category_id)
            if isinstance(candidate, discord.CategoryChannel):
                category = candidate
            else:
                category_missing = ["configured_category_missing"]
        elif channel is not None:
            candidate = getattr(channel, "category", None)
            if isinstance(candidate, discord.CategoryChannel):
                category = candidate

        if category is not None:
            try:
                effective = category.permissions_for(me)
                if (
                    not bool(getattr(effective, "administrator", False))
                    and not bool(getattr(effective, "manage_channels", False))
                ):
                    category_missing = ["manage_channels"]
            except Exception:
                category_missing = ["category_permission_check"]

    return {
        "healthy": not missing and not channel_missing and not category_missing,
        "guild_missing": missing,
        "channel_missing": channel_missing,
        "channel": channel,
        "category_missing": category_missing,
        "category": category,
        "reauthorize_url": (
            community_hub_oauth_url(_safe_int(getattr(me, "id", 0), 0), guild=guild)
            if missing
            else ""
        ),
    }


def _permission_names(names: list[str]) -> str:
    return ", ".join(_HUB_PERMISSION_LABELS.get(name, name.replace("_", " ").title()) for name in names)


def _hublink_readiness_embed(
    guild: Optional[discord.Guild],
    settings: dict[str, Any],
    *,
    connected_name: str = "",
) -> discord.Embed:
    report = _community_hub_readiness(guild, settings)
    title = "✅ Community Hub Ready" if report["healthy"] else "🛠️ Community Hub Setup Check"
    description = (
        f"HubLink connected to **{connected_name}**. "
        if connected_name
        else ""
    )
    description += (
        "Dank Shield has the access Community Hub needs."
        if report["healthy"]
        else "The server link is safe, but Discord is still blocking one or more Community Hub capabilities."
    )
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())

    guild_missing = list(report.get("guild_missing") or [])
    if guild_missing:
        embed.add_field(
            name="1. Server permissions",
            value=(
                f"Dank Shield is missing: **{_permission_names(guild_missing)}**.\n"
                "Tap **Reauthorize Dank Shield** below. Discord opens this server directly; review the requested Community Hub permissions and authorize. "
                "The repair link does **not** request Administrator."
            )[:1024],
            inline=False,
        )

    channel_missing = list(report.get("channel_missing") or [])
    channel = report.get("channel")
    if channel_missing:
        if "configured_channel_missing" in channel_missing:
            embed.add_field(
                name="2. Community Hub channel",
                value=(
                    "The saved Community Hub channel no longer exists or Dank Shield can no longer resolve it. "
                    "Open **Community Hub → Staff Dashboard → Settings** and choose a valid Hub channel, then run **Check Again**."
                ),
                inline=False,
            )
        else:
            label = getattr(channel, "mention", None) or getattr(channel, "name", None) or "the configured Community Hub channel"
            embed.add_field(
                name="2. Channel access",
                value=(
                    f"In {label}, Dank Shield is blocked from: **{_permission_names(channel_missing)}**.\n"
                    "On mobile: open the channel → tap its name → **Settings / Edit Channel** → **Permissions** → "
                    "**Dank Shield** → allow the listed items. If a category controls the channel, fix the category and sync the channel."
                )[:1024],
                inline=False,
            )

    category_missing = list(report.get("category_missing") or [])
    category = report.get("category")
    if category_missing:
        if "configured_category_missing" in category_missing:
            embed.add_field(
                name="3. Temporary voice category",
                value=(
                    "The saved parent category for temporary Community Hub voice rooms no longer exists. "
                    "Open **Community Hub → Staff Dashboard → Settings** and choose a valid parent category, or clear it to use the Hub channel's category."
                ),
                inline=False,
            )
        else:
            label = getattr(category, "name", None) or "the Community Hub voice category"
            embed.add_field(
                name="3. Temporary voice category",
                value=(
                    f"In **{label}**, Dank Shield needs **Manage Channels** to create temporary game voice rooms. "
                    "On mobile: open the category settings → **Permissions** → **Dank Shield** → allow **Manage Channels**, then run **Check Again**."
                )[:1024],
                inline=False,
            )

    if report["healthy"]:
        embed.add_field(
            name="Sharing defaults",
            value="Public Community Hub groups: **On** • Aggregate/live activity: **Off** until separately enabled.",
            inline=False,
        )
    embed.set_footer(text="HubLink never needs a server ID from the user.")
    return embed


_COMPONENT_BURSTS: dict[tuple[int, int], list[float]] = {}
_COMPONENT_BURST_WINDOW_SECONDS = 10.0
_COMPONENT_BURST_LIMIT = 20
_COMPONENT_BURST_MAX_KEYS = 10000


def _component_action_allowed(interaction: discord.Interaction) -> bool:
    guild_id = int(interaction.guild_id or 0)
    user_id = int(getattr(interaction.user, "id", 0) or 0)
    if user_id <= 0:
        return False
    now = time.monotonic()
    cutoff = now - _COMPONENT_BURST_WINDOW_SECONDS
    key = (guild_id, user_id)
    recent = [stamp for stamp in _COMPONENT_BURSTS.get(key, []) if stamp > cutoff]
    if len(recent) >= _COMPONENT_BURST_LIMIT:
        _COMPONENT_BURSTS[key] = recent
        return False
    recent.append(now)
    _COMPONENT_BURSTS[key] = recent

    if len(_COMPONENT_BURSTS) > _COMPONENT_BURST_MAX_KEYS:
        stale = [
            existing_key
            for existing_key, values in _COMPONENT_BURSTS.items()
            if not values or values[-1] <= cutoff
        ][:1000]
        for existing_key in stale:
            _COMPONENT_BURSTS.pop(existing_key, None)
    return True


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _defer_update(interaction: discord.Interaction) -> None:
    if await safe_defer_interaction(
        interaction,
        ephemeral=False,
        action_name="community_hub_component",
    ):
        return
    raise RuntimeError("Community Hub interaction acknowledgement failed")


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    if await safe_defer_interaction(
        interaction,
        ephemeral=True,
        action_name="community_hub_modal_or_private_action",
    ):
        return
    raise RuntimeError("Community Hub interaction acknowledgement failed")


async def _edit_private_original(
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


async def _followup(interaction: discord.Interaction, content: str) -> None:
    await interaction.followup.send(
        content,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _close_private_panel(interaction: discord.Interaction) -> None:
    await _defer_update(interaction)
    try:
        await interaction.delete_original_response()
        return
    except discord.NotFound:
        return
    except discord.HTTPException:
        pass
    try:
        await interaction.edit_original_response(
            content="Community Hub closed.",
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.NotFound, discord.HTTPException):
        return


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, hub.CommunityStorageUnavailable):
        return "Community Hub storage is unavailable. Staff can open Community Hub Health for the migration/status detail."
    text = _safe_str(exc)
    if not text:
        return "Community Hub could not complete that action."
    text = text.replace("PostgrestAPIError", "").strip(" :")
    return text[:500]


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _private(interaction, "Open your own Community Hub panel to use these controls.")
            return False
        if not _component_action_allowed(interaction):
            await _private(interaction, "You're using Community Hub controls too quickly. Wait a few seconds and try again.")
            return False
        return True


async def _run_session_mutation(
    interaction: discord.Interaction,
    *,
    session_id: str,
    action: str,
    factory: Callable[[], Awaitable[Any]],
    risk_level: str = "moderate",
) -> tuple[str, Any]:
    guild = interaction.guild
    if guild is None:
        return "failed", None
    state, result, _job = await run_exclusive(
        guild_id=int(guild.id),
        actor_id=int(interaction.user.id),
        operation_type=f"community.session.{action}",
        risk_level=risk_level,
        source="discord_command",
        payload={
            "session_id": session_id,
            "action": action,
            "actor_id": int(interaction.user.id),
        },
        concurrency_class="community_session_mutation",
        concurrency_key=session_id,
        timeout_seconds=45.0,
        reject_if_busy=True,
        factory=factory,
    )
    return state, result


async def _operation_feedback(
    interaction: discord.Interaction,
    state: str,
    *,
    duplicate: str = "That action was already processed.",
    busy: str = "That session is already processing another change. Refresh in a moment.",
    failed: str = "That action failed before it could finish.",
) -> bool:
    if state == "duplicate":
        await _followup(interaction, duplicate)
        return False
    if state == "busy":
        await _followup(interaction, busy)
        return False
    if state in {"failed", "partial", "expired", "cancelled"}:
        await _followup(interaction, failed)
        return False
    return True


_STATE_LABELS = {
    "creating": "Creating",
    "open": "Open",
    "forming": "Forming",
    "ready": "Ready",
    "active": "Playing",
    "paused": "Paused",
    "ending": "Ending",
    "ended": "Ended",
    "archived": "Archived",
    "cleaned": "Ended",
    "abandoned": "Abandoned",
    "interrupted": "Interrupted",
    "recovering": "Recovering",
    "failed": "Needs staff attention",
}


def build_session_embed(
    session: dict[str, Any],
    members: list[dict[str, Any]],
) -> discord.Embed:
    counts = hub.summarize_member_counts(members)
    state = _safe_str(session.get("state"), "open")
    ended = state in {"ended", "archived", "cleaned", "failed", "abandoned"}
    color = discord.Color.greyple() if ended else discord.Color.blurple()
    game = _safe_str(session.get("game_name"), "Gaming Session")
    embed = discord.Embed(
        title=f"🎮 {game}",
        description=(
            _safe_str(session.get("notes"))
            or "A Community Hub gaming group. Use the controls below to join or manage your spot."
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Status", value=_STATE_LABELS.get(state, state.title()), inline=True)
    embed.add_field(
        name="Players",
        value=f"{counts['active']} / {_safe_int(session.get('capacity'), 6)}"
        + (f" • {counts['waitlist']} waiting" if counts["waitlist"] else ""),
        inline=True,
    )
    embed.add_field(
        name="Ready",
        value=f"{counts['ready']} / {counts['active']}",
        inline=True,
    )
    style = _safe_str(session.get("play_style"), "casual").replace("_", " ").title()
    mic = _safe_str(session.get("mic_preference"), "optional").replace("_", " ").title()
    embed.add_field(name="Play style", value=style, inline=True)
    embed.add_field(name="Mic", value=mic, inline=True)
    embed.add_field(name="Access", value=_safe_str(session.get("privacy"), "public").replace("_", " ").title(), inline=True)

    links: list[str] = []
    thread_id = _safe_int(session.get("thread_id"), 0)
    voice_id = _safe_int(session.get("voice_channel_id"), 0)
    if thread_id > 0:
        links.append(f"Discussion: <#{thread_id}>")
    if voice_id > 0:
        links.append(f"Voice: <#{voice_id}>")
    if links:
        embed.add_field(name="Session spaces", value="\n".join(links), inline=False)

    if state == "ending":
        embed.add_field(
            name="Closing",
            value="New joins are stopped. Temporary resources are in their configured cleanup grace period.",
            inline=False,
        )
    elif ended:
        embed.add_field(
            name="Session finished",
            value="The old session stays ended. The previous host can use Play Again to create a fresh session.",
            inline=False,
        )

    sid = _safe_str(session.get("id"))
    version = _safe_int(session.get("version"), 1)
    embed.set_footer(text="Dank Shield Community Hub • live session")
    return embed


def _hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 Community Hub",
        description=(
            "Find outside gaming groups and real Discord communities **right from Find Players**, "
            "plus groups hosted here. Start a session, join events or set game notifications."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Find Players",
        value="One search shows free outside gaming groups, opt-in Public Gaming Network servers "
              "and groups hosted here. Outside discovery works without a paid token.",
        inline=False,
    )
    embed.add_field(
        name="Gaming sessions",
        value=(
            "Sessions support ready checks, waitlists, host handoff, pause/resume, temporary discussion/voice spaces, "
            "clean ending, Play Again, restart recovery, and bounded cleanup."
        ),
        inline=False,
    )
    embed.add_field(
        name="Open to Play & Match Safety",
        value=(
            "Open to Play is a short-lived opt-in availability status. Automatic Quick Match pairing is a separate "
            "switch you control. Match Safety privately keeps chosen members out of automatic matches without changing server moderation."
        ),
        inline=False,
    )
    embed.add_field(
        name="Live Captions",
        value=(
            "Optional per-speaker voice-to-text for Community Hub voice sessions. Each participant explicitly controls "
            "their own isolated voice. If they enabled remembered auto-caption for this server, Dank Shield restores that consent automatically when they join an active captioned session."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value=(
            "Community Pulse uses aggregate activity. Dank Shield does not keep a long-term per-member presence or game-history dossier."
        ),
        inline=False,
    )
    embed.add_field(name="Control lifetime", value=private_menu_lifecycle_text(), inline=False)
    return embed



def _live_captions_overview_embed(
    sessions: list[dict[str, Any]],
    manager: Any,
    *,
    user_id: int,
) -> discord.Embed:
    capability = voice_receive_capability()
    enabled = live_captions_enabled()

    if not enabled:
        status = (
            "🟡 **Installed, validation locked.** Live transcription is disabled on this host until the real Discord "
            "DAVE receive soak test is completed. No voice audio is captured while this lock is active."
        )
    elif not capability.available:
        status = f"🔴 **Host not ready.** {capability.reason}."
    else:
        status = "🟢 **Ready on this host.** A host/co-host/staff member can start captions from a session's Manage screen."

    embed = discord.Embed(
        title="📝 Community Hub Live Captions",
        description=(
            f"{status}\n\n"
            "Live Captions keep each Discord speaker on a separate audio stream before speech-to-text. "
            "A participant explicitly enables **Auto-Caption My Voice** once for this server; that remembered consent is then restored automatically in ordinary or Community Hub caption sessions until the member turns it off."
        ),
        color=discord.Color.blurple(),
    )

    if not sessions:
        embed.add_field(
            name="Your groups",
            value="You are not in an active Community Hub group. Join a group or start a gaming session first.",
            inline=False,
        )
    else:
        lines: list[str] = []
        for session in sessions[:8]:
            sid = _safe_str(session.get("id"))
            membership = session.get("membership") if isinstance(session.get("membership"), dict) else {}
            role = _safe_str(membership.get("role"), "member").replace("_", " ").title()
            state = manager.status(sid) if sid else {"active": False, "opted_in_user_ids": []}
            running = bool(state.get("active"))
            opted_in = int(user_id) in {
                _safe_int(value, 0) for value in (state.get("opted_in_user_ids") or [])
            }
            voice_ready = _safe_int(session.get("voice_channel_id"), 0) > 0
            language_mode = (
                "Auto 85+"
                if not state.get("language_codes")
                else ", ".join(str(value) for value in (state.get("language_codes") or []))
            )
            output_mode = {
                "english": "English",
                "bilingual": "Original + English",
            }.get(str(state.get("output_mode") or "original"), "Original")
            lines.append(
                f"• **{_safe_str(session.get('game_name'), 'Gaming Session')}** — {role} • "
                f"{'Captions running' if running else 'Captions off'} • "
                f"{'Your voice opted in' if opted_in else 'Your voice not opted in'} • "
                f"{'Voice room ready' if voice_ready else 'No session voice room'}"
                + (f" • {language_mode} → {output_mode}" if running else "")
            )
        embed.add_field(name="Your groups", value="\n".join(lines)[:1024], inline=False)

    embed.add_field(
        name="How to use it",
        value=(
            "1. Join or create a Community Hub gaming session.\n"
            "2. Host/co-host/staff opens **Manage Session → Live Captions**.\n"
            "3. Each speaker explicitly enables **Auto-Caption My Voice** once for this server. If already enabled, Dank Shield restores it automatically for the active session.\n"
            "4. **/captions → My Language** saves a per-server accuracy hint such as English; Auto still supports all listed languages and code-switching.\n"
            "5. Captions appear in the session discussion destination; the server's shared caption setting controls Original, English, or Original + English text."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value=(
            "Only members who explicitly enabled auto-caption for this server are routed to transcription. That consent and the optional **My Language** hint are remembered per server; raw audio is never persisted. "
            "Leaving the captioned VC or stopping the session immediately clears active audio admission and buffered/in-flight audio, while the remembered preference remains until the member turns it off. "
            "Opted-in audio is streamed to Google Gemini Live Transcribe. If English output is enabled, only finalized transcript text is translated. "
            "This deployment uses Gemini's Free Tier, where Google states Free Tier submitted content may be used to improve its products."
        ),
        inline=False,
    )
    return embed





async def _toggle_live_caption_consent(
    interaction: discord.Interaction,
    session_id: str,
) -> str:
    sid = _safe_str(session_id)
    try:
        members = await hub.list_session_members(sid)
    except hub.CommunityHubError as exc:
        return f"❌ {_error_text(exc)}"

    mine = next(
        (
            row
            for row in members
            if _safe_int(row.get("user_id"), 0) == int(interaction.user.id)
        ),
        None,
    )
    if mine is None or _safe_str(mine.get("role")) == "waitlist":
        return "Join this group before opting your voice into Live Captions."

    manager = ensure_community_voice_caption_manager(interaction.client)
    try:
        enabled = await manager.toggle_consent(sid, int(interaction.user.id))
    except VoiceReceiveUnavailable as exc:
        return f"❌ {exc}"

    if enabled:
        return (
            "✅ **Auto-caption is ON and remembered for this server.** Dank Shield keeps your Discord audio separate from other speakers and automatically restores your consent when you join an active ordinary or Community Hub captioned VC. "
            "Your saved **/captions → My Language** choice is reused automatically. Opted-in audio is streamed to **Google Gemini Live Transcribe**; if English output is enabled, only finalized transcript text is translated. "
            "This deployment uses Gemini's **Free Tier**, where Google states Free Tier submitted content may be used to improve its products. Dank Shield does not save the audio. Use the caption control again to revoke remembered consent."
        )
    return "✅ Auto-caption is OFF for this server. Your remembered consent was revoked and active/buffered/in-flight caption audio was cleared immediately."

class StartSessionModal(discord.ui.Modal, title="Start a Gaming Session"):
    game = discord.ui.TextInput(
        label="Game",
        placeholder="Minecraft",
        min_length=1,
        max_length=80,
    )
    group_size = discord.ui.TextInput(
        label="Group size",
        placeholder="6",
        default="6",
        min_length=1,
        max_length=2,
    )
    play_style = discord.ui.TextInput(
        label="Play style",
        placeholder="casual, competitive, beginner, or any",
        default="casual",
        min_length=2,
        max_length=20,
    )
    mic = discord.ui.TextInput(
        label="Mic preference",
        placeholder="optional, preferred, required, or no mic",
        default="optional",
        min_length=2,
        max_length=20,
    )
    notes = discord.ui.TextInput(
        label="Session notes",
        placeholder="Beginner friendly, survival world, cross-play, etc.",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "Community Hub sessions can only be created inside a server.")

        try:
            capacity = int(str(self.group_size.value).strip())
        except Exception:
            return await _followup(interaction, "Group size must be a number between 2 and the server's configured limit.")

        style = _safe_str(self.play_style.value).lower().replace(" ", "_")
        mic = _safe_str(self.mic.value).lower().replace(" ", "_")
        if mic == "no":
            mic = "no_mic"

        runtime = ensure_community_hub_runtime(interaction.client)

        async def _factory() -> dict[str, Any]:
            session = await hub.create_session(
                guild_id=int(guild.id),
                host_id=int(interaction.user.id),
                idempotency_key=f"modal:{int(interaction.id)}",
                game_name=str(self.game.value),
                notes=str(self.notes.value or ""),
                capacity=capacity,
                mic_preference=mic,
                play_style=style,
                privacy="public",
            )
            return await runtime.provision_session(interaction, session)

        state, result, _job = await run_exclusive(
            guild_id=int(guild.id),
            actor_id=int(interaction.user.id),
            operation_type="community.session.create",
            risk_level="moderate",
            source="discord_command",
            payload={
                "actor_id": int(interaction.user.id),
                "game": hub.normalize_game_name(self.game.value),
            },
            idempotency_key=f"community:create:{int(interaction.id)}",
            concurrency_class="community_session_mutation",
            concurrency_key=f"creator:{int(interaction.user.id)}",
            timeout_seconds=60.0,
            reject_if_busy=True,
            factory=_factory,
        )

        if state == "duplicate":
            return await _followup(interaction, "That session creation was already submitted.")
        if state == "busy":
            return await _followup(interaction, "You already have a session creation operation running.")
        if state != "succeeded" or not isinstance(result, dict):
            return await _followup(interaction, "The gaming session could not be created safely.")

        session = result.get("session") if isinstance(result.get("session"), dict) else {}
        warnings = [str(item) for item in (result.get("warnings") or []) if item]
        text = f"✅ {_safe_str(session.get('game_name'), 'Gaming')} session created."
        if warnings:
            text += "\n⚠️ " + "\n⚠️ ".join(warnings[:4])
        await _followup(interaction, text)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if not interaction.response.is_done():
            await _defer_ephemeral(interaction)
        await _followup(interaction, f"Community Hub could not create that session: {_error_text(error)}")


class QuickMatchModal(discord.ui.Modal, title="Quick Match"):
    game = discord.ui.TextInput(
        label="Game",
        placeholder="Minecraft",
        min_length=1,
        max_length=80,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        guild_id = int(interaction.guild_id or 0)
        try:
            game = hub.normalize_game_name(self.game.value)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        runtime = ensure_community_hub_runtime(interaction.client)
        request_key = f"quick:{int(interaction.id)}"

        async def _factory() -> Any:
            result = await hub.quick_match(
                guild_id,
                int(interaction.user.id),
                game,
                idempotency_key=request_key,
            )
            if not isinstance(result, dict) or not bool(result.get("created_match")):
                return result

            match_user_id = _safe_int(result.get("match_user_id"), 0)
            session = result.get("session") if isinstance(result.get("session"), dict) else {}
            if not session or match_user_id <= 0:
                raise hub.CommunityHubError("Quick Match formed an incomplete match result.")

            if bool(result.get("replayed")) and _safe_str(session.get("state")) != "creating":
                if _safe_str(session.get("state")) in {"open", "forming", "ready", "active", "paused"}:
                    return result
                raise hub.CommunityHubError("The previous Quick Match session is no longer recoverable.")

            try:
                provisioned = await runtime.provision_session(interaction, session)
            except Exception:
                # The DB claimed this member's auto-match switch before creating
                # the session. Restore it when Discord provisioning fails so a
                # transient channel/permission problem does not silently opt them out.
                try:
                    await hub.set_availability_auto_match(
                        guild_id,
                        match_user_id,
                        True,
                        game_name=game,
                    )
                except hub.CommunityHubError:
                    pass
                raise

            current = provisioned.get("session") if isinstance(provisioned.get("session"), dict) else session
            result["session"] = current
            result["warnings"] = [
                str(item)
                for item in (provisioned.get("warnings") or [])
                if item
            ]

            # A successful match consumes both members' same-game availability.
            # Failure here is non-destructive: the claimed member remains
            # auto-match off, so they cannot be duplicated into another group.
            try:
                await hub.clear_availability(guild_id, match_user_id, game_name=game)
                await hub.clear_availability(
                    guild_id,
                    int(interaction.user.id),
                    game_name=game,
                )
            except hub.CommunityHubError:
                pass
            return result

        # PostgreSQL owns candidate selection and row locking. The operation queue
        # owns the whole interaction, including Discord provisioning when a new
        # group is formed, so retries cannot split the DB and Discord halves.
        state, result, _job = await run_exclusive(
            guild_id=guild_id,
            actor_id=int(interaction.user.id),
            operation_type="community.session.quick_match",
            risk_level="moderate",
            source="discord_command",
            payload={"game": game, "actor_id": int(interaction.user.id)},
            idempotency_key=f"community:quick:{int(interaction.id)}",
            concurrency_class="community_session_mutation",
            concurrency_key=f"quick-match:{int(interaction.user.id)}",
            timeout_seconds=60.0,
            reject_if_busy=True,
            factory=_factory,
        )
        if state == "duplicate":
            return await _followup(interaction, "That Quick Match request was already processed.")
        if state == "busy":
            return await _followup(interaction, "You already have a Quick Match request running.")
        if state != "succeeded":
            return await _followup(interaction, "Quick Match could not complete safely.")
        if not isinstance(result, dict):
            return await _followup(
                interaction,
                f"No open **{game}** group has space and nobody has enabled Quick Match for that game right now.",
            )

        current = result.get("session") if isinstance(result.get("session"), dict) else {}
        if not current:
            return await _followup(interaction, "Quick Match completed without a usable session result.")

        created_match = bool(result.get("created_match"))
        match_user_id = _safe_int(result.get("match_user_id"), 0)
        runtime.cancel_scheduled_cleanup(_safe_str(current.get("id")))
        await hub.bump_hourly_metric(guild_id, "joins", 1)
        await hub.bump_game_metric(guild_id, _safe_str(current.get("game_name"), game), "joins", 1)
        if not created_match:
            await runtime.refresh_session_card(current)

        if created_match and match_user_id > 0 and interaction.guild is not None:
            matched_member = interaction.guild.get_member(match_user_id)
            panel_channel_id = _safe_int(current.get("panel_channel_id"), 0)
            panel_message_id = _safe_int(current.get("panel_message_id"), 0)
            jump_url = (
                f"https://discord.com/channels/{guild_id}/{panel_channel_id}/{panel_message_id}"
                if panel_channel_id > 0 and panel_message_id > 0
                else ""
            )
            if matched_member is not None:
                try:
                    await matched_member.send(
                        (
                            f"⚡ Quick Match paired you for **{_safe_str(current.get('game_name'), game)}** "
                            f"in **{interaction.guild.name}**."
                            + (f"\n{jump_url}" if jump_url else "\nOpen Community Hub → My Groups to see it.")
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass

        warnings = [str(item) for item in (result.get("warnings") or []) if item]
        if created_match:
            text = (
                f"✅ Quick Match formed a new **{_safe_str(current.get('game_name'), game)}** group"
                + (f" with <@{match_user_id}>." if match_user_id > 0 else ".")
            )
        else:
            text = f"✅ Quick Match joined **{_safe_str(current.get('game_name'), game)}**."
        if warnings:
            text += "\n⚠️ " + "\n⚠️ ".join(warnings[:4])
        await _followup(interaction, text)


def _availability_embed(rows: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="🟢 Open to Play",
        description=(
            "Temporarily mark yourself available for a game. Other members can browse your active listing, "
            "and it expires automatically. Quick Match is a separate explicit opt-in: when enabled, Dank Shield "
            "may pair you into a new same-game group if someone searches and no open group already exists."
        ),
        color=discord.Color.blurple(),
    )
    if not rows:
        embed.add_field(
            name="Not currently open to play",
            value="Set a game and a short availability window below.",
            inline=False,
        )
        return embed

    for row in rows[:8]:
        expires = _safe_str(row.get("expires_at"))
        try:
            stamp = int(datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp())
            expires_text = f"<t:{stamp}:R>"
        except Exception:
            expires_text = "soon"
        note = _safe_str(row.get("note"))[:240]
        value = (
            f"{_safe_str(row.get('play_style'), 'any').replace('_', ' ').title()} • "
            f"Mic: {_safe_str(row.get('mic_preference'), 'optional').replace('_', ' ').title()} • "
            f"Quick Match: {'On' if bool(row.get('auto_match')) else 'Off'} • "
            f"expires {expires_text}"
        )
        if note:
            value += f"\n{note}"
        embed.add_field(
            name=_safe_str(row.get("game_name"), "Game")[:256],
            value=value[:1024],
            inline=False,
        )
    if len(rows) > 8:
        embed.set_footer(text=f"{len(rows)} active Open to Play entries • showing the first 8.")
    return embed


class OpenToPlayModal(discord.ui.Modal, title="Open to Play"):
    game = discord.ui.TextInput(
        label="Game",
        placeholder="Minecraft",
        min_length=1,
        max_length=80,
    )
    duration = discord.ui.TextInput(
        label="Available for how many hours?",
        default="2",
        min_length=1,
        max_length=1,
    )
    play_style = discord.ui.TextInput(
        label="Play style",
        default="any",
        placeholder="casual, competitive, beginner, or any",
        max_length=20,
    )
    mic = discord.ui.TextInput(
        label="Mic preference",
        default="optional",
        placeholder="optional, preferred, required, or no mic",
        max_length=20,
    )
    note = discord.ui.TextInput(
        label="Optional note",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            hours = int(str(self.duration.value).strip())
        except Exception:
            return await _followup(interaction, "Availability must be between 1 and 8 hours.")
        if not 1 <= hours <= 8:
            return await _followup(interaction, "Availability must be between 1 and 8 hours.")

        style = _safe_str(self.play_style.value).lower().replace(" ", "_")
        mic = _safe_str(self.mic.value).lower().replace(" ", "_")
        if mic in {"no", "none"}:
            mic = "no_mic"

        try:
            row = await hub.set_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                str(self.game.value),
                duration_hours=hours,
                play_style=style,
                mic_preference=mic,
                note=str(self.note.value or ""),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        await _followup(
            interaction,
            f"✅ You're Open to Play for **{_safe_str(row.get('game_name'), 'that game')}** "
            f"for up to {hours} hour{'s' if hours != 1 else ''}.",
        )


class OpenToPlayView(_OwnedView):
    def __init__(self, owner_id: int, rows: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.rows = list(rows)
        enabled = bool(self.rows) and all(bool(row.get("auto_match")) for row in self.rows)
        for item in self.children:
            if getattr(item, "custom_id", None) == "dank:hub:available:automatch:v1":
                item.label = "Disable Quick Match" if enabled else "Enable Quick Match"
                break

    @discord.ui.button(
        label="Set / Update",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="dank:hub:available:set:v1",
        row=0,
    )
    async def set_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(OpenToPlayModal())

    @discord.ui.button(
        label="Enable Quick Match",
        emoji="⚡",
        style=discord.ButtonStyle.primary,
        custom_id="dank:hub:available:automatch:v1",
        row=0,
    )
    async def auto_match(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            rows = await hub.list_user_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
            if not rows:
                return await _followup(interaction, "Set an Open to Play game before enabling Quick Match.")
            enable = not all(bool(row.get("auto_match")) for row in rows)
            await hub.set_availability_auto_match(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                enable,
            )
            rows = await hub.list_user_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        await _edit_private_original(
            interaction,
            content=None,
            embed=_availability_embed(rows),
            view=OpenToPlayView(self.owner_id, rows),
        )
        await _followup(
            interaction,
            "✅ Quick Match is enabled for your active Open to Play games."
            if enable
            else "Quick Match auto-pairing is disabled. Your Open to Play listings remain visible.",
        )

    @discord.ui.button(
        label="Clear All",
        emoji="🧹",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:hub:available:clear:v1",
        row=0,
    )
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            await hub.clear_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
            rows = await hub.list_user_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        await _edit_private_original(
            interaction,
            content=None,
            embed=_availability_embed(rows),
            view=OpenToPlayView(self.owner_id, rows),
        )
        await _followup(interaction, "✅ Open to Play status cleared.")

    @discord.ui.button(
        label="Community Hub",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:hub:available:back:v1",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_hub_embed(),
            view=CommunityHubView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:hub:available:close:v1",
        row=0,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)


def _match_safety_embed(blocked_ids: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Match Safety",
        description=(
            "Privately keep specific members out of your automatic Community Hub matches. "
            "This does not kick, ban, timeout, punish, or publicly label anyone."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Your exclusions",
        value=(
            "\n".join(f"• <@{uid}>" for uid in blocked_ids[:20])
            if blocked_ids
            else "No personal match exclusions."
        ),
        inline=False,
    )
    embed.set_footer(text="This only affects Community Hub matching for you.")
    return embed


class MatchSafetyUserSelect(discord.ui.UserSelect):
    def __init__(self, owner_id: int, blocked_ids: list[str]) -> None:
        self.owner_id = int(owner_id)
        self.blocked_ids = set(blocked_ids)
        super().__init__(
            placeholder="Choose a member to block/unblock from matching",
            min_values=1,
            max_values=1,
            custom_id="dank:hub:safety:user:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        if int(target.id) == int(interaction.user.id):
            return await _private(interaction, "You cannot add yourself to your own match exclusions.")

        await _defer_update(interaction)
        blocked = str(int(target.id)) not in self.blocked_ids
        try:
            await hub.set_match_block(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                int(target.id),
                blocked,
            )
            current = await hub.list_match_blocks(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        await _edit_private_original(
            interaction,
            content=None,
            embed=_match_safety_embed(current),
            view=MatchSafetyView(self.owner_id, current),
        )
        await _followup(
            interaction,
            "✅ Member excluded from your automatic matching."
            if blocked
            else "✅ Match exclusion removed.",
        )


class MatchSafetyView(_OwnedView):
    def __init__(self, owner_id: int, blocked_ids: list[str]) -> None:
        super().__init__(owner_id)
        self.add_item(MatchSafetyUserSelect(owner_id, blocked_ids))

    @discord.ui.button(
        label="Community Hub",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:hub:safety:back:v1",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_hub_embed(),
            view=CommunityHubView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:hub:safety:close:v1",
        row=1,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)


class CommunityHubView(_OwnedView):
    @discord.ui.button(label="Find Players", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:find:v1", row=0)
    async def find_players(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_unified_player_discovery(interaction, self.owner_id)

    @discord.ui.button(label="Start Gaming Session", emoji="➕", style=discord.ButtonStyle.success, custom_id="dank:hub:start:v1", row=0)
    async def start_session(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StartSessionModal())

    @discord.ui.button(label="My Groups", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="dank:hub:mine:v1", row=0)
    async def my_groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            sessions = await hub.list_user_sessions(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                active_only=True,
                limit=25,
            )
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_my_groups_embed(sessions),
            view=MyGroupsView(self.owner_id, sessions),
        )

    @discord.ui.button(label="Community Pulse", emoji="📈", style=discord.ButtonStyle.secondary, custom_id="dank:hub:pulse:v1", row=0)
    async def pulse(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _edit_private_original(interaction, content="Community Pulse requires a server.", embed=None, view=CommunityHubView(self.owner_id))
        runtime = ensure_community_hub_runtime(interaction.client)
        try:
            pulse = await runtime.pulse_snapshot(guild)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_pulse_embed(pulse),
            view=PulseView(self.owner_id),
        )

    @discord.ui.button(label="Events", emoji="📅", style=discord.ButtonStyle.secondary, custom_id="dank:hub:events:v1", row=0)
    async def events(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            events = await hub.list_upcoming_events(int(interaction.guild_id or 0), limit=25)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_events_embed(events),
            view=EventsView(self.owner_id, events),
        )

    @discord.ui.button(label="Quick Match", emoji="⚡", style=discord.ButtonStyle.primary, custom_id="dank:hub:quick:v1", row=1)
    async def quick_match(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(QuickMatchModal())

    @discord.ui.button(label="Game Notifications", emoji="🔔", style=discord.ButtonStyle.secondary, custom_id="dank:hub:notif:v1", row=1)
    async def notifications(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            prefs = await hub.list_user_notification_preferences(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_notification_embed(prefs),
            view=NotificationView(self.owner_id),
        )

    @discord.ui.button(label="Open to Play", emoji="🟢", style=discord.ButtonStyle.secondary, custom_id="dank:hub:available:v1", row=1)
    async def available(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            rows = await hub.list_user_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_availability_embed(rows),
            view=OpenToPlayView(self.owner_id, rows),
        )

    @discord.ui.button(label="Partner Groups", emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partners:v1", row=1)
    async def partners(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(interaction, self.owner_id, partner=True)

    @discord.ui.button(label="Live Captions", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="dank:hub:captions:v1", row=1)
    async def live_captions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            sessions = await hub.list_user_sessions(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                active_only=True,
                limit=25,
            )
        except hub.CommunityHubError as exc:
            return await _edit_private_original(
                interaction,
                content=f"❌ {_error_text(exc)}",
                embed=None,
                view=CommunityHubView(self.owner_id),
            )
        manager = ensure_community_voice_caption_manager(interaction.client)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_live_captions_overview_embed(
                sessions,
                manager,
                user_id=int(interaction.user.id),
            ),
            view=LiveCaptionsHomeView(self.owner_id),
        )

    @discord.ui.button(label="Browse Public Servers", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:v1", row=3)
    async def public_network(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_public_lfg_network(interaction, self.owner_id)

    @discord.ui.button(label="More Gaming Sources", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:v1", row=2)
    async def external_groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_players_home(interaction, self.owner_id)

    @discord.ui.button(label="Staff Dashboard", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:v1", row=2)
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Staff Dashboard requires server management authority.")
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)

    @discord.ui.button(label="Match Safety", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:safety:v1", row=2)
    async def match_safety(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            blocked = await hub.list_match_blocks(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_match_safety_embed(blocked),
            view=MatchSafetyView(self.owner_id, blocked),
        )

    @discord.ui.button(label="Back to Dank Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank:hub:home:v1", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import replace_with_compact_dank_home
        await replace_with_compact_dank_home(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="dank:hub:close:v1", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)


async def open_community_hub(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
    recovery_notice: Optional[str] = None,
) -> None:
    ensure_community_hub_runtime(interaction.client)
    if interaction.guild is None:
        return await _private(interaction, "Community Hub is available inside servers.")
    if replace_message:
        await interaction.response.edit_message(
            content=recovery_notice,
            embed=_hub_embed(),
            view=CommunityHubView(int(interaction.user.id)),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    await _private(
        interaction,
        content=recovery_notice or "",
        embed=_hub_embed(),
        view=CommunityHubView(int(interaction.user.id)),
    )


def _find_players_embed(
    sessions: list[dict[str, Any]],
    *,
    partner: bool,
    partner_activity: Optional[list[dict[str, Any]]] = None,
    availability_summary: Optional[dict[str, Any]] = None,
    game_query: str = "",
    play_style: str = "",
    mic_preference: str = "",
    page: int = 0,
    has_next: bool = False,
) -> discord.Embed:
    title = "🌐 Find Players Across Partner Servers" if partner else "🔎 Find Players"
    description = (
        "Approved partner servers only expose public session summaries. Raw member presence is not shared."
        if partner
        else (
            "Browse open groups or use Quick Match. Open to Play listings are opt-in and expire automatically; "
            "automatic pairing only uses members who separately enabled Quick Match."
        )
    )
    if game_query:
        description += (
            f"\n\n🎮 Game filter: **{discord.utils.escape_markdown(game_query)}** "
            "(up to 100 recent authorized source results)."
        )
    if play_style or mic_preference:
        preferences = []
        if play_style:
            preferences.append(f"Style: {play_style.replace('_', ' ').title()}")
        if mic_preference:
            preferences.append(f"Mic: {mic_preference.replace('_', ' ').title()}")
        description += "\n🎚️ " + " • ".join(preferences) + " (exact session preferences)"
    if page or has_next:
        description += (
            f"\n📄 Page {page + 1} of recent matching groups. "
            "Use the Filters menu to browse more."
        )
    description += "\n\nSources: local sessions, consented HubLink partners, or read-only The100.io listings."
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    if partner and partner_activity:
        lines: list[str] = []
        for row in partner_activity[:10]:
            line = (
                f"• **{_safe_str(row.get('guild_name'), 'Partner Server')}**: "
                f"{_safe_int(row.get('active_groups'))} groups • "
                f"{_safe_int(row.get('voice_members'))} in voice • "
                f"{_safe_int(row.get('open_to_play_members'))} Open to Play"
            )
            if bool(row.get("presence_available")):
                line += (
                    f" • {_safe_int(row.get('online_members'))} online"
                    f" • {_safe_int(row.get('gaming_members'))} gaming"
                )
            lines.append(line)
        if lines:
            embed.add_field(
                name="Partner activity",
                value="\n".join(lines)[:1024],
                inline=False,
            )

    if not partner and isinstance(availability_summary, dict):
        unique_users = _safe_int(availability_summary.get("unique_users"), 0)
        auto_match_users = _safe_int(availability_summary.get("auto_match_users"), 0)
        top_games = list(availability_summary.get("top_games") or [])
        if top_games:
            game_lines = [
                (
                    f"• **{_safe_str(row.get('name'), 'Game')}**: "
                    f"{_safe_int(row.get('count'))} available"
                    + (
                        f" • {_safe_int(row.get('auto_match_count'))} Quick Match"
                        if _safe_int(row.get("auto_match_count")) > 0
                        else ""
                    )
                )
                for row in top_games[:6]
            ]
            detail = "\n".join(game_lines)
        else:
            detail = "Nobody is currently marked Open to Play."
        embed.add_field(
            name=f"🟢 Open to Play • {unique_users} available • {auto_match_users} Quick Match",
            value=detail[:1024],
            inline=False,
        )

    if not sessions:
        embed.add_field(
            name="No matching groups" if (game_query or play_style or mic_preference) else "No open groups right now",
            value=(
                "No recent groups matched those filters. Clear filters or adjust your search."
                if (game_query or play_style or mic_preference)
                else (
                    "Quick Match can form a new group when another member has explicitly enabled Quick Match "
                    "for the same game. You can also start a session yourself."
                    if not partner
                    else "No public groups are currently shared by approved partner servers."
                )
            ),
            inline=False,
        )
        return embed

    for session in sessions[:8]:
        game = _safe_str(session.get("game_name"), "Game")
        state = _STATE_LABELS.get(_safe_str(session.get("state")), _safe_str(session.get("state"), "Open").title())
        cap = _safe_int(session.get("capacity"), 6)
        source = f" • Server {session.get('guild_id')}" if partner else ""
        style = _safe_str(session.get("play_style")).replace("_", " ").title()
        mic = _safe_str(session.get("mic_preference")).replace("_", " ").title()
        prefs = (f" • {style}" if style else "") + (f" • Mic: {mic}" if mic else "")
        embed.add_field(name=game[:256], value=f"{state} • up to {cap} players{source}{prefs}", inline=False)
    if len(sessions) > 8:
        embed.set_footer(text=f"{len(sessions)} groups available • use the selector for the full list")
    return embed

def _available_players_embed(game: str, rows: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🟢 Open to Play • {game}",
        description=(
            "These members explicitly marked themselves available for this game. "
            "Quick Match still prefers an existing open group first, and only auto-pairs members whose Quick Match switch is on."
        ),
        color=discord.Color.blurple(),
    )
    if not rows:
        embed.add_field(
            name="Nobody else is listed right now",
            value="Availability expires automatically, so this list only reflects active Open to Play entries.",
            inline=False,
        )
        return embed

    for row in rows[:8]:
        user_id = _safe_int(row.get("user_id"), 0)
        expires = _safe_str(row.get("expires_at"))
        try:
            stamp = int(datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp())
            expires_text = f"<t:{stamp}:R>"
        except Exception:
            expires_text = "soon"
        value = (
            f"{_safe_str(row.get('play_style'), 'any').replace('_', ' ').title()} • "
            f"Mic: {_safe_str(row.get('mic_preference'), 'optional').replace('_', ' ').title()} • "
            f"Quick Match: {'On' if bool(row.get('auto_match')) else 'Off'} • expires {expires_text}"
        )
        note = _safe_str(row.get("note"))[:240]
        if note:
            value += f"\n{note}"
        embed.add_field(
            name=f"<@{user_id}>" if user_id > 0 else "Available member",
            value=value[:1024],
            inline=False,
        )
    if len(rows) > 8:
        embed.set_footer(text=f"{len(rows)} members are Open to Play for {game}; showing the first 8.")
    return embed


class AvailableGameSelect(discord.ui.Select):
    def __init__(
        self,
        owner_id: int,
        availability_summary: dict[str, Any],
        *,
        selected_game: str = "",
    ) -> None:
        self.owner_id = int(owner_id)
        games = list(availability_summary.get("top_games") or [])
        options = [
            discord.SelectOption(
                label=_safe_str(row.get("name"), "Game")[:100],
                value=_safe_str(row.get("name"), "Game")[:100],
                description=(
                    f"{_safe_int(row.get('count'))} available"
                    + (
                        f" • {_safe_int(row.get('auto_match_count'))} Quick Match"
                        if _safe_int(row.get("auto_match_count")) > 0
                        else ""
                    )
                )[:100],
                emoji="🟢",
                default=bool(
                    selected_game
                    and _safe_str(row.get("name")).casefold() == selected_game.casefold()
                ),
            )
            for row in games[:25]
            if _safe_str(row.get("name"))
        ]
        super().__init__(
            placeholder="Browse Open to Play by game",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="Nobody is Open to Play", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:find:availablegame:v1",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        game = self.values[0]
        if game == "none":
            return
        await _defer_update(interaction)
        guild_id = int(interaction.guild_id or 0)
        try:
            rows = await hub.list_available_users(guild_id, game, limit=25)
            sessions = await hub.list_active_sessions(guild_id, limit=25)
            availability = await hub.availability_summary(guild_id, limit=1000)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(
                interaction,
                content=f"❌ {_error_text(exc)}",
                embed=None,
                view=CommunityHubView(self.owner_id),
            )

        rows = [
            row
            for row in rows
            if _safe_int(row.get("user_id"), 0) != int(interaction.user.id)
        ]
        await _edit_private_original(
            interaction,
            content=None,
            embed=_available_players_embed(game, rows),
            view=FindPlayersView(
                self.owner_id,
                sessions,
                partner=False,
                availability_summary=availability,
                selected_game=game,
            ),
        )


class SessionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, sessions: list[dict[str, Any]], *, partner: bool) -> None:
        self.owner_id = int(owner_id)
        self.sessions = {_safe_str(row.get("id")): row for row in sessions if row.get("id")}
        self.partner = bool(partner)
        options: list[discord.SelectOption] = []
        for row in sessions[:25]:
            sid = _safe_str(row.get("id"))
            game = _safe_str(row.get("game_name"), "Game")[:100]
            state = _STATE_LABELS.get(_safe_str(row.get("state")), "Open")
            desc = f"{state} • capacity {_safe_int(row.get('capacity'), 6)}"
            if partner:
                desc += f" • server {_safe_str(row.get('guild_id'))}"
            options.append(discord.SelectOption(label=game, value=sid, description=desc[:100], emoji="🎮"))
        super().__init__(
            placeholder="Choose a gaming group",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No active groups", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:find:select:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        sid = self.values[0]
        if sid == "none":
            return
        await _defer_update(interaction)
        guild_id = int(interaction.guild_id or 0)
        try:
            if self.partner:
                # Re-evaluate both HubLink sides' discovery consent on every
                # click. A stale select must not expose a revoked partner card.
                approved = await hub.list_partner_discovery_sessions(guild_id, limit=100)
                session = next((row for row in approved if _safe_str(row.get("id")) == sid), None)
            else:
                # Local choices are snapshots too; require a current, scoped
                # row before opening details or reading session members.
                session = await hub.get_session(sid, guild_id=guild_id)
                if _safe_str(session.get("state")) not in hub.ACTIVE_SESSION_STATES:
                    session = None
        except hub.CommunityHubError as exc:
            return await _edit_private_original(
                interaction, content=f"❌ {_error_text(exc)}",
                embed=None, view=CommunityHubView(self.owner_id),
            )
        if session is None:
            return await _edit_private_original(
                interaction, content="This group is no longer publicly available. Refresh Find Players.",
                embed=None, view=CommunityHubView(self.owner_id),
            )
        if self.partner and int(_safe_int(session.get("guild_id"), 0)) != guild_id:
            embed = discord.Embed(
                title=f"🌐 {_safe_str(session.get('game_name'), 'Partner Group')}",
                description=(
                    "This group belongs to an approved partner server. Community Hub intentionally does not expose "
                    "raw member presence or bypass that server's membership and moderation boundaries."
                ),
                color=discord.Color.blurple(),
            )
            panel_channel = _safe_int(session.get("panel_channel_id"), 0)
            panel_message = _safe_int(session.get("panel_message_id"), 0)
            if panel_channel and panel_message:
                embed.add_field(
                    name="Open in partner server",
                    value=f"https://discord.com/channels/{_safe_int(session.get('guild_id'))}/{panel_channel}/{panel_message}",
                    inline=False,
                )
            return await _edit_private_original(
                interaction,
                content=None,
                embed=embed,
                view=FindPlayersView(self.owner_id, list(self.sessions.values()), partner=True),
            )
        try:
            members = await hub.list_session_members(sid)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=build_session_embed(session, members),
            view=SessionDetailView(self.owner_id, session_id=sid, return_kind="find"),
        )


class SessionPreferenceSelect(discord.ui.Select):
    """One native mobile select for both durable session preference fields."""

    def __init__(
        self, owner_id: int, *, partner: bool, game_query: str = "",
        play_style: str = "", mic_preference: str = "",
        page: int = 0, has_next: bool = False,
    ) -> None:
        self.owner_id = int(owner_id)
        self.partner = bool(partner)
        self.game_query = str(game_query or "")[:80]
        self.play_style = play_style
        self.mic_preference = mic_preference
        self.page = max(0, min(int(page), 3))
        self.has_next = bool(has_next)
        items = [
            ("Reset session preferences", "clear", "🧹"),
            ("Any play style", "style:off", "🎮"),
            ("Casual", "style:casual", "🌿"),
            ("Competitive", "style:competitive", "🏆"),
            ("Beginner", "style:beginner", "🌱"),
            ("No style preference", "style:any", "🎲"),
            ("Any microphone policy", "mic:off", "🎤"),
            ("Mic optional", "mic:optional", "🔈"),
            ("Mic preferred", "mic:preferred", "🔊"),
            ("Mic required", "mic:required", "🎙️"),
            ("No microphone", "mic:no_mic", "🔇"),
        ]
        if self.page > 0:
            items.insert(0, ("Previous groups", "page:prev", "⬅️"))
        if self.has_next:
            items.insert(0, ("More groups", "page:next", "➡️"))
        options = [
            discord.SelectOption(label=label, value=value, emoji=emoji)
            for label, value, emoji in items
        ]
        description = (
            " • ".join(filter(None, (
                f"Style: {play_style.replace('_', ' ')}" if play_style else "",
                f"Mic: {mic_preference.replace('_', ' ')}" if mic_preference else "",
            ))) or "All styles and mic policies"
        )
        super().__init__(
            placeholder=f"🎚️ Filters / Page {self.page + 1} • {description}"[:150],
            options=options, min_values=1, max_values=1,
            custom_id="dank:hub:find:preferences:v1",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        style = self.play_style
        mic = self.mic_preference
        if choice in {"page:prev", "page:next"}:
            if choice == "page:prev" and self.page == 0:
                return await _private(interaction, "Already at the first page.")
            if choice == "page:next" and not self.has_next:
                return await _private(interaction, "No more recent groups found.")
            await _defer_update(interaction)
            return await _open_find_players_source(
                interaction, self.owner_id, partner=self.partner,
                game_query=self.game_query, play_style=style, mic_preference=mic,
                page=self.page + (1 if choice == "page:next" else -1),
            )
        if choice == "clear":
            style, mic = "", ""
        elif choice.startswith("style:"):
            value = choice.split(":", 1)[1]
            style = "" if value == "off" else value
        elif choice.startswith("mic:"):
            value = choice.split(":", 1)[1]
            mic = "" if value == "off" else value
        else:
            return await _private(interaction, "Unknown group preference filter.")
        await _defer_update(interaction)
        await _open_find_players_source(
            interaction, self.owner_id, partner=self.partner,
            game_query=self.game_query, play_style=style, mic_preference=mic,
        )


class FindPlayersView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        sessions: list[dict[str, Any]],
        *,
        partner: bool,
        availability_summary: Optional[dict[str, Any]] = None,
        selected_game: str = "",
        game_query: str = "",
        play_style: str = "",
        mic_preference: str = "",
        page: int = 0,
        has_next: bool = False,
    ) -> None:
        super().__init__(owner_id)
        self.sessions = list(sessions)
        self.partner = bool(partner)
        self.game_query = str(game_query or "")[:80]
        self.play_style = play_style
        self.mic_preference = mic_preference
        self.page = max(0, min(int(page), 3))
        self.has_next = bool(has_next)
        self.availability_summary = dict(availability_summary or {})
        self.selected_game = _safe_str(selected_game)
        self.add_item(SessionSelect(owner_id, sessions, partner=partner))
        self.local_source.disabled = not self.partner
        self.partner_source.disabled = self.partner
        self.clear_game.disabled = not bool(self.game_query or self.play_style or self.mic_preference)
        self.add_item(SessionPreferenceSelect(
            owner_id, partner=self.partner, game_query=self.game_query,
            play_style=self.play_style, mic_preference=self.mic_preference,
            page=self.page, has_next=self.has_next,
        ))
        if not self.partner:
            self.add_item(
                AvailableGameSelect(
                    owner_id,
                    self.availability_summary,
                    selected_game=self.selected_game,
                )
            )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:refresh:v1", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(
            interaction, self.owner_id, partner=self.partner,
            game_query=self.game_query, play_style=self.play_style,
            mic_preference=self.mic_preference,
        )

    @discord.ui.button(label="Quick Match", emoji="⚡", style=discord.ButtonStyle.primary, custom_id="dank:hub:find:quick:v1", row=1)
    async def quick_match(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.partner:
            return await _private(interaction, "Quick Match only runs inside this server. Partner groups keep their own membership boundary.")
        await interaction.response.send_modal(QuickMatchModal())

    @discord.ui.button(label="Open to Play", emoji="🟢", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:available:v1", row=1)
    async def open_to_play(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.partner:
            return await _private(interaction, "Open to Play is managed inside your current server.")
        await _defer_update(interaction)
        try:
            rows = await hub.list_user_availability(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_availability_embed(rows),
            view=OpenToPlayView(self.owner_id, rows),
        )

    @discord.ui.button(label="Local Groups", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:local:v1", row=3)
    async def local_source(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(
            interaction, self.owner_id, partner=False, game_query=self.game_query,
            play_style=self.play_style, mic_preference=self.mic_preference,
        )

    @discord.ui.button(label="Partner Groups", emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:partners:v1", row=3)
    async def partner_source(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(
            interaction, self.owner_id, partner=True, game_query=self.game_query,
            play_style=self.play_style, mic_preference=self.mic_preference,
        )

    @discord.ui.button(label="Search Game", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:find:game:v1", row=3)
    async def search_game(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DiscoveryGameSearchModal(
            self.owner_id, source="partner" if self.partner else "local",
            current_query=self.game_query, play_style=self.play_style,
            mic_preference=self.mic_preference,
        ))

    @discord.ui.button(label="Clear Filters", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:clear:v1", row=3)
    async def clear_game(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(interaction, self.owner_id, partner=self.partner)

    @discord.ui.button(label="All Gaming Groups", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:external:v1", row=3)
    async def external_groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_unified_player_discovery(interaction, self.owner_id, game=self.game_query[:55])

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="dank:hub:find:close:v1", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)

async def _open_find_players_source(
    interaction: discord.Interaction,
    owner_id: int,
    *,
    partner: bool,
    game_query: str = "",
    play_style: str = "",
    mic_preference: str = "",
    page: int = 0,
) -> None:
    """Refresh only the selected authorized source; never merge private guild data."""
    guild_id = int(interaction.guild_id or 0)
    if guild_id <= 0:
        return await _edit_private_original(
            interaction, content="Community Hub requires a Discord server.",
            embed=None, view=CommunityHubView(owner_id),
        )
    try:
        sessions = (
            await hub.list_partner_discovery_sessions(guild_id, limit=100)
            if partner else await hub.list_active_sessions(guild_id, limit=100)
        )
        activity = (
            await ensure_community_hub_runtime(interaction.client).partner_activity_snapshots(guild_id, limit=10)
            if partner else []
        )
        availability = {} if partner else await hub.availability_summary(guild_id, limit=1000)
    except hub.CommunityHubError as exc:
        return await _edit_private_original(
            interaction, content=f"❌ {_error_text(exc)}",
            embed=None, view=CommunityHubView(owner_id),
        )
    # Scan only the latest 100 already-authorized records. Paging is a
    # presentation limit, never a new cross-guild discovery permission.
    page = max(0, min(int(page), 3))
    filtered = filter_discovery_games(
        sessions, game_query, limit=25,
        play_style=play_style, mic_preference=mic_preference,
        offset=page * 25,
    )
    if page and not filtered:
        # A session may expire or be revoked between clicks; never strand the
        # member on an empty stale page when newer results are available.
        page = 0
        filtered = filter_discovery_games(
            sessions, game_query, limit=25,
            play_style=play_style, mic_preference=mic_preference,
        )
    has_next = bool(filter_discovery_games(
        sessions, game_query, limit=1,
        play_style=play_style, mic_preference=mic_preference,
        offset=(page + 1) * 25,
    ))
    await _edit_private_original(
        interaction, content=None,
        embed=_find_players_embed(
            filtered, partner=partner, partner_activity=activity,
            availability_summary=availability, game_query=game_query,
            play_style=play_style, mic_preference=mic_preference,
            page=page, has_next=has_next,
        ),
        view=FindPlayersView(
            owner_id, filtered, partner=partner,
            availability_summary=availability, game_query=game_query,
            play_style=play_style, mic_preference=mic_preference,
            page=page, has_next=has_next,
        ),
    )


class DiscoveryGameSearchModal(discord.ui.Modal, title="Find Groups by Game"):
    game = discord.ui.TextInput(
        label="Game name",
        placeholder="Minecraft, GTA V, etc.",
        min_length=1,
        max_length=80,
    )

    def __init__(
        self, owner_id: int, *, source: str, current_query: str = "",
        play_style: str = "", mic_preference: str = "",
    ) -> None:
        super().__init__()
        self.owner_id = int(owner_id)
        self.play_style = play_style
        self.mic_preference = mic_preference
        if source not in {"local", "partner", "external"}:
            raise ValueError("Unknown Community Hub discovery source")
        self.source = source
        self.game.default = str(current_query or "")[:80] or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "Open your own Community Hub panel.")
        await _defer_ephemeral(interaction)
        query = str(self.game.value or "").strip()[:80]
        if self.source == "external":
            await _open_external_sessions(interaction, self.owner_id, game_query=query)
        else:
            kwargs = {}
            if self.play_style:
                kwargs["play_style"] = self.play_style
            if self.mic_preference:
                kwargs["mic_preference"] = self.mic_preference
            await _open_find_players_source(
                interaction, self.owner_id,
                partner=self.source == "partner", game_query=query, **kwargs,
            )


def _external_groups_embed(
    sessions: list[dict[str, str]], *, page: int = 0, notice: str = "",
    game_query: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="🌍 External Gaming Groups • The100.io",
        description=(
            "Read-only upcoming sessions from an independent service. "
            "Use its website to check availability and join there. "
            "These are not Dank Shield sessions, and Quick Match does not include them."
        ),
        color=discord.Color.blurple(),
    )
    if game_query:
        embed.add_field(
            name="🎮 Game filter",
            value=discord.utils.escape_markdown(game_query)[:80]
            + " • matches within the 25 most recent provider results",
            inline=False,
        )
    if notice:
        embed.add_field(name="Provider status", value=notice[:1024], inline=False)
    if not sessions:
        embed.add_field(
            name="No external sessions shown",
            value=(
                "No matches for this game in the current provider snapshot. Try another game or clear the filter."
                if game_query and not notice else
                "External discovery must be enabled by staff and configured on the bot host."
            ),
            inline=False,
        )
        return embed
    page = max(0, min(page, (len(sessions) - 1) // 5))
    for row in sessions[page * 5: page * 5 + 5]:
        game = discord.utils.escape_markdown(row["game"])
        title = discord.utils.escape_markdown(row["title"])
        starts = discord.utils.escape_markdown(row.get("starts_at", ""))
        detail = f"{title}" + (f" • {starts}" if starts else "")
        embed.add_field(
            name=f"🎮 {game}"[:256],
            value=f"{detail[:850]}\n[View session on The100.io]({row['url']})",
            inline=False,
        )
    embed.set_footer(text=f"External provider • page {page + 1}/{(len(sessions) + 4) // 5} • check live availability on The100.io")
    return embed



def _outside_players_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🌍 Find Players Outside Your Server",
        description=(
            "Use real, independently published gaming directories. "
            "The Minecraft player directory has public profiles (not an automatic invite or live Discord session). "
            "You can also browse outside communities manually. "
            "The100.io is optional and requires an authorized supporter/developer token. "
            "**Nobody is contacted automatically.**"
        ), color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎯 Live Multi-Game Groups • Free",
        value="Atlas Gaming public posts for 28+ games; filter game, region, group type and ready-now status. No API key.",
        inline=False,
    )
    embed.add_field(
        name="⛏️ Minecraft Players • Free",
        value="Search public Minecraft player profiles by edition. No API key.",
        inline=False,
    )
    embed.add_field(
        name="🧭 More Free Communities",
        value="Browse public, independently moderated gaming communities in your browser. "
              "Read their rules before sharing invitations.",
        inline=False,
    )
    embed.add_field(
        name="📣 Bring Players to Your Server",
        value="For your own public groups use Community Hub → Public Gaming Network / Public Recruiting. "
              "These are separate from third-party player profiles.",
        inline=False,
    )
    return embed


class OutsidePlayersView(_OwnedView):
    @discord.ui.button(label="Live Gaming Groups", emoji="🎯", style=discord.ButtonStyle.primary, custom_id="dank:hub:outside:atlas:v1", row=0)
    async def live_groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_atlas_groups(interaction, self.owner_id)

    @discord.ui.button(label="Minecraft Players", emoji="⛏️", style=discord.ButtonStyle.primary, custom_id="dank:hub:outside:minecraft:v1", row=0)
    async def minecraft(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_minecraft_players(interaction, self.owner_id)

    @discord.ui.button(label="More Free Communities", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:communities:v1", row=0)
    async def communities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_communities(interaction, self.owner_id)

    @discord.ui.button(label="The100.io (Optional)", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:the100:v1", row=1)
    async def the100(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_external_sessions(interaction, self.owner_id)

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_hub_embed(), view=CommunityHubView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _open_outside_players_home(interaction: discord.Interaction, owner_id: int) -> None:
    await _edit_private_original(
        interaction, content=None, embed=_outside_players_embed(),
        view=OutsidePlayersView(owner_id),
    )


class UnifiedGameSearchModal(discord.ui.Modal, title="Find a Gaming Group"):
    game = discord.ui.TextInput(
        label="Which game? (blank = all games)", required=False, max_length=55,
        placeholder="Minecraft, Fortnite, Valorant...",
    )
    region = discord.ui.TextInput(
        label="Region for outside groups (optional)", required=False,
        max_length=40, placeholder="NA, US East, EU...",
    )

    def __init__(self, owner_id: int, *, game: str = "", region: str = "") -> None:
        super().__init__()
        self.owner_id = int(owner_id)
        self.game.default = game[:55] or None
        self.region.default = region[:40] or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "Open your own Find Players menu.")
        await _defer_ephemeral(interaction)
        await _open_unified_player_discovery(
            interaction, self.owner_id,
            game=str(self.game.value or "").strip(),
            region=str(self.region.value or "").strip(),
        )


def _discovery_safe_label(value: Any, limit: int = 65) -> str:
    text = " ".join(
        "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
        .replace("@", "＠").split()
    )[:max(1, min(int(limit), 90))]
    return discord.utils.escape_markdown(text)


def _unified_discovery_embed(
    atlas_rows: list[dict[str, Any]], network_rows: list[dict[str, Any]],
    local_rows: list[dict[str, Any]], *, game: str = "", region: str = "",
    atlas_notice: str = "", network_notice: str = "", local_notice: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="🎮 Find Players & Gaming Servers",
        description=(
            "**Results from outside gaming communities appear here first.** "
            "You can also discover approved Discord servers from the Public Gaming Network "
            "and join active groups in this server. Search once; no hidden outside-player menu. "
            "External availability and age/verification requirements must be checked with the host."
        ),
        color=discord.Color.blurple(),
    )
    filters = " • ".join(filter(None, (
        "Game: " + discord.utils.escape_markdown(game)[:55] if game else "",
        "Outside region: " + discord.utils.escape_markdown(region)[:40] if region else "",
    )))
    if filters:
        embed.add_field(name="Your search", value=filters, inline=False)

    if atlas_rows:
        displayed = []
        for row in atlas_rows[:5]:
            title = _discovery_safe_label(row.get("title") or "Group", 65)
            game_name = _discovery_safe_label(row.get("game") or "Game", 40)
            place = _discovery_safe_label(row.get("region") or "", 30)
            url = atlas._safe_details_url(row.get("url"))
            # The Atlas adapter returns only trusted provider-origin URLs.
            displayed.append(
                f"• **{game_name}**: {title}"
                + (f" • {place}" if place else "")
                + f" • [View group]({url})"
            )
        embed.add_field(name="🌍 Outside gaming groups", value="\n".join(displayed)[:1024], inline=False)
    else:
        embed.add_field(
            name="🌍 Outside gaming groups",
            value=(atlas_notice[:450] if atlas_notice else
                   "No outside posts match right now. Search another game or browse more sources."),
            inline=False,
        )

    if network_rows:
        displayed = []
        for row in network_rows[:5]:
            community = _discovery_safe_label(row["community"], 48)
            game_name = _discovery_safe_label(row["game"], 50)
            displayed.append(f"• **{community}** • {game_name} • up to {row['capacity']} players")
        embed.add_field(
            name="🧭 Public Gaming Network • approved Discord communities",
            value="\n".join(displayed)[:1024] +
                  "\n**Choose a server below to view its current approved invitation.**",
            inline=False,
        )
    else:
        embed.add_field(
            name="🧭 Public Gaming Network",
            value=network_notice[:450] if network_notice else
                  "No participating Discord servers have advertised a matching group yet. "
                  "Outside groups can still be found above.",
            inline=False,
        )

    if local_rows:
        displayed = [
            f"• {_discovery_safe_label(row.get('game_name') or 'Game', 65)}"
            for row in local_rows[:3]
        ]
        text = "\n".join(displayed) + "\nUse **This Server** to join."
    else:
        text = (local_notice[:300] if local_notice else
                "No matching sessions hosted here right now. Outside search still works.")
    embed.add_field(name="🏠 Groups in this server", value=text[:1024], inline=False)
    embed.set_footer(text="External listings are independent • Public Network is staff/host opt-in • No automatic joins")
    return embed


class UnifiedPlayerDiscoveryView(_OwnedView):
    def __init__(
        self, owner_id: int, network_rows: list[dict[str, Any]],
        *, game: str = "", region: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.game = game[:55]
        self.region = region[:40]
        # Reuse the existing click-time server/host consent validation.
        self.add_item(PublicNetworkSelect(
            owner_id, network_rows[:20], self.game, page=0,
            return_to_unified=True, region=self.region,
        ))

    @discord.ui.button(label="Search Any Game", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:unified:search:v1", row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            UnifiedGameSearchModal(self.owner_id, game=self.game, region=self.region)
        )

    @discord.ui.button(label="More Outside Groups", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:outside:v1", row=1)
    async def outside(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_atlas_groups(
            interaction, self.owner_id, game=self.game, region=self.region,
        )

    @discord.ui.button(label="Public Network", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:network:v1", row=1)
    async def network(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_public_lfg_network(interaction, self.owner_id, game_query=self.game)

    @discord.ui.button(label="This Server", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:local:v1", row=2)
    async def local(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(interaction, self.owner_id, partner=False, game_query=self.game)

    @discord.ui.button(label="Minecraft Players", emoji="⛏️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:minecraft:v1", row=2)
    async def minecraft(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_minecraft_players(interaction, self.owner_id)

    @discord.ui.button(label="More Sources", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:sources:v1", row=2)
    async def sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_players_home(interaction, self.owner_id)

    @discord.ui.button(label="Refresh Results", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:refresh:v1", row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_unified_player_discovery(interaction, self.owner_id, game=self.game, region=self.region)

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:unified:home:v1", row=3)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_private_original(
            interaction, content=None, embed=_hub_embed(), view=CommunityHubView(self.owner_id),
        )


async def _open_unified_player_discovery(
    interaction: discord.Interaction, owner_id: int, *,
    game: str = "", region: str = "",
) -> None:
    """Query each authorized source independently, with partial results on outage."""
    gid = int(interaction.guild_id or 0)
    if gid <= 0:
        return await _edit_private_original(
            interaction, content="Find Players works inside a Discord server.",
            embed=None, view=CommunityHubView(owner_id),
        )
    game, region = str(game or "").strip()[:55], str(region or "").strip()[:40]
    from ..community_public_lfg import sanitize_directory

    results = await asyncio.gather(
        atlas.list_atlas_posts(game=game, region=region, limit=25),
        hub.list_public_lfg_directory(limit=100),
        hub.list_active_sessions(gid, limit=30),
        return_exceptions=True,
    )
    atlas_rows: list[dict[str, Any]] = []
    network_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    notices = ["", "", ""]
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            if index == 0:
                notices[index] = "Outside group listings are temporarily unavailable. Try More Sources."
            elif index == 1:
                notices[index] = "Cross-server listings aren't set up on this bot yet. Outside groups still work."
            else:
                notices[index] = "Local group listings are temporarily unavailable."
            continue
        if index == 0:
            atlas_rows = result if isinstance(result, list) else []
        elif index == 1:
            network_rows = sanitize_directory(result, game=game)[:40]
        else:
            local_rows = filter_discovery_games(result, game, limit=10)

    embed = _unified_discovery_embed(
        atlas_rows, network_rows, local_rows, game=game, region=region,
        atlas_notice=notices[0], network_notice=notices[1], local_notice=notices[2],
    )
    await _edit_private_original(
        interaction, content=None, embed=embed,
        view=UnifiedPlayerDiscoveryView(owner_id, network_rows, game=game, region=region),
    )


class AtlasSearchModal(discord.ui.Modal, title="Search Outside Gaming Groups"):
    game = discord.ui.TextInput(
        label="Game (leave blank for all)",
        placeholder="Minecraft, Valorant, Destiny 2", required=False, max_length=55,
    )
    region = discord.ui.TextInput(
        label="Region (leave blank for all)",
        placeholder="NA, EU, US East", required=False, max_length=40,
    )

    def __init__(self, owner_id: int, *, game: str = "", region: str = "", ready: bool = False) -> None:
        super().__init__()
        self.owner_id = int(owner_id)
        self.ready = bool(ready)
        self.game.default = game[:55] or None
        self.region.default = region[:40] or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "Open your own Community Hub panel.")
        await _defer_ephemeral(interaction)
        await _open_atlas_groups(
            interaction, self.owner_id, game=str(self.game.value or "").strip(),
            region=str(self.region.value or "").strip(), ready=self.ready,
        )


def _atlas_groups_embed(
    rows: list[dict[str, Any]], *, page: int, game: str, region: str,
    ready: bool, group_type: str = "", notice: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Live Gaming Groups • Atlas Gaming",
        description=(
            "Free, public, independently hosted looking-for-group posts across 28+ games. "
            "Atlas Gaming hosts the real listings; availability and age requirements must be checked there. "
            "This bot never contacts or auto-enrolls players."
        ), color=discord.Color.blurple(),
    )
    filters = " • ".join(filter(None, (
        f"Game: {discord.utils.escape_markdown(game)}" if game else "",
        f"Region: {discord.utils.escape_markdown(region)}" if region else "",
        "Ready now" if ready else "",
        f"Group type: {group_type.upper()}" if group_type else "",
    )))
    if filters:
        embed.add_field(name="Filters", value=filters[:500], inline=False)
    if notice:
        embed.add_field(name="Provider status", value=notice[:400], inline=False)
    if not rows and not notice:
        embed.add_field(name="No matching groups", value="Try clearing filters or another game.", inline=False)
    for row in rows[page * 5:page * 5 + 5]:
        title = discord.utils.escape_markdown(row["title"])[:80]
        game_name = discord.utils.escape_markdown(row["game"])[:60]
        details = " • ".join(filter(None, (
            row.get("type", ""), discord.utils.escape_markdown(row.get("region", ""))[:40],
            discord.utils.escape_markdown(row.get("mode", ""))[:40],
            "Ready now" if row.get("ready_now") else "",
        )))
        # Only allowitelisted provider-origin links, never external contact URLs.
        view_label = "View on Atlas Gaming" if row["url"] != atlas.SITE else "Browse Atlas Gaming"
        embed.add_field(
            name=f"🎮 {game_name}"[:95],
            value=f"**{title}**\n{details[:180]}\n[{view_label}]({row['url']})",
            inline=False,
        )
    embed.set_footer(text=f"Page {page + 1} of {max(1, (len(rows) + 4) // 5)} • latest provider posts")
    return embed


class AtlasTypeSelect(discord.ui.Select):
    def __init__(self, owner_id: int, *, game: str, region: str, ready: bool, group_type: str) -> None:
        self.owner_id = owner_id
        self.game = game
        self.region = region
        self.ready = ready
        self.group_type = group_type
        options = [
            ("Any group", "all"),
            ("Looking for a group", atlas.ATLAS_TYPE_VALUES[0]),
            ("Recruiting teammates", "lfm"),
            ("Gaming server", "server"),
            ("Clan", "clan"),
            ("Squad", "squad"),
        ]
        super().__init__(
            placeholder="What kind of group?",
            custom_id="dank:hub:atlas:type:v1", row=3,
            options=[discord.SelectOption(label=label, value=value, default=(group_type or "all") == value)
                     for label, value in options],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        choice = self.values[0]
        await _open_atlas_groups(
            interaction, self.owner_id, game=self.game, region=self.region,
            ready=self.ready, group_type="" if choice == "all" else choice,
        )


class AtlasGroupsView(_OwnedView):
    def __init__(self, owner_id: int, rows: list[dict[str, Any]], *, page: int = 0,
                 game: str = "", region: str = "", ready: bool = False,
                 group_type: str = "") -> None:
        super().__init__(owner_id)
        self.game = game[:55]
        self.region = region[:40]
        self.ready = bool(ready)
        self.group_type = group_type
        self.page = max(0, min(int(page), max(0, (len(rows) - 1) // 5)))
        self.pages = max(1, (len(rows) + 4) // 5)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page + 1 >= self.pages
        self.toggle_ready.label = "Show All" if self.ready else "Ready Now"
        self.add_item(AtlasTypeSelect(
            owner_id, game=self.game, region=self.region, ready=self.ready,
            group_type=self.group_type,
        ))
        self.add_item(discord.ui.Button(
            label="Browse Atlas Gaming", emoji="🌐",
            style=discord.ButtonStyle.link, url=atlas.SITE, row=2,
        ))

    @discord.ui.button(label="Search Game / Region", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:atlas:search:v1", row=0)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            AtlasSearchModal(self.owner_id, game=self.game, region=self.region, ready=self.ready)
        )

    @discord.ui.button(label="Ready Now", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank:hub:atlas:ready:v1", row=0)
    async def toggle_ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_atlas_groups(
            interaction, self.owner_id, game=self.game, region=self.region,
            ready=not self.ready, group_type=self.group_type,
        )

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:atlas:prev:v1", row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_atlas_groups(
            interaction, self.owner_id, game=self.game, region=self.region,
            ready=self.ready, page=max(0, self.page - 1), group_type=self.group_type,
        )

    @discord.ui.button(label="More Groups", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:atlas:next:v1", row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_atlas_groups(
            interaction, self.owner_id, game=self.game, region=self.region,
            ready=self.ready, page=self.page + 1, group_type=self.group_type,
        )

    @discord.ui.button(label="Outside Sources", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:atlas:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_players_home(interaction, self.owner_id)


async def _open_atlas_groups(
    interaction: discord.Interaction, owner_id: int, *,
    game: str = "", region: str = "", ready: bool = False, page: int = 0,
    group_type: str = "",
) -> None:
    rows: list[dict[str, Any]] = []
    notice = ""
    try:
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        if not bool(settings.get("enabled", True)) or bool(settings.get("maintenance_mode")):
            notice = "Community Hub is unavailable on this server."
        else:
            rows = await atlas.list_atlas_posts(
                game=game, region=region, ready_now=ready, group_type=group_type,
            )
    except (hub.CommunityHubError, external.ExternalDiscoveryError) as exc:
        notice = str(exc)[:400]
    page = max(0, min(int(page), max(0, (len(rows) - 1) // 5)))
    await _edit_private_original(
        interaction, content=None,
        embed=_atlas_groups_embed(
            rows, page=page, game=game, region=region, ready=ready,
            group_type=group_type, notice=notice,
        ),
        view=AtlasGroupsView(
            owner_id, rows, page=page, game=game, region=region,
            ready=ready, group_type=group_type,
        ),
    )


class MinecraftEditionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, edition: str) -> None:
        self.owner_id = int(owner_id)
        choices = [
            ("All editions", "", "🎮"),
            ("Java", "JAVA", "💻"),
            ("Bedrock", "BEDROCK", "📱"),
            ("Cross-play", "CROSSPLAY", "🔀"),
        ]
        super().__init__(
            placeholder="Minecraft edition: " + (edition or "All"),
            options=[
                discord.SelectOption(label=label, value=value or "ALL", emoji=emoji, default=edition == value)
                for label, value, emoji in choices
            ],
            min_values=1, max_values=1,
            row=0, custom_id="dank:hub:outside:minecraft:edition:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        selected = self.values[0]
        await _open_minecraft_players(
            interaction, self.owner_id, edition="" if selected == "ALL" else selected,
        )


def _minecraft_players_embed(
    rows: list[dict[str, Any]], *, page: int, edition: str, notice: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="⛏️ Minecraft Players • Outside Servers",
        description=(
            "Public Minecraft player profiles from an independent website. "
            "These profiles **are not confirmed online now**, do not represent open Discord sessions, "
            "and are not members of your server. Open Player Directory to contact people under its rules."
        ),
        color=discord.Color.blurple(),
    )
    if notice:
        embed.add_field(name="Provider status", value=notice[:400], inline=False)
    for row in rows[:20]:
        name = discord.utils.escape_markdown(str(row["name"]))[:70]
        edition_label = str(row["edition"])
        modes = ", ".join(discord.utils.escape_markdown(v) for v in row.get("modes", [])[:3])
        headline = discord.utils.escape_markdown(str(row.get("headline", "")))[:120]
        details = " • ".join(filter(None, [edition_label, modes, headline]))
        embed.add_field(
            name=f"🎮 {name}"[:95], value=details[:240] or "Public Minecraft profile",
            inline=False,
        )
    if not rows and not notice:
        embed.add_field(
            name="No matching profiles", value="No public profiles in this provider page. Try another edition.",
            inline=False,
        )
    embed.set_footer(
        text=f"Provider page {page} • {edition or 'All editions'} • cached read-only profiles"
    )
    return embed


class MinecraftPlayersView(_OwnedView):
    def __init__(
        self, owner_id: int, *, page: int = 1, pages: int = 1, edition: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.page = max(1, min(int(page), 5))
        self.pages = max(1, min(int(pages), 5))
        self.edition = edition
        self.add_item(MinecraftEditionSelect(owner_id, edition))
        self.previous.disabled = self.page <= 1
        self.next_page.disabled = self.page >= self.pages
        self.add_item(discord.ui.Button(
            label="Open Player Directory", emoji="🌐", style=discord.ButtonStyle.link,
            url=free_lfg.MINECRAFT_SITE_URL, row=2,
        ))

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:minecraft:prev:v1", row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_minecraft_players(
            interaction, self.owner_id, page=max(1, self.page - 1), edition=self.edition,
        )

    @discord.ui.button(label="More Players", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:minecraft:next:v1", row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_minecraft_players(
            interaction, self.owner_id, page=min(self.pages, self.page + 1), edition=self.edition,
        )

    @discord.ui.button(label="Outside Sources", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:minecraft:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_players_home(interaction, self.owner_id)


async def _open_minecraft_players(
    interaction: discord.Interaction, owner_id: int, *,
    page: int = 1, edition: str = "",
) -> None:
    rows: list[dict[str, Any]] = []
    pages, notice = 1, ""
    try:
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        if not bool(settings.get("enabled", True)) or bool(settings.get("maintenance_mode")):
            notice = "Community Hub is unavailable on this server."
        else:
            rows, pages = await free_lfg.list_minecraft_players(page=page, edition=edition)
    except (hub.CommunityHubError, external.ExternalDiscoveryError) as exc:
        notice = str(exc)[:400]
    await _edit_private_original(
        interaction, content=None,
        embed=_minecraft_players_embed(rows, page=page, edition=edition, notice=notice),
        view=MinecraftPlayersView(owner_id, page=page, pages=pages, edition=edition),
    )


class OutsideCommunitiesView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        for idx, item in enumerate(free_lfg.provider_navigation()):
            self.add_item(discord.ui.Button(
                label=item["label"], style=discord.ButtonStyle.link,
                url=item["url"], row=idx // 5,
            ))

    @discord.ui.button(label="Outside Sources", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:outside:links:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_outside_players_home(interaction, self.owner_id)


async def _open_outside_communities(interaction: discord.Interaction, owner_id: int) -> None:
    embed = discord.Embed(
        title="🧭 Free Gaming Communities",
        description=(
            "Visit independently moderated gaming communities in your browser. "
            "Links are **manual destinations**, not API-fed live sessions. "
            "Check each community's posting, age and invite policies; "
            "Dank Shield never scrapes posts or sends messages for you."
        ), color=discord.Color.blurple(),
    )
    for item in free_lfg.provider_navigation():
        embed.add_field(name=item["label"], value=item["game"] + " • Open with a button below", inline=False)
    await _edit_private_original(
        interaction, content=None, embed=embed,
        view=OutsideCommunitiesView(owner_id),
    )


class ExternalGroupsView(_OwnedView):
    def __init__(
        self, owner_id: int, sessions: list[dict[str, str]], *,
        page: int = 0, game_query: str = "", notice: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.sessions = list(sessions[:25])
        self.game_query = str(game_query or "")[:80]
        self.notice = str(notice or "")[:300]
        self.page = max(0, min(page, max(0, (len(self.sessions) - 1) // 5)))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 5 >= len(self.sessions)
        self.clear_game.disabled = not bool(self.game_query)

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:prev:v1", row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        # Recheck guild consent and live provider health on every page action.
        await _open_external_sessions(
            interaction, self.owner_id,
            game_query=self.game_query, page=max(0, self.page - 1),
        )

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:next:v1", row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_external_sessions(
            interaction, self.owner_id,
            game_query=self.game_query, page=self.page + 1,
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:refresh:v1", row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_external_sessions(interaction, self.owner_id, game_query=self.game_query)

    @discord.ui.button(label="Search Game", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:external:game:v1", row=1)
    async def search_game(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            DiscoveryGameSearchModal(self.owner_id, source="external", current_query=self.game_query)
        )

    @discord.ui.button(label="Clear Filter", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:clear:v1", row=1)
    async def clear_game(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_external_sessions(interaction, self.owner_id)

    @discord.ui.button(label="Find Players", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:find:v1", row=1)
    async def local_groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_find_players_source(interaction, self.owner_id, partner=False, game_query=self.game_query)

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:external:back:v1", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="dank:hub:external:close:v1", row=0)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)


async def _open_external_sessions(
    interaction: discord.Interaction, owner_id: int, *, game_query: str = "",
    page: int = 0,
) -> None:
    sessions: list[dict[str, str]] = []
    notice = ""
    try:
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        if not bool(settings.get("enabled", True)) or bool(settings.get("maintenance_mode")):
            notice = "Community Hub is currently unavailable on this server."
        elif not bool(settings.get("external_the100_enabled")):
            notice = "Staff have not enabled The100.io discovery in Community Hub Settings."
        elif not external.the100_configured():
            notice = "The100.io needs an authorized supporter/developer API token. Public Gaming Network works without it."
        else:
            sessions = await external.list_the100_sessions(limit=25)
            if not sessions:
                notice = "The100.io returned no upcoming sessions."
            else:
                sessions = filter_discovery_games(sessions, game_query, external=True, limit=25)
    except (hub.CommunityHubError, external.ExternalDiscoveryError) as exc:
        notice = str(exc)[:300]
    await _edit_private_original(
        interaction, content=None,
        embed=_external_groups_embed(sessions, page=page, notice=notice, game_query=game_query),
        view=ExternalGroupsView(owner_id, sessions, page=page, game_query=game_query, notice=notice),
    )


def _my_groups_embed(sessions: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="👥 My Groups",
        description="Your active Community Hub gaming sessions.",
        color=discord.Color.blurple(),
    )
    if not sessions:
        embed.add_field(name="No active groups", value="Use Find Players or Start Gaming Session.", inline=False)
    else:
        for row in sessions[:10]:
            membership = row.get("membership") if isinstance(row.get("membership"), dict) else {}
            role = _safe_str(membership.get("role"), "member").replace("_", " ").title()
            embed.add_field(
                name=_safe_str(row.get("game_name"), "Game")[:256],
                value=f"{_STATE_LABELS.get(_safe_str(row.get('state')), 'Active')} • {role}",
                inline=False,
            )
    return embed


class MyGroupSelect(SessionSelect):
    def __init__(self, owner_id: int, sessions: list[dict[str, Any]]) -> None:
        super().__init__(owner_id, sessions, partner=False)
        self.custom_id = "dank:hub:mine:select:v1"

    async def callback(self, interaction: discord.Interaction) -> None:
        sid = self.values[0]
        if sid == "none":
            return
        await _defer_update(interaction)
        try:
            session = await hub.get_session(sid, guild_id=int(interaction.guild_id or 0))
            members = await hub.list_session_members(sid)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=build_session_embed(session, members),
            view=SessionDetailView(self.owner_id, session_id=sid, return_kind="mine"),
        )


class MyGroupsView(_OwnedView):
    def __init__(self, owner_id: int, sessions: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.add_item(MyGroupSelect(owner_id, sessions))

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:mine:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())


class LiveCaptionsHomeView(_OwnedView):
    @discord.ui.button(label="My Groups", emoji="👥", style=discord.ButtonStyle.primary, custom_id="dank:hub:captions:groups:v1", row=0)
    async def groups(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            sessions = await hub.list_user_sessions(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                active_only=True,
                limit=25,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_my_groups_embed(sessions),
            view=MyGroupsView(self.owner_id, sessions),
        )

    @discord.ui.button(label="Start Gaming Session", emoji="➕", style=discord.ButtonStyle.success, custom_id="dank:hub:captions:start:v1", row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StartSessionModal())

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:captions:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_hub_embed(),
            view=CommunityHubView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ReportSessionModal(discord.ui.Modal, title="Report Community Hub Session"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Harassment, unsafe behavior, spam, session abuse, etc.",
        min_length=2,
        max_length=100,
    )
    details = discord.ui.TextInput(
        label="Details",
        placeholder="Give staff enough context to review what happened.",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            report = await hub.submit_report(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                session_id=self.session_id,
                reason=str(self.reason.value),
                details=str(self.details.value or ""),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        report_id = _safe_str(report.get("id"))
        await _followup(
            interaction,
            f"✅ Report submitted privately to Community Hub staff review"
            + (f" • case {report_id[:8]}" if report_id else "")
            + ".",
        )


class SessionDetailView(_OwnedView):
    def __init__(self, owner_id: int, *, session_id: str, return_kind: str) -> None:
        super().__init__(owner_id)
        self.session_id = session_id
        self.return_kind = return_kind

    @discord.ui.button(label="Join Group", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:detail:join:v1", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)

        async def _factory() -> Any:
            return await hub.join_session(self.session_id, int(interaction.guild_id or 0), int(interaction.user.id))

        state, result = await _run_session_mutation(interaction, session_id=self.session_id, action="join", factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        if isinstance(result, dict):
            role = _safe_str(result.get("role"), "member")
            session = result.get("session") if isinstance(result.get("session"), dict) else await hub.get_session(self.session_id)
            await hub.bump_hourly_metric(int(interaction.guild_id or 0), "joins", 1)
            await hub.bump_game_metric(int(interaction.guild_id or 0), _safe_str(session.get("game_name"), "Game"), "joins", 1)
            ensure_community_hub_runtime(interaction.client).cancel_scheduled_cleanup(self.session_id)
            members = await hub.list_session_members(self.session_id)
            await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=self)
            await _followup(interaction, "✅ Joined the waitlist." if role == "waitlist" else "✅ Joined the group.")

    @discord.ui.button(label="Leave Group", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="dank:hub:detail:leave:v1", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        runtime = ensure_community_hub_runtime(interaction.client)

        async def _factory() -> Any:
            return await hub.leave_session(self.session_id, int(interaction.guild_id or 0), int(interaction.user.id))

        state, result = await _run_session_mutation(interaction, session_id=self.session_id, action="leave", factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        await hub.bump_hourly_metric(int(interaction.guild_id or 0), "leaves", 1)
        if isinstance(result, dict) and bool(result.get("needs_cleanup")):
            settings = await runtime.settings_for(int(interaction.guild_id or 0))
            runtime.schedule_empty_cleanup(
                self.session_id,
                int(interaction.guild_id or 0),
                actor_id=int(interaction.user.id),
                delay_seconds=max(60, _safe_int(settings.get("cleanup_grace_seconds"), 300)),
            )
        await _followup(interaction, "✅ You left the group. The session itself was not destroyed unless you were the last participant.")
        await _edit_private_original(interaction, content=None, embed=_hub_embed(), view=CommunityHubView(self.owner_id))

    @discord.ui.button(label="Ready / Not Ready", emoji="☑️", style=discord.ButtonStyle.primary, custom_id="dank:hub:detail:ready:v1", row=0)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            members = await hub.list_session_members(self.session_id)
            mine = next((row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)), None)
            if mine is None:
                return await _followup(interaction, "Join the group before changing your ready state.")
            new_ready = not bool(mine.get("ready"))
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        async def _factory() -> Any:
            return await hub.set_ready(self.session_id, int(interaction.guild_id or 0), int(interaction.user.id), new_ready)

        state, session = await _run_session_mutation(interaction, session_id=self.session_id, action="ready", factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=self)
        await ensure_community_hub_runtime(interaction.client).refresh_session_card(session)
        await _followup(interaction, "✅ Ready." if new_ready else "Ready status cleared.")

    @discord.ui.button(label="Auto-Caption My Voice", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="dank:hub:detail:captionme:v1", row=1)
    async def caption_me(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_ephemeral(interaction)
        message = await _toggle_live_caption_consent(interaction, self.session_id)
        await _followup(interaction, message)

    @discord.ui.button(label="Manage Session", emoji="🎛️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:detail:manage:v1", row=1)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            session = await hub.get_session(self.session_id, guild_id=int(interaction.guild_id or 0))
            members = await hub.list_session_members(self.session_id)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        member = next((row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)), None)
        role = _safe_str((member or {}).get("role"))
        if role not in {"host", "cohost"} and not _staff_authorized(interaction):
            return await _followup(interaction, "Only the host, a co-host, or authorized staff can manage this session.")
        await _edit_private_original(
            interaction,
            content=None,
            embed=build_session_embed(session, members),
            view=SessionControlView(self.owner_id, session_id=self.session_id),
        )

    @discord.ui.button(label="Report Problem", emoji="🚩", style=discord.ButtonStyle.secondary, custom_id="dank:hub:detail:report:v1", row=1)
    async def report_problem(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(ReportSessionModal(self.session_id))

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:detail:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if self.return_kind == "mine":
            sessions = await hub.list_user_sessions(int(interaction.guild_id or 0), int(interaction.user.id), active_only=True)
            return await _edit_private_original(interaction, content=None, embed=_my_groups_embed(sessions), view=MyGroupsView(self.owner_id, sessions))
        sessions = await hub.list_active_sessions(int(interaction.guild_id or 0), limit=25)
        availability = await hub.availability_summary(int(interaction.guild_id or 0), limit=1000)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_find_players_embed(sessions, partner=False, availability_summary=availability),
            view=FindPlayersView(
                self.owner_id,
                sessions,
                partner=False,
                availability_summary=availability,
            ),
        )


class RoleTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner_id: int, session_id: str, role: str) -> None:
        super().__init__(
            placeholder="Choose a current session participant",
            min_values=1,
            max_values=1,
            custom_id=f"dank:hub:role:{role}:v1",
            row=0,
        )
        self.owner_id = int(owner_id)
        self.session_id = session_id
        self.role = role

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        await _defer_update(interaction)
        members = await hub.list_session_members(self.session_id)
        if not any(_safe_int(row.get("user_id")) == int(target.id) for row in members):
            return await _followup(interaction, "That user is not an active participant in this session.")

        async def _factory() -> Any:
            return await hub.assign_member_role(
                self.session_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                int(target.id),
                self.role,
                staff_override=_staff_authorized(interaction),
            )

        state, session = await _run_session_mutation(
            interaction,
            session_id=self.session_id,
            action=f"assign_{self.role}",
            factory=_factory,
            risk_level="moderate",
        )
        if not await _operation_feedback(interaction, state):
            return
        members = await hub.list_session_members(self.session_id)
        await ensure_community_hub_runtime(interaction.client).refresh_session_card(session)
        await _edit_private_original(
            interaction,
            content=None,
            embed=build_session_embed(session, members),
            view=SessionControlView(self.owner_id, session_id=self.session_id),
        )
        label = "host" if self.role == "host" else "co-host"
        await _followup(interaction, f"✅ Session {label} updated.")


class RoleTargetView(_OwnedView):
    def __init__(self, owner_id: int, session_id: str, role: str) -> None:
        super().__init__(owner_id)
        self.session_id = session_id
        self.add_item(RoleTargetSelect(owner_id, session_id, role))

    @discord.ui.button(label="Back to Session Controls", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:role:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        session = await hub.get_session(self.session_id, guild_id=int(interaction.guild_id or 0))
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=SessionControlView(self.owner_id, session_id=self.session_id))


class ExternalAtlasPublishModal(discord.ui.Modal, title="Advertise This Gaming Group"):
    region = discord.ui.TextInput(
        label="Where are you playing?",
        placeholder="NA, US East, EU West...",
        min_length=2, max_length=40,
    )
    hours = discord.ui.TextInput(
        label="Advert lifetime (1, 2, 4, 12, or 24 hours)",
        placeholder="2", default="2",
        min_length=1, max_length=2,
    )
    adult = discord.ui.TextInput(
        label="18+ players only? YES or NO",
        placeholder="YES if your community is age-restricted",
        min_length=2, max_length=3,
    )
    confirm = discord.ui.TextInput(
        label="Type POST to share your server invite",
        placeholder="POST", min_length=4, max_length=4,
    )

    def __init__(self, session_id: str, guild_id: int, owner_id: int):
        super().__init__()
        self.session_id = session_id
        self.guild_id = guild_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        if (interaction.guild is None
                or int(interaction.guild.id) != self.guild_id
                or int(interaction.user.id) != self.owner_id
                or bool(getattr(interaction.user, "bot", False))):
            return await _followup(interaction, "Only the host or co-host who opened this form can publish.")
        if str(self.confirm.value or "").strip().upper() != "POST":
            return await _followup(interaction, "No advertisement was queued. Type POST to confirm.")
        try:
            duration = int(str(self.hours.value or "").strip())
        except ValueError:
            return await _followup(interaction, "Choose 1, 2, 4, 12 or 24 hours.")
        adult_text = str(self.adult.value or "").strip().upper()
        if adult_text not in {"YES", "NO"}:
            return await _followup(interaction, "Choose YES or NO for the 18+ audience.")
        guild_age_level = getattr(getattr(interaction.guild, "nsfw_level", None), "name", "")
        adult_only = adult_text == "YES" or guild_age_level == "explicit"
        from ..community_lfg_atlas_publishing import (
            enqueue_host_publication, AtlasPublishError, atlas_game_slug,
        )
        try:
            session = await hub.get_session(self.session_id, guild_id=self.guild_id)
            if not atlas_game_slug(session.get("game_name")):
                return await _followup(
                    interaction, "This game's title isn't on the external directory's supported list yet. "
                    "Your public Community Hub board remains available.",
                )
            result = await enqueue_host_publication(
                self.session_id, self.guild_id, self.owner_id,
                region=str(self.region.value or "").strip(), hours=duration,
                adult_only=adult_only,
            )
        except (AtlasPublishError, hub.CommunityHubError) as exc:
            return await _followup(interaction, f"❌ {str(exc)[:420]}")
        return await _followup(
            interaction,
            "✅ External gaming advertisement **queued**, not yet published. "
            "Your guild's staff-approved invite, game and region will be public on Atlas Gaming "
            "when the provider confirms it. No member IDs or session notes are shared. " +
            ("Audience: 18+ only. " if adult_only else "Audience: general gaming. ") +
            "Use **External Ad Status** to check confirmation and **Remove External Ad** to revoke.",
        )


async def _require_atlas_host(
    interaction: discord.Interaction, session_id: str, *, allow_staff: bool = False,
) -> bool:
    if interaction.guild is None or bool(getattr(interaction.user, "bot", False)):
        return False
    try:
        members = await hub.list_session_members(session_id)
    except hub.CommunityHubError:
        return False
    if any(
        str(r.get("user_id")) == str(interaction.user.id)
        and r.get("role") in {"host", "cohost"} and not r.get("left_at")
        for r in members
    ):
        return True
    return allow_staff and _staff_authorized(interaction)


class SessionControlView(_OwnedView):
    def __init__(self, owner_id: int, *, session_id: str) -> None:
        super().__init__(owner_id)
        self.session_id = session_id

    async def _transition(self, interaction: discord.Interaction, action: str, success: str) -> None:
        await _defer_update(interaction)
        runtime = ensure_community_hub_runtime(interaction.client)

        async def _factory() -> Any:
            return await hub.transition_session(
                self.session_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                action,
                staff_override=_staff_authorized(interaction),
            )

        state, session = await _run_session_mutation(interaction, session_id=self.session_id, action=action, factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        if action == "start":
            await hub.bump_hourly_metric(int(interaction.guild_id or 0), "sessions_started", 1)
        await runtime.refresh_session_card(session)
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=self)
        await _followup(interaction, success)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.success, custom_id="dank:hub:manage:start:v1", row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._transition(interaction, "start", "✅ Session started.")

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:pause:v1", row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._transition(interaction, "pause", "Session paused.")

    @discord.ui.button(label="Resume", emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:resume:v1", row=0)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._transition(interaction, "resume", "✅ Session resumed.")

    @discord.ui.button(label="Lock / Unlock", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:lock:v1", row=0)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        session = await hub.get_session(self.session_id, guild_id=int(interaction.guild_id or 0))
        action = "unlock" if session.get("privacy") == "locked" else "lock"

        async def _factory() -> Any:
            return await hub.transition_session(
                self.session_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                action,
                staff_override=_staff_authorized(interaction),
            )

        state, updated = await _run_session_mutation(interaction, session_id=self.session_id, action=action, factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        await ensure_community_hub_runtime(interaction.client).refresh_session_card(updated)
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(updated, members), view=self)
        await _followup(interaction, "Session unlocked." if action == "unlock" else "Session locked to new joins.")

    @discord.ui.button(label="Keep Open", emoji="⏳", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:extend:v1", row=0)
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._transition(
            interaction,
            "extend",
            "✅ Idle timer refreshed. The server's hard maximum session lifetime still applies.",
        )

    @discord.ui.button(label="Transfer Host", emoji="👑", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:host:v1", row=1)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="👑 Transfer Session Host",
                description="Choose an active participant. Host transfer changes session control, not server roles or moderation authority.",
                color=discord.Color.blurple(),
            ),
            view=RoleTargetView(self.owner_id, self.session_id, "host"),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Choose Co-host", emoji="🤝", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:cohost:v1", row=1)
    async def cohost(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🤝 Choose Co-host",
                description="Choose an active participant to help manage this session.",
                color=discord.Color.blurple(),
            ),
            view=RoleTargetView(self.owner_id, self.session_id, "cohost"),
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @discord.ui.button(label="Live Captions", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:captions:v1", row=1)
    async def captions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            session = await hub.get_session(self.session_id, guild_id=int(interaction.guild_id or 0))
            members = await hub.list_session_members(self.session_id)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        mine = next(
            (row for row in members if _safe_int(row.get("user_id"), 0) == int(interaction.user.id)),
            None,
        )
        if _safe_str((mine or {}).get("role")) not in {"host", "cohost"} and not _staff_authorized(interaction):
            return await _followup(interaction, "Only the host, a co-host, or authorized staff can control Live Captions.")

        manager = ensure_community_voice_caption_manager(interaction.client)
        status = manager.status(self.session_id)
        try:
            if bool(status.get("active")):
                await manager.stop(self.session_id)
                return await _followup(interaction, "✅ Live Captions stopped and all speaker consent was cleared.")

            capability = voice_receive_capability()
            if not capability.available:
                return await _followup(
                    interaction,
                    f"❌ Live Captions are not ready on this host: {capability.reason}.",
                )
            await manager.start(session)
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _followup(
            interaction,
            "✅ Live Captions started. Each participant must press **Caption My Voice** before their isolated speaker "
            "audio is sent to **Google Gemini's transcription API** for speech-to-text. Dank Shield itself does not save the audio.",
        )

    @discord.ui.button(label="End Session", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="dank:hub:manage:end:v1", row=1)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        runtime = ensure_community_hub_runtime(interaction.client)

        async def _factory() -> Any:
            return await runtime.begin_end(
                self.session_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                staff_override=_staff_authorized(interaction),
                reason="Ended from Community Hub session controls",
            )

        state, session = await _run_session_mutation(
            interaction,
            session_id=self.session_id,
            action="end",
            factory=_factory,
            risk_level="dangerous",
        )
        if not await _operation_feedback(interaction, state):
            return
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=CommunityHubView(self.owner_id))
        await _followup(interaction, "✅ Session is ending. New joins are stopped and temporary spaces will clean up after the server's grace period.")

    @discord.ui.button(label="Call Game Crew", emoji="📣", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:gameping:v1", row=2)
    async def call_game_crew(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if interaction.guild is None or bool(getattr(interaction.user, "bot", False)):
            return await _followup(interaction, "Use this control as a server member.")
        try:
            session = await hub.get_session(self.session_id, guild_id=int(interaction.guild.id))
            members = await hub.list_session_members(self.session_id)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        mine = next(
            (row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)), None
        )
        if _safe_str((mine or {}).get("role")) not in {"host", "cohost"} and not _staff_authorized(interaction):
            return await _followup(interaction, "Only this group's host, co-host or authorized staff can call a game role.")
        try:
            message = await game_pings.call_game_crew(interaction.guild, self.session_id)
        except Exception as exc:
            print(f"⚠️ community_hub game role call error={type(exc).__name__}")
            message = "Game-role calls are unavailable; no successful ping was confirmed."
        await _followup(interaction, message)

    @discord.ui.button(label="Share Outside Server", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:publiclfg:v1", row=2)
    async def share_outside(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        guild_id = int(interaction.guild_id or 0)
        if guild_id <= 0 or bool(getattr(interaction.user, "bot", False)):
            return await _followup(interaction, "Use this control as a server member.")
        try:
            session = await hub.get_session(self.session_id, guild_id=guild_id)
            members = await hub.list_session_members(self.session_id)
            settings = await hub.get_settings(guild_id)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        mine = next(
            (row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)),
            None,
        )
        if _safe_str((mine or {}).get("role")) not in {"host", "cohost"} and not _staff_authorized(interaction):
            return await _followup(interaction, "Only this group's host, co-host or server staff can share it publicly.")
        current = bool(session.get("public_lfg_opt_in"))
        if not current:
            if (
                not bool(settings.get("enabled", True))
                or bool(settings.get("maintenance_mode"))
                or not bool(settings.get("public_lfg_enabled"))
                or not verified_public_lfg_invite(settings.get("public_lfg_invite_url"))
            ):
                return await _followup(
                    interaction,
                    "Outside recruitment is off. Ask staff to enable Public Recruiting in Community Hub Settings first.",
                )
            if session.get("privacy") != "public" or session.get("state") not in {"open", "forming", "ready", "active"}:
                return await _followup(interaction, "Only an active, publicly joinable gaming group can be shared.")
        async def _factory() -> Any:
            return await hub.set_session_public_lfg(
                self.session_id, guild_id, enabled=not current,
                expected_version=_safe_int(session.get("version"), 0),
            )
        try:
            state, updated = await _run_session_mutation(
                interaction, session_id=self.session_id,
                action="public_recruitment", factory=_factory,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        if not await _operation_feedback(interaction, state):
            return
        if not isinstance(updated, dict):
            return await _followup(interaction, "Recruitment state was not confirmed. Refresh and check again.")
        await _edit_private_original(
            interaction, content=None,
            embed=build_session_embed(updated, members), view=self,
        )
        if current:
            return await _followup(interaction, "✅ Public recruiting revoked. This group is no longer listed outside Discord.")
        url = community_public_lfg_url()
        if url:
            return await _followup(
                interaction,
                "✅ Group shared on the public gaming board. Share this public discovery link with gamers outside the server: "
                + url,
            )
        return await _followup(
            interaction,
            "✅ Group marked for public recruiting. Staff still need to configure a public HTTPS Site URL before you can share the board.",
        )

    @discord.ui.button(label="Advertise Externally", emoji="📢", style=discord.ButtonStyle.primary, custom_id="dank:hub:manage:atlas:publish:v1", row=3)
    async def atlas_publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        # Do not call any external API from this click. Only show the
        # consent form. The SQL queue independently verifies membership.
        await interaction.response.send_modal(
            ExternalAtlasPublishModal(
                self.session_id, int(interaction.guild_id or 0),
                int(interaction.user.id),
            )
        )

    @discord.ui.button(label="Remove External Ad", emoji="🛑", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:atlas:remove:v1", row=3)
    async def atlas_remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if not await _require_atlas_host(interaction, self.session_id, allow_staff=True):
            return await _followup(interaction, "Only the host, co-host or authorized staff can remove an external ad.")
        from ..community_lfg_atlas_publishing import request_removal, AtlasPublishError
        try:
            requested = await request_removal(self.session_id, int(interaction.guild_id or 0))
        except AtlasPublishError as exc:
            return await _followup(interaction, f"❌ {str(exc)[:300]}")
        await _followup(
            interaction,
            "✅ External removal requested. The cleanup worker will withdraw the ad "
            "using its private deletion token, or prevent an unsent ad from publishing. "
            "Use **External Ad Status** to verify the result."
            if requested else "No active external advertisement found to remove.",
        )

    @discord.ui.button(label="External Ad Status", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:atlas:status:v1", row=3)
    async def atlas_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if not await _require_atlas_host(interaction, self.session_id, allow_staff=True):
            return await _followup(interaction, "Only the host, co-host or authorized staff can inspect this ad.")
        from ..community_lfg_atlas_publishing import get_publication_status, AtlasPublishError
        try:
            status = await get_publication_status(self.session_id, int(interaction.guild_id or 0))
        except AtlasPublishError as exc:
            return await _followup(interaction, f"❌ {str(exc)[:250]}")
        if not status:
            return await _followup(interaction, "No outside advertising request exists for this group.")
        state = status["state"].replace("_", " ").title()
        url = status.get("url")
        note = status.get("last_error", "")
        summary = (
            f"🌍 **External Ad: {state}**\\n"
            f"Region: {status['region']}\\n"
            f"Expires: {status['expires_at']}\\n"
            + (f"Public listing: {url}\\n" if url else "")
            + ("Removal requested.\\n" if status.get("revoke_requested") else "")
            + (f"Provider note: {note[:150]}\\n" if note else "")
        )
        if status["state"] == "unknown":
            summary += ("The provider's publish result was uncertain. Dank Shield will **not repost "
                        "automatically** because it could create an unmanaged duplicate.")
        await _followup(interaction, summary[:1850])

    @discord.ui.button(label="Back to Group", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:manage:back:v1", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        session = await hub.get_session(self.session_id, guild_id=int(interaction.guild_id or 0))
        members = await hub.list_session_members(self.session_id)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=SessionDetailView(self.owner_id, session_id=self.session_id, return_kind="mine"))


class CommunitySessionPublicView(discord.ui.View):
    def __init__(self, *, ended: bool = False) -> None:
        super().__init__(timeout=None)
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.custom_id == "dank:hub:public:replay:v1":
                item.disabled = not ended
            else:
                item.disabled = ended

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _component_action_allowed(interaction):
            return True
        await _private(interaction, "You're using Community Hub controls too quickly. Wait a few seconds and try again.")
        return False

    async def _resolve(self, interaction: discord.Interaction) -> Optional[dict[str, Any]]:
        message_id = _safe_int(getattr(getattr(interaction, "message", None), "id", 0), 0)
        guild_id = int(interaction.guild_id or 0)
        if message_id <= 0 or guild_id <= 0:
            await _private(interaction, "This Community Hub session card is no longer attached to a server session.")
            return None
        try:
            session = await hub.get_session_by_panel_message(guild_id, message_id)
        except hub.CommunityHubError as exc:
            await _private(interaction, f"❌ {_error_text(exc)}")
            return None
        if not session:
            await _private(interaction, "This is an old or untracked Community Hub card. Use Community Hub to find the current session.")
            return None
        return session

    async def _mutate(
        self,
        interaction: discord.Interaction,
        action: str,
        factory_builder: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> None:
        await _defer_update(interaction)
        session = await self._resolve(interaction)
        if session is None:
            return
        sid = _safe_str(session.get("id"))

        async def _factory() -> Any:
            return await factory_builder(session)

        state, result = await _run_session_mutation(interaction, session_id=sid, action=action, factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        runtime = ensure_community_hub_runtime(interaction.client)
        current = result.get("session") if isinstance(result, dict) and isinstance(result.get("session"), dict) else result
        if not isinstance(current, dict):
            current = await hub.get_session(sid, guild_id=int(interaction.guild_id or 0))
        await runtime.refresh_session_card(current)
        await _followup(interaction, "✅ Community Hub updated your session.")

    @discord.ui.button(label="Join Group", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:public:join:v1", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button

        async def _join(session: dict[str, Any]) -> Any:
            sid = _safe_str(session.get("id"))
            result = await hub.join_session(sid, int(interaction.guild_id or 0), int(interaction.user.id))
            ensure_community_hub_runtime(interaction.client).cancel_scheduled_cleanup(sid)
            await hub.bump_hourly_metric(int(interaction.guild_id or 0), "joins", 1)
            await hub.bump_game_metric(int(interaction.guild_id or 0), _safe_str(session.get("game_name"), "Game"), "joins", 1)
            return result

        await self._mutate(interaction, "join", _join)

    @discord.ui.button(label="Leave Group", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="dank:hub:public:leave:v1", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button

        async def _leave(session: dict[str, Any]) -> Any:
            result = await hub.leave_session(_safe_str(session.get("id")), int(interaction.guild_id or 0), int(interaction.user.id))
            await hub.bump_hourly_metric(int(interaction.guild_id or 0), "leaves", 1)
            if bool(result.get("needs_cleanup")):
                runtime = ensure_community_hub_runtime(interaction.client)
                settings = await runtime.settings_for(int(interaction.guild_id or 0))
                runtime.schedule_empty_cleanup(
                    _safe_str(session.get("id")),
                    int(interaction.guild_id or 0),
                    actor_id=int(interaction.user.id),
                    delay_seconds=max(60, _safe_int(settings.get("cleanup_grace_seconds"), 300)),
                )
            return result

        await self._mutate(interaction, "leave", _leave)

    @discord.ui.button(label="Ready", emoji="☑️", style=discord.ButtonStyle.primary, custom_id="dank:hub:public:ready:v1", row=0)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        session = await self._resolve(interaction)
        if session is None:
            return
        sid = _safe_str(session.get("id"))
        try:
            members = await hub.list_session_members(sid)
            mine = next((row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)), None)
            if mine is None:
                return await _followup(interaction, "Join the group before changing your ready state.")
            new_ready = not bool(mine.get("ready"))
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        async def _factory() -> Any:
            return await hub.set_ready(sid, int(interaction.guild_id or 0), int(interaction.user.id), new_ready)

        state, current = await _run_session_mutation(interaction, session_id=sid, action="ready", factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        await ensure_community_hub_runtime(interaction.client).refresh_session_card(current)
        await _followup(interaction, "✅ Ready." if new_ready else "Ready status cleared.")

    @discord.ui.button(label="Manage", emoji="🎛️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:public:manage:v1", row=0)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_ephemeral(interaction)
        session = await self._resolve(interaction)
        if session is None:
            return
        sid = _safe_str(session.get("id"))
        try:
            members = await hub.list_session_members(sid)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        mine = next((row for row in members if _safe_int(row.get("user_id")) == int(interaction.user.id)), None)
        if _safe_str((mine or {}).get("role")) not in {"host", "cohost"} and not _staff_authorized(interaction):
            return await _followup(interaction, "Only the host, a co-host, or authorized staff can manage this session.")
        await interaction.followup.send(
            embed=build_session_embed(session, members),
            view=SessionControlView(int(interaction.user.id), session_id=sid),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @discord.ui.button(label="Auto-Caption My Voice", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="dank:hub:public:captionme:v1", row=1)
    async def caption_me(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_ephemeral(interaction)
        session = await self._resolve(interaction)
        if session is None:
            return
        message = await _toggle_live_caption_consent(interaction, _safe_str(session.get("id")))
        await _followup(interaction, message)

    @discord.ui.button(label="Play Again", emoji="🔁", style=discord.ButtonStyle.success, custom_id="dank:hub:public:replay:v1", row=0)
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_ephemeral(interaction)
        session = await self._resolve(interaction)
        if session is None:
            return
        sid = _safe_str(session.get("id"))
        runtime = ensure_community_hub_runtime(interaction.client)

        async def _factory() -> Any:
            return await runtime.restart_from_ended(interaction, session)

        state, result = await _run_session_mutation(interaction, session_id=sid, action="play_again", factory=_factory)
        if not await _operation_feedback(interaction, state):
            return
        new_session = result.get("session") if isinstance(result, dict) else None
        await _followup(
            interaction,
            f"✅ New {_safe_str((new_session or {}).get('game_name'), 'gaming')} session created. The old session remains ended.",
        )


def _pulse_embed(pulse: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="📈 Community Pulse",
        description="Current aggregate community activity. No long-term per-member presence history is shown here.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Active groups", value=str(_safe_int(pulse.get("active_groups"))), inline=True)
    embed.add_field(name="In voice", value=str(_safe_int(pulse.get("voice_members"))), inline=True)
    embed.add_field(name="Open to Play", value=str(_safe_int(pulse.get("open_to_play_members"))), inline=True)
    open_games = pulse.get("open_to_play_games") or []
    if open_games:
        embed.add_field(
            name="Players available now",
            value="\n".join(
                f"• {_safe_str(row.get('name'), 'Game')}: {_safe_int(row.get('count'))}"
                for row in open_games[:5]
            ),
            inline=False,
        )
    if bool(pulse.get("presence_available")):
        embed.add_field(name="Online", value=str(_safe_int(pulse.get("online_members"))), inline=True)
        embed.add_field(name="Gaming", value=str(_safe_int(pulse.get("gaming_members"))), inline=True)
        games = pulse.get("top_games") or []
        if games:
            embed.add_field(
                name="Playing now",
                value="\n".join(f"• {name}: {count}" for name, count in games[:5]),
                inline=False,
            )
    else:
        embed.add_field(
            name="Game presence",
            value=(
                "Not available. Discord Presence is privileged and Community Hub does not assume it is authorized. "
                "Find Players and voice/session features still work without it."
            ),
            inline=False,
        )
    return embed


class PulseView(_OwnedView):
    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:pulse:refresh:v1")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        pulse = await ensure_community_hub_runtime(interaction.client).pulse_snapshot(interaction.guild)
        await _edit_private_original(interaction, content=None, embed=_pulse_embed(pulse), view=PulseView(self.owner_id))

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:pulse:back:v1")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())


def _notification_embed(prefs: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="🔔 Game Notifications",
        description=(
            "Opt in by game. Notifications are never enabled merely because Discord says you played something. "
            "Use the button below to add or update a game preference."
        ),
        color=discord.Color.blurple(),
    )
    if not prefs:
        embed.add_field(name="No game notifications", value="You have not opted into any game alerts in this server.", inline=False)
    else:
        lines = []
        for row in prefs[:20]:
            state = "On" if bool(row.get("enabled")) else "Off"
            lines.append(f"• {_safe_str(row.get('game_name'), 'Game')}: {state}")
        embed.add_field(name="Your preferences", value="\n".join(lines), inline=False)
    return embed


class NotificationModal(discord.ui.Modal, title="Game Notification Preference"):
    game = discord.ui.TextInput(label="Game", placeholder="Minecraft", min_length=1, max_length=80)
    enabled = discord.ui.TextInput(label="Enabled?", placeholder="yes or no", default="yes", min_length=2, max_length=3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        enabled = _safe_str(self.enabled.value).lower() in {"yes", "y", "on"}
        try:
            pref = await hub.set_notification_preference(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                str(self.game.value),
                enabled=enabled,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _followup(interaction, f"✅ {_safe_str(pref.get('game_name'), 'Game')} notifications are {'on' if enabled else 'off'}.")


class NotificationView(_OwnedView):
    @discord.ui.button(label="Add / Update Game", emoji="🔔", style=discord.ButtonStyle.primary, custom_id="dank:hub:notif:set:v1")
    async def set_pref(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(NotificationModal())

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:notif:back:v1")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())


def _event_time(value: Any) -> str:
    text = _safe_str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        ts = int(dt.timestamp())
        return f"<t:{ts}:F> • <t:{ts}:R>"
    except Exception:
        return text or "Time not available"


def _events_embed(events: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="📅 Community Events",
        description="Upcoming Community Hub events and game nights.",
        color=discord.Color.blurple(),
    )
    if not events:
        embed.add_field(name="Nothing scheduled", value="Staff can create an event from the Community Hub Staff Dashboard.", inline=False)
    else:
        for event in events[:8]:
            embed.add_field(
                name=_safe_str(event.get("title"), "Community Event")[:256],
                value=f"{_event_time(event.get('starts_at'))}\n{_safe_str(event.get('game_name')) or 'Community event'}",
                inline=False,
            )
    return embed


class EventSelect(discord.ui.Select):
    def __init__(self, owner_id: int, events: list[dict[str, Any]]) -> None:
        self.owner_id = int(owner_id)
        self.events = {_safe_str(row.get("id")): row for row in events if row.get("id")}
        options = [
            discord.SelectOption(
                label=_safe_str(row.get("title"), "Event")[:100],
                value=_safe_str(row.get("id")),
                description=_safe_str(row.get("game_name"), "Community event")[:100],
                emoji="📅",
            )
            for row in events[:25]
        ]
        super().__init__(
            placeholder="Choose an event",
            options=options or [discord.SelectOption(label="No events", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:event:select:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        eid = self.values[0]
        if eid == "none":
            return
        event = self.events.get(eid)
        if event is None:
            return
        await _defer_update(interaction)
        attendees = await hub.list_event_attendees(eid)
        going = sum(1 for row in attendees if row.get("response") in {"going", "attended"})
        interested = sum(1 for row in attendees if row.get("response") == "interested")
        embed = discord.Embed(
            title=f"📅 {_safe_str(event.get('title'), 'Community Event')}",
            description=_safe_str(event.get("description")) or "Community Hub event",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="When", value=_event_time(event.get("starts_at")), inline=False)
        if event.get("game_name"):
            embed.add_field(name="Game", value=_safe_str(event.get("game_name")), inline=True)
        embed.add_field(name="Going", value=str(going), inline=True)
        embed.add_field(name="Interested", value=str(interested), inline=True)
        await _edit_private_original(interaction, content=None, embed=embed, view=EventDetailView(self.owner_id, event_id=eid))


class EventsView(_OwnedView):
    def __init__(self, owner_id: int, events: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.add_item(EventSelect(owner_id, events))

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:event:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())


class EventDetailView(_OwnedView):
    def __init__(self, owner_id: int, *, event_id: str) -> None:
        super().__init__(owner_id)
        self.event_id = event_id

    async def _rsvp(self, interaction: discord.Interaction, response: str) -> None:
        await _defer_update(interaction)
        try:
            row = await hub.rsvp_event(
                self.event_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                response,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        actual = _safe_str(row.get("response"), response)
        if actual == "waitlist":
            await _followup(interaction, "✅ The event is full, so you're on the waitlist. If a spot opens, Community Hub will promote the oldest waiting spot.")
            return
        await _followup(interaction, f"✅ Event response saved: {actual.replace('_', ' ')}.")

    @discord.ui.button(label="Going", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:event:going:v1")
    async def going(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._rsvp(interaction, "going")

    @discord.ui.button(label="Interested", emoji="⭐", style=discord.ButtonStyle.primary, custom_id="dank:hub:event:interest:v1")
    async def interested(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._rsvp(interaction, "interested")

    @discord.ui.button(label="Not Going", emoji="🚫", style=discord.ButtonStyle.secondary, custom_id="dank:hub:event:no:v1")
    async def not_going(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._rsvp(interaction, "declined")

    @discord.ui.button(label="Events", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:event:list:v1")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        events = await hub.list_upcoming_events(int(interaction.guild_id or 0), limit=25)
        await _edit_private_original(interaction, content=None, embed=_events_embed(events), view=EventsView(self.owner_id, events))


async def _open_staff_dashboard(interaction: discord.Interaction, owner_id: int) -> None:
    if not _staff_authorized(interaction):
        return await _followup(interaction, "Staff Dashboard requires server management authority.")
    try:
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        sessions = await hub.list_active_sessions(int(interaction.guild_id or 0), limit=100)
    except hub.CommunityHubError as exc:
        return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(owner_id))
    embed = discord.Embed(
        title="🛠️ Community Hub Staff Dashboard",
        description="Configuration, analytics, session operations, events, partner federation, and reliability health.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Mode", value=_safe_str(settings.get("mode"), "minimal").title(), inline=True)
    embed.add_field(name="Active sessions", value=str(len(sessions)), inline=True)
    embed.add_field(name="Maintenance", value="On" if bool(settings.get("maintenance_mode")) else "Off", inline=True)
    embed.add_field(
        name="Temporary spaces",
        value=(
            f"Voice: {'On' if bool(settings.get('auto_create_voice')) else 'Off'} • "
            f"Threads: {'On' if bool(settings.get('auto_create_thread')) else 'Off'}"
        ),
        inline=False,
    )
    await _edit_private_original(
        interaction,
        content=None,
        embed=embed,
        view=StaffDashboardView(owner_id),
    )


def _safety_reports_embed(reports: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="🚩 Community Hub Safety",
        description=(
            "Private session reports submitted through Community Hub. These reports do not automatically punish anyone; "
            "staff reviews the context and uses the normal moderation system when action is warranted."
        ),
        color=discord.Color.blurple(),
    )
    if not reports:
        embed.add_field(name="Queue clear", value="No open Community Hub safety reports.", inline=False)
        return embed
    for row in reports[:10]:
        rid = _safe_str(row.get("id"))
        embed.add_field(
            name=f"{_safe_str(row.get('reason'), 'Report')[:220]} • {rid[:8]}",
            value=(
                f"State: {_safe_str(row.get('state'), 'open').title()} • "
                f"Reporter: <@{_safe_str(row.get('reporter_user_id'))}>"
            )[:1024],
            inline=False,
        )
    return embed


class SafetyReportSelect(discord.ui.Select):
    def __init__(self, owner_id: int, reports: list[dict[str, Any]]) -> None:
        self.owner_id = int(owner_id)
        self.reports = {_safe_str(row.get("id")): row for row in reports if row.get("id")}
        options = [
            discord.SelectOption(
                label=_safe_str(row.get("reason"), "Community Hub report")[:100],
                value=_safe_str(row.get("id")),
                description=f"{_safe_str(row.get('state'), 'open').title()} • case {_safe_str(row.get('id'))[:8]}"[:100],
                emoji="🚩",
            )
            for row in reports[:25]
        ]
        super().__init__(
            placeholder="Choose a safety report",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No open reports", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:staff:safetyselect:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _staff_authorized(interaction):
            return await _private(interaction, "Community Hub safety review requires server management authority.")
        report_id = self.values[0]
        if report_id == "none":
            return
        await _defer_update(interaction)
        row = self.reports.get(report_id)
        if row is None:
            return await _followup(interaction, "That safety report is no longer in this view.")
        embed = discord.Embed(
            title=f"🚩 Safety Report • {report_id[:8]}",
            description=_safe_str(row.get("details")) or "No additional details were supplied.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Reason", value=_safe_str(row.get("reason"), "Not specified")[:1024], inline=False)
        embed.add_field(name="Reporter", value=f"<@{_safe_str(row.get('reporter_user_id'))}>", inline=True)
        embed.add_field(name="State", value=_safe_str(row.get("state"), "open").title(), inline=True)
        session_id = _safe_str(row.get("session_id"))
        if session_id:
            embed.add_field(name="Session", value=session_id[:36], inline=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=embed,
            view=SafetyReportDetailView(self.owner_id, report_id=report_id),
        )


class SafetyReportsView(_OwnedView):
    def __init__(self, owner_id: int, reports: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.add_item(SafetyReportSelect(owner_id, reports))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Community Hub safety review requires server management authority.")
        return False

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:safetyrefresh:v1", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        reports = await hub.list_reports(int(interaction.guild_id or 0), states=("open", "reviewing"), limit=50)
        await _edit_private_original(interaction, content=None, embed=_safety_reports_embed(reports), view=SafetyReportsView(self.owner_id, reports))

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:safetyback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


class SafetyReportDetailView(_OwnedView):
    def __init__(self, owner_id: int, *, report_id: str) -> None:
        super().__init__(owner_id)
        self.report_id = report_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Community Hub safety review requires server management authority.")
        return False

    async def _set_state(self, interaction: discord.Interaction, state: str) -> None:
        await _defer_update(interaction)
        try:
            await hub.update_report_state(
                self.report_id,
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                state,
            )
            reports = await hub.list_reports(
                int(interaction.guild_id or 0),
                states=("open", "reviewing"),
                limit=50,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_safety_reports_embed(reports),
            view=SafetyReportsView(self.owner_id, reports),
        )
        await _followup(interaction, f"✅ Report marked {state}.")

    @discord.ui.button(label="Reviewing", emoji="👀", style=discord.ButtonStyle.primary, custom_id="dank:hub:staff:reportreview:v1", row=0)
    async def reviewing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._set_state(interaction, "reviewing")

    @discord.ui.button(label="Resolve", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:staff:reportresolve:v1", row=0)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._set_state(interaction, "resolved")

    @discord.ui.button(label="Dismiss", emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:reportdismiss:v1", row=0)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._set_state(interaction, "dismissed")

    @discord.ui.button(label="Safety Queue", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:reportback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        reports = await hub.list_reports(int(interaction.guild_id or 0), states=("open", "reviewing"), limit=50)
        await _edit_private_original(interaction, content=None, embed=_safety_reports_embed(reports), view=SafetyReportsView(self.owner_id, reports))



def _game_pings_embed(config: dict[str, Any]) -> discord.Embed:
    state = game_pings._model(config.get(game_pings.CONFIG_KEY))
    embed = discord.Embed(
        title="🎮 Game Role Notifications",
        description=(
            "Map games to existing, **enabled Notification roles** from Community & Pings. "
            "Role pings are **never automatic**: the active group's host/co-host or staff "
            "must press **Call Game Crew** in that session's Manage panel. "
            "Cooldowns are durable across bot restarts and shared between servers only within their own guild."
        ),
        color=discord.Color.blurple(),
    )
    mapped = list(state["games"].items())[:game_pings.MAX_GAMES]
    if not mapped:
        embed.add_field(
            name="No game roles configured",
            value="First create an enabled Notification role in **/dank pings**, then configure its game here.",
            inline=False,
        )
    else:
        lines = [
            f"• **{discord.utils.escape_markdown(_safe_str(row.get('game_name'), key))}** "
            f"→ <@&{_safe_int(row.get('role_id'))}> "
            f"• {_safe_int(row.get('cooldown_minutes'), 30)} min"
            for key, row in mapped
        ]
        embed.add_field(name=f"Game role mappings ({len(mapped)}/{game_pings.MAX_GAMES})", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="No /toke or CHEERS role reuse • max 4 calls/hour/server • 5-minute global gap • one ping per session")
    return embed


async def _open_game_pings_manager(interaction: discord.Interaction, owner_id: int) -> None:
    if not _staff_authorized(interaction) or interaction.guild is None:
        return await _followup(interaction, "Only server-management staff can configure Game Role Notifications.")
    try:
        config = await get_guild_config(interaction.guild.id, refresh=True)
    except Exception:
        return await _followup(interaction, "Game-role configuration storage is unavailable.")
    await _edit_private_original(
        interaction, content=None, embed=_game_pings_embed(config),
        view=GameRolePingsManagerView(owner_id),
    )


class GameRolePingsManagerView(_OwnedView):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Only server-management staff can configure game-role pings.")
        return False

    @discord.ui.button(label="Configure Game", emoji="🎮", style=discord.ButtonStyle.primary, custom_id="dank:hub:gamepings:configure:v1")
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(GameRoleConfigModal(self.owner_id))

    @discord.ui.button(label="Remove Mapping", emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:gamepings:remove:v1")
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(GameRoleRemoveModal(self.owner_id))

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:gamepings:back:v1")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


class GameRoleConfigModal(discord.ui.Modal, title="Configure Game Role Ping"):
    game = discord.ui.TextInput(label="Game name", placeholder="Minecraft", min_length=1, max_length=80)
    cooldown = discord.ui.TextInput(label="Cooldown minutes (15 to 240)", placeholder="30", default="30", max_length=3)

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id or not _staff_authorized(interaction):
            return await _private(interaction, "Only server-management staff can map game notification roles.")
        await _defer_ephemeral(interaction)
        try:
            game = hub.normalize_game_name(self.game.value)
            minutes = int(str(self.cooldown.value))
            if not 15 <= minutes <= 240:
                raise ValueError("Cooldown must be between 15 and 240 minutes.")
            config = await get_guild_config(int(interaction.guild_id or 0), refresh=True)
            roles = game_pings.eligible_game_roles(interaction.guild, config)
        except (ValueError, hub.CommunityHubError):
            return await _followup(interaction, "Enter a valid game and cooldown of 15–240 minutes.")
        except Exception:
            return await _followup(interaction, "Could not load the current Community & Pings roles.")
        if not roles:
            return await _followup(
                interaction,
                "No eligible roles. Open **/dank pings**, create or enable a Notification role "
                "separate from /toke, CHEERS and Movie Night, then retry.",
            )
        browser = _game_role_browser(interaction.guild, self.owner_id, game, minutes, roles)
        await interaction.edit_original_response(
            embed=browser.embed(), content=None, view=browser,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _game_role_browser(
    guild: discord.Guild, owner_id: int, game: str, minutes: int,
    roles: list[tuple[int, str]],
) -> DankGuildResourceBrowserView:
    """Reuse the canonical Search-Safe role discovery UI; keep saves in game_pings."""
    eligible_ids = {int(role_id) for role_id, _ in roles}

    async def picked(interaction: discord.Interaction, role: Any) -> None:
        if not _staff_authorized(interaction) or interaction.guild != guild:
            return await _private(interaction, "Only server-management staff can map game notification roles.")
        if not isinstance(role, discord.Role) or int(role.id) not in eligible_ids:
            return await _private(interaction, "That Notification role is no longer available. Reopen Game Role Pings.")
        await _defer_update(interaction)
        try:
            # configure() independently reloads and verifies current Community
            # & Pings eligibility before its existing atomic guild-config CAS.
            saved = await game_pings.configure(
                guild, game, int(role.id), cooldown_minutes=minutes,
            )
        except (ValueError, hub.CommunityHubError) as exc:
            return await _followup(interaction, f"❌ {str(exc)[:250]}")
        if not saved:
            return await _followup(interaction, "Game-role settings could not be saved atomically. No changes confirmed.")
        await _open_game_pings_manager(interaction, owner_id)

    async def back(interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        await _open_game_pings_manager(interaction, owner_id)

    return DankGuildResourceBrowserView(
        guild=guild,
        author_id=owner_id,
        resource_kinds=("role",),
        on_pick=picked,
        custom_id="dank:hub:gamepings:role_browser:v1",
        title=f"🎮 Game Role for {game}"[:256],
        placeholder="Choose an enabled Notification role…",
        predicate=lambda role: isinstance(role, discord.Role) and int(role.id) in eligible_ids,
        on_home=back,
        home_label="Back to Game Role Pings",
        empty_message="No eligible Notification roles match. Check /dank pings or clear the search.",
    )


class GameRoleRemoveModal(discord.ui.Modal, title="Remove Game Role Mapping"):
    game = discord.ui.TextInput(label="Game name to remove", placeholder="Minecraft", min_length=1, max_length=80)

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id or not _staff_authorized(interaction):
            return await _private(interaction, "Only server-management staff can remove game-role mappings.")
        await _defer_ephemeral(interaction)
        try:
            saved = await game_pings.configure(
                interaction.guild, str(self.game.value), 0, remove=True,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        if not saved:
            return await _followup(interaction, "Could not confirm removal. The previous mapping remains active.")
        await _open_game_pings_manager(interaction, self.owner_id)


class StaffDashboardView(_OwnedView):
    @discord.ui.button(label="Public Gaming Panel", emoji="🎮", style=discord.ButtonStyle.primary, custom_id="dank:hub:staff:publicpanel:v1", row=2)
    async def public_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Only Manage Server staff can post the public gaming panel.")
        await _defer_update(interaction)
        from .community_lfg_public_panel_ui import show_public_panel_setup
        await show_public_panel_setup(interaction, self.owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Staff Dashboard requires server management authority.")
        return False

    @discord.ui.button(label="Analytics", emoji="📊", style=discord.ButtonStyle.primary, custom_id="dank:hub:staff:analytics:v1", row=0)
    async def analytics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            summary = await hub.analytics_summary(int(interaction.guild_id or 0), days=7)
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(interaction, content=None, embed=_analytics_embed(summary), view=AnalyticsView(self.owner_id, days=7))

    @discord.ui.button(label="Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:settings:v1", row=0)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

    @discord.ui.button(label="Active Sessions", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:sessions:v1", row=0)
    async def sessions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        sessions = await hub.list_active_sessions(int(interaction.guild_id or 0), limit=25)
        await _edit_private_original(interaction, content=None, embed=_find_players_embed(sessions, partner=False), view=StaffSessionsView(self.owner_id, sessions))

    @discord.ui.button(label="Reliability Health", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:health:v1", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        snapshot = await ensure_community_hub_runtime(interaction.client).reliability_snapshot()
        await _edit_private_original(interaction, content=None, embed=_health_embed(snapshot), view=StaffBackView(self.owner_id))

    @discord.ui.button(label="Create Event", emoji="📅", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:event:v1", row=1)
    async def create_event(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CreateEventModal())

    @discord.ui.button(label="Safety Reports", emoji="🚩", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:safety:v1", row=1)
    async def safety_reports(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            reports = await hub.list_reports(
                int(interaction.guild_id or 0),
                states=("open", "reviewing"),
                limit=50,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction,
            content=None,
            embed=_safety_reports_embed(reports),
            view=SafetyReportsView(self.owner_id, reports),
        )

    @discord.ui.button(label="Partner Network", emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:partner:v1", row=1)
    async def partner(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(interaction, content=None, embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client), view=PartnerAdminView(self.owner_id, links))

    @discord.ui.button(label="Game Role Pings", emoji="📣", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:gamepings:v1", row=1)
    async def game_role_pings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_game_pings_manager(interaction, self.owner_id)

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:back:v1", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())


class StaffBackView(_OwnedView):
    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staffback:v1")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


def _analytics_embed(summary: dict[str, Any]) -> discord.Embed:
    days = _safe_int(summary.get("days"), 7)
    created = _safe_int(summary.get("sessions_created"))
    completed = _safe_int(summary.get("sessions_completed"))
    completion = float(summary.get("completion_rate") or 0.0)
    average = max(0, _safe_int(float(summary.get("average_duration_seconds") or 0.0)))
    hours, rem = divmod(average, 3600)
    minutes = rem // 60
    embed = discord.Embed(
        title=f"📊 Community Hub Analytics • {days} days",
        description="Aggregate staff analytics. Presence peaks are only populated when Discord Presence access and the server opt-in are both enabled.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Sessions created", value=str(created), inline=True)
    embed.add_field(name="Completed", value=str(completed), inline=True)
    embed.add_field(name="Completion rate", value=f"{completion * 100:.1f}%", inline=True)
    embed.add_field(name="Joins", value=str(_safe_int(summary.get("joins"))), inline=True)
    embed.add_field(name="Leaves", value=str(_safe_int(summary.get("leaves"))), inline=True)
    embed.add_field(name="Average duration", value=f"{hours}h {minutes}m", inline=True)
    embed.add_field(name="Voice peak", value=str(_safe_int(summary.get("voice_participants_peak"))), inline=True)
    embed.add_field(name="Gaming presence peak", value=str(_safe_int(summary.get("gaming_presence_peak"))), inline=True)
    embed.add_field(name="Cleanup failures", value=str(_safe_int(summary.get("cleanup_failures"))), inline=True)
    games = summary.get("top_games") or []
    if games:
        embed.add_field(
            name="Top games",
            value="\n".join(
                f"• {_safe_str(row.get('name'), 'Game')}: {_safe_int(row.get('joins'))} joins, {_safe_int(row.get('sessions'))} sessions"
                for row in games[:8]
            ),
            inline=False,
        )
    return embed


class AnalyticsRangeSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current_days: int) -> None:
        self.owner_id = int(owner_id)
        options = [
            discord.SelectOption(label="24 hours", value="1", default=current_days == 1),
            discord.SelectOption(label="7 days", value="7", default=current_days == 7),
            discord.SelectOption(label="30 days", value="30", default=current_days == 30),
            discord.SelectOption(label="90 days", value="90", default=current_days == 90),
        ]
        super().__init__(placeholder="Analytics range", options=options, custom_id="dank:hub:analytics:range:v1")

    async def callback(self, interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        days = _safe_int(self.values[0], 7)
        summary = await hub.analytics_summary(int(interaction.guild_id or 0), days=days)
        await _edit_private_original(interaction, content=None, embed=_analytics_embed(summary), view=AnalyticsView(self.owner_id, days=days))


class AnalyticsView(_OwnedView):
    def __init__(self, owner_id: int, *, days: int) -> None:
        super().__init__(owner_id)
        self.add_item(AnalyticsRangeSelect(owner_id, days))

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:analytics:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


def _settings_embed(settings: dict[str, Any], bot: Any) -> discord.Embed:
    presence_runtime = bool(getattr(getattr(bot, "intents", None), "presences", False))
    on = lambda key, default=False: "On" if bool(settings.get(key, default)) else "Off"
    embed = discord.Embed(
        title="⚙️ Community Hub Settings",
        description=(
            f"**Mode: {_safe_str(settings.get('mode'), 'minimal').title()}** • "
            f"Maintenance: **{on('maintenance_mode')}**\n"
            "Minimal posts session cards; Smart manages temporary spaces; "
            "Managed adds full Hub infrastructure. Choose the area you need below."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Participation & safety",
        value=(
            f"Public recruiting: **{on('public_lfg_enabled')}** • "
            f"Member-created: **{on('allow_member_creation', True)}**\n"
            f"Analytics: **{on('analytics_enabled', True)}** • "
            f"Presence: **{on('presence_analytics_enabled') if presence_runtime else 'Intent unavailable'}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Session limits",
        value=(
            f"Active: **{_safe_int(settings.get('max_active_sessions'), 20)}** • "
            f"Per member: **{_safe_int(settings.get('max_active_sessions_per_member'), 2)}** • "
            f"Voice rooms: **{_safe_int(settings.get('max_temporary_voice_rooms'), 12)}**\n"
            f"Group size: **{_safe_int(settings.get('max_session_capacity'), 25)}** • "
            f"Minimum: **{_safe_int(settings.get('minimum_players_to_start'), 1)}** • "
            f"Ready required: **{on('require_ready_to_start')}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Notifications & outside groups",
        value=(
            f"Notices: **{on('notifications_enabled', True)}** • "
            f"Max/hour: **{_safe_int(settings.get('max_notifications_per_hour'), 5)}**\n"
            f"The100.io: **{on('external_the100_enabled')}** "
            f"({'API ready' if external.the100_configured() else 'No host token'}) • Read-only"
        ),
        inline=False,
    )
    embed.add_field(
        name="Cleanup and abuse limits",
        value=(
            f"Cooldown: **{_safe_int(settings.get('session_creation_cooldown_seconds'), 30)}s** • "
            f"Reports/hour: **{_safe_int(settings.get('max_reports_per_hour_per_member'), 5)}**\n"
            f"Idle: **{_safe_int(settings.get('idle_timeout_seconds'), 1800)}s** • "
            f"Lifetime: **{_safe_int(settings.get('max_session_lifetime_seconds'), 43200)}s** • "
            f"End: **{'Archive' if bool(settings.get('archive_threads_on_end', True)) else 'Delete'}**"
        ),
        inline=False,
    )
    embed.set_footer(text="Detailed limits, ready timers and dispatch caps are in their settings controls")
    return embed


class ModeSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current: str, *, section: str = "general") -> None:
        self.owner_id = int(owner_id)
        self.section = section
        options = [
            discord.SelectOption(label="Minimal", value="minimal", description="Session cards and member controls only", default=current == "minimal"),
            discord.SelectOption(label="Smart", value="smart", description="Temporary discussion and voice when permissions allow", default=current == "smart"),
            discord.SelectOption(label="Managed", value="managed", description="Full managed Community Hub infrastructure", default=current == "managed"),
        ]
        super().__init__(placeholder="Community Hub mode", options=options, custom_id="dank:hub:settings:mode:v1", row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        runtime = ensure_community_hub_runtime(interaction.client)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"mode": self.values[0]},
            actor_id=int(interaction.user.id),
        )
        runtime.invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))


class LimitsModal(discord.ui.Modal, title="Community Hub Limits"):
    active = discord.ui.TextInput(label="Max active sessions", default="20", max_length=3)
    per_member = discord.ui.TextInput(label="Max active sessions per member", default="2", max_length=2)
    voices = discord.ui.TextInput(label="Max temporary voice rooms", default="12", max_length=2)
    capacity = discord.ui.TextInput(label="Maximum group size", default="25", max_length=2)
    grace = discord.ui.TextInput(label="Cleanup grace seconds", default="300", max_length=5)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            patch = {
                "max_active_sessions": max(1, min(100, int(str(self.active.value)))),
                "max_active_sessions_per_member": max(1, min(10, int(str(self.per_member.value)))),
                "max_temporary_voice_rooms": max(0, min(50, int(str(self.voices.value)))),
                "max_session_capacity": max(2, min(99, int(str(self.capacity.value)))),
                "cleanup_grace_seconds": max(60, min(86400, int(str(self.grace.value)))),
            }
        except Exception:
            return await _followup(interaction, "All Community Hub limit fields must be numbers.")
        settings = await hub.update_settings(int(interaction.guild_id or 0), patch, actor_id=int(interaction.user.id))
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _followup(interaction, "✅ Community Hub limits updated.")
        _ = settings


class AbuseLimitsModal(discord.ui.Modal, title="Community Hub Abuse Limits"):
    creation_cooldown = discord.ui.TextInput(label="Session create cooldown seconds", default="30", max_length=4)
    creations_hour = discord.ui.TextInput(label="Max session creates per member/hour", default="10", max_length=3)
    reports_hour = discord.ui.TextInput(label="Max safety reports per member/hour", default="5", max_length=2)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            patch = {
                "session_creation_cooldown_seconds": max(5, min(3600, int(str(self.creation_cooldown.value)))),
                "max_session_creations_per_hour_per_member": max(1, min(100, int(str(self.creations_hour.value)))),
                "max_reports_per_hour_per_member": max(1, min(50, int(str(self.reports_hour.value)))),
            }
        except Exception:
            return await _followup(interaction, "Abuse-limit values must be valid numbers.")
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            patch,
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _followup(interaction, "✅ Community Hub abuse limits updated.")
        _ = settings


class LifecycleModal(discord.ui.Modal, title="Community Hub Lifecycle"):
    minimum = discord.ui.TextInput(label="Minimum players to start", default="1", max_length=2)
    ready_required = discord.ui.TextInput(label="Require everyone ready? yes/no", default="no", max_length=3)
    ready_timeout = discord.ui.TextInput(label="Ready expires after seconds", default="300", max_length=4)
    idle_minutes = discord.ui.TextInput(label="Idle timeout minutes", default="30", max_length=5)
    max_hours = discord.ui.TextInput(label="Maximum session lifetime hours", default="12", max_length=3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            minimum = max(1, min(99, int(str(self.minimum.value))))
            ready_seconds = max(60, min(3600, int(str(self.ready_timeout.value))))
            idle_seconds = max(300, min(604800, int(str(self.idle_minutes.value)) * 60))
            lifetime_seconds = max(1800, min(1209600, int(str(self.max_hours.value)) * 3600))
        except Exception:
            return await _followup(interaction, "Lifecycle values must be valid numbers.")
        ready_text = _safe_str(self.ready_required.value).lower()
        if ready_text not in {"yes", "no", "y", "n"}:
            return await _followup(interaction, "Require everyone ready must be yes or no.")
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {
                "minimum_players_to_start": minimum,
                "require_ready_to_start": ready_text in {"yes", "y"},
                "ready_timeout_seconds": ready_seconds,
                "idle_timeout_seconds": idle_seconds,
                "max_session_lifetime_seconds": lifetime_seconds,
            },
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _followup(interaction, "✅ Community Hub lifecycle settings updated.")
        _ = settings


class NotificationLimitsModal(discord.ui.Modal, title="Community Hub Notification Safety"):
    per_hour = discord.ui.TextInput(label="Max alerts per member per hour", default="5", max_length=2)
    cooldown = discord.ui.TextInput(label="Cooldown seconds per member", default="300", max_length=4)
    dispatch = discord.ui.TextInput(label="Max members per dispatch", default="50", max_length=3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        try:
            patch = {
                "max_notifications_per_hour": max(1, min(50, int(str(self.per_hour.value)))),
                "notification_cooldown_seconds": max(60, min(3600, int(str(self.cooldown.value)))),
                "max_notification_targets_per_dispatch": max(0, min(250, int(str(self.dispatch.value)))),
            }
        except Exception:
            return await _followup(interaction, "Notification safety values must be valid numbers.")
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            patch,
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _followup(interaction, "✅ Notification safety limits updated.")
        _ = settings


class CategorySelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = int(owner_id)
        super().__init__(
            placeholder="Choose parent category for temporary voice rooms",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.category],
            custom_id="dank:hub:settings:categoryselect:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _defer_update(interaction)
        channel = self.values[0]
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"parent_category_id": str(channel.id)},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))
        await _followup(interaction, "✅ Temporary voice rooms will use that category when possible.")


class CategoryPickerView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(CategorySelect(owner_id))

    @discord.ui.button(label="Back to Settings", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:catback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))


class PublicNetworkSearchModal(discord.ui.Modal, title="Search Public Gaming Network"):
    game = discord.ui.TextInput(
        label="Game to search (clear for all)",
        placeholder="Minecraft, Destiny 2, GTA V",
        required=False,
        max_length=60,
    )

    def __init__(self, owner_id: int, *, current_game: str = "") -> None:
        super().__init__()
        self.owner_id = int(owner_id)
        self.game.default = str(current_game or "")[:60] or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "Open your own Community Hub panel.")
        await _defer_ephemeral(interaction)
        await _open_public_lfg_network(
            interaction, self.owner_id,
            game_query=_safe_str(self.game.value)[:60],
        )


def _public_network_embed(
    groups: list[dict[str, Any]], *, game_query: str = "",
    page: int = 0, has_next: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Public Gaming Network",
        description=(
            "Browse gaming groups that have **both server approval and host opt-in**. "
            "These groups may be in other Discord servers. "
            "Opening a server invite does not automatically join the group, "
            "and its normal age, verification, and safety rules still apply."
        ),
        color=discord.Color.blurple(),
    )
    if game_query:
        embed.add_field(name="Game search", value=discord.utils.escape_markdown(game_query)[:60], inline=False)
    if not groups:
        embed.add_field(name="No groups found", value="No approved public groups match right now. Try another game.", inline=False)
    for row in groups[:8]:
        embed.add_field(
            name=_safe_str(row["game"], "Game")[:100],
            value=f"Hosted by {discord.utils.escape_markdown(row['community'])[:60]} • Up to {row['capacity']} players. Choose below for the invite.",
            inline=False,
        )
    if page or has_next or len(groups) > 8:
        embed.set_footer(text=f"Page {page + 1} • {len(groups)} choices • browse more with the page controls.")
    return embed


class PublicNetworkSelect(discord.ui.Select):
    def __init__(self, owner_id: int, groups: list[dict[str, Any]], game_query: str, *,
                 page: int = 0, return_to_unified: bool = False, region: str = "") -> None:
        self.owner_id = int(owner_id)
        self.return_to_unified = bool(return_to_unified)
        self.region = str(region or "")[:40]
        self.groups = list(groups[:25])
        self.game_query = game_query
        self.page = max(0, int(page))
        options = [
            discord.SelectOption(
                label=str(row["game"])[:100],
                description=f"{row['community'][:50]} • up to {row['capacity']} players",
                value=str(i),
                emoji="🎮",
            )
            for i, row in enumerate(self.groups)
        ]
        super().__init__(
            placeholder="Choose an outside gaming group",
            custom_id="dank:hub:publicnetwork:select:v1", row=0,
            min_values=1, max_values=1,
            options=options or [discord.SelectOption(label="No public groups right now", value="none")],
            disabled=not bool(options),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        if choice == "none":
            return
        await _defer_update(interaction)
        # A dropdown snapshot alone is not authority: re-read live publication,
        # then require the exact listed invite/game still present.
        try:
            fresh = await hub.list_public_lfg_directory(limit=100)
        except hub.CommunityHubError:
            return await _followup(interaction, "The public directory is unavailable right now. No invitation was opened.")
        from ..community_public_lfg import sanitize_directory
        approved = sanitize_directory(fresh, game=self.game_query)
        try:
            row = self.groups[int(choice)]
        except (ValueError, IndexError):
            return await _followup(interaction, "This listing changed; refresh the directory.")
        if row not in approved:
            return await _followup(interaction, "This group was unpublished or expired. Refresh the directory.")
        embed = discord.Embed(
            title=f"🎮 {row['game'][:80]}",
            description=(
                f"Hosted by **{discord.utils.escape_markdown(row['community'])[:60]}**. "
                "This group was deliberately advertised outside its server. "
                "Join its Discord first, then follow that server's verification and group-joining steps."
            ),
            color=discord.Color.blurple(),
        )
        await _edit_private_original(
            interaction, content=None, embed=embed,
            view=PublicNetworkDetailView(
                self.owner_id, row, game_query=self.game_query, page=self.page,
                return_to_unified=self.return_to_unified, region=self.region,
            ),
        )


class PublicNetworkDetailView(_OwnedView):
    def __init__(self, owner_id: int, row: dict[str, Any], *,
                 game_query: str = "", page: int = 0, return_to_unified: bool = False,
                 region: str = "") -> None:
        super().__init__(owner_id)
        self.return_to_unified = bool(return_to_unified)
        self.region = str(region or "")[:40]
        self.game_query = game_query
        self.page = page
        self.add_item(discord.ui.Button(
            label="Join Discord Community", style=discord.ButtonStyle.link,
            url=row["join_url"], emoji="🌍", row=0,
        ))

    @discord.ui.button(label="Back to Public Groups", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:detailback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if self.return_to_unified:
            await _open_unified_player_discovery(
                interaction, self.owner_id, game=self.game_query, region=self.region,
            )
        else:
            await _open_public_lfg_network(
                interaction, self.owner_id, game_query=self.game_query, page=self.page,
            )


class PublicNetworkView(_OwnedView):
    def __init__(self, owner_id: int, groups: list[dict[str, Any]], *, game_query: str = "", page: int = 0, has_next: bool = False) -> None:
        super().__init__(owner_id)
        self.game_query = game_query
        self.page = max(0, min(int(page), 1))
        self.has_next = bool(has_next)
        self.add_item(PublicNetworkSelect(owner_id, groups, game_query, page=self.page))
        for child in self.children:
            if getattr(child, "custom_id", None) == "dank:hub:publicnetwork:prev:v1":
                child.disabled = self.page == 0
            if getattr(child, "custom_id", None) == "dank:hub:publicnetwork:next:v1":
                child.disabled = not self.has_next

    @discord.ui.button(label="Search Game", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="dank:hub:publicnetwork:search:v1", row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(PublicNetworkSearchModal(self.owner_id, current_game=self.game_query))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:refresh:v1", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_public_lfg_network(interaction, self.owner_id, game_query=self.game_query)

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:prev:v1", row=2)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_public_lfg_network(interaction, self.owner_id, game_query=self.game_query, page=max(0, self.page - 1))

    @discord.ui.button(label="More Groups", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:next:v1", row=2)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not self.has_next:
            return await _private(interaction, "No more public groups are available.")
        await _defer_update(interaction)
        await _open_public_lfg_network(interaction, self.owner_id, game_query=self.game_query, page=self.page + 1)

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publicnetwork:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_hub_embed(), view=CommunityHubView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _open_public_lfg_network(
    interaction: discord.Interaction, owner_id: int, *, game_query: str = "", page: int = 0,
) -> None:
    from ..community_public_lfg import sanitize_directory
    try:
        fresh = await hub.list_public_lfg_directory(limit=100)
        groups = sanitize_directory(fresh, game=game_query)
    except hub.CommunityHubError:
        # Cross-server opt-in storage is optional and might not be deployed.
        # Never dead-end a member who wants to find a group right now.
        return await _open_unified_player_discovery(
            interaction, owner_id, game=game_query,
        )
    # Discord select menus allow at most 25 options. Keep the source bounded,
    # but make all 40 approved public groups reachable on Android and desktop.
    page = max(0, min(int(page), 1))
    start = page * 20
    if page and start >= len(groups):
        page, start = 0, 0
    visible = groups[start:start + 20]
    has_next = len(groups) > start + 20
    await _edit_private_original(
        interaction, content=None,
        embed=_public_network_embed(
            visible, game_query=game_query, page=page, has_next=has_next,
        ),
        view=PublicNetworkView(
            owner_id, visible, game_query=game_query,
            page=page, has_next=has_next,
        ),
    )


def verified_public_lfg_invite(value: Any) -> str:
    from ..community_public_lfg import verified_invite_url
    return verified_invite_url(value)


def community_public_lfg_url() -> str:
    from ..community_public_lfg import public_lfg_page_url
    return public_lfg_page_url()


def _public_recruiting_embed(settings: dict[str, Any]) -> discord.Embed:
    enabled = bool(settings.get("public_lfg_enabled"))
    invite = verified_public_lfg_invite(settings.get("public_lfg_invite_url"))
    url = community_public_lfg_url()
    embed = discord.Embed(
        title="📣 Public Gaming Recruitment",
        description=(
            "Help people **outside this Discord server** discover your gaming groups. "
            "First, authorized staff approve a server invite and publish the board. "
            "Then each group's host or co-host separately chooses **Share Outside Server**. "
            "Private groups and member identities are never published."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Recruitment", value="Enabled" if enabled else "Off", inline=True)
    embed.add_field(name="Public community name", value=_safe_str(settings.get("public_lfg_display_name"), "Not configured")[:60], inline=False)
    embed.add_field(name="Server invite", value=invite or "Not configured", inline=False)
    embed.add_field(name="Public board", value=(url) if url else "Public HTTPS Site URL not configured", inline=False)
    embed.add_field(
        name="Important",
        value="Outside users join your **server**, then must meet your verification/age rules before joining a session. Publishing does not message strangers or auto-advertise elsewhere.",
        inline=False,
    )
    return embed


class PublicRecruitingInviteModal(discord.ui.Modal, title="Publish Gaming Recruitment"):
    community_name = discord.ui.TextInput(
        label="Public community name",
        placeholder="Your Discord server name",
        min_length=2,
        max_length=60,
    )
    invite = discord.ui.TextInput(
        label="Permanent or approved server invite",
        placeholder="https://discord.gg/yourserver",
        max_length=140,
    )
    confirmation = discord.ui.TextInput(
        label="Type PUBLISH to allow public group listings",
        placeholder="PUBLISH",
        max_length=7,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        if not _staff_authorized(interaction) or interaction.guild is None:
            return await _followup(interaction, "Manage Server authority is required.")
        if _safe_str(self.confirmation.value) != "PUBLISH":
            return await _followup(interaction, "Recruitment was not enabled. Enter PUBLISH to confirm.")
        name = " ".join(
            "".join(ch if ch.isprintable() else " " for ch in str(self.community_name.value or ""))
            .replace("@", "＠").split()
        )[:60]
        if len(name) < 2:
            return await _followup(interaction, "Enter a public community name (2–60 characters).")
        url = verified_public_lfg_invite(self.invite.value)
        if not url:
            return await _followup(interaction, "Use a valid HTTPS Discord server invite, not a channel or website link.")
        try:
            live_invite = await interaction.client.fetch_invite(url)
        except (discord.HTTPException, ValueError):
            return await _followup(interaction, "Discord could not verify this invitation. Nothing was published.")
        destination = getattr(live_invite, "guild", None)
        if not destination or int(destination.id) != int(interaction.guild_id or 0):
            return await _followup(interaction, "That invite points to another server. Nothing was published.")
        try:
            await hub.update_settings(
                int(interaction.guild_id or 0),
                {"public_lfg_invite_url": url, "public_lfg_display_name": name, "public_lfg_enabled": True},
                actor_id=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _followup(interaction, "✅ Recruitment is enabled. Hosts must still opt in for EACH public group via Manage Session → Share Outside Server.")


class PublicRecruitingSettingsView(_OwnedView):
    @discord.ui.button(label="Set Invite & Enable", emoji="🌍", style=discord.ButtonStyle.success, custom_id="dank:hub:publiclfg:enable:v1", row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Manage Server authority is required.")
        modal = PublicRecruitingInviteModal()
        modal.community_name.default = str(getattr(interaction.guild, "name", "") or "")[:60]
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Stop Recruiting", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="dank:hub:publiclfg:disable:v1", row=0)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Manage Server authority is required.")
        await _defer_update(interaction)
        try:
            # Revoke the guild gate immediately, even if old session flags remain.
            settings = await hub.update_settings(
                int(interaction.guild_id or 0), {"public_lfg_enabled": False},
                actor_id=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction, content=None,
            embed=_public_recruiting_embed(settings), view=self,
        )
        await _followup(interaction, "Public recruiting stopped. All sessions from this guild are hidden from the outside directory.")

    @discord.ui.button(label="Back to Settings", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:publiclfg:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction, content=None, embed=_settings_embed(settings, interaction.client),
            view=HubSettingsView(self.owner_id, settings),
        )


_HUB_SETTINGS_GROUPS = {
    "general": ("Maintenance", "Member Creation", "Public Recruiting"),
    "rooms": ("Auto Voice", "Auto Discussion", "Parent Category", "Archive Ended Threads"),
    "limits": ("Limits", "Lifecycle", "Abuse Limits"),
    "notifications": (
        "Notifications", "Notification Safety", "Presence Analytics",
        "Aggregate Analytics", "The100.io Discovery",
    ),
}


class HubSettingsSectionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current: str) -> None:
        self.owner_id = int(owner_id)
        super().__init__(
            placeholder="Choose Community Hub settings",
            options=[
                discord.SelectOption(label=label, value=key, default=(key == current))
                for key, label in (
                    ("general", "General"),
                    ("rooms", "Rooms & Threads"),
                    ("limits", "Session Limits"),
                    ("notifications", "Notifications & Analytics"),
                )
            ],
            custom_id="dank:hub:settings:section:v2",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "Open your own Community Hub settings.")
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_settings_embed(settings, interaction.client),
            view=HubSettingsView(self.owner_id, settings, section=self.values[0]),
        )


class HubSettingsView(_OwnedView):
    def __init__(self, owner_id: int, settings: dict[str, Any], *, section: str = "general") -> None:
        super().__init__(owner_id)
        self.settings = dict(settings)
        self.section = section if section in _HUB_SETTINGS_GROUPS else "general"
        existing = {str(getattr(item, "label", "")): item for item in self.children}
        self.clear_items()
        self.add_item(HubSettingsSectionSelect(self.owner_id, self.section))
        if self.section == "general":
            self.add_item(ModeSelect(owner_id, _safe_str(settings.get("mode"), "minimal"), section=self.section))
        labels = (*_HUB_SETTINGS_GROUPS[self.section], "Staff Dashboard")
        for index, label in enumerate(labels):
            item = existing[label]
            item.row = (2 if self.section == "general" else 1) + index // 4
            self.add_item(item)

    @discord.ui.button(label="Maintenance", emoji="🚧", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:maintenance:v1", row=1)
    async def maintenance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        new_value = not bool(self.settings.get("maintenance_mode"))
        settings = await hub.update_settings(int(interaction.guild_id or 0), {"maintenance_mode": new_value}, actor_id=int(interaction.user.id))
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Auto Voice", emoji="🎙️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:voice:v1", row=1)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"auto_create_voice": not bool(self.settings.get("auto_create_voice"))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Auto Discussion", emoji="💬", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:thread:v1", row=1)
    async def thread(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"auto_create_thread": not bool(self.settings.get("auto_create_thread"))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Presence Analytics", emoji="📡", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:presence:v1", row=1)
    async def presence(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        if not bool(getattr(getattr(interaction.client, "intents", None), "presences", False)):
            return await _followup(
                interaction,
                "Presence analytics cannot be enabled in-server until Discord's privileged Presence intent is explicitly authorized in the Developer Portal and enabled for this bot process.",
            )
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"presence_analytics_enabled": not bool(self.settings.get("presence_analytics_enabled"))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Notifications", emoji="🔔", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:notifications:v1", row=2)
    async def notifications(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"notifications_enabled": not bool(self.settings.get("notifications_enabled", True))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_settings_embed(settings, interaction.client),
            view=HubSettingsView(self.owner_id, settings, section=self.section),
        )

    @discord.ui.button(label="Limits", emoji="📏", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:limits:v1", row=2)
    async def limits(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = LimitsModal()
        modal.active.default = str(_safe_int(self.settings.get("max_active_sessions"), 20))
        modal.per_member.default = str(_safe_int(self.settings.get("max_active_sessions_per_member"), 2))
        modal.voices.default = str(_safe_int(self.settings.get("max_temporary_voice_rooms"), 12))
        modal.capacity.default = str(_safe_int(self.settings.get("max_session_capacity"), 25))
        modal.grace.default = str(_safe_int(self.settings.get("cleanup_grace_seconds"), 300))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Lifecycle", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:lifecycle:v1", row=2)
    async def lifecycle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = LifecycleModal()
        modal.minimum.default = str(_safe_int(self.settings.get("minimum_players_to_start"), 1))
        modal.ready_required.default = "yes" if bool(self.settings.get("require_ready_to_start")) else "no"
        modal.ready_timeout.default = str(_safe_int(self.settings.get("ready_timeout_seconds"), 300))
        modal.idle_minutes.default = str(max(5, _safe_int(self.settings.get("idle_timeout_seconds"), 1800) // 60))
        modal.max_hours.default = str(max(1, _safe_int(self.settings.get("max_session_lifetime_seconds"), 43200) // 3600))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Notification Safety", emoji="🔕", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:notiflimits:v1", row=2)
    async def notification_limits(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = NotificationLimitsModal()
        modal.per_hour.default = str(_safe_int(self.settings.get("max_notifications_per_hour"), 5))
        modal.cooldown.default = str(_safe_int(self.settings.get("notification_cooldown_seconds"), 300))
        modal.dispatch.default = str(_safe_int(self.settings.get("max_notification_targets_per_dispatch"), 50))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Parent Category", emoji="🗂️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:category:v1", row=2)
    async def category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🗂️ Temporary Room Category",
                description="Choose the category used for temporary Community Hub voice rooms. Existing permanent channels are not moved.",
                color=discord.Color.blurple(),
            ),
            view=CategoryPickerView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Member Creation", emoji="➕", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:membercreate:v1", row=3)
    async def member_creation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"allow_member_creation": not bool(self.settings.get("allow_member_creation", True))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Archive Ended Threads", emoji="🗄️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:archive:v1", row=3)
    async def archive_threads(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"archive_threads_on_end": not bool(self.settings.get("archive_threads_on_end", True))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Aggregate Analytics", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:analytics:v1", row=3)
    async def aggregate_analytics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"analytics_enabled": not bool(self.settings.get("analytics_enabled", True))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Abuse Limits", emoji="🚦", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:abuse:v1", row=3)
    async def abuse_limits(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = AbuseLimitsModal()
        modal.creation_cooldown.default = str(_safe_int(self.settings.get("session_creation_cooldown_seconds"), 30))
        modal.creations_hour.default = str(_safe_int(self.settings.get("max_session_creations_per_hour_per_member"), 10))
        modal.reports_hour.default = str(_safe_int(self.settings.get("max_reports_per_hour_per_member"), 5))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Public Recruiting", emoji="📣", style=discord.ButtonStyle.primary, custom_id="dank:hub:settings:publiclfg:v1", row=4)
    async def public_recruiting(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Manage Server authority is required.")
        await _defer_update(interaction)
        try:
            settings = await hub.get_settings(int(interaction.guild_id or 0))
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        await _edit_private_original(
            interaction, content=None,
            embed=_public_recruiting_embed(settings),
            view=PublicRecruitingSettingsView(self.owner_id),
        )

    @discord.ui.button(label="The100.io Discovery", emoji="🌍", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:the100:v1", row=4)
    async def the100(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "Manage Server authority is required.")
        if not bool(self.settings.get("external_the100_enabled")) and not external.the100_configured():
            return await _private(interaction, "The100.io requires a server-side API token before enabling discovery.")
        await _defer_update(interaction)
        try:
            current = await hub.get_settings(int(interaction.guild_id or 0))
            settings = await hub.update_settings(
                int(interaction.guild_id or 0),
                {"external_the100_enabled": not bool(current.get("external_the100_enabled"))},
                actor_id=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings, section=self.section))

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:back:v1", row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


class StaffSessionSelect(SessionSelect):
    def __init__(self, owner_id: int, sessions: list[dict[str, Any]]) -> None:
        super().__init__(owner_id, sessions, partner=False)
        self.custom_id = "dank:hub:staff:sessionselect:v1"

    async def callback(self, interaction: discord.Interaction) -> None:
        sid = self.values[0]
        if sid == "none":
            return
        await _defer_update(interaction)
        session = await hub.get_session(sid, guild_id=int(interaction.guild_id or 0))
        members = await hub.list_session_members(sid)
        await _edit_private_original(interaction, content=None, embed=build_session_embed(session, members), view=SessionControlView(self.owner_id, session_id=sid))


class StaffSessionsView(_OwnedView):
    def __init__(self, owner_id: int, sessions: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.add_item(StaffSessionSelect(owner_id, sessions))

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:staff:sessionsback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


def _health_embed(snapshot: dict[str, Any]) -> discord.Embed:
    queue = snapshot.get("operation_queue") if isinstance(snapshot.get("operation_queue"), dict) else {}
    totals = queue.get("totals") if isinstance(queue.get("totals"), dict) else {}
    global_row = queue.get("global") if isinstance(queue.get("global"), dict) else {}
    recovery = snapshot.get("recovery_rest") if isinstance(snapshot.get("recovery_rest"), dict) else {}
    embed = discord.Embed(
        title="🩺 Community Hub Reliability",
        description="Live process health. Discord route buckets remain owned by discord.py; this panel shows Dank Shield's application-level backpressure and recovery pacing.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Operation queue", value=_safe_str(queue.get("status"), "unknown").title(), inline=True)
    embed.add_field(name="Queued", value=str(_safe_int(totals.get("queued"))), inline=True)
    embed.add_field(name="Running", value=str(_safe_int(totals.get("running"))), inline=True)
    embed.add_field(name="Duplicates blocked", value=str(_safe_int(totals.get("duplicate_hits"))), inline=True)
    embed.add_field(name="Busy rejected", value=str(_safe_int(totals.get("busy_rejected"))), inline=True)
    embed.add_field(name="Retries", value=str(_safe_int(totals.get("retry_count"))), inline=True)
    embed.add_field(
        name="Recovery REST pacing",
        value=f"{_safe_int(recovery.get('reserved_in_window'))} / {_safe_int(recovery.get('budget_per_30s'))} reserved in current window",
        inline=False,
    )
    embed.add_field(name="Persistence", value=_safe_str(global_row.get("persistence"), "unknown"), inline=True)
    embed.add_field(name="Cleanup jobs", value=str(_safe_int(snapshot.get("cleanup_tasks"))), inline=True)
    embed.add_field(name="Notification jobs", value=str(_safe_int(snapshot.get("notification_tasks"))), inline=True)
    return embed


class CreateEventModal(discord.ui.Modal, title="Create Community Event"):
    title_input = discord.ui.TextInput(label="Event title", placeholder="Friday Minecraft Night", max_length=100)
    game = discord.ui.TextInput(label="Game or activity", placeholder="Minecraft", required=False, max_length=80)
    start = discord.ui.TextInput(label="Start time with UTC offset", placeholder="2026-09-26 20:00 -04:00", max_length=40)
    capacity = discord.ui.TextInput(label="Capacity (optional)", placeholder="32", required=False, max_length=5)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = str(self.start.value).strip()
        try:
            normalized = raw.replace(" ", "T", 1)
            start_dt = datetime.fromisoformat(normalized)
            if start_dt.tzinfo is None:
                return await _followup(interaction, "Include a UTC offset in the start time, such as -04:00 or +00:00.")
            if start_dt.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return await _followup(interaction, "Event start time must be in the future.")
        except Exception:
            return await _followup(interaction, "Use a start time like 2026-09-26 20:00 -04:00.")

        cap: Optional[int] = None
        if str(self.capacity.value or "").strip():
            try:
                cap = max(2, min(10000, int(str(self.capacity.value))))
            except Exception:
                return await _followup(interaction, "Event capacity must be a number.")

        try:
            event = await hub.create_event(
                guild_id=int(interaction.guild_id or 0),
                title=str(self.title_input.value),
                description=str(self.description.value or ""),
                game_name=str(self.game.value or ""),
                starts_at=start_dt,
                ends_at=None,
                capacity=cap,
                created_by=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        ensure_community_hub_runtime(interaction.client).schedule_event_notifications(event)
        await _followup(interaction, f"✅ Event created for {_event_time(event.get('starts_at'))}.")


def _partner_embed(links: list[dict[str, Any]], guild_id: int, bot: Any) -> discord.Embed:
    embed = discord.Embed(
        title="🌐 Community Hub Partner Network",
        description=(
            "Connect servers with a short-lived HubLink code; nobody needs to find or paste a Discord server ID. "
            "Public session discovery is enabled only after both servers consent. Aggregate activity stays off until separately enabled. "
            "Raw member presence never crosses server boundaries."
        ),
        color=discord.Color.blurple(),
    )
    if not links:
        embed.add_field(
            name="No connected servers yet",
            value="Create a **HubLink** and send the code to the other server's admin. If they do not have Dank Shield yet, send the install button too.",
            inline=False,
        )
        return embed
    for row in links[:12]:
        other_id = _safe_str(row.get("guild_b_id")) if _safe_str(row.get("guild_a_id")) == str(guild_id) else _safe_str(row.get("guild_a_id"))
        other = bot.get_guild(_safe_int(other_id))
        name = getattr(other, "name", None) or "Unavailable partner server"
        state = _safe_str(row.get("state"), "pending").title()
        requested_here = _safe_str(row.get("requested_by_guild_id")) == str(guild_id)
        suffix = " • requested here" if state == "Pending" and requested_here else ""
        embed.add_field(name=name[:256], value=f"{state}{suffix}", inline=False)
    return embed


def _hublink_share_embed(
    *,
    guild_name: str,
    code: str,
    expires_at: Any,
    install_url: str,
) -> discord.Embed:
    try:
        stamp = int(datetime.fromisoformat(_safe_str(expires_at).replace("Z", "+00:00")).timestamp())
        expires = f"<t:{stamp}:R>"
    except Exception:
        expires = "in about 15 minutes"
    embed = discord.Embed(
        title="🔗 HubLink Ready",
        description=(
            f"Connect another server to **{guild_name}** without sharing a server ID.\n\n"
            f"**HubLink code:** `{code}`\n"
            f"**Expires:** {expires}"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Send this to the other server's owner/admin",
        value=(
            "1. If Dank Shield is already there: **/dank → Community Hub → Staff Dashboard → Partner Network → Redeem HubLink**.\n"
            "2. Enter the code and confirm the server name.\n"
            "3. If Dank Shield is not installed yet, use **Add Dank Shield** below first, then redeem the same code."
        ),
        inline=False,
    )
    embed.add_field(
        name="What connecting enables",
        value=(
            "Public Community Hub group discovery: **On after confirmation**\n"
            "Aggregate/live activity sharing: **Off by default**\n"
            "The code is one-use and the database stores only its cryptographic hash."
        ),
        inline=False,
    )
    if not install_url:
        embed.add_field(
            name="Install link unavailable",
            value="Dank Shield could not build its install link from the current application identity. The HubLink code itself is still valid.",
            inline=False,
        )
    return embed


class HubLinkShareView(_OwnedView):
    def __init__(self, owner_id: int, *, install_url: str) -> None:
        super().__init__(owner_id)
        if install_url:
            self.add_item(
                discord.ui.Button(
                    label="Add Dank Shield",
                    emoji="➕",
                    style=discord.ButtonStyle.link,
                    url=install_url,
                    row=0,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "HubLink management requires server management authority.")
        return False

    @discord.ui.button(label="Check This Server", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:readiness:v1", row=1)
    async def readiness(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_hublink_readiness_embed(interaction.guild, settings),
            view=HubLinkReadinessView(self.owner_id, interaction.guild, settings),
        )

    @discord.ui.button(label="Cancel HubLink", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="dank:hub:hublink:revoke:v1", row=1)
    async def revoke(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            count = await hub.revoke_hublink_codes(
                int(interaction.guild_id or 0),
                actor_id=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerAdminView(self.owner_id, links),
        )
        await _followup(
            interaction,
            "✅ HubLink cancelled. The code can no longer be redeemed."
            if count
            else "That HubLink was already used, expired, replaced, or cancelled.",
        )

    @discord.ui.button(label="Partner Network", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:shareback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerAdminView(self.owner_id, links),
        )


class HubLinkRedeemModal(discord.ui.Modal, title="Redeem Community HubLink"):
    code = discord.ui.TextInput(
        label="HubLink code",
        placeholder="DANK-ABCD-2345",
        min_length=8,
        max_length=20,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        if int(getattr(interaction.user, "id", 0) or 0) != self.owner_id or not _staff_authorized(interaction):
            return await _followup(interaction, "❌ Redeeming a HubLink requires server management authority.")
        guild_id = int(interaction.guild_id or 0)
        try:
            preview = await hub.inspect_hublink_code(
                str(self.code.value),
                target_guild_id=guild_id,
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        source_id = _safe_int(preview.get("source_guild_id"), 0)
        source = interaction.client.get_guild(source_id)
        if source is None:
            return await _followup(
                interaction,
                "❌ Dank Shield is no longer connected to the server that created this HubLink. Ask that server to create a new code.",
            )

        code = hub.format_hublink_code(str(self.code.value))
        embed = discord.Embed(
            title="🔗 Confirm HubLink",
            description=(
                f"Connect **{interaction.guild.name if interaction.guild else 'this server'}** with **{source.name}**?\n\n"
                "This confirmation enables **public Community Hub group discovery** between the two servers. "
                "**Aggregate/live activity sharing remains off** until separately enabled."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="No server IDs",
            value="Dank Shield identified both servers from trusted Discord interactions and the one-time code.",
            inline=False,
        )
        await _edit_private_original(
            interaction,
            content=None,
            embed=embed,
            view=HubLinkConfirmView(
                self.owner_id,
                code=code,
                source_guild_id=source_id,
            ),
        )


class HubLinkConfirmView(_OwnedView):
    def __init__(self, owner_id: int, *, code: str, source_guild_id: int) -> None:
        super().__init__(owner_id)
        self.code = code
        self.source_guild_id = int(source_guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Confirming a HubLink requires server management authority.")
        return False

    @discord.ui.button(label="Connect Servers", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:hublink:confirm:v1", row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        guild_id = int(interaction.guild_id or 0)
        source = interaction.client.get_guild(self.source_guild_id)
        if source is None:
            return await _followup(interaction, "❌ The source server is no longer available to Dank Shield. Ask them for a new HubLink.")

        try:
            preview = await hub.inspect_hublink_code(self.code, target_guild_id=guild_id)
            if _safe_int(preview.get("source_guild_id"), 0) != self.source_guild_id:
                return await _followup(interaction, "❌ That HubLink no longer points to the server you reviewed.")
            result = await hub.redeem_hublink_code(
                self.code,
                target_guild_id=guild_id,
                actor_id=int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        runtime = ensure_community_hub_runtime(interaction.client)
        runtime.invalidate_settings(guild_id)
        runtime.invalidate_settings(self.source_guild_id)
        settings = await hub.get_settings(guild_id)

        creator_id = _safe_int(preview.get("created_by_user_id"), 0)
        creator = source.get_member(creator_id) if creator_id > 0 else None
        if creator is not None:
            try:
                await creator.send(
                    (
                        f"🔗 Community HubLink connected **{source.name}** with "
                        f"**{interaction.guild.name if interaction.guild else 'another server'}**. "
                        "Public group discovery is on; aggregate/live activity sharing remains off."
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        await _edit_private_original(
            interaction,
            content=None,
            embed=_hublink_readiness_embed(
                interaction.guild,
                settings,
                connected_name=source.name,
            ),
            view=HubLinkReadinessView(
                self.owner_id,
                interaction.guild,
                settings,
                connected_name=source.name,
            ),
        )
        await _followup(
            interaction,
            "✅ HubLink connected. No server IDs were needed. "
            "Aggregate/live activity sharing remains off until separately enabled."
            + (" This was a safe replay of the same completed HubLink." if bool(result.get("replayed")) else ""),
        )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:cancel:v1", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerAdminView(self.owner_id, links),
        )


class HubLinkReadinessView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        guild: Optional[discord.Guild],
        settings: dict[str, Any],
        *,
        connected_name: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.connected_name = connected_name
        report = _community_hub_readiness(guild, settings)
        repair_url = _safe_str(report.get("reauthorize_url"))
        if repair_url:
            self.add_item(
                discord.ui.Button(
                    label="Reauthorize Dank Shield",
                    emoji="🔐",
                    style=discord.ButtonStyle.link,
                    url=repair_url,
                    row=0,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Community Hub setup checks require server management authority.")
        return False

    @discord.ui.button(label="Check Again", emoji="🔄", style=discord.ButtonStyle.primary, custom_id="dank:hub:hublink:checkagain:v1", row=1)
    async def check_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_hublink_readiness_embed(interaction.guild, settings, connected_name=self.connected_name),
            view=HubLinkReadinessView(
                self.owner_id,
                interaction.guild,
                settings,
                connected_name=self.connected_name,
            ),
        )

    @discord.ui.button(label="Open Access Repair", emoji="🧰", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:accessrepair:v1", row=1)
    async def access_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify import setup_permission_repair_services

        await setup_permission_repair_services.open_permission_repair(
            interaction,
            parent="security",
            include_activity_coverage=False,
        )

    @discord.ui.button(label="Partner Network", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:readyback:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerAdminView(self.owner_id, links),
        )


class PendingPartnerSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild_id: int, links: list[dict[str, Any]], bot: Any) -> None:
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)
        pending = [
            row for row in links
            if row.get("state") == "pending" and _safe_str(row.get("requested_by_guild_id")) != str(guild_id)
        ]
        self.links = {_safe_str(row.get("id")): row for row in pending if row.get("id")}
        options = []
        for row in pending[:25]:
            other_id = _safe_str(row.get("guild_b_id")) if _safe_str(row.get("guild_a_id")) == str(guild_id) else _safe_str(row.get("guild_a_id"))
            guild = bot.get_guild(_safe_int(other_id))
            options.append(
                discord.SelectOption(
                    label=(getattr(guild, "name", None) or "Unavailable partner server")[:100],
                    value=_safe_str(row.get("id")),
                    description="Review this incoming partner request",
                )
            )
        super().__init__(
            placeholder="Review incoming partner request",
            options=options or [discord.SelectOption(label="No incoming requests", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:partner:pendingselect:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        link_id = self.values[0]
        if link_id == "none":
            return
        await _defer_update(interaction)
        link = self.links.get(link_id)
        if not link:
            return await _followup(interaction, "That partner request is no longer available.")
        other_id = (
            _safe_str(link.get("guild_b_id"))
            if _safe_str(link.get("guild_a_id")) == str(self.guild_id)
            else _safe_str(link.get("guild_a_id"))
        )
        other = interaction.client.get_guild(_safe_int(other_id))
        embed = discord.Embed(
            title="🌐 Review Partner Request",
            description=(
                f"Request from **{getattr(other, 'name', None) or 'Unavailable partner server'}**.\n\n"
                "Approving allows public Community Hub session discovery and aggregate activity sharing. "
                "It never shares raw member presence."
            ),
            color=discord.Color.blurple(),
        )
        await _edit_private_original(
            interaction,
            content=None,
            embed=embed,
            view=PendingPartnerDecisionView(self.owner_id, link_id=link_id),
        )


class PendingPartnerDecisionView(_OwnedView):
    def __init__(self, owner_id: int, *, link_id: str) -> None:
        super().__init__(owner_id)
        self.link_id = link_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Partner management requires server management authority.")
        return False

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:partner:approve:v1", row=0)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        if not bool(settings.get("partner_discovery_enabled")):
            return await _followup(interaction, "Enable Partner Discovery before approving partner links.")
        await hub.update_partner_link(
            self.link_id,
            state="active",
            aggregate_activity_shared=True,
            session_discovery_shared=True,
        )
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client),
        )
        await _followup(interaction, "✅ Partner link approved. Only public session summaries and opted-in aggregates are shared.")

    @discord.ui.button(label="Reject", emoji="🚫", style=discord.ButtonStyle.danger, custom_id="dank:hub:partner:reject:v1", row=0)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await hub.update_partner_link(
            self.link_id,
            state="rejected",
            aggregate_activity_shared=False,
            session_discovery_shared=False,
        )
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client),
        )
        await _followup(interaction, "Partner request rejected.")

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:decisionback:v1", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client),
        )


class ActivePartnerSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild_id: int, links: list[dict[str, Any]], bot: Any) -> None:
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)
        active = [row for row in links if row.get("state") == "active"]
        self.links = {_safe_str(row.get("id")): row for row in active if row.get("id")}
        options = []
        for row in active[:25]:
            other_id = _safe_str(row.get("guild_b_id")) if _safe_str(row.get("guild_a_id")) == str(guild_id) else _safe_str(row.get("guild_a_id"))
            guild = bot.get_guild(_safe_int(other_id))
            options.append(
                discord.SelectOption(
                    label=(getattr(guild, "name", None) or "Unavailable partner server")[:100],
                    value=_safe_str(row.get("id")),
                    description="Manage this active partner link",
                    emoji="🌐",
                )
            )
        super().__init__(
            placeholder="Manage active partner link",
            options=options or [discord.SelectOption(label="No active partners", value="none")],
            disabled=not bool(options),
            custom_id="dank:hub:partner:activeselect:v1",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        link_id = self.values[0]
        if link_id == "none":
            return
        await _defer_update(interaction)
        link = self.links.get(link_id)
        if not link:
            return await _followup(interaction, "That partner link is no longer available.")
        other_id = (
            _safe_str(link.get("guild_b_id"))
            if _safe_str(link.get("guild_a_id")) == str(self.guild_id)
            else _safe_str(link.get("guild_a_id"))
        )
        other = interaction.client.get_guild(_safe_int(other_id))
        embed = discord.Embed(
            title="🌐 Active Partner",
            description=(
                f"**{getattr(other, 'name', None) or 'Unavailable partner server'}** is connected to this Community Hub.\n\n"
                "Revoking stops future cross-server Community Hub discovery. It does not delete either server's own sessions."
            ),
            color=discord.Color.blurple(),
        )
        await _edit_private_original(
            interaction,
            content=None,
            embed=embed,
            view=ActivePartnerDecisionView(self.owner_id, link_id=link_id),
        )


class ActivePartnerDecisionView(_OwnedView):
    def __init__(self, owner_id: int, *, link_id: str) -> None:
        super().__init__(owner_id)
        self.link_id = link_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Partner management requires server management authority.")
        return False

    @discord.ui.button(label="Revoke Partner Link", emoji="🔌", style=discord.ButtonStyle.danger, custom_id="dank:hub:partner:revoke:v1", row=0)
    async def revoke(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await hub.update_partner_link(
            self.link_id,
            state="revoked",
            aggregate_activity_shared=False,
            session_discovery_shared=False,
        )
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client),
        )
        await _followup(interaction, "✅ Partner link revoked.")

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:activeback:v1", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client),
        )


class PartnerAdminView(_OwnedView):
    def __init__(self, owner_id: int, links: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.links = list(links)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Partner management requires server management authority.")
        return False

    @discord.ui.button(label="Create HubLink", emoji="🔗", style=discord.ButtonStyle.primary, custom_id="dank:hub:hublink:create:v1", row=1)
    async def create_hublink(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "HubLink must be created inside a server.")
        try:
            record = await hub.create_hublink_code(
                int(guild.id),
                int(interaction.user.id),
            )
        except hub.CommunityHubError as exc:
            return await _followup(interaction, f"❌ {_error_text(exc)}")

        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(guild.id))
        install_url = _public_install_url(interaction.client)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_hublink_share_embed(
                guild_name=guild.name,
                code=_safe_str(record.get("code")),
                expires_at=record.get("expires_at"),
                install_url=install_url,
            ),
            view=HubLinkShareView(self.owner_id, install_url=install_url),
        )

    @discord.ui.button(label="Redeem HubLink", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank:hub:hublink:redeem:v1", row=1)
    async def redeem_hublink(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(HubLinkRedeemModal(self.owner_id))

    @discord.ui.button(label="Check Setup", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:hublink:setup:v1", row=1)
    async def check_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_hublink_readiness_embed(interaction.guild, settings),
            view=HubLinkReadinessView(self.owner_id, interaction.guild, settings),
        )

    @discord.ui.button(label="Discovery On / Off", emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:enable:v1", row=1)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        settings = await hub.get_settings(int(interaction.guild_id or 0))
        updated = await hub.update_settings(
            int(interaction.guild_id or 0),
            {"partner_discovery_enabled": not bool(settings.get("partner_discovery_enabled"))},
            actor_id=int(interaction.user.id),
        )
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(
            interaction,
            content=None,
            embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client),
            view=PartnerAdminView(self.owner_id, links),
        )
        await _followup(interaction, f"Partner discovery is now {'enabled' if updated.get('partner_discovery_enabled') else 'disabled'}.")

    @discord.ui.button(label="Review Links", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:refresh:v1", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        view = PartnerApprovalView(self.owner_id, int(interaction.guild_id or 0), links, interaction.client)
        await _edit_private_original(interaction, content=None, embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client), view=view)

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:back:v1", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


class PartnerApprovalView(_OwnedView):
    def __init__(self, owner_id: int, guild_id: int, links: list[dict[str, Any]], bot: Any) -> None:
        super().__init__(owner_id)
        self.add_item(PendingPartnerSelect(owner_id, guild_id, links, bot))
        self.add_item(ActivePartnerSelect(owner_id, guild_id, links, bot))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if _staff_authorized(interaction):
            return True
        await _private(interaction, "Partner management requires server management authority.")
        return False

    @discord.ui.button(label="Partner Network", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:partner:approvalback:v1", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        links = await hub.list_partner_links(int(interaction.guild_id or 0), active_only=False)
        await _edit_private_original(interaction, content=None, embed=_partner_embed(links, int(interaction.guild_id or 0), interaction.client), view=PartnerAdminView(self.owner_id, links))


__all__ = [
    "CommunityHubView",
    "CommunitySessionPublicView",
    "build_session_embed",
    "open_community_hub",
]
