from __future__ import annotations

"""Dedicated setup home for static welcome content and live join/exit cards."""

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
from .exit_card_runtime import resolve_exit_card_channel
from .exit_card_service import (
    configured_exit_color_mode,
    configured_exit_font_style_key,
    configured_exit_shuffle_mode,
    configured_exit_theme_key,
    exit_cards_enabled,
)
from .exit_card_studio_ui import open_exit_card_studio, send_exit_studio_preview
from .guild_config import get_guild_config
from .welcome_card_service import (
    configured_color_mode,
    configured_custom_font,
    configured_font_style_key,
    configured_shuffle_mode,
    configured_theme_key,
)
from .welcome_card_studio_ui import open_welcome_card_studio
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


def _font_label(config: Any, key: str) -> str:
    custom_font, custom_name = configured_custom_font(config)
    if key == "custom" and custom_font:
        return custom_name or "Uploaded Font"
    return getattr(
        FONT_STYLES.get(key),
        "label",
        key.replace("_", " ").title(),
    )


async def _send_private(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
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
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _welcome_embed(guild: discord.Guild, config: Any) -> discord.Embed:
    static_channel = welcome_channel_for(guild, config)

    join_theme_key = configured_theme_key(config)
    join_theme = BUILTIN_THEMES.get(join_theme_key)
    join_font_key = configured_font_style_key(config)
    join_cards = _cfg_bool(config, "welcome_card_enabled")
    join_channel_id = _cfg_int(
        config,
        "join_welcome_channel_id",
        "welcome_channel_id",
    )
    join_channel = guild.get_channel(join_channel_id)

    exit_enabled = exit_cards_enabled(config)
    exit_channel, exit_reason = resolve_exit_card_channel(guild, config)
    exit_theme_key = configured_exit_theme_key(config)
    exit_theme = BUILTIN_THEMES.get(exit_theme_key)
    exit_font_key = configured_exit_font_style_key(config)

    join_text_enabled = _cfg_bool(
        config,
        "welcome_join_enabled",
        "join_welcome_enabled",
    )

    embed = discord.Embed(
        title="👋 Welcome, Join & Exit",
        description=(
            "Static start-here content, the live Welcome Card, and the live Exit "
            "Card each have one clear owner. Staff audit/modlog events remain separate."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="📌 Static welcome/start-here message",
        value=(
            f"**Channel:** {static_channel.mention if isinstance(static_channel, discord.TextChannel) else 'Not selected'}\n"
            f"**Message:** {'Enabled' if bool(_value(config, 'welcome_message_enabled', False)) else 'Not posted/enabled'}\n"
            "Use the channel picker, edit the text, preview it, then post/update without duplicates."
        ),
        inline=False,
    )
    embed.add_field(
        name="🖼️ Canonical live Welcome Card",
        value=(
            f"**Status:** {'On' if join_cards else 'Off'}\n"
            f"**Channel:** {join_channel.mention if isinstance(join_channel, discord.TextChannel) else 'Not selected'}\n"
            f"**Theme:** {getattr(join_theme, 'label', join_theme_key)}\n"
            f"**Font:** {_font_label(config, join_font_key)}\n"
            f"**Colors:** {configured_color_mode(config).replace('_', ' ').title()}\n"
            f"**Shuffle:** {configured_shuffle_mode(config).replace('_', ' ').title()}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🚪 Canonical live Exit Card",
        value=(
            f"**Status:** {'On' if exit_enabled else 'Off'}\n"
            f"**Channel:** {exit_channel.mention if isinstance(exit_channel, discord.TextChannel) else 'Not selected'}\n"
            f"**Theme:** {getattr(exit_theme, 'label', exit_theme_key)}\n"
            f"**Font:** {_font_label(config, exit_font_key)}\n"
            f"**Colors:** {configured_exit_color_mode(config).replace('_', ' ').title()}\n"
            f"**Shuffle:** {configured_exit_shuffle_mode(config).replace('_', ' ').title()}\n"
            f"**Route:** {exit_reason}"
        ),
        inline=True,
    )
    embed.add_field(
        name="📣 Compatibility text announcement",
        value=(
            f"**Separate join text:** {'On' if join_text_enabled else 'Off'}"
            + (
                f" in {join_channel.mention}"
                if isinstance(join_channel, discord.TextChannel)
                else ""
            )
            + "\nThe old leave-event sender is retired; Exit Card Studio owns public leaves."
        ),
        inline=False,
    )
    embed.add_field(
        name="Simple order",
        value=(
            "1. Set your static welcome/start-here message if you use one.\n"
            "2. Open **Join Card Studio** for live joins.\n"
            "3. Open **Exit Card Studio** for live leaves.\n"
            "4. Preview each live card before relying on it."
        ),
        inline=False,
    )
    embed.set_footer(text="Lifecycle setup • one canonical join sender • one canonical exit sender")
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
            return await _send_private(
                interaction,
                content="❌ Only the person who opened this editor can submit it.",
            )
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
        if not isinstance(view, WelcomeSetupView) or not await view.interaction_check(
            interaction
        ):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel = (
            guild.get_channel(int(selected.id))
            if guild is not None and selected is not None
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            return await _send_private(
                interaction,
                content="❌ Choose a text channel from this server.",
            )
        await save_welcome_channel(interaction, channel)


class WelcomeSetupView(discord.ui.View):
    def __init__(self, *, owner_id: int, config: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.config = config
        self.add_item(WelcomeChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _send_private(
                interaction,
                content="❌ Only the manager who opened this setup can use it.",
            )
            return False
        return True

    @discord.ui.button(
        label="Edit Welcome Text",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def edit_text(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(
            WelcomeTemplateModal(author_id=self.owner_id, config=self.config)
        )

    @discord.ui.button(
        label="Preview Static Message",
        emoji="👀",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def preview_static(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_welcome_preview(interaction)

    @discord.ui.button(
        label="Post / Update Static",
        emoji="📌",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def post_static(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await post_welcome_message(interaction)

    @discord.ui.button(
        label="Join Card Studio",
        emoji="🪄",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def card_studio(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_welcome_card_studio(interaction)

    @discord.ui.button(
        label="Preview Join Card",
        emoji="🖼️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def preview_card(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await welcome_card_preview(interaction)

    @discord.ui.button(
        label="Exit Card Studio",
        emoji="🚪",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def exit_studio(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_exit_card_studio(interaction)

    @discord.ui.button(
        label="Preview Exit Card",
        emoji="👋",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def preview_exit(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await send_exit_studio_preview(interaction)

    @discord.ui.button(
        label="Text Announcements",
        emoji="📣",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def events(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_join_leave_announcements(interaction)

    @discord.ui.button(
        label="Uploads & Advanced",
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
            title="📎 Lifecycle Card Uploads & Advanced Tools",
            description=(
                "Discord buttons cannot open a file picker, so artwork/font uploads "
                "use the attachment commands below."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Join background",
            value="Use `/dank welcome card-upload` and attach a safe 3:1 image.",
            inline=False,
        )
        embed.add_field(
            name="Exit background",
            value="Use `/dank welcome exit-card-upload` and attach a safe 3:1 image.",
            inline=False,
        )
        embed.add_field(
            name="Shared custom font",
            value=(
                "Use `/dank welcome card-font-upload`, then choose **Uploaded Font** "
                "inside either Studio. Use `/dank welcome card-font-clear` to remove it."
            ),
            inline=False,
        )
        await _send_private(interaction, embed=embed)

    @discord.ui.button(
        label="Lifecycle Health",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def health(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_welcome_health(interaction)

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
        guild = interaction.guild
        if guild is None:
            return await _send_private(
                interaction,
                content="❌ Use this inside a server.",
            )
        config = await get_guild_config(guild.id, refresh=True)
        await interaction.response.edit_message(
            embed=await _welcome_embed(guild, config),
            view=WelcomeSetupView(owner_id=self.owner_id, config=config),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext.public_setup_recommend import _open_advanced_settings

        await _open_advanced_settings(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from .commands_ext.public_setup_recommend import _home_edit

        await _home_edit(interaction)

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
            content="Welcome, Join & Exit setup closed.",
            embed=None,
            view=None,
        )


async def open_welcome_setup(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_private(
            interaction,
            content="❌ Use this inside a server.",
        )
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        config = await get_guild_config(guild.id, refresh=True)
        await interaction.followup.send(
            embed=await _welcome_embed(guild, config),
            view=WelcomeSetupView(owner_id=interaction.user.id, config=config),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not open Welcome, Join & Exit: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


__all__ = ["WelcomeSetupView", "open_welcome_setup"]
