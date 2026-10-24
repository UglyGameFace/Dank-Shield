from __future__ import annotations

"""Final Discord-visible public command surface.

All implementation modules are registered first so their services, listeners,
persistent views, safety checks, and compatibility shims remain loaded. This
module then compacts only the *application-command tree* into a few stable
doorways backed by action-complete UI centers.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from ..navigation_registry import (
    CATEGORIES,
    FEATURES,
    NavigationCategory,
    NavigationFeature,
    category_by_key,
    feature_by_key,
    features_for_category,
    grouped_features,
    RECENT_UPDATES,
    recent_features,
    search_features,
)
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from ..ui import DankChoice, DankPickerView
from .public_setup_group import dank_group

_INSTALLED = False

_UPLOAD_CHOICES = [
    app_commands.Choice(name="Join Card Background", value="join_background"),
    app_commands.Choice(name="Exit Card Background", value="exit_background"),
    app_commands.Choice(name="Custom Card Font", value="custom_font"),
]


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


async def _replace_panel(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    elif interaction.message is not None:
        await interaction.response.edit_message(**payload)
    else:
        await interaction.response.send_message(**payload, ephemeral=True)


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


def _home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ DANK SHIELD",
        description=(
            "**CONTROL CENTER**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🧭 **{len(CATEGORIES)} sections**  •  ✨ **{len(FEATURES)} destinations**\n\n"
            "Pick a topic below, then choose what you want to do. "
            "Not sure where to start? Use **Find Feature** and type everyday words.\n"
            "Tap **New & Updated** to see what changed recently."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Private controls • about 15 minutes")
    return embed


def _help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Help",
        description="Use the category pages, **Find**, or the complete **Directory**. Feature owners still enforce their own permissions.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Normal entry",
        value="`/dank home` — categorized control center\n**🔎 Find** — search by normal words and aliases\n**📚 Directory** — complete categorized feature list",
        inline=False,
    )
    embed.add_field(
        name="Optional fast doorways",
        value=(
            "`/captions` — ordinary server voice captions and personal consent\n"
            "`/cinema` — Dank Cinema session, queue, info, and vote shortcuts\n"
            "`/mod` — moderation/member center\n"
            "`/movie` — backward-compatible Movie Night hub; optional magnet or .torrent attachment starts media\n"
            "`/role` — smart Roles & Profiles doorway with member/role shortcuts\n"
            "`/ticket` — current ticket controls\n"
            "`/tickets` — queues, ticket setup, routing, categories\n"
            "`/toke` — ping the configured opt-in crowd with optional text/media (Community & Pings starter role required)\n"
            "`/verify` — verification status/repair center"
        ),
        inline=False,
    )
    embed.add_field(
        name="Direct /dank shortcuts",
        value=(
            "`/dank protection` • `/dank voice-safety` • `/dank diagnostics` • "
            "`/dank stats` • `/dank status` • `/dank logs`\n"
            "`/dank welcome` • `/dank design` • `/dank share` • "
            "`/dank pings` • `/dank community` • `/dank profile` • `/dank help`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Uploads",
        value=(
            "`/dank upload` — choose **Join Card Background**, **Exit Card Background**, or **Custom Card Font**, "
            "then attach the file. Every non-upload card action remains in the Welcome/Exit menus."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • open a feature center to manage settings")
    return embed


def _asset_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📎 Card Assets",
        description=(
            "Discord cannot open an attachment picker from a button, so **card asset** uploads use the compact "
            "`/dank upload` command. Member-facing `/toke upload:` is a separate optional media attachment on the Toke post."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Upload choices",
        value=(
            "• **Join Card Background** — PNG/JPG/WEBP artwork\n"
            "• **Exit Card Background** — PNG/JPG/WEBP artwork\n"
            "• **Custom Card Font** — validated supported font file"
        ),
        inline=False,
    )
    embed.add_field(
        name="Remove uploaded font",
        value="Use **Clear Uploaded Font** below. No extra slash command is needed.",
        inline=False,
    )
    return embed


_FEATURE_ROUTE_KEYS = frozenset(
    {
        "setup_center",
        "verification",
        "verification_reminders",
        "action_reminder_center",
        "action_notification_systems",
        "action_builder",
        "action_safety_schedule",
        "action_my_preferences",
        "welcome",
        "member_setup_manager",
        "protection",
        "members_moderation",
        "roles_profiles",
        "profile_builder",
        "community_pings_manager",
        "community_tools",
        "embed_studio",
        "share_router",
        "share_downloads",
        "toke_settings",
        "somewhere_420",
        "community_hub",
        "movie_night",
        "tickets",
        "server_design",
        "card_assets",
        "live_captions",
        "voice_safety",
        "logs_activity",
        "server_stats",
        "status",
        "diagnostics",
        "my_profile",
        "my_member_setup",
        "profile_tags",
        "my_community_pings",
        "help",
    }
)

_REGISTRY_ROUTE_KEYS = frozenset(item.key for item in FEATURES)
if _FEATURE_ROUTE_KEYS != _REGISTRY_ROUTE_KEYS:
    missing = sorted(_REGISTRY_ROUTE_KEYS - _FEATURE_ROUTE_KEYS)
    extra = sorted(_FEATURE_ROUTE_KEYS - _REGISTRY_ROUTE_KEYS)
    raise RuntimeError(
        f"Dank Shield navigation registry routing mismatch missing={missing} extra={extra}"
    )


def _category_embed(category: NavigationCategory) -> discord.Embed:
    embed = discord.Embed(
        title=f"{category.emoji} {category.label}",
        description=(
            f"**Home › {category.label}**\n{category.description}\n\n"
            "Choose a button below. 🔒 means server staff must open it."
        ),
        color=discord.Color.blurple(),
    )
    for group_name, features in grouped_features(category.key):
        lines = []
        for feature in features:
            lock = " 🔒" if feature.manager_only else ""
            lines.append(
                f"{feature.emoji} **{feature.label}**{lock}\n"
                f"  {feature.description}"
            )
        embed.add_field(
            name=group_name,
            value="\n\n".join(lines)[:1024],
            inline=False,
        )
    embed.set_footer(text="Blue = main tool • Grey = browse • Green = confirm • Red = delete")
    return embed


def _directory_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📚 All Dank Shield Features",
        description=(
            "Complete registry of the current public UI. Choose a category below, "
            "or use **🔎 Find** from Home when you know the name or purpose."
        ),
        color=discord.Color.blurple(),
    )
    for category in CATEGORIES:
        features = features_for_category(category.key)
        labels = " • ".join(f"{item.emoji} {item.label}" for item in features)
        embed.add_field(
            name=f"{category.emoji} {category.label}",
            value=labels[:1024] if labels else "No registered features.",
            inline=False,
        )
    embed.set_footer(text=f"Dank Shield • {len(FEATURES)} registered feature destinations")
    return embed


def _search_embed(query: str, results: list[NavigationFeature]) -> discord.Embed:
    embed = discord.Embed(
        title="🔎 Find a Feature",
        description=(
            f"Search: **{query or 'all features'}**\n"
            "Choose a result below. Search understands common names such as "
            "`voice safety`, `download channel`, `sesh`, `captions`, and `share router`."
        ),
        color=discord.Color.blurple(),
    )
    if not results:
        embed.add_field(
            name="No matches",
            value="Try a broader phrase, or open **📚 Directory** from Home.",
            inline=False,
        )
    else:
        lines = []
        for feature in results[:10]:
            category = category_by_key(feature.category)
            category_name = category.label if category is not None else feature.category
            lines.append(f"{feature.emoji} **{feature.label}** — {category_name}")
        embed.add_field(name=f"Matches • {len(results)}", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Search opens the existing canonical feature owner; it does not duplicate feature logic.")
    return embed


def _recent_embed() -> discord.Embed:
    embed = discord.Embed(
        title="✨ What's New in Dank Shield",
        description=(
            "**Home › New & Updated**\n"
            "These improvements are merged into the bot's code. "
            "They appear in your server after its bot deployment is updated. "
            "Tap a button to open the feature."
        ),
        color=discord.Color.blurple(),
    )
    grouped: dict[str, list[str]] = {}
    for update in RECENT_UPDATES:
        feature = feature_by_key(update.key)
        if feature is None:
            continue
        grouped.setdefault(update.merged_date, []).append(
            f"{feature.emoji} **{feature.label}: {update.headline}**\n"
            f"  {update.detail}"
        )
    for date, lines in grouped.items():
        embed.add_field(
            name=f"Updated {date}",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )
    embed.set_footer(text="Only merged updates • Features not yet merged are not listed")
    return embed


async def _route_feature(interaction: discord.Interaction, feature_key: str) -> None:
    key = str(feature_key or "").strip()
    if key not in _FEATURE_ROUTE_KEYS:
        return await _private(interaction, "❌ That Dank Shield feature is not registered.")

    if key == "setup_center":
        from .public_command_hub import _invoke_saved
        return await _invoke_saved("setup", interaction)

    if key == "verification":
        from .public_verify_command_center import open_verify_command_center
        return await open_verify_command_center(interaction)

    if key == "action_reminder_center":
        from .public_action_center import open_action_center
        return await open_action_center(interaction)

    if key == "action_notification_systems":
        from .public_action_notification_directory import open_notification_owner_directory
        return await open_notification_owner_directory(interaction)

    if key == "action_builder":
        from .public_action_builder import open_action_builder
        return await open_action_builder(interaction)

    if key == "action_safety_schedule":
        from .public_action_schedule import open_schedule_panel
        return await open_schedule_panel(interaction)

    if key == "action_my_preferences":
        from .public_action_preferences import open_member_action_preferences
        return await open_member_action_preferences(interaction)

    if key == "verification_reminders":
        from .public_verify_reminder_ui import open_reminder_settings
        return await open_reminder_settings(interaction, int(interaction.user.id))

    if key == "welcome":
        from stoney_verify.welcome_setup_ui import open_welcome_setup
        return await open_welcome_setup(interaction)

    if key == "member_setup_manager":
        from .public_member_setup import open_member_setup_admin
        return await open_member_setup_admin(interaction)

    if key == "protection":
        from . import public_protection_center
        await public_protection_center._ack_protection_entry(interaction)
        from .public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        return await public_protection_center._refresh_panel(
            interaction,
            content="🛡️ Protection Center opened from Dank Shield navigation.",
        )

    if key == "members_moderation":
        from .public_mod_command_center import open_mod_command_center
        return await open_mod_command_center(interaction)

    if key == "roles_profiles":
        from .public_role_center import open_roles_profiles_center
        return await open_roles_profiles_center(interaction)

    if key == "profile_builder":
        from .public_self_roles_group import _post_profile_builder
        return await _post_profile_builder(
            interaction,
            title="Profile Panel",
            replace_message=True,
        )

    if key == "community_pings_manager":
        from .public_toke import open_community_ping_setup
        return await open_community_ping_setup(interaction, replace_message=True)

    if key == "community_tools":
        from .public_community_tools import open_community_tools
        return await open_community_tools(interaction, replace_message=True)

    if key == "embed_studio":
        from ..embed_studio_ui import open_embed_studio
        return await open_embed_studio(interaction)

    if key == "share_router":
        from .public_share_router import open_share_router
        return await open_share_router(interaction, replace_message=True)

    if key == "share_downloads":
        from .public_share_router import open_share_router_download_settings
        return await open_share_router_download_settings(interaction)

    if key == "toke_settings":
        from stoney_verify.toke_live_ui import open_toke_settings
        return await open_toke_settings(interaction, replace=True)

    if key == "somewhere_420":
        from .public_somewhere_420 import open_somewhere_420
        return await open_somewhere_420(interaction)

    if key == "community_hub":
        from .public_community_hub import open_community_hub
        return await open_community_hub(interaction, replace_message=True)

    if key == "movie_night":
        from .public_movie_night import open_movie_night
        return await open_movie_night(interaction, replace_message=True)

    if key == "tickets":
        from .public_ticket_command_center import open_ticket_operations_center
        return await open_ticket_operations_center(interaction)

    if key == "server_design":
        from . import public_design_bridge
        return await public_design_bridge.open_design_studio_from_setup(interaction)

    if key == "card_assets":
        return await _replace_panel(
            interaction,
            embed=_asset_embed(),
            view=CardAssetView(int(interaction.user.id)),
        )

    if key == "live_captions":
        from .public_live_captions import open_server_live_captions
        return await open_server_live_captions(interaction, replace_message=True)

    if key == "voice_safety":
        # Voice Safety remains owned by Protection Center; navigation must
        # never duplicate guild permissions, consent, or receiver management.
        from .public_voice_safety import open_voice_safety_center
        return await open_voice_safety_center(interaction)

    if key == "logs_activity":
        from .public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        from .public_setup_recommend import _open_advanced_logs_activity
        return await _open_advanced_logs_activity(interaction)

    if key == "server_stats":
        from .public_server_stats import open_server_stats_center
        return await open_server_stats_center(interaction)

    if key == "status":
        from .public_command_hub import _invoke_saved
        return await _invoke_saved("status", interaction)

    if key == "diagnostics":
        from .public_command_hub import _invoke_saved
        return await _invoke_saved("diagnostics", interaction)

    if key == "my_profile":
        from .public_command_hub import open_profile_entry
        return await open_profile_entry(interaction)

    if key == "my_member_setup":
        from .public_member_setup import open_member_setup
        return await open_member_setup(interaction, replace_message=True)

    if key == "profile_tags":
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _private(interaction, "❌ Profile Tags & Cosmetics only works inside a server.")
        from .public_self_roles_group import _open_profile_cosmetics
        return await _open_profile_cosmetics(
            interaction,
            guild,
            member,
            replace_message=True,
        )

    if key == "my_community_pings":
        from .public_toke import open_member_community_pings
        return await open_member_community_pings(interaction, replace_message=True)

    if key == "help":
        return await _replace_panel(
            interaction,
            embed=_help_embed(),
            view=CompactHelpView(int(interaction.user.id)),
        )


async def _open_home(interaction: discord.Interaction) -> None:
    await _replace_panel(
        interaction,
        embed=_home_embed(),
        view=CompactDankHomeView(int(interaction.user.id)),
    )


async def _open_category(interaction: discord.Interaction, category_key: str) -> None:
    category = category_by_key(category_key)
    if category is None:
        return await _private(interaction, "❌ That Dank Shield category is unavailable.")
    await _replace_panel(
        interaction,
        embed=_category_embed(category),
        view=FeatureCategoryView(int(interaction.user.id), category.key),
    )


async def _open_recent(interaction: discord.Interaction) -> None:
    await _replace_panel(
        interaction,
        embed=_recent_embed(),
        view=RecentFeaturesView(int(interaction.user.id)),
    )


async def _open_directory(interaction: discord.Interaction) -> None:
    await _replace_panel(
        interaction,
        embed=_directory_embed(),
        view=FeatureDirectoryView(int(interaction.user.id)),
    )


class FeatureSearchModal(discord.ui.Modal, title="Find a Dank Shield Feature"):
    query = discord.ui.TextInput(
        label="What are you looking for?",
        placeholder="invite shield, ping roles, member setup, captions…",
        required=True,
        min_length=1,
        max_length=100,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.query.value or "").strip()
        results = search_features(text)
        if not results:
            return await _replace_panel(
                interaction,
                embed=_search_embed(text, results),
                view=FeatureSearchEmptyView(self.owner_id),
            )

        choices = [
            DankChoice(
                label=item.label,
                value=item.key,
                description=(
                    f"{category_by_key(item.category).label if category_by_key(item.category) else item.category} • "
                    f"{item.description}"
                ),
                emoji=item.emoji,
            )
            for item in results
        ]

        async def picked(pick_interaction: discord.Interaction, value: str) -> None:
            await _route_feature(pick_interaction, value)

        async def home(home_interaction: discord.Interaction) -> None:
            await _open_home(home_interaction)

        view = DankPickerView(
            author_id=self.owner_id,
            choices=choices,
            on_pick=picked,
            custom_id="dank:navigation:search:results:v1",
            placeholder="Choose a matching feature…",
            title="Find a Feature",
            on_home=home,
            home_label="Dank Shield Home",
        )
        await _replace_panel(interaction, embed=_search_embed(text, results), view=view)


class _HomeSectionSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="🧭 Choose a section…",
            min_values=1,
            max_values=1,
            custom_id="dank:home:sections:v1",
            row=0,
            options=[
                discord.SelectOption(
                    label=category.label[:100],
                    value=category.key,
                    emoji=category.emoji,
                    description=category.description[:100],
                )
                for category in CATEGORIES
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = str(self.values[0] if self.values else "").strip()
        await _open_category(interaction, value)


# Blue marks the main starting point, never a staff-access level.
# Real authorization remains inside each existing feature owner.
_MAIN_FEATURE_KEYS = frozenset({
    "setup_center", "verification", "action_reminder_center", "protection",
    "roles_profiles", "share_router", "tickets", "embed_studio",
    "live_captions", "server_stats", "my_profile", "help",
})


class _FeatureButton(discord.ui.Button):
    def __init__(self, feature: NavigationFeature, *, row: int) -> None:
        super().__init__(
            label=feature.label,
            emoji=feature.emoji,
            style=(discord.ButtonStyle.primary if feature.key in _MAIN_FEATURE_KEYS
                   else discord.ButtonStyle.secondary),
            custom_id=f"dank:navigation:feature:{feature.key}:v1"[:100],
            row=row,
        )
        self.feature_key = feature.key

    async def callback(self, interaction: discord.Interaction) -> None:
        await _route_feature(interaction, self.feature_key)


class _FindFeatureButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(
            label="Find Feature",
            emoji="🔎",
            style=discord.ButtonStyle.primary,
            custom_id="dank:navigation:find:v1",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeatureSearchModal(int(interaction.user.id)))


class _RecentFeaturesButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="New & Updated",
            emoji="✨",
            style=discord.ButtonStyle.primary,
            custom_id="dank:navigation:recent:v1",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_recent(interaction)


class _AllFeaturesButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(
            label="Directory",
            emoji="📚",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:navigation:all:v1",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_directory(interaction)


class _HelpNavigationButton(discord.ui.Button):
    def __init__(self, *, row: int = 1) -> None:
        super().__init__(
            label="Help",
            emoji="❓",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:navigation:help:v1",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _route_feature(interaction, "help")


class _HomeButton(discord.ui.Button):
    def __init__(self, *, row: int, label: str = "Home") -> None:
        super().__init__(
            label=label,
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank:navigation:home:{row}:{label.lower().replace(' ', '_')}:v1"[:100],
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_home(interaction)


class _CloseNavigationButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank:navigation:close:{row}:v1",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _replace_panel(interaction, content="Dank Shield navigation closed.", embed=None, view=None)


class _CategoryNavButton(discord.ui.Button):
    def __init__(self, *, category_key: str, action: str, row: int) -> None:
        self.category_key = str(category_key)
        self.action = str(action)
        labels = {
            "back": ("Back", "⬅️"),
            "home": ("Home", "🏠"),
            "refresh": ("Refresh", "🔄"),
        }
        label, emoji = labels[self.action]
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank:navigation:category:{self.category_key}:{self.action}:v1"[:100],
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action in {"back", "home"}:
            await _open_home(interaction)
        else:
            await _open_category(interaction, self.category_key)


class CompactDankHomeView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(_HomeSectionSelect())
        self.add_item(_FindFeatureButton(row=1))
        self.add_item(_AllFeaturesButton(row=1))
        self.add_item(_HelpNavigationButton(row=1))
        self.add_item(_CloseNavigationButton(row=1))
        self.add_item(_RecentFeaturesButton(row=2))


class FeatureCategoryView(_OwnedView):
    def __init__(self, owner_id: int, category_key: str) -> None:
        super().__init__(owner_id)
        self.category_key = str(category_key)
        groups = grouped_features(self.category_key)
        features = tuple(feature for _label, group in groups for feature in group)
        for index, feature in enumerate(features):
            self.add_item(_FeatureButton(feature, row=index // 5))

        nav_row = max(1, (len(features) + 4) // 5)
        if nav_row > 4:
            raise RuntimeError(
                f"Dank Shield category {self.category_key!r} exceeds the mobile component layout budget"
            )
        self.add_item(_CategoryNavButton(category_key=self.category_key, action="back", row=nav_row))
        self.add_item(_CategoryNavButton(category_key=self.category_key, action="home", row=nav_row))
        self.add_item(_CategoryNavButton(category_key=self.category_key, action="refresh", row=nav_row))
        self.add_item(_CloseNavigationButton(row=nav_row))


class RecentFeaturesView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        features = recent_features()
        for index, feature in enumerate(features):
            self.add_item(_FeatureButton(feature, row=index // 5))
        row = max(1, (len(features) + 4) // 5)
        if row > 4:
            raise RuntimeError("Recent features exceed mobile Discord component limit")
        self.add_item(_HomeButton(row=row))
        self.add_item(_CloseNavigationButton(row=row))


class FeatureDirectoryView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(_HomeSectionSelect())
        self.add_item(_FindFeatureButton(row=1))
        self.add_item(_HelpNavigationButton(row=1))
        self.add_item(_HomeButton(row=1))
        self.add_item(_CloseNavigationButton(row=1))


class FeatureSearchEmptyView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(_FindFeatureButton(row=0))
        self.add_item(_AllFeaturesButton(row=0))
        self.add_item(_HomeButton(row=0))
        self.add_item(_CloseNavigationButton(row=0))


class CompactHelpView(_OwnedView):
    @discord.ui.button(label="Control Center", emoji="🏠", style=discord.ButtonStyle.primary, custom_id="dank:help:home:v1")
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank:help:close:v1")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Help closed.", embed=None, view=None)


class CardAssetView(_OwnedView):
    @discord.ui.button(label="Welcome / Exit Studio", emoji="👋", style=discord.ButtonStyle.primary, custom_id="dank:assets:studio:v1", row=0)
    async def studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction)

    @discord.ui.button(label="Clear Uploaded Font", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="dank:assets:clear_font:v1", row=0)
    async def clear_font(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_welcome_card_studio import welcome_card_font_clear
        await welcome_card_font_clear(interaction)

    @discord.ui.button(label="Control Center", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank:assets:home:v1", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def replace_with_compact_dank_home(
    interaction: discord.Interaction,
    *,
    content: str = "",
) -> None:
    """Atomically replace a component message with a fresh canonical home view."""
    await interaction.response.edit_message(
        content=content or None,
        embed=_home_embed(),
        view=CompactDankHomeView(int(interaction.user.id)),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def open_compact_dank_home(
    interaction: discord.Interaction,
    *,
    content: str = "",
) -> None:
    await _private(
        interaction,
        content=content,
        embed=_home_embed(),
        view=CompactDankHomeView(int(interaction.user.id)),
    )


@app_commands.describe(
    asset="What card asset are you uploading?",
    file="Attach the image or font file.",
)
@app_commands.choices(asset=_UPLOAD_CHOICES)
async def consolidated_asset_upload(
    interaction: discord.Interaction,
    asset: app_commands.Choice[str],
    file: discord.Attachment,
) -> None:
    value = str(getattr(asset, "value", asset) or "").strip().lower()
    if value == "join_background":
        from .public_welcome_group import welcome_card_upload
        return await welcome_card_upload(interaction, background=file)
    if value == "exit_background":
        from .public_exit_card_studio import exit_card_upload
        return await exit_card_upload(interaction, background=file)
    if value == "custom_font":
        from .public_welcome_card_studio import welcome_card_font_upload
        return await welcome_card_font_upload(interaction, font_file=file)
    await _private(interaction, "❌ That upload type is unavailable.")


def _remove_tree_command(tree: Any, name: str) -> None:
    try:
        tree.remove_command(name, guild=None)
    except Exception:
        try:
            commands = getattr(tree, "_global_commands", None)
            if isinstance(commands, dict):
                commands.pop(name, None)
        except Exception:
            pass


def _standalone(name: str, description: str, callback: Any) -> app_commands.Command:
    resolved = getattr(callback, "callback", callback)
    if not callable(resolved):
        raise TypeError(f"{name} callback is not callable")
    return app_commands.Command(name=name, description=description, callback=resolved)


# A small set of discoverable direct commands, each delegating to its existing
# Home feature owner. Keep this short enough for Discord's 25 subcommand limit,
# with extra payload headroom for future command schema changes.
DANK_SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("community", "Open the community hub, LFG and activities.", "community_hub"),
    ("design", "Open channel naming and server design settings.", "server_design"),
    ("diagnostics", "Check setup, startup and operational health.", "diagnostics"),
    ("help", "See the Dank Shield feature guide and navigation.", "help"),
    ("logs", "Configure logging destinations and activity tracking.", "logs_activity"),
    ("pings", "Manage community and notification ping roles.", "community_pings_manager"),
    ("profile", "Open your member profile and privacy settings.", "my_profile"),
    ("protection", "Open AntiNuke, Spam Guard, invites and Automod.", "protection"),
    ("share", "Open Share Router and inline media settings.", "share_router"),
    ("stats", "Manage live server stats and accurate counters.", "server_stats"),
    ("status", "View the bot's service and runtime status.", "status"),
    ("voice-safety", "Configure disclosed AI voice safety and alerts.", "voice_safety"),
    ("welcome", "Manage welcome, join and exit messages and cards.", "welcome"),
)


def _shortcut_callback(feature_key: str) -> Any:
    """Use the existing owner; do not create alternative command/business logic."""

    async def open_shortcut(interaction: discord.Interaction) -> None:
        # These UI owners use component edit-message when reached through Home.
        # Fresh slash interactions must receive a new private response instead.
        if feature_key == "community_hub":
            from .public_community_hub import open_community_hub
            return await open_community_hub(interaction, replace_message=False)
        if feature_key == "share_router":
            from .public_share_router import open_share_router
            return await open_share_router(interaction, replace_message=False)
        if feature_key == "community_pings_manager":
            from .public_toke import open_community_ping_setup
            return await open_community_ping_setup(interaction, replace_message=False)
        if feature_key == "server_stats" and not interaction.response.is_done():
            # Canonical stats editor switches to edit_original_response once
            # deferred, instead of edit_message (invalid for slash commands).
            await interaction.response.defer(ephemeral=True, thinking=True)
        await _route_feature(interaction, feature_key)

    return open_shortcut


def _compact_dank_children(tree: Any) -> int:
    setup_command = dank_group.get_command("setup")
    if not isinstance(setup_command, app_commands.Command):
        raise RuntimeError("canonical /dank setup is unavailable before public compaction")

    for item in list(getattr(dank_group, "commands", []) or []):
        try:
            dank_group.remove_command(str(getattr(item, "name", "")))
        except Exception:
            pass

    dank_group.add_command(_standalone("home", "Open the complete Dank Shield control center.", open_compact_dank_home))
    # Re-add the exact canonical setup command object. This preserves its existing
    # permission checks, callback, and single-owner setup implementation.
    dank_group.add_command(setup_command)
    upload = app_commands.Command(
        name="upload",
        description="Upload Join/Exit card artwork or a custom card font.",
        callback=consolidated_asset_upload,
    )
    dank_group.add_command(upload)

    for name, description, feature_key in DANK_SHORTCUTS:
        if feature_key not in _FEATURE_ROUTE_KEYS:
            raise RuntimeError(f"shortcut {name} has no canonical registered feature owner")
        dank_group.add_command(_standalone(name, description, _shortcut_callback(feature_key)))

    from .public_cheers_owner import cheers_group
    dank_group.add_command(cheers_group)

    from .public_command_hub import DANK_PAYLOAD_SAFETY_LIMIT, dank_payload_size
    size = dank_payload_size(tree)
    if size > DANK_PAYLOAD_SAFETY_LIMIT:
        raise RuntimeError(
            f"compact v2 /dank payload={size} exceeds safety limit={DANK_PAYLOAD_SAFETY_LIMIT}"
        )
    return size


def install_compact_public_surface_v2(bot: Any, tree: Any) -> dict[str, Any]:
    global _INSTALLED
    from .public_community_tools import ensure_community_tools_runtime
    ensure_community_tools_runtime(bot)
    from ..community_hub_runtime import ensure_community_hub_runtime
    ensure_community_hub_runtime(bot)
    from ..services.naming_identity import install_naming_identity_runtime
    install_naming_identity_runtime(bot)

    if _INSTALLED:
        roots = sorted(str(getattr(item, "name", "")) for item in tree.get_commands(guild=None))
        return {"installed": True, "roots": roots}

    from .public_live_captions import open_server_live_captions_command
    from .public_mod_command_center import open_mod_command_center
    from .public_movie_night import build_cinema_command_group, open_movie_night_command
    from .public_role_center import open_role_command
    from .public_ticket_command_center import (
        open_current_ticket_center,
        open_ticket_operations_center,
    )
    from .public_toke import open_toke_command
    from stoney_verify.toke_live_ui import register_toke_runtime
    register_toke_runtime(bot)
    from .public_verify_command_center import open_verify_command_center

    replacements = (
        ("captions", "Open Live Captions for ordinary server voice channels.", open_server_live_captions_command),
        ("mod", "Open the complete moderation and member action center.", open_mod_command_center),
        ("movie", "Open Movie Night, or attach an authorized magnet/.torrent.", open_movie_night_command),
        ("role", "Open Roles & Profiles or jump to a member or role.", open_role_command),
        ("ticket", "Open controls for the current or selected ticket.", open_current_ticket_center),
        ("tickets", "Open ticket queues, lookup, setup, routing, and category tools.", open_ticket_operations_center),
        ("toke", "Ping the opt-in sesh crowd.", open_toke_command),
        ("verify", "Open the complete verification status and repair center.", open_verify_command_center),
    )
    for name, description, callback in replacements:
        _remove_tree_command(tree, name)
        tree.add_command(_standalone(name, description, callback))

    _remove_tree_command(tree, "cinema")
    tree.add_command(build_cinema_command_group())

    for retired_root in ("ticket-intake", "ticket-category", "ticket-panel"):
        _remove_tree_command(tree, retired_root)

    size = _compact_dank_children(tree)
    roots = sorted(str(getattr(item, "name", "")) for item in tree.get_commands(guild=None))
    expected_roots = {"captions", "cinema", "dank", "mod", "movie", "role", "ticket", "tickets", "toke", "verify"}
    command_roots = {name for name in roots if name != "View Dank Profile"}
    if command_roots != expected_roots:
        raise RuntimeError(
            f"compact v2 final roots mismatch expected={sorted(expected_roots)} actual={sorted(command_roots)}"
        )

    dank_children = sorted(str(getattr(item, "name", "")) for item in dank_group.commands)
    expected_children = sorted(["home", "setup", "upload", "cheers"] + [name for name, _, _ in DANK_SHORTCUTS])
    if dank_children != expected_children:
        raise RuntimeError(f"compact v2 /dank children mismatch: {dank_children}")

    _INSTALLED = True
    result = {
        "installed": True,
        "roots": roots,
        "dank_children": dank_children,
        "dank_payload": size,
    }
    print(
        "✅ public_command_surface_v2 compact UI installed "
        f"roots={roots} dank_children={dank_children} payload={size}"
    )
    return result


__all__ = [
    "CardAssetView",
    "CompactDankHomeView",
    "FeatureCategoryView",
    "FeatureDirectoryView",
    "FeatureSearchModal",
    "consolidated_asset_upload",
    "install_compact_public_surface_v2",
    "open_compact_dank_home",
]
