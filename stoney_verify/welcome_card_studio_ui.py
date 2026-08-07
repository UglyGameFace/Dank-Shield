from __future__ import annotations

"""Canonical button-first Welcome Card Studio.

The panel remains usable when the current card cannot render. Writes use fresh
per-guild config and acknowledge the interaction before database or image work.
"""

from typing import Any, Mapping, Optional

import discord

from .commands_ext.public_setup_group import _require_setup_permission, _upsert_config
from .guild_config import get_guild_config, invalidate_guild_config
from .ui.picker import DankPickerView, make_choice
from .welcome_card_service import (
    configured_color_mode,
    configured_custom_font,
    configured_font_style_key,
    configured_shuffle_mode,
    configured_theme_key,
    welcome_card_file,
    welcome_cards_enabled,
)
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_theme_key,
)


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


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


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


async def _preview_file(
    member: discord.Member,
    cfg: Any,
) -> tuple[Optional[discord.File], str]:
    try:
        return await welcome_card_file(member, cfg), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _channel_for(
    guild: discord.Guild,
    cfg: Any,
) -> Optional[discord.TextChannel]:
    dedicated = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None), 0)
    fallback = _safe_int(_cfg_value(cfg, "welcome_channel_id", None), 0)
    channel = guild.get_channel(dedicated or fallback)
    return channel if isinstance(channel, discord.TextChannel) else None


def _font_label(cfg: Any) -> str:
    key = configured_font_style_key(cfg)
    custom_font, custom_name = configured_custom_font(cfg)
    if custom_font and custom_name and key == CUSTOM_FONT_STYLE_KEY:
        return custom_name
    style = FONT_STYLES.get(key)
    return getattr(style, "label", key.replace("_", " ").title())


def _studio_embed(
    guild: discord.Guild,
    cfg: Any,
    *,
    preview_error: str = "",
) -> discord.Embed:
    theme_key = configured_theme_key(cfg)
    theme = BUILTIN_THEMES.get(theme_key)
    channel = _channel_for(guild, cfg)
    embed = discord.Embed(
        title="🪄 Welcome Card Studio",
        description=(
            "This panel is the authoritative live join-card setup. Changes are "
            "read fresh when a member joins; the retired legacy v3 card is never used."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Live status",
        value=(
            f"**Card:** {'Enabled' if welcome_cards_enabled(cfg) else 'Disabled'}\n"
            f"**Channel:** {channel.mention if channel else 'Not configured'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Current design",
        value=(
            f"**Theme:** {getattr(theme, 'label', theme_key)}\n"
            f"**Font:** {_font_label(cfg)}\n"
            f"**Colors:** {configured_color_mode(cfg).replace('_', ' ').title()}\n"
            f"**Shuffle:** {configured_shuffle_mode(cfg).replace('_', ' ').title()}"
        ),
        inline=True,
    )
    embed.add_field(
        name="What posts live",
        value=(
            "The image uses the selected theme, font, colors, background, shuffle, "
            "and saved join text. Missing **Attach Files** uses one matching embed "
            "fallback instead of an unrelated legacy card."
        ),
        inline=False,
    )
    if preview_error:
        embed.add_field(
            name="⚠️ Preview needs repair",
            value=(
                f"`{preview_error[:850]}`\nThe Studio is still usable. Change the "
                "theme, font, or background, then preview again."
            ),
            inline=False,
        )
    embed.set_footer(text="Welcome Card Studio • canonical live runtime")
    return embed


class WelcomeCardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose the live join-card channel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:welcome:studio:join_channel:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, WelcomeCardStudioView):
            return
        if not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel = guild.get_channel(int(selected.id)) if guild and selected else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(
                interaction,
                content="❌ Choose a text channel from this server.",
            )
        await _defer(interaction)
        try:
            cfg = await _save(
                interaction,
                {
                    "join_welcome_channel_id": str(channel.id),
                    "welcome_card_enabled": True,
                },
            )
        except Exception as exc:
            return await _private(
                interaction,
                content=f"❌ Could not save the join-card channel: `{type(exc).__name__}: {exc}`",
            )
        await _private(
            interaction,
            content=f"✅ Live join-card channel saved as {channel.mention} and cards enabled.",
            embed=_studio_embed(guild, cfg),
            view=WelcomeCardStudioView(owner_id=view.owner_id),
        )


class WelcomeCardStudioView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.add_item(WelcomeCardChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(
            interaction,
            content="❌ Open your own Welcome Card Studio to use these controls.",
        )
        return False

    @discord.ui.button(
        label="Enable / Disable",
        emoji="🔌",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def toggle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, content="❌ Use this inside a server.")
        await _defer(interaction)
        try:
            cfg = await get_guild_config(int(guild.id), refresh=True)
            enabled = not welcome_cards_enabled(cfg)
            fresh = await _save(
                interaction,
                {"welcome_card_enabled": enabled},
            )
        except Exception as exc:
            return await _private(
                interaction,
                content=f"❌ Could not change card status: `{type(exc).__name__}: {exc}`",
            )
        await _private(
            interaction,
            content=f"✅ Live welcome cards are now **{'enabled' if enabled else 'disabled'}**.",
            embed=_studio_embed(guild, fresh),
            view=WelcomeCardStudioView(owner_id=self.owner_id),
        )

    @discord.ui.button(
        label="Theme",
        emoji="🖼️",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def theme(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_theme_picker(interaction)

    @discord.ui.button(
        label="Font",
        emoji="🔤",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def font(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext.public_welcome_card_studio import welcome_card_font

        await welcome_card_font(interaction)

    @discord.ui.button(
        label="Colors",
        emoji="🎨",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def colors(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext.public_welcome_card_studio import welcome_card_colors

        await welcome_card_colors(interaction)

    @discord.ui.button(
        label="Shuffle",
        emoji="🔀",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def shuffle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext.public_welcome_card_studio import welcome_card_shuffle

        await welcome_card_shuffle(interaction)

    @discord.ui.button(
        label="Preview",
        emoji="👁️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def preview(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await send_studio_preview(interaction)

    @discord.ui.button(
        label="Uploads",
        emoji="📎",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def uploads(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        embed = discord.Embed(
            title="📎 Welcome Card Uploads",
            description=(
                "Discord buttons cannot open an attachment picker, so uploads "
                "remain slash commands while every other control stays here."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Custom background",
            value="Use `/dank welcome card-upload` with a PNG, JPG, or WEBP image.",
            inline=False,
        )
        embed.add_field(
            name="Custom font",
            value="Use `/dank welcome card-font-upload` with a licensed TTF/OTF/TTC/OTC/WOFF/WOFF2 file.",
            inline=False,
        )
        embed.add_field(
            name="Remove custom font",
            value="Use `/dank welcome card-font-clear`.",
            inline=False,
        )
        await _private(interaction, embed=embed)

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_welcome_card_studio(interaction)

    @discord.ui.button(
        label="Welcome & Join",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .welcome_setup_ui import open_welcome_setup

        await open_welcome_setup(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        row=4,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Welcome Card Studio closed.",
            embed=None,
            view=None,
        )


async def open_theme_picker(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _private(interaction, content="❌ Use this inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    current = configured_theme_key(cfg)

    async def on_pick(
        component_interaction: discord.Interaction,
        value: str,
    ) -> None:
        key = normalize_theme_key(value)
        await _defer(component_interaction)
        try:
            fresh = await _save(
                component_interaction,
                {
                    "welcome_card_enabled": True,
                    "welcome_card_theme": key,
                    "welcome_card_background_b64": "",
                    "welcome_card_background_type": "",
                    "welcome_card_background_name": "",
                },
            )
        except Exception as exc:
            return await _private(
                component_interaction,
                content=f"❌ Could not save theme: `{type(exc).__name__}: {exc}`",
            )
        file, preview_error = await _preview_file(
            component_interaction.user,
            fresh,
        )
        await _private(
            component_interaction,
            content=f"✅ Theme set to **{BUILTIN_THEMES[key].label}** and cards enabled.",
            embed=_studio_embed(guild, fresh, preview_error=preview_error),
            view=WelcomeCardStudioView(
                owner_id=int(component_interaction.user.id)
            ),
            file=file,
        )

    await _private(
        interaction,
        content=(
            "## 🖼️ Welcome Card Themes\nChoosing a built-in theme clears an "
            "uploaded background so the selected theme is actually live."
        ),
        view=DankPickerView(
            author_id=int(interaction.user.id),
            choices=[
                make_choice(
                    theme.label,
                    theme.key,
                    description=getattr(
                        theme,
                        "description",
                        "Built-in welcome-card theme.",
                    ),
                    emoji="🖼️",
                    default=theme.key == current,
                )
                for theme in BUILTIN_THEMES.values()
            ],
            on_pick=on_pick,
            custom_id=f"dank:welcome:theme:studio:v1:{guild.id}",
            placeholder="Choose the live welcome-card theme…",
            title="Welcome Card Themes",
        ),
    )


async def send_studio_preview(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(interaction, content="❌ Use this inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    file, error = await _preview_file(interaction.user, cfg)
    if file is None:
        return await _private(
            interaction,
            content=f"❌ Preview failed, but settings were not changed: `{error}`",
            embed=_studio_embed(guild, cfg, preview_error=error),
            view=WelcomeCardStudioView(owner_id=int(interaction.user.id)),
        )
    await _private(
        interaction,
        content="✅ This is the current production join-card design.",
        embed=_studio_embed(guild, cfg),
        file=file,
        view=WelcomeCardStudioView(owner_id=int(interaction.user.id)),
    )


async def open_welcome_card_studio(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return await _private(interaction, content="❌ Use this inside a server.")
    await _defer(interaction)
    try:
        cfg = await get_guild_config(int(guild.id), refresh=True)
        file, preview_error = await _preview_file(interaction.user, cfg)
        await _private(
            interaction,
            embed=_studio_embed(guild, cfg, preview_error=preview_error),
            view=WelcomeCardStudioView(owner_id=int(interaction.user.id)),
            file=file,
        )
    except Exception as exc:
        await _private(
            interaction,
            content=f"❌ Could not open Welcome Card Studio: `{type(exc).__name__}: {exc}`",
        )


__all__ = [
    "WelcomeCardStudioView",
    "open_theme_picker",
    "open_welcome_card_studio",
    "send_studio_preview",
]
