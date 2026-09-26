from __future__ import annotations

"""Mobile-first Dank Shield Community Hub.

The public slash-command tree stays intentionally small. Community Hub is opened
from the main Dank Shield home panel and then uses buttons, selects, and modals.
All slow mutations acknowledge Discord first, re-authorize on the server side,
and run through the existing bot-wide operation queue.
"""

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import discord

from .. import community_hub_service as hub
from ..community_hub_runtime import ensure_community_hub_runtime
from ..interaction_guard import safe_defer_interaction
from ..operation_queue import run_exclusive
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS, private_menu_lifecycle_text
from ..permission_repair_core import approved_public_permissions, reauthorize_url
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
    "move_members": "Move Members",
}


def _public_install_url(bot: Any) -> str:
    client_id = _safe_int(
        getattr(bot, "application_id", 0)
        or getattr(getattr(bot, "user", None), "id", 0),
        0,
    )
    if client_id <= 0:
        return ""
    try:
        return discord.utils.oauth_url(
            client_id,
            permissions=approved_public_permissions(),
            scopes=("bot", "applications.commands"),
        )
    except Exception:
        return ""


def _community_hub_required_permissions(settings: dict[str, Any]) -> tuple[str, ...]:
    names = [
        "view_channel",
        "send_messages",
        "embed_links",
        "read_message_history",
    ]
    if bool(settings.get("auto_create_thread", True)):
        names.extend(("send_messages_in_threads", "manage_threads"))
    if bool(settings.get("auto_create_voice", True)):
        names.extend(("manage_channels", "move_members"))
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
            "reauthorize_url": "",
        }

    me = getattr(guild, "me", None)
    if me is None:
        return {
            "healthy": False,
            "guild_missing": ["Dank Shield could not resolve its server member."],
            "channel_missing": [],
            "channel": None,
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
                channel_required = (
                    "view_channel",
                    "send_messages",
                    "embed_links",
                    "read_message_history",
                )
                channel_missing = [
                    name
                    for name in channel_required
                    if not bool(getattr(effective, "administrator", False))
                    and not bool(getattr(effective, name, False))
                ]
            except Exception:
                channel_missing = ["channel_permission_check"]

    return {
        "healthy": not missing and not channel_missing,
        "guild_missing": missing,
        "channel_missing": channel_missing,
        "channel": channel,
        "reauthorize_url": reauthorize_url(guild) if missing else "",
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
                "Tap **Reauthorize Dank Shield** below, choose this server, review the requested permissions, and authorize. "
                "The normal repair link does **not** request Administrator."
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
    embed.set_footer(text=f"Dank Shield Community Hub • Session {sid[:8]} • v{version}")
    return embed


def _hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 Community Hub",
        description=(
            "Find people to play with, start a gaming session, join community events, "
            "and manage your own game notifications. No mystery acronyms required."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Find Players",
        value="Browse active groups in this server and, when staff enables it, approved partner servers.",
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
        name="Privacy",
        value=(
            "Community Pulse uses aggregate activity. Dank Shield does not keep a long-term per-member presence or game-history dossier."
        ),
        inline=False,
    )
    embed.add_field(name="Control lifetime", value=private_menu_lifecycle_text(), inline=False)
    return embed


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
        try:
            sessions = await hub.list_active_sessions(int(interaction.guild_id or 0), limit=25)
            availability = await hub.availability_summary(int(interaction.guild_id or 0), limit=1000)
        except hub.CommunityHubError as exc:
            return await _edit_private_original(
                interaction,
                content=f"❌ {_error_text(exc)}",
                embed=None,
                view=CommunityHubView(self.owner_id),
            )
        await _edit_private_original(
            interaction,
            content=None,
            embed=_find_players_embed(sessions, partner=False, availability_summary=availability),
            view=FindPlayersView(self.owner_id, sessions, partner=False, availability_summary=availability),
        )

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
        try:
            sessions = await hub.list_partner_discovery_sessions(int(interaction.guild_id or 0), limit=25)
            activity = await ensure_community_hub_runtime(interaction.client).partner_activity_snapshots(
                int(interaction.guild_id or 0),
                limit=10,
            )
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_find_players_embed(sessions, partner=True, partner_activity=activity),
            view=FindPlayersView(self.owner_id, sessions, partner=True),
        )

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
            name="No open groups right now",
            value=(
                "Quick Match can form a new group when another member has explicitly enabled Quick Match "
                "for the same game. You can also start a session yourself."
                if not partner
                else "No public groups are currently shared by approved partner servers."
            ),
            inline=False,
        )
        return embed

    for session in sessions[:8]:
        game = _safe_str(session.get("game_name"), "Game")
        state = _STATE_LABELS.get(_safe_str(session.get("state")), _safe_str(session.get("state"), "Open").title())
        cap = _safe_int(session.get("capacity"), 6)
        source = f" • Server {session.get('guild_id')}" if partner else ""
        embed.add_field(name=game[:256], value=f"{state} • up to {cap} players{source}", inline=False)
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
        session = self.sessions.get(sid)
        if session is None:
            try:
                session = await hub.get_session(sid)
            except hub.CommunityHubError as exc:
                return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        if self.partner and int(_safe_int(session.get("guild_id"), 0)) != int(interaction.guild_id or 0):
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


class FindPlayersView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        sessions: list[dict[str, Any]],
        *,
        partner: bool,
        availability_summary: Optional[dict[str, Any]] = None,
        selected_game: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.sessions = list(sessions)
        self.partner = bool(partner)
        self.availability_summary = dict(availability_summary or {})
        self.selected_game = _safe_str(selected_game)
        self.add_item(SessionSelect(owner_id, sessions, partner=partner))
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
        try:
            sessions = (
                await hub.list_partner_discovery_sessions(int(interaction.guild_id or 0), limit=25)
                if self.partner
                else await hub.list_active_sessions(int(interaction.guild_id or 0), limit=25)
            )
            activity = (
                await ensure_community_hub_runtime(interaction.client).partner_activity_snapshots(
                    int(interaction.guild_id or 0),
                    limit=10,
                )
                if self.partner
                else []
            )
            availability = (
                {}
                if self.partner
                else await hub.availability_summary(int(interaction.guild_id or 0), limit=1000)
            )
        except hub.CommunityHubError as exc:
            return await _edit_private_original(interaction, content=f"❌ {_error_text(exc)}", embed=None, view=CommunityHubView(self.owner_id))
        await _edit_private_original(
            interaction,
            content=None,
            embed=_find_players_embed(
                sessions,
                partner=self.partner,
                partner_activity=activity,
                availability_summary=availability,
            ),
            view=FindPlayersView(
                self.owner_id,
                sessions,
                partner=self.partner,
                availability_summary=availability,
            ),
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

    @discord.ui.button(label="Community Hub", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank:hub:find:back:v1", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(embed=_hub_embed(), view=CommunityHubView(self.owner_id), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="dank:hub:find:close:v1", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_private_panel(interaction)

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


class StaffDashboardView(_OwnedView):
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
    embed = discord.Embed(
        title="⚙️ Community Hub Settings",
        description=(
            "Minimal uses public session cards only. Smart adds managed temporary discussion and voice spaces. "
            "Managed is the full Community Hub infrastructure mode."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Mode", value=_safe_str(settings.get("mode"), "minimal").title(), inline=True)
    embed.add_field(name="Maintenance", value="On" if bool(settings.get("maintenance_mode")) else "Off", inline=True)
    embed.add_field(name="Member-created sessions", value="On" if bool(settings.get("allow_member_creation", True)) else "Off", inline=True)
    embed.add_field(name="Aggregate analytics", value="On" if bool(settings.get("analytics_enabled", True)) else "Off", inline=True)
    embed.add_field(
        name="Presence analytics",
        value=(
            ("On" if bool(settings.get("presence_analytics_enabled")) else "Off")
            if presence_runtime
            else "Unavailable until privileged Presence intent is explicitly authorized and enabled"
        ),
        inline=False,
    )
    embed.add_field(
        name="Limits",
        value=(
            f"Active sessions: {_safe_int(settings.get('max_active_sessions'), 20)}\n"
            f"Per member: {_safe_int(settings.get('max_active_sessions_per_member'), 2)}\n"
            f"Temporary voice rooms: {_safe_int(settings.get('max_temporary_voice_rooms'), 12)}\n"
            f"Max group size: {_safe_int(settings.get('max_session_capacity'), 25)}\n"
            f"Minimum to start: {_safe_int(settings.get('minimum_players_to_start'), 1)} • "
            f"Ready required: {'Yes' if bool(settings.get('require_ready_to_start')) else 'No'} • "
            f"Ready expires: {_safe_int(settings.get('ready_timeout_seconds'), 300)}s"
        ),
        inline=False,
    )
    embed.add_field(
        name="Abuse controls",
        value=(
            f"Create cooldown: {_safe_int(settings.get('session_creation_cooldown_seconds'), 30)}s • "
            f"max {_safe_int(settings.get('max_session_creations_per_hour_per_member'), 10)} sessions/member/hour • "
            f"max {_safe_int(settings.get('max_reports_per_hour_per_member'), 5)} reports/member/hour"
        ),
        inline=False,
    )
    embed.add_field(
        name="Notifications",
        value=(
            f"{'On' if bool(settings.get('notifications_enabled', True)) else 'Off'} • "
            f"max {_safe_int(settings.get('max_notifications_per_hour'), 5)}/member/hour • "
            f"dispatch cap {_safe_int(settings.get('max_notification_targets_per_dispatch'), 50)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Cleanup",
        value=(
            f"Grace: {_safe_int(settings.get('cleanup_grace_seconds'), 300)}s • "
            f"Idle: {_safe_int(settings.get('idle_timeout_seconds'), 1800)}s • "
            f"Hard lifetime: {_safe_int(settings.get('max_session_lifetime_seconds'), 43200)}s • "
            f"Ended discussions: {'Archive' if bool(settings.get('archive_threads_on_end', True)) else 'Delete'}"
        ),
        inline=False,
    )
    return embed


class ModeSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current: str) -> None:
        self.owner_id = int(owner_id)
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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))


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


class HubSettingsView(_OwnedView):
    def __init__(self, owner_id: int, settings: dict[str, Any]) -> None:
        super().__init__(owner_id)
        self.settings = dict(settings)
        self.add_item(ModeSelect(owner_id, _safe_str(settings.get("mode"), "minimal")))

    @discord.ui.button(label="Maintenance", emoji="🚧", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:maintenance:v1", row=1)
    async def maintenance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer_update(interaction)
        new_value = not bool(self.settings.get("maintenance_mode"))
        settings = await hub.update_settings(int(interaction.guild_id or 0), {"maintenance_mode": new_value}, actor_id=int(interaction.user.id))
        ensure_community_hub_runtime(interaction.client).invalidate_settings(int(interaction.guild_id or 0))
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
            view=HubSettingsView(self.owner_id, settings),
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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

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
        await _edit_private_original(interaction, content=None, embed=_settings_embed(settings, interaction.client), view=HubSettingsView(self.owner_id, settings))

    @discord.ui.button(label="Abuse Limits", emoji="🚦", style=discord.ButtonStyle.secondary, custom_id="dank:hub:settings:abuse:v1", row=3)
    async def abuse_limits(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        modal = AbuseLimitsModal()
        modal.creation_cooldown.default = str(_safe_int(self.settings.get("session_creation_cooldown_seconds"), 30))
        modal.creations_hour.default = str(_safe_int(self.settings.get("max_session_creations_per_hour_per_member"), 10))
        modal.reports_hour.default = str(_safe_int(self.settings.get("max_reports_per_hour_per_member"), 5))
        await interaction.response.send_modal(modal)

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
            "Public session discovery is enabled only after both servers consent. Aggregate activity stays off until separately enabled."
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
            "✅ HubLink connected. No server IDs were needed."
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
                f"Request from **{getattr(other, 'name', None) or f'Server {other_id}'}**.\n\n"
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
                f"**{getattr(other, 'name', None) or f'Server {other_id}'}** is connected to this Community Hub.\n\n"
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
