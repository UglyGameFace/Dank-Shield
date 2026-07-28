from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

import discord
from discord import app_commands

from .public_self_roles_group import profile_group
from .public_setup_group import _require_setup_permission
from stoney_verify.guild_config import get_guild_config, upsert_guild_config
from stoney_verify.profile_card_runtime import (
    LIVE_ALLOWED_FIELDS_KEY,
    LIVE_CHANNEL_IDS_KEY,
    LIVE_ENABLED_KEY,
    LiveProfileCardRuntime,
    _copy_base_profile_embed,
    parse_live_card_config,
    render_live_profile_card,
)
from stoney_verify.profile_card_service import (
    DEFAULT_PROFILE_PREFERENCES,
    InvalidPlatformProfile,
    display_profile_username,
    effective_preferences,
    PLATFORM_SPECS,
    ProfileStorageUnavailable,
    get_effective_profile_settings,
    get_profile_guild_settings,
    get_profile_user,
    platform_entry_mode,
    remove_platform_identity,
    save_platform_identity,
    upsert_profile_guild_settings,
    upsert_profile_user_preferences,
)


_RUNTIME_ATTRIBUTE = "_dank_live_profile_card_runtime"
_REGISTERED = False

_PLATFORM_CHOICES = [
    app_commands.Choice(name=spec.label, value=key)
    for key, spec in PLATFORM_SPECS.items()
]


def _yes_no(value: Any) -> str:
    return "✅ On" if bool(value) else "❌ Off"


def _channel_ids(value: Any) -> list[int]:
    from stoney_verify.profile_card_runtime import _channel_ids as parse_channel_ids

    return sorted(parse_channel_ids(value))


async def _defer_private(
    interaction: discord.Interaction,
    *,
    component_update: bool = False,
) -> None:
    if interaction.response.is_done():
        return
    if component_update:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _send_private(
    interaction: discord.Interaction,
    **kwargs: Any,
) -> None:
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    if not interaction.response.is_done():
        await interaction.response.send_message(**kwargs)
    else:
        await interaction.followup.send(**kwargs)


async def _safe_ephemeral(
    interaction: discord.Interaction,
    content: str,
    *,
    ok: bool = True,
) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        await _send_private(
            interaction,
            content=prefix + str(content or "")[:1900],
        )
    except Exception:
        pass


def _profile_runtime(client: Any) -> Optional[LiveProfileCardRuntime]:
    runtime = getattr(client, _RUNTIME_ATTRIBUTE, None)
    return runtime if isinstance(runtime, LiveProfileCardRuntime) else None


async def invalidate_member_live_cards(
    client: Any,
    guild: discord.Guild,
    user_id: int,
    *,
    all_guilds: bool = False,
) -> None:
    runtime = _profile_runtime(client)
    if runtime is None:
        return
    if all_guilds:
        await runtime.remove_user_cards_all_guilds(int(user_id))
    else:
        await runtime.remove_user_cards(guild, int(user_id))


async def _settings_payload(guild_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    user_row, guild_row = await asyncio.gather(
        get_profile_user(user_id),
        get_profile_guild_settings(guild_id, user_id),
    )
    effective = {
        "preferences": effective_preferences(
            user_row.get("preferences"),
            guild_row.get("settings"),
        ),
        "platforms": dict(user_row.get("platforms") or {}),
    }
    return user_row, guild_row, effective


def _settings_embed(
    member: discord.Member,
    user_row: Mapping[str, Any],
    guild_row: Mapping[str, Any],
    effective: Mapping[str, Any],
) -> discord.Embed:
    preferences = dict(effective.get("preferences") or {})
    global_preferences = dict(user_row.get("preferences") or {})
    local = dict(guild_row.get("settings") or {})
    platforms = dict(user_row.get("platforms") or {})

    def shown(value: Any) -> str:
        return "✅ Shown" if bool(value) else "❌ Hidden"

    embed = discord.Embed(
        title="🔐 Profile Privacy",
        description=(
            "The top row handles the important stuff: turn your live signature on or off, manage gaming/social "
            "accounts, preview it, or go back. The buttons below change optional details."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Right now in this server",
        value=(
            f"**Live signature:** {'✅ On' if preferences.get('live_cards_enabled', True) else '⏸️ Off'}\n"
            f"**Server roles:** {shown(preferences.get('show_server_roles', False))}\n"
            f"**Profile tags:** {shown(preferences.get('show_profile_tags', True))}\n"
            f"**Account dates:** {shown(preferences.get('show_account_dates', True))}\n"
            f"**Gaming/social accounts:** {shown(preferences.get('show_platforms', True))}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Your default choices",
        value=(
            f"Server roles: {'Show' if global_preferences.get('show_server_roles', False) else 'Hide'} • "
            f"Profile tags: {'Show' if global_preferences.get('show_profile_tags', True) else 'Hide'} • "
            f"Dates: {'Show' if global_preferences.get('show_account_dates', True) else 'Hide'} • "
            f"Accounts: {'Show' if global_preferences.get('show_platforms', True) else 'Hide'}"
        ),
        inline=False,
    )
    hidden_here = [
        label
        for key, label in (
            ("live_cards_enabled", "live signature"),
            ("show_server_roles", "server roles"),
            ("show_profile_tags", "profile tags"),
            ("show_account_dates", "account dates"),
            ("show_platforms", "gaming/social accounts"),
        )
        if local.get(key) is False
    ]
    embed.add_field(
        name="Different only in this server",
        value=(
            "Hidden here: " + ", ".join(hidden_here)
            if hidden_here
            else "Nothing special is hidden here; your default choices are being used."
        ),
        inline=False,
    )

    identity_lines: list[str] = []
    for key, spec in PLATFORM_SPECS.items():
        entry = platforms.get(key)
        if not isinstance(entry, Mapping):
            continue
        username = str(entry.get("username") or "").strip()
        mode = platform_entry_mode(entry)
        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"
        display_value = (
            f"`{display_profile_username(username)}`"
            if username and mode != "logo"
            else "Logo only"
        )
        prefix = f"{spec.emoji} " if spec.emoji else ""
        identity_lines.append(
            f"{prefix}**{spec.label}:** {display_value} — {visibility} • {mode.title()}"
        )
    account_summary = "\n".join(identity_lines)[:820] if identity_lines else "No accounts saved yet."
    embed.add_field(
        name="Gaming & social accounts",
        value=(
            account_summary
            + "\n\nUse **Manage Accounts** below to add, edit, remove, or change an account's Public/Private status."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Privacy rule",
        value=(
            "Only you can make a saved identity public. Server managers may restrict which fields are allowed, "
            "but they cannot expose anything you kept private."
        ),
        inline=False,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Dank Shield profile settings • private response")
    return embed


class ProfileSettingsView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        guild_id: int,
        user_preferences: Mapping[str, Any],
        guild_settings: Mapping[str, Any],
    ) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.guild_id = int(guild_id)
        global_values = dict(user_preferences or {})
        local_values = dict(guild_settings or {})
        specs = (
            ("Live", "live_cards_enabled", "🪪"),
            ("Server Roles", "show_server_roles", "🏷️"),
            ("Profile Tags", "show_profile_tags", "🎭"),
            ("Dates", "show_account_dates", "📅"),
            ("Platforms", "show_platforms", "🔗"),
        )
        for label, key, emoji in specs:
            self.add_item(_GlobalPrivacyToggleButton(label, key, global_values, emoji, 0))
        for label, key, emoji in specs:
            self.add_item(_GuildPrivacyToggleButton(label, key, local_values, emoji, 1))
        self.add_item(_PreviewProfileButton(row=2))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _safe_ephemeral(interaction, "Only the member who opened these settings can use them.", ok=False)
            return False
        if interaction.guild is None or int(interaction.guild.id) != self.guild_id:
            await _safe_ephemeral(interaction, "Reopen profile settings in this server.", ok=False)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        user_row, guild_row, effective = await _settings_payload(self.guild_id, self.author_id)
        preferences = dict(effective.get("preferences") or {})
        payload = {
            "embed": _settings_embed(member, user_row, guild_row, effective),
            "view": ProfileSettingsView(
                author_id=self.author_id,
                guild_id=self.guild_id,
                user_preferences=dict(user_row.get("preferences") or {}),
                guild_settings=dict(guild_row.get("settings") or {}),
            ),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.edit_original_response(**payload)
        else:
            await interaction.response.edit_message(**payload)


class _GlobalPrivacyToggleButton(discord.ui.Button):
    def __init__(
        self,
        label: str,
        key: str,
        preferences: Mapping[str, Any],
        emoji: str,
        row: int,
    ) -> None:
        self.preference_key = key
        current = bool(preferences.get(key, DEFAULT_PROFILE_PREFERENCES.get(key, True)))
        super().__init__(
            label=f"Hide {label} Everywhere" if current else f"Show {label} Everywhere",
            emoji=emoji,
            style=discord.ButtonStyle.secondary if current else discord.ButtonStyle.success,
            custom_id=f"dank:profilecard:v2:global:{key}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        await _defer_private(interaction, component_update=True)
        try:
            user_row = await get_profile_user(view.author_id, refresh=True)
            default = DEFAULT_PROFILE_PREFERENCES.get(self.preference_key, True)
            current = bool(dict(user_row.get("preferences") or {}).get(self.preference_key, default))
            await upsert_profile_user_preferences(
                view.author_id,
                {self.preference_key: not current},
            )
            if interaction.guild is not None:
                await invalidate_member_live_cards(
                    interaction.client,
                    interaction.guild,
                    view.author_id,
                    all_guilds=True,
                )
            await view.refresh(interaction)
        except ProfileStorageUnavailable:
            await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing was changed.", ok=False)


class _GuildPrivacyToggleButton(discord.ui.Button):
    def __init__(
        self,
        label: str,
        key: str,
        settings: Mapping[str, Any],
        emoji: str,
        row: int,
    ) -> None:
        self.preference_key = key
        hidden = settings.get(key) is False
        super().__init__(
            label=f"Use Default {label} Here" if hidden else f"Hide {label} In This Server",
            emoji=emoji,
            style=discord.ButtonStyle.success if hidden else discord.ButtonStyle.secondary,
            custom_id=f"dank:profilecard:v2:guild:{key}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        await _defer_private(interaction, component_update=True)
        try:
            guild_row = await get_profile_guild_settings(view.guild_id, view.author_id, refresh=True)
            settings = dict(guild_row.get("settings") or {})
            hidden = settings.get(self.preference_key) is False
            await upsert_profile_guild_settings(
                view.guild_id,
                view.author_id,
                {self.preference_key: None if hidden else False},
            )
            if interaction.guild is not None:
                await invalidate_member_live_cards(
                    interaction.client,
                    interaction.guild,
                    view.author_id,
                )
            await view.refresh(interaction)
        except ProfileStorageUnavailable:
            await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing was changed.", ok=False)


class _PreviewProfileButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Preview Public Card",
            emoji="👀",
            style=discord.ButtonStyle.primary,
            custom_id="dank:profilecard:v1:preview",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        await _defer_private(interaction)
        try:
            config = await get_guild_config(view.guild_id)
            allowed = set(parse_live_card_config(config).allowed_fields)
            rendered = await render_live_profile_card(
                member,
                allowed,
                trigger_message_id=0,
                require_live_enabled=False,
            )
        except ProfileStorageUnavailable:
            return await _safe_ephemeral(interaction, "Private profile storage is unavailable.", ok=False)
        if rendered is None:
            return await _safe_ephemeral(
                interaction,
                "Your current privacy settings hide every optional card field. Nothing public would be posted.",
                ok=True,
            )
        await _send_private(
            interaction,
            embed=rendered.embed,
            view=rendered.view,
        )


async def profile_settings(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    await _defer_private(interaction)
    try:
        user_row, guild_row, effective = await _settings_payload(guild.id, member.id)
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(
            interaction,
            "Private profile storage is not ready. No privacy setting was guessed or changed.",
            ok=False,
        )
    await _send_private(
        interaction,
        embed=_settings_embed(member, user_row, guild_row, effective),
        view=ProfileSettingsView(
            author_id=member.id,
            guild_id=guild.id,
            user_preferences=dict(user_row.get("preferences") or {}),
            guild_settings=dict(guild_row.get("settings") or {}),
        ),
    )


@app_commands.choices(platform=_PLATFORM_CHOICES)
@app_commands.describe(
    platform="Platform identity to save.",
    username="Username or handle displayed on the card.",
    profile_url="Optional official public profile URL. Username-only platforms must leave this blank.",
    shared="Whether this identity may appear on your public/live profile card.",
)
async def profile_platform(
    interaction: discord.Interaction,
    platform: app_commands.Choice[str],
    username: str,
    shared: bool = False,
    profile_url: str = "",
) -> None:
    if interaction.guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    await _defer_private(interaction)
    try:
        entry = await save_platform_identity(
            interaction.user.id,
            platform.value,
            username=username,
            profile_url=profile_url,
            shared=shared,
        )
    except InvalidPlatformProfile as exc:
        return await _safe_ephemeral(interaction, str(exc), ok=False)
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing was saved.", ok=False)
    await invalidate_member_live_cards(
        interaction.client,
        interaction.guild,
        interaction.user.id,
        all_guilds=True,
    )
    spec = PLATFORM_SPECS[entry["platform"]]
    visibility = "shared on cards" if entry["shared"] else "saved privately"
    link_state = " with an official link" if entry["url"] else " by username only"
    await _safe_ephemeral(interaction, f"{spec.label} `{entry['username']}` is {visibility}{link_state}.")


@app_commands.choices(platform=_PLATFORM_CHOICES)
@app_commands.describe(platform="Saved platform identity to remove.")
async def profile_platform_remove(
    interaction: discord.Interaction,
    platform: app_commands.Choice[str],
) -> None:
    if interaction.guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    await _defer_private(interaction)
    try:
        removed = await remove_platform_identity(interaction.user.id, platform.value)
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing was removed.", ok=False)
    await invalidate_member_live_cards(
        interaction.client,
        interaction.guild,
        interaction.user.id,
        all_guilds=True,
    )
    spec = PLATFORM_SPECS[platform.value]
    await _safe_ephemeral(
        interaction,
        f"Removed your saved {spec.label} identity." if removed else f"No {spec.label} identity was saved.",
        ok=True,
    )


class _PublicFullRolesButton(discord.ui.Button):
    def __init__(self, member_id: int) -> None:
        super().__init__(
            label="View Full Profile Tags",
            emoji="📋",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank:profilecard:v1:full_roles:{int(member_id)}",
            row=4,
        )
        self.member_id = int(member_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "This only works inside the server.", ok=False)
        member = guild.get_member(self.member_id)
        if not isinstance(member, discord.Member):
            return await _safe_ephemeral(interaction, "That member is no longer available.", ok=False)
        await _defer_private(interaction)
        try:
            config = parse_live_card_config(await get_guild_config(guild.id))
            effective = await get_effective_profile_settings(guild.id, member.id)
        except ProfileStorageUnavailable:
            return await _safe_ephemeral(interaction, "Private profile storage is unavailable.", ok=False)
        preferences = dict(effective.get("preferences") or {})
        if not bool(preferences.get("show_profile_tags", True)) or "profile_tags" not in config.allowed_fields:
            return await _safe_ephemeral(interaction, "This member has hidden their profile tags.", ok=False)
        from .public_self_roles_group import _profile_full_roles_embed

        await _send_private(
            interaction,
            embed=_profile_full_roles_embed(member),
        )


class _PublicProfileSettingsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Privacy & Platforms",
            emoji="🔐",
            style=discord.ButtonStyle.primary,
            custom_id="dank:profilecard:v1:open_settings",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await profile_settings(interaction)


class PublicProfileView(discord.ui.View):
    def __init__(
        self,
        *,
        member_id: int,
        source_view: Optional[discord.ui.View],
        show_roles: bool,
        show_settings: bool,
    ) -> None:
        super().__init__(timeout=300)
        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button):
                continue
            if child.url:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Profile")[:80],
                        emoji=child.emoji,
                        style=discord.ButtonStyle.link,
                        url=str(child.url),
                        row=child.row,
                    )
                )
            elif child.custom_id:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Username")[:80],
                        emoji=child.emoji,
                        style=child.style,
                        custom_id=str(child.custom_id),
                        row=child.row,
                    )
                )
        if show_roles:
            self.add_item(_PublicFullRolesButton(member_id))
        if show_settings:
            self.add_item(_PublicProfileSettingsButton())


async def send_privacy_aware_profile(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    guild = interaction.guild
    if guild is None or int(member.guild.id) != int(guild.id):
        return await _safe_ephemeral(interaction, "That member is not available in this server.", ok=False)
    await _defer_private(interaction)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id))
        effective = await get_effective_profile_settings(guild.id, member.id)
        rendered = await render_live_profile_card(
            member,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(
            interaction,
            "Private profile storage is unavailable. Dank Shield will not guess this member's privacy settings.",
            ok=False,
        )

    preferences = dict(effective.get("preferences") or {})
    show_roles = bool(preferences.get("show_profile_tags", True)) and "profile_tags" in config.allowed_fields
    if rendered is None:
        from .public_self_roles_group import _profile_card

        rendered_embed = _copy_base_profile_embed(
            _profile_card(member),
            show_roles=False,
            show_dates=False,
        )
        rendered_embed.description = "This member has hidden their optional profile details."
        rendered_view = None
    else:
        rendered_embed = rendered.embed
        rendered_view = rendered.view
        rendered_embed.description = "Member profile • only fields this member chose to share"
    rendered_embed.set_footer(text="Dank Shield member profile")
    view = PublicProfileView(
        member_id=member.id,
        source_view=rendered_view,
        show_roles=show_roles,
        show_settings=int(interaction.user.id) == int(member.id),
    )
    await _send_private(
        interaction,
        embed=rendered_embed,
        view=view if view.children else None,
        file=rendered.file if rendered is not None else None,
    )


def _live_status_embed(guild: discord.Guild, config: Mapping[str, Any]) -> discord.Embed:
    live = parse_live_card_config(config)
    channel_lines: list[str] = []
    for channel_id in sorted(live.channel_ids):
        channel = guild.get_channel(channel_id)
        channel_lines.append(channel.mention if isinstance(channel, discord.TextChannel) else f"Missing channel `{channel_id}`")
    embed = discord.Embed(
        title="🪪 Live Profile Cards",
        description=(
            "One bot-owned card follows the latest eligible human speaker in each enabled channel. "
            f"Bursts are debounced, replacements are limited to about {int(live.replacement_cooldown_seconds)} seconds, "
            "and the same speaker is cooldown-suppressed."
        ),
        color=discord.Color.green() if live.enabled else discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Status", value="Enabled" if live.enabled else "Disabled", inline=True)
    embed.add_field(
        name="Allowed fields",
        value=(
            ", ".join(sorted(live.allowed_fields))
            if live.allowed_fields
            else "None • basic avatar/name signatures still post"
        ),
        inline=True,
    )
    embed.add_field(
        name="Channels",
        value="\n".join(channel_lines)[:1024] if channel_lines else "No channels selected.",
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value="Member privacy always wins. Platform identities remain hidden until the member explicitly shares them.",
        inline=False,
    )
    embed.set_footer(text="/dank setup → All Features & Settings → Member Profiles & Live Cards")
    return embed


@app_commands.describe(
    channel="Text channel to enable or disable.",
    enabled="Enable live profile cards in this channel, or disable and clean up its card.",
)
async def profile_live_cards(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    enabled: bool,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None or int(channel.guild.id) != int(guild.id):
        return await _safe_ephemeral(interaction, "Choose a text channel from this server.", ok=False)

    me = guild.me
    if enabled:
        if not isinstance(me, discord.Member):
            return await _safe_ephemeral(interaction, "Dank Shield could not resolve its server member.", ok=False)
        permissions = channel.permissions_for(me)
        missing: list[str] = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages:
            missing.append("Send Messages")
        if not permissions.embed_links:
            missing.append("Embed Links")
        if not permissions.read_message_history:
            missing.append("Read Message History")
        if not permissions.attach_files:
            missing.append("Attach Files")
        if missing:
            return await _safe_ephemeral(
                interaction,
                f"Fix Dank Shield permissions in {channel.mention}: " + ", ".join(missing),
                ok=False,
            )

    await _defer_private(interaction)
    config = await get_guild_config(guild.id, refresh=True)
    channel_ids = set(_channel_ids(config.get(LIVE_CHANNEL_IDS_KEY)))
    if enabled:
        channel_ids.add(int(channel.id))
    else:
        channel_ids.discard(int(channel.id))

    updated = await upsert_guild_config(
        guild.id,
        {
            LIVE_ENABLED_KEY: bool(channel_ids),
            LIVE_CHANNEL_IDS_KEY: [str(value) for value in sorted(channel_ids)],
        },
    )

    runtime = getattr(getattr(interaction, "client", None), _RUNTIME_ATTRIBUTE, None)
    if not enabled and isinstance(runtime, LiveProfileCardRuntime):
        await runtime.disable_channel(guild, channel)

    await _send_private(interaction, embed=_live_status_embed(guild, updated))


@app_commands.describe(
    server_roles="Allow safe server roles when the member also allows them.",
    profile_tags="Allow pronouns, identity, interests, and cosmetic profile tags.",
    account_dates="Allow joined/account-created dates when the member also allows them.",
    platforms="Allow explicitly shared platform identities.",
)
async def profile_live_fields(
    interaction: discord.Interaction,
    server_roles: bool,
    profile_tags: bool,
    account_dates: bool,
    platforms: bool,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    await _defer_private(interaction)
    allowed: list[str] = []
    if server_roles:
        allowed.append("server_roles")
    if profile_tags:
        allowed.append("profile_tags")
    if account_dates:
        allowed.append("account_dates")
    if platforms:
        allowed.append("platforms")
    updated = await upsert_guild_config(guild.id, {LIVE_ALLOWED_FIELDS_KEY: allowed})
    runtime = _profile_runtime(interaction.client)
    if runtime is not None:
        await runtime.invalidate_guild_cards(guild)
    await _send_private(interaction, embed=_live_status_embed(guild, updated))


async def profile_live_status(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    await _defer_private(interaction)
    config = await get_guild_config(guild.id, refresh=True)
    await _send_private(interaction, embed=_live_status_embed(guild, config))


def _attach_profile_commands() -> None:
    command_specs = (
        ("settings", "Open your private profile privacy and platform settings.", profile_settings),
        ("platform", "Save or update one private/shared platform identity.", profile_platform),
        ("platform-remove", "Remove one saved platform identity.", profile_platform_remove),
        ("live-cards", "Manager fallback: toggle one channel; the full picker is in /dank setup.", profile_live_cards),
        ("live-fields", "Manager: restrict fields live profile cards may display.", profile_live_fields),
        ("live-status", "Manager: view live profile card configuration.", profile_live_status),
    )
    for name, description, callback in command_specs:
        if profile_group.get_command(name) is not None:
            continue
        profile_group.add_command(app_commands.Command(name=name, description=description, callback=callback))


def register_public_profile_cards(bot: Any, tree: Any) -> None:
    del tree
    global _REGISTERED
    _attach_profile_commands()
    if bot is None:
        return
    runtime = getattr(bot, _RUNTIME_ATTRIBUTE, None)
    if not isinstance(runtime, LiveProfileCardRuntime):
        runtime = LiveProfileCardRuntime(bot)
        setattr(bot, _RUNTIME_ATTRIBUTE, runtime)
        bot.add_listener(runtime.on_message, "on_message")
        bot.add_listener(runtime.on_ready, "on_ready")
        bot.add_listener(runtime.on_member_remove, "on_member_remove")
        bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")
    if not _REGISTERED:
        _REGISTERED = True
        print("✅ public_profile_cards: attached privacy, platform, and non-repetitive live-card controls")


__all__ = [
    "ProfileSettingsView",
    "profile_live_cards",
    "profile_live_fields",
    "profile_live_status",
    "profile_platform",
    "profile_platform_remove",
    "profile_settings",
    "invalidate_member_live_cards",
    "send_privacy_aware_profile",
    "register_public_profile_cards",
]
