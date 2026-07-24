from __future__ import annotations

"""Discord-native welcome-card font and color studio."""

from io import BytesIO
from typing import Any, Optional

import discord

from ..guild_config import get_guild_config, invalidate_guild_config
from ..ui.picker import DankPickerView, make_choice
from ..welcome_card_studio_renderer import (
    BUILTIN_THEMES,
    COLOR_MODES,
    COLOR_PRESETS,
    COLOR_SWATCHES,
    FONT_STYLES,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_hex_color,
    parse_hex_color,
    render_color_catalog,
    render_font_catalog,
)
from ..welcome_card_service import (
    configured_color_mode,
    configured_custom_colors,
    configured_font_style_key,
    configured_theme_key,
    welcome_card_file,
)
from .public_setup_group import _require_setup_permission, _upsert_config
from .public_welcome_group import _defer, _send, welcome_group


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
}


async def _component_defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


async def _retire_picker(interaction: discord.Interaction) -> None:
    try:
        await interaction.edit_original_response(view=None)
    except Exception:
        pass


async def _fresh_cfg(guild_id: int) -> Any:
    invalidate_guild_config(int(guild_id))
    return await get_guild_config(int(guild_id), refresh=True)


async def _save_and_preview(
    interaction: discord.Interaction,
    *,
    updates: dict[str, Any],
    message: str,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    await _component_defer(interaction)
    await _upsert_config(int(interaction.guild.id), updates)
    cfg = await _fresh_cfg(int(interaction.guild.id))
    card = await welcome_card_file(interaction.user, cfg)
    await _retire_picker(interaction)
    await interaction.followup.send(
        message,
        file=card,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _catalog_colors(cfg: Any) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    theme = BUILTIN_THEMES[configured_theme_key(cfg)]
    primary, secondary = configured_custom_colors(cfg)
    try:
        parsed_primary = parse_hex_color(primary)
        parsed_secondary = parse_hex_color(secondary)
        if configured_color_mode(cfg) == "custom" and parsed_primary and parsed_secondary:
            return parsed_primary, parsed_secondary
    except Exception:
        pass
    return theme.primary, theme.secondary


async def _send_font_picker(
    interaction: discord.Interaction,
    *,
    cfg: Optional[Any] = None,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    if cfg is None:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current = configured_font_style_key(cfg)
    primary, secondary = _catalog_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        style_key = normalize_font_style_key(value)
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_font_style": style_key,
            },
            message=(
                f"✅ Welcome-card font set to **{FONT_STYLES[style_key].label}**. "
                "This is the live production renderer preview."
            ),
        )

    choices = [
        make_choice(
            style.label,
            style.key,
            description=style.description,
            emoji=_FONT_EMOJIS.get(style.key, "🔤"),
            default=style.key == current,
        )
        for style in FONT_STYLES.values()
    ]
    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=on_pick,
        custom_id=f"dank:welcome:font:{interaction.guild.id}",
        placeholder="Choose a font by its visual preview…",
        title="Welcome Card Font Picker",
    )
    catalog = discord.File(
        BytesIO(
            render_font_catalog(
                display_name=interaction.user.display_name,
                primary=primary,
                secondary=secondary,
            )
        ),
        filename="welcome-font-picker.png",
    )
    sender = (
        interaction.followup.send
        if interaction.response.is_done()
        else interaction.response.send_message
    )
    await sender(
        "## 🔤 Welcome Card Font Picker\n"
        "The styles below are rendered by the same engine used for live joins. "
        "Pick the matching name from the dropdown.",
        file=catalog,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_palette_picker(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _component_defer(interaction)
    await _retire_picker(interaction)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        preset = COLOR_PRESETS.get(value)
        if preset is None:
            return await _send(component_interaction, "❌ That palette is no longer available.")
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_color_mode": "custom",
                "welcome_card_custom_primary": preset.primary,
                "welcome_card_custom_secondary": preset.secondary,
            },
            message=(
                f"✅ Welcome-card palette set to **{preset.label}**. "
                "No color code entry was required."
            ),
        )

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=[
            make_choice(
                preset.label,
                preset.key,
                description=preset.description,
                emoji=preset.emoji,
            )
            for preset in COLOR_PRESETS.values()
        ],
        on_pick=on_pick,
        custom_id=f"dank:welcome:palette:{interaction.guild.id}",
        placeholder="Choose a ready-made two-color palette…",
        title="Welcome Card Palette Picker",
    )
    catalog = discord.File(
        BytesIO(render_color_catalog(swatches=False)),
        filename="welcome-color-palettes.png",
    )
    await interaction.followup.send(
        "## 🎨 Ready-Made Palettes\n"
        "Choose the palette name shown in the visual sheet. "
        "Dank Shield saves both colors automatically.",
        file=catalog,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_secondary_swatch_picker(
    interaction: discord.Interaction,
    *,
    primary_key: str,
) -> None:
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    primary = COLOR_SWATCHES.get(primary_key)
    if primary is None:
        return await _send(interaction, "❌ That primary color is no longer available.")
    await _component_defer(interaction)
    await _retire_picker(interaction)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        secondary = COLOR_SWATCHES.get(value)
        if secondary is None:
            return await _send(component_interaction, "❌ That secondary color is no longer available.")
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_color_mode": "custom",
                "welcome_card_custom_primary": primary.hex_value,
                "welcome_card_custom_secondary": secondary.hex_value,
            },
            message=(
                f"✅ Custom colors saved: **{primary.label} → {secondary.label}**. "
                "The hex values are stored internally, so nobody had to memorize them."
            ),
        )

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=[
            make_choice(
                swatch.label,
                swatch.key,
                description=f"Use {swatch.label} as the secondary accent.",
                emoji=swatch.emoji,
            )
            for swatch in COLOR_SWATCHES.values()
        ],
        on_pick=on_pick,
        custom_id=f"dank:welcome:secondary:{interaction.guild.id}:{primary.key}",
        placeholder=f"Primary is {primary.label}. Choose the second color…",
        title="Choose Secondary Welcome Color",
    )
    await interaction.followup.send(
        f"## 🎨 Primary: {primary.emoji} {primary.label}\n"
        "Now choose the second color. A live welcome-card preview appears after saving.",
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_primary_swatch_picker(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _component_defer(interaction)
    await _retire_picker(interaction)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        await _send_secondary_swatch_picker(component_interaction, primary_key=value)

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=[
            make_choice(
                swatch.label,
                swatch.key,
                description=f"Use {swatch.label} as the main accent.",
                emoji=swatch.emoji,
            )
            for swatch in COLOR_SWATCHES.values()
        ],
        on_pick=on_pick,
        custom_id=f"dank:welcome:primary:{interaction.guild.id}",
        placeholder="Choose the main color…",
        title="Choose Primary Welcome Color",
    )
    catalog = discord.File(
        BytesIO(render_color_catalog(swatches=True)),
        filename="welcome-color-picker.png",
    )
    await interaction.followup.send(
        "## 🖌️ Custom Color Picker\n"
        "Pick the main color from the visual swatches. "
        "You will choose the second color next.",
        file=catalog,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class _AdvancedHexModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        author_id: int,
        current_primary: str = "",
        current_secondary: str = "",
    ) -> None:
        super().__init__(title="Advanced Welcome Colors", timeout=900)
        self.author_id = int(author_id)
        self.primary_input = discord.ui.TextInput(
            label="Primary hex color",
            placeholder="#22DCFF",
            default=current_primary or "",
            required=True,
            max_length=7,
        )
        self.secondary_input = discord.ui.TextInput(
            label="Secondary hex color",
            placeholder="#BC42FF",
            default=current_secondary or "",
            required=True,
            max_length=7,
        )
        self.add_item(self.primary_input)
        self.add_item(self.secondary_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.author_id:
            return await interaction.response.send_message(
                "Only the person who opened this color editor can submit it.",
                ephemeral=True,
            )
        try:
            primary = normalize_hex_color(str(self.primary_input.value))
            secondary = normalize_hex_color(str(self.secondary_input.value))
        except ValueError as exc:
            return await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        await _save_and_preview(
            interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_color_mode": "custom",
                "welcome_card_custom_primary": primary,
                "welcome_card_custom_secondary": secondary,
            },
            message=(
                f"✅ Advanced custom palette saved: `{primary}` → `{secondary}`. "
                "Hex remains available as a fallback, not the normal workflow."
            ),
        )


async def _send_color_picker(
    interaction: discord.Interaction,
    *,
    cfg: Optional[Any] = None,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    if cfg is None:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current_mode = configured_color_mode(cfg)
    current_primary, current_secondary = configured_custom_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        if value in {"auto", "profile", "card", "theme"}:
            mode = normalize_color_mode(value)
            await _save_and_preview(
                component_interaction,
                updates={
                    "welcome_card_enabled": True,
                    "welcome_card_color_mode": mode,
                    "welcome_card_custom_primary": "",
                    "welcome_card_custom_secondary": "",
                },
                message=f"✅ Welcome-card colors set to **{COLOR_MODES[mode]}**.",
            )
            return
        if value == "palette":
            return await _send_palette_picker(component_interaction)
        if value == "picker":
            return await _send_primary_swatch_picker(component_interaction)
        if value == "advanced":
            modal = _AdvancedHexModal(
                author_id=int(component_interaction.user.id),
                current_primary=current_primary,
                current_secondary=current_secondary,
            )
            return await component_interaction.response.send_modal(modal)
        await _send(component_interaction, "❌ That color option is no longer available.")

    choices = [
        make_choice(
            "Smart Auto", "auto",
            description="Profile banner/accent, then card, avatar, and theme fallbacks.",
            emoji="✨", default=current_mode == "auto",
        ),
        make_choice(
            "Member Profile", "profile",
            description="Match each member's Discord banner, accent, or avatar.",
            emoji="👤", default=current_mode == "profile",
        ),
        make_choice(
            "Card Background", "card",
            description="Match the server's uploaded welcome background.",
            emoji="🖼️", default=current_mode == "card",
        ),
        make_choice(
            "Selected Theme", "theme",
            description="Always use the chosen built-in theme palette.",
            emoji="🛡️", default=current_mode == "theme",
        ),
        make_choice(
            "Ready-Made Palettes", "palette",
            description="Choose a named two-color look in one tap.", emoji="🎨",
        ),
        make_choice(
            "Custom Color Picker", "picker",
            description="Pick primary and secondary colors from visual swatches.", emoji="🖌️",
        ),
        make_choice(
            "Advanced Hex Fallback", "advanced",
            description="Optional manual codes for designers who already know them.",
            emoji="⌨️", default=current_mode == "custom",
        ),
    ]
    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=on_pick,
        custom_id=f"dank:welcome:colors:{interaction.guild.id}",
        placeholder="Choose automatic colors, palettes, or the visual picker…",
        title="Welcome Card Color Studio",
    )
    sender = (
        interaction.followup.send
        if interaction.response.is_done()
        else interaction.response.send_message
    )
    await sender(
        "## 🎨 Welcome Card Color Studio\n"
        "No color code is required. Use **Ready-Made Palettes** or "
        "**Custom Color Picker** for visual choices; hex is only the advanced fallback.",
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@welcome_group.command(
    name="card-font",
    description="Open the visual font picker and preview every live style.",
)
async def welcome_card_font(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    await _send_font_picker(interaction, cfg=cfg)


@welcome_group.command(
    name="card-colors",
    description="Open automatic colors, ready-made palettes, and the visual color picker.",
)
async def welcome_card_colors(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    await _send_color_picker(interaction, cfg=cfg)


@welcome_group.command(
    name="card-style",
    description="Show current welcome-card styling and open its visual controls.",
)
async def welcome_card_style(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    font_key = configured_font_style_key(cfg)
    color_key = configured_color_mode(cfg)
    primary, secondary = configured_custom_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        if value == "font":
            await _component_defer(component_interaction)
            await _retire_picker(component_interaction)
            fresh = await get_guild_config(int(component_interaction.guild.id), refresh=True)
            return await _send_font_picker(component_interaction, cfg=fresh)
        if value == "colors":
            await _component_defer(component_interaction)
            await _retire_picker(component_interaction)
            fresh = await get_guild_config(int(component_interaction.guild.id), refresh=True)
            return await _send_color_picker(component_interaction, cfg=fresh)
        if value == "preview":
            await _component_defer(component_interaction)
            card = await welcome_card_file(component_interaction.user, cfg)
            return await component_interaction.followup.send(
                "Live welcome-card preview:", file=card, ephemeral=True,
            )

    controls = DankPickerView(
        author_id=int(interaction.user.id),
        choices=[
            make_choice(
                "Change Font", "font",
                description="Open the visual font preview picker.", emoji="🔤",
            ),
            make_choice(
                "Change Colors", "colors",
                description="Open automatic colors and visual palettes.", emoji="🎨",
            ),
            make_choice(
                "Preview Again", "preview",
                description="Render the current production card again.", emoji="👁️",
            ),
        ],
        on_pick=on_pick,
        custom_id=f"dank:welcome:studio:{interaction.guild.id}",
        placeholder="Choose what to customize…",
        title="Welcome Card Studio",
    )

    lines = [
        "## 🪄 Welcome Card Studio",
        f"**Font:** {FONT_STYLES[font_key].label}",
        f"**Colors:** {COLOR_MODES[color_key]}",
    ]
    if color_key == "custom" and primary and secondary:
        preset_match = next(
            (
                preset.label
                for preset in COLOR_PRESETS.values()
                if preset.primary.upper() == primary.upper()
                and preset.secondary.upper() == secondary.upper()
            ),
            None,
        )
        lines.append(f"**Palette:** {preset_match or 'Custom picked colors'}")
    if color_key in {"auto", "profile"}:
        lines.append(
            "Profile banners/accent colors are detected automatically when Discord provides them."
        )
    card = await welcome_card_file(interaction.user, cfg)
    await interaction.followup.send(
        "\n".join(lines),
        file=card,
        view=controls,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "welcome_card_colors",
    "welcome_card_font",
    "welcome_card_style",
]
