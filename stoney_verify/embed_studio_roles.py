"""Persistent, DB-authorized self-role actions for bot-owned Studio posts.

One semantic on_interaction listener routes all guilds and published posts.
No per-message cached View registrations, no global startup scans, and no
privileged or verification/access/staff role self-assignment.
"""
from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Mapping

import discord

from .embed_studio_core import StudioValidationError, validate_project

ROLE_PREFIX = "dank:es:role:v1:"
_ATTACHED_BOT_IDS: set[int] = set()
# Bounded per-member cooldown so rapidly repeated clicks cannot exhaust
# Discord REST or Supabase on busy multi-guild deployments.
_ROLE_LAST_CLICK: dict[tuple[int, int], float] = {}
_ROLE_COOLDOWN_SECONDS = 2.5
_ROLE_COOLDOWN_MAX_KEYS = 4096


def _rate_limited(guild_id: int, member_id: int, *, now: float | None = None) -> bool:
    stamp = monotonic() if now is None else now
    key = (int(guild_id), int(member_id))
    previous = _ROLE_LAST_CLICK.get(key)
    if previous is not None and stamp - previous < _ROLE_COOLDOWN_SECONDS:
        return True
    if len(_ROLE_LAST_CLICK) >= _ROLE_COOLDOWN_MAX_KEYS:
        stale = [k for k, v in _ROLE_LAST_CLICK.items() if stamp - v >= _ROLE_COOLDOWN_SECONDS]
        for item in stale:
            _ROLE_LAST_CLICK.pop(item, None)
        if len(_ROLE_LAST_CLICK) >= _ROLE_COOLDOWN_MAX_KEYS:
            _ROLE_LAST_CLICK.pop(next(iter(_ROLE_LAST_CLICK)), None)
    _ROLE_LAST_CLICK[key] = stamp
    return False


def role_custom_id(token: str, index: str | int) -> str:
    token = str(token or "")
    part = str(index)
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid embed role action token")
    if part != "pick" and (not part.isdigit() or int(part) >= 10):
        raise ValueError("Invalid embed role action index")
    return f"{ROLE_PREFIX}{token}:{part}"


def parse_role_custom_id(value: str) -> tuple[str, str] | None:
    raw = str(value or "")
    if not raw.startswith(ROLE_PREFIX):
        return None
    parts = raw[len(ROLE_PREFIX):].split(":")
    if len(parts) != 2:
        return None
    try:
        role_custom_id(parts[0], parts[1])
    except ValueError:
        return None
    return parts[0], parts[1]


def member_role_view(project: Mapping[str, Any], token: str) -> discord.ui.View | None:
    """Discord message actions, not a live in-memory handler registration."""
    model = validate_project(project)
    links = model["links"]
    roles = model["role_actions"]
    if not roles and not links:
        return None
    view = discord.ui.View(timeout=None)
    for item in links:
        view.add_item(discord.ui.Button(label=item["label"], url=item["url"], style=discord.ButtonStyle.link))
    if not roles:
        return view
    if model["role_mode"] == "dropdown":
        select = discord.ui.Select(
            custom_id=role_custom_id(token, "pick"),
            placeholder="Select a role to add or remove",
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=role["label"], value=str(i)) for i, role in enumerate(roles)],
        )
        view.add_item(select)
    else:
        for i, item in enumerate(roles):
            view.add_item(discord.ui.Button(label=item["label"], style=discord.ButtonStyle.secondary, custom_id=role_custom_id(token, i)))
    return view


async def _answer(interaction: discord.Interaction, text: str) -> None:
    opts = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text, **opts)
    else:
        await interaction.response.send_message(text, **opts)


async def _role_click(interaction: discord.Interaction, token: str, item: str) -> None:
    guild = interaction.guild
    user = interaction.user
    channel = interaction.channel
    message = interaction.message
    if not guild or not isinstance(user, discord.Member) or not message or not channel:
        return await _answer(interaction, "This role control only works in its server.")
    if _rate_limited(guild.id, user.id):
        return await _answer(interaction, "Please wait a moment before changing roles again.")
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        from .guild_config import get_guild_config
        from .embed_studio_core import STUDIO_KEY
        from .community_pings_service import community_member_lock
        from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
        async with community_member_lock(int(guild.id), int(user.id)):
            config = await asyncio.wait_for(get_guild_config(int(guild.id), refresh=True), timeout=12)
            if str(config.get("source") or "").startswith("unavailable:"):
                return await _answer(interaction, "Embed role settings are unavailable. No role changed.")
            # Self-role clicks must consult the SAME authoritative storage as
            # Studio publishing. After a v2 cutover the legacy guild blob is
            # intentionally frozen, and reading it would reject live panels.
            from .embed_studio_storage_v2 import storage_v2_enabled, read_library
            if storage_v2_enabled():
                _version, library = await asyncio.wait_for(
                    read_library(int(guild.id), config), timeout=12
                )
            else:
                library = config.get(STUDIO_KEY)
            if not isinstance(library, Mapping):
                return await _answer(interaction, "This embed is no longer configured for role actions.")
            posted = library.get("published")
            if not isinstance(posted, dict):
                return await _answer(interaction, "This embed is no longer configured for role actions.")
            key = f"{int(channel.id)}:{int(message.id)}"
            record = posted.get(key)
            if not isinstance(record, Mapping) or record.get("role_token") != token:
                return await _answer(interaction, "This role control is outdated. Ask staff to republish it.")
            if int(record.get("channel_id", 0)) != channel.id or int(record.get("message_id", 0)) != message.id:
                return await _answer(interaction, "This role control belongs to a different post.")
            # Require the component to have been emitted by this bot, not
            # copied into an unrelated message.
            if not interaction.client.user or message.author.id != interaction.client.user.id:
                return await _answer(interaction, "Only Dank Shield posts can manage roles.")

            role_project = {
                "embeds": [{"title": "Validated role actions"}],
                "content": "",
                "role_mode": record.get("role_mode", "buttons"),
                "role_actions": record.get("role_actions") or [],
                "links": [],
            }
            model = validate_project(role_project)
            if model["role_mode"] == "dropdown":
                if item != "pick":
                    return await _answer(interaction, "Role controls have changed. Refresh this post.")
                values = interaction.data.get("values", []) if isinstance(interaction.data, dict) else []
                if not isinstance(values, list) or len(values) != 1:
                    return await _answer(interaction, "Choose one role.")
                index = str(values[0])
            else:
                if item == "pick":
                    return await _answer(interaction, "Role controls have changed. Refresh this post.")
                index = item
            if not index.isascii() or not index.isdecimal() or int(index) >= len(model["role_actions"]):
                return await _answer(interaction, "That role option was removed.")
            role_id = int(model["role_actions"][int(index)]["role_id"])
            role = guild.get_role(role_id)
            if not isinstance(role, discord.Role):
                return await _answer(interaction, "This role no longer exists.")

            why = _profile_cosmetic_role_blocker(guild, role, config)
            if why:
                return await _answer(interaction, "That role is no longer safe to self-assign. Ask server staff to review the configuration.")
            try:
                current = await asyncio.wait_for(guild.fetch_member(user.id), timeout=8)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                return await _answer(interaction, "Couldn't recheck your current roles. Try again.")
            if role in current.roles:
                await current.remove_roles(role, reason="Dank Shield Embed Studio optional self-role")
                outcome = "Removed"
            else:
                await current.add_roles(role, reason="Dank Shield Embed Studio optional self-role")
                outcome = "Added"

        await _answer(interaction, f"✅ {outcome} {role.mention}.")
    except asyncio.TimeoutError:
        await _answer(interaction, "Role settings took too long to verify. No change was confirmed.")
    except (discord.Forbidden, discord.HTTPException):
        await _answer(interaction, "Discord could not update that role. Staff should check bot hierarchy.")
    except (StudioValidationError, ValueError, TypeError):
        await _answer(interaction, "That role control has an invalid or outdated configuration.")
    except Exception as exc:
        print(f"⚠️ embed studio role action failed guild={guild.id} error={type(exc).__name__}")
        await _answer(interaction, "Role action failed safely. Please try again later.")


async def _listener(interaction: discord.Interaction) -> None:
    if interaction.type is not discord.InteractionType.component:
        return
    data = interaction.data if isinstance(interaction.data, dict) else {}
    candidate = str(data.get("custom_id") or "")
    if not candidate.startswith(ROLE_PREFIX):
        return
    parsed = parse_role_custom_id(candidate)
    if parsed is None:
        await _answer(interaction, "This embed role control is invalid.")
        return
    await _role_click(interaction, *parsed)


def install_embed_studio_role_runtime(bot: Any, *, strict: bool = False) -> bool:
    """Exactly one listener per client; no guild-wide panel read on startup."""
    key = id(bot)
    if key in _ATTACHED_BOT_IDS:
        return True
    try:
        bot.add_listener(_listener, "on_interaction")
        _ATTACHED_BOT_IDS.add(key)
        return True
    except Exception as exc:
        if strict:
            raise RuntimeError("Embed Studio role action runtime is missing") from exc
        return False
