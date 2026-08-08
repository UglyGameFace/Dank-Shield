from __future__ import annotations

"""Button-first configuration surface for the canonical Exit Card runtime."""

from typing import Any, Mapping, Optional

import discord

from .commands_ext.public_setup_group import _require_setup_permission, _upsert_config
from .exit_card_runtime import build_exit_card_embed, resolve_exit_card_channel
from .exit_card_service import (
    configured_exit_color_mode,
    configured_exit_custom_colors,
    configured_exit_font_style_key,
    configured_exit_shuffle_mode,
    configured_exit_theme_key,
    decode_exit_custom_background,
    exit_card_file,
    exit_cards_enabled,
)
from .guild_config import get_guild_config, invalidate_guild_config
from .welcome_card_service import configured_custom_font
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_MODES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_hex_color,
    normalize_theme_key,
)

SHUFFLE_LABELS = {
    "off": "Off",
    "fonts": "Shuffle Fonts",
    "themes": "Shuffle Themes",
    "fonts_themes": "Shuffle Fonts + Themes",
    "everything": "Shuffle Everything",
}


def _value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(cfg, Mapping):
            if key in cfg:
                return cfg.get(key)
            for bucket in ("settings", "config", "metadata", "meta"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and key in nested:
                    return nested.get(key)
        value = getattr(cfg, key, None)
        return default if value is None else value
    except Exception:
        return default


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
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _fresh_cfg(guild_id: int) -> Any:
    invalidate_guild_config(int(guild_id))
    return await get_guild_config(int(guild_id), refresh=True)


async def _save(
    interaction: discord.Interaction,
    updates: Mapping[str, Any],
) -> Any:
    if interaction.guild is None:
        raise RuntimeError("This must be used inside a server.")
    await _upsert_config(int(interaction.guild.id), dict(updates))
    return await _fresh_cfg(int(interaction.guild.id))


def _font_label(cfg: Any) -> str:
    key = configured_exit_font_style_key(cfg)
    if key == CUSTOM_FONT_STYLE_KEY:
        custom, name = configured_custom_font(cfg)
        if custom:
            return name or "Uploaded Font"
    style = FONT_STYLES.get(key)
    return style.label if style is not None else key.replace("_", " ").title()


def _background_label(cfg: Any) -> str:
    if str(_value(cfg, "exit_card_background_b64", "") or "").strip():
        return "Custom Exit artwork"
    mode = str(_value(cfg, "exit_card_background_mode", "") or "").strip().lower()
    if mode == "builtin":
        return "Built-in theme"
    if decode_exit_custom_background(cfg):
        return "Inherited Welcome artwork"
    return "Built-in theme"


def _template_text(cfg: Any) -> tuple[str, str]:
    title = str(
        _value(cfg, "exit_card_title", None)
        or _value(cfg, "welcome_leave_title", None)
        or "{display_name} left"
    ).strip()
    body = str(
        _value(cfg, "exit_card_body", None)
        or _value(cfg, "welcome_leave_body", None)
        or "Thanks for being part of {server_name}. Members now: {member_count}."
    ).strip()
    return title, body


def _studio_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    channel, reason = resolve_exit_card_channel(guild, cfg)
    theme_key = configured_exit_theme_key(cfg)
    theme = BUILTIN_THEMES.get(theme_key)
    primary, secondary = configured_exit_custom_colors(cfg)
    color_mode = configured_exit_color_mode(cfg)
    title, body = _template_text(cfg)
    enabled = exit_cards_enabled(cfg)

    embed = discord.Embed(
        title="🚪 Exit Card Studio",
        description=(
            "This is the **authoritative live leave-card setup**. It controls the "
            "one member-facing card sent when someone leaves. Staff audit logs stay separate."
        ),
        color=discord.Color.orange() if enabled else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Live delivery",
        value=(
            f"**Status:** {'✅ ON' if enabled else '⚪ OFF'}\n"
            f"**Channel:** {channel.mention if isinstance(channel, discord.TextChannel) else 'not set'}\n"
            f"**Route:** {reason}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Design",
        value=(
            f"**Theme:** {getattr(theme, 'label', theme_key)}\n"
            f"**Font:** {_font_label(cfg)}\n"
            f"**Colors:** {color_mode.replace('_', ' ').title()}"
            + (f" `{primary}` → `{secondary}`" if color_mode == "custom" and primary and secondary else "")
            + f"\n**Background:** {_background_label(cfg)}\n"
            f"**Shuffle:** {SHUFFLE_LABELS.get(configured_exit_shuffle_mode(cfg), 'Off')}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Exit message",
        value=f"**Title:** {title[:180]}\n**Body:** {body[:500]}",
        inline=False,
    )
    embed.add_field(
        name="Placeholders",
        value=(
            "`{username}` `{display_name}` `{server_name}` `{member_count}` "
            "`{account_age}` `{joined_at}` `{rules_channel}` `{verify_channel}` "
            "`{support_channel}`. Token names are case-insensitive and tolerate spaces."
        ),
        inline=False,
    )
    embed.set_footer(text="Exit Card Studio • dank_shield:exit_card_runtime:v1")
    return embed


async def send_exit_studio_preview(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(interaction, content="❌ Use this inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    try:
        card = await exit_card_file(interaction.user, cfg)
        embed = build_exit_card_embed(interaction.user, cfg)
        embed.set_image(url=f"attachment://{card.filename}")
        embed.set_footer(text="Preview only • dank_shield:exit_card_runtime:v1")
        await _private(
            interaction,
            content="Preview only — nothing was posted publicly.",
            embed=embed,
            file=card,
        )
    except Exception as exc:
        embed = build_exit_card_embed(interaction.user, cfg)
        embed.set_footer(text="Preview fallback • image renderer needs attention")
        await _private(
            interaction,
            content=f"⚠️ Image preview failed: `{type(exc).__name__}: {exc}`. Text fallback preview is below.",
            embed=embed,
        )


async def _refresh(
    interaction: discord.Interaction,
    *,
    notice: str = "",
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, content="❌ Use this inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = _studio_embed(guild, cfg)
    if notice:
        embed.add_field(name="Last action", value=notice[:1024], inline=False)
    view = ExitCardStudioView(owner_id=int(interaction.user.id), cfg=cfg)
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=embed, view=view)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
    except Exception:
        await _private(interaction, embed=embed, view=view)


class _OwnedPicker(discord.ui.View):
    def __init__(self, *, owner_id: int, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _private(interaction, content="❌ Open your own Exit Card Studio to use this picker.")
            return False
        return await _require_setup_permission(interaction)


class ExitCardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose the exact live exit-card channel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:exit:studio:channel:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ExitCardStudioView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel = guild.get_channel(int(selected.id)) if guild is not None and selected is not None else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, content="❌ Choose a text channel from this server.")
        me = guild.me
        if isinstance(me, discord.Member):
            perms = channel.permissions_for(me)
            missing = [
                name
                for name, allowed in {
                    "View Channel": perms.view_channel,
                    "Send Messages": perms.send_messages,
                    "Embed Links": perms.embed_links,
                    "Read Message History": perms.read_message_history,
                }.items()
                if not allowed
            ]
            if missing:
                return await _private(
                    interaction,
                    content=f"❌ I cannot use {channel.mention}. Missing: {', '.join(missing)}.",
                )
        await _defer(interaction)
        await _save(
            interaction,
            {
                "exit_card_channel_id": str(channel.id),
                "exit_card_enabled": True,
                "goodbye_channel_id": str(channel.id),
                "leave_channel_id": str(channel.id),
                "welcome_leave_enabled": True,
                "goodbye_enabled": True,
                "leave_message_enabled": True,
            },
        )
        await _refresh(interaction, notice=f"✅ Exit cards are ON in {channel.mention}.")


class ExitTextModal(discord.ui.Modal):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(title="Edit Exit Card Message", timeout=900)
        self.owner_id = int(owner_id)
        title, body = _template_text(cfg)
        self.title_input = discord.ui.TextInput(
            label="Exit title",
            default=title[:256],
            placeholder="{display_name} left",
            max_length=256,
            required=True,
        )
        self.body_input = discord.ui.TextInput(
            label="Exit body",
            default=body[:1800],
            placeholder="Thanks for being part of {server_name}. Members now: {member_count}.",
            max_length=1800,
            required=True,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, content="❌ Only the manager who opened this editor can submit it.")
        if not await _require_setup_permission(interaction):
            return
        await _defer(interaction)
        title = str(self.title_input.value or "").strip()[:256]
        body = str(self.body_input.value or "").strip()[:1800]
        await _save(
            interaction,
            {
                "exit_card_title": title,
                "exit_card_body": body,
                # Keep the old announcement editor and new runtime in sync.
                "welcome_leave_title": title,
                "welcome_leave_body": body,
            },
        )
        await _refresh(interaction, notice="✅ Exit-card message text saved.")


class ThemeSelect(discord.ui.Select):
    def __init__(self, *, cfg: Any) -> None:
        current = configured_exit_theme_key(cfg)
        options = [
            discord.SelectOption(
                label=theme.label[:100],
                value=theme.key,
                description=theme.description[:100],
                default=theme.key == current,
            )
            for theme in BUILTIN_THEMES.values()
        ]
        super().__init__(placeholder="Choose Exit Card theme…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        theme_key = normalize_theme_key((self.values or [""])[0])
        await _defer(interaction)
        await _save(
            interaction,
            {
                "exit_card_theme": theme_key,
                "exit_card_background_mode": "builtin",
                "exit_card_background_b64": "",
                "exit_card_background_type": "",
                "exit_card_background_name": "",
            },
        )
        await _private(interaction, content=f"✅ Exit theme set to **{BUILTIN_THEMES[theme_key].label}**.")


class ThemePicker(_OwnedPicker):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.add_item(ThemeSelect(cfg=cfg))


class FontSelect(discord.ui.Select):
    def __init__(self, *, cfg: Any) -> None:
        current = configured_exit_font_style_key(cfg)
        custom, custom_name = configured_custom_font(cfg)
        options = [
            discord.SelectOption(
                label=style.label[:100],
                value=style.key,
                description=style.description[:100],
                default=style.key == current,
            )
            for style in FONT_STYLES.values()
        ]
        if custom:
            options.append(
                discord.SelectOption(
                    label=f"Uploaded: {custom_name or 'Custom Font'}"[:100],
                    value=CUSTOM_FONT_STYLE_KEY,
                    description="Use the licensed font uploaded through Welcome Card tools.",
                    default=current == CUSTOM_FONT_STYLE_KEY,
                )
            )
        super().__init__(placeholder="Choose Exit Card font…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = normalize_font_style_key((self.values or [""])[0])
        await _defer(interaction)
        await _save(interaction, {"exit_card_font_style": key})
        await _private(interaction, content=f"✅ Exit-card font set to **{_font_label({'exit_card_font_style': key}) if key != CUSTOM_FONT_STYLE_KEY else 'Uploaded Font'}**.")


class FontPicker(_OwnedPicker):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.add_item(FontSelect(cfg=cfg))


class ExitAdvancedColorsModal(discord.ui.Modal):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(title="Advanced Exit Colors", timeout=900)
        self.owner_id = int(owner_id)
        primary, secondary = configured_exit_custom_colors(cfg)
        self.primary = discord.ui.TextInput(label="Primary hex", default=primary or "#22DCFF", max_length=7)
        self.secondary = discord.ui.TextInput(label="Secondary hex", default=secondary or "#BC42FF", max_length=7)
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, content="❌ Only the manager who opened this editor can submit it.")
        if not await _require_setup_permission(interaction):
            return
        try:
            primary = normalize_hex_color(str(self.primary.value))
            secondary = normalize_hex_color(str(self.secondary.value))
        except ValueError as exc:
            return await _private(interaction, content=f"❌ {exc}")
        await _defer(interaction)
        await _save(
            interaction,
            {
                "exit_card_color_mode": "custom",
                "exit_card_custom_primary": primary,
                "exit_card_custom_secondary": secondary,
            },
        )
        await _private(interaction, content=f"✅ Exit colors saved: `{primary}` → `{secondary}`.")


class ColorSelect(discord.ui.Select):
    def __init__(self, *, cfg: Any) -> None:
        current = configured_exit_color_mode(cfg)
        options = [
            discord.SelectOption(
                label=label,
                value=f"mode:{key}",
                description={
                    "auto": "Use profile/card/theme fallbacks intelligently.",
                    "profile": "Match the departing member's profile visuals.",
                    "card": "Match the active exit-card background.",
                    "theme": "Always use the selected theme palette.",
                }.get(key, "Use this color-resolution mode."),
                default=key == current,
            )
            for key, label in COLOR_MODES.items()
        ]
        options.extend(
            discord.SelectOption(
                label=preset.label[:100],
                value=f"preset:{preset.key}",
                description=preset.description[:100],
                emoji=preset.emoji,
            )
            for preset in COLOR_PRESETS.values()
        )
        options.append(
            discord.SelectOption(
                label="Advanced Hex Colors",
                value="advanced",
                description="Enter two exact #RRGGBB colors.",
            )
        )
        super().__init__(placeholder="Choose Exit Card colors…", options=options[:25], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        value = (self.values or [""])[0]
        if value == "advanced":
            cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
            return await interaction.response.send_modal(ExitAdvancedColorsModal(owner_id=int(interaction.user.id), cfg=cfg))
        await _defer(interaction)
        if value.startswith("mode:"):
            mode = normalize_color_mode(value.split(":", 1)[1])
            await _save(
                interaction,
                {
                    "exit_card_color_mode": mode,
                    "exit_card_custom_primary": "",
                    "exit_card_custom_secondary": "",
                },
            )
            return await _private(interaction, content=f"✅ Exit colors set to **{COLOR_MODES[mode]}**.")
        if value.startswith("preset:"):
            preset = COLOR_PRESETS.get(value.split(":", 1)[1])
            if preset is None:
                return await _private(interaction, content="❌ That palette is no longer available.")
            await _save(
                interaction,
                {
                    "exit_card_color_mode": "custom",
                    "exit_card_custom_primary": preset.primary,
                    "exit_card_custom_secondary": preset.secondary,
                },
            )
            return await _private(interaction, content=f"✅ Exit palette set to **{preset.label}**.")


class ColorPicker(_OwnedPicker):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.add_item(ColorSelect(cfg=cfg))


class ShuffleSelect(discord.ui.Select):
    def __init__(self, *, cfg: Any) -> None:
        current = configured_exit_shuffle_mode(cfg)
        options = [
            discord.SelectOption(label=label, value=key, default=key == current)
            for key, label in SHUFFLE_LABELS.items()
        ]
        super().__init__(placeholder="Choose Exit Card shuffle mode…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        mode = (self.values or ["off"])[0]
        await _defer(interaction)
        await _save(interaction, {"exit_card_shuffle_mode": mode})
        await _private(interaction, content=f"✅ Exit-card shuffle set to **{SHUFFLE_LABELS.get(mode, 'Off')}**.")


class ShufflePicker(_OwnedPicker):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(owner_id=owner_id)
        self.add_item(ShuffleSelect(cfg=cfg))


class ExitCardStudioView(discord.ui.View):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.cfg = cfg
        self.add_item(ExitCardChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _private(interaction, content="❌ Open your own Exit Card Studio to use these controls.")
            return False
        return await _require_setup_permission(interaction)

    @discord.ui.button(label="Enable / Disable", emoji="🔌", style=discord.ButtonStyle.success, row=1)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        current = exit_cards_enabled(await get_guild_config(int(interaction.guild.id), refresh=True)) if interaction.guild else False
        await _defer(interaction)
        enabled = not current
        await _save(
            interaction,
            {
                "exit_card_enabled": enabled,
                "welcome_leave_enabled": enabled,
                "goodbye_enabled": enabled,
                "leave_message_enabled": enabled,
            },
        )
        await _refresh(interaction, notice=f"✅ Exit cards {'enabled' if enabled else 'disabled'}.")

    @discord.ui.button(label="Edit Text", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
        await interaction.response.send_modal(ExitTextModal(owner_id=self.owner_id, cfg=cfg))

    @discord.ui.button(label="Theme", emoji="🖼️", style=discord.ButtonStyle.primary, row=1)
    async def theme(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
        await _private(interaction, content="Choose the live Exit Card theme.", view=ThemePicker(owner_id=self.owner_id, cfg=cfg))

    @discord.ui.button(label="Font", emoji="🔤", style=discord.ButtonStyle.secondary, row=1)
    async def font(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
        await _private(interaction, content="Choose the Exit Card font. Uploaded fonts are shared safely with Welcome Studio.", view=FontPicker(owner_id=self.owner_id, cfg=cfg))

    @discord.ui.button(label="Colors", emoji="🎨", style=discord.ButtonStyle.secondary, row=2)
    async def colors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
        await _private(interaction, content="Choose how Exit Card colors are resolved.", view=ColorPicker(owner_id=self.owner_id, cfg=cfg))

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True) if interaction.guild else {}
        await _private(interaction, content="Choose deterministic Exit Card shuffle behavior.", view=ShufflePicker(owner_id=self.owner_id, cfg=cfg))

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.primary, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await send_exit_studio_preview(interaction)

    @discord.ui.button(label="Clear Artwork", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def clear_art(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer(interaction)
        await _save(
            interaction,
            {
                "exit_card_background_mode": "builtin",
                "exit_card_background_b64": "",
                "exit_card_background_type": "",
                "exit_card_background_name": "",
            },
        )
        await _refresh(interaction, notice="✅ Custom Exit artwork cleared. Built-in theme background is active.")

    @discord.ui.button(label="Reset Design", emoji="♻️", style=discord.ButtonStyle.secondary, row=2)
    async def reset_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer(interaction)
        await _save(
            interaction,
            {
                "exit_card_theme": "classic",
                "exit_card_font_style": "neon",
                "exit_card_color_mode": "auto",
                "exit_card_custom_primary": "",
                "exit_card_custom_secondary": "",
                "exit_card_shuffle_mode": "off",
                "exit_card_background_mode": "builtin",
                "exit_card_background_b64": "",
                "exit_card_background_type": "",
                "exit_card_background_name": "",
            },
        )
        await _refresh(interaction, notice="✅ Exit design reset. Channel, enabled state, and message text were preserved.")

    @discord.ui.button(label="Uploads", emoji="📎", style=discord.ButtonStyle.secondary, row=3)
    async def uploads(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        embed = discord.Embed(
            title="📎 Exit Card Uploads",
            description="Discord buttons cannot open an attachment picker, so uploads use the compact slash commands.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Exit background", value="Use `/dank welcome exit-card-upload` with a PNG, JPG, or WEBP image.", inline=False)
        embed.add_field(name="Shared custom font", value="Use `/dank welcome card-font-upload`, then choose **Uploaded Font** inside Exit Card Studio.", inline=False)
        await _private(interaction, embed=embed)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _defer(interaction)
        await _refresh(interaction, notice="🔄 Refreshed.")

    @discord.ui.button(label="Welcome & Join", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Exit Card Studio closed.", embed=None, view=None)


async def open_exit_card_studio(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, content="❌ Use this inside a server.")
    await _defer(interaction)
    try:
        cfg = await get_guild_config(int(guild.id), refresh=True)
        await _private(
            interaction,
            embed=_studio_embed(guild, cfg),
            view=ExitCardStudioView(owner_id=int(interaction.user.id), cfg=cfg),
        )
    except Exception as exc:
        await _private(interaction, content=f"❌ Could not open Exit Card Studio: `{type(exc).__name__}: {exc}`")


__all__ = [
    "ExitCardStudioView",
    "open_exit_card_studio",
    "send_exit_studio_preview",
]
