from __future__ import annotations

"""Public Movie Night command, setup, sources, and room control hub."""

import asyncio
import importlib.util
import os
import re
from typing import Any, Mapping, Optional

import aiohttp
import discord
from discord import app_commands

from stoney_verify.community_pings_service import (
    COMMUNITY_PINGS_KEY,
    movie_night_registration_blocker,
    movie_night_role_id,
    parse_community_pings,
    register_movie_night_notification_role,
    validate_config,
)
from stoney_verify.media_source_registry import (
    CustomMediaSource,
    MediaSourceRegistry,
    add_custom_source,
    enabled_custom_sources,
    load_media_source_registry,
    remove_custom_source,
    save_media_source_registry,
    set_custom_source_enabled,
)
from stoney_verify.movie_night import (
    MovieNightRoom,
    get_movie_night_manager,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.torrent_media_server import (
    media_public_base_url,
    media_server_ready,
)
from stoney_verify.torrent_streaming import (
    find_magnet,
    get_torrent_manager,
    is_torrent_filename,
)
from stoney_verify.ui.picker import DankChoice, DankPickerView


_ALLOWED_NONE = discord.AllowedMentions.none()
_MOVIE_ROLE_NAME = "Movie Night"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _compact(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_NONE,
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


async def _replace(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "allowed_mentions": _ALLOWED_NONE,
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    elif interaction.message is not None:
        await interaction.response.edit_message(**payload)
    else:
        await interaction.response.send_message(**payload, ephemeral=True)


def _staff_authorized(interaction: discord.Interaction) -> bool:
    from .public_owner_authority import (
        interaction_has_administrator_authority,
        interaction_has_manage_guild_authority,
        interaction_is_actual_guild_owner,
    )

    return bool(
        interaction_is_actual_guild_owner(interaction)
        or interaction_has_administrator_authority(interaction)
        or interaction_has_manage_guild_authority(interaction)
    )


async def _load_community(
    guild: discord.Guild,
) -> tuple[Mapping[str, Any], Any]:
    from stoney_verify.guild_config import get_guild_config

    raw = await get_guild_config(int(guild.id), refresh=True)
    return raw, parse_community_pings(raw)


async def _save_community(
    guild: discord.Guild,
    *,
    expected_config: Mapping[str, Any],
    updated: Any,
) -> tuple[bool, Mapping[str, Any]]:
    errors = validate_config(updated)
    if errors:
        raise ValueError("Community & Pings validation failed: " + "; ".join(errors[:4]))

    from stoney_verify.guild_config import compare_and_swap_guild_config_key

    return await compare_and_swap_guild_config_key(
        int(guild.id),
        COMMUNITY_PINGS_KEY,
        expected=expected_config.get(COMMUNITY_PINGS_KEY),
        value=updated.to_payload(),
        source="movie_night_setup",
    )


def _movie_role(
    guild: discord.Guild,
    raw_config: Mapping[str, Any],
) -> Optional[discord.Role]:
    model = parse_community_pings(raw_config)
    role_id = movie_night_role_id(model)
    if not role_id:
        return None
    role = guild.get_role(int(role_id))
    return role if isinstance(role, discord.Role) else None


def _channel_permissions(
    guild: discord.Guild,
    channel: Any,
) -> Optional[discord.Permissions]:
    me = guild.me
    if me is None or channel is None or not hasattr(channel, "permissions_for"):
        return None
    try:
        return channel.permissions_for(me)
    except Exception:
        return None


def _setup_readiness(
    guild: discord.Guild,
    channel: Any,
    raw_config: Mapping[str, Any],
    source_registry: MediaSourceRegistry,
) -> dict[str, Any]:
    role = _movie_role(guild, raw_config)
    perms = _channel_permissions(guild, channel)
    me = guild.me
    guild_perms = getattr(me, "guild_permissions", None)

    can_send = bool(
        perms
        and getattr(perms, "view_channel", False)
        and getattr(perms, "send_messages", False)
        and getattr(perms, "embed_links", False)
    )
    can_attach = bool(perms and getattr(perms, "attach_files", False))
    can_manage_roles = bool(
        guild_perms
        and (
            getattr(guild_perms, "administrator", False)
            or getattr(guild_perms, "manage_roles", False)
        )
    )
    ping_ready = bool(
        role
        and (
            getattr(role, "mentionable", False)
            or (
                perms
                and (
                    getattr(perms, "administrator", False)
                    or getattr(perms, "mention_everyone", False)
                )
            )
        )
    )

    public_base = media_public_base_url()
    stream_secret = bool(
        str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()
    )
    libtorrent_ready = importlib.util.find_spec("libtorrent") is not None
    pyav_ready = importlib.util.find_spec("av") is not None
    runtime_ready = bool(media_server_ready())

    blockers: list[str] = []
    warnings: list[str] = []

    if role is None:
        blockers.append("Movie Night notification role is not mapped.")
    if not can_send:
        blockers.append("Dank Shield needs View Channel, Send Messages, and Embed Links here.")
    if role is not None and not ping_ready:
        blockers.append(
            "The Movie Night role is not mentionable and Dank Shield lacks Mention Everyone here."
        )
    if not public_base:
        blockers.append("DANK_MEDIA_PUBLIC_BASE_URL is not configured.")
    if not stream_secret:
        blockers.append("DANK_TORRENT_STREAM_SECRET is not configured.")
    if not libtorrent_ready:
        blockers.append("The pinned libtorrent runtime is not installed.")
    if not pyav_ready:
        blockers.append("The pinned PyAV metadata runtime is not installed.")
    if public_base and stream_secret and not runtime_ready:
        warnings.append(
            "Media settings exist, but the public media server is not currently reporting started."
        )
    if not can_attach:
        warnings.append(
            "Attach Files is missing here. Torrent streaming still uses the media server, "
            "but direct Discord media relay/fallbacks may be reduced."
        )
    if role is None and not can_manage_roles:
        blockers.append("Dank Shield needs Manage Roles to create the Movie Night role.")
    if not enabled_custom_sources(source_registry.sources and source_registry or MediaSourceRegistry()):
        warnings.append(
            "No custom media sources are enabled. Magnet/.torrent playback still works."
        )

    return {
        "role": role,
        "can_send": can_send,
        "can_attach": can_attach,
        "can_manage_roles": can_manage_roles,
        "ping_ready": ping_ready,
        "public_base": public_base,
        "stream_secret": stream_secret,
        "libtorrent_ready": libtorrent_ready,
        "pyav_ready": pyav_ready,
        "runtime_ready": runtime_ready,
        "sources": len(source_registry.sources),
        "enabled_sources": len(enabled_custom_sources(source_registry)),
        "blockers": blockers,
        "warnings": warnings,
        "launch_ready": not blockers,
    }


def _status(value: bool) -> str:
    return "✅ Ready" if value else "❌ Missing"


def _setup_embed(
    guild: discord.Guild,
    channel: Any,
    raw_config: Mapping[str, Any],
    source_registry: MediaSourceRegistry,
) -> discord.Embed:
    ready = _setup_readiness(guild, channel, raw_config, source_registry)
    role = ready["role"]

    embed = discord.Embed(
        title="🎬 Movie Night Setup",
        description=(
            "**Home › Community & Engagement › Movie Night › Setup**\n"
            "This page validates the whole Movie Night chain before a room is allowed to launch."
        ),
        color=discord.Color.green() if ready["launch_ready"] else discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="1 • Community & Pings role",
        value=(
            f"{'✅' if role else '❌'} Notify role: "
            f"{role.mention if isinstance(role, discord.Role) else 'Not configured'}\n"
            f"{'✅' if ready['ping_ready'] else '❌'} Notification ping readiness\n"
            f"{'✅' if ready['can_manage_roles'] else '⚠️'} Manage Roles "
            "(needed only to create/repair the role)"
        ),
        inline=False,
    )
    embed.add_field(
        name="2 • Current channel",
        value=(
            f"{_status(ready['can_send'])} • View / Send / Embed\n"
            f"{'✅' if ready['can_attach'] else '⚠️'} Attach Files"
        ),
        inline=False,
    )
    embed.add_field(
        name="3 • Torrent + metadata runtime",
        value=(
            f"{_status(ready['libtorrent_ready'])} • libtorrent\n"
            f"{_status(ready['pyav_ready'])} • PyAV / FFmpeg metadata\n"
            f"{_status(bool(ready['public_base']))} • DANK_MEDIA_PUBLIC_BASE_URL\n"
            f"{_status(ready['stream_secret'])} • DANK_TORRENT_STREAM_SECRET\n"
            f"{_status(ready['runtime_ready'])} • public media server process"
        ),
        inline=False,
    )
    embed.add_field(
        name="4 • Search / custom sources",
        value=(
            f"Configured: **{ready['sources']}** • Enabled: **{ready['enabled_sources']}**\n"
            "Custom authorized HTTPS feeds are managed from **Sources**. "
            "Direct magnet and .torrent playback does not require a custom feed."
        ),
        inline=False,
    )

    if ready["blockers"]:
        embed.add_field(
            name="🚫 Launch blockers",
            value="\n".join(f"• {item}" for item in ready["blockers"])[:1024],
            inline=False,
        )
    if ready["warnings"]:
        embed.add_field(
            name="⚠️ Warnings",
            value="\n".join(f"• {item}" for item in ready["warnings"])[:1024],
            inline=False,
        )

    embed.set_footer(
        text=(
            "Ready to launch"
            if ready["launch_ready"]
            else "Fix every red blocker before starting Movie Night"
        )
    )
    return embed


async def _sources_state(
    guild_id: int,
) -> tuple[Mapping[str, Any], MediaSourceRegistry]:
    return await load_media_source_registry(int(guild_id), refresh=True)


def _sources_embed(registry: MediaSourceRegistry) -> discord.Embed:
    embed = discord.Embed(
        title="🎞️ Movie Night Sources",
        description=(
            "Add authorized HTTPS catalog/feed sources for Movie Night search. "
            "Results from every source retain provenance and merge into the same "
            "seed/leech/quality voting model."
        ),
        color=discord.Color.blurple(),
    )
    if not registry.sources:
        embed.add_field(
            name="Configured sources",
            value="None yet. Magnet and .torrent playback still works directly.",
            inline=False,
        )
    else:
        lines = []
        for source in registry.sources:
            state = "✅" if source.enabled else "⏸️"
            lines.append(
                f"{state} **{source.label}** • `{source.source_id}`\n"
                f"↳ {source.endpoint_url[:180]}"
            )
        embed.add_field(
            name=f"Configured sources • {len(registry.sources)}",
            value="\n".join(lines)[:4000],
            inline=False,
        )
    embed.add_field(
        name="Network safety",
        value=(
            "Sources must use HTTPS, cannot embed credentials, and cannot point at obvious "
            "localhost/private/reserved addresses. The resolver must also re-check DNS destinations "
            "before fetching."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Revision {registry.revision} • per-server sources")
    return embed


def _room_for_interaction(interaction: discord.Interaction) -> Optional[MovieNightRoom]:
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        return None
    return get_movie_night_manager().active_room_for_channel(
        int(guild.id),
        int(getattr(channel, "id", 0) or 0),
    )


def _room_embed(
    interaction: discord.Interaction,
    room: Optional[MovieNightRoom],
) -> discord.Embed:
    if room is None:
        embed = discord.Embed(
            title="🎬 Movie Night",
            description=(
                "No room is active in this channel. Start one, then use Search or "
                "`/movie magnet:` / `/movie torrent:` to choose the media."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Room flow",
            value=(
                "1. **Start / Join**\n"
                "2. **Search / Vote** or provide a magnet/.torrent\n"
                "3. Pick the release/quality using seed, leech, metadata, and votes\n"
                "4. Watch together with host controls and vote failover"
            ),
            inline=False,
        )
        return embed

    manager = get_movie_night_manager()
    active = manager.active_viewers(room)
    host = interaction.guild.get_member(room.host_id) if interaction.guild else None
    host_label = host.mention if isinstance(host, discord.Member) else f"<@{room.host_id}>"
    embed = discord.Embed(
        title="🎬 Movie Night • Active Room",
        description=(
            f"Host: {host_label}\n"
            f"State: **{room.playback_state.title()}**\n"
            f"Viewers: **{len(active)}**\n"
            f"Position: **{int(room.current_position())}s**"
        ),
        color=discord.Color.green(),
    )
    if room.approved_search_query:
        embed.add_field(
            name="Approved search",
            value=room.approved_search_query[:1024],
            inline=False,
        )
    if room.queue:
        embed.add_field(
            name="Queue",
            value=f"{len(room.queue)} movie(s) queued.",
            inline=True,
        )
    unresolved = [vote for vote in room.votes.values() if not vote.resolved]
    if unresolved:
        latest = max(unresolved, key=lambda item: item.created_at)
        embed.add_field(
            name="Open vote",
            value=(
                f"**{latest.action}** • ✅ {len(latest.yes)} / ❌ {len(latest.no)}\n"
                "Use the Vote Yes / Vote No controls in this hub."
            ),
            inline=False,
        )
    if room.stream_token:
        embed.add_field(
            name="Media",
            value="Torrent/media session attached and ready for the shared playback pipeline.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Media",
            value="No media attached yet. Search or provide a magnet/.torrent.",
            inline=False,
        )
    embed.set_footer(text=f"Room {room.room_id} • Movie Night state is shared per channel")
    return embed


def _queue_embed(room: MovieNightRoom) -> discord.Embed:
    manager = get_movie_night_manager()
    embed = discord.Embed(
        title="📺 Movie Night Queue",
        color=discord.Color.blurple(),
    )
    if not room.candidates:
        embed.description = "No movie candidates yet. Use Search / Vote to begin."
        return embed

    ranked = manager.ranked_candidates(room.room_id)
    lines: list[str] = []
    for candidate in ranked[:15]:
        variants = manager.ranked_variants(room.room_id, candidate.candidate_id)
        best = variants[0] if variants else None
        if best is None:
            lines.append(f"• **{candidate.title}** • {len(candidate.votes)} vote(s)")
            continue
        health = best.swarm_health
        meta = best.metadata or {}
        release = meta.get("release_name") if isinstance(meta.get("release_name"), Mapping) else {}
        source = str(release.get("source") or "Unknown source")
        size = f"{best.file_size / (1024 ** 3):.2f} GiB" if best.file_size else "size unknown"
        lines.append(
            f"• **{candidate.title}** • {len(candidate.votes)} movie vote(s)\n"
            f"  ↳ {source} • {size} • 🌱 {health['seeds']} seeds • "
            f"🧲 {health['leechers']} leeches • {len(best.votes)} release vote(s)"
        )
    embed.description = "\n".join(lines)[:4000]
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/movie` panel to use these controls.")
        return False


class CustomSourceModal(discord.ui.Modal, title="Add / Update Movie Source"):
    source_id = discord.ui.TextInput(
        label="Source ID",
        placeholder="family-library",
        min_length=1,
        max_length=48,
    )
    label = discord.ui.TextInput(
        label="Display name",
        placeholder="Family Library",
        min_length=1,
        max_length=80,
    )
    endpoint = discord.ui.TextInput(
        label="HTTPS catalog/feed endpoint",
        placeholder="https://media.example.com/search?q={query}",
        min_length=8,
        max_length=1000,
    )

    def __init__(self, *, owner_id: int, baseline: Mapping[str, Any]) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.baseline = dict(baseline)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ This source editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Movie Night sources are configured inside a server.")
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")

        current = MediaSourceRegistry()
        from stoney_verify.media_source_registry import parse_media_source_registry
        current = parse_media_source_registry(self.baseline)
        try:
            updated = add_custom_source(
                current,
                source_id=str(self.source_id.value),
                label=str(self.label.value),
                endpoint_url=str(self.endpoint.value),
                added_by=int(interaction.user.id),
            )
        except ValueError as exc:
            return await _private(interaction, f"❌ {exc}")

        try:
            applied, _saved = await save_media_source_registry(
                int(guild.id),
                expected_config=self.baseline,
                updated=updated,
            )
        except Exception as exc:
            return await _private(
                interaction,
                f"❌ Movie Night source could not save safely: {type(exc).__name__}.",
            )
        if not applied:
            return await _private(
                interaction,
                "❌ Movie Night sources changed in another admin session. Refresh and try again.",
            )
        await open_movie_night_sources(interaction, replace_message=True)


class SourceActionView(_OwnedView):
    def __init__(self, owner_id: int, source_id: str) -> None:
        super().__init__(owner_id)
        self.source_id = str(source_id)

    async def _mutate(
        self,
        interaction: discord.Interaction,
        *,
        remove: bool = False,
        enabled: Optional[bool] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")
        raw, registry = await _sources_state(int(guild.id))
        try:
            updated = (
                remove_custom_source(registry, self.source_id)
                if remove
                else set_custom_source_enabled(
                    registry,
                    self.source_id,
                    bool(enabled),
                )
            )
        except (ValueError, LookupError) as exc:
            return await _private(interaction, f"❌ {exc}")
        applied, _saved = await save_media_source_registry(
            int(guild.id),
            expected_config=raw,
            updated=updated,
        )
        if not applied:
            return await _private(
                interaction,
                "❌ Sources changed in another admin session. Refresh before trying again.",
            )
        await open_movie_night_sources(interaction, replace_message=True)

    @discord.ui.button(label="Enable", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._mutate(interaction, enabled=True)

    @discord.ui.button(label="Disable", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._mutate(interaction, enabled=False)

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._mutate(interaction, remove=True)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night_sources(interaction, replace_message=True)


async def _open_source_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Movie Night inside a server.")
    _raw, registry = await _sources_state(int(guild.id))
    if not registry.sources:
        return await _private(interaction, "ℹ️ No custom Movie Night sources are configured.")

    async def picked(pick_interaction: discord.Interaction, value: str) -> None:
        source = next(
            (item for item in registry.sources if item.source_id == value),
            None,
        )
        if source is None:
            return await _private(pick_interaction, "❌ That source no longer exists.")
        embed = discord.Embed(
            title=f"🎞️ {source.label}",
            description=(
                f"ID: `{source.source_id}`\n"
                f"State: **{'Enabled' if source.enabled else 'Disabled'}**\n"
                f"Endpoint: {source.endpoint_url}"
            ),
            color=discord.Color.blurple(),
        )
        await _replace(
            pick_interaction,
            embed=embed,
            view=SourceActionView(int(pick_interaction.user.id), source.source_id),
        )

    choices = [
        DankChoice(
            label=source.label,
            value=source.source_id,
            description=("Enabled" if source.enabled else "Disabled"),
            emoji="✅" if source.enabled else "⏸️",
        )
        for source in registry.sources
    ]
    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=picked,
        custom_id="dank:movie:sources:manage",
        placeholder="Choose a Movie Night source…",
        title="Manage Movie Night Source",
        on_home=lambda back_interaction: open_movie_night_sources(
            back_interaction,
            replace_message=True,
        ),
        home_label="Sources",
    )
    await _replace(
        interaction,
        embed=discord.Embed(
            title="🎞️ Manage Movie Night Source",
            description="Choose the custom source to enable, disable, or remove.",
            color=discord.Color.blurple(),
        ),
        view=view,
    )


class MovieNightSourcesView(_OwnedView):
    @discord.ui.button(label="Add / Update Source", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Use Movie Night inside a server.")
        raw, _registry = await _sources_state(int(guild.id))
        await interaction.response.send_modal(
            CustomSourceModal(owner_id=self.owner_id, baseline=raw)
        )

    @discord.ui.button(label="Manage Source", emoji="🛠️", style=discord.ButtonStyle.primary, row=0)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")
        await _open_source_picker(interaction)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night(interaction, replace_message=True)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Movie Night sources closed.", embed=None, view=None)


async def open_movie_night_sources(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Movie Night only works inside a server.")
    _raw, registry = await _sources_state(int(guild.id))
    embed = _sources_embed(registry)
    view = MovieNightSourcesView(int(interaction.user.id))
    if replace_message:
        await _replace(interaction, embed=embed, view=view)
    else:
        await _private(interaction, embed=embed, view=view)


async def _create_or_repair_movie_role(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Movie Night setup only works inside a server.")
    if not _staff_authorized(interaction):
        return await _private(interaction, "❌ Manage Server or Administrator is required.")

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    raw, model = await _load_community(guild)
    current_id = movie_night_role_id(model)
    current_role = guild.get_role(current_id) if current_id else None

    if isinstance(current_role, discord.Role):
        try:
            updated = register_movie_night_notification_role(
                model,
                role_id=int(current_role.id),
            )
            applied, _saved = await _save_community(
                guild,
                expected_config=raw,
                updated=updated,
            )
        except Exception as exc:
            return await interaction.edit_original_response(
                content=f"❌ Movie Night role mapping could not be repaired: {type(exc).__name__}: {exc}",
                embed=None,
                view=MovieNightSetupView(int(interaction.user.id)),
            )
        if not applied:
            return await interaction.edit_original_response(
                content="❌ Community & Pings changed during setup. Refresh and retry.",
                embed=None,
                view=MovieNightSetupView(int(interaction.user.id)),
            )
        return await open_movie_night_setup(interaction, replace_message=True)

    blocker = movie_night_registration_blocker(model)
    if blocker:
        return await interaction.edit_original_response(
            content=f"❌ {blocker}",
            embed=None,
            view=MovieNightSetupView(int(interaction.user.id)),
        )

    me = guild.me
    perms = getattr(me, "guild_permissions", None)
    if not perms or not (
        getattr(perms, "administrator", False)
        or getattr(perms, "manage_roles", False)
    ):
        return await interaction.edit_original_response(
            content="❌ Dank Shield needs Manage Roles before it can create the Movie Night role.",
            embed=None,
            view=MovieNightSetupView(int(interaction.user.id)),
        )

    role: Optional[discord.Role] = None
    try:
        role = await guild.create_role(
            name=_MOVIE_ROLE_NAME,
            mentionable=False,
            reason="Dank Shield Movie Night setup",
        )
        updated = register_movie_night_notification_role(
            model,
            role_id=int(role.id),
            label=_MOVIE_ROLE_NAME,
            emoji="🎬",
            description="Opt in to Movie Night announcements",
        )
        applied, _saved = await _save_community(
            guild,
            expected_config=raw,
            updated=updated,
        )
        if not applied:
            raise RuntimeError(
                "Community & Pings changed during role creation; the new role was not registered."
            )
    except Exception as exc:
        if isinstance(role, discord.Role):
            try:
                await role.delete(
                    reason="Rollback failed Movie Night Community & Pings registration"
                )
            except Exception:
                pass
        return await interaction.edit_original_response(
            content=f"❌ Movie Night role setup failed safely: {type(exc).__name__}: {exc}",
            embed=None,
            view=MovieNightSetupView(int(interaction.user.id)),
        )

    await open_movie_night_setup(interaction, replace_message=True)


async def _test_public_media(interaction: discord.Interaction) -> None:
    base = media_public_base_url()
    if not base:
        return await _private(
            interaction,
            "❌ DANK_MEDIA_PUBLIC_BASE_URL is not configured.",
        )
    health_url = base.rstrip("/") + "/health"
    timeout = aiohttp.ClientTimeout(total=6.0, connect=3.0, sock_read=4.0)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(health_url, allow_redirects=False) as response:
                if response.status != 200:
                    return await _private(
                        interaction,
                        f"❌ Public media health returned HTTP {response.status}.",
                    )
                data = await response.json(content_type=None)
    except Exception as exc:
        return await _private(
            interaction,
            f"❌ Public media endpoint is not reachable: {type(exc).__name__}.",
        )
    if not isinstance(data, Mapping) or data.get("service") != "dank_torrent_media":
        return await _private(
            interaction,
            "❌ The public URL responded, but it was not Dank Shield's torrent media service.",
        )
    await _private(
        interaction,
        "✅ Public Movie Night media endpoint is reachable and identified correctly.",
    )


class MovieNightSetupView(_OwnedView):
    @discord.ui.button(label="Create / Repair Role", emoji="🎬", style=discord.ButtonStyle.success, row=0)
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _create_or_repair_movie_role(interaction)

    @discord.ui.button(label="Sources", emoji="🎞️", style=discord.ButtonStyle.primary, row=0)
    async def sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night_sources(interaction, replace_message=True)

    @discord.ui.button(label="Test Media Endpoint", emoji="🌐", style=discord.ButtonStyle.primary, row=0)
    async def test_media(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _test_public_media(interaction)

    @discord.ui.button(label="Community & Pings", emoji="🌿", style=discord.ButtonStyle.secondary, row=1)
    async def community(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_pings import open_community_ping_setup
        await open_community_ping_setup(interaction, replace_message=True)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night_setup(interaction, replace_message=True)

    @discord.ui.button(label="Back to Movie Night", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night(interaction, replace_message=True)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Movie Night setup closed.", embed=None, view=None)


async def open_movie_night_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Movie Night setup only works inside a server.")
    if not _staff_authorized(interaction):
        return await _private(interaction, "❌ Manage Server or Administrator is required.")
    raw, _model = await _load_community(guild)
    _source_raw, registry = await _sources_state(int(guild.id))
    embed = _setup_embed(guild, interaction.channel, raw, registry)
    view = MovieNightSetupView(int(interaction.user.id))
    if replace_message:
        await _replace(interaction, embed=embed, view=view)
    else:
        await _private(interaction, embed=embed, view=view)


class MovieSearchModal(discord.ui.Modal, title="Search / Vote for a Movie"):
    query = discord.ui.TextInput(
        label="Movie or show",
        placeholder="What should the room watch next?",
        min_length=1,
        max_length=180,
    )

    def __init__(self, *, owner_id: int, room_id: str) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.room_id = str(room_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ This search belongs to another member.")
        manager = get_movie_night_manager()
        try:
            vote = manager.propose_vote(
                self.room_id,
                proposer_id=int(interaction.user.id),
                action="search",
                payload={"query": _compact(self.query.value)},
            )
        except Exception as exc:
            return await _private(interaction, f"❌ Search vote could not start: {exc}")
        if vote.resolved and vote.passed:
            await _private(
                interaction,
                f"✅ Search approved: **{_compact(self.query.value)}**. "
                "Configured source resolvers can now populate release candidates.",
            )
        else:
            await _private(
                interaction,
                f"🗳️ Search vote opened for **{_compact(self.query.value)}**. "
                "Other active viewers can vote from their `/movie` panel.",
            )


async def _announce_room(
    interaction: discord.Interaction,
    room: MovieNightRoom,
    *,
    role: discord.Role,
) -> None:
    channel = interaction.channel
    if channel is None or not hasattr(channel, "send"):
        return
    embed = discord.Embed(
        title="🎬 Movie Night Started",
        description=(
            f"{interaction.user.mention} is hosting Movie Night.\n"
            "Open `/movie` to join, search, vote, and view the queue."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=f"Room {room.room_id}")
    allowed = discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[role],
        replied_user=False,
    )
    await channel.send(
        content=role.mention,
        embed=embed,
        allowed_mentions=allowed,
    )


async def _start_or_join_room(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        return await _private(interaction, "❌ Movie Night only works inside a server.")

    raw, _model = await _load_community(guild)
    _source_raw, registry = await _sources_state(int(guild.id))
    ready = _setup_readiness(guild, channel, raw, registry)
    if not ready["launch_ready"]:
        return await _private(
            interaction,
            "❌ Movie Night setup is not launch-ready. Open **Setup** and fix the listed blockers.",
        )

    manager = get_movie_night_manager()
    room = manager.active_room_for_channel(int(guild.id), int(channel.id))
    if room is not None:
        manager.join_room(room.room_id, user_id=int(interaction.user.id))
        return await open_movie_night(interaction, replace_message=True)

    role = ready["role"]
    if not isinstance(role, discord.Role):
        return await _private(interaction, "❌ Movie Night notification role is missing.")

    try:
        room = manager.create_room(
            guild_id=int(guild.id),
            channel_id=int(channel.id),
            host_id=int(interaction.user.id),
            stream_token="",
        )
        await _announce_room(interaction, room, role=role)
    except Exception as exc:
        return await _private(
            interaction,
            f"❌ Movie Night room could not start: {type(exc).__name__}: {exc}",
        )
    await open_movie_night(interaction, replace_message=True)


def _latest_open_vote(room: MovieNightRoom) -> Any:
    votes = [item for item in room.votes.values() if not item.resolved]
    return max(votes, key=lambda item: item.created_at) if votes else None


class MovieNightHubView(_OwnedView):
    @discord.ui.button(label="Start / Join", emoji="🎬", style=discord.ButtonStyle.success, row=0)
    async def start_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _start_or_join_room(interaction)

    @discord.ui.button(label="Search / Vote", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room = _room_for_interaction(interaction)
        if room is None:
            return await _private(interaction, "❌ Start or join a Movie Night room first.")
        get_movie_night_manager().join_room(
            room.room_id,
            user_id=int(interaction.user.id),
        )
        await interaction.response.send_modal(
            MovieSearchModal(owner_id=self.owner_id, room_id=room.room_id)
        )

    @discord.ui.button(label="Queue", emoji="📺", style=discord.ButtonStyle.primary, row=0)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room = _room_for_interaction(interaction)
        if room is None:
            return await _private(interaction, "ℹ️ No Movie Night room is active here.")
        await _replace(
            interaction,
            embed=_queue_embed(room),
            view=MovieNightHubView(self.owner_id),
        )

    @discord.ui.button(label="Vote Yes", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._cast_latest(interaction, True)

    @discord.ui.button(label="Vote No", emoji="❌", style=discord.ButtonStyle.danger, row=1)
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._cast_latest(interaction, False)

    async def _cast_latest(self, interaction: discord.Interaction, approve: bool) -> None:
        room = _room_for_interaction(interaction)
        if room is None:
            return await _private(interaction, "ℹ️ No Movie Night room is active here.")
        manager = get_movie_night_manager()
        manager.join_room(room.room_id, user_id=int(interaction.user.id))
        vote = _latest_open_vote(room)
        if vote is None:
            return await _private(interaction, "ℹ️ There is no open Movie Night vote.")
        try:
            manager.cast_vote(
                room.room_id,
                vote.vote_id,
                user_id=int(interaction.user.id),
                approve=approve,
            )
        except Exception as exc:
            return await _private(interaction, f"❌ Vote failed: {exc}")
        await open_movie_night(interaction, replace_message=True)

    @discord.ui.button(label="Sources", emoji="🎞️", style=discord.ButtonStyle.secondary, row=2)
    async def sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night_sources(interaction, replace_message=True)

    @discord.ui.button(label="Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night_setup(interaction, replace_message=True)

    @discord.ui.button(label="Community & Pings", emoji="🌿", style=discord.ButtonStyle.secondary, row=2)
    async def pings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if _staff_authorized(interaction):
            from .public_community_pings import open_community_ping_setup
            return await open_community_ping_setup(interaction, replace_message=True)
        from .public_community_pings import open_member_community_pings
        return await open_member_community_pings(interaction, replace_message=True)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night(interaction, replace_message=True)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Movie Night closed.", embed=None, view=None)


async def open_movie_night(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    if interaction.guild is None:
        return await _private(interaction, "❌ Movie Night only works inside a server.")
    room = _room_for_interaction(interaction)
    if room is not None:
        get_movie_night_manager().join_room(
            room.room_id,
            user_id=int(interaction.user.id),
        )
    embed = _room_embed(interaction, room)
    view = MovieNightHubView(int(interaction.user.id))
    if replace_message:
        await _replace(interaction, embed=embed, view=view)
    else:
        await _private(interaction, embed=embed, view=view)


async def _attach_torrent_media(
    interaction: discord.Interaction,
    *,
    magnet: str = "",
    torrent: Optional[discord.Attachment] = None,
) -> None:
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        return await _private(interaction, "❌ Movie Night only works inside a server.")
    if magnet and torrent is not None:
        return await _private(interaction, "❌ Choose either a magnet or a .torrent file, not both.")

    raw, _model = await _load_community(guild)
    _source_raw, registry = await _sources_state(int(guild.id))
    ready = _setup_readiness(guild, channel, raw, registry)
    if not ready["launch_ready"]:
        return await _private(
            interaction,
            "❌ Movie Night setup is not launch-ready. Run `/movie` → **Setup** first.",
        )

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    manager = get_torrent_manager()
    try:
        if magnet:
            clean_magnet = find_magnet(magnet)
            if not clean_magnet:
                raise ValueError("That is not a valid magnet link.")
            session = await manager.start_magnet(
                clean_magnet,
                guild_id=int(guild.id),
                owner_id=int(interaction.user.id),
            )
        elif torrent is not None:
            if not is_torrent_filename(str(torrent.filename or "")):
                raise ValueError("Upload a real .torrent metadata file.")
            if int(getattr(torrent, "size", 0) or 0) > manager.max_metadata_bytes:
                raise ValueError("That .torrent metadata file exceeds the configured limit.")
            payload = await torrent.read()
            session = await manager.start_torrent_bytes(
                bytes(payload),
                guild_id=int(guild.id),
                owner_id=int(interaction.user.id),
            )
        else:
            return await open_movie_night(interaction, replace_message=True)
    except Exception as exc:
        return await interaction.edit_original_response(
            content=f"❌ Torrent could not start: {type(exc).__name__}: {exc}",
            embed=None,
            view=MovieNightHubView(int(interaction.user.id)),
        )

    stream_url = manager.stream_url(session)
    if not stream_url:
        await manager.remove(session.token)
        return await interaction.edit_original_response(
            content="❌ Torrent started, but no signed public stream URL could be created. Check Movie Night Setup.",
            embed=None,
            view=MovieNightHubView(int(interaction.user.id)),
        )

    room_manager = get_movie_night_manager()
    room = room_manager.active_room_for_channel(int(guild.id), int(channel.id))
    if room is None:
        room = room_manager.create_room(
            guild_id=int(guild.id),
            channel_id=int(channel.id),
            host_id=int(interaction.user.id),
            stream_token=session.token,
        )
        role = ready["role"]
        if isinstance(role, discord.Role):
            try:
                await _announce_room(interaction, room, role=role)
            except Exception:
                pass
    else:
        if int(room.host_id) != int(interaction.user.id):
            await manager.remove(session.token)
            return await interaction.edit_original_response(
                content="❌ Only the active Movie Night host can replace the room's media source.",
                embed=None,
                view=MovieNightHubView(int(interaction.user.id)),
            )
        previous = str(room.stream_token or "")
        room.stream_token = session.token
        if previous and previous != session.token:
            await manager.remove(previous)

    await interaction.edit_original_response(
        content=(
            f"✅ Movie Night media attached: **{session.file_name}**\n"
            f"Full progressive stream: {stream_url}"
        )[:2000],
        embed=_room_embed(interaction, room),
        view=MovieNightHubView(int(interaction.user.id)),
        allowed_mentions=_ALLOWED_NONE,
    )


@app_commands.describe(
    magnet="Optional authorized magnet link to attach to this channel's Movie Night.",
    torrent="Optional .torrent metadata file to attach to this channel's Movie Night.",
)
async def open_movie_night_command(
    interaction: discord.Interaction,
    magnet: Optional[str] = None,
    torrent: Optional[discord.Attachment] = None,
) -> None:
    if magnet or torrent is not None:
        return await _attach_torrent_media(
            interaction,
            magnet=str(magnet or ""),
            torrent=torrent,
        )
    await open_movie_night(interaction, replace_message=False)


__all__ = [
    "CustomSourceModal",
    "MovieNightHubView",
    "MovieNightSetupView",
    "MovieNightSourcesView",
    "SourceActionView",
    "open_movie_night",
    "open_movie_night_command",
    "open_movie_night_setup",
    "open_movie_night_sources",
]
