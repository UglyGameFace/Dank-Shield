from __future__ import annotations

"""Canonical `/dank welcome` styling commands.

The full button-first panel lives in ``welcome_card_studio_ui``. These commands
provide reliable upload and direct-entry surfaces after `/dank` compaction.
Every save is acknowledged even when the optional preview render fails.
"""

import asyncio
from pathlib import Path
from typing import Any, Mapping, Optional

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
    configured_shuffle_mode,
    welcome_card_file,
)
from ..welcome_card_typography_engine import (
    COLOR_MODES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    DEFAULT_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_hex_color,
)
from .public_setup_group import _require_setup_permission, _upsert_config
from .public_welcome_group import (
    register_public_welcome_group_commands,
    welcome_group,
)

_REGISTERED = False
_EXPECTED_COMMANDS = {
    "card-colors",
    "card-font",
    "card-font-clear",
    "card-font-upload",
    "card-shuffle",
    "card-style",
}

SHUFFLE_MODE_LABELS = {
    "off": "Off",
    "fonts": "Shuffle Fonts",
    "themes": "Shuffle Themes",
    "fonts_themes": "Shuffle Fonts + Themes",
    "everything": "Shuffle Everything",
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
    CUSTOM_FONT_STYLE_KEY: "📎",
}


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


async def _fresh_cfg(guild_id: int) -> Any:
    invalidate_guild_config(int(guild_id))
    return await get_guild_config(int(guild_id), refresh=True)


async def _save_cfg(
    interaction: discord.Interaction,
    updates: Mapping[str, Any],
) -> Any:
    if interaction.guild is None:
        raise RuntimeError("This must be used inside a server.")
    await _upsert_config(int(interaction.guild.id), dict(updates))
    return await _fresh_cfg(int(interaction.guild.id))


async def _optional_preview(
    member: discord.Member,
    cfg: Any,
) -> tuple[Optional[discord.File], str]:
    try:
        return await welcome_card_file(member, cfg), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


async def _save_and_preview(
    interaction: discord.Interaction,
    *,
    updates: Mapping[str, Any],
    success: str,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    await _defer(interaction)
    try:
        cfg = await _save_cfg(interaction, updates)
    except Exception as exc:
        return await interaction.followup.send(
            f"❌ Nothing was saved: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    preview, preview_error = await _optional_preview(interaction.user, cfg)
    content = success
    if preview_error:
        content += (
            "\n⚠️ Settings **were saved**, but the preview could not render: "
            f"`{preview_error}`. Open `/dank welcome card-studio` to repair the active asset."
        )
    await interaction.followup.send(
        content,
        file=preview,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _font_label(cfg: Any) -> str:
    key = configured_font_style_key(cfg)
    custom_font, custom_name = configured_custom_font(cfg)
    if key == CUSTOM_FONT_STYLE_KEY and custom_font:
        return custom_name
    style = FONT_STYLES.get(key)
    return getattr(style, "label", key.replace("_", " ").title())


async def _send_font_picker(
    interaction: discord.Interaction,
    *,
    cfg: Optional[Any] = None,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    if cfg is None:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current = configured_font_style_key(cfg)
    custom_font, custom_name = configured_custom_font(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        style_key = normalize_font_style_key(value)
        if style_key == CUSTOM_FONT_STYLE_KEY and not custom_font:
            return await _private(
                component_interaction,
                content="❌ No usable custom font is stored. Upload one first.",
            )
        label = (
            custom_name
            if style_key == CUSTOM_FONT_STYLE_KEY
            else FONT_STYLES[style_key].label
        )
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_font_style": style_key,
            },
            success=f"✅ Welcome-card font set to **{label}** and cards enabled.",
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
                f"Uploaded: {custom_name}"[:100],
                CUSTOM_FONT_STYLE_KEY,
                description="Use the server's validated uploaded font.",
                emoji="📎",
                default=current == CUSTOM_FONT_STYLE_KEY,
            )
        )

    await _private(
        interaction,
        content=(
            "## 🔤 Welcome Card Fonts\nChoose the font used by the live Studio runtime. "
            "Uploads remain available through `/dank welcome card-font-upload`."
        ),
        view=DankPickerView(
            author_id=int(interaction.user.id),
            choices=choices,
            on_pick=on_pick,
            custom_id=f"dank:welcome:font:v2:{interaction.guild.id}",
            placeholder="Choose the live welcome-card font…",
            title="Welcome Card Fonts",
        ),
    )


class AdvancedWelcomeColorsModal(discord.ui.Modal):
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
            default=current_primary,
            required=True,
            max_length=7,
        )
        self.secondary_input = discord.ui.TextInput(
            label="Secondary hex color",
            placeholder="#BC42FF",
            default=current_secondary,
            required=True,
            max_length=7,
        )
        self.add_item(self.primary_input)
        self.add_item(self.secondary_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(
                interaction,
                content="❌ Only the person who opened this editor can submit it.",
            )
        try:
            primary = normalize_hex_color(str(self.primary_input.value))
            secondary = normalize_hex_color(str(self.secondary_input.value))
        except ValueError as exc:
            return await _private(interaction, content=f"❌ {exc}")
        await _save_and_preview(
            interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_color_mode": "custom",
                "welcome_card_custom_primary": primary,
                "welcome_card_custom_secondary": secondary,
            },
            success=f"✅ Custom palette saved: `{primary}` → `{secondary}`.",
        )


async def _send_palette_picker(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        preset = COLOR_PRESETS.get(value)
        if preset is None:
            return await _private(
                component_interaction,
                content="❌ That palette is no longer available.",
            )
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_color_mode": "custom",
                "welcome_card_custom_primary": preset.primary,
                "welcome_card_custom_secondary": preset.secondary,
            },
            success=f"✅ Welcome-card palette set to **{preset.label}**.",
        )

    await _private(
        interaction,
        content="## 🎨 Ready-Made Welcome Palettes",
        view=DankPickerView(
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
            custom_id=f"dank:welcome:palette:v2:{interaction.guild.id}",
            placeholder="Choose a ready-made palette…",
            title="Welcome Card Palettes",
        ),
    )


async def _send_color_picker(
    interaction: discord.Interaction,
    *,
    cfg: Optional[Any] = None,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    if cfg is None:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current_mode = configured_color_mode(cfg)
    current_primary, current_secondary = configured_custom_colors(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        if value in {"auto", "profile", "card", "theme"}:
            mode = normalize_color_mode(value)
            return await _save_and_preview(
                component_interaction,
                updates={
                    "welcome_card_enabled": True,
                    "welcome_card_color_mode": mode,
                    "welcome_card_custom_primary": "",
                    "welcome_card_custom_secondary": "",
                },
                success=f"✅ Welcome-card colors set to **{COLOR_MODES[mode]}**.",
            )
        if value == "palette":
            return await _send_palette_picker(component_interaction)
        if value == "advanced":
            return await component_interaction.response.send_modal(
                AdvancedWelcomeColorsModal(
                    author_id=int(component_interaction.user.id),
                    current_primary=current_primary,
                    current_secondary=current_secondary,
                )
            )
        await _private(
            component_interaction,
            content="❌ That color option is no longer available.",
        )

    await _private(
        interaction,
        content="## 🎨 Welcome Card Colors",
        view=DankPickerView(
            author_id=int(interaction.user.id),
            choices=[
                make_choice(
                    "Smart Auto",
                    "auto",
                    description="Profile, card, avatar, and theme fallbacks.",
                    emoji="✨",
                    default=current_mode == "auto",
                ),
                make_choice(
                    "Member Profile",
                    "profile",
                    description="Match each member's profile visuals.",
                    emoji="👤",
                    default=current_mode == "profile",
                ),
                make_choice(
                    "Card Background",
                    "card",
                    description="Match the selected or uploaded card background.",
                    emoji="🖼️",
                    default=current_mode == "card",
                ),
                make_choice(
                    "Selected Theme",
                    "theme",
                    description="Always use the built-in theme palette.",
                    emoji="🛡️",
                    default=current_mode == "theme",
                ),
                make_choice(
                    "Ready-Made Palettes",
                    "palette",
                    description="Choose a named two-color palette.",
                    emoji="🎨",
                ),
                make_choice(
                    "Advanced Hex Colors",
                    "advanced",
                    description="Enter two exact hex colors.",
                    emoji="⌨️",
                    default=current_mode == "custom",
                ),
            ],
            on_pick=on_pick,
            custom_id=f"dank:welcome:colors:v2:{interaction.guild.id}",
            placeholder="Choose how live card colors are resolved…",
            title="Welcome Card Colors",
        ),
    )


async def _send_shuffle_picker(
    interaction: discord.Interaction,
    *,
    cfg: Optional[Any] = None,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    if cfg is None:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current = configured_shuffle_mode(cfg)

    async def on_pick(component_interaction: discord.Interaction, value: str) -> None:
        mode = str(value or "").strip().lower()
        label = SHUFFLE_MODE_LABELS.get(mode)
        if label is None:
            return await _private(
                component_interaction,
                content="❌ That shuffle mode is no longer available.",
            )
        await _save_and_preview(
            component_interaction,
            updates={
                "welcome_card_enabled": True,
                "welcome_card_shuffle_mode": mode,
            },
            success=(
                f"✅ Welcome-card shuffle set to **{label}**. Results are stable "
                "per member so retries keep the same design."
            ),
        )

    await _private(
        interaction,
        content="## 🔀 Welcome Card Shuffle",
        view=DankPickerView(
            author_id=int(interaction.user.id),
            choices=[
                make_choice(
                    "Off",
                    "off",
                    description="Always use the configured fixed design.",
                    emoji="🛑",
                    default=current == "off",
                ),
                make_choice(
                    "Shuffle Fonts",
                    "fonts",
                    description="Rotate fonts while preserving theme/colors.",
                    emoji="🔤",
                    default=current == "fonts",
                ),
                make_choice(
                    "Shuffle Themes",
                    "themes",
                    description="Rotate built-in themes while preserving font/colors.",
                    emoji="🖼️",
                    default=current == "themes",
                ),
                make_choice(
                    "Shuffle Fonts + Themes",
                    "fonts_themes",
                    description="Rotate fonts and themes together.",
                    emoji="🎲",
                    default=current == "fonts_themes",
                ),
                make_choice(
                    "Shuffle Everything",
                    "everything",
                    description="Rotate font, theme, and safe palette.",
                    emoji="🌈",
                    default=current == "everything",
                ),
            ],
            on_pick=on_pick,
            custom_id=f"dank:welcome:shuffle:v2:{interaction.guild.id}",
            placeholder="Choose how live cards should shuffle…",
            title="Welcome Card Shuffle",
        ),
    )


async def welcome_card_font(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    await _send_font_picker(
        interaction,
        cfg=await get_guild_config(int(interaction.guild.id), refresh=True),
    )


async def welcome_card_colors(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    await _send_color_picker(
        interaction,
        cfg=await get_guild_config(int(interaction.guild.id), refresh=True),
    )


async def welcome_card_shuffle(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    await _send_shuffle_picker(
        interaction,
        cfg=await get_guild_config(int(interaction.guild.id), refresh=True),
    )


async def welcome_card_font_upload(
    interaction: discord.Interaction,
    font_file: discord.Attachment,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    suffix = Path(str(font_file.filename or "")).suffix.lower()
    if suffix not in SUPPORTED_FONT_EXTENSIONS:
        return await _private(
            interaction,
            content=f"❌ Unsupported font type. Upload **{supported_font_types_text()}**.",
        )
    if int(getattr(font_file, "size", 0) or 0) > MAX_FONT_UPLOAD_BYTES:
        return await _private(
            interaction,
            content="❌ The font file exceeds the **4 MB** upload limit.",
        )
    await _defer(interaction)
    try:
        raw = await font_file.read()
        normalized = await asyncio.to_thread(
            normalize_uploaded_font,
            raw,
            str(font_file.filename or "uploaded-font"),
        )
    except ValueError as exc:
        return await interaction.followup.send(
            f"❌ {exc}",
            ephemeral=True,
        )
    except Exception as exc:
        return await interaction.followup.send(
            f"❌ Font upload failed safely: `{type(exc).__name__}`. Nothing was saved.",
            ephemeral=True,
        )

    try:
        cfg = await _save_cfg(
            interaction,
            {
                "welcome_card_enabled": True,
                "welcome_card_font_style": CUSTOM_FONT_STYLE_KEY,
                "welcome_card_custom_font_b64": encode_custom_font(normalized.data),
                "welcome_card_custom_font_name": normalized.display_name,
                "welcome_card_custom_font_format": normalized.source_format,
                "welcome_card_custom_font_glyphs": normalized.glyph_count,
            },
        )
    except Exception as exc:
        return await interaction.followup.send(
            f"❌ Valid font, but nothing was saved: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )

    preview, preview_error = await _optional_preview(interaction.user, cfg)
    content = (
        f"✅ Custom font **{normalized.display_name}** uploaded and activated.\n"
        f"**Format:** {normalized.source_format} • **Glyphs:** {normalized.glyph_count:,}"
    )
    if preview_error:
        content += f"\n⚠️ Saved, but preview failed: `{preview_error}`."
    await interaction.followup.send(
        content,
        file=preview,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def welcome_card_font_clear(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(
            interaction,
            content="❌ This must be used inside a server.",
        )
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    current = configured_font_style_key(cfg)
    next_style = DEFAULT_FONT_STYLE_KEY if current == CUSTOM_FONT_STYLE_KEY else current
    await _save_and_preview(
        interaction,
        updates={
            "welcome_card_font_style": next_style,
            "welcome_card_custom_font_b64": "",
            "welcome_card_custom_font_name": "",
            "welcome_card_custom_font_format": "",
            "welcome_card_custom_font_glyphs": 0,
        },
        success=f"✅ Uploaded font removed. Active font: **{FONT_STYLES[next_style].label}**.",
    )


async def welcome_card_style(interaction: discord.Interaction) -> None:
    from stoney_verify.welcome_card_studio_ui import open_welcome_card_studio

    await open_welcome_card_studio(interaction)


def _add_command(name: str, description: str, callback: Any) -> None:
    if welcome_group.get_command(name) is not None:
        raise RuntimeError(f"duplicate /dank welcome command: {name}")
    welcome_group.add_command(
        app_commands.Command(
            name=name,
            description=description,
            callback=callback,
        )
    )


def register_public_welcome_card_studio_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    register_public_welcome_group_commands(bot, tree)
    _add_command(
        "card-font",
        "Choose the live welcome-card font.",
        welcome_card_font,
    )
    _add_command(
        "card-colors",
        "Choose automatic colors or a palette.",
        welcome_card_colors,
    )
    _add_command(
        "card-font-upload",
        "Upload a TTF, OTF, TTC, OTC, WOFF, or WOFF2 font.",
        welcome_card_font_upload,
    )
    _add_command(
        "card-font-clear",
        "Remove the server's uploaded welcome-card font.",
        welcome_card_font_clear,
    )
    _add_command(
        "card-shuffle",
        "Choose how live cards shuffle fonts, themes, or colors.",
        welcome_card_shuffle,
    )
    _add_command(
        "card-style",
        "Open the complete Welcome Card Studio.",
        welcome_card_style,
    )
    names = {
        str(getattr(command, "name", ""))
        for command in getattr(welcome_group, "commands", [])
        if getattr(command, "name", "")
    }
    missing = sorted(_EXPECTED_COMMANDS - names)
    if missing:
        raise RuntimeError(
            "welcome card studio registration incomplete: " + ", ".join(missing)
        )
    _REGISTERED = True
    print(
        "✅ public_welcome_card_studio registered reliable canonical commands "
        f"commands={sorted(_EXPECTED_COMMANDS)}"
    )


__all__ = [
    "register_public_welcome_card_studio_commands",
    "welcome_card_colors",
    "welcome_card_font",
    "welcome_card_font_clear",
    "welcome_card_font_upload",
    "welcome_card_shuffle",
    "welcome_card_style",
]
