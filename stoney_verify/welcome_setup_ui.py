from __future__ import annotations

"""Dedicated setup home for static welcomes, join cards, and join/leave logs.

This area is intentionally separate from member profile signatures.
"""

from typing import Any, Mapping, Optional

import discord

from .commands_ext.public_setup_group import _require_setup_permission
from .commands_ext.public_welcome_group import (
    _cfg_bool,
    _cfg_int,
    open_join_leave_announcements,
    open_welcome_health,
    open_welcome_preview,
    post_welcome_message,
    save_welcome_channel,
    save_welcome_template_service,
    welcome_card_preview,
)
from .commands_ext.public_welcome_card_studio import welcome_card_style
from .guild_config import get_guild_config
from .welcome_card_service import (
    configured_color_mode,
    configured_custom_font,
    configured_font_style_key,
    configured_shuffle_mode,
    configured_theme_key,
)
from .welcome_card_typography_engine import BUILTIN_THEMES, FONT_STYLES
from .welcome_message import welcome_channel_for


def _value(config: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(config, Mapping):
            return config.get(key, default)
        value = getattr(config, key, default)
        return default if value is None else value
    except Exception:
        return default


async def _send_private(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload = {
        "content": content,
        "embed": embed,
        "view": view,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _welcome_embed(guild: discord.Guild, config: Any) -> discord.Embed:
    channel = welcome_channel_for(guild, config)
    theme_key = configured_theme_key(config)
    theme = BUILTIN_THEMES.get(theme_key)
    font_key = configured_font_style_key(config)
    custom_font, custom_font_name = configured_custom_font(config)
    font_label = (
        custom_font_name
        if custom_font and custom_font_name
        else getattr(FONT_STYLES.get(font_key), "label", font_key.replace("_", " ").title())
    )
    join_cards = _cfg_bool(config, "welcome_card_enabled")
    join_enabled = _cfg_bool(config, "welcome_join_enabled", "join_welcome_enabled")
    leave_enabled = _cfg_bool(config, "welcome_leave_enabled", "goodbye_enabled", "leave_message_enabled")
    join_channel_id = _cfg_int(config, "join_welcome_channel_id", "welcome_channel_id")
    leave_channel_id = _cfg_int(config, "goodbye_channel_id", "leave_channel_id", "welcome_channel_id")
    join_channel = guild.get_channel(join_channel_id)
    leave_channel = guild.get_channel(leave_channel_id)

    embed = discord.Embed(
        title="👋 Welcome & Join",
        description=(
            "Everything a new member sees is managed here. This area controls the static welcome/start-here message, "
            "the image sent when someone joins, and separate join/leave announcements. It never changes profile signatures."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="📌 Static welcome/start-here message",
        value=(
            f"**Channel:** {channel.mention if isinstance(channel, discord.TextChannel) else 'Not selected'}\n"
            f"**Message:** {'Enabled' if bool(_value(config, 'welcome_message_enabled', False)) else 'Not posted/enabled'}\n"
            "Use the channel picker, edit the text, preview it, then post/update without duplicates."
        ),
        inline=False,
    )
    embed.add_field(
        name="🖼️ Join-only image card",
        value=(
            f"**Status:** {'On' if join_cards else 'Off'}\n"
            f"**Theme:** {getattr(theme, 'label', theme_key)}\n"
            f"**Font:** {font_label}\n"
            f"**Colors:** {configured_color_mode(config).replace('_', ' ').title()}\n"
            f"**Shuffle:** {configured_shuffle_mode(config).replace('_', ' ').title()}"
        ),
        inline=True,
    )
    embed.add_field(
        name="📣 Join & leave announcements",
        value=(
            f"**Join:** {'On' if join_enabled else 'Off'}"
            + (f" in {join_channel.mention}" if isinstance(join_channel, discord.TextChannel) else "")
            + f"\n**Leave:** {'On' if leave_enabled else 'Off'}"
            + (f" in {leave_channel.mention}" if isinstance(leave_channel, discord.TextChannel) else "")
        ),
        inline=True,
    )
    embed.add_field(
        name="Simple order",
        value=(
            "1. Pick the static welcome channel.\n"
            "2. Edit and preview the static message.\n"
            "3. Style and preview the join card.\n"
            "4. Turn join/leave announcements on only where wanted."
        ),
        inline=False,
    )
    embed.set_footer(text="Welcome & Join • separate from Profile Signatures")
    return embed


class WelcomeTemplateModal(discord.ui.Modal):
    def __init__(self, *, author_id: int, config: Any) -> None:
        super().__init__(title="Edit Static Welcome Message", timeout=900)
        self.author_id = int(author_id)
        self.title_input = discord.ui.TextInput(
            label="Welcome title",
            default=str(_value(config, "welcome_message_title", "") or "")[:256],
            placeholder="Welcome to {server_name}!",
            max_length=256,
            required=False,
        )
        self.body_input = discord.ui.TextInput(
            label="Welcome message",
            default=str(_value(config, "welcome_message_body", "") or "")[:1800],
            placeholder="Read {rules}, then visit {verify}. Need help? Use {support}.",
            max_length=1800,
            required=False,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _send_private(interaction, content="❌ Only the person who opened this editor can submit it.")
        await save_welcome_template_service(
            interaction,
            title=str(self.title_input.value or "").strip() or None,
            body=str(self.body_input.value or "").strip() or None,
        )


class WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose the static welcome/start-here channel…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank_setup_welcome:channel",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, WelcomeSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel = guild.get_channel(int(selected.id)) if guild is not None and selected is not None else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_private(interaction, content="❌ Choose a text channel from this server.")
        await save_welcome_channel(interaction, channel)


class WelcomeSetupView(discord.ui.View):
    def __init__(self, *, owner_id: int, config: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.config = config
        self.add_item(WelcomeChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _send_private(interaction, content="❌ Only the manager who opened this setup can use it.")
            return False
        return True

    @discord.ui.button(label="Edit Welcome Text", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            WelcomeTemplateModal(author_id=self.owner_id, config=self.config)
        )

    @discord.ui.button(label="Preview Static Message", emoji="👀", style=discord.ButtonStyle.secondary, row=1)
    async def preview_static(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_welcome_preview(interaction)

    @discord.ui.button(label="Post / Update Static", emoji="📌", style=discord.ButtonStyle.success, row=1)
    async def post_static(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await post_welcome_message(interaction)

    @discord.ui.button(label="Join Card Studio", emoji="🪄", style=discord.ButtonStyle.primary, row=2)
    async def card_studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await welcome_card_style(interaction)

    @discord.ui.button(label="Preview Join Card", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2)
    async def preview_card(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await welcome_card_preview(interaction)

    @discord.ui.button(label="Join & Leave Announcements", emoji="📣", style=discord.ButtonStyle.primary, row=2)
    async def events(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_join_leave_announcements(interaction)

    @discord.ui.button(label="Uploads & Advanced", emoji="📎", style=discord.ButtonStyle.secondary, row=3)
    async def uploads(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        embed = discord.Embed(
            title="📎 Join Card Uploads & Advanced Tools",
            description=(
                "Discord buttons cannot open a file picker, so uploads use the two attachment commands below. "
                "Everything else stays inside the button-first Join Card Studio."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Custom background",
            value="Use `/dank welcome card-upload` and attach a safe 3:1 image.",
            inline=False,
        )
        embed.add_field(
            name="Custom font",
            value="Use `/dank welcome card-font-upload` and attach a font you are allowed to use.",
            inline=False,
        )
        embed.add_field(
            name="Remove uploaded font",
            value="Use `/dank welcome card-font-clear`.",
            inline=False,
        )
        await _send_private(interaction, embed=embed)

    @discord.ui.button(label="Welcome Health", emoji="🩺", style=discord.ButtonStyle.secondary, row=3)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_welcome_health(interaction)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _send_private(interaction, content="❌ Use this inside a server.")
        config = await get_guild_config(guild.id, refresh=True)
        await interaction.response.edit_message(
            embed=await _welcome_embed(guild, config),
            view=WelcomeSetupView(owner_id=self.owner_id, config=config),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_setup_recommend import _open_advanced_settings

        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=4)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_setup_recommend import _home_edit

        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Welcome & Join setup closed.", embed=None, view=None)


async def open_welcome_setup(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_private(interaction, content="❌ Use this inside a server.")
    if not interaction.response.is_done():
        await interaction.response.defer()
    config = await get_guild_config(guild.id, refresh=True)
    payload = {
        "embed": await _welcome_embed(guild, config),
        "view": WelcomeSetupView(owner_id=interaction.user.id, config=config),
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    try:
        await interaction.edit_original_response(**payload)
    except Exception:
        await interaction.followup.send(**payload, ephemeral=True)


__all__ = ["WelcomeSetupView", "open_welcome_setup"]
