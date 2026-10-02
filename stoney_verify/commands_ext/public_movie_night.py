from __future__ import annotations

"""Public Movie Night command, setup, sources, and room control hub."""

import asyncio
import importlib.util
import os
import re
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

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
    PROVIDER_TYPE_EXTERNAL,
    PROVIDER_TYPE_JSON,
    CustomMediaSource,
    MediaSourceRegistry,
    add_custom_source,
    enabled_custom_sources,
    enabled_external_sources,
    load_media_source_registry,
    prepare_example_search_url,
    remove_custom_source,
    render_provider_search_url,
    save_media_source_registry,
    set_custom_source_enabled,
)
from stoney_verify.media_source_resolver import (
    INTERNET_ARCHIVE_SOURCE_LABEL,
    MediaSourceSearchOutcome,
    ResolvedMediaVariant,
    fetch_torrent_metadata,
    probe_custom_media_source,
    search_movie_sources,
)
from stoney_verify.movie_catalog import (
    CatalogMovie,
    get_tmdb_watch_availability,
    search_tmdb_movies,
    tmdb_catalog_ready,
)
from stoney_verify.movie_night import (
    MovieNightRoom,
    get_movie_night_manager,
    movie_room_lease_key,
)
from stoney_verify.movie_night_session import terminate_movie_night_room
from stoney_verify.movie_night_web import movie_night_watch_url
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.torrent_media_server import (
    media_bind_host,
    media_bind_port,
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
_CINEMA_NAME = "Dank Cinema"
_CINEMA_TAGLINE = "Search it. Queue it. Vote it. Watch together."
_CINEMA_FOOTER = "Dank Cinema • powered by Dank Shield"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _compact(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _format_bytes(value: Any) -> str:
    size = max(0, _safe_int(value, 0))
    if size <= 0:
        return "size unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            break
        amount /= 1024.0
    return f"{amount:.2f} {unit}"


def _release_source_label(metadata: Mapping[str, Any]) -> str:
    release = metadata.get("release_name")
    if isinstance(release, Mapping):
        return _compact(release.get("source") or "Unknown source", 40)
    return "Unknown source"


def _release_hint_label(metadata: Mapping[str, Any]) -> str:
    release = metadata.get("release_name")
    if not isinstance(release, Mapping):
        return ""
    parts: list[str] = []
    for key in ("resolution", "video_codec", "audio_codec"):
        value = _compact(release.get(key), 30)
        if value and value not in parts:
            parts.append(value)
    return " • ".join(parts[:3])


def _variant_choice_text(variant: Any) -> tuple[str, str]:
    metadata = variant.metadata if isinstance(variant.metadata, Mapping) else {}
    source = _release_source_label(metadata)
    hint = _release_hint_label(metadata)
    health = variant.swarm_health
    label = f"{source} • {_format_bytes(variant.file_size)}"
    description = (
        f"Seeds {health['seeds']} • Leeches {health['leechers']} • "
        f"{variant.source_label or variant.source_id or 'custom source'}"
    )
    if hint:
        description = f"{hint} • {description}"
    return label[:100], description[:100]


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
    bind_host = media_bind_host()
    bind_port = media_bind_port()
    public_host = ""
    try:
        public_host = str(urlsplit(public_base).hostname or "").lower()
    except Exception:
        public_host = ""
    external_public_base = bool(
        public_base
        and public_host not in {"localhost", "127.0.0.1", "::1"}
    )
    externally_bound = bool(
        not external_public_base
        or bind_host in {"0.0.0.0", "::", "[::]"}
    )
    libtorrent_ready = importlib.util.find_spec("libtorrent") is not None
    pyav_ready = importlib.util.find_spec("av") is not None
    runtime_ready = bool(media_server_ready())
    storage: dict[str, Any] = {}
    capacity: dict[str, Any] = {}
    if libtorrent_ready:
        try:
            torrent_manager = get_torrent_manager()
            storage = torrent_manager.storage_status()
            capacity = torrent_manager.capacity_status(guild_id=int(guild.id))
        except Exception:
            storage = {}
            capacity = {}

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
    if not externally_bound:
        blockers.append(
            "Public Movie Night media is configured, but DANK_MEDIA_BIND_HOST is not externally reachable. "
            "Use 0.0.0.0 on Discloud/site hosting."
        )
    if not libtorrent_ready:
        blockers.append("The pinned libtorrent runtime is not installed.")
    if not pyav_ready:
        blockers.append("The pinned PyAV metadata runtime is not installed.")
    free_bytes = _safe_int(storage.get("free_bytes"), 0)
    max_file_bytes = _safe_int(storage.get("max_file_bytes"), 0)
    if free_bytes > 0 and max_file_bytes > 0 and free_bytes < min(max_file_bytes, 2 * 1024 ** 3):
        warnings.append(
            "Torrent storage is low; larger Movie Night releases may not fit this host."
        )
    if capacity and not bool(capacity.get("admission_allowed", False)):
        blocker = str(capacity.get("blocker") or "").strip()
        if blocker:
            warnings.append(
                "New unique Movie Night media is temporarily admission-blocked: "
                + blocker
            )
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
        "storage": storage,
        "capacity": capacity,
        "bind_host": bind_host,
        "bind_port": bind_port,
        "externally_bound": externally_bound,
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
        title="🍿 Dank Cinema • Setup",
        description=(
            "**Home › Community & Engagement › Dank Cinema › Setup**\n"
            "This page validates the full Dank Cinema chain before a room is allowed to launch."
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
            f"{_status(ready['externally_bound'])} • bind {ready['bind_host']}:{ready['bind_port']}\n"
            f"{_status(ready['runtime_ready'])} • public media server process"
        ),
        inline=False,
    )
    storage = ready.get("storage") if isinstance(ready.get("storage"), Mapping) else {}
    if storage:
        embed.add_field(
            name="4 • Storage / movie size",
            value=(
                f"Per-movie cap: **{_format_bytes(storage.get('max_file_bytes'))}**\n"
                f"Torrent budget: **{_format_bytes(storage.get('max_torrent_bytes'))}**\n"
                f"Free disk now: **{_format_bytes(storage.get('free_bytes'))}**"
            ),
            inline=False,
        )

    capacity = ready.get("capacity") if isinstance(ready.get("capacity"), Mapping) else {}
    if capacity:
        rss = capacity.get("current_rss_mb")
        rss_text = "unknown" if rss is None else f"{float(rss):.0f} MB"
        headroom = capacity.get("memory_headroom_mb")
        headroom_text = (
            "unknown"
            if headroom is None
            else f"{max(0.0, float(headroom)):.0f} MB"
        )
        embed.add_field(
            name="5 • Media capacity",
            value=(
                f"Process RSS: **{rss_text} / {int(capacity.get('process_limit_mb') or 0)} MB**\n"
                f"Protected reserve: **{int(capacity.get('protected_reserve_mb') or 0)} MB**\n"
                f"Protected headroom: **{headroom_text}**\n"
                f"Adaptive next-session estimate: **{int(capacity.get('estimated_session_mb') or 0)} MB** "
                f"• samples: **{int(capacity.get('memory_samples') or 0)}**\n"
                f"Unique torrents: **{int(capacity.get('active_unique_sessions') or 0)}** "
                f"• leases: **{int(capacity.get('total_leases') or 0)}** "
                f"• shared: **{int(capacity.get('shared_sessions') or 0)}**\n"
                f"Global slots: **{int(capacity.get('session_slots_available') or 0)}** "
                f"• soft/hard: **{int(capacity.get('soft_session_limit') or 0)}"
                f"/{int(capacity.get('hard_session_limit') or 0)}**\n"
                f"This server: **{int(capacity.get('guild_unique_sessions') or 0)}"
                f"/{int(capacity.get('per_guild_limit') or 0)} unique** "
                f"• slots: **{int(capacity.get('guild_slots_available') or 0)}**\n"
                f"Disk reserve: **{_format_bytes(capacity.get('disk_reserve_bytes'))}** "
                f"• committed: **{_format_bytes(capacity.get('committed_file_bytes'))}**"
            )[:1024],
            inline=False,
        )

    embed.add_field(
        name="6 • Dank Cinema providers",
        value=(
            f"{'✅' if tmdb_catalog_ready() else '⚠️'} **Dank Catalog** • powered by TMDB "
            f"({'ready' if tmdb_catalog_ready() else 'bot token not configured'})\n"
            f"✅ **Dank Archive** • {INTERNET_ARCHIVE_SOURCE_LABEL}\n"
            f"✅ **Dank Direct** • magnet links + .torrent files\n"
            f"🧩 **Provider Lab** • {ready['sources']} custom configured • {ready['enabled_sources']} enabled"
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
            f"{_CINEMA_FOOTER} • ready to launch"
            if ready["launch_ready"]
            else f"{_CINEMA_FOOTER} • fix every red blocker before launch"
        )
    )
    return embed


async def _sources_state(
    guild_id: int,
) -> tuple[Mapping[str, Any], MediaSourceRegistry]:
    return await load_media_source_registry(int(guild_id), refresh=True)


def _sources_embed(registry: MediaSourceRegistry) -> discord.Embed:
    catalog_ready = tmdb_catalog_ready()
    embed = discord.Embed(
        title="🎞️ Dank Cinema • Provider Deck",
        description=(
            f"**{_CINEMA_TAGLINE}**\n"
            "Regular members only use **Find Movie**. Dank Cinema handles catalog matching, "
            "provider discovery, release ranking, magnets, and .torrent plumbing behind the scenes."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🔎 Dank Catalog",
        value=(
            f"{'✅' if catalog_ready else '⚠️'} Exact movie matching "
            f"{'ready' if catalog_ready else 'needs the bot-owner catalog token'}\n"
            "**Powered by TMDB** for title, year, poster, overview, and movie identity. "
            "Catalog metadata never pretends to be the playable movie."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎬 Dank Archive",
        value=(
            f"✅ Built-in playable search via **{INTERNET_ARCHIVE_SOURCE_LABEL}** • no API key.\n"
            "Public-domain-focused playback feeds the same Dank Cinema release, vote, and stream engine."
        ),
        inline=False,
    )
    embed.add_field(
        name="📡 Dank Watch",
        value=(
            f"{'✅' if catalog_ready else '⚠️'} Legal availability discovery "
            f"{'ready' if catalog_ready else 'activates with Dank Catalog'}\n"
            "**Availability data by JustWatch via TMDB.** Free/ad-supported, subscription, "
            "rent, and buy options stay informational and are never disguised as direct streams."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚡ Dank Engine",
        value=(
            "Provider searches run through one Movie Night pipeline: normalize → dedupe → "
            "rank releases → vote → libtorrent verification/streaming. A provider can fail "
            "without replacing the direct magnet/.torrent path."
        ),
        inline=False,
    )
    embed.add_field(
        name="🧲 Dank Direct",
        value=(
            "✅ **Magnet links** • /movie magnet:<link>\n"
            "✅ **.torrent files** • /movie torrent:<file>\n"
            "Provider search can fail spectacularly and the host can still feed Dank Cinema media directly."
        ),
        inline=False,
    )
    embed.add_field(
        name="🧩 Dank Provider Lab",
        value=(
            "Advanced owners can add either kind of custom provider:\n"
            "• **Add JSON Provider** — authorized HTTPS JSON results feed playable releases into Dank Engine.\n"
            "• **Add Search Link** — opens the provider's own movie-results page without scraping it.\n"
            "Paste a working search URL after searching once for **Batman**; Dank Cinema detects common "
            "query parameters and keeps the internal provider identity hidden."
        ),
        inline=False,
    )
    if not registry.sources:
        embed.add_field(
            name="📚 Custom Providers",
            value="None added. Built-in search and direct magnet/.torrent playback still work.",
            inline=False,
        )
    else:
        rows = []
        for source in registry.sources:
            state = "✅" if source.enabled else "⏸️"
            mode = "JSON" if source.provider_type == PROVIDER_TYPE_JSON else "Search link"
            rows.append(
                f"{state} **{source.label}** • {mode}\n"
                f"↳ {source.endpoint_url[:180]}"
            )

        visible_rows: list[str] = []
        for index, row in enumerate(rows):
            hidden_after = len(rows) - (index + 1)
            suffix = f"\n… +{hidden_after} more provider(s)" if hidden_after else ""
            candidate = "\n".join([*visible_rows, row])
            if len(candidate) + len(suffix) > 1024:
                break
            visible_rows.append(row)

        hidden = len(rows) - len(visible_rows)
        provider_value = "\n".join(visible_rows)
        if hidden:
            provider_value += f"\n… +{hidden} more provider(s)"
        embed.add_field(
            name=f"📚 Custom Providers • {len(registry.sources)}",
            value=provider_value,
            inline=False,
        )
    embed.add_field(
        name="🔒 Provider Safety",
        value=(
            "**JSON providers** must use HTTPS and return structured results with playable media refs. "
            "**Search-link providers** only open the provider's own result page and are never scraped. "
            "Do not put passwords, API secrets, or private-network addresses in either URL."
        ),
        inline=False,
    )
    embed.set_footer(text=f"{_CINEMA_FOOTER} • provider revision {registry.revision}")
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
            title="🍿 Dank Cinema",
            description=(
                "No room is active in this channel. Start one, then use **Find Movie** or "
                "`/movie magnet:` / `/movie torrent:` to choose the media."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Room flow",
            value=(
                "1. **Start / Join**\n"
                "2. **Find Movie** or provide a magnet/.torrent\n"
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
        title="🍿 Dank Cinema • Now Showing",
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
        current_candidate = (
            room.candidates.get(room.current_candidate_id)
            if room.current_candidate_id
            else None
        )
        current_variant = (
            current_candidate.variants.get(room.current_variant_id)
            if current_candidate is not None and room.current_variant_id
            else None
        )
        if current_candidate is not None and current_variant is not None:
            health = current_variant.swarm_health
            media_value = (
                f"**{current_candidate.title}** • "
                f"{_release_source_label(current_variant.metadata)} • "
                f"{_format_bytes(current_variant.file_size)}\n"
                f"🌱 {health['seeds']} seeds • 🧲 {health['leechers']} leeches • "
                f"👥 {health['peers']} peers\n"
                "Use **Watch** for the synchronized full-video player."
            )
        else:
            media_value = (
                "Torrent/media session attached. Use **Watch** for the synchronized "
                "full-video player."
            )
        embed.add_field(
            name="Media",
            value=media_value[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Media",
            value="No media attached yet. Search or provide a magnet/.torrent.",
            inline=False,
        )
    embed.set_footer(text=f"{_CINEMA_FOOTER} • room {room.room_id}")
    return embed


def _queue_embed(room: MovieNightRoom) -> discord.Embed:
    manager = get_movie_night_manager()
    embed = discord.Embed(
        title="📺 Dank Cinema • Queue",
        color=discord.Color.blurple(),
    )
    queued = [
        room.candidates[candidate_id]
        for candidate_id in room.queue
        if candidate_id in room.candidates
    ]
    if not queued:
        embed.description = (
            "The shared queue is empty. Open **Movie Picks**, choose a movie, and use "
            "**Vote to Queue**."
        )
        return embed

    active = manager.active_viewers(room)
    lines: list[str] = []
    for index, candidate in enumerate(queued[:15], start=1):
        variants = manager.ranked_variants(room.room_id, candidate.candidate_id)
        best = variants[0] if variants else None
        if best is None:
            lines.append(
                f"**{index}. {candidate.title}** • {len(candidate.votes & active)} movie vote(s)"
            )
            continue
        health = best.swarm_health
        source = _release_source_label(best.metadata)
        lines.append(
            f"**{index}. {candidate.title}** • {source} • {_format_bytes(best.file_size)}\n"
            f"↳ 🌱 {health['seeds']} • 🧲 {health['leechers']} • "
            f"🗳️ {len(best.votes & active)} release vote(s)"
        )
    embed.description = "\n".join(lines)[:4000]
    return embed



def _candidate_embed(room: MovieNightRoom, candidate: Any) -> discord.Embed:
    manager = get_movie_night_manager()
    active = manager.active_viewers(room)
    movie_votes = len(candidate.votes & active)
    variants = manager.ranked_variants(room.room_id, candidate.candidate_id)

    embed = discord.Embed(
        title=f"🎬 Dank Cinema • {candidate.title}",
        description=(
            f"Movie votes: **{movie_votes}** • Releases: **{len(variants)}**\n"
            "Release ordering favors live seeds and swarm health when votes are tied."
        ),
        color=discord.Color.blurple(),
    )
    lines: list[str] = []
    for index, variant in enumerate(variants[:8], start=1):
        health = variant.swarm_health
        source = _release_source_label(
            variant.metadata if isinstance(variant.metadata, Mapping) else {}
        )
        lines.append(
            f"**{index}. {source}** • {_format_bytes(variant.file_size)} • "
            f"🌱 {health['seeds']} • 🧲 {health['leechers']} • "
            f"👥 {health['peers']} • 🗳️ {len(variant.votes & active)}"
        )
    catalog = (
        candidate.metadata.get("catalog")
        if isinstance(candidate.metadata, Mapping)
        and isinstance(candidate.metadata.get("catalog"), Mapping)
        else {}
    )
    if catalog:
        year = _safe_int(catalog.get("year"), 0)
        overview = _compact(catalog.get("overview"), 900)
        catalog_id = _compact(catalog.get("catalog_id"), 40)
        embed.add_field(
            name="Catalog match",
            value=(
                f"TMDB: **{catalog_id or 'unknown'}**"
                + (f" • **{year}**" if year else "")
                + (f"\n{overview}" if overview else "")
            )[:1024],
            inline=False,
        )
        poster_url = str(catalog.get("poster_url") or "").strip()
        if poster_url.startswith("https://image.tmdb.org/"):
            embed.set_thumbnail(url=poster_url)

        watch = catalog.get("watch") if isinstance(catalog.get("watch"), Mapping) else {}
        if watch:
            watch_lines: list[str] = []
            for key, label in (
                ("free", "Free"),
                ("ads", "Free with ads"),
                ("flatrate", "Subscription"),
                ("rent", "Rent"),
                ("buy", "Buy"),
            ):
                names = watch.get(key)
                if isinstance(names, list):
                    clean_names = [_compact(name, 50) for name in names if _compact(name, 50)]
                    if clean_names:
                        watch_lines.append(f"**{label}:** {', '.join(clean_names[:8])}")
            link = str(watch.get("link") or "").strip()
            if link.startswith("https://www.themoviedb.org/"):
                watch_lines.append(f"[View provider details on TMDB]({link})")
            if watch_lines:
                watch_lines.append("*Availability data: JustWatch via TMDB.*")
                embed.add_field(
                    name=f"📡 Dank Watch • {watch.get('region') or 'region'}",
                    value="\n".join(watch_lines)[:1024],
                    inline=False,
                )

    embed.add_field(
        name="Top releases",
        value="\n".join(lines)[:1024] if lines else (
            "No playable release is attached yet. The host can still provide a magnet or .torrent."
        ),
        inline=False,
    )
    if candidate.candidate_id in room.queue:
        embed.add_field(name="Queue", value="✅ This movie is queued.", inline=False)
    return embed


def _release_embed(room: MovieNightRoom, candidate: Any, variant: Any) -> discord.Embed:
    manager = get_movie_night_manager()
    active = manager.active_viewers(room)
    health = variant.swarm_health
    metadata = variant.metadata if isinstance(variant.metadata, Mapping) else {}
    release = metadata.get("release_name") if isinstance(metadata.get("release_name"), Mapping) else {}
    source_reported = (
        metadata.get("source_reported")
        if isinstance(metadata.get("source_reported"), Mapping)
        else {}
    )
    verified = metadata.get("verified") if isinstance(metadata.get("verified"), Mapping) else {}

    source = _release_source_label(metadata)
    hint = _release_hint_label(metadata)
    embed = discord.Embed(
        title=f"🎞️ {candidate.title} • {source}",
        description=(
            f"Release votes: **{len(variant.votes & active)}**\n"
            f"Source: **{variant.source_label or variant.source_id or 'Custom source'}**"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Swarm",
        value=(
            f"🌱 Seeds: **{health['seeds']}**\n"
            f"🧲 Leeches: **{health['leechers']}**\n"
            f"👥 Peers: **{health['peers']}**\n"
            f"Health: **{health['label']}** • ratio **{health['seed_leech_ratio']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="File",
        value=(
            f"Size: **{_format_bytes(variant.file_size)}**\n"
            f"Release: **{source}** *(inferred)*\n"
            f"{hint or 'Quality details pending file verification'}"
        )[:1024],
        inline=True,
    )

    if source_reported:
        source_lines = [
            f"• **{_compact(key, 40)}:** {_compact(value, 100)}"
            for key, value in list(source_reported.items())[:6]
        ]
        embed.add_field(
            name="Source-reported metadata • not yet verified",
            value="\n".join(source_lines)[:1024],
            inline=False,
        )

    if verified:
        video = verified.get("video") if isinstance(verified.get("video"), Mapping) else {}
        audio = verified.get("audio_tracks") if isinstance(verified.get("audio_tracks"), list) else []
        embed.add_field(
            name="Verified from selected media",
            value=(
                f"Duration: **{verified.get('duration') or 'unknown'}**\n"
                f"Video: **{video.get('resolution') or 'unknown'}** • "
                f"**{video.get('codec') or 'unknown'}**\n"
                f"Audio tracks: **{len(audio)}**"
            )[:1024],
            inline=False,
        )

    try:
        file_cap = int(get_torrent_manager().max_file_bytes)
    except Exception:
        file_cap = 0
    if file_cap > 0 and variant.file_size > file_cap:
        embed.add_field(
            name="⚠️ Host compatibility",
            value=(
                f"This release is **{_format_bytes(variant.file_size)}**, above the current "
                f"Movie Night per-file cap of **{_format_bytes(file_cap)}**. "
                "Choose another release or raise the configured cap on a host with enough disk."
            )[:1024],
            inline=False,
        )

    embed.set_footer(
        text=(
            "Release/source labels are inferred from naming until the actual file is probed."
        )
    )
    return embed


def _materialize_search_results(
    room: MovieNightRoom,
    outcome: MediaSourceSearchOutcome,
    *,
    proposer_id: int,
    query: str,
    catalog_metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[int, int]:
    manager = get_movie_night_manager()
    candidate_ids: set[str] = set()
    release_count = 0

    for result in outcome.variants:
        candidate = manager.find_candidate_by_title(room.room_id, result.title)
        catalog = (
            dict(catalog_metadata)
            if isinstance(catalog_metadata, Mapping)
            and _compact(catalog_metadata.get("title")).casefold() == result.title.casefold()
            else {}
        )
        candidate_metadata: dict[str, Any] = {"search_query": query}
        if catalog:
            candidate_metadata["catalog"] = catalog

        if candidate is None:
            candidate = manager.nominate(
                room.room_id,
                user_id=int(proposer_id),
                title=result.title,
                metadata=candidate_metadata,
                auto_vote=False,
            )
        elif catalog:
            candidate.metadata.update(candidate_metadata)
        candidate_ids.add(candidate.candidate_id)
        manager.add_variant(
            room.room_id,
            candidate.candidate_id,
            user_id=int(proposer_id),
            source_ref=result.source_ref,
            source_id=result.source_id,
            source_label=result.source_label,
            file_size=result.file_size,
            peers=result.peers,
            seeds=result.seeds,
            leechers=result.leechers,
            metadata=result.metadata,
            auto_vote=False,
        )
        release_count += 1

    return len(candidate_ids), release_count


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/movie` panel to use these controls.")
        return False


class ExternalSearchResultsView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        room_id: str,
        query: str,
        links: list[tuple[str, str]],
        candidate_id: str = "",
    ) -> None:
        super().__init__(owner_id)
        self.room_id = str(room_id)
        self.query = _compact(query)
        self.candidate_id = str(candidate_id or "")

        for index, (label, url) in enumerate(links[:20]):
            self.add_item(
                discord.ui.Button(
                    label=_compact(label, 80) or "Open provider",
                    emoji="🔎",
                    style=discord.ButtonStyle.link,
                    url=url,
                    row=index // 5,
                )
            )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.candidate_id:
            return await _open_candidate_detail(
                interaction,
                self.room_id,
                self.candidate_id,
            )
        await open_movie_night(interaction, replace_message=True)


async def _external_provider_links(
    guild_id: int,
    query: str,
) -> list[tuple[str, str]]:
    _raw, registry = await _sources_state(int(guild_id))
    links: list[tuple[str, str]] = []
    for source in enabled_external_sources(registry):
        try:
            url = render_provider_search_url(source.endpoint_url, query)
        except ValueError:
            continue
        links.append((source.label, url))
        if len(links) >= 20:
            break
    return links


async def _open_external_search_results(
    interaction: discord.Interaction,
    *,
    room_id: str,
    query: str,
    candidate_id: str = "",
) -> bool:
    room = get_movie_night_manager().get(room_id)
    if room is None:
        await _private(interaction, "❌ This Movie Night room no longer exists.")
        return False

    links = await _external_provider_links(int(room.guild_id), query)
    if not links:
        await _private(
            interaction,
            "ℹ️ No external search-link providers are enabled for this server.",
        )
        return False

    embed = discord.Embed(
        title="🔗 Dank Cinema • Search Elsewhere",
        description=(
            f"Search **{_compact(query)}** on an enabled provider's own results page.\n"
            "These buttons only open external search pages. Dank Cinema does not scrape, "
            "copy, or treat those pages as playable releases."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="External providers",
        value="\n".join(f"• {label}" for label, _url in links)[:1024],
        inline=False,
    )
    embed.set_footer(text=f"{_CINEMA_FOOTER} • external search")
    await _replace(
        interaction,
        embed=embed,
        view=ExternalSearchResultsView(
            int(interaction.user.id),
            room_id=room.room_id,
            query=query,
            links=links,
            candidate_id=candidate_id,
        ),
    )
    return True


class MovieCandidateView(_OwnedView):
    def __init__(self, owner_id: int, room_id: str, candidate_id: str) -> None:
        super().__init__(owner_id)
        self.room_id = str(room_id)
        self.candidate_id = str(candidate_id)

    @discord.ui.button(label="Vote / Unvote Movie", emoji="🗳️", style=discord.ButtonStyle.primary, row=0)
    async def vote_movie(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        manager = get_movie_night_manager()
        room = manager.get(self.room_id)
        if room is None:
            return await _private(interaction, "❌ This Movie Night room no longer exists.")
        manager.join_room(self.room_id, user_id=int(interaction.user.id))
        candidate = room.candidates.get(self.candidate_id)
        if candidate is None:
            return await _private(interaction, "❌ That movie result no longer exists.")
        approve = int(interaction.user.id) not in candidate.votes
        manager.vote_candidate(
            self.room_id,
            self.candidate_id,
            user_id=int(interaction.user.id),
            approve=approve,
        )
        await _replace(
            interaction,
            embed=_candidate_embed(room, candidate),
            view=MovieCandidateView(self.owner_id, self.room_id, self.candidate_id),
        )

    @discord.ui.button(label="Choose Release", emoji="🎞️", style=discord.ButtonStyle.success, row=0)
    async def releases(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_release_picker(interaction, self.room_id, self.candidate_id)

    @discord.ui.button(label="Vote to Queue", emoji="📺", style=discord.ButtonStyle.secondary, row=0)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        manager = get_movie_night_manager()
        room = manager.get(self.room_id)
        if room is None:
            return await _private(interaction, "❌ This Movie Night room no longer exists.")
        manager.join_room(self.room_id, user_id=int(interaction.user.id))
        try:
            vote = manager.propose_vote(
                self.room_id,
                proposer_id=int(interaction.user.id),
                action="queue",
                payload={"candidate_id": self.candidate_id},
            )
        except Exception as exc:
            return await _private(interaction, f"❌ Queue vote could not start: {exc}")
        if vote.resolved and vote.passed:
            return await _private(interaction, "✅ Movie added to the shared queue.")
        await _private(
            interaction,
            "🗳️ Queue vote opened. Other active viewers can vote from /movie.",
        )

    @discord.ui.button(label="Search Elsewhere", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def external_search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room = get_movie_night_manager().get(self.room_id)
        if room is None:
            return await _private(interaction, "❌ This Movie Night room no longer exists.")
        candidate = room.candidates.get(self.candidate_id)
        if candidate is None:
            return await _private(interaction, "❌ That movie result no longer exists.")
        await _open_external_search_results(
            interaction,
            room_id=room.room_id,
            query=candidate.title,
            candidate_id=candidate.candidate_id,
        )

    @discord.ui.button(label="Back to Results", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_results(interaction, self.room_id, replace_message=True)


class MovieReleaseView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        room_id: str,
        candidate_id: str,
        variant_id: str,
    ) -> None:
        super().__init__(owner_id)
        self.room_id = str(room_id)
        self.candidate_id = str(candidate_id)
        self.variant_id = str(variant_id)

    def _resolve(self) -> tuple[Optional[MovieNightRoom], Any, Any]:
        manager = get_movie_night_manager()
        room = manager.get(self.room_id)
        if room is None:
            return None, None, None
        candidate = room.candidates.get(self.candidate_id)
        if candidate is None:
            return room, None, None
        return room, candidate, candidate.variants.get(self.variant_id)

    @discord.ui.button(label="Vote / Unvote Release", emoji="🗳️", style=discord.ButtonStyle.primary, row=0)
    async def vote_release(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room, candidate, variant = self._resolve()
        if room is None or candidate is None or variant is None:
            return await _private(interaction, "❌ That Movie Night release no longer exists.")
        manager = get_movie_night_manager()
        manager.join_room(self.room_id, user_id=int(interaction.user.id))
        approve = int(interaction.user.id) not in variant.votes
        manager.vote_variant(
            self.room_id,
            self.candidate_id,
            self.variant_id,
            user_id=int(interaction.user.id),
            approve=approve,
        )
        await _replace(
            interaction,
            embed=_release_embed(room, candidate, variant),
            view=MovieReleaseView(
                self.owner_id,
                self.room_id,
                self.candidate_id,
                self.variant_id,
            ),
        )

    @discord.ui.button(label="Play / Request This Release", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room, candidate, variant = self._resolve()
        if room is None or candidate is None or variant is None:
            return await _private(interaction, "❌ That Movie Night release no longer exists.")

        manager = get_movie_night_manager()
        manager.join_room(self.room_id, user_id=int(interaction.user.id))
        host_active = manager.host_active(room)

        if int(interaction.user.id) == int(room.host_id):
            return await _start_variant_source(
                interaction,
                room,
                self.candidate_id,
                self.variant_id,
                authorized_by_vote=False,
            )

        if host_active:
            return await _private(
                interaction,
                "ℹ️ The host is active. Vote for this release or queue the movie; "
                "only the active host can replace what is playing.",
            )

        try:
            vote = manager.propose_vote(
                self.room_id,
                proposer_id=int(interaction.user.id),
                action="play_variant",
                payload={
                    "candidate_id": self.candidate_id,
                    "variant_id": self.variant_id,
                },
            )
        except Exception as exc:
            return await _private(interaction, f"❌ Playback vote could not start: {exc}")

        if vote.resolved and vote.passed:
            return await _execute_passed_vote(interaction, room, vote)

        await _private(
            interaction,
            "🗳️ Host-away playback vote opened for this release. "
            "Other active viewers can approve it from /movie.",
        )

    @discord.ui.button(label="Vote to Queue Movie", emoji="📺", style=discord.ButtonStyle.secondary, row=1)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        manager = get_movie_night_manager()
        room = manager.get(self.room_id)
        if room is None:
            return await _private(interaction, "❌ This Movie Night room no longer exists.")
        manager.join_room(self.room_id, user_id=int(interaction.user.id))
        vote = manager.propose_vote(
            self.room_id,
            proposer_id=int(interaction.user.id),
            action="queue",
            payload={"candidate_id": self.candidate_id},
        )
        if vote.resolved and vote.passed:
            return await _private(interaction, "✅ Movie added to the shared queue.")
        await _private(interaction, "🗳️ Queue vote opened.")

    @discord.ui.button(label="Back to Releases", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_release_picker(interaction, self.room_id, self.candidate_id)


async def _open_candidate_detail(
    interaction: discord.Interaction,
    room_id: str,
    candidate_id: str,
) -> None:
    manager = get_movie_night_manager()
    room = manager.get(room_id)
    if room is None:
        return await _private(interaction, "❌ This Movie Night room no longer exists.")
    candidate = room.candidates.get(str(candidate_id))
    if candidate is None:
        return await _private(interaction, "❌ That movie result no longer exists.")
    await _replace(
        interaction,
        embed=_candidate_embed(room, candidate),
        view=MovieCandidateView(
            int(interaction.user.id),
            room.room_id,
            candidate.candidate_id,
        ),
    )


async def _open_release_picker(
    interaction: discord.Interaction,
    room_id: str,
    candidate_id: str,
) -> None:
    manager = get_movie_night_manager()
    room = manager.get(room_id)
    if room is None:
        return await _private(interaction, "❌ This Movie Night room no longer exists.")
    candidate = room.candidates.get(str(candidate_id))
    if candidate is None:
        return await _private(interaction, "❌ That movie result no longer exists.")

    variants = manager.ranked_variants(room.room_id, candidate.candidate_id)
    if not variants:
        return await _private(interaction, "ℹ️ No playable releases were returned for this movie.")

    async def picked(pick_interaction: discord.Interaction, value: str) -> None:
        variant = candidate.variants.get(value)
        if variant is None:
            return await _private(pick_interaction, "❌ That release no longer exists.")
        await _replace(
            pick_interaction,
            embed=_release_embed(room, candidate, variant),
            view=MovieReleaseView(
                int(pick_interaction.user.id),
                room.room_id,
                candidate.candidate_id,
                variant.variant_id,
            ),
        )

    choices: list[DankChoice] = []
    for variant in variants[:25]:
        label, description = _variant_choice_text(variant)
        choices.append(
            DankChoice(
                label=label,
                value=variant.variant_id,
                description=description,
                emoji="🎞️",
                default=variant.variant_id == candidate.selected_variant_id,
            )
        )

    picker = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=picked,
        custom_id=f"dank:movie:release:{candidate.candidate_id[:16]}",
        placeholder="Choose a release / quality…",
        title=f"Releases • {candidate.title[:70]}",
        on_home=lambda back_interaction: _open_candidate_detail(
            back_interaction,
            room.room_id,
            candidate.candidate_id,
        ),
        home_label="Movie",
    )
    await _replace(
        interaction,
        embed=_candidate_embed(room, candidate),
        view=picker,
    )


async def open_movie_results(
    interaction: discord.Interaction,
    room_id: str,
    *,
    replace_message: bool = True,
) -> None:
    manager = get_movie_night_manager()
    room = manager.get(room_id)
    if room is None:
        return await _private(interaction, "❌ This Movie Night room no longer exists.")

    ranked = manager.ranked_candidates(room.room_id)
    if not ranked:
        message = "ℹ️ No Movie Night search results are loaded yet."
        if replace_message:
            return await _replace(
                interaction,
                content=message,
                embed=_room_embed(interaction, room),
                view=MovieNightHubView(int(interaction.user.id)),
            )
        return await _private(interaction, message)

    active = manager.active_viewers(room)
    choices: list[DankChoice] = []
    for candidate in ranked[:25]:
        variants = manager.ranked_variants(room.room_id, candidate.candidate_id)
        best = variants[0] if variants else None
        if best is None:
            description = f"{len(candidate.votes & active)} movie vote(s) • no release"
        else:
            health = best.swarm_health
            description = (
                f"{len(variants)} releases • {health['seeds']} seeds • "
                f"{health['leechers']} leeches"
            )
        choices.append(
            DankChoice(
                label=candidate.title[:100],
                value=candidate.candidate_id,
                description=description[:100],
                emoji="🎬",
                default=candidate.candidate_id in room.queue,
            )
        )

    async def picked(pick_interaction: discord.Interaction, value: str) -> None:
        await _open_candidate_detail(pick_interaction, room.room_id, value)

    picker = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=picked,
        custom_id=f"dank:movie:results:{room.room_id[:16]}",
        placeholder="Choose a movie result…",
        title="Dank Cinema Results",
        on_home=lambda back_interaction: open_movie_night(
            back_interaction,
            replace_message=True,
        ),
        home_label="Dank Cinema",
    )
    embed = discord.Embed(
        title="🔎 Dank Cinema • Search Results",
        description=(
            f"Approved search: **{room.approved_search_query or '—'}**\n"
            f"Movies: **{len(ranked)}** • "
            f"Choose a title, then compare releases by seeds, leeches, size, metadata, and votes."
        ),
        color=discord.Color.blurple(),
    )
    if replace_message:
        await _replace(interaction, embed=embed, view=picker)
    else:
        await _private(interaction, embed=embed, view=picker)


class CustomSourceModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        owner_id: int,
        baseline: Mapping[str, Any],
        source: Optional[CustomMediaSource] = None,
    ) -> None:
        super().__init__(
            title="Edit Dank Provider" if source is not None else "Add Dank Provider",
            timeout=300,
        )
        self.owner_id = int(owner_id)
        self.baseline = dict(baseline)
        self.source_id = str(source.source_id if source is not None else "")

        self.label_input = discord.ui.TextInput(
            label="Provider name (optional)",
            placeholder="My Movie Feed",
            default=str(source.label if source is not None else "")[:80] or None,
            required=False,
            max_length=80,
        )
        self.endpoint_input = discord.ui.TextInput(
            label="Provider search URL",
            placeholder="https://api.example.com/search?q=batman",
            default=str(source.endpoint_url if source is not None else "")[:1000] or None,
            min_length=8,
            max_length=1000,
        )
        self.add_item(self.label_input)
        self.add_item(self.endpoint_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ This source editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Movie Night sources are configured inside a server.")
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")

        from stoney_verify.media_source_registry import parse_media_source_registry

        current = parse_media_source_registry(self.baseline)
        try:
            prepared_url = prepare_example_search_url(str(self.endpoint_input.value))
            host = str(urlsplit(prepared_url).hostname or "").strip(".")
            fallback_label = host.split(".", 1)[0].replace("-", " ").replace("_", " ").title()
            label = _compact(self.label_input.value, 80) or fallback_label or "Custom Movies"
            updated = add_custom_source(
                current,
                source_id=self.source_id,
                label=label,
                endpoint_url=prepared_url,
                added_by=int(interaction.user.id),
            )
        except ValueError as exc:
            return await _private(interaction, f"❌ {exc}")

        candidate = next(
            (
                item
                for item in updated.sources
                if (self.source_id and item.source_id == self.source_id)
                or (not self.source_id and item.endpoint_url == prepared_url and item.label == label)
            ),
            None,
        )
        if candidate is None:
            return await _private(interaction, "❌ Dank Cinema could not prepare that provider.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        probe = await probe_custom_media_source(candidate, query="batman")
        if not probe.reachable:
            return await _replace(
                interaction,
                content=(
                    "❌ **Provider was not saved.** Dank Cinema tested the URL and could not use it.\n"
                    f"{probe.error}"
                )[:2000],
                embed=_sources_embed(current),
                view=MovieNightSourcesView(int(interaction.user.id)),
            )

        try:
            applied, _saved = await save_media_source_registry(
                int(guild.id),
                expected_config=self.baseline,
                updated=updated,
            )
        except Exception as exc:
            return await _replace(
                interaction,
                content=f"❌ Dank Cinema provider could not save safely: {type(exc).__name__}.",
                embed=_sources_embed(current),
                view=MovieNightSourcesView(int(interaction.user.id)),
            )
        if not applied:
            return await _replace(
                interaction,
                content="❌ Dank Cinema providers changed in another admin session. Refresh and try again.",
                embed=_sources_embed(current),
                view=MovieNightSourcesView(int(interaction.user.id)),
            )

        notice = "✅ Dank provider tested and saved."
        if probe.playable_results == 0:
            notice = (
                "⚠️ Provider responded with valid JSON and was saved, but the Batman test "
                "returned no playable results. Try a title you know exists in that source."
            )
        await _replace(
            interaction,
            content=notice,
            embed=_sources_embed(updated),
            view=MovieNightSourcesView(int(interaction.user.id)),
        )



class ExternalSearchProviderModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        owner_id: int,
        baseline: Mapping[str, Any],
        source: Optional[CustomMediaSource] = None,
    ) -> None:
        super().__init__(
            title="Edit Search-Link Provider" if source is not None else "Add Search-Link Provider",
            timeout=300,
        )
        self.owner_id = int(owner_id)
        self.baseline = dict(baseline)
        self.source_id = str(source.source_id if source is not None else "")

        self.label_input = discord.ui.TextInput(
            label="Provider name (optional)",
            placeholder="Public Movie Catalog",
            default=str(source.label if source is not None else "")[:80] or None,
            required=False,
            max_length=80,
        )
        self.endpoint_input = discord.ui.TextInput(
            label="Working provider search URL",
            placeholder="https://movies.example/search?q=batman",
            default=str(source.endpoint_url if source is not None else "")[:1000] or None,
            min_length=8,
            max_length=1000,
        )
        self.add_item(self.label_input)
        self.add_item(self.endpoint_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ This provider editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Dank Cinema providers are configured inside a server.")
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")

        from stoney_verify.media_source_registry import parse_media_source_registry

        current = parse_media_source_registry(self.baseline)
        try:
            prepared_url = prepare_example_search_url(str(self.endpoint_input.value))
            render_provider_search_url(prepared_url, "x" * 180)
            host = str(urlsplit(prepared_url).hostname or "").strip(".")
            fallback_label = host.split(".", 1)[0].replace("-", " ").replace("_", " ").title()
            label = _compact(self.label_input.value, 80) or fallback_label or "External Search"
            updated = add_custom_source(
                current,
                source_id=self.source_id,
                label=label,
                endpoint_url=prepared_url,
                added_by=int(interaction.user.id),
                provider_type=PROVIDER_TYPE_EXTERNAL,
            )
        except ValueError as exc:
            return await _private(interaction, f"❌ {exc}")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            applied, _saved = await save_media_source_registry(
                int(guild.id),
                expected_config=self.baseline,
                updated=updated,
            )
        except Exception as exc:
            return await _replace(
                interaction,
                content=f"❌ Dank Cinema search-link provider could not save safely: {type(exc).__name__}.",
                embed=_sources_embed(current),
                view=MovieNightSourcesView(int(interaction.user.id)),
            )
        if not applied:
            return await _replace(
                interaction,
                content="❌ Dank Cinema providers changed in another admin session. Refresh and try again.",
                embed=_sources_embed(current),
                view=MovieNightSourcesView(int(interaction.user.id)),
            )

        await _replace(
            interaction,
            content=(
                "✅ Search-link provider saved. Dank Cinema will open that provider's own "
                "search-results page for the movie title; it will not scrape or ingest the page."
            ),
            embed=_sources_embed(updated),
            view=MovieNightSourcesView(int(interaction.user.id)),
        )


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

    @discord.ui.button(label="Edit Provider", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None or not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")
        raw, registry = await _sources_state(int(guild.id))
        source = next(
            (item for item in registry.sources if item.source_id == self.source_id),
            None,
        )
        if source is None:
            return await _private(interaction, "❌ That source no longer exists.")
        modal: discord.ui.Modal
        if source.provider_type == PROVIDER_TYPE_EXTERNAL:
            modal = ExternalSearchProviderModal(
                owner_id=self.owner_id,
                baseline=raw,
                source=source,
            )
        else:
            modal = CustomSourceModal(
                owner_id=self.owner_id,
                baseline=raw,
                source=source,
            )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Enable Provider", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._mutate(interaction, enabled=True)

    @discord.ui.button(label="Pause Provider", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._mutate(interaction, enabled=False)

    @discord.ui.button(label="Remove Provider", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
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
            title=f"🧩 Dank Provider • {source.label}",
            description=(
                f"State: **{'Enabled' if source.enabled else 'Disabled'}**\n"
                f"Mode: **{'Structured JSON' if source.provider_type == PROVIDER_TYPE_JSON else 'External search link'}**\n"
                f"Search URL: {source.endpoint_url}\n\n"
                "Use **Edit Provider** to change the name or URL. Dank Cinema keeps the internal "
                "source identity automatically."
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
            description=(
                ("JSON • " if source.provider_type == PROVIDER_TYPE_JSON else "Search link • ")
                + ("Enabled" if source.enabled else "Disabled")
            ),
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
        home_label="Provider Deck",
    )
    await _replace(
        interaction,
        embed=discord.Embed(
            title="🧩 Dank Cinema • Provider Lab",
            description="Manage one advanced custom provider without exposing it to regular members.",
            color=discord.Color.blurple(),
        ),
        view=view,
    )


class MovieNightSourcesView(_OwnedView):
    @discord.ui.button(label="Add JSON Provider", emoji="🧩", style=discord.ButtonStyle.success, row=0)
    async def add_json(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
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

    @discord.ui.button(label="Add Search Link", emoji="🔗", style=discord.ButtonStyle.success, row=0)
    async def add_external(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await _private(interaction, "❌ Manage Server or Administrator is required.")
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Use Movie Night inside a server.")
        raw, _registry = await _sources_state(int(guild.id))
        await interaction.response.send_modal(
            ExternalSearchProviderModal(owner_id=self.owner_id, baseline=raw)
        )

    @discord.ui.button(label="Manage Providers", emoji="🛠️", style=discord.ButtonStyle.primary, row=0)
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
        await _replace(interaction, content="Dank Cinema provider deck closed.", embed=None, view=None)


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
            current_channel_perms = _channel_permissions(guild, interaction.channel)
            can_ping_locked_role = bool(
                current_channel_perms
                and (
                    getattr(current_channel_perms, "administrator", False)
                    or getattr(current_channel_perms, "mention_everyone", False)
                )
            )
            if not current_role.mentionable and not can_ping_locked_role:
                me = guild.me
                guild_perms = getattr(me, "guild_permissions", None)
                if (
                    me is not None
                    and guild_perms is not None
                    and (
                        getattr(guild_perms, "administrator", False)
                        or getattr(guild_perms, "manage_roles", False)
                    )
                    and current_role < me.top_role
                ):
                    await current_role.edit(
                        mentionable=True,
                        reason="Dank Shield Movie Night notification readiness",
                    )
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
            mentionable=True,
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
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    health_url = base.rstrip("/") + "/health"
    timeout = aiohttp.ClientTimeout(total=6.0, connect=3.0, sock_read=4.0)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(health_url, allow_redirects=False) as response:
                if response.status != 200:
                    return await _replace(
                        interaction,
                        content=f"❌ Public media health returned HTTP {response.status}.",
                        embed=None,
                        view=MovieNightSetupView(int(interaction.user.id)),
                    )
                data = await response.json(content_type=None)
    except Exception as exc:
        return await _replace(
            interaction,
            content=f"❌ Public media endpoint is not reachable: {type(exc).__name__}.",
            embed=None,
            view=MovieNightSetupView(int(interaction.user.id)),
        )
    if not isinstance(data, Mapping) or data.get("service") != "dank_torrent_media":
        return await _replace(
            interaction,
            content="❌ The public URL responded, but it was not Dank Shield's torrent media service.",
            embed=None,
            view=MovieNightSetupView(int(interaction.user.id)),
        )
    await _replace(
        interaction,
        content="✅ Public Movie Night media endpoint is reachable and identified correctly.",
        embed=None,
        view=MovieNightSetupView(int(interaction.user.id)),
    )


class MovieNightSetupView(_OwnedView):
    @discord.ui.button(label="Create / Repair Role", emoji="🎬", style=discord.ButtonStyle.success, row=0)
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _create_or_repair_movie_role(interaction)

    @discord.ui.button(label="Provider Deck", emoji="🎞️", style=discord.ButtonStyle.primary, row=0)
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


async def _start_variant_source(
    interaction: discord.Interaction,
    room: MovieNightRoom,
    candidate_id: str,
    variant_id: str,
    *,
    authorized_by_vote: bool,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Movie Night only works inside a server.")

    room_manager = get_movie_night_manager()
    current = room_manager.get(room.room_id)
    if current is None:
        return await _private(interaction, "❌ This Movie Night room no longer exists.")
    candidate = current.candidates.get(str(candidate_id))
    if candidate is None:
        return await _private(interaction, "❌ That movie result no longer exists.")
    variant = candidate.variants.get(str(variant_id))
    if variant is None:
        return await _private(interaction, "❌ That release no longer exists.")

    if not authorized_by_vote and int(interaction.user.id) != int(current.host_id):
        return await _private(
            interaction,
            "❌ Only the active host can directly replace the Movie Night media.",
        )

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    torrent_manager = get_torrent_manager()
    if variant.file_size and int(variant.file_size) > int(torrent_manager.max_file_bytes):
        return await _replace(
            interaction,
            content=(
                f"❌ This release is {_format_bytes(variant.file_size)}, above this host's "
                f"{_format_bytes(torrent_manager.max_file_bytes)} Movie Night file cap."
            ),
            embed=_release_embed(current, candidate, variant),
            view=MovieReleaseView(
                int(interaction.user.id),
                current.room_id,
                candidate.candidate_id,
                variant.variant_id,
            ),
        )
    previous = str(current.stream_token or "")
    lease_key = movie_room_lease_key(int(current.guild_id), int(current.channel_id))
    source_ref = str(variant.source_ref or "").strip()

    try:
        if source_ref.lower().startswith("magnet:?"):
            clean_magnet = find_magnet(source_ref)
            if not clean_magnet:
                raise ValueError("This source returned an invalid magnet link.")
            session = await torrent_manager.start_magnet(
                clean_magnet,
                guild_id=int(guild.id),
                owner_id=int(current.host_id),
                replace_token=previous,
                lease_key=lease_key,
            )
        elif source_ref.lower().startswith("https://"):
            payload = await fetch_torrent_metadata(
                source_ref,
                max_bytes=torrent_manager.max_metadata_bytes,
            )
            session = await torrent_manager.start_torrent_bytes(
                payload,
                guild_id=int(guild.id),
                owner_id=int(current.host_id),
                replace_token=previous,
                lease_key=lease_key,
            )
        else:
            raise ValueError(
                "This release is not a supported magnet or HTTPS .torrent source."
            )
    except Exception as exc:
        return await _replace(
            interaction,
            content=f"❌ Selected release could not start: {type(exc).__name__}: {exc}",
            embed=_release_embed(current, candidate, variant),
            view=MovieReleaseView(
                int(interaction.user.id),
                current.room_id,
                candidate.candidate_id,
                variant.variant_id,
            ),
        )

    stream_url = torrent_manager.stream_url(session)
    if not stream_url:
        await torrent_manager.release_lease(
            session.token,
            lease_key,
            remove_if_unused=True,
        )
        return await _replace(
            interaction,
            content="❌ The torrent started but no signed public stream URL could be created.",
            embed=_release_embed(current, candidate, variant),
            view=MovieReleaseView(
                int(interaction.user.id),
                current.room_id,
                candidate.candidate_id,
                variant.variant_id,
            ),
        )

    latest_room = room_manager.get(current.room_id)
    if (
        latest_room is None
        or latest_room.ended
        or int(latest_room.host_id) != int(current.host_id)
        or str(latest_room.stream_token or "") != previous
    ):
        await torrent_manager.release_lease(
            session.token,
            lease_key,
            remove_if_unused=True,
        )
        return await _replace(
            interaction,
            content=(
                "❌ Movie Night changed while this release was loading, so the stale "
                "media result was discarded instead of overwriting the newer room state."
            ),
            embed=_room_embed(interaction, latest_room) if latest_room is not None else None,
            view=MovieNightHubView(int(interaction.user.id), latest_room),
        )

    room_manager.select_variant(
        latest_room.room_id,
        candidate.candidate_id,
        variant_id=variant.variant_id,
    )
    room_manager.set_room_media(
        latest_room.room_id,
        host_id=int(latest_room.host_id),
        stream_token=session.token,
        candidate_id=candidate.candidate_id,
        variant_id=variant.variant_id,
    )
    current = latest_room

    merged_meta = dict(variant.metadata or {})
    merged_meta["release_name"] = dict(session.release_metadata or merged_meta.get("release_name") or {})
    if session.verified_metadata:
        merged_meta["verified"] = dict(session.verified_metadata)
    variant.metadata = merged_meta
    variant.file_size = int(session.file_size or variant.file_size)

    if previous and previous != session.token:
        await torrent_manager.release_lease(
            previous,
            lease_key,
            remove_if_unused=True,
        )

    await _replace(
        interaction,
        content=(
            f"✅ Now playing **{candidate.title}** • "
            f"{_release_source_label(variant.metadata)} • {_format_bytes(variant.file_size)}\n"
            f"Dank Cinema stream: {stream_url}"
        )[:2000],
        embed=_release_embed(current, candidate, variant),
        view=MovieNightHubView(int(interaction.user.id), current),
    )


async def _execute_search_vote(
    interaction: discord.Interaction,
    room: MovieNightRoom,
    vote: Any,
) -> None:
    manager = get_movie_night_manager()
    if not manager.claim_vote_execution(room.room_id, vote.vote_id):
        return await open_movie_results(interaction, room.room_id, replace_message=True)

    query = _compact(vote.payload.get("query"))
    if not query:
        manager.set_vote_execution_error(room.room_id, vote.vote_id, "Search query was empty.")
        return await _private(interaction, "❌ The approved Movie Night search query was empty.")

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    catalog_metadata = (
        dict(vote.payload.get("catalog"))
        if isinstance(vote.payload.get("catalog"), Mapping)
        else {}
    )
    catalog_id = _compact(catalog_metadata.get("catalog_id"), 40)

    try:
        if catalog_id:
            outcome, watch = await asyncio.gather(
                search_movie_sources(int(room.guild_id), query),
                get_tmdb_watch_availability(catalog_id),
            )
            watch_metadata = watch.to_metadata()
            if any(
                watch_metadata.get(key)
                for key in ("free", "ads", "flatrate", "rent", "buy")
            ):
                catalog_metadata["watch"] = watch_metadata
                vote.payload["catalog"] = dict(catalog_metadata)
        else:
            outcome = await search_movie_sources(int(room.guild_id), query)
    except Exception as exc:
        manager.set_vote_execution_error(
            room.room_id,
            vote.vote_id,
            f"{type(exc).__name__}: {exc}",
        )
        return await _replace(
            interaction,
            content=f"❌ Movie Night source search failed: {type(exc).__name__}: {exc}",
            embed=_room_embed(interaction, room),
            view=MovieNightHubView(int(interaction.user.id)),
        )

    active = manager.active_viewers(room)
    actor_id = (
        int(vote.proposer_id)
        if int(vote.proposer_id) in active
        else int(interaction.user.id)
    )

    if not outcome.variants:
        if catalog_metadata:
            title = _compact(catalog_metadata.get("title")) or query
            candidate = manager.find_candidate_by_title(room.room_id, title)
            if candidate is None:
                candidate = manager.nominate(
                    room.room_id,
                    user_id=actor_id,
                    title=title,
                    metadata={
                        "search_query": query,
                        "catalog": dict(catalog_metadata),
                    },
                    auto_vote=False,
                )
            vote.payload["movie_count"] = 1
            vote.payload["release_count"] = 0
            if outcome.errors:
                vote.payload["source_warnings"] = tuple(outcome.errors[:10])
            return await _replace(
                interaction,
                content=(
                    f"🎬 Found **{title}** in the movie catalog, but no connected playback "
                    "provider returned a release. The host can still attach a magnet or .torrent."
                )[:2000],
                embed=_candidate_embed(room, candidate),
                view=MovieCandidateView(
                    int(interaction.user.id),
                    room.room_id,
                    candidate.candidate_id,
                ),
            )

        external_links = await _external_provider_links(int(room.guild_id), query)
        if external_links:
            vote.payload["external_provider_count"] = len(external_links)
            return await _replace(
                interaction,
                content=(
                    f"🔗 No connected provider returned a playable release for **{query}**, "
                    "but external search providers are available."
                )[:2000],
                embed=discord.Embed(
                    title="🔗 Dank Cinema • External Search Available",
                    description=(
                        "Open a provider's own search-results page below. Dank Cinema does not "
                        "scrape or ingest those pages."
                    ),
                    color=discord.Color.blurple(),
                ),
                view=ExternalSearchResultsView(
                    int(interaction.user.id),
                    room_id=room.room_id,
                    query=query,
                    links=external_links,
                ),
            )

        detail = "; ".join(outcome.errors[:4]) or "No releases were returned."
        manager.set_vote_execution_error(room.room_id, vote.vote_id, detail)
        return await _replace(
            interaction,
            content=f"ℹ️ No playable releases found for **{query}**. {detail}"[:2000],
            embed=_room_embed(interaction, room),
            view=MovieNightHubView(int(interaction.user.id)),
        )

    movies, releases = _materialize_search_results(
        room,
        outcome,
        proposer_id=actor_id,
        query=query,
        catalog_metadata=catalog_metadata,
    )

    if outcome.errors:
        vote.payload["source_warnings"] = tuple(outcome.errors[:10])
    vote.payload["movie_count"] = int(movies)
    vote.payload["release_count"] = int(releases)

    await open_movie_results(interaction, room.room_id, replace_message=True)


async def _execute_passed_vote(
    interaction: discord.Interaction,
    room: MovieNightRoom,
    vote: Any,
) -> None:
    if not getattr(vote, "resolved", False) or not getattr(vote, "passed", False):
        return await open_movie_night(interaction, replace_message=True)

    if vote.action == "end":
        manager = get_movie_night_manager()
        if not manager.claim_vote_execution(room.room_id, vote.vote_id):
            return await open_movie_night(interaction, replace_message=True)
        result = await terminate_movie_night_room(room)
        notice = "✅ Movie Night ended and the room media session was released."
        if result.cleanup_error:
            notice = (
                "⚠️ Movie Night ended, but torrent cleanup reported an error. "
                "The idle media cleanup worker can still reclaim it. "
                f"({result.cleanup_error})"
            )
        return await _replace(
            interaction,
            content=notice[:2000],
            embed=_room_embed(interaction, None),
            view=MovieNightHubView(int(interaction.user.id)),
        )

    if vote.action == "search":
        return await _execute_search_vote(interaction, room, vote)

    if vote.action == "play_variant":
        manager = get_movie_night_manager()
        if not manager.claim_vote_execution(room.room_id, vote.vote_id):
            return await open_movie_night(interaction, replace_message=True)
        candidate_id = str(vote.payload.get("candidate_id") or "")
        variant_id = str(vote.payload.get("variant_id") or "")
        if not candidate_id or not variant_id:
            manager.set_vote_execution_error(
                room.room_id,
                vote.vote_id,
                "Approved playback vote had no release identity.",
            )
            return await _private(
                interaction,
                "❌ Approved playback vote did not contain a valid release.",
            )
        try:
            return await _start_variant_source(
                interaction,
                room,
                candidate_id,
                variant_id,
                authorized_by_vote=True,
            )
        except Exception as exc:
            manager.set_vote_execution_error(
                room.room_id,
                vote.vote_id,
                f"{type(exc).__name__}: {exc}",
            )
            raise

    await open_movie_night(interaction, replace_message=True)


async def _propose_movie_search_vote(
    interaction: discord.Interaction,
    *,
    room_id: str,
    query: str,
    catalog_movie: Optional[CatalogMovie] = None,
) -> None:
    manager = get_movie_night_manager()
    payload: dict[str, Any] = {"query": _compact(query)}
    if catalog_movie is not None:
        payload["catalog"] = catalog_movie.to_metadata()
    try:
        vote = manager.propose_vote(
            room_id,
            proposer_id=int(interaction.user.id),
            action="search",
            payload=payload,
        )
    except Exception as exc:
        return await _private(interaction, f"❌ Search vote could not start: {exc}")

    room = manager.get(room_id)
    if room is None:
        return await _private(interaction, "❌ This Movie Night room no longer exists.")
    if vote.resolved and vote.passed:
        return await _execute_passed_vote(interaction, room, vote)

    selected = catalog_movie.title if catalog_movie is not None else _compact(query)
    if catalog_movie is not None and catalog_movie.year:
        selected = f"{selected} ({catalog_movie.year})"
    await _private(
        interaction,
        f"🗳️ Search vote opened for **{selected}**. "
        "Other active viewers can vote from their /movie panel.",
    )


class MovieSearchModal(discord.ui.Modal, title="Dank Cinema Search"):
    query = discord.ui.TextInput(
        label="Movie title",
        placeholder="Interstellar, The Dark Knight, Shrek…",
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

        raw_query = _compact(self.query.value)
        if not tmdb_catalog_ready():
            return await _propose_movie_search_vote(
                interaction,
                room_id=self.room_id,
                query=raw_query,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        catalog = await search_tmdb_movies(raw_query, limit=8)
        if not catalog.movies:
            return await _propose_movie_search_vote(
                interaction,
                room_id=self.room_id,
                query=raw_query,
            )

        if len(catalog.movies) == 1:
            movie = catalog.movies[0]
            return await _propose_movie_search_vote(
                interaction,
                room_id=self.room_id,
                query=movie.title,
                catalog_movie=movie,
            )

        movie_by_id = {movie.provider_id: movie for movie in catalog.movies}

        async def picked(pick_interaction: discord.Interaction, value: str) -> None:
            if value == "__raw__":
                return await _propose_movie_search_vote(
                    pick_interaction,
                    room_id=self.room_id,
                    query=raw_query,
                )
            movie = movie_by_id.get(value)
            if movie is None:
                return await _private(pick_interaction, "❌ That catalog result expired.")
            await _propose_movie_search_vote(
                pick_interaction,
                room_id=self.room_id,
                query=movie.title,
                catalog_movie=movie,
            )

        choices = [
            DankChoice(
                label=(
                    f"{movie.title} ({movie.year})"
                    if movie.year
                    else movie.title
                )[:100],
                value=movie.provider_id,
                description=(
                    movie.overview
                    or movie.original_title
                    or "TMDB movie result"
                )[:100],
                emoji="🎬",
            )
            for movie in catalog.movies
        ]
        choices.append(
            DankChoice(
                label=f'Use exactly "{raw_query}"'[:100],
                value="__raw__",
                description="Skip catalog matching and search providers with the text you typed.",
                emoji="🔎",
            )
        )

        picker = DankPickerView(
            author_id=int(interaction.user.id),
            choices=choices,
            on_pick=picked,
            custom_id=f"dank:movie:catalog:{self.room_id[:16]}",
            placeholder="Choose the exact movie…",
            title="Dank Cinema • Choose Movie",
            on_home=lambda back_interaction: open_movie_night(
                back_interaction,
                replace_message=True,
            ),
            home_label="Dank Cinema",
        )
        await _replace(
            interaction,
            content=(
                "🔎 **Choose the exact movie.** This identifies the title only; playback "
                "still comes from connected providers or a host-supplied magnet/.torrent."
            ),
            embed=None,
            view=picker,
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
        title="🍿 Dank Cinema Started",
        description=(
            f"{interaction.user.mention} opened **Dank Cinema**.\n"
            "Open `/movie` to join, find a movie, vote, and watch together."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=f"{_CINEMA_FOOTER} • room {room.room_id}")
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


class ConfirmMovieNightEndView(_OwnedView):
    def __init__(self, owner_id: int, room_id: str) -> None:
        super().__init__(owner_id)
        self.room_id = str(room_id)

    @discord.ui.button(label="End Movie Night", emoji="🛑", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        manager = get_movie_night_manager()
        room = manager.get(self.room_id)
        if room is None or room.ended:
            return await open_movie_night(interaction, replace_message=True)
        if int(room.host_id) != int(interaction.user.id):
            return await _private(interaction, "❌ Only the active Movie Night host can end it immediately.")

        result = await terminate_movie_night_room(room)
        notice = "✅ Movie Night ended and its room media session was released."
        if result.cleanup_error:
            notice = (
                "⚠️ Movie Night ended, but torrent cleanup reported an error. "
                "The idle media cleanup worker can still reclaim it. "
                f"({result.cleanup_error})"
            )
        await _replace(
            interaction,
            content=notice[:2000],
            embed=_room_embed(interaction, None),
            view=MovieNightHubView(int(interaction.user.id)),
        )

    @discord.ui.button(label="Keep Watching", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_movie_night(interaction, replace_message=True)


class MovieNightHubView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        room: Optional[MovieNightRoom] = None,
    ) -> None:
        super().__init__(owner_id)
        if room is not None and room.stream_token:
            watch_url = movie_night_watch_url(room.room_id, int(owner_id))
            if watch_url:
                self.add_item(
                    discord.ui.Button(
                        label="Watch",
                        emoji="▶️",
                        style=discord.ButtonStyle.link,
                        url=watch_url,
                        row=3,
                    )
                )

    @discord.ui.button(label="Start / Join", emoji="🎬", style=discord.ButtonStyle.success, row=0)
    async def start_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _start_or_join_room(interaction)

    @discord.ui.button(label="Find Movie", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
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

    @discord.ui.button(label="Movie Picks", emoji="🎞️", style=discord.ButtonStyle.primary, row=0)
    async def results(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room = _room_for_interaction(interaction)
        if room is None:
            return await _private(interaction, "ℹ️ No Movie Night room is active here.")
        await open_movie_results(interaction, room.room_id, replace_message=True)

    @discord.ui.button(label="Watch Queue", emoji="📺", style=discord.ButtonStyle.primary, row=0)
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
            vote = manager.cast_vote(
                room.room_id,
                vote.vote_id,
                user_id=int(interaction.user.id),
                approve=approve,
            )
        except Exception as exc:
            return await _private(interaction, f"❌ Vote failed: {exc}")
        if vote.resolved and vote.passed and vote.action in {"search", "play_variant", "end"}:
            return await _execute_passed_vote(interaction, room, vote)
        await open_movie_night(interaction, replace_message=True)

    @discord.ui.button(label="Provider Deck", emoji="🎞️", style=discord.ButtonStyle.secondary, row=2)
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

    @discord.ui.button(label="End Session", emoji="🛑", style=discord.ButtonStyle.danger, row=3)
    async def end_session(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        room = _room_for_interaction(interaction)
        if room is None:
            return await _private(interaction, "ℹ️ No Movie Night room is active here.")

        manager = get_movie_night_manager()
        manager.join_room(room.room_id, user_id=int(interaction.user.id))
        if int(room.host_id) == int(interaction.user.id):
            return await _replace(
                interaction,
                content=(
                    "🛑 End this Movie Night completely? This stops the room, releases its "
                    "torrent/media lease, clears the queue, and lets a fresh room start here."
                ),
                embed=_room_embed(interaction, room),
                view=ConfirmMovieNightEndView(int(interaction.user.id), room.room_id),
            )

        existing = next(
            (
                item
                for item in room.votes.values()
                if not item.resolved and item.action == "end"
            ),
            None,
        )
        try:
            if existing is not None:
                vote = manager.cast_vote(
                    room.room_id,
                    existing.vote_id,
                    user_id=int(interaction.user.id),
                    approve=True,
                )
            else:
                vote = manager.propose_vote(
                    room.room_id,
                    proposer_id=int(interaction.user.id),
                    action="end",
                )
        except Exception as exc:
            return await _private(interaction, f"❌ End-session vote could not start: {exc}")

        if vote.resolved and vote.passed:
            return await _execute_passed_vote(interaction, room, vote)
        await _replace(
            interaction,
            content=(
                "🗳️ **End Movie Night** vote opened. Active viewers can use "
                "**Vote Yes** / **Vote No**."
            ),
            embed=_room_embed(interaction, room),
            view=MovieNightHubView(int(interaction.user.id), room),
        )

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
    view = MovieNightHubView(int(interaction.user.id), room)
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
            "❌ Movie Night setup is not launch-ready. Run /movie → Setup first.",
        )

    room_manager = get_movie_night_manager()
    room = room_manager.active_room_for_channel(int(guild.id), int(channel.id))
    if room is not None and int(room.host_id) != int(interaction.user.id):
        return await _private(
            interaction,
            "❌ Only the active Movie Night host can replace the room's media source.",
        )
    previous = str(room.stream_token or "") if room is not None else ""
    lease_key = movie_room_lease_key(int(guild.id), int(channel.id))

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
                replace_token=previous,
                lease_key=lease_key,
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
                replace_token=previous,
                lease_key=lease_key,
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
        await manager.release_lease(
            session.token,
            lease_key,
            remove_if_unused=True,
        )
        return await interaction.edit_original_response(
            content="❌ Torrent started, but no signed public stream URL could be created. Check Movie Night Setup.",
            embed=None,
            view=MovieNightHubView(int(interaction.user.id)),
        )

    latest_room = room_manager.active_room_for_channel(
        int(guild.id),
        int(channel.id),
    )
    if room is None:
        if latest_room is not None:
            await manager.release_lease(
                session.token,
                lease_key,
                remove_if_unused=True,
            )
            return await interaction.edit_original_response(
                content=(
                    "❌ Another Movie Night room started while this torrent was loading. "
                    "The stale media start was discarded safely."
                ),
                embed=_room_embed(interaction, latest_room),
                view=MovieNightHubView(int(interaction.user.id), latest_room),
            )
        try:
            room = room_manager.create_room(
                guild_id=int(guild.id),
                channel_id=int(channel.id),
                host_id=int(interaction.user.id),
                stream_token=session.token,
            )
        except Exception as exc:
            await manager.release_lease(
                session.token,
                lease_key,
                remove_if_unused=True,
            )
            return await interaction.edit_original_response(
                content=f"❌ Movie Night room changed while media was loading: {exc}",
                embed=None,
                view=MovieNightHubView(int(interaction.user.id)),
            )
        role = ready["role"]
        if isinstance(role, discord.Role):
            try:
                await _announce_room(interaction, room, role=role)
            except Exception:
                pass
    else:
        if (
            latest_room is None
            or latest_room.ended
            or latest_room.room_id != room.room_id
            or int(latest_room.host_id) != int(interaction.user.id)
            or str(latest_room.stream_token or "") != previous
        ):
            await manager.release_lease(
                session.token,
                lease_key,
                remove_if_unused=True,
            )
            return await interaction.edit_original_response(
                content=(
                    "❌ Movie Night changed while this torrent was loading, so the stale "
                    "media result was discarded."
                ),
                embed=_room_embed(interaction, latest_room) if latest_room is not None else None,
                view=MovieNightHubView(int(interaction.user.id), latest_room),
            )
        room = latest_room
        room_manager.set_room_media(
            room.room_id,
            host_id=int(interaction.user.id),
            stream_token=session.token,
        )
        if previous and previous != session.token:
            await manager.release_lease(
                previous,
                lease_key,
                remove_if_unused=True,
            )

    await interaction.edit_original_response(
        content=(
            f"✅ Movie Night media attached: **{session.file_name}**\n"
            f"Dank Cinema stream: {stream_url}"
        )[:2000],
        embed=_room_embed(interaction, room),
        view=MovieNightHubView(int(interaction.user.id), room),
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
