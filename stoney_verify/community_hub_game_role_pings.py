from __future__ import annotations

"""Manual Community Hub game-role recruitment, isolated from /toke and CHEERS.

The per-guild mapping and *send-before* cooldown reservations share one atomic
guild-config compare-and-swap value. A failed/uncertain reservation never sends
a Discord mention. This module does not listen to gameplay, create roles,
auto-ping at session creation, or convert external groups to native sessions.
"""

import time
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

import discord

from . import community_hub_service as hub
from .community_lfg_discovery import game_search_key
from .community_pings_service import (
    CAP_MOVIE_NIGHT_NOTIFY,
    CAP_TOKE_NOTIFY,
    parse_community_pings,
)
from .guild_config import compare_and_swap_guild_config_key, get_guild_config

CONFIG_KEY = "community_hub_game_role_pings_v1"
MAX_GAMES = 12
DEFAULT_COOLDOWN_MINUTES = 30
MIN_COOLDOWN_MINUTES = 15
MAX_COOLDOWN_MINUTES = 240
GUILD_COOLDOWN_SECONDS = 300
MAX_GUILD_CALLS_PER_HOUR = 4
ALLOW_SESSION_STATES = frozenset({"open", "forming", "ready", "active"})
_MAX_ATTEMPTS = 3


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def notification_game_key(value: str) -> str:
    """Use the same known game aliases as Find Players for role mapping."""
    normalized = game_search_key(value)
    if normalized:
        return normalized
    # Unlike the historical ASCII-only fallback, different non-Latin game
    # names must not collapse to a shared "game" role or ping each other.
    unicode_name = unicodedata.normalize("NFKC", str(value)).casefold()
    safe = re.sub(r"[^\\w]+", "-", unicode_name, flags=re.UNICODE).strip("-")[:80]
    if not safe:
        raise ValueError("Game name has no searchable characters.")
    return safe


def _as_int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _model(value: Any) -> dict[str, Any]:
    blob = _as_dict(value)
    raw_mapping = _as_dict(blob.get("games"))
    games = {
        str(key): dict(item)
        for key, item in list(raw_mapping.items())[:MAX_GAMES]
        if isinstance(key, str) and isinstance(item, Mapping)
    }
    history = [
        int(item) for item in (blob.get("recent_calls") or [])[:24]
        if isinstance(item, (int, float)) and 0 < item
    ] if isinstance(blob.get("recent_calls"), list) else []
    called = blob.get("called_sessions")
    # Cross-reconfiguration duplicate guard. At four calls per hour, 160
    # retained IDs cover at least forty hours of reservations, longer than
    # a Community Hub session's configurable hard lifetime.
    session_ids = [
        str(item)[:64] for item in (called if isinstance(called, list) else [])[-160:]
        if isinstance(item, str) and 0 < len(item) <= 64
    ]
    raw_game_claims = _as_dict(blob.get("last_game_calls"))
    last_game_calls = {
        key: _as_int(stamp)
        for key, stamp in list(raw_game_claims.items())[-24:]
        if isinstance(key, str) and len(key) <= 100 and _as_int(stamp) > 0
    }
    return {
        "version": 1, "games": games, "recent_calls": history,
        "called_sessions": list(dict.fromkeys(session_ids)),
        "last_game_calls": last_game_calls,
    }


def eligible_game_roles(guild: discord.Guild, config: Mapping[str, Any]) -> list[tuple[int, str]]:
    """Only explicitly-enabled, non-/toke Community & Pings notification roles."""
    model = parse_community_pings(config)
    if model.source != "v2":
        return []
    cheers = _as_dict(config.get("cheers_lobby_v1"))
    cheers_id = _as_int(cheers.get("role_id"))
    results: list[tuple[int, str]] = []
    ids: set[int] = set()
    for option in model.options:
        rid = int(option.role_id)
        if (
            not option.enabled or option.kind != "notification"
            or rid in ids or rid == cheers_id
            or CAP_TOKE_NOTIFY in option.capabilities
            or CAP_MOVIE_NIGHT_NOTIFY in option.capabilities
        ):
            continue
        role = guild.get_role(rid)
        if (
            not isinstance(role, discord.Role)
            or role.is_default() or role.managed
            or role.guild.id != guild.id
        ):
            continue
        results.append((rid, option.label[:90]))
        ids.add(rid)
    return results[:25]


async def current(guild_id: int) -> dict[str, Any]:
    config = await get_guild_config(int(guild_id), refresh=True)
    return _model(config.get(CONFIG_KEY))


async def configure(
    guild: discord.Guild, game_name: str, role_id: int, *, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    remove: bool = False,
) -> bool:
    """CAS guild-scoped setup to avoid overwriting another staff editor."""
    game = hub.normalize_game_name(game_name)
    key = notification_game_key(game)
    cooldown = int(cooldown_minutes)
    if not MIN_COOLDOWN_MINUTES <= cooldown <= MAX_COOLDOWN_MINUTES:
        raise ValueError("Game call cooldown must be between 15 and 240 minutes.")

    for _ in range(_MAX_ATTEMPTS):
        raw_config = await get_guild_config(guild.id, refresh=True)
        eligible_ids = {rid for rid, _ in eligible_game_roles(guild, raw_config)}
        if not remove and int(role_id) not in eligible_ids:
            raise ValueError("Choose an enabled Notification role in Community & Pings, separate from /toke and CHEERS.")
        before = raw_config.get(CONFIG_KEY)
        state = _model(before)
        games = state["games"]
        if remove:
            games.pop(key, None)
        else:
            if key not in games and len(games) >= MAX_GAMES:
                raise ValueError("This server already has 12 game-role mappings.")
            # Retain the last-session claim on reconfiguration, so staff cannot
            # bypass the one-call-per-session budget by remapping a role.
            previous = _as_dict(games.get(key))
            games[key] = {
                "game_name": game,
                "role_id": str(int(role_id)),
                "cooldown_minutes": cooldown,
                "last_at": max(_as_int(previous.get("last_at")), state["last_game_calls"].get(key, 0)),
                "last_session_id": str(previous.get("last_session_id") or "")[:64],
            }
        try:
            applied, _ = await compare_and_swap_guild_config_key(
                guild.id, CONFIG_KEY, expected=before, value=state,
                source="community_hub_game_role_setup",
            )
        except Exception:
            return False
        if applied:
            return True
    return False


async def call_game_crew(guild: discord.Guild, session_id: str) -> str:
    """Explicit-only role mention with click-time permissions and atomic claim."""
    gid = int(guild.id)
    try:
        session = await hub.get_session(session_id, guild_id=gid)
        settings = await hub.get_settings(gid)
    except hub.CommunityHubError:
        return "Could not verify this group's current settings. No ping sent."
    if (
        not bool(settings.get("enabled", True))
        or bool(settings.get("maintenance_mode"))
        or not bool(settings.get("notifications_enabled", True))
    ):
        return "Community Hub notifications are disabled or temporarily unavailable."
    if (
        str(session.get("state")) not in ALLOW_SESSION_STATES
        or str(session.get("privacy")) != "public"
    ):
        return "Only current public, joinable gaming groups can call a game role."

    game = hub.normalize_game_name(session.get("game_name"))
    key = notification_game_key(game)
    channel_id = _as_int(session.get("panel_channel_id"))
    message_id = _as_int(session.get("panel_message_id"))
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel) or message_id <= 0:
        return "This group has no current public text-channel card. No ping sent."
    me = guild.me
    if not isinstance(me, discord.Member):
        return "Cannot verify Dank Shield's Discord permissions. No ping sent."

    for _ in range(_MAX_ATTEMPTS):
        try:
            raw_config = await get_guild_config(gid, refresh=True)
        except Exception:
            return "Role mapping storage is unavailable. No ping sent."
        before = raw_config.get(CONFIG_KEY)
        state = _model(before)
        mapping = _as_dict(state["games"].get(key))
        rid = _as_int(mapping.get("role_id"))
        if str(session_id) in state["called_sessions"]:
            return "This group has already called its game crew. Start a new group for another call."
        if rid <= 0:
            return f"No game notification role is configured for {game}. Ask server staff."
        if rid not in {rid for rid, _ in eligible_game_roles(guild, raw_config)}:
            return "This game role is no longer an enabled Community & Pings notification. No ping sent."
        role = guild.get_role(rid)
        if not isinstance(role, discord.Role):
            return "The configured notification role no longer exists."

        perms = channel.permissions_for(me)
        role_permissions = channel.permissions_for(role)
        if not (
            perms.view_channel and perms.send_messages and perms.embed_links
            and role_permissions.view_channel
            and (role.mentionable or perms.mention_everyone)
        ):
            return "Role or channel mention permissions are missing. No ping sent."

        now = int(time.time())
        cooldown = max(MIN_COOLDOWN_MINUTES, min(MAX_COOLDOWN_MINUTES, _as_int(mapping.get("cooldown_minutes")) or DEFAULT_COOLDOWN_MINUTES)) * 60
        if mapping.get("last_session_id") == str(session_id):
            return "This group has already called its game crew. Start a new group for another call."
        elapsed = now - max(_as_int(mapping.get("last_at")), state["last_game_calls"].get(key, 0))
        if elapsed < cooldown:
            return f"That game's crew can be called again in {(cooldown - elapsed + 59) // 60} minute(s)."
        history = [stamp for stamp in state["recent_calls"] if 0 <= now - stamp < 3600]
        if len(history) >= MAX_GUILD_CALLS_PER_HOUR:
            return "This server has reached its game-role call limit (4 per hour)."
        if history and now - history[-1] < GUILD_COOLDOWN_SECONDS:
            return f"Another game role was called recently. Try in {(GUILD_COOLDOWN_SECONDS - now + history[-1] + 59) // 60} minute(s)."

        state["games"][key] = {**mapping, "last_at": now, "last_session_id": str(session_id)[:64]}
        # Unlike an editable game mapping, claims cannot be refunded by staff
        # removing/re-adding the role before its game cooldown expires.
        state["last_game_calls"] = dict(
            sorted(
                {**state["last_game_calls"], key: now}.items(),
                key=lambda entry: entry[1],
            )[-24:]
        )
        state["recent_calls"] = [*history, now][-24:]
        state["called_sessions"] = [*state["called_sessions"], str(session_id)[:64]][-160:]
        try:
            claimed, _ = await compare_and_swap_guild_config_key(
                gid, CONFIG_KEY, expected=before, value=state,
                source="community_hub_game_role_call",
            )
        except Exception:
            return "Durable cooldown storage is unavailable. No ping sent."
        if not claimed:
            continue

        # Once claimed, never refund an uncertain Discord send. This prevents
        # double pings during network errors, retries or shard contention.
        try:
            refreshed = await hub.get_session(session_id, guild_id=gid)
            latest = await get_guild_config(gid, refresh=True)
            latest_settings = await hub.get_settings(gid)
        except Exception:
            return "Call reserved, but the group's current state could not be verified. No ping sent."
        # Honor a staff disable or revoked role permission even after winning
        # the durable claim. Sending after an opt-out would be worse than
        # consuming a cooldown with no Discord message.
        current_perms = channel.permissions_for(me)
        current_role_perms = channel.permissions_for(role)
        if (
            str(refreshed.get("state")) not in ALLOW_SESSION_STATES
            or str(refreshed.get("privacy")) != "public"
            or _as_int(refreshed.get("panel_message_id")) != message_id
            or _as_int(refreshed.get("panel_channel_id")) != channel_id
            or _as_int(_as_dict(_model(latest.get(CONFIG_KEY))["games"].get(key)).get("role_id")) != rid
            or rid not in {rid for rid, _ in eligible_game_roles(guild, latest)}
            or not bool(latest_settings.get("enabled", True))
            or bool(latest_settings.get("maintenance_mode"))
            or not bool(latest_settings.get("notifications_enabled", True))
            or not (
                current_perms.view_channel and current_perms.send_messages
                and current_perms.embed_links and current_role_perms.view_channel
                and (role.mentionable or current_perms.mention_everyone)
            )
        ):
            return "The group, role, permissions or notification setting changed while processing the call. No ping sent."

        jump = f"https://discord.com/channels/{gid}/{channel_id}/{message_id}"
        embed = discord.Embed(
            title="🎮 Game Crew Wanted",
            description=f"**{discord.utils.escape_markdown(game)}** needs players.\n[Open the current Community Hub group]({jump})",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Manual Community Hub game call • separate from /toke and CHEERS")
        try:
            sent = await channel.send(
                content=role.mention, embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=False, everyone=False, roles=[role], replied_user=False,
                ),
            )
        except discord.HTTPException:
            return "Could not confirm the Discord announcement. Its cooldown remains reserved to prevent duplicate pings."
        return f"Called {role.mention} for {game}: {sent.jump_url}"
    return "Game role settings changed during the call. No ping was sent. Try again."
