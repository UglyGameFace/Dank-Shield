from __future__ import annotations

"""Discord setup UI for Channel Builder font defaults."""

from typing import Any

import discord

try:
    from stoney_verify.startup_guards import channel_builder_full_font_catalog_guard as _font_catalog_guard

    _font_catalog_guard.apply()
except Exception:
    pass

try:
    from stoney_verify.startup_guards import channel_font_exact_unicode_guard as _exact_unicode_guard

    _exact_unicode_guard.apply()
except Exception:
    pass

_CONFIG_KEY = "channel_builder_style_options"
_RUNTIME_FONT_OPTIONS: dict[str, dict[str, str]] = {}

_STYLE_LABELS: dict[str, str] = {
    "normal": "Normal",
    "bold_sans": "Bold Sans",
    "italic_sans": "Italic Sans",
    "bold_italic_sans": "Bold Italic Sans",
    "monospace": "Monospace",
    "fullwidth": "Fullwidth",
    "serif_bold": "Serif Bold",
    "serif_italic": "Serif Italic",
    "serif_bold_italic": "Serif Bold Italic",
    "script": "Script",
    "bold_script": "Bold Script",
    "fraktur": "Fraktur / Gothic",
    "bold_fraktur": "Bold Fraktur",
    "circled": "Circled",
    "parenthesized": "Parenthesized",
    "small_caps": "Small Caps",
    "upside_down": "Upside Down",
}
_RISKY_STYLES = {"script", "bold_script", "fraktur", "bold_fraktur", "circled", "parenthesized", "upside_down"}
_SCOPE_LABELS: dict[str, str] = {
    "whole_name": "Style generated name",
    "text_only": "Text only — keep emoji",
}
_STYLE_EXAMPLES: dict[str, tuple[str, str]] = {
    "normal": ("gaming-clips", "🔥・general-chat"),
    "bold_sans": ("𝗴𝗮𝗺𝗶𝗻𝗴-𝗰𝗹𝗶𝗽𝘀", "🔥・𝗴𝗲𝗻𝗲𝗿𝗮𝗹-𝗰𝗵𝗮𝘁"),
    "italic_sans": ("𝘨𝘢𝘮𝘪𝘯𝘨-𝘤𝘭𝘪𝘱𝘴", "🔥・𝘨𝘦𝘯𝘦𝘳𝘢𝘭-𝘤𝘩𝘢𝘵"),
    "bold_italic_sans": ("𝙜𝙖𝙢𝙞𝙣𝙜-𝙘𝙡𝙞𝙥𝙨", "🔥・𝙜𝙚𝙣𝙚𝙧𝙖𝙡-𝙘𝙝𝙖𝙩"),
    "monospace": ("𝚐𝚊𝚖𝚒𝚗𝚐-𝚌𝚕𝚒𝚙𝚜", "🔥・𝚐𝚎𝚗𝚎𝚛𝚊𝚕-𝚌𝚑𝚊𝚝"),
    "fullwidth": ("ｇａｍｉｎｇ－ｃｌｉｐｓ", "🔥・ｇｅｎｅｒａｌ－ｃｈａｔ"),
    "serif_bold": ("𝐠𝐚𝐦𝐢𝐧𝐠-𝐜𝐥𝐢𝐩𝐬", "🔥・𝐠𝐞𝐧𝐞𝐫𝐚𝐥-𝐜𝐡𝐚𝐭"),
    "serif_italic": ("𝑔𝑎𝑚𝑖𝑛𝑔-𝑐𝑙𝑖𝑝𝑠", "🔥・𝑔𝑒𝑛𝑒𝑟𝑎𝑙-𝑐ℎ𝑎𝑡"),
    "serif_bold_italic": ("𝒈𝒂𝒎𝒊𝒏𝒈-𝒄𝒍𝒊𝒑𝒔", "🔥・𝒈𝒆𝒏𝒆𝒓𝒂𝒍-𝒄𝒉𝒂𝒕"),
    "script": ("𝑔𝒶𝓂𝒾𝓃𝑔-𝒸𝓁𝒾𝓅𝓈", "🔥・𝑔𝑒𝓃𝑒𝓇𝒶𝓁-𝒸𝒽𝒶𝓉"),
    "bold_script": ("𝓰𝓪𝓶𝓲𝓷𝓰-𝓬𝓵𝓲𝓹𝓼", "🔥・𝓰𝓮𝓷𝓮𝓻𝓪𝓵-𝓬𝓱𝓪𝓽"),
    "fraktur": ("𝔤𝔞𝔪𝔦𝔫𝔤-𝔠𝔩𝔦𝔭𝔰", "🔥・𝔤𝔢𝔫𝔢𝔯𝔞𝔩-𝔠𝔥𝔞𝔱"),
    "bold_fraktur": ("𝖌𝖆𝖒𝖎𝖓𝖌-𝖈𝖑𝖎𝖕𝖘", "🔥・𝖌𝖊𝖓𝖊𝖗𝖆𝖑-𝖈𝖍𝖆𝖙"),
    "circled": ("ⓖⓐⓜⓘⓝⓖ-ⓒⓛⓘⓟⓢ", "🔥・ⓖⓔⓝⓔⓡⓐⓛ-ⓒⓗⓐⓣ"),
    "parenthesized": ("⒢⒜⒨⒤⒩⒢-⒞⒧⒤⒫⒮", "🔥・⒢⒠⒩⒠⒭⒜⒧-⒞⒣⒜⒯"),
    "small_caps": ("ɢᴀᴍɪɴɢ-ᴄʟɪᴘꜱ", "🔥・ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ"),
    "upside_down": ("ƃuᴉɯɐƃ-sdᴉlɔ", "🔥・ʇɐɥɔ-lɐɹǝuǝƃ"),
}


def _log(message: str) -> None:
    try:
        print(f"🔤 setup_channel_font_mode_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_channel_font_mode_guard {message}")
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


def _cache_key(guild_id: int) -> str:
    try:
        return str(int(guild_id))
    except Exception:
        return "0"


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
    cached = _RUNTIME_FONT_OPTIONS.get(_cache_key(guild_id))
    if cached:
        return normalize_font_options(cached)
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=True)
        stored = cfg.get(_CONFIG_KEY) if isinstance(cfg, dict) else None
        loaded = normalize_font_options(stored) if isinstance(stored, dict) else normalize_font_options(
            {
                "unicodeStyle": cfg.get("channel_builder_unicode_style") if isinstance(cfg, dict) else None,
                "unicodeStyleScope": cfg.get("channel_builder_unicode_style_scope") if isinstance(cfg, dict) else None,
            }
        )
        if loaded != normalize_font_options({}):
            _RUNTIME_FONT_OPTIONS[_cache_key(guild_id)] = loaded
        return loaded
    except Exception as exc:
        _warn(f"load failed guild={guild_id}: {exc!r}")
        return normalize_font_options({})


async def save_channel_font_options(guild_id: int, options: dict[str, str]) -> dict[str, str]:
    clean = normalize_font_options(options)
    _RUNTIME_FONT_OPTIONS[_cache_key(guild_id)] = dict(clean)
    try:
        from stoney_verify.guild_config import clear_guild_config_cache, upsert_guild_config

        cfg = await upsert_guild_config(
            guild_id,
            {
                _CONFIG_KEY: clean,
                "channel_builder_unicode_style": clean["unicodeStyle"],
                "channel_builder_unicode_style_scope": clean["unicodeStyleScope"],
            },
        )
        clear_guild_config_cache(guild_id)
        try:
            source = str(cfg.get("source") or "") if isinstance(cfg, dict) else ""
            if source.startswith("env_fallback") or source.startswith("unconfigured"):
                _warn(f"DB save unavailable guild={guild_id}; using runtime font options until restart")
        except Exception:
            pass
    except Exception as exc:
        _warn(f"save failed guild={guild_id}; using runtime font options until restart: {exc!r}")
    return clean


async def _require_setup(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


def _example(options: dict[str, str]) -> str:
    style = options.get("unicodeStyle") or "normal"
    scope = options.get("unicodeStyleScope") or "whole_name"
    whole, text_only = _STYLE_EXAMPLES.get(style, _STYLE_EXAMPLES["normal"])
    return text_only if scope == "text_only" else f"🎮・{whole}"


def _preview_lines(options: dict[str, str]) -> list[str]:
    current = options.get("unicodeStyle") or "normal"
    scope = options.get("unicodeStyleScope") or "whole_name"
    lines: list[str] = []
    for style, label in _STYLE_LABELS.items():
        whole, text_only = _STYLE_EXAMPLES.get(style, _STYLE_EXAMPLES["normal"])
        preview = text_only if scope == "text_only" else f"🎮・{whole}"
        marker = "✅" if style == current else "▫️"
        risk = " ⚠️" if style in _RISKY_STYLES else ""
        lines.append(f"{marker} **{label}**{risk}: `{preview}`")
    return lines


def _preview_fields(embed: discord.Embed, options: dict[str, str]) -> None:
    lines = _preview_lines(options)
    chunks = [lines[:6], lines[6:12], lines[12:]]
    for index, chunk in enumerate(chunks, start=1):
        if chunk:
            embed.add_field(name=f"Preview Gallery {index}", value="\n".join(chunk)[:1024], inline=False)


async def build_channel_font_embed(guild_id: int, *, saved_message: str | None = None, options_override: dict[str, str] | None = None) -> discord.Embed:
    options = normalize_font_options(options_override) if options_override else await load_channel_font_options(guild_id)
    embed = discord.Embed(
        title="🔤 Channel Name Fonts",
        description=(
            "Pick by looking at the preview gallery below — no need to test fonts one by one.\n\n"
            "**Text only — keep emoji** preserves existing emojis/decorations and changes only the words."
        ),
        color=discord.Color.blurple(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message, inline=False)
    embed.add_field(name="Selected font", value=_STYLE_LABELS.get(options["unicodeStyle"], "Normal"), inline=True)
    embed.add_field(name="Apply mode", value=_SCOPE_LABELS.get(options["unicodeStyleScope"], "Style generated name"), inline=True)
    embed.add_field(name="Current example", value=f"`{_example(options)}`", inline=False)
    embed.add_field(name="Note", value="⚠️ means decorative/high-risk for readability, search, or screen readers. Use readable styles for important channels.", inline=False)
    _preview_fields(embed, options)
    embed.set_footer(text="Path: /dank setup → More Options → Channel Name Fonts")
    return embed


class FontStyleSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(
                label=(f"⚠️ {label}" if value in _RISKY_STYLES else label),
                value=value,
                description=("Decorative/high-risk" if value in _RISKY_STYLES else "Readable/recommended" if value != "normal" else "Plain Discord text"),
                default=value == current,
            )
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
        label = _STYLE_LABELS.get(saved["unicodeStyle"], saved["unicodeStyle"])
        await interaction.response.edit_message(
            embed=await build_channel_font_embed(int(interaction.guild.id), saved_message=f"Font style saved as **{label}**.", options_override=saved),
            view=ChannelFontModeView(saved),
        )


class ChannelFontModeView(discord.ui.View):
    def __init__(self, options: dict[str, str]) -> None:
        super().__init__(timeout=900)
        self.options = normalize_font_options(options)
        self.add_item(FontStyleSelect(self.options["unicodeStyle"]))
        self._mark_scope_buttons()

    def _mark_scope_buttons(self) -> None:
        current = self.options.get("unicodeStyleScope") or "whole_name"
        for child in self.children:
            cid = str(getattr(child, "custom_id", "") or "")
            if cid.endswith(":whole"):
                child.label = ("✅ Style generated name" if current == "whole_name" else "Style generated name")
                child.style = discord.ButtonStyle.primary if current == "whole_name" else discord.ButtonStyle.secondary
            if cid.endswith(":text_only"):
                child.label = ("✅ Text only — keep emoji" if current == "text_only" else "Text only — keep emoji")
                child.style = discord.ButtonStyle.primary if current == "text_only" else discord.ButtonStyle.secondary

    @discord.ui.button(label="Style generated name", emoji="🏷️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:whole", row=1)
    async def whole_name(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_scope(interaction, "whole_name")

    @discord.ui.button(label="Text only — keep emoji", emoji="🔤", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:text_only", row=1)
    async def text_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_scope(interaction, "text_only")

    @discord.ui.button(label="Refresh Preview", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))

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
        await interaction.response.edit_message(
            embed=await build_channel_font_embed(int(interaction.guild.id), saved_message=f"Apply mode saved as **{_SCOPE_LABELS.get(scope, scope)}**.", options_override=saved),
            view=ChannelFontModeView(saved),
        )


class ChannelFontsButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(label="Channel Name Fonts", emoji="🔤", style=discord.ButtonStyle.primary, custom_id="dank_setup_tools:channel_fonts", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))


def apply() -> bool:
    _log("active; /dank setup exposes Channel Name Fonts preview gallery")
    return True


apply()

__all__ = ["apply", "ChannelFontsButton", "load_channel_font_options", "normalize_font_options"]
