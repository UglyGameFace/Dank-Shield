from __future__ import annotations

"""Public controls for automatic welcome-card colors and typography."""

from typing import Optional

import discord
from discord import app_commands

from ..guild_config import get_guild_config, invalidate_guild_config
from ..welcome_card_renderer import (
    COLOR_MODES,
    FONT_STYLES,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_hex_color,
)
from ..welcome_card_service import (
    configured_color_mode,
    configured_custom_colors,
    configured_font_style_key,
    welcome_card_file,
)
from .public_setup_group import _require_setup_permission, _upsert_config
from .public_welcome_group import _defer, _send, welcome_group

_FONT_CHOICES = [
    app_commands.Choice(name=style.label, value=style.key)
    for style in FONT_STYLES.values()
]
_COLOR_MODE_CHOICES = [
    app_commands.Choice(name=label, value=key)
    for key, label in COLOR_MODES.items()
]


async def _preview_after_save(interaction: discord.Interaction, message: str) -> None:
    if interaction.guild is None:
        return
    invalidate_guild_config(int(interaction.guild.id))
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    card = await welcome_card_file(interaction.user, cfg) if isinstance(interaction.user, discord.Member) else None
    await interaction.followup.send(message, file=card, ephemeral=True)


@welcome_group.command(
    name="card-font",
    description="Choose the live welcome-card font style and preview it.",
)
@app_commands.describe(font_style="The display style used for WELCOME, names, and the member line.")
@app_commands.choices(font_style=_FONT_CHOICES)
async def welcome_card_font(
    interaction: discord.Interaction,
    font_style: app_commands.Choice[str],
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    style_key = normalize_font_style_key(font_style.value)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": True,
            "welcome_card_font_style": style_key,
        },
    )
    await _preview_after_save(
        interaction,
        f"✅ Welcome-card font set to **{FONT_STYLES[style_key].label}**. The live preview is below.",
    )


@welcome_group.command(
    name="card-colors",
    description="Use automatic profile/card colors, a theme palette, or custom hex colors.",
)
@app_commands.describe(
    mode="How Dank Shield should choose each card's accent colors.",
    primary="Custom mode only. First hex color, such as #22DCFF.",
    secondary="Custom mode only. Second hex color, such as #BC42FF.",
)
@app_commands.choices(mode=_COLOR_MODE_CHOICES)
async def welcome_card_colors(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    primary: Optional[str] = None,
    secondary: Optional[str] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")

    mode_key = normalize_color_mode(mode.value)
    normalized_primary = ""
    normalized_secondary = ""
    if mode_key == "custom":
        if not primary or not secondary:
            return await _send(
                interaction,
                "❌ Custom mode needs both `primary` and `secondary`, for example `#22DCFF` and `#BC42FF`.",
            )
        try:
            normalized_primary = normalize_hex_color(primary)
            normalized_secondary = normalize_hex_color(secondary)
        except ValueError as exc:
            return await _send(interaction, f"❌ {exc}")

    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": True,
            "welcome_card_color_mode": mode_key,
            "welcome_card_custom_primary": normalized_primary,
            "welcome_card_custom_secondary": normalized_secondary,
        },
    )

    detail = COLOR_MODES[mode_key]
    if mode_key == "custom":
        detail += f" (`{normalized_primary}` → `{normalized_secondary}`)"
    elif mode_key == "auto":
        detail += " (profile banner/accent → card background → avatar → theme fallback)"
    elif mode_key == "profile":
        detail += " (profile banner/accent → avatar → theme fallback)"
    elif mode_key == "card":
        detail += " (uploaded card background → theme fallback)"

    await _preview_after_save(
        interaction,
        f"✅ Welcome-card color mode set to **{detail}**. The live preview is below.",
    )


@welcome_group.command(
    name="card-style",
    description="Show the current automatic color and font settings with a live preview.",
)
async def welcome_card_style(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    font_key = configured_font_style_key(cfg)
    color_key = configured_color_mode(cfg)
    primary, secondary = configured_custom_colors(cfg)
    lines = [
        f"**Font:** {FONT_STYLES[font_key].label}",
        f"**Colors:** {COLOR_MODES[color_key]}",
    ]
    if color_key == "custom" and primary and secondary:
        lines.append(f"**Custom palette:** `{primary}` → `{secondary}`")
    if color_key in {"auto", "profile"}:
        lines.append("Discord profile banners/accent colors are detected automatically when available; safe fallbacks are built in.")
    card = await welcome_card_file(interaction.user, cfg) if isinstance(interaction.user, discord.Member) else None
    await interaction.followup.send("\n".join(lines), file=card, ephemeral=True)


__all__ = [
    "welcome_card_colors",
    "welcome_card_font",
    "welcome_card_style",
]
