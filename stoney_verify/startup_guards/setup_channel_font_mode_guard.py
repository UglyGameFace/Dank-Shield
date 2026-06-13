from __future__ import annotations

"""Discord setup UI for Channel Builder font defaults."""

from typing import Any

import discord

_CONFIG_KEY = "channel_builder_style_options"
_STYLE_LABELS: dict[str, str] = {
    "normal": "Normal",
    "bold_sans": "Bold Sans",
    "italic_sans": "Italic Sans",
    "monospace": "Monospace",
    "fullwidth": "Fullwidth",
    "small_caps": "Small Caps",
}
_SCOPE_LABELS: dict[str, str] = {
    "whole_name": "Style generated name",
    "text_only": "Text only — keep emoji",
}


def _log(message: str) -> None:
    try:
        print(f"🔤 setup_channel_font_mode_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def normalize_font_options(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    style = _safe_str(raw.get("unicodeStyle") or raw.get("unicode_style") or raw.get("font") or raw.get("style") or "normal").lower().replace("-", "_")
    if style not in _STYLE_LABELS:
        style = "normal"
    scope = _safe_str(raw.get("unicodeStyleScope") or raw.get("unicode_style_scope") or raw.get("fontApplyMode") or raw.get("font_apply_mode") or raw.get("scope") or "whole_name").lower().replace("-", "_")
    if scope not in _SCOPE_LABELS:
        scope = "whole_name"
    return {"unicodeStyle": style, "unicodeStyleScope": scope}


async def load_channel_font_options(guild_id: int) -> dict[str, str]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=True)
        stored = cfg.get(_CONFIG_KEY) if isinstance(cfg, dict) else None
        if isinstance(stored, dict):
            return normalize_font_options(stored)
        return normalize_font_options(
            {
                "unicodeStyle": cfg.get("channel_builder_unicode_style") if isinstance(cfg, dict) else None,
                "unicodeStyleScope": cfg.get("channel_builder_unicode_style_scope") if isinstance(cfg, dict) else None,
            }
        )
    except Exception:
        return normalize_font_options({})


async def save_channel_font_options(guild_id: int, options: dict[str, str]) -> dict[str, str]:
    clean = normalize_font_options(options)
    try:
        from stoney_verify.guild_config import clear_guild_config_cache, upsert_guild_config

        await upsert_guild_config(
            guild_id,
            {
                _CONFIG_KEY: clean,
                "channel_builder_unicode_style": clean["unicodeStyle"],
                "channel_builder_unicode_style_scope": clean["unicodeStyleScope"],
            },
        )
        clear_guild_config_cache(guild_id)
    except Exception:
        pass
    return clean


async def _require_setup(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


def _example(options: dict[str, str]) -> str:
    scope = options.get("unicodeStyleScope") or "whole_name"
    style = options.get("unicodeStyle") or "normal"
    if scope == "text_only" and style == "bold_sans":
        return "🔥・𝗴𝗲𝗻𝗲𝗿𝗮𝗹-𝗰𝗵𝗮𝘁"
    if scope == "text_only" and style == "small_caps":
        return "🔥・ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ"
    if scope == "text_only":
        return "🔥・general-chat"
    if style == "bold_sans":
        return "🎮・𝗴𝗲𝗻𝗲𝗿𝗮𝗹-𝗰𝗵𝗮𝘁"
    if style == "small_caps":
        return "🎮・ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ"
    return "🎮・general-chat"


async def build_channel_font_embed(guild_id: int) -> discord.Embed:
    options = await load_channel_font_options(guild_id)
    embed = discord.Embed(
        title="🔤 Channel Name Fonts",
        description=(
            "Set the bot-side default for Channel Builder naming.\n\n"
            "Use **Text only — keep emoji** when a channel already has emoji/decorations and you only want the words changed."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Font style", value=_STYLE_LABELS.get(options["unicodeStyle"], "Normal"), inline=True)
    embed.add_field(name="Apply mode", value=_SCOPE_LABELS.get(options["unicodeStyleScope"], "Style generated name"), inline=True)
    embed.add_field(name="Example", value=f"`{_example(options)}`", inline=False)
    embed.set_footer(text="Path: /dank setup → More Options → Channel Name Fonts")
    return embed


class FontStyleSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(label=label, value=value, default=value == current)
            for value, label in _STYLE_LABELS.items()
        ]
        super().__init__(placeholder="Choose font style…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        current = await load_channel_font_options(int(interaction.guild.id))
        current["unicodeStyle"] = str(self.values[0])
        saved = await save_channel_font_options(int(interaction.guild.id), current)
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id)), view=ChannelFontModeView(saved))


class ChannelFontModeView(discord.ui.View):
    def __init__(self, options: dict[str, str]) -> None:
        super().__init__(timeout=900)
        self.options = normalize_font_options(options)
        self.add_item(FontStyleSelect(self.options["unicodeStyle"]))

    @discord.ui.button(label="Style generated name", emoji="🏷️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:whole", row=1)
    async def whole_name(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_scope(interaction, "whole_name")

    @discord.ui.button(label="Text only — keep emoji", emoji="🔤", style=discord.ButtonStyle.primary, custom_id="dank_setup_font:text_only", row=1)
    async def text_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_scope(interaction, "text_only")

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.commands_ext import public_setup_solid as solid

        embed, view = await solid._build_main_setup_payload(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _set_scope(self, interaction: discord.Interaction, scope: str) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        current = await load_channel_font_options(int(interaction.guild.id))
        current["unicodeStyleScope"] = scope
        saved = await save_channel_font_options(int(interaction.guild.id), current)
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id)), view=ChannelFontModeView(saved))


class ChannelFontsButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(label="Channel Name Fonts", emoji="🔤", style=discord.ButtonStyle.primary, custom_id="dank_setup_tools:channel_fonts", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id)), view=ChannelFontModeView(options))


def apply() -> bool:
    _log("active; /dank setup exposes Channel Name Fonts controls")
    return True


apply()

__all__ = ["apply", "ChannelFontsButton", "load_channel_font_options", "normalize_font_options"]
