from __future__ import annotations

"""Menu-first public Share Router UI and production runtime registrar."""

from typing import Any, Optional

import discord

from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    SHARE_ROUTER_CATEGORY_NAME,
    is_share_router_design_resource,
    share_source_key,
)
from stoney_verify.share_router_runtime import (
    create_or_repair_hidden_share_hub,
    ensure_share_router_runtime,
    find_share_router_category,
    find_share_source_channel,
    guild_routes,
    remove_route,
    route_health,
    route_permission_blockers,
    save_route,
    source_age_blocker,
    source_privacy_blocker,
)
from stoney_verify.ui import DankChoice, DankGuildResourceBrowserView, DankPickerView

_ALLOWED_MENTIONS = discord.AllowedMentions.none()
_REGISTERED = False


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
    category = find_share_router_category(guild)

    embed = discord.Embed(
        title="🔗 Share Router",
        description=(
            "Use private, plain proxy channels as mobile share-sheet destinations, then let Dank Shield "
            "forward the post into the real channel. This is useful when Discord does not list an "
            "age-restricted destination in the phone share sheet."
        ),
        color=discord.Color.blurple(),
    )

    if category is None:
        hub_status = "❌ Missing. Use Create / Repair Hub."
    elif str(category.name) != SHARE_ROUTER_CATEGORY_NAME:
        hub_status = f"⚠️ Found styled/legacy hub: {category.name}. Repair will restore the plain name."
    else:
        found = sum(1 for name in DEFAULT_SHARE_CHANNELS if find_share_source_channel(category, name) is not None)
        hub_status = f"✅ {category.name} • {found}/{len(DEFAULT_SHARE_CHANNELS)} proxy channels present."
    embed.add_field(name="Proxy hub", value=hub_status[:1024], inline=False)

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
        line = f"{icon} {source_text} → {target_text}"
        if health.blockers:
            line += f" • {health.blockers[0]}"
        elif health.warnings:
            line += f" • {health.warnings[0]}"
        lines.append(line[:300])

    if routes:
        embed.add_field(
            name=f"Saved routes • {healthy}/{len(routes)} healthy",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Saved routes",
            value="None yet. Create or repair the hub, then choose Add / Change Route.",
            inline=False,
        )

    embed.add_field(
        name="Safety boundary",
        value=(
            "Proxy sources stay non-age-restricted and hidden from @everyone. Dank Shield also checks "
            "that the sender has normal View Channel + Send Messages permission in the destination "
            "before forwarding. Discord still controls who can view age-restricted destinations."
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
    embed.set_footer(text="Plain proxy names are reserved infrastructure and are excluded from Dank Design.")
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own Share Router panel to use these controls.")
        return False


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


def _source_choices(guild: discord.Guild, routes: list[dict[str, Any]]) -> list[DankChoice]:
    category = find_share_router_category(guild)
    if category is None:
        return []

    target_by_source = {
        _safe_int(route.get("source_channel_id"), 0): _safe_int(route.get("target_channel_id"), 0)
        for route in routes
    }
    choices: list[DankChoice] = []
    for canonical in DEFAULT_SHARE_CHANNELS:
        source = find_share_source_channel(category, canonical)
        if source is None:
            continue
        target = guild.get_channel(target_by_source.get(int(source.id), 0))
        description = (
            f"Currently routes to {_target_label(target)}"
            if isinstance(target, discord.TextChannel)
            else "No destination saved yet"
        )
        choices.append(
            DankChoice(
                label=canonical,
                value=str(int(source.id)),
                description=description,
                emoji="🔗",
            )
        )
    return choices


async def _open_source_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")

    routes = await guild_routes(int(guild.id))
    choices = _source_choices(guild, routes)
    if not choices:
        return await _private(
            interaction,
            "❌ No canonical proxy channels are available. Run Create / Repair Hub first.",
        )

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, value: str) -> None:
        source = guild.get_channel(_safe_int(value, 0))
        if not isinstance(source, discord.TextChannel):
            return await _private(pick_interaction, "❌ That proxy source no longer exists.")
        await _open_target_browser(pick_interaction, source)

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=picked,
        custom_id="dank:share_router:source",
        placeholder="Choose the plain proxy source…",
        title="Choose Share Router Source",
        on_home=back,
        home_label="Share Router",
    )
    embed = discord.Embed(
        title="🔗 Choose Proxy Source",
        description=(
            "Pick the plain channel that should appear in the mobile share sheet. "
            "Next you will choose its real destination."
        ),
        color=discord.Color.blurple(),
    )
    await _replace(interaction, embed=embed, view=view)


async def _open_target_browser(
    interaction: discord.Interaction,
    source: discord.TextChannel,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")

    async def back(back_interaction: discord.Interaction) -> None:
        await _open_source_picker(back_interaction)

    async def picked_target(
        pick_interaction: discord.Interaction,
        resource: Any,
    ) -> None:
        if not isinstance(resource, discord.TextChannel):
            return await _private(pick_interaction, "❌ Pick a normal text channel.")

        blockers: list[str] = []
        age = source_age_blocker(source)
        if age:
            blockers.append(age)
        privacy = source_privacy_blocker(source)
        if privacy:
            blockers.append(privacy)
        blockers.extend(route_permission_blockers(source, resource, delete_source=True))

        if blockers:
            embed = discord.Embed(
                title="🚫 Share Route Not Saved",
                description="\n".join(f"• {item}" for item in blockers)[:4000],
                color=discord.Color.red(),
            )
            embed.add_field(
                name="What to fix",
                value=(
                    "Keep the proxy source non-age-restricted, hide it from @everyone, and make sure "
                    "Dank Shield can read/delete there and send in the destination."
                ),
                inline=False,
            )
            return await _private(pick_interaction, embed=embed)

        await save_route(
            int(guild.id),
            source_channel_id=int(source.id),
            target_channel_id=int(resource.id),
            delete_source=True,
            created_by_id=int(pick_interaction.user.id),
        )
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
        title=f"Choose Destination for #{source.name}",
        placeholder="Search or choose the real destination…",
        predicate=allowed_target,
        on_home=back,
        home_label="Back to proxy sources",
        empty_message="No eligible text-channel destinations are visible in the server cache.",
    )
    await _replace(interaction, embed=browser.embed(), view=browser)


async def _open_remove_picker(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, "❌ Use Share Router inside a server.")

    routes = await guild_routes(int(guild.id))
    choices: list[DankChoice] = []
    for route in routes:
        source_id = _safe_int(route.get("source_channel_id"), 0)
        target_id = _safe_int(route.get("target_channel_id"), 0)
        source = guild.get_channel(source_id)
        target = guild.get_channel(target_id)
        source_label = f"#{source.name}" if isinstance(source, discord.TextChannel) else f"source {source_id}"
        choices.append(
            DankChoice(
                label=source_label,
                value=str(source_id),
                description=f"Destination: {_target_label(target if isinstance(target, discord.TextChannel) else None)}",
                emoji="🗑️",
            )
        )

    if not choices:
        return await _private(interaction, "ℹ️ There are no saved Share Router routes to remove.")

    async def back(back_interaction: discord.Interaction) -> None:
        await open_share_router(back_interaction, replace_message=True)

    async def picked(pick_interaction: discord.Interaction, value: str) -> None:
        await remove_route(int(guild.id), _safe_int(value, 0))
        await open_share_router(pick_interaction, replace_message=True)

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices[:25],
        on_pick=picked,
        custom_id="dank:share_router:remove",
        placeholder="Choose a saved route to remove…",
        title="Remove Share Route",
        on_home=back,
        home_label="Share Router",
    )
    await _replace(
        interaction,
        embed=discord.Embed(
            title="🗑️ Remove Share Route",
            description="This removes only the saved forwarding rule. It does not delete channels.",
            color=discord.Color.orange(),
        ),
        view=view,
    )


class ShareRouterView(_OwnedView):
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

    global _REGISTERED
    _ = tree
    if _REGISTERED:
        return
    if not ensure_share_router_runtime(bot):
        raise RuntimeError("Share Router runtime listener could not be installed.")
    _REGISTERED = True


__all__ = [
    "ShareRouterView",
    "open_share_router",
    "register_public_share_router",
]
