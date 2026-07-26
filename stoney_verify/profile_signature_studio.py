from __future__ import annotations

"""Button-first customization for member profile signatures.

The member studio owns personal appearance, privacy, platforms, previews, and
reset actions. The server studio owns channel-independent defaults only. Neither
surface reads or changes welcome/join settings except the explicit one-time
"Import Join Card Look" action.
"""

from io import BytesIO
from typing import Any, Mapping, Optional

import discord

from .guild_config import get_guild_config, upsert_guild_config
from .profile_card_runtime import LiveProfileCardRuntime, parse_live_card_config, render_live_profile_card
from .profile_card_service import (
    PLATFORM_SPECS,
    InvalidPlatformProfile,
    ProfileStorageUnavailable,
    display_profile_username,
    get_profile_user,
    remove_platform_identity,
    save_platform_identity,
    upsert_profile_user_preferences,
)
from .profile_signature_style import (
    DEFAULT_MEMBER_PROFILE_STYLE,
    DEFAULT_SERVER_PROFILE_STYLE,
    PROFILE_CUSTOM_BACKGROUND_KEY,
    PROFILE_CUSTOM_FONT_KEY,
    PROFILE_CUSTOM_FONT_NAME_KEY,
    SERVER_STYLE_CONFIG_KEYS,
    effective_profile_style,
    encode_profile_asset,
    normalize_member_profile_style,
    server_profile_style,
    theme_style_updates,
)
from .ui.picker import DankPickerView, make_choice
from .welcome_card_service import (
    configured_color_mode,
    configured_custom_colors,
    configured_custom_font,
    configured_font_style_key,
    configured_theme_key,
    decode_custom_background,
)
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_hex_color,
)

_RUNTIME_ATTRIBUTE = "_dank_live_profile_card_runtime"

_THEME_EMOJIS = {
    "default": "🛡️",
    "ocean": "🌊",
    "forest": "🌲",
    "sunset": "🌇",
    "purple": "💜",
    "galaxy": "🌌",
    "minimal": "⬜",
    "dark": "🌑",
}
_FONT_EMOJIS = {
    "neon": "✨",
    "tech": "🖥️",
    "bold": "💥",
    "clean": "⬜",
    "chrome": "💎",
    "outline": "🌀",
    "arcade": "🕹️",
    "street": "⚡",
    "future": "🚀",
    "soft": "🌙",
    "stencil": "🎖️",
    "varsity": "🏆",
    "blackletter": "🌑",
    "prism": "🌈",
    "terminal": "⌨️",
    "retro": "📼",
}


def _member(interaction: discord.Interaction) -> Optional[discord.Member]:
    return interaction.user if isinstance(interaction.user, discord.Member) else None


async def _private(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    file: Optional[discord.File] = None,
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
    if file is not None:
        payload["file"] = file
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _invalidate(interaction: discord.Interaction, *, all_guilds: bool = False) -> None:
    runtime = getattr(interaction.client, _RUNTIME_ATTRIBUTE, None)
    member = _member(interaction)
    guild = interaction.guild
    if not isinstance(runtime, LiveProfileCardRuntime) or member is None or guild is None:
        return
    if all_guilds:
        await runtime.remove_user_cards_all_guilds(member.id)
    else:
        await runtime.remove_user_cards(guild, member.id)


async def _invalidate_guild(interaction: discord.Interaction) -> None:
    runtime = getattr(interaction.client, _RUNTIME_ATTRIBUTE, None)
    if isinstance(runtime, LiveProfileCardRuntime) and interaction.guild is not None:
        await runtime.invalidate_guild_cards(interaction.guild)


def _style_labels(preferences: Mapping[str, Any], config: Any) -> dict[str, str]:
    effective = effective_profile_style(preferences, config)
    theme = BUILTIN_THEMES.get(str(effective.get("theme")))
    font = FONT_STYLES.get(str(effective.get("font")))
    return {
        "theme": getattr(theme, "label", str(effective.get("theme") or "Default")),
        "font": (
            str(effective.get("custom_font_name") or "Server custom font")
            if str(effective.get("font")) == CUSTOM_FONT_STYLE_KEY
            else getattr(font, "label", str(effective.get("font") or "Clean"))
        ),
        "colors": str(effective.get("color_mode") or "profile").replace("_", " ").title(),
        "background": str(effective.get("background_mode") or "theme").replace("_", " ").title(),
        "layout": str(effective.get("layout") or "classic").replace("_", " ").title(),
        "frame": str(effective.get("avatar_frame") or "glow").replace("_", " ").title(),
    }


async def _studio_embed(member: discord.Member) -> discord.Embed:
    user = await get_profile_user(member.id)
    config = await get_guild_config(member.guild.id)
    preferences = dict(user.get("preferences") or {})
    labels = _style_labels(preferences, config)
    platforms = dict(user.get("platforms") or {})
    shared = sum(1 for value in platforms.values() if isinstance(value, Mapping) and value.get("shared"))
    embed = discord.Embed(
        title="🪪 My Profile Signature",
        description=(
            "Make your compact signature feel like yours. Everything is button-first, previewable, and optional. "
            "Your privacy choices always win over server defaults."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Current look",
        value=(
            f"**Theme:** {labels['theme']}\n"
            f"**Font:** {labels['font']}\n"
            f"**Colors:** {labels['colors']}\n"
            f"**Background:** {labels['background']}\n"
            f"**Layout:** {labels['layout']}\n"
            f"**Avatar frame:** {labels['frame']}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Sharing",
        value=(
            f"**Live signature:** {'On' if preferences.get('live_cards_enabled', True) else 'Off'}\n"
            f"**Roles:** {'Shown' if preferences.get('show_roles', True) else 'Hidden'}\n"
            f"**Dates:** {'Shown' if preferences.get('show_account_dates', True) else 'Hidden'}\n"
            f"**Platforms:** {shared} shared"
        ),
        inline=True,
    )
    embed.add_field(
        name="Easy rule",
        value=(
            "**Appearance** changes how your signature looks. **Privacy** changes what it may show. "
            "**Platforms** manages gaming and social identities. These settings never change the server's welcome cards."
        ),
        inline=False,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Member Profile Signature • separate from Welcome & Join")
    return embed


async def _preview(interaction: discord.Interaction, *, member: Optional[discord.Member] = None) -> None:
    target = member or _member(interaction)
    guild = interaction.guild
    if target is None or guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    await _defer(interaction)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id, refresh=True))
        rendered = await render_live_profile_card(
            target,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    if rendered is None:
        return await _private(
            interaction,
            content="Your privacy settings hide every optional detail. Turn on at least one detail to preview a signature.",
        )
    rendered.embed.set_footer(text="Preview only • compact profile signature")
    await _private(interaction, embed=rendered.embed, view=rendered.view, file=rendered.file)


async def _save_member_style(
    interaction: discord.Interaction,
    updates: Mapping[str, Any],
    *,
    message: str,
) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    await _defer(interaction)
    try:
        await upsert_profile_user_preferences(member.id, dict(updates))
        await _invalidate(interaction, all_guilds=True)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
    await _private(interaction, content=f"✅ {message}")
    await _preview(interaction, member=member)


async def _save_server_style(
    interaction: discord.Interaction,
    updates: Mapping[str, Any],
    *,
    message: str,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, content="❌ Use this inside a server.")
    from .commands_ext.public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    await _defer(interaction)
    await upsert_guild_config(guild.id, dict(updates))
    await _invalidate_guild(interaction)
    await _private(interaction, content=f"✅ {message}")
    member = _member(interaction)
    if member is not None:
        await _preview(interaction, member=member)


async def _theme_picker(interaction: discord.Interaction, *, server: bool) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    user = await get_profile_user(member.id)
    current = normalize_member_profile_style(user.get("preferences")).get("signature_theme", "server")
    if server:
        current = server_profile_style(await get_guild_config(member.guild.id)).get("theme", "default")

    async def picked(component: discord.Interaction, value: str) -> None:
        if server:
            await _save_server_style(
                component,
                theme_style_updates(value, member=False),
                message=f"Server profile-signature theme set to **{BUILTIN_THEMES[value].label}** with its colors and background.",
            )
        else:
            label = "Server Default" if value == "server" else BUILTIN_THEMES[value].label
            await _save_member_style(
                component,
                theme_style_updates(value, member=True),
                message=f"Your signature theme is now **{label}** with its colors and background.",
            )

    choices = []
    if not server:
        choices.append(make_choice("Server Default", "server", description="Restore the server's complete signature look.", emoji="🏠", default=current == "server"))
    choices.extend(
        make_choice(
            theme.label,
            theme.key,
            description="Apply this theme's colors, background, and artwork",
            emoji=_THEME_EMOJIS.get(theme.key, "🎨"),
            default=current == theme.key,
        )
        for theme in BUILTIN_THEMES.values()
    )
    await _private(
        interaction,
        content="## 🖼️ Signature Themes\nPick a complete look. Its colors, background, and artwork apply immediately; you can override individual parts afterward.",
        view=DankPickerView(
            author_id=member.id,
            choices=choices,
            on_pick=picked,
            custom_id=f"dank:profile:theme:{'server' if server else 'member'}:{member.id}",
            placeholder="Choose a signature theme…",
            title="Profile Signature Themes",
        ),
    )


async def _font_picker(interaction: discord.Interaction, *, server: bool) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    user = await get_profile_user(member.id)
    current = normalize_member_profile_style(user.get("preferences")).get("signature_font", "server")
    config = await get_guild_config(member.guild.id)
    if server:
        current = server_profile_style(config).get("font", "clean")

    async def picked(component: discord.Interaction, value: str) -> None:
        if server:
            await _save_server_style(
                component,
                {SERVER_STYLE_CONFIG_KEYS["font"]: value},
                message=f"Server profile-signature font set to **{FONT_STYLES[value].label}**.",
            )
        else:
            label = "Server Default" if value == "server" else FONT_STYLES[value].label
            await _save_member_style(
                component,
                {"signature_font": value},
                message=f"Your signature font is now **{label}**.",
            )

    choices = []
    if not server:
        choices.append(make_choice("Server Default", "server", description="Follow this server's profile-signature font.", emoji="🏠", default=current == "server"))
    choices.extend(
        make_choice(
            style.label,
            style.key,
            description=style.description,
            emoji=_FONT_EMOJIS.get(style.key, "🔤"),
            default=current == style.key,
        )
        for style in FONT_STYLES.values()
    )
    await _private(
        interaction,
        content="## 🔤 Signature Font Gallery\nChoose a readable built-in font. Server custom fonts remain available through **Server Default**.",
        view=DankPickerView(
            author_id=member.id,
            choices=choices,
            on_pick=picked,
            custom_id=f"dank:profile:font:{'server' if server else 'member'}:{member.id}",
            placeholder="Choose a signature font…",
            title="Profile Signature Fonts",
        ),
    )


async def _color_picker(interaction: discord.Interaction, *, server: bool) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")

    async def picked(component: discord.Interaction, value: str) -> None:
        if value in {"server", "auto", "profile", "theme"}:
            updates = {
                (SERVER_STYLE_CONFIG_KEYS["color_mode"] if server else "signature_color_mode"): value,
            }
            saver = _save_server_style if server else _save_member_style
            await saver(component, updates, message=f"Signature colors set to **{value.replace('_', ' ').title()}**.")
            return
        preset = COLOR_PRESETS.get(value)
        if preset is None:
            return await _private(component, content="❌ That palette is no longer available.")
        if server:
            updates = {
                SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
                SERVER_STYLE_CONFIG_KEYS["custom_primary"]: preset.primary,
                SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: preset.secondary,
            }
            await _save_server_style(component, updates, message=f"Server signature palette set to **{preset.label}**.")
        else:
            await _save_member_style(
                component,
                {
                    "signature_color_mode": "custom",
                    "signature_custom_primary": preset.primary,
                    "signature_custom_secondary": preset.secondary,
                },
                message=f"Your signature palette is now **{preset.label}**.",
            )

    choices = []
    if not server:
        choices.append(make_choice("Server Default", "server", description="Follow the server's signature color choice.", emoji="🏠"))
    choices.extend(
        [
            make_choice("Smart Auto", "auto", description="Use the safest available accent source.", emoji="✨"),
            make_choice("Match My Avatar", "profile", description="Pull colors from your profile picture.", emoji="👤"),
            make_choice("Selected Theme", "theme", description="Use the chosen signature theme colors.", emoji="🛡️"),
        ]
    )
    choices.extend(
        make_choice(preset.label, preset.key, description=preset.description, emoji=preset.emoji)
        for preset in COLOR_PRESETS.values()
    )
    await _private(
        interaction,
        content="## 🎨 Signature Colors\nNo color codes are required. Pick automatic colors or a ready-made palette.",
        view=DankPickerView(
            author_id=member.id,
            choices=choices,
            on_pick=picked,
            custom_id=f"dank:profile:colors:{'server' if server else 'member'}:{member.id}",
            placeholder="Choose signature colors…",
            title="Profile Signature Colors",
        ),
    )


class CustomProfileColorsModal(discord.ui.Modal):
    def __init__(self, *, server: bool, author_id: int) -> None:
        super().__init__(title="Advanced Signature Colors", timeout=900)
        self.server = bool(server)
        self.author_id = int(author_id)
        self.primary = discord.ui.TextInput(label="Primary color", placeholder="#22DCFF", max_length=7)
        self.secondary = discord.ui.TextInput(label="Secondary color", placeholder="#BC42FF", max_length=7)
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")
        try:
            primary = normalize_hex_color(str(self.primary.value))
            secondary = normalize_hex_color(str(self.secondary.value))
        except ValueError as exc:
            return await _private(interaction, content=f"❌ {exc}")
        if self.server:
            await _save_server_style(
                interaction,
                {
                    SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
                    SERVER_STYLE_CONFIG_KEYS["custom_primary"]: primary,
                    SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: secondary,
                },
                message="Advanced server signature colors saved.",
            )
        else:
            await _save_member_style(
                interaction,
                {
                    "signature_color_mode": "custom",
                    "signature_custom_primary": primary,
                    "signature_custom_secondary": secondary,
                },
                message="Your advanced signature colors were saved.",
            )


async def _simple_picker(
    interaction: discord.Interaction,
    *,
    server: bool,
    kind: str,
) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    definitions: dict[str, tuple[str, list[tuple[str, str, str, str]]]] = {
        "background": (
            "Signature Background",
            [
                ("Server Default", "server", "Follow the server's background choice.", "🏠"),
                ("Theme Artwork", "theme", "Use a clean theme gradient.", "🖼️"),
                ("Match My Avatar", "profile", "Use a blurred version of your avatar.", "👤"),
                ("Server Custom Artwork", "custom", "Use the server's uploaded profile artwork when available.", "📎"),
            ],
        ),
        "layout": (
            "Signature Layout",
            [
                ("Server Default", "server", "Follow the server's layout.", "🏠"),
                ("Classic", "classic", "Avatar left, name and details right.", "🪪"),
                ("Minimal", "minimal", "Less decoration and fewer visual elements.", "⬜"),
                ("Spotlight", "spotlight", "Larger avatar with a bold member focus.", "✨"),
            ],
        ),
        "frame": (
            "Avatar Frame",
            [
                ("Server Default", "server", "Follow the server's avatar frame.", "🏠"),
                ("Glow", "glow", "Soft colored glow around the avatar.", "✨"),
                ("Clean Ring", "ring", "Simple high-contrast ring.", "⭕"),
                ("No Frame", "none", "Show the avatar without decoration.", "🚫"),
            ],
        ),
    }
    title, options = definitions[kind]
    if server:
        options = [option for option in options if option[1] != "server"]
    key = {
        "background": "background_mode",
        "layout": "layout",
        "frame": "avatar_frame",
    }[kind]

    async def picked(component: discord.Interaction, value: str) -> None:
        label = next((label for label, choice, _desc, _emoji in options if choice == value), value)
        if server:
            await _save_server_style(
                component,
                {SERVER_STYLE_CONFIG_KEYS[key]: value},
                message=f"Server {title.lower()} set to **{label}**.",
            )
        else:
            member_key = {
                "background": "signature_background_mode",
                "layout": "signature_layout",
                "frame": "signature_avatar_frame",
            }[kind]
            await _save_member_style(
                component,
                {member_key: value},
                message=f"Your {title.lower()} is now **{label}**.",
            )

    await _private(
        interaction,
        content=f"## {title}\nChoose the option that is easiest to recognize and read.",
        view=DankPickerView(
            author_id=member.id,
            choices=[
                make_choice(label, value, description=description, emoji=emoji)
                for label, value, description, emoji in options
            ],
            on_pick=picked,
            custom_id=f"dank:profile:{kind}:{'server' if server else 'member'}:{member.id}",
            placeholder=f"Choose {title.lower()}…",
            title=title,
        ),
    )


class ProfileAppearanceView(discord.ui.View):
    def __init__(self, *, author_id: int, server: bool = False) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.server = bool(server)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the person who opened this studio can use it.")
            return False
        return True

    @discord.ui.button(label="Theme", emoji="🖼️", style=discord.ButtonStyle.primary, row=0)
    async def theme(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _theme_picker(interaction, server=self.server)

    @discord.ui.button(label="Font", emoji="🔤", style=discord.ButtonStyle.primary, row=0)
    async def font(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _font_picker(interaction, server=self.server)

    @discord.ui.button(label="Colors", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def colors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _color_picker(interaction, server=self.server)

    @discord.ui.button(label="Custom Colors", emoji="🖌️", style=discord.ButtonStyle.secondary, row=0)
    async def custom_colors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            CustomProfileColorsModal(server=self.server, author_id=self.author_id)
        )

    @discord.ui.button(label="Background", emoji="🌄", style=discord.ButtonStyle.secondary, row=1)
    async def background(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _simple_picker(interaction, server=self.server, kind="background")

    @discord.ui.button(label="Layout", emoji="📐", style=discord.ButtonStyle.secondary, row=1)
    async def layout(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _simple_picker(interaction, server=self.server, kind="layout")

    @discord.ui.button(label="Avatar Frame", emoji="⭕", style=discord.ButtonStyle.secondary, row=1)
    async def frame(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _simple_picker(interaction, server=self.server, kind="frame")

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.success, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _preview(interaction)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.server:
            await open_server_signature_defaults(interaction)
        else:
            await open_profile_signature_studio(interaction)


class PlatformEditModal(discord.ui.Modal):
    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any]) -> None:
        spec = PLATFORM_SPECS[platform]
        super().__init__(title=f"{spec.label} Profile", timeout=900)
        self.author_id = int(author_id)
        self.platform = platform
        self.username = discord.ui.TextInput(
            label="Username or handle",
            default=str(entry.get("username") or "")[:80],
            max_length=80,
            required=True,
        )
        self.url = discord.ui.TextInput(
            label="Official profile link (optional)",
            default=str(entry.get("url") or "")[:500],
            max_length=500,
            required=False,
            placeholder="Leave blank for username-only platforms",
        )
        self.add_item(self.username)
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")
        user = await get_profile_user(self.author_id, refresh=True)
        current = dict(user.get("platforms") or {}).get(self.platform)
        shared = bool(current.get("shared")) if isinstance(current, Mapping) else False
        try:
            entry = await save_platform_identity(
                self.author_id,
                self.platform,
                username=str(self.username.value),
                profile_url=str(self.url.value),
                shared=shared,
            )
        except InvalidPlatformProfile as exc:
            return await _private(interaction, content=f"❌ {exc}")
        except ProfileStorageUnavailable:
            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _invalidate(interaction, all_guilds=True)
        spec = PLATFORM_SPECS[self.platform]
        await _private(
            interaction,
            content=(
                f"✅ {spec.label} saved as `{display_profile_username(entry['username'])}`. "
                f"It is currently **{'shared' if entry['shared'] else 'private'}**."
            ),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform),
        )


class PlatformDetailView(discord.ui.View):
    def __init__(self, *, author_id: int, platform: str) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.platform = str(platform)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the person who opened this editor can use it.")
            return False
        return True

    @discord.ui.button(label="Add / Edit", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        user = await get_profile_user(self.author_id, refresh=True)
        raw = dict(user.get("platforms") or {}).get(self.platform)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        await interaction.response.send_modal(
            PlatformEditModal(author_id=self.author_id, platform=self.platform, entry=entry)
        )

    @discord.ui.button(label="Share / Hide", emoji="👁️", style=discord.ButtonStyle.success, row=0)
    async def share(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            user = await get_profile_user(self.author_id, refresh=True)
            raw = dict(user.get("platforms") or {}).get(self.platform)
            if not isinstance(raw, Mapping) or not str(raw.get("username") or "").strip():
                return await _private(interaction, content="Add the username first, then choose whether to share it.")
            entry = await save_platform_identity(
                self.author_id,
                self.platform,
                username=raw.get("username"),
                profile_url=raw.get("url"),
                shared=not bool(raw.get("shared")),
            )
        except (InvalidPlatformProfile, ProfileStorageUnavailable) as exc:
            return await _private(interaction, content=f"❌ {exc}")
        await _invalidate(interaction, all_guilds=True)
        await _private(
            interaction,
            content=f"✅ {PLATFORM_SPECS[self.platform].label} is now **{'shared' if entry['shared'] else 'private'}**.",
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform),
        )

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            removed = await remove_platform_identity(self.author_id, self.platform)
        except ProfileStorageUnavailable:
            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _invalidate(interaction, all_guilds=True)
        await _private(
            interaction,
            content=(
                f"✅ Removed {PLATFORM_SPECS[self.platform].label}."
                if removed
                else f"No {PLATFORM_SPECS[self.platform].label} profile was saved."
            ),
            view=PlatformManagerView(author_id=self.author_id),
        )

    @discord.ui.button(label="Back to Platforms", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_platform_manager(interaction)


class PlatformSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose a gaming or social platform…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=spec.label, value=key, emoji=spec.emoji)
                for key, spec in PLATFORM_SPECS.items()
            ],
            custom_id="dank:profile:platform_picker:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PlatformManagerView) or not await view.interaction_check(interaction):
            return
        platform = str(self.values[0])
        user = await get_profile_user(view.author_id, refresh=True)
        raw = dict(user.get("platforms") or {}).get(platform)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        spec = PLATFORM_SPECS[platform]
        embed = discord.Embed(
            title=f"{spec.emoji} {spec.label}",
            description=(
                "Add or edit the username, then use **Share / Hide** to control whether it appears on your signature. "
                "Links are accepted only for supported official profile pages."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Username", value=f"`{display_profile_username(entry['username'])}`" if entry.get("username") else "Not saved", inline=False)
        embed.add_field(name="Visibility", value="Shared" if entry.get("shared") else "Private", inline=True)
        embed.add_field(name="Official link", value="Saved" if entry.get("url") else "None", inline=True)
        await _private(
            interaction,
            embed=embed,
            view=PlatformDetailView(author_id=view.author_id, platform=platform),
        )


class PlatformManagerView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.add_item(PlatformSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the person who opened this manager can use it.")
            return False
        return True

    @discord.ui.button(label="Back to Signature", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_profile_signature_studio(interaction)


async def open_platform_manager(interaction: discord.Interaction) -> None:
    member = _member(interaction)
    if member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    try:
        user = await get_profile_user(member.id)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    platforms = dict(user.get("platforms") or {})
    lines = []
    for key, spec in PLATFORM_SPECS.items():
        raw = platforms.get(key)
        if not isinstance(raw, Mapping) or not raw.get("username"):
            continue
        lines.append(
            f"{spec.emoji} **{spec.label}:** `{display_profile_username(raw.get('username'))}` — "
            f"{'shared' if raw.get('shared') else 'private'}"
        )
    embed = discord.Embed(
        title="🎮 Platforms & Accounts",
        description=(
            "Choose a platform below. Saving an account does **not** share it automatically; "
            "you control visibility with **Share / Hide**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Saved accounts", value="\n".join(lines)[:1024] if lines else "None saved yet.", inline=False)
    await _private(interaction, embed=embed, view=PlatformManagerView(author_id=member.id))


class SignatureStudioView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the member who opened these settings can use them.")
            return False
        return True

    @discord.ui.button(label="Appearance", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(
            interaction,
            embed=discord.Embed(
                title="🎨 Signature Appearance",
                description="Choose a theme, font, colors, background, layout, and avatar frame. Every change can be previewed.",
                color=discord.Color.blurple(),
            ),
            view=ProfileAppearanceView(author_id=self.author_id),
        )

    @discord.ui.button(label="Privacy", emoji="🔐", style=discord.ButtonStyle.primary, row=0)
    async def privacy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_profile_cards import profile_settings

        await profile_settings(interaction)

    @discord.ui.button(label="Platforms", emoji="🎮", style=discord.ButtonStyle.primary, row=0)
    async def platforms(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_platform_manager(interaction)

    @discord.ui.button(label="Profile Roles", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = _member(interaction)
        if member is None:
            return await _private(interaction, content="❌ Use this inside a server as a member.")
        from .commands_ext.public_self_roles_group import ProfileEditView, _profile_edit_embed

        await _private(interaction, embed=_profile_edit_embed(member), view=ProfileEditView())

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.success, row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _preview(interaction)

    @discord.ui.button(label="Reset My Look", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _save_member_style(
            interaction,
            DEFAULT_MEMBER_PROFILE_STYLE,
            message="Your personal signature appearance now follows the server defaults.",
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Profile signature settings closed.", embed=None, view=None)


async def open_profile_signature_studio(interaction: discord.Interaction) -> None:
    member = _member(interaction)
    if member is None or interaction.guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    try:
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    await _private(interaction, embed=embed, view=SignatureStudioView(author_id=member.id))


async def _import_welcome_look(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, content="❌ Use this inside a server.")
    config = await get_guild_config(guild.id, refresh=True)
    primary, secondary = configured_custom_colors(config)
    custom_font, custom_font_name = configured_custom_font(config)
    custom_background = decode_custom_background(config)
    updates: dict[str, Any] = {
        SERVER_STYLE_CONFIG_KEYS["theme"]: configured_theme_key(config),
        SERVER_STYLE_CONFIG_KEYS["font"]: configured_font_style_key(config),
        SERVER_STYLE_CONFIG_KEYS["color_mode"]: configured_color_mode(config),
        SERVER_STYLE_CONFIG_KEYS["custom_primary"]: primary,
        SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: secondary,
        SERVER_STYLE_CONFIG_KEYS["background_mode"]: "custom" if custom_background else "theme",
        PROFILE_CUSTOM_BACKGROUND_KEY: encode_profile_asset(custom_background),
        PROFILE_CUSTOM_FONT_KEY: encode_profile_asset(custom_font or b""),
        PROFILE_CUSTOM_FONT_NAME_KEY: custom_font_name,
    }
    await _save_server_style(
        interaction,
        updates,
        message=(
            "Imported the current Join Card look as a **one-time copy**. Welcome and profile settings remain independent from now on."
        ),
    )


class ServerSignatureDefaultsView(ProfileAppearanceView):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(author_id=author_id, server=True)
        self.add_item(_ImportWelcomeLookButton())
        self.add_item(_ResetServerLookButton())


class _ImportWelcomeLookButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Import Join Card Look Once",
            emoji="📥",
            style=discord.ButtonStyle.secondary,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _import_welcome_look(interaction)


class _ResetServerLookButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Reset Server Defaults", emoji="🔄", style=discord.ButtonStyle.danger, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        updates = {
            SERVER_STYLE_CONFIG_KEYS[key]: value
            for key, value in DEFAULT_SERVER_PROFILE_STYLE.items()
        }
        updates.update(
            {
                PROFILE_CUSTOM_BACKGROUND_KEY: "",
                PROFILE_CUSTOM_FONT_KEY: "",
                PROFILE_CUSTOM_FONT_NAME_KEY: "",
            }
        )
        await _save_server_style(interaction, updates, message="Server profile-signature defaults reset.")


async def open_server_signature_defaults(interaction: discord.Interaction) -> None:
    from .commands_ext.public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    member = _member(interaction)
    if guild is None or member is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    config = await get_guild_config(guild.id, refresh=True)
    labels = _style_labels({}, config)
    embed = discord.Embed(
        title="🎨 Server Profile-Signature Defaults",
        description=(
            "These are the starting visual defaults for members who choose **Server Default**. "
            "They do not change welcome cards. Members may still personalize their own signature."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current defaults",
        value=(
            f"**Theme:** {labels['theme']}\n**Font:** {labels['font']}\n**Colors:** {labels['colors']}\n"
            f"**Background:** {labels['background']}\n**Layout:** {labels['layout']}\n**Avatar frame:** {labels['frame']}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Optional import",
        value=(
            "**Import Join Card Look Once** copies the current join-card artwork/font/colors into profile defaults. "
            "After the copy, both systems stay separate."
        ),
        inline=False,
    )
    await _private(interaction, embed=embed, view=ServerSignatureDefaultsView(author_id=member.id))


__all__ = [
    "ProfileAppearanceView",
    "SignatureStudioView",
    "open_platform_manager",
    "open_profile_signature_studio",
    "open_server_signature_defaults",
]
