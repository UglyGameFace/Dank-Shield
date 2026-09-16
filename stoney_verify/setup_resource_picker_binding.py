from __future__ import annotations

"""Canonical `/dank setup` resource-picker binding.

The three public setup owners predate the shared Dank Shield resource browser and
still define Discord-native RoleSelect/ChannelSelect compatibility classes.
Those classes also contain the feature-specific validation/save behavior we do
not want to fork. This module binds the live class names to buttons that open the
shared cache-backed browser, then delegates the selected live object back through
the original owner callback.

This binding is installed explicitly by ``commands.py`` before split command
registration. It is not a startup guard, import hook, config writer, or second
business-logic owner.
"""

from types import SimpleNamespace
from typing import Any, Iterable

import discord

from .ui import DankGuildResourceBrowserView

_BOUND = False


def _custom_id(prefix: str, columns: Iterable[str], row: int) -> str:
    suffix = "_".join(str(value or "").strip() for value in columns if str(value or "").strip())
    suffix = suffix or str(int(row))
    return f"{prefix}:{suffix}"[:100]


def _resource_kinds(channel_types: Iterable[discord.ChannelType]) -> tuple[str, ...]:
    out: list[str] = []
    for channel_type in list(channel_types or []):
        kind = ""
        if channel_type == discord.ChannelType.category:
            kind = "category"
        elif channel_type in {discord.ChannelType.text, discord.ChannelType.news}:
            kind = "text"
        elif channel_type == discord.ChannelType.voice:
            kind = "voice"
        elif getattr(discord.ChannelType, "stage_voice", None) is not None and channel_type == discord.ChannelType.stage_voice:
            kind = "stage"
        elif getattr(discord.ChannelType, "forum", None) is not None and channel_type == discord.ChannelType.forum:
            kind = "forum"
        if kind and kind not in out:
            out.append(kind)
    return tuple(out) or ("channel",)


def _message_embed(interaction: discord.Interaction) -> discord.Embed | None:
    try:
        embeds = list(getattr(getattr(interaction, "message", None), "embeds", []) or [])
        return embeds[0] if embeds else None
    except Exception:
        return None


async def _restore_parent(
    interaction: discord.Interaction,
    *,
    parent_view: discord.ui.View | None,
    parent_embed: discord.Embed | None,
) -> None:
    kwargs: dict[str, Any] = {"view": parent_view}
    if parent_embed is not None:
        kwargs["embed"] = parent_embed
    await interaction.response.edit_message(**kwargs)


def _install_solid() -> None:
    from .commands_ext import public_setup_solid as solid

    legacy_role = solid.SaveRoleSelect
    legacy_channel = solid.SaveChannelSelect

    class SetupRolePickerButton(discord.ui.Button):
        def __init__(
            self,
            *,
            placeholder: str,
            columns: tuple[str, ...],
            require_manage: bool,
            also_same: tuple[str, ...] = (),
            row: int = 0,
        ) -> None:
            self.placeholder = str(placeholder or "Choose a role")
            self.columns = tuple(columns)
            self.also_same = tuple(also_same)
            self.require_manage = bool(require_manage)
            super().__init__(
                label=self.placeholder[:80],
                emoji="🎭",
                style=discord.ButtonStyle.primary,
                custom_id=_custom_id("dank_setup:role", self.columns + self.also_same, row),
                row=row,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, role: Any) -> None:
                adapter = SimpleNamespace(
                    columns=self.columns,
                    also_same=self.also_same,
                    require_manage=self.require_manage,
                    placeholder=self.placeholder,
                    values=[role],
                )
                await legacy_role.callback(adapter, pick_interaction)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=("role",),
                on_pick=on_pick,
                custom_id=_custom_id("dank_setup:role_browser", self.columns + self.also_same, int(self.row or 0)),
                title="🎭 Choose a Server Role",
                placeholder=self.placeholder,
                on_home=on_home,
                home_label="Back to Setup",
                empty_message="No server roles matched. Try Search with part of the role name or its Discord ID.",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    class SetupChannelPickerButton(discord.ui.Button):
        def __init__(
            self,
            *,
            placeholder: str,
            columns: tuple[str, ...],
            channel_types: list[discord.ChannelType],
            also_same: tuple[str, ...] = (),
            row: int = 0,
            require_category_manage: bool = False,
            require_text: bool = False,
            require_files: bool = False,
        ) -> None:
            self.placeholder = str(placeholder or "Choose a channel")
            self.columns = tuple(columns)
            self.channel_types = list(channel_types)
            self.also_same = tuple(also_same)
            self.require_category_manage = bool(require_category_manage)
            self.require_text = bool(require_text)
            self.require_files = bool(require_files)
            super().__init__(
                label=self.placeholder[:80],
                emoji="🧭",
                style=discord.ButtonStyle.primary,
                custom_id=_custom_id("dank_setup:channel", self.columns + self.also_same, row),
                row=row,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, channel: Any) -> None:
                adapter = SimpleNamespace(
                    columns=self.columns,
                    also_same=self.also_same,
                    require_category_manage=self.require_category_manage,
                    require_text=self.require_text,
                    require_files=self.require_files,
                    placeholder=self.placeholder,
                    values=[channel],
                )
                await legacy_channel.callback(adapter, pick_interaction)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=_resource_kinds(self.channel_types),
                on_pick=on_pick,
                custom_id=_custom_id("dank_setup:channel_browser", self.columns + self.also_same, int(self.row or 0)),
                title="🧭 Choose a Server Channel / Category",
                placeholder=self.placeholder,
                on_home=on_home,
                home_label="Back to Setup",
                empty_message="No matching server channels/categories were found. Try Search with part of the name or its Discord ID.",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    solid.SaveRoleSelect = SetupRolePickerButton
    solid.SaveChannelSelect = SetupChannelPickerButton


def _install_full_customization() -> None:
    from .commands_ext import public_setup_full_customization as full

    legacy_role = full.SaveRoleSelect
    legacy_channel = full.SaveChannelSelect

    class FullRolePickerButton(discord.ui.Button):
        def __init__(
            self,
            *,
            placeholder: str,
            columns: tuple[str, ...],
            also_same: tuple[str, ...] = (),
            require_manage: bool = True,
            row: int = 0,
        ) -> None:
            self.placeholder = str(placeholder or "Choose a role")
            self.columns = tuple(columns)
            self.also_same = tuple(also_same)
            self.require_manage = bool(require_manage)
            super().__init__(
                label=self.placeholder[:80],
                emoji="🎭",
                style=discord.ButtonStyle.primary,
                custom_id=_custom_id("dank_full:role", self.columns + self.also_same, row),
                row=row,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await full._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, role: Any) -> None:
                adapter = SimpleNamespace(
                    columns=self.columns,
                    also_same=self.also_same,
                    require_manage=self.require_manage,
                    placeholder=self.placeholder,
                    values=[role],
                )
                await legacy_role.callback(adapter, pick_interaction)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=("role",),
                on_pick=on_pick,
                custom_id=_custom_id("dank_full:role_browser", self.columns + self.also_same, int(self.row or 0)),
                title="🎭 Choose a Server Role",
                placeholder=self.placeholder,
                on_home=on_home,
                home_label="Back to Full Setup",
                empty_message="No server roles matched. Try Search with part of the role name or its Discord ID.",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    class FullChannelPickerButton(discord.ui.Button):
        def __init__(
            self,
            *,
            placeholder: str,
            columns: tuple[str, ...],
            channel_types: list[discord.ChannelType],
            also_same: tuple[str, ...] = (),
            row: int = 0,
            need_files: bool = False,
        ) -> None:
            self.placeholder = str(placeholder or "Choose a channel")
            self.columns = tuple(columns)
            self.channel_types = list(channel_types)
            self.also_same = tuple(also_same)
            self.need_files = bool(need_files)
            super().__init__(
                label=self.placeholder[:80],
                emoji="🧭",
                style=discord.ButtonStyle.primary,
                custom_id=_custom_id("dank_full:channel", self.columns + self.also_same, row),
                row=row,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await full._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, channel: Any) -> None:
                adapter = SimpleNamespace(
                    columns=self.columns,
                    also_same=self.also_same,
                    need_files=self.need_files,
                    placeholder=self.placeholder,
                    values=[channel],
                )
                await legacy_channel.callback(adapter, pick_interaction)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=_resource_kinds(self.channel_types),
                on_pick=on_pick,
                custom_id=_custom_id("dank_full:channel_browser", self.columns + self.also_same, int(self.row or 0)),
                title="🧭 Choose a Server Channel / Category",
                placeholder=self.placeholder,
                on_home=on_home,
                home_label="Back to Full Setup",
                empty_message="No matching server channels/categories were found. Try Search with part of the name or its Discord ID.",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    full.SaveRoleSelect = FullRolePickerButton
    full.SaveChannelSelect = FullChannelPickerButton


def _install_guided_recommend() -> None:
    from .commands_ext import public_setup_recommend as recommend

    class GuidedExistingRoleButton(discord.ui.Button):
        def __init__(self, *, requirement_key: str) -> None:
            self.requirement_key = str(requirement_key)
            super().__init__(
                label="Choose one I already have",
                emoji="🔎",
                style=discord.ButtonStyle.primary,
                custom_id=f"dank_setup_guided_existing_role:{self.requirement_key}"[:100],
                row=0,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await recommend.solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, role: Any) -> None:
                await recommend._guided_save_existing_item(pick_interaction, self.requirement_key, role)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=("role",),
                on_pick=on_pick,
                custom_id=f"dank_guided:role:{self.requirement_key}"[:80],
                title="🎭 Choose an Existing Role",
                placeholder="Choose one I already have",
                on_home=on_home,
                home_label="Back to Guided Setup",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    class GuidedExistingChannelButton(discord.ui.Button):
        def __init__(
            self,
            *,
            requirement_key: str,
            channel_type: discord.ChannelType,
        ) -> None:
            self.requirement_key = str(requirement_key)
            self.channel_type = channel_type
            super().__init__(
                label="Choose one I already have",
                emoji="🔎",
                style=discord.ButtonStyle.primary,
                custom_id=f"dank_setup_guided_existing_channel:{self.requirement_key}"[:100],
                row=0,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await recommend.solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            parent_view = self.view
            parent_embed = _message_embed(interaction)

            async def on_home(home_interaction: discord.Interaction) -> None:
                await _restore_parent(home_interaction, parent_view=parent_view, parent_embed=parent_embed)

            async def on_pick(pick_interaction: discord.Interaction, channel: Any) -> None:
                await recommend._guided_save_existing_item(pick_interaction, self.requirement_key, channel)

            browser = DankGuildResourceBrowserView(
                guild=guild,
                author_id=int(interaction.user.id),
                resource_kinds=_resource_kinds((self.channel_type,)),
                on_pick=on_pick,
                custom_id=f"dank_guided:channel:{self.requirement_key}"[:80],
                title="🧭 Choose an Existing Channel / Category",
                placeholder="Choose one I already have",
                on_home=on_home,
                home_label="Back to Guided Setup",
            )
            await interaction.response.edit_message(embed=browser.embed(), view=browser)

    recommend.GuidedExistingRoleSelect = GuidedExistingRoleButton
    recommend.GuidedExistingChannelSelect = GuidedExistingChannelButton


def apply_setup_resource_picker_binding() -> bool:
    """Bind all normal public setup entity choices to the shared Dank browser."""

    global _BOUND
    if _BOUND:
        return True
    try:
        _install_solid()
        _install_full_customization()
        _install_guided_recommend()
    except Exception as exc:
        try:
            print(f"❌ setup resource picker binding failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False
    _BOUND = True
    try:
        print("✅ setup resource picker binding active")
    except Exception:
        pass
    return True


__all__ = ["apply_setup_resource_picker_binding"]
