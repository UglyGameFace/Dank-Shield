from __future__ import annotations

"""Proportion-safe font gallery and custom font upload controls."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands

from ..guild_config import get_guild_config, invalidate_guild_config
from ..ui.picker import DankPickerView, make_choice
from ..welcome_card_font_assets import (
    MAX_FONT_UPLOAD_BYTES,
    SUPPORTED_FONT_EXTENSIONS,
    encode_custom_font,
    normalize_uploaded_font,
    supported_font_types_text,
)
from ..welcome_card_service import (
    configured_color_mode,
    configured_custom_colors,
    configured_custom_font,
    configured_font_style_key,
    configured_theme_key,
    welcome_card_file,
)
from ..welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_MODES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    DEFAULT_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_font_style_key,
    parse_hex_color,
    render_font_catalog,
)
from . import welcome_card_style_commands as existing_style_commands
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
    "stencil": "🎖️",
    "varsity": "🏆",
    "blackletter": "🌑",
    "prism": "🌈",
    "terminal": "⌨️",
    "retro": "📼",
    CUSTOM_FONT_STYLE_KEY: "📎",
}


def _replace_existing_command(name: str) -> None:
    try:
        if welcome_group.get_command(name) is not None:
            welcome_group.remove_command(name)
    except Exception:
        pass


# The original studio commands are loaded first for color controls. Replace only
# font/style surfaces so there is one visible command for each name.
_replace_existing_command("card-font")
_replace_existing_command("card-style")


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
    custom_font, custom_font_name = configured_custom_font(cfg)
    primary, secondary = _catalog_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        style_key = normalize_font_style_key(value)
        if style_key == CUSTOM_FONT_STYLE_KEY and not custom_font:
            return await _send(
                component_interaction,
                "❌ No custom font is stored yet. Use `/dank welcome card-font-upload` first.",
            )
        label = custom_font_name if style_key == CUSTOM_FONT_STYLE_KEY else FONT_STYLES[style_key].label
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_font_style": style_key,
            },
            message=(
                f"✅ Welcome-card font set to **{label}**. "
                "The live card was uniformly fitted inside both width and height limits."
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
    if custom_font:
        choices.append(
            make_choice(
                f"Uploaded: {custom_font_name}"[:100],
                CUSTOM_FONT_STYLE_KEY,
                description="Use the server's validated uploaded font.",
                emoji=_FONT_EMOJIS[CUSTOM_FONT_STYLE_KEY],
                default=current == CUSTOM_FONT_STYLE_KEY,
            )
        )

    view = DankPickerView(
        author_id=int(interaction.user.id),
        choices=choices,
        on_pick=on_pick,
        custom_id=f"dank:welcome:font:v2:{interaction.guild.id}",
        placeholder="Choose one of the proportion-safe font previews…",
        title="Welcome Card Font Gallery",
    )
    catalog = discord.File(
        BytesIO(
            render_font_catalog(
                display_name=interaction.user.display_name,
                primary=primary,
                secondary=secondary,
                custom_font_bytes=custom_font,
                custom_font_name=custom_font_name,
            )
        ),
        filename="welcome-font-gallery.png",
    )
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(
        "## 🔤 Welcome Card Font Gallery\n"
        "Every preview is measured against the same width **and height** safe box—no stretched or crushed lettering. "
        "Use `/dank welcome card-font-upload` to add your own font.",
        file=catalog,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@welcome_group.command(
    name="card-font",
    description="Open the expanded proportion-safe font gallery.",
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
    name="card-font-upload",
    description="Upload a custom TTF, OTF, collection, WOFF, or WOFF2 font.",
)
@app_commands.describe(
    font_file="Font file: TTF, OTF, TTC, OTC, WOFF, or WOFF2. You must have rights to use it.",
)
async def welcome_card_font_upload(
    interaction: discord.Interaction,
    font_file: discord.Attachment,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")

    suffix = Path(str(font_file.filename or "")).suffix.lower()
    if suffix not in SUPPORTED_FONT_EXTENSIONS:
        return await _send(
            interaction,
            f"❌ Unsupported font type. Upload **{supported_font_types_text()}**.",
        )
    if int(getattr(font_file, "size", 0) or 0) > MAX_FONT_UPLOAD_BYTES:
        return await _send(interaction, "❌ The font file exceeds the **4 MB** upload limit.")

    await _defer(interaction)
    try:
        raw = await font_file.read()
        normalized = await asyncio.to_thread(
            normalize_uploaded_font,
            raw,
            str(font_file.filename or "uploaded-font"),
        )
    except ValueError as exc:
        return await _send(interaction, f"❌ {exc}")
    except Exception as exc:
        return await _send(
            interaction,
            f"❌ Font upload failed safely: `{type(exc).__name__}`. Nothing was saved.",
        )

    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": True,
            "welcome_card_font_style": CUSTOM_FONT_STYLE_KEY,
            "welcome_card_custom_font_b64": encode_custom_font(normalized.data),
            "welcome_card_custom_font_name": normalized.display_name,
            "welcome_card_custom_font_format": normalized.source_format,
            "welcome_card_custom_font_glyphs": normalized.glyph_count,
        },
    )
    cfg = await _fresh_cfg(int(interaction.guild.id))
    card = await welcome_card_file(interaction.user, cfg)
    await interaction.followup.send(
        "\n".join(
            [
                f"✅ Custom font **{normalized.display_name}** uploaded and activated.",
                f"**Format:** {normalized.source_format}",
                f"**Glyphs:** {normalized.glyph_count:,}",
                "The file was validated, normalized, and fitted proportionally before use.",
                "Only upload fonts you are licensed or otherwise allowed to use.",
            ]
        ),
        file=card,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@welcome_group.command(
    name="card-font-clear",
    description="Remove the server's uploaded font and return to a built-in style.",
)
async def welcome_card_font_clear(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current = configured_font_style_key(cfg)
    next_style = DEFAULT_FONT_STYLE_KEY if current == CUSTOM_FONT_STYLE_KEY else current
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_font_style": next_style,
            "welcome_card_custom_font_b64": "",
            "welcome_card_custom_font_name": "",
            "welcome_card_custom_font_format": "",
            "welcome_card_custom_font_glyphs": 0,
        },
    )
    fresh = await _fresh_cfg(int(interaction.guild.id))
    card = await welcome_card_file(interaction.user, fresh)
    await interaction.followup.send(
        "✅ Uploaded welcome-card font removed. "
        f"Active font: **{FONT_STYLES[next_style].label}**.",
        file=card,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@welcome_group.command(
    name="card-style",
    description="Show current styling and open font/color controls.",
)
async def welcome_card_style(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    font_key = configured_font_style_key(cfg)
    custom_font, custom_font_name = configured_custom_font(cfg)
    color_key = configured_color_mode(cfg)
    primary, secondary = configured_custom_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        if value == "font":
            await _component_defer(component_interaction)
            await _retire_picker(component_interaction)
            fresh_cfg = await get_guild_config(int(component_interaction.guild.id), refresh=True)
            return await _send_font_picker(component_interaction, cfg=fresh_cfg)
        if value == "colors":
            await _component_defer(component_interaction)
            await _retire_picker(component_interaction)
            fresh_cfg = await get_guild_config(int(component_interaction.guild.id), refresh=True)
            return await existing_style_commands._send_color_picker(component_interaction, cfg=fresh_cfg)
        if value == "preview":
            await _component_defer(component_interaction)
            card = await welcome_card_file(component_interaction.user, cfg)
            return await component_interaction.followup.send(
                "Live welcome-card preview:",
                file=card,
                ephemeral=True,
            )

    controls = DankPickerView(
        author_id=int(interaction.user.id),
        choices=[
            make_choice(
                "Change Font",
                "font",
                description="Open the expanded proportional font gallery.",
                emoji="🔤",
            ),
            make_choice(
                "Change Colors",
                "colors",
                description="Open automatic colors, palettes, and visual swatches.",
                emoji="🎨",
            ),
            make_choice(
                "Preview Again",
                "preview",
                description="Render the current production card again.",
                emoji="👁️",
            ),
        ],
        on_pick=on_pick,
        custom_id=f"dank:welcome:studio:v2:{interaction.guild.id}",
        placeholder="Choose what to customize…",
        title="Welcome Card Studio",
    )

    font_label = custom_font_name if font_key == CUSTOM_FONT_STYLE_KEY and custom_font else FONT_STYLES[font_key].label
    lines = [
        "## 🪄 Welcome Card Studio",
        f"**Font:** {font_label}",
        f"**Colors:** {COLOR_MODES[color_key]}",
        "**Fit:** uniform width + height bounds; no horizontal stretching",
    ]
    if custom_font:
        lines.append("**Custom upload:** stored and available")
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
        lines.append("Profile banners/accent colors are detected automatically when Discord provides them.")

    card = await welcome_card_file(interaction.user, cfg)
    await interaction.followup.send(
        "\n".join(lines),
        file=card,
        view=controls,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "welcome_card_font",
    "welcome_card_font_clear",
    "welcome_card_font_upload",
    "welcome_card_style",
]
