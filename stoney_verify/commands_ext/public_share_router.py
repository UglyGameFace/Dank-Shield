from __future__ import annotations

"""Menu-first public Share Router UI and production runtime registrar."""

from typing import Any, Optional

import discord

from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS

from stoney_verify.share_router_media_resolver import (
    MediaResolution,
    PROVIDERS,
    media_provider_policy,
    media_resolver_snapshot,
    provider_for_url,
    provider_label,
    resolve_media_url,
)
from stoney_verify.share_router_media_remux import media_remux_snapshot
from stoney_verify.share_router_download_queue import queue_snapshot
from stoney_verify.share_router_external_links import (
    check_public_delivery, delivery_status, file_max_bytes, storage_max_bytes,
    link_egress_budget,
)
from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    SHARE_ROUTER_CATEGORY_NAME,
    is_share_router_design_resource,
    share_source_key,
)
from stoney_verify.share_router_runtime import (
    create_or_repair_hidden_share_hub,
    create_custom_share_source,
    available_share_source_ids,
    delete_custom_share_source,
    ensure_share_router_runtime,
    find_share_router_category,
    find_share_source_channel,
    guild_routes,
    guild_custom_share_sources,
    guild_pending_source_cleanup_count,
    guild_download_channel_id,
    guild_download_expiry_seconds,
    guild_download_delivery,
    configure_guild_download,
    _guild_source_lock,
    save_guild_download_channel,
    save_guild_download_expiry_seconds,
    prune_missing_custom_share_sources,
    remove_route,
    route_health,
    route_permission_blockers,
    save_managed_share_route,
    save_existing_share_route,
    route_source_blockers,
    validate_route_content_filter,
    _share_video_limit_bytes,
    source_age_blocker,
    source_privacy_blocker,
)
from stoney_verify.ui import DankGuildResourceBrowserView

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _can_manage(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.manage_channels)
    except Exception:
        return False


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
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
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    if interaction.response.is_done():
        await interaction.edit_original_response(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=_ALLOWED_MENTIONS,
        )
    else:
        await interaction.response.edit_message(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=_ALLOWED_MENTIONS,
        )


async def _require_manage(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await _private(interaction, "❌ Share Router must be configured inside a server.")
        return False
    if not _can_manage(interaction):
        await _private(
            interaction,
            "❌ Share Router setup requires Manage Server, Manage Channels, or Administrator.",
        )
        return False
    return True


def _target_label(target: Optional[discord.TextChannel]) -> str:
    if not isinstance(target, discord.TextChannel):
        return "missing"
    try:
        prefix = "🔞 " if target.is_nsfw() else ""
    except Exception:
        prefix = ""
    return f"{prefix}#{target.name}"


async def _share_router_embed(guild: discord.Guild) -> discord.Embed:
    routes = await guild_routes(int(guild.id))
    active = sum(route_health(guild, route).ok for route in routes)
    needs_attention = len(routes) - active
    download_id = await guild_download_channel_id(int(guild.id))
    mode, destination_id = await guild_download_delivery(int(guild.id))
    intake = guild.get_channel(download_id) if download_id else None
    target = guild.get_channel(destination_id) if destination_id else None

    if not download_id:
        delivery = "Off. Set up a video download channel to start."
    elif not isinstance(intake, discord.TextChannel):
        delivery = "⚠️ Saved download channel is missing. Check setup before enabling."
    elif mode == "off":
        delivery = f"Paused in {intake.mention}. No automatic messages are sent."
    elif mode == "dm":
        delivery = f"{intake.mention} → private DM to the member who posted the link."
    else:
        where = target.mention if isinstance(target, discord.TextChannel) else "⚠️ destination missing"
        delivery = f"{intake.mention} → {where}"
        if mode == "both":
            delivery += " and a private DM to the author."

    embed = discord.Embed(
        title="🔗 Share Router",
        description=(
            "Move shared posts between channels and choose where requested "
            "videos are delivered. Each server manages its own settings. "
            "Nothing is sent to DMs unless that delivery option is enabled."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Sharing between channels",
        value=(
            f"{active}/{len(routes)} working"
            + (f" • ⚠️ {needs_attention} need attention" if needs_attention else "")
            + ". Choose **Manage Forwarding** to edit sources and destinations."
        ) if routes else "No forwarding configured yet. Choose **Set Up Sharing**.",
        inline=False,
    )
    embed.add_field(name="Video downloads", value=delivery[:1024], inline=False)
    if routes:
        preview = []
        for route in routes[:4]:
            source = guild.get_channel(_safe_int(route.get("source_channel_id")))
            target_channel = guild.get_channel(_safe_int(route.get("target_channel_id")))
            origin = source.mention if isinstance(source, discord.TextChannel) else "Missing source"
            destination = (
                target_channel.mention if isinstance(target_channel, discord.TextChannel)
                else "Missing destination"
            )
            marker = "✅" if route_health(guild, route).ok else "⚠️"
            preview.append(f"{marker} {origin} → {destination}")
        if len(routes) > 4:
            preview.append(f"…and {len(routes) - 4} more in Manage Forwarding.")
        embed.add_field(name="Your channel routes", value="\n".join(preview)[:1024], inline=False)
    embed.set_footer(
        text="Advanced options, safety checks, video testing and cleanup remain available in Tools."
    )
    return embed


async def _share_router_check_embed(guild: discord.Guild) -> discord.Embed:
    embed = await _share_router_embed(guild)
    embed.title = "✅ Share Router Setup Check"
    pending = await guild_pending_source_cleanup_count(int(guild.id))
    embed.add_field(
        name="Automatic cleanup",
        value=(
            f"⚠️ {pending} successfully forwarded posts are awaiting deletion. "
            "Check Manage Messages in private share channels."
            if pending else "Successful private shares are cleaned after delivery. "
            "Failed shares always keep their original posts."
        ),
        inline=False,
    )
    category = find_share_router_category(guild)
    custom = await guild_custom_share_sources(int(guild.id))
    working = sum(isinstance(guild.get_channel(cid), discord.TextChannel) for cid in custom)
    embed.add_field(
        name="Private share channels",
        value=(
            ("✅ Hub found" if category is not None else "No private hub yet")
            + f" • {working}/{len(custom)} extra channels found"
        ),
        inline=False,
    )
    download_id = await guild_download_channel_id(int(guild.id))
    if download_id:
        ready, reason = delivery_status()
        embed.add_field(
            name="Large video links",
            value="✅ Configured • verify public reachability in Advanced Tools."
            if ready else "⚠️ Large-file hosting unavailable: " + reason[:800],
            inline=False,
        )
    embed.set_footer(text="This check is read-only. Choose a setup option to make changes.")
    return embed


def _share_media_health_embed() -> discord.Embed:
    """Operator-only aggregates. Never expose URL, query, token or user content."""
    snapshot = media_resolver_snapshot()
    health = snapshot.get("providers") or {}
    policy = media_provider_policy()
    embed = discord.Embed(
        title="🎞️ Share Router Media Health",
        description=(
            "Runtime-only metadata from this bot process. Counts are global across "
            "servers, reset on restart and do **not** guarantee inline video playback. "
            "Use **Test Provider** for a fresh metadata-only check of a public URL."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Resolver",
        value=(
            f"Cache: {snapshot.get('cache_entries', 0)} • "
            f"Active extraction: {snapshot.get('inflight', 0)} • "
            f"Parallelism cap: {snapshot.get('extract_concurrency', 0)}"
        ),
        inline=False,
    )
    lines: list[str] = []
    for key in [*(p.key for p in PROVIDERS), "direct"]:
        if key == "direct":
            lines.append("• Direct media: URL-based classification (not extracted)")
            continue
        state = health.get(key) or {}
        label = provider_label(key)
        if not policy.get(key, False):
            lines.append(f"⏸️ {label}: disabled")
            continue
        attempts = int(state.get("attempts") or 0)
        fallbacks = int(state.get("fallback") or 0)
        ready = sum(int(state.get(k) or 0) for k in ("progressive", "manifest", "merge"))
        lines.append(f"• {label}: {ready} classified / {fallbacks} fallback / {attempts} extracted")
    for start in range(0, len(lines), 7):
        embed.add_field(
            name=f"Providers ({start + 1}-{min(start + 7, len(lines))})",
            value="\n".join(lines[start:start + 7])[:1024],
            inline=False,
        )
    remux = media_remux_snapshot()
    embed.add_field(
        name="Controlled remux",
        value=(
            f"Successful: {remux.get('success', 0)} • "
            f"Failed: {remux.get('failed', 0)} • "
            f"Timed out: {remux.get('timeout', 0)} • "
            f"Over limit: {remux.get('oversize', 0)} • "
            f"Unavailable: {remux.get('unavailable', 0)}"
        ),
        inline=False,
    )
    embed.set_footer(text="No member links or private media URLs are stored in this view.")
    return embed


_FACEBOOK_PROBE_REASONS = {
    "provider_extractor_incompatible": (
        "Facebook returned video metadata the current extractor cannot parse. "
        "This is a known upstream Facebook Reel issue. The original public "
        "link is preserved; updating yt-dlp alone may not resolve it."
    ),
    "provider_no_downloadable_formats": (
        "No downloadable video stream was exposed. Facebook may require "
        "a login or group membership, or its video format may have changed. "
        "The original link is preserved."
    ),
    "provider_login_required": (
        "Facebook requires login or authorization. Share Router does not "
        "use personal Facebook sessions, private cookies, or bypass permissions."
    ),
    "provider_private_or_restricted": (
        "Facebook marked this video private or restricted. "
        "Share Router will keep the original link rather than bypass access."
    ),
    "provider_rate_limited": (
        "Facebook rate-limited this request. Repeated retries can make "
        "extraction less reliable; the original link is preserved."
    ),
    "provider_forbidden": (
        "Facebook refused the metadata/media request. The source link remains "
        "available rather than forcing an unsafe download."
    ),
    "provider_bot_verification": (
        "Facebook requested a bot check or CAPTCHA. Share Router cannot "
        "bypass the verification prompt; the source link is preserved."
    ),
}


def _provider_probe_outcome(provider: str, reason: str) -> str:
    """Explain fixed, privacy-safe statuses without disclosing signed URLs."""
    if provider == "facebook":
        return _FACEBOOK_PROBE_REASONS.get(reason, reason)
    return reason


class ShareRouterProviderProbeModal(discord.ui.Modal, title="Test Share Router Provider"):
    media_url = discord.ui.TextInput(
        label="Public video URL",
        placeholder="Paste a public video/clip URL (not an account login link)",
        max_length=1000,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id or not await _require_manage(interaction):
            return
        url = str(self.media_url.value or "").strip()
        # Reject private/invalid URLs before doing any provider resolution work.
        provider = provider_for_url(url)
        if not provider:
            return await _private(
                interaction,
                "❌ Not a recognized safe public media URL. Provide a supported "
                "social video page or direct HTTP(S) media URL.",
            )
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result: MediaResolution = await resolve_media_url(
                url, max_bytes=_share_video_limit_bytes(interaction.guild),
            )
        except Exception as exc:
            return await _private(
                interaction,
                f"❌ Metadata check could not complete ({type(exc).__name__}). "
                "No media was downloaded or sent.",
            )
        labels = {
            "progressive": "✅ Progressive media candidate",
            "merge": "🧩 Separate video and audio candidate",
            "manifest": "📺 HLS/DASH manifest candidate",
            "link": "🔗 Link-only fallback",
        }
        embed = discord.Embed(
            title="🧪 Share Router Provider Test",
            description=(
                "Public social providers are checked through metadata extraction. "
                "Direct files are classified by their URL without a download. "
                "**Neither result proves upload or inline playback.** The submitted "
                "URL and any signed tokens are intentionally not displayed."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Provider", value=provider_label(result.provider), inline=True)
        embed.add_field(name="Classification", value=labels.get(result.delivery, "Unknown"), inline=True)
        embed.add_field(
            name="Declared size",
            value=f"{result.known_size:,} bytes" if result.known_size else "Unknown (actual upload is still bounded)",
            inline=False,
        )
        embed.add_field(
            name="Outcome",
            value=(
                _provider_probe_outcome(result.provider, result.reason)
                or (
                    "Direct URL classified only; availability and file format not yet checked."
                    if result.provider == "direct"
                    else "Candidate found; file transfer and playback not yet verified."
                )
            )[:256],
            inline=False,
        )
        embed.set_footer(text="No source data is posted to a server channel.")
        await _private(interaction, embed=embed)


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own Share Router panel to use these controls.")
        return False


_FILTER_LABELS = {
    "everything": "Everything",
    "videos": "Videos Only",
    "images": "Images Only",
    "links": "Links Only",
}


def _target_options_embed(
    source: discord.TextChannel, *, delete_source: bool = True,
    content_filter: str = "everything",
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔗 Destination for #{source.name}",
        description=(
            "Search is the fastest way to find the real destination on mobile. "
            "Dank Shield can match the current/styled name, a saved previous name, Discord ID, or mention.\n\n"
            + (
                "🧹 **Private share channel:** original drop-off posts are cleaned up after delivery."
                if delete_source else
                "🛡️ **Keep Original mode:** posts stay in your existing channel. "
                "Only members who can also post in the destination will be forwarded."
            )
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Content to forward",
        value=(
            f"**{_FILTER_LABELS[content_filter]}**. Change the filter below, then "
            "choose your destination. Videos Only includes video attachments, "
            "playable embeds and recognized video links; Images Only includes "
            "pictures/GIFs; Links Only includes messages with web links. "
            "Other messages remain in the original source."
        ),
        inline=False,
    )
    embed.add_field(
        name="Recommended",
        value="🔎 **Search Destination** — type the channel name instead of scrolling through the server.",
        inline=False,
    )
    embed.add_field(
        name="Browse instead",
        value="📋 **Browse Channels** — open the paged destination list when you want to look manually.",
        inline=False,
    )
    embed.set_footer(text="Nothing is changed until you choose a destination.")
    return embed


class ShareRouterTargetLandingView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        source: discord.TextChannel,
        browser: DankGuildResourceBrowserView,
        delete_source: bool = True,
        content_filter: str = "everything",
    ) -> None:
        super().__init__(owner_id)
        self.source = source
        self.browser = browser
        self.delete_source = delete_source
        self.content_filter = validate_route_content_filter(content_filter)
        for option in self.content_choice.options:
            option.default = option.value == self.content_filter
        if not delete_source:
            self.back.label = "Back to Existing Channels"

    @discord.ui.select(
        placeholder="Content filter • choose what gets forwarded",
        row=2,
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Videos Only", value="videos", emoji="🎬",
                                 description="Video uploads, video embeds & recognized video links"),
            discord.SelectOption(label="Images Only", value="images", emoji="🖼️",
                                 description="Image uploads, GIFs & direct image links"),
            discord.SelectOption(label="Links Only", value="links", emoji="🔗",
                                 description="Messages containing URLs (including video links)"),
            discord.SelectOption(label="Everything", value="everything", emoji="📋",
                                 description="All qualifying messages, including conversations"),
        ],
    )
    async def content_choice(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        if not await _require_manage(interaction) or interaction.guild_id != self.source.guild.id:
            return
        try:
            chosen = validate_route_content_filter(select.values[0])
        except (IndexError, ValueError):
            return await _private(interaction, "❌ Select one supported content filter.")
        await _open_target_browser(
            interaction, self.source,
            delete_source=self.delete_source,
            content_filter=chosen,
        )

    @discord.ui.button(label="Search Destination", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
    async def search_destination(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(self.browser.search_modal())

    @discord.ui.button(label="Browse Channels", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def browse_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=self.browser.embed(), view=self.browser)

    @discord.ui.button(label="Back to Share Channels", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.delete_source:
            await _open_source_picker(interaction)
        else:
            await _open_existing_source_picker(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            content="Share Router destination selection closed.",
            embed=None,
            view=None,
        )


async def open_share_router(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    if not await _require_manage(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    embed = await _share_router_embed(guild)
    view = ShareRouterView(int(interaction.user.id))
    if replace_message and not interaction.response.is_done():
        await _replace(interaction, embed=embed, view=view)
    else:
        await _private(interaction, embed=embed, view=view)


async def open_share_router_download_settings(interaction: discord.Interaction) -> None:
    """One canonical entry for Home navigation and the Share Router button."""
    if not await _require_manage(interaction):
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    current = await guild_download_expiry_seconds(int(interaction.guild.id))
    await _replace(
        interaction,
        embed=await _download_settings_embed(interaction.guild),
        view=ShareRouterDownloadSettingsView(int(interaction.user.id), current),
    )


async def _open_source_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")

    source_ids = await available_share_source_ids(guild)
    if not source_ids:
        return await _private(
            interaction,
            "❌ No private share channels are available. Use Set Up Sharing to create them first.",
        )

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not await _require_manage(pick_interaction):
            return
        current = await available_share_source_ids(guild)
        if not isinstance(resource, discord.TextChannel) or resource.id not in current:
            return await _private(pick_interaction, "❌ This share channel is no longer configured.")
        await _open_target_browser(
            pick_interaction, resource,
            delete_source=is_share_router_design_resource(resource),
        )

    view = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(interaction.user.id),
        resource_kinds=("text",),
        on_pick=picked,
        custom_id="dank:share_router:source",
        placeholder="Choose a share channel…",
        title="Choose Share Channel to Forward From",
        predicate=lambda resource: int(getattr(resource, "id", 0)) in source_ids,
        on_home=back,
        home_label="Share Router",
    )
    await _replace(interaction, embed=view.embed(), view=view)


async def _open_existing_source_picker(interaction: discord.Interaction) -> None:
    """Search-Safe selection of an existing, ordinary source channel."""
    guild = interaction.guild
    if guild is None or not await _require_manage(interaction):
        return
    download_id = await guild_download_channel_id(int(guild.id))

    def allowed_source(resource: Any) -> bool:
        return (
            isinstance(resource, discord.TextChannel)
            and resource.guild.id == guild.id
            and int(resource.id) != download_id
            and not is_share_router_design_resource(resource)
        )

    async def back(pick_interaction: discord.Interaction) -> None:
        await open_share_router(pick_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if (
            not await _require_manage(pick_interaction)
            or pick_interaction.guild_id != guild.id
        ):
            return
        if (
            not allowed_source(resource)
            or not isinstance(guild.get_channel(resource.id), discord.TextChannel)
        ):
            return await _private(pick_interaction, "❌ That ordinary channel is no longer available.")
        await _open_target_browser(pick_interaction, resource, delete_source=False)

    browser = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(interaction.user.id),
        resource_kinds=("text",),
        on_pick=picked,
        custom_id="dank:share_router:existing_source",
        placeholder="Choose an existing channel…",
        title="Keep Original • Existing Source Channel",
        predicate=allowed_source,
        on_home=back,
        home_label="Share Router",
    )
    await _replace(interaction, embed=browser.embed(), view=browser)


async def _open_target_browser(
    interaction: discord.Interaction,
    source: discord.TextChannel,
    *, delete_source: bool = True, content_filter: str | None = None,
) -> None:
    content_filter = validate_route_content_filter(
        content_filter if content_filter is not None else (
            "everything" if delete_source else "videos"
        )
    )
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")

    async def back(back_interaction: discord.Interaction) -> None:
        await _open_target_browser(
            back_interaction, source,
            delete_source=delete_source, content_filter=content_filter,
        )

    async def picked_target(
        pick_interaction: discord.Interaction,
        resource: Any,
    ) -> None:
        if not await _require_manage(pick_interaction):
            return
        if pick_interaction.guild_id != guild.id:
            return await _private(pick_interaction, "❌ Reopen Share Router in this server.")
        if (
            source.guild.id != guild.id
            or not isinstance(guild.get_channel(source.id), discord.TextChannel)
        ):
            return await _private(pick_interaction, "❌ Source channel no longer exists.")
        source_ids = await available_share_source_ids(guild)
        if delete_source and int(source.id) not in source_ids:
            return await _private(pick_interaction, "❌ This share channel is no longer configured.")
        if delete_source != is_share_router_design_resource(source):
            return await _private(pick_interaction, "❌ Channel mode changed. Start again.")
        if not isinstance(resource, discord.TextChannel):
            return await _private(pick_interaction, "❌ Pick a normal text channel.")

        blockers = route_source_blockers(
            source, resource, delete_source=delete_source,
        )
        blockers.extend(route_permission_blockers(
            source, resource, delete_source=delete_source,
        ))

        if blockers:
            embed = discord.Embed(
                title="🚫 Share Route Not Saved",
                description="\n".join(f"• {item}" for item in blockers)[:4000],
                color=discord.Color.red(),
            )
            embed.add_field(
                name="What to fix",
                value=(
                    (
                        "Keep this share channel non-age-restricted, private, and allow the bot to delete "
                        "delivered posts in it. The destination must allow sending."
                        if delete_source else
                        "Keep the existing channel unchanged. Give Dank Shield View Channel "
                        "and Read Message History in the source, plus Send Messages in the target. "
                        "An age-restricted source can only forward to another age-restricted channel."
                    )
                ),
                inline=False,
            )
            return await _private(pick_interaction, embed=embed)

        try:
            save = save_managed_share_route if delete_source else save_existing_share_route
            await save(
                guild,
                source_channel_id=int(source.id),
                target_channel_id=int(resource.id),
                created_by_id=int(pick_interaction.user.id),
                content_filter=content_filter,
            )
        except (OSError, ValueError) as exc:
            return await _private(pick_interaction, f"❌ Share route could not be saved: {exc}")
        await open_share_router(pick_interaction, replace_message=True)

    def allowed_target(resource: Any) -> bool:
        if not isinstance(resource, discord.TextChannel):
            return False
        if int(getattr(resource, "id", 0) or 0) == int(source.id):
            return False
        return not is_share_router_design_resource(resource)

    browser = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(interaction.user.id),
        resource_kinds=("text",),
        on_pick=picked_target,
        custom_id="dank:share_router:target",
        title=f"Browse Destinations for #{source.name}",
        placeholder="Choose a destination channel…",
        predicate=allowed_target,
        on_home=back,
        home_label="Destination options",
        empty_message=(
            "No eligible text-channel destinations matched. Use 🔎 Search with the current/styled name, "
            "a saved previous name, Discord ID, or mention."
        ),
    )
    await _replace(
        interaction,
        embed=_target_options_embed(
            source, delete_source=delete_source,
            content_filter=content_filter,
        ),
        view=ShareRouterTargetLandingView(
            int(interaction.user.id),
            source=source,
            browser=browser,
            delete_source=delete_source,
            content_filter=content_filter,
        ),
    )


async def _open_remove_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")
    routes = await guild_routes(int(guild.id))
    source_ids = {
        _safe_int(route.get("source_channel_id"), 0) for route in routes
    }
    source_ids = {
        source_id for source_id in source_ids
        if isinstance(guild.get_channel(source_id), discord.TextChannel)
    }
    if not source_ids:
        return await _private(interaction, "ℹ️ No saved routes with existing sources are available.")

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not await _require_manage(pick_interaction):
            return
        current = await guild_routes(int(guild.id))
        if not isinstance(resource, discord.TextChannel) or resource.id not in {
            _safe_int(route.get("source_channel_id"), 0) for route in current
        }:
            return await _private(pick_interaction, "❌ That route no longer exists.")
        await remove_route(int(guild.id), int(resource.id))
        await open_share_router(pick_interaction, replace_message=True)

    view = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(interaction.user.id),
        resource_kinds=("text",),
        on_pick=picked,
        custom_id="dank:share_router:remove",
        placeholder="Choose a saved route to remove…",
        title="Remove Share Route (keeps the channels)",
        predicate=lambda resource: int(getattr(resource, "id", 0)) in source_ids,
        on_home=back,
        home_label="Share Router",
    )
    await _replace(interaction, embed=view.embed(), view=view)



class ShareRouterCreateSourceModal(discord.ui.Modal, title="Create Private Share Channel"):
    name = discord.ui.TextInput(
        label="Share channel name",
        placeholder="gaming-clips",
        min_length=1,
        max_length=100,
        required=True,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id or not await _require_manage(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels):
            return await _private(interaction, "❌ Manage Channels permission is required to create share channels.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            source = await create_custom_share_source(guild, interaction.user, str(self.name.value))
        except (discord.HTTPException, OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Could not create share channel: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content=f"✅ Created {source.mention}. Choose Add / Change Forwarding to select where its posts go.",
            embed=await _share_router_embed(guild),
            view=ShareRouterView(self.owner_id),
        )


class ShareRouterDeleteSourceConfirmView(_OwnedView):
    def __init__(self, owner_id: int, source_id: int) -> None:
        super().__init__(owner_id)
        self.source_id = int(source_id)

    @discord.ui.button(label="Delete Share Channel", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels):
            return await _private(interaction, "❌ Manage Channels permission is required to delete share channels.")
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await delete_custom_share_source(guild, self.source_id)
        except (discord.HTTPException, OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Share channel deletion stopped: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content="✅ Private share channel and its forwarding rule removed. Default share channels were not changed.",
            embed=await _share_router_embed(guild),
            view=ShareRouterView(self.owner_id),
        )

    @discord.ui.button(label="Cancel Delete", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)


async def _open_delete_source_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")
    custom_ids = set(await guild_custom_share_sources(int(guild.id)))
    active = {
        source_id for source_id in custom_ids
        if isinstance(guild.get_channel(source_id), discord.TextChannel)
    }
    if not active:
        return await _private(interaction, "ℹ️ No extra private share channels are available to delete.")

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not await _require_manage(pick_interaction):
            return
        if (
            not isinstance(resource, discord.TextChannel)
            or int(resource.id) not in set(await guild_custom_share_sources(int(guild.id)))
        ):
            return await _private(pick_interaction, "❌ This is no longer a registered private share channel.")
        embed = discord.Embed(
            title="⚠️ Permanently delete share channel?",
            description=(
                f"Delete {resource.mention} from Discord and remove its saved forwarding rule? "
                "Any remaining messages in this share channel will also be deleted. "
                "The destination and four default share channels are never deleted."
            ),
            color=discord.Color.orange(),
        )
        await _replace(
            pick_interaction, embed=embed,
            view=ShareRouterDeleteSourceConfirmView(int(pick_interaction.user.id), int(resource.id)),
        )

    view = DankGuildResourceBrowserView(
        guild=guild,
        author_id=int(interaction.user.id),
        resource_kinds=("text",),
        on_pick=picked,
        custom_id="dank:share_router:delete_source",
        placeholder="Choose an extra private share channel to delete…",
        title="Delete Extra Share Channel",
        predicate=lambda resource: int(getattr(resource, "id", 0)) in active,
        on_home=back,
        home_label="Share Router",
    )
    await _replace(interaction, embed=view.embed(), view=view)



class ShareRouterPruneConfirmView(_OwnedView):
    @discord.ui.button(label="Confirm cleanup", emoji="🧹", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await prune_missing_custom_share_sources(guild)
        except (discord.HTTPException, OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Could not verify missing share channels: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content=f"✅ Pruned {count} saved share-channel entry/entries not visible in Discord's channel listing for this server. No Discord channel was deleted.",
            embed=await _share_router_embed(guild),
            view=ShareRouterView(self.owner_id),
        )

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)


class DownloadExpirySelect(discord.ui.Select):
    def __init__(self, current: int) -> None:
        options = [
            discord.SelectOption(label="1 hour", value="3600", description="Temporary private delivery"),
            discord.SelectOption(label="24 hours", value="86400", description="Short-term download"),
            discord.SelectOption(label="7 days (recommended)", value="604800", description="Default expiration"),
            discord.SelectOption(label="30 days (maximum)", value="2592000", description="Maximum configurable access"),
        ]
        for option in options:
            option.default = option.value == str(current)
        super().__init__(
            placeholder="Choose automatic link expiration",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_manage(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            await save_guild_download_expiry_seconds(
                int(interaction.guild.id), int(self.values[0]),
            )
            await _replace(
                interaction,
                embed=await _download_settings_embed(interaction.guild),
                view=ShareRouterDownloadSettingsView(int(interaction.user.id), int(self.values[0])),
            )
        except Exception as exc:
            await _private(interaction, f"❌ Could not save expiration ({type(exc).__name__}).")


async def _download_settings_embed(guild: discord.Guild) -> discord.Embed:
    ident = await guild_download_channel_id(int(guild.id))
    expire = await guild_download_expiry_seconds(int(guild.id))
    ready, reason = delivery_status()
    channel = guild.get_channel(ident) if ident else None
    embed = discord.Embed(
        title="📥 Private Video Downloads",
        description=(
            "Server owners choose **Where Videos Go** to select private DMs, "
            "a server channel, both, or off. This advanced page controls link "
            "expiration and media hosting. Smaller files are sent as attachments; "
            "larger ones use expiring HTTPS links when hosting is available."
        ),
        color=discord.Color.blurple(),
    )
    mode, destination_id = await guild_download_delivery(int(guild.id))
    target = guild.get_channel(destination_id) if destination_id else None
    labels = {"off": "Paused", "dm": "Private DM", "channel": "Server channel", "both": "DM and server channel"}
    embed.add_field(name="Request channel", value=channel.mention if channel else "Not configured", inline=False)
    embed.add_field(
        name="Where videos go",
        value=labels[mode] + (f" • {target.mention}" if isinstance(target, discord.TextChannel) else ""),
        inline=False,
    )
    embed.add_field(name="Link expiration", value={
        3600: "1 hour", 86400: "24 hours", 604800: "7 days (recommended)",
        2592000: "30 days (maximum)",
    }.get(expire, "7 days"), inline=True)
    embed.add_field(
        name="External link hosting",
        value=("✅ Storage/public URL configured. Press Test HTTPS Hosting to confirm reachability."
               if ready else f"⚠️ Not ready: {reason}. Set DANK_MEDIA_PUBLIC_BASE_URL to your existing HTTPS Discloud site URL, restart, and return here.")[:1024],
        inline=False,
    )
    try:
        backlog = await queue_snapshot()
        queue_status = (
            f"{backlog['queued']} waiting / {backlog['running']} processing. "
            f"Maximum {backlog['limit']} jobs across the bot, "
            f"{backlog['per_guild_limit']} per server; "
            f"{backlog['workers']} active worker slots."
        )
    except Exception:
        queue_status = "⚠️ Queue unavailable. Download requests will be preserved for retry."
    embed.add_field(name="Download queue", value=queue_status[:1024], inline=False)
    embed.add_field(
        name="Hosting limits",
        value=f"Up to {file_max_bytes() // (1024 * 1024)} MiB per file, "
              f"{storage_max_bytes() // (1024 * 1024)} MiB total configured storage. "
              f"Up to {link_egress_budget() // (1024 * 1024)} MiB of total public "
              "transfer per issued link. These are safety limits, not "
              "guaranteed free disk or bandwidth.",
        inline=False,
    )
    embed.add_field(
        name="Important: storage durability",
        value=(
            "Expiring links rely on the hosting app keeping the staged file. "
            "A restart/redeploy that removes local storage will invalidate links early. "
            "Verify persistent storage with Discloud before promising 7- or 30-day availability."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Anyone with a private link can use it until expiry; Discord may retain cached previews.",
    )
    return embed


class ShareRouterDownloadSettingsView(_OwnedView):
    def __init__(self, owner_id: int, current: int) -> None:
        super().__init__(owner_id)
        self.add_item(DownloadExpirySelect(current))

    @discord.ui.button(label="Test HTTPS Hosting", emoji="🩺", style=discord.ButtonStyle.primary, row=1)
    async def test_hosting(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await check_public_delivery()
        await _private(
            interaction,
            ("✅ " if ok else "❌ ") + message
            + (". Discord inline playback still requires a real video canary."
               if ok else ". Check the Download Settings readiness message and the public media URL."),
        )

    @discord.ui.button(label="Test Video Source", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
    async def test_video_source(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await interaction.response.send_modal(ShareRouterProviderProbeModal(int(interaction.user.id)))

    @discord.ui.button(label="Where Videos Go", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)


class ShareRouterControlsView(_OwnedView):
    """Existing handlers remain authoritative, grouped into small plain-language views."""

    def __init__(self, owner_id: int, *, section: str) -> None:
        super().__init__(owner_id)
        sections = {
            "setup": {
                "Share From Existing Channel", "Create Private Share Channel",
                "Create / Repair Share Channels", "Share Router Home",
            },
            "routes": {
                "Add / Change Forwarding", "Remove Forwarding",
                "Delete Private Share Channel", "Clean Up Missing Channels",
                "Share Router Home",
            },
            "advanced": {
                "Download Settings", "Video Service Status", "Test Video Link",
                "Refresh", "Share Router Home",
            },
        }
        if section not in sections:
            raise ValueError("Unknown Share Router menu section")
        for item in tuple(self.children):
            if getattr(item, "label", "") not in sections[section]:
                self.remove_item(item)
        for index, item in enumerate(self.children):
            item.row = index // 5

    @discord.ui.button(label="Share Router Home", emoji="⬅️", style=discord.ButtonStyle.secondary, row=4)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)


    @discord.ui.button(label="Share From Existing Channel", emoji="📁", style=discord.ButtonStyle.primary, row=2)
    async def use_existing_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_manage(interaction):
            return
        await _open_existing_source_picker(interaction)

    @discord.ui.button(label="Create Private Share Channel", emoji="➕", style=discord.ButtonStyle.primary, row=2)
    async def create_proxy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await interaction.response.send_modal(ShareRouterCreateSourceModal(int(interaction.user.id)))

    @discord.ui.button(label="Clean Up Missing Channels", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def clean_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=discord.Embed(
                title="⚠️ Confirm missing share-channel cleanup",
                description=(
                    "Only saved private share channel IDs absent from Discord's channel listing "
                    "will be unregistered, along with their saved routes. No live channel is deleted. "
                    "**Important:** Discord may omit channels that Dank Shield cannot view. "
                    "If access was revoked rather than the channel being deleted, restore bot "
                    "View Channel permissions instead of cleaning. This action cannot reliably "
                    "distinguish every permission change from deletion."
                ),
                color=discord.Color.orange(),
            ),
            view=ShareRouterPruneConfirmView(int(interaction.user.id)),
        )

    @discord.ui.button(label="Delete Private Share Channel", emoji="🗑️", style=discord.ButtonStyle.secondary, row=2)
    async def delete_proxy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_delete_source_picker(interaction)

    @discord.ui.button(label="Video Download Setup", emoji="📥", style=discord.ButtonStyle.primary, row=3)
    async def create_download_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)

    @discord.ui.button(label="Download Settings", emoji="⏳", style=discord.ButtonStyle.secondary, row=3)
    async def download_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_download_settings(interaction)

    @discord.ui.button(label="Pause Downloads", emoji="⏸️", style=discord.ButtonStyle.secondary, row=3)
    async def disable_downloads(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_download_delivery_confirmation(interaction, "off")

    @discord.ui.button(label="Create / Repair Share Channels", emoji="🛠️", style=discord.ButtonStyle.primary, row=0)
    async def repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await create_or_repair_hidden_share_hub(guild, interaction.user)
        except Exception as exc:
            return await interaction.edit_original_response(
                content=f"❌ Share Router hub repair failed safely: {type(exc).__name__}: {exc}",
                embed=None,
                view=ShareRouterView(self.owner_id),
                allowed_mentions=_ALLOWED_MENTIONS,
            )

        # A repair is not complete until its status message is delivered.
        # The old code left the interaction on "thinking" if reading route
        # health or editing the result failed after the Discord defer.
        try:
            embed = await _share_router_embed(guild)
            notes: list[str] = []
            if result.created:
                notes.append("Created: " + ", ".join(result.created))
            if result.repaired:
                notes.append("Repaired: " + ", ".join(result.repaired))
            notes.extend(result.warnings)
            if not notes:
                notes.append("Hub names and privacy were already in the expected state.")
            embed.add_field(name="Last repair", value="\n".join(notes)[:1024], inline=False)
            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=ShareRouterView(self.owner_id),
                allowed_mentions=_ALLOWED_MENTIONS,
            )
        except Exception as exc:
            print(
                "⚠️ share_router repair status failed "
                f"guild={int(guild.id)} error={type(exc).__name__}: {exc}"
            )
            try:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ Hub repair ran, but Share Router could not display its status: "
                        f"{type(exc).__name__}. Refresh the panel and check Discloud logs."
                    ),
                    embed=None,
                    view=ShareRouterView(self.owner_id),
                    allowed_mentions=_ALLOWED_MENTIONS,
                )
            except discord.HTTPException:
                print(
                    "❌ share_router repair could not send failure status "
                    f"guild={int(guild.id)}"
                )

    @discord.ui.button(label="Add / Change Forwarding", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_route(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_source_picker(interaction)

    @discord.ui.button(label="Remove Forwarding", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_remove_picker(interaction)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)

    @discord.ui.button(label="Video Service Status", emoji="🎞️", style=discord.ButtonStyle.secondary, row=1)
    async def media_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _private(interaction, embed=_share_media_health_embed())

    @discord.ui.button(label="Test Video Link", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
    async def test_provider(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await interaction.response.send_modal(ShareRouterProviderProbeModal(int(interaction.user.id)))

    @discord.ui.button(label="Community Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_tools import CommunityToolsView, _center_embed

        await _replace(
            interaction,
            embed=_center_embed(),
            view=CommunityToolsView(self.owner_id),
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Share Router closed.", embed=None, view=None)


def _video_destination_blocker(
    guild: discord.Guild, actor: discord.Member, channel: Any, request_id: int,
) -> str:
    """Apply the same sender-visible and bot-send rules before save and delivery."""
    if (
        not isinstance(channel, discord.TextChannel)
        or getattr(channel.guild, "id", None) != int(guild.id)
        or int(channel.id) == int(request_id)
        or is_share_router_design_resource(channel)
    ):
        return "Choose a normal channel in this server, different from the request channel."
    try:
        if channel.is_nsfw():
            return "Video destinations cannot be age-restricted."
        sender = channel.permissions_for(actor)
        bot = guild.me
        bot_perms = channel.permissions_for(bot) if bot is not None else None
        if not sender.view_channel or not sender.send_messages:
            return "Members must be allowed to view and post in the destination channel."
        if (
            bot_perms is None or not bot_perms.view_channel
            or not bot_perms.send_messages or not bot_perms.attach_files
        ):
            return "Dank Shield needs View Channel, Send Messages and Attach Files there."
    except Exception:
        return "Could not check the destination permissions."
    return ""


async def _video_delivery_embed(guild: discord.Guild) -> discord.Embed:
    request_id = await guild_download_channel_id(int(guild.id))
    mode, destination_id = await guild_download_delivery(int(guild.id))
    request = guild.get_channel(request_id) if request_id else None
    dest = guild.get_channel(destination_id) if destination_id else None
    names = {"off": "Paused", "dm": "Private DM to the author",
             "channel": "Server channel", "both": "Private DM and server channel"}
    details = names[mode]
    if mode in {"channel", "both"}:
        details += " • " + (dest.mention if isinstance(dest, discord.TextChannel) else "⚠️ destination missing")
    embed = discord.Embed(
        title="🎬 Where Videos Go",
        description=(
            "People post one public video link in a request channel. "
            "You choose what happens next. **DMs are optional**, not required. "
            "Choose an option to see what will change before saving."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Requests arrive in",
        value=(request.mention if isinstance(request, discord.TextChannel)
               else "Not set up yet. Choose an existing channel or let Dank Shield create one."),
        inline=False,
    )
    embed.add_field(name="Current delivery", value=details, inline=False)
    embed.add_field(
        name="What happens to the original link?",
        value=("It is removed only after every chosen destination receives an actual "
               "video attachment or a hosted video link. Failed deliveries keep the link."),
        inline=False,
    )
    embed.set_footer(text="Existing servers keep their saved DM setting until you change it.")
    return embed


async def open_share_router_video_delivery(interaction: discord.Interaction) -> None:
    if not await _require_manage(interaction):
        return
    await _replace(
        interaction,
        embed=await _video_delivery_embed(interaction.guild),
        view=ShareRouterVideoDeliveryView(int(interaction.user.id)),
    )


async def _open_download_delivery_confirmation(
    interaction: discord.Interaction, mode: str, destination_id: int = 0,
) -> None:
    if not await _require_manage(interaction):
        return
    guild = interaction.guild
    request_id = await guild_download_channel_id(int(guild.id))
    destination = guild.get_channel(destination_id) if destination_id else None
    if mode in {"channel", "both"}:
        blocker = _video_destination_blocker(guild, interaction.user, destination, request_id)
        if blocker:
            return await _private(interaction, "❌ " + blocker)
    elif mode not in {"dm", "off"}:
        return await _private(interaction, "❌ Unsupported video delivery choice.")
    names = {"dm": "Send a private DM", "channel": "Post in a server channel",
             "both": "Send a DM and post in a server channel", "off": "Pause all video downloads"}
    lines = [
        f"**Choice:** {names[mode]}",
        "**Request channel:** " + (
            guild.get_channel(request_id).mention if request_id and guild.get_channel(request_id)
            else ("No request channel needed while paused." if mode == "off"
                  else "Create a new #video-downloads channel after confirmation.")
        ),
    ]
    if destination is not None:
        lines.append(f"**Video destination:** {destination.mention}")
    lines.append(
        "Only requested videos are processed. Originals are removed only after "
        "successful delivery to every chosen destination. Your forwarding routes are unchanged."
    )
    await _replace(
        interaction,
        embed=discord.Embed(
            title="Confirm Video Delivery",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        ),
        view=ShareRouterDeliveryConfirmView(
            int(interaction.user.id), mode=mode, destination_id=destination_id,
        ),
    )


class ShareRouterDeliveryConfirmView(_OwnedView):
    def __init__(self, owner_id: int, *, mode: str, destination_id: int) -> None:
        super().__init__(owner_id)
        self.mode = mode
        self.destination_id = destination_id

    @discord.ui.button(label="Save This Choice", emoji="✅", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        mode = self.mode
        await interaction.response.defer(ephemeral=True, thinking=True)
        created = None
        try:
            async with _guild_source_lock(int(guild.id)):
                request_id = await guild_download_channel_id(int(guild.id))
                if mode in {"channel", "both"}:
                    target = guild.get_channel(self.destination_id)
                    blocker = _video_destination_blocker(
                        guild, interaction.user, target, request_id,
                    )
                    if blocker:
                        raise ValueError(blocker)
                if not request_id and mode == "off":
                    await interaction.edit_original_response(
                        content="✅ Downloads are already off in this server.",
                        embed=None, view=ShareRouterVideoDeliveryView(self.owner_id),
                    )
                    return
                if request_id and not isinstance(guild.get_channel(request_id), discord.TextChannel):
                    if mode != "off":
                        raise ValueError("The saved request channel is missing. Choose a request channel first.")
                if not request_id:
                    me = guild.me
                    if (
                        not interaction.user.guild_permissions.manage_channels
                        and not interaction.user.guild_permissions.administrator
                    ) or me is None or not me.guild_permissions.manage_channels:
                        raise ValueError("Creating a request channel needs Manage Channels for you and Dank Shield.")
                    created = await guild.create_text_channel(
                        "video-downloads",
                        topic="Post one public video link here. Delivery is configured by server staff.",
                        reason="Staff-confirmed Share Router video request channel",
                    )
                    request_id = int(created.id)
                await configure_guild_download(
                    int(guild.id), channel_id=request_id, mode=mode,
                    destination_id=self.destination_id,
                )
        except (discord.HTTPException, OSError, ValueError) as exc:
            if created is not None:
                try:
                    await created.delete(reason="Video setup could not save settings")
                except discord.HTTPException:
                    pass
            await interaction.edit_original_response(
                content=f"❌ Video delivery was not changed ({type(exc).__name__}): {exc}",
                embed=None, view=ShareRouterVideoDeliveryView(self.owner_id),
            )
            return
        await interaction.edit_original_response(
            content="✅ Video delivery preference saved for this server.",
            embed=await _video_delivery_embed(guild),
            view=ShareRouterVideoDeliveryView(self.owner_id),
            allowed_mentions=_ALLOWED_MENTIONS,
        )

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)


class ShareRouterDestinationChoiceView(_OwnedView):
    def __init__(self, owner_id: int, mode: str) -> None:
        super().__init__(owner_id)
        self.mode = mode

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.primary)
    async def use_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_download_delivery_confirmation(
            interaction, self.mode,
            int(interaction.channel.id) if isinstance(interaction.channel, discord.TextChannel) else 0,
        )

    @discord.ui.button(label="Choose Channel", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        request_id = await guild_download_channel_id(int(guild.id))

        async def home(back_interaction: discord.Interaction) -> None:
            await open_share_router_video_delivery(back_interaction)

        async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
            await _open_download_delivery_confirmation(
                pick_interaction, self.mode, int(resource.id),
            )

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("text",),
            on_pick=picked,
            custom_id="dank:share_router:download_destination",
            placeholder="Choose where the videos will be posted…",
            title="Choose Video Destination",
            predicate=lambda resource: not _video_destination_blocker(
                guild, interaction.user, resource, request_id,
            ),
            on_home=home,
            home_label="Where Videos Go",
        )
        await _replace(interaction, embed=browser.embed(), view=browser)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)


async def _choose_video_destination(interaction: discord.Interaction, mode: str) -> None:
    if not await _require_manage(interaction):
        return
    await _replace(
        interaction,
        embed=discord.Embed(
            title="Where Should Videos Be Posted?",
            description=(
                "Choose a normal server channel. Members must be allowed to view and "
                "send messages there, and Dank Shield needs permission to attach files. "
                "This does not turn on DMs unless you also selected **Both**."
            ),
            color=discord.Color.blurple(),
        ),
        view=ShareRouterDestinationChoiceView(int(interaction.user.id), mode),
    )


async def _request_channel_blocker(guild: discord.Guild, channel: Any, routes: list[dict[str, Any]]) -> str:
    if (
        not isinstance(channel, discord.TextChannel)
        or getattr(channel.guild, "id", None) != int(guild.id)
        or is_share_router_design_resource(channel)
    ):
        return "Select an ordinary text channel in this server."
    if any(_safe_int(route.get("source_channel_id")) == int(channel.id) for route in routes):
        return "This channel already forwards posts elsewhere. Choose a different request channel."
    try:
        if channel.is_nsfw():
            return "A video request channel cannot be age-restricted."
        bot = guild.me
        perms = channel.permissions_for(bot) if bot is not None else None
        if (
            perms is None or not perms.view_channel or not perms.send_messages
            or not perms.read_message_history
        ):
            return "Dank Shield needs View Channel, Send Messages and Read History."
    except Exception:
        return "Could not check this request channel's permissions."
    return ""


class ShareRouterRequestConfirmView(_OwnedView):
    def __init__(self, owner_id: int, request_id: int) -> None:
        super().__init__(owner_id)
        self.request_id = request_id

    @discord.ui.button(label="Use Channel, Keep Downloads Paused", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        routes = await guild_routes(int(guild.id))
        channel = guild.get_channel(self.request_id)
        blocker = await _request_channel_blocker(guild, channel, routes)
        if blocker:
            return await _private(interaction, "❌ " + blocker)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with _guild_source_lock(int(guild.id)):
                routes = await guild_routes(int(guild.id))
                blocker = await _request_channel_blocker(
                    guild, guild.get_channel(self.request_id), routes,
                )
                if blocker:
                    raise ValueError(blocker)
                # Selecting the intake channel alone never starts sending DMs.
                await configure_guild_download(
                    int(guild.id), channel_id=self.request_id, mode="off",
                )
        except (ValueError, OSError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Request channel was not changed: {exc}",
                embed=None, view=ShareRouterVideoDeliveryView(self.owner_id),
            )
        await interaction.edit_original_response(
            content="✅ Request channel saved with delivery paused. Now choose where videos go.",
            embed=await _video_delivery_embed(guild),
            view=ShareRouterVideoDeliveryView(self.owner_id),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)


async def _confirm_request_channel(interaction: discord.Interaction, channel: Any) -> None:
    if not await _require_manage(interaction):
        return
    guild = interaction.guild
    blocker = await _request_channel_blocker(guild, channel, await guild_routes(int(guild.id)))
    if blocker:
        return await _private(interaction, "❌ " + blocker)
    await _replace(
        interaction,
        embed=discord.Embed(
            title="Confirm Video Request Channel",
            description=(
                f"Use {channel.mention} for video requests? "
                "**Downloads will be paused** until you separately choose DM, "
                "server channel or both. The old request channel stays on Discord "
                "but stops being monitored. Existing forwarding routes are untouched."
            ),
            color=discord.Color.blurple(),
        ),
        view=ShareRouterRequestConfirmView(int(interaction.user.id), int(channel.id)),
    )


class ShareRouterRequestChoiceView(_OwnedView):
    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.primary)
    async def use_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _confirm_request_channel(interaction, interaction.channel)

    @discord.ui.button(label="Choose Channel", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        routes = await guild_routes(int(guild.id))

        async def home(back_interaction: discord.Interaction) -> None:
            await open_share_router_video_delivery(back_interaction)

        async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
            await _confirm_request_channel(pick_interaction, resource)

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("text",),
            on_pick=picked,
            custom_id="dank:share_router:download_request_channel",
            placeholder="Choose where members will post links…",
            title="Choose Video Request Channel",
            predicate=lambda resource: not (
                isinstance(resource, discord.TextChannel)
                and is_share_router_design_resource(resource)
            ) and isinstance(resource, discord.TextChannel),
            on_home=home,
            home_label="Where Videos Go",
        )
        await _replace(interaction, embed=browser.embed(), view=browser)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)


async def _choose_request_channel(interaction: discord.Interaction) -> None:
    if not await _require_manage(interaction):
        return
    await _replace(
        interaction,
        embed=discord.Embed(
            title="Choose Where Members Request Videos",
            description=(
                "Use an existing text channel, or go back and choose a delivery "
                "option to create a new #video-downloads channel. "
                "Selecting an existing request channel never enables delivery by itself."
            ),
            color=discord.Color.blurple(),
        ),
        view=ShareRouterRequestChoiceView(int(interaction.user.id)),
    )


class ShareRouterVideoDeliveryView(_OwnedView):
    @discord.ui.button(label="Private DM", emoji="✉️", style=discord.ButtonStyle.primary, row=0)
    async def dm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_download_delivery_confirmation(interaction, "dm")

    @discord.ui.button(label="Post in Server Channel", emoji="📺", style=discord.ButtonStyle.primary, row=0)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _choose_video_destination(interaction, "channel")

    @discord.ui.button(label="Both DM and Channel", emoji="📨", style=discord.ButtonStyle.secondary, row=1)
    async def both(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _choose_video_destination(interaction, "both")

    @discord.ui.button(label="Pause Downloads", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_download_delivery_confirmation(interaction, "off")

    @discord.ui.button(label="Choose Request Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=2)
    async def request_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _choose_request_channel(interaction)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)


class ShareRouterView(_OwnedView):
    """Short top-level menu; all existing forwarding operations are still reachable."""

    @discord.ui.button(label="Set Up Sharing", emoji="🛠️", style=discord.ButtonStyle.primary, row=0)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=discord.Embed(
                title="Set Up Sharing",
                description=(
                    "**Share From Existing Channel:** forward matching posts without deleting originals.\n"
                    "**Create Private Share Channel:** make a new mobile share-sheet channel; "
                    "successfully forwarded posts are cleaned.\n"
                    "**Create / Repair Share Channels:** set up the default four private share channels."
                ),
                color=discord.Color.blurple(),
            ),
            view=ShareRouterControlsView(self.owner_id, section="setup"),
        )

    @discord.ui.button(label="Where Videos Go", emoji="🎬", style=discord.ButtonStyle.primary, row=0)
    async def delivery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_video_delivery(interaction)

    @discord.ui.button(label="Manage Forwarding", emoji="📁", style=discord.ButtonStyle.secondary, row=1)
    async def routes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=discord.Embed(
                title="Manage Forwarding",
                description=(
                    "Change which channel receives posts, remove a forwarding rule, "
                    "or safely clean up old private share channels. Removing a rule "
                    "does not delete ordinary member messages."
                ),
                color=discord.Color.blurple(),
            ),
            view=ShareRouterControlsView(self.owner_id, section="routes"),
        )

    @discord.ui.button(label="Check My Setup", emoji="✅", style=discord.ButtonStyle.secondary, row=1)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=await _share_router_check_embed(interaction.guild),
            view=ShareRouterView(self.owner_id),
        )

    @discord.ui.button(label="Advanced Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=2)
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=discord.Embed(
                title="Advanced Share Router Tools",
                description=(
                    "Video service status, source-link testing, download expiration, "
                    "and other troubleshooting tools. These do not change delivery "
                    "preferences until you save them."
                ),
                color=discord.Color.blurple(),
            ),
            view=ShareRouterControlsView(self.owner_id, section="advanced"),
        )

    @discord.ui.button(label="Community Tools", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_tools import CommunityToolsView, _center_embed
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Share Router closed.", embed=None, view=None)


def register_public_share_router(bot: Any, tree: Any) -> None:
    """Attach Share Router runtime without adding a public slash-command child."""

    _ = tree
    if not ensure_share_router_runtime(bot):
        raise RuntimeError("Share Router runtime listener could not be installed.")


__all__ = [
    "ShareRouterTargetLandingView",
    "ShareRouterView",
    "open_share_router",
    "register_public_share_router",
]
