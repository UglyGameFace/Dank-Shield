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
    custom_ids = await guild_custom_share_sources(int(guild.id))
    category = find_share_router_category(guild)

    embed = discord.Embed(
        title="🔗 Share Router",
        description=(
            "**Two forwarding modes:** Use Existing Channel to forward from an ordinary "
            "channel **without deleting its posts**, or Create Proxy for a private "
            "mobile share-sheet drop-off that cleans successful posts. Both use "
            "Search-Safe destination selection."

        ),
        color=discord.Color.blurple(),
    )

    if category is None:
        hub_status = "ℹ️ No proxy hub. Optional for Use Existing Channel; Create / Repair Hub enables private mobile drop-offs."
    elif str(category.name) != SHARE_ROUTER_CATEGORY_NAME:
        hub_status = f"⚠️ Found styled/legacy hub: {category.name}. Repair will restore the plain name."
    else:
        found = sum(1 for name in DEFAULT_SHARE_CHANNELS if find_share_source_channel(category, name) is not None)
        hub_status = f"✅ {category.name} • {found}/{len(DEFAULT_SHARE_CHANNELS)} proxy channels present."
    embed.add_field(name="Proxy hub", value=hub_status[:1024], inline=False)
    live_custom = sum(isinstance(guild.get_channel(source_id), discord.TextChannel) for source_id in custom_ids)
    embed.add_field(name="Custom proxies", value=f"{live_custom} active / {len(custom_ids)} registered in this server. Create Proxy adds another source without replacing the four defaults.", inline=False)
    download_id = await guild_download_channel_id(int(guild.id))
    download_expiry = await guild_download_expiry_seconds(int(guild.id))
    hosting_ready, hosting_reason = delivery_status()
    download_channel = guild.get_channel(download_id) if download_id else None
    embed.add_field(
        name="Private video downloads",
        value=(
            f"✅ {download_channel.mention} • Posts send a **private DM** to their author. "
            "Small videos are DM attachments. Above the DM limit: "
            + ("expiring Watch + Download links." if hosting_ready else "source-link fallback.")
            + f" Expiration: {download_expiry // 3600}h."
            if isinstance(download_channel, discord.TextChannel)
            else ("⚠️ Download channel missing. Disable it before creating a replacement."
                  if download_id else "Disabled • Create Download Channel to opt in.")
        )[:1024],
        inline=False,
    )
    if download_id:
        embed.add_field(
            name="Large-file delivery status",
            value=(
                "✅ Host configured • Live public reachability must still be tested."
                if hosting_ready else f"⚠️ {hosting_reason}"
            )[:1024],
            inline=False,
        )
    pending_cleanup = await guild_pending_source_cleanup_count(int(guild.id))
    cleanup_status = (
        f"⚠️ {pending_cleanup} delivered post(s) awaiting deletion. "
        "Check Dank Shield's Manage Messages permission in the proxy channels."
        if pending_cleanup else "✅ Delivered shares are removed from their proxy sources."
    )
    embed.add_field(
        name="Automatic proxy cleanup",
        value=(
            f"{cleanup_status}\n"
            "If forwarding fails, the original is kept so no share is lost. "
            "Successful forwards are cleaned automatically, including custom and default proxies."
        )[:1024],
        inline=False,
    )

    lines: list[str] = []
    healthy = 0
    for route in routes[:20]:
        source_id = _safe_int(route.get("source_channel_id"), 0)
        target_id = _safe_int(route.get("target_channel_id"), 0)
        source = guild.get_channel(source_id)
        target = guild.get_channel(target_id)
        health = route_health(guild, route)
        if health.ok:
            healthy += 1
        icon = "✅" if health.ok else "⚠️"
        source_text = f"#{source.name}" if isinstance(source, discord.TextChannel) else f"source {source_id}"
        target_text = _target_label(target if isinstance(target, discord.TextChannel) else None)
        keep_original = (
            isinstance(source, discord.TextChannel)
            and not is_share_router_design_resource(source)
            and route.get("delete_source") is False
        )
        mode = "🛡️ Keep Original" if keep_original else "🧹 Proxy cleanup"
        raw_filter = route.get("content_filter", "everything")
        display_filter = (
            _FILTER_LABELS.get(raw_filter, "⚠️ Invalid filter")
            if isinstance(raw_filter, str) else "⚠️ Invalid filter"
        )
        line = f"{icon} {source_text} → {target_text} • {mode} • {display_filter}"
        if health.blockers:
            line += f" • {health.blockers[0]}"
        elif health.warnings:
            line += f" • {health.warnings[0]}"
        lines.append(line[:300])
    if len(routes) > 20:
        lines.append(f"…and {len(routes) - 20} more. All routes remain active.")

    if routes:
        embed.add_field(
            name=f"Saved routes • {healthy}/{len(routes)} healthy",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Saved routes",
            value="None yet. Use Existing Channel for a non-destructive route, or Create / Repair Hub for private share-sheet proxies.",
            inline=False,
        )

    embed.add_field(
        name="Safety boundary",
        value=(
            "Proxies stay hidden, non-age-restricted, and clean delivered source posts. "
            "Existing channels keep original posts and can be public. An age-restricted "
            "source never forwards to a non-age-restricted destination. All routes "
            "require the sender to have normal destination View + Send permissions."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Who can use the proxies",
        value=(
            "Hub repair preserves unrelated explicit permission overwrites and grants the configuring "
            "admin plus configured staff/control roles access. Add any other intended role through "
            "normal Discord channel permissions."
        )[:1024],
        inline=False,
    )
    embed.set_footer(text="Share Router proxy channels keep plain names so mobile sharing stays reliable. • Share Router v2")
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
                result.reason
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
                "🧹 **Proxy mode:** original drop-off posts are cleaned up after delivery."
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

    @discord.ui.button(label="Back to Proxy Sources", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
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
            "❌ No proxy channels are available. Run Create / Repair Hub or Create Proxy first.",
        )

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not await _require_manage(pick_interaction):
            return
        current = await available_share_source_ids(guild)
        if not isinstance(resource, discord.TextChannel) or resource.id not in current:
            return await _private(pick_interaction, "❌ This proxy source is no longer configured.")
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
        placeholder="Choose a proxy source…",
        title="Choose Share Router Source",
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
            return await _private(pick_interaction, "❌ This proxy source is no longer configured.")
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
                        "Keep the proxy non-age-restricted, private, and allow the bot to delete "
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



class ShareRouterCreateSourceModal(discord.ui.Modal, title="Create Share Router Proxy"):
    name = discord.ui.TextInput(
        label="Proxy channel name",
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
            return await _private(interaction, "❌ Manage Channels permission is required to create proxies.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            source = await create_custom_share_source(guild, interaction.user, str(self.name.value))
        except (discord.HTTPException, OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Proxy creation failed: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content=f"✅ Created {source.mention}. Use Add / Change Route to select the destination.",
            embed=await _share_router_embed(guild),
            view=ShareRouterView(self.owner_id),
        )


class ShareRouterDeleteSourceConfirmView(_OwnedView):
    def __init__(self, owner_id: int, source_id: int) -> None:
        super().__init__(owner_id)
        self.source_id = int(source_id)

    @discord.ui.button(label="Delete Proxy Channel", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels):
            return await _private(interaction, "❌ Manage Channels permission is required to delete proxies.")
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await delete_custom_share_source(guild, self.source_id)
        except (discord.HTTPException, OSError, ValueError) as exc:
            return await interaction.edit_original_response(
                content=f"❌ Proxy deletion stopped: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content="✅ Custom proxy channel and its route removed. Built-in proxies were not changed.",
            embed=await _share_router_embed(guild),
            view=ShareRouterView(self.owner_id),
        )

    @discord.ui.button(label="Keep Proxy", emoji="↩️", style=discord.ButtonStyle.secondary)
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
        return await _private(interaction, "ℹ️ No custom proxy channels are available to delete.")

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not await _require_manage(pick_interaction):
            return
        if (
            not isinstance(resource, discord.TextChannel)
            or int(resource.id) not in set(await guild_custom_share_sources(int(guild.id)))
        ):
            return await _private(pick_interaction, "❌ This is no longer a registered custom proxy.")
        embed = discord.Embed(
            title="⚠️ Permanently delete proxy?",
            description=(
                f"Delete {resource.mention} from Discord and remove its saved forwarding rule? "
                "Any remaining messages in this proxy will also be deleted. "
                "The destination and four built-in proxy channels are never deleted."
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
        placeholder="Choose a custom proxy to delete…",
        title="Delete Custom Proxy Channel",
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
                content=f"❌ Could not verify missing proxy channels: {exc}",
                embed=None, view=ShareRouterView(self.owner_id),
            )
        await interaction.edit_original_response(
            content=f"✅ Pruned {count} custom proxy registration(s) not visible in Discord's live channel listing for this server. No Discord channel was deleted.",
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
            "One-time server setup: **Create Download Channel** on the Share Router menu. "
            "Members post a public video URL there; the result is sent privately in DM. "
            "Smaller files arrive as attachments; larger ones use expiring HTTPS Watch and "
            "Download links when hosting is available."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Download channel", value=channel.mention if channel else "Not configured", inline=False)
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

    @discord.ui.button(label="Back to Share Router", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)

class ShareRouterView(_OwnedView):
    @discord.ui.button(label="Use Existing Channel", emoji="📁", style=discord.ButtonStyle.primary, row=2)
    async def use_existing_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_manage(interaction):
            return
        await _open_existing_source_picker(interaction)

    @discord.ui.button(label="Create Proxy", emoji="➕", style=discord.ButtonStyle.primary, row=2)
    async def create_proxy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await interaction.response.send_modal(ShareRouterCreateSourceModal(int(interaction.user.id)))

    @discord.ui.button(label="Remove Missing Proxies", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def clean_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _replace(
            interaction,
            embed=discord.Embed(
                title="⚠️ Confirm stale proxy cleanup",
                description=(
                    "Only custom proxy IDs absent from Discord's channel listing in this server "
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

    @discord.ui.button(label="Delete Custom Proxy", emoji="🗑️", style=discord.ButtonStyle.secondary, row=2)
    async def delete_proxy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_delete_source_picker(interaction)

    @discord.ui.button(label="Create Download Channel", emoji="📥", style=discord.ButtonStyle.primary, row=3)
    async def create_download_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        existing_id = await guild_download_channel_id(int(guild.id))
        if existing_id:
            await interaction.edit_original_response(
                content="⚠️ This server already has a configured download channel. "
                        "Disable the old one first if it was deleted.",
                embed=None, view=None,
            )
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            await interaction.edit_original_response(
                content="❌ Dank Shield needs Manage Channels to create the video download channel.",
                embed=None, view=None,
            )
            return
        channel = None
        try:
            channel = await guild.create_text_channel(
                "video-downloads",
                topic="Post one public video link to receive a private DM. "
                      "Discord's DM upload limits apply; source links may remain "
                      "visible if the bot cannot delete messages.",
                reason="Dank Shield Share Router opt-in private video download channel",
            )
            await save_guild_download_channel(int(guild.id), int(channel.id))
        except Exception as exc:
            # Best-effort rollback only for a channel created by this click.
            if channel is not None:
                try:
                    await channel.delete(reason="Download configuration save failed")
                except discord.HTTPException:
                    pass
            await interaction.edit_original_response(
                content=f"❌ Download setup did not finish ({type(exc).__name__}). "
                        "Check channel permissions and try again.",
                embed=None, view=None,
            )
            return
        await interaction.edit_original_response(
            content=(
                f"✅ Created {channel.mention}. Members can paste one public video URL "
                "per message; Dank Shield will DM the file when it fits the DM "
                "limit, otherwise a link. Source posts are deleted only after "
                "successful DM delivery and only with Manage Messages."
            ),
            embed=None, view=None, allowed_mentions=_ALLOWED_MENTIONS,
        )

    @discord.ui.button(label="Download Settings", emoji="⏳", style=discord.ButtonStyle.secondary, row=3)
    async def download_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router_download_settings(interaction)

    @discord.ui.button(label="Disable Downloads", emoji="⏸️", style=discord.ButtonStyle.secondary, row=3)
    async def disable_downloads(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await save_guild_download_channel(int(interaction.guild.id), 0)
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"❌ Could not disable downloads ({type(exc).__name__}).",
                embed=None, view=None,
            )
            return
        await interaction.edit_original_response(
            content="✅ Auto-download DMs disabled for this server. "
                    "The Discord channel was preserved; remove it manually if desired.",
            embed=None, view=None,
        )

    @discord.ui.button(label="Create / Repair Hub", emoji="🛠️", style=discord.ButtonStyle.primary, row=0)
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

    @discord.ui.button(label="Add / Change Route", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_route(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_source_picker(interaction)

    @discord.ui.button(label="Remove Route", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _open_remove_picker(interaction)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_share_router(interaction, replace_message=True)

    @discord.ui.button(label="Media Health", emoji="🎞️", style=discord.ButtonStyle.secondary, row=1)
    async def media_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_manage(interaction):
            return
        await _private(interaction, embed=_share_media_health_embed())

    @discord.ui.button(label="Test Provider", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
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
