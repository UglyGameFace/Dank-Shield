from __future__ import annotations

"""Dedicated setup home for static welcome content and live join/exit cards."""

from typing import Any, Mapping, Optional

import discord

from .commands_ext.public_setup_group import _require_setup_permission
from .ui.current_channel import current_text_channel
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


_WELCOME_SECTIONS = {
    "static": ("Use This Channel", "Edit Welcome Text", "Preview Static Message", "Post / Update Static"),
    "join": ("Join Card Studio", "Preview Join Card"),
    "exit": ("Exit Card Studio", "Preview Exit Card"),
    "tools": ("Text Announcements", "Uploads & Advanced", "Lifecycle Health"),
}
_WELCOME_SECTION_LABELS = {
    "static": "Static Welcome",
    "join": "Join Card",
    "exit": "Exit Card",
    "tools": "Other Tools",
}


async def _welcome_embed(
    guild: discord.Guild, config: Any, *, section: str = "static",
) -> discord.Embed:
    """Compact on desktop and mobile; only the selected task is described."""
    section = section if section in _WELCOME_SECTIONS else "static"
    embed = discord.Embed(
        title=f"👋 Welcome & Exit • {_WELCOME_SECTION_LABELS[section]}",
        color=discord.Color.blurple(),
    )
    if section == "static":
        static_channel = welcome_channel_for(guild, config)
        embed.description = "Edit, preview, and post the static start-here message. Posting updates the existing panel when possible."
        embed.add_field(
            name="Static welcome",
            value=(
                f"Channel: {static_channel.mention if isinstance(static_channel, discord.TextChannel) else 'Not selected'}\n"
                f"Status: **{'Enabled' if bool(_value(config, 'welcome_message_enabled', False)) else 'Not posted/enabled'}**"
            ),
            inline=False,
        )
    elif section == "join":
        join_id = _cfg_int(config, "join_welcome_channel_id", "welcome_channel_id")
        channel = guild.get_channel(join_id)
        theme_key = configured_theme_key(config)
        embed.description = "Preview the live card members see when joining, or open Join Card Studio to change its design."
        embed.add_field(
            name="Live join card",
            value=(
                f"Status: **{'On' if _cfg_bool(config, 'welcome_card_enabled') else 'Off'}**\n"
                f"Channel: {channel.mention if isinstance(channel, discord.TextChannel) else 'Not selected'}\n"
                f"Style: **{getattr(BUILTIN_THEMES.get(theme_key), 'label', theme_key)}** • "
                f"{_font_label(config, configured_font_style_key(config))}"
            ),
            inline=False,
        )
    elif section == "exit":
        channel, reason = resolve_exit_card_channel(guild, config)
        theme_key = configured_exit_theme_key(config)
        embed.description = "Preview the leave card, or open Exit Card Studio for delivery, design and text settings."
        embed.add_field(
            name="Live exit card",
            value=(
                f"Status: **{'On' if exit_cards_enabled(config) else 'Off'}**\n"
                f"Channel: {channel.mention if isinstance(channel, discord.TextChannel) else 'Not selected'}\n"
                f"Style: **{getattr(BUILTIN_THEMES.get(theme_key), 'label', theme_key)}** • "
                f"{_font_label(config, configured_exit_font_style_key(config))}\n"
                f"Route: **{reason}**"
            ),
            inline=False,
        )
    else:
        embed.description = (
            "Configure optional join-text announcements, manage card artwork/uploads, "
            "or inspect lifecycle health. These are separate from member-facing card delivery."
        )
        embed.add_field(
            name="Join text",
            value=f"Status: **{'On' if _cfg_bool(config, 'welcome_join_enabled', 'join_welcome_enabled') else 'Off'}**",
            inline=False,
        )
    embed.set_footer(text="Choose a section • No changes occur until you use an action")
    return embed


class _WelcomeSectionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, section: str) -> None:
        self.owner_id = int(owner_id)
        super().__init__(
            placeholder="Choose welcome or exit settings",
            options=[
                discord.SelectOption(
                    label=label, value=key, default=section == key,
                )
                for key, label in _WELCOME_SECTION_LABELS.items()
            ],
            row=0, custom_id="dank:welcome:settings:section:v2",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _send_private(interaction, content="Open your own Welcome & Exit panel.")
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _send_private(interaction, content="Use this inside a server.")
        from .commands_ext import public_setup_solid as solid
        await solid._safe_defer_update(interaction)
        config = await get_guild_config(int(guild.id), refresh=True)
        section = self.values[0]
        await solid._edit_or_followup(
            interaction, embed=await _welcome_embed(guild, config, section=section),
            view=WelcomeSetupView(owner_id=self.owner_id, config=config, section=section),
        )


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
    def __init__(self, *, owner_id: int, config: Any, section: str = "static") -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.config = config
        self.section = section if section in _WELCOME_SECTIONS else "static"
        original = {str(getattr(item, "label", "")): item for item in self.children}
        self.clear_items()
        self.add_item(_WelcomeSectionSelect(self.owner_id, self.section))
        if self.section == "static":
            picker = WelcomeChannelSelect()
            picker.row = 1
            self.add_item(picker)
        actions = (*_WELCOME_SECTIONS[self.section], "Refresh", "Back to All Features", "Close")
        if self.section == "tools":
            actions += ("Setup Home",)
        for index, name in enumerate(actions):
            item = original[name]
            item.row = (2 if self.section == "static" else 1) + index // 4
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _send_private(
                interaction,
                content="❌ Only the manager who opened this setup can use it.",
            )
            return False
        return True

    @discord.ui.button(
        label="Use This Channel", emoji="📍",
        style=discord.ButtonStyle.success, row=3,
    )
    async def use_this_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await self.interaction_check(interaction):
            return
        if not await _require_setup_permission(interaction):
            return
        channel = current_text_channel(interaction)
        if channel is None:
            return await _send_private(
                interaction,
                content="❌ Open Welcome Setup in the text channel you want "
                        "or use the channel picker to choose another.",
            )
        await save_welcome_channel(interaction, channel)

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

        from .commands_ext import public_setup_solid as solid

        await solid._safe_defer_update(interaction)
        config = await get_guild_config(guild.id, refresh=True)
        await solid._edit_or_followup(
            interaction,
            embed=await _welcome_embed(guild, config, section=self.section),
            view=WelcomeSetupView(owner_id=self.owner_id, config=config, section=self.section),
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
