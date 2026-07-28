from __future__ import annotations

"""Button-first customization for member profile signatures.

The member studio owns personal appearance, privacy, platforms, previews, and
reset actions. The server studio owns channel-independent defaults only. Neither
surface reads or changes welcome/join settings except the explicit one-time
"Import Join Card Look" action.
"""

import asyncio
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
    effective_preferences,
    get_profile_guild_settings,
    get_profile_user,
    platform_entry_mode,
    remove_platform_identity,
    save_platform_identity,
    upsert_profile_guild_settings,
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


async def _edit_private(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    file: Optional[discord.File] = None,
) -> None:
    payload: dict[str, Any] = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "attachments": [file] if file is not None else [],
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.edit_message(**payload)
    else:
        await interaction.edit_original_response(**payload)


async def _defer(interaction: discord.Interaction, *, component_update: bool = False) -> None:
    if interaction.response.is_done():
        return
    if component_update:
        await interaction.response.defer()
    else:
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
    user, guild_row, config = await asyncio.gather(
        get_profile_user(member.id),
        get_profile_guild_settings(member.guild.id, member.id),
        get_guild_config(member.guild.id),
    )
    preferences = dict(user.get("preferences") or {})
    effective_privacy = effective_preferences(preferences, guild_row.get("settings"))
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
            f"**Live signature:** {'On' if effective_privacy.get('live_cards_enabled', True) else 'Off'}\n"
            f"**Server roles:** {'Shown' if effective_privacy.get('show_server_roles', False) else 'Hidden'}\n"
            f"**Profile tags:** {'Shown' if effective_privacy.get('show_profile_tags', True) else 'Hidden'}\n"
            f"**Dates:** {'Shown' if effective_privacy.get('show_account_dates', True) else 'Hidden'}\n"
            f"**Platforms:** {shared} shared"
        ),
        inline=True,
    )
    embed.add_field(
        name="Easy rule",
        value=(
            "**Server Roles** only controls whether safe roles already assigned by this server appear. "
            "**Profile Tags** opens pronouns, identity, interests, and optional cosmetics. They are separate menus."
        ),
        inline=False,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Member Profile Signature • separate from Welcome & Join")
    return embed


class SignaturePreviewView(discord.ui.View):
    def __init__(self, *, author_id: int, source_view: Optional[discord.ui.View]) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
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
                    )
                )
            elif child.custom_id:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Username")[:80],
                        emoji=child.emoji,
                        style=child.style,
                        custom_id=str(child.custom_id),
                    )
                )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the member who opened this preview can use it.")
            return False
        return True

    @discord.ui.button(label="Back to Profile", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_profile_signature_studio(interaction, replace=True)


async def _preview(
    interaction: discord.Interaction,
    *,
    member: Optional[discord.Member] = None,
    notice: str = "",
) -> None:
    target = member or _member(interaction)
    guild = interaction.guild
    if target is None or guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    component_update = getattr(interaction, "type", None) == discord.InteractionType.component
    await _defer(interaction, component_update=component_update)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id, refresh=True))
        rendered = await render_live_profile_card(
            target,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _edit_private(interaction, content="❌ Private profile storage is unavailable.")
    if rendered is None:
        return await _edit_private(
            interaction,
            content="Your live signature is currently unavailable. Return to Profile Privacy and check Live Signature.",
            view=SignatureStudioView(author_id=target.id),
        )
    rendered.embed.set_footer(text="Preview only • compact profile signature • nothing posted publicly")
    await _edit_private(
        interaction,
        content=notice,
        embed=rendered.embed,
        view=SignaturePreviewView(author_id=target.id, source_view=rendered.view),
        file=rendered.file,
    )


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
        return await _edit_private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
    await _preview(interaction, member=member, notice=f"✅ {message}")


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
    member = _member(interaction)
    if member is not None:
        await _preview(interaction, member=member, notice=f"✅ {message}")
    else:
        await _edit_private(interaction, content=f"✅ {message}")


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
            await open_profile_signature_studio(interaction, replace=True)


class PlatformEditModal(discord.ui.Modal):
    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any]) -> None:
        spec = PLATFORM_SPECS[platform]
        super().__init__(title=f"{spec.label} Details", timeout=900)
        self.author_id = int(author_id)
        self.platform = platform
        self.username = discord.ui.TextInput(
            label="Username or handle (optional)",
            default=str(entry.get("username") or "")[:80],
            max_length=80,
            required=False,
            placeholder="Leave blank when you only want the platform logo",
        )
        self.url = discord.ui.TextInput(
            label="Official profile link (optional)",
            default=str(entry.get("url") or "")[:500],
            max_length=500,
            required=False,
            placeholder="Only supported official profile links are accepted",
        )
        self.add_item(self.username)
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")
        user = await get_profile_user(self.author_id, refresh=True)
        current = dict(user.get("platforms") or {}).get(self.platform)
        current = dict(current) if isinstance(current, Mapping) else {}
        shared = bool(current.get("shared"))
        username = str(self.username.value or "").strip()
        profile_url = str(self.url.value or "").strip()
        mode = platform_entry_mode(current) if current else ""
        if not username and not profile_url:
            mode = "logo"
        elif mode == "link" and not profile_url:
            mode = "username" if username else "logo"
        elif mode == "username" and not username:
            mode = "link" if profile_url else "logo"
        elif not mode or mode == "logo":
            mode = "link" if profile_url else "username" if username else "logo"
        try:
            entry = await save_platform_identity(
                self.author_id,
                self.platform,
                username=username,
                profile_url=profile_url,
                shared=shared,
                mode=mode,
            )
        except InvalidPlatformProfile as exc:
            return await _private(interaction, content=f"❌ {exc}")
        except ProfileStorageUnavailable:
            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _invalidate(interaction, all_guilds=True)
        spec = PLATFORM_SPECS[self.platform]
        await _edit_private(
            interaction,
            content=f"✅ {spec.label} saved. Choose how it should appear below.",
            embed=_platform_detail_embed(self.platform, entry),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),
        )


def _platform_detail_embed(platform: str, entry: Mapping[str, Any]) -> discord.Embed:
    spec = PLATFORM_SPECS[platform]
    raw = dict(entry or {})
    username = str(raw.get("username") or "").strip()
    shared = bool(raw.get("shared"))
    mode = platform_entry_mode(raw)
    mode_label = {
        "link": "Official profile link",
        "username": "Copy-ready username button",
        "logo": "Logo only",
    }[mode]
    title_prefix = f"{spec.emoji} " if spec.emoji else ""
    embed = discord.Embed(
        title=f"{title_prefix}{spec.label}",
        description=(
            "**Link** opens an official profile. **Username** shows the gamertag as a button and returns a private "
            "copy-ready box in the same channel. **Logo only** shows the real platform mark without requiring details."
        ),
        color=discord.Color.green() if shared else discord.Color.blurple(),
    )
    embed.add_field(
        name="Saved username",
        value=f"`{display_profile_username(username)}`" if username else "Not required",
        inline=False,
    )
    embed.add_field(name="Visibility", value="Public" if shared else "Private", inline=True)
    embed.add_field(name="Public display", value=mode_label if shared else "Hidden", inline=True)
    embed.add_field(name="Official link", value="Saved" if raw.get("url") else "Not saved", inline=True)
    return embed


class _PlatformModeButton(discord.ui.Button):
    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any], mode: str) -> None:
        spec = PLATFORM_SPECS[platform]
        raw = dict(entry or {})
        labels = {"link": "Show Link", "username": "Show Username", "logo": "Logo Only"}
        disabled = (mode == "link" and not str(raw.get("url") or "").strip()) or (
            mode == "username" and not str(raw.get("username") or "").strip()
        )
        super().__init__(
            label=labels[mode],
            emoji=spec.emoji if mode == "logo" else None,
            style=(
                discord.ButtonStyle.success
                if bool(raw.get("shared")) and platform_entry_mode(raw) == mode
                else discord.ButtonStyle.secondary
            ),
            disabled=disabled,
            row=0,
        )
        self.author_id = int(author_id)
        self.platform = platform
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        user = await get_profile_user(self.author_id, refresh=True)
        raw = dict(user.get("platforms") or {}).get(self.platform)
        raw = dict(raw) if isinstance(raw, Mapping) else {}
        try:
            entry = await save_platform_identity(
                self.author_id,
                self.platform,
                username=raw.get("username", ""),
                profile_url=raw.get("url", ""),
                shared=True,
                mode=self.mode,
            )
        except (InvalidPlatformProfile, ProfileStorageUnavailable) as exc:
            return await _private(interaction, content=f"❌ {exc}")
        await _invalidate(interaction, all_guilds=True)
        await _edit_private(
            interaction,
            content=f"✅ {PLATFORM_SPECS[self.platform].label} now uses **{self.label}**.",
            embed=_platform_detail_embed(self.platform, entry),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),
        )


class _PlatformPrivateButton(discord.ui.Button):
    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any]) -> None:
        raw = dict(entry or {})
        super().__init__(
            label="Make Private",
            style=discord.ButtonStyle.danger,
            disabled=not bool(raw.get("shared")),
            row=0,
        )
        self.author_id = int(author_id)
        self.platform = platform

    async def callback(self, interaction: discord.Interaction) -> None:
        user = await get_profile_user(self.author_id, refresh=True)
        raw = dict(user.get("platforms") or {}).get(self.platform)
        raw = dict(raw) if isinstance(raw, Mapping) else {}
        if not raw:
            return await _private(interaction, content="Nothing is saved for that platform yet.")
        try:
            entry = await save_platform_identity(
                self.author_id,
                self.platform,
                username=raw.get("username", ""),
                profile_url=raw.get("url", ""),
                shared=False,
                mode=platform_entry_mode(raw),
            )
        except (InvalidPlatformProfile, ProfileStorageUnavailable) as exc:
            return await _private(interaction, content=f"❌ {exc}")
        await _invalidate(interaction, all_guilds=True)
        await _edit_private(
            interaction,
            content=f"✅ {PLATFORM_SPECS[self.platform].label} is now private.",
            embed=_platform_detail_embed(self.platform, entry),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),
        )


class PlatformDetailView(discord.ui.View):
    def __init__(self, *, author_id: int, platform: str, entry: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.platform = str(platform)
        raw = dict(entry or {})
        spec = PLATFORM_SPECS[self.platform]
        if spec.supports_url:
            self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="link"))
        self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="username"))
        self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="logo"))
        self.add_item(_PlatformPrivateButton(author_id=self.author_id, platform=self.platform, entry=raw))
        self.remove.disabled = not bool(raw)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the person who opened this editor can use it.")
            return False
        return True

    @discord.ui.button(label="Add / Edit Details", style=discord.ButtonStyle.primary, row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        user = await get_profile_user(self.author_id, refresh=True)
        raw = dict(user.get("platforms") or {}).get(self.platform)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        await interaction.response.send_modal(
            PlatformEditModal(author_id=self.author_id, platform=self.platform, entry=entry)
        )

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            await remove_platform_identity(self.author_id, self.platform)
        except ProfileStorageUnavailable:
            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _invalidate(interaction, all_guilds=True)
        await open_platform_manager(interaction, replace=True)

    @discord.ui.button(label="Back to Platforms", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_platform_manager(interaction, replace=True)


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
        await _edit_private(
            interaction,
            embed=_platform_detail_embed(platform, entry),
            view=PlatformDetailView(author_id=view.author_id, platform=platform, entry=entry),
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
        await open_profile_signature_studio(interaction, replace=True)


async def open_platform_manager(interaction: discord.Interaction, *, replace: bool = False) -> None:
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
        if not isinstance(raw, Mapping):
            continue
        mode = platform_entry_mode(raw)
        username = str(raw.get("username") or "").strip()
        identity = (
            f"`{display_profile_username(username)}`"
            if username and mode != "logo"
            else "Logo only"
        )
        prefix = f"{spec.emoji} " if spec.emoji else ""
        lines.append(
            f"{prefix}**{spec.label}:** {identity} — "
            f"{'🌐 Public' if raw.get('shared') else '🔒 Private'} • {mode.title()}"
        )
    embed = discord.Embed(
        title="🎮 Platforms & Accounts",
        description=(
            "Choose a platform, then select **Link**, **Username**, **Logo only**, or **Private**. "
            "Logo only needs no account details, and saving details never exposes them automatically."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Saved accounts", value="\n".join(lines)[:1024] if lines else "None saved yet.", inline=False)
    panel = PlatformManagerView(author_id=member.id)
    if replace:
        await _edit_private(interaction, embed=embed, view=panel)
    else:
        await _private(interaction, embed=embed, view=panel)


async def open_server_role_display(interaction: discord.Interaction) -> None:
    member = _member(interaction)
    if member is None or interaction.guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    user = await get_profile_user(member.id, refresh=True)
    guild_row = await get_profile_guild_settings(member.guild.id, member.id, refresh=True)
    enabled = bool(
        effective_preferences(user.get("preferences"), guild_row.get("settings")).get("show_server_roles", False)
    )
    embed = discord.Embed(
        title="Server Role Display",
        description=(
            "This only controls whether safe roles already assigned by this server appear on your signature. "
            "It does **not** open or edit pronouns, identity, interests, or cosmetic tags."
        ),
        color=discord.Color.green() if enabled else discord.Color.blurple(),
    )
    embed.add_field(name="Current setting", value="Shown" if enabled else "Hidden", inline=False)
    await _edit_private(
        interaction,
        embed=embed,
        view=ServerRoleDisplayView(author_id=member.id, enabled=enabled),
    )


class ServerRoleDisplayView(discord.ui.View):
    def __init__(self, *, author_id: int, enabled: bool) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.enabled = bool(enabled)
        self.toggle.label = "Hide Server Roles" if self.enabled else "Show Server Roles"
        self.toggle.style = discord.ButtonStyle.danger if self.enabled else discord.ButtonStyle.success

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Open your own profile settings to use this.")
            return False
        return True

    @discord.ui.button(label="Show Server Roles", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await upsert_profile_user_preferences(self.author_id, {"show_server_roles": not self.enabled})
        await _invalidate(interaction, all_guilds=True)
        await open_server_role_display(interaction)

    @discord.ui.button(label="Back to Signature", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_profile_signature_studio(interaction, replace=True)


class SignatureStudioView(discord.ui.View):
    def __init__(self, *, author_id: int, live_enabled: bool = True) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.live_enabled = bool(live_enabled)
        self.live_toggle.label = "Live Signature: ON" if self.live_enabled else "Live Signature: OFF"
        self.live_toggle.emoji = "✅" if self.live_enabled else "⏸️"
        self.live_toggle.style = discord.ButtonStyle.success if self.live_enabled else discord.ButtonStyle.danger

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
        await open_platform_manager(interaction, replace=True)

    @discord.ui.button(label="Server Roles", style=discord.ButtonStyle.secondary, row=1)
    async def server_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_server_role_display(interaction)

    @discord.ui.button(label="Profile Tags", style=discord.ButtonStyle.secondary, row=1)
    async def profile_tags(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
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

    @discord.ui.button(label="Live Signature: ON", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def live_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = _member(interaction)
        if member is None:
            return await _private(interaction, content="❌ Use this inside a server as a member.")
        await _defer(interaction, component_update=True)
        try:
            user, guild_row = await asyncio.gather(
                get_profile_user(member.id, refresh=True),
                get_profile_guild_settings(member.guild.id, member.id, refresh=True),
            )
            current = bool(
                effective_preferences(
                    user.get("preferences"),
                    guild_row.get("settings"),
                ).get("live_cards_enabled", True)
            )
            if current:
                await upsert_profile_user_preferences(member.id, {"live_cards_enabled": False})
            else:
                await upsert_profile_user_preferences(member.id, {"live_cards_enabled": True})
                await upsert_profile_guild_settings(
                    member.guild.id,
                    member.id,
                    {"live_cards_enabled": None},
                )
            await _invalidate(interaction, all_guilds=True)
            updated_user, updated_guild = await asyncio.gather(
                get_profile_user(member.id, refresh=True),
                get_profile_guild_settings(member.guild.id, member.id, refresh=True),
            )
            enabled = bool(
                effective_preferences(
                    updated_user.get("preferences"),
                    updated_guild.get("settings"),
                ).get("live_cards_enabled", True)
            )
            embed = await _studio_embed(member)
        except ProfileStorageUnavailable:
            return await _edit_private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _edit_private(
            interaction,
            content=f"✅ Live Signature is now **{'ON' if enabled else 'OFF'}**.",
            embed=embed,
            view=SignatureStudioView(author_id=member.id, live_enabled=enabled),
        )

    @discord.ui.button(label="Reset My Look", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _save_member_style(
            interaction,
            DEFAULT_MEMBER_PROFILE_STYLE,
            message="Your personal signature appearance now follows the server defaults.",
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Profile signature settings closed.", embed=None, view=None)


async def open_profile_signature_studio(interaction: discord.Interaction, *, replace: bool = False) -> None:
    member = _member(interaction)
    if member is None or interaction.guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    try:
        user, guild_row = await asyncio.gather(
            get_profile_user(member.id, refresh=True),
            get_profile_guild_settings(member.guild.id, member.id, refresh=True),
        )
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    live_enabled = bool(
        effective_preferences(
            user.get("preferences"),
            guild_row.get("settings"),
        ).get("live_cards_enabled", True)
    )
    panel = SignatureStudioView(author_id=member.id, live_enabled=live_enabled)
    if replace:
        await _edit_private(interaction, embed=embed, view=panel)
    else:
        await _private(interaction, embed=embed, view=panel)


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
