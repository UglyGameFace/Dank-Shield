from __future__ import annotations

"""Canonical Fix Access UI on the shared Dank Shield resource browser.

Fix Access keeps its mutation/audit rules in permission_repair_core, but
role/channel/category discovery, paging, Search-Safe lookup, and search UX are
owned by the shared DankGuildResourceBrowserView. Discord's native ChannelSelect
is intentionally not used.
"""

from typing import Any, Mapping, Optional, Sequence

import discord

from . import permission_repair_core as core
from .ui.resource_browser import DankGuildResourceBrowserView


class TargetSearchModal(discord.ui.Modal, title="Search Dank Shield Targets"):
    """Compatibility/auth boundary delegating matching to the shared browser."""

    query = discord.ui.TextInput(
        label="Name, previous name, ID, or mention",
        placeholder="Example: modlog, tickets, 123456789…",
        required=False,
        max_length=100,
    )

    def __init__(
        self,
        *,
        state: core.PermissionRepairState,
        current_query: str = "",
    ) -> None:
        super().__init__()
        self.state = state
        if current_query:
            self.query.default = current_query[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Server owner or Manage Server, Manage Channels, or Administrator authority is required.",
            )

        browser = TargetChannelPickerView(
            self.state,
            actor=interaction.user,
        )
        view = await browser.search(str(self.query.value or ""))
        kwargs = {
            "embed": view.embed(),
            "view": view,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.message is not None:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(
                **kwargs,
                ephemeral=True,
            )


async def _safe_ephemeral(interaction: discord.Interaction, message: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


def _actor_is_privileged(guild: discord.Guild, actor: Any) -> bool:
    try:
        if int(getattr(actor, "id", 0) or 0) == int(getattr(guild, "owner_id", 0) or 0):
            return True
    except Exception:
        pass
    try:
        return bool(getattr(getattr(actor, "guild_permissions", None), "administrator", False))
    except Exception:
        return False


def _actor_can_see(channel: Any, guild: discord.Guild, actor: Any) -> bool:
    if _actor_is_privileged(guild, actor):
        return True
    try:
        permissions = channel.permissions_for(actor)
        return bool(
            getattr(permissions, "view_channel", False)
            or getattr(permissions, "manage_channels", False)
        )
    except Exception:
        return False


class TargetChannelPickerView(DankGuildResourceBrowserView):
    """Fix Access specialization of the canonical shared resource browser."""

    def __init__(
        self,
        state: core.PermissionRepairState,
        *,
        actor: Any,
        query: str = "",
        page: int = 0,
        alias_index: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        self.state = state
        self.actor = actor

        def allowed_target(resource: Any) -> bool:
            return bool(
                core._target_supported(resource)
                and _actor_can_see(resource, state.guild, actor)
            )

        async def picked_target(
            interaction: discord.Interaction,
            resource: Any,
        ) -> None:
            await self.pick_target(interaction, resource)

        async def back(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                embed=core.build_preview_embed(self.state),
                view=TargetPermissionRepairView(self.state),
            )

        super().__init__(
            guild=state.guild,
            author_id=int(state.actor_id),
            resource_kinds=("channel",),
            on_pick=picked_target,
            custom_id="dank_permission_repair:target",
            title="🔎 Dank Shield Target Picker",
            placeholder="Choose a channel or category…",
            query=query,
            page=page,
            predicate=allowed_target,
            alias_index=alias_index,
            on_home=back,
            home_label="Back to Fix Access",
            empty_message=(
                "No visible repair targets matched. Use 🔎 Search with the current/styled name, "
                "a saved previous name, Discord ID, or mention."
            ),
        )

    def search_modal(self) -> discord.ui.Modal:
        return TargetSearchModal(
            state=self.state,
            current_query=self.query,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if not core._actor_can_manage(interaction):
            await _safe_ephemeral(
                interaction,
                "❌ Server owner or Manage Server, Manage Channels, or Administrator authority is required.",
            )
            return False
        return True

    def clone(
        self,
        *,
        query: Optional[str] = None,
        page: Optional[int] = None,
        alias_index: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> "TargetChannelPickerView":
        return TargetChannelPickerView(
            self.state,
            actor=self.actor,
            query=self.query if query is None else query,
            page=self.page if page is None else page,
            alias_index=self.alias_index if alias_index is None else alias_index,
        )

    def embed(self) -> discord.Embed:
        embed = super().embed()
        embed.title = "🔎 Dank Shield Target Picker"
        if self.state.target is not None:
            embed.add_field(
                name="Current target",
                value=core._target_label(self.state.target),
                inline=True,
            )
        return embed

    async def pick_target(
        self,
        interaction: discord.Interaction,
        target: Any,
    ) -> None:
        if int(interaction.user.id) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Server owner or Manage Server, Manage Channels, or Administrator authority is required.",
            )
        if not core._target_supported(target):
            return await _safe_ephemeral(
                interaction,
                "❌ That channel/category no longer exists or cannot be repaired.",
            )
        if not _actor_can_see(target, self.state.guild, interaction.user):
            return await _safe_ephemeral(
                interaction,
                "❌ You no longer have access to that target.",
            )

        self.state.target = target
        if not isinstance(target, discord.CategoryChannel):
            self.state.include_children = False

        await interaction.response.edit_message(
            embed=core.build_preview_embed(self.state),
            view=TargetPermissionRepairView(self.state),
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        _ = item
        error_id = ""
        try:
            from .interaction_guard import log_interaction_failure

            record = log_interaction_failure(
                interaction,
                error,
                stage="access_repair_picker_callback_failed",
                action_name="specific_access_repair_picker",
                fix_hint="Reopen Repair Bot Access and choose the target again.",
            )
            error_id = record.error_id
        except Exception:
            pass
        try:
            print(
                "[permission_repair_ui] target picker callback failed "
                f"error_id={error_id or '-'} "
                f"{type(error).__name__}: {error}"
            )
        except Exception:
            pass
        suffix = f" Error ID: `{error_id}`." if error_id else ""
        await _safe_ephemeral(
            interaction,
            "⚠️ Dank Shield could not finish that picker action. "
            "Reopen **Repair Bot Access** and try again." + suffix,
        )


class _TargetBrowserButton(discord.ui.Button):
    def __init__(self, state: core.PermissionRepairState) -> None:
        self.state = state
        selected = core._target_name(state.target) if state.target is not None else ""
        label = f"Change Target: {selected}" if selected else "Choose Channel / Category"
        super().__init__(
            label=label[:80],
            emoji="🔎",
            style=discord.ButtonStyle.primary,
            custom_id="dank_permission_repair:target",
            row=0,
        )

    @property
    def placeholder(self) -> str:
        # Compatibility for the existing UI-flow assertion while this item is now
        # a button that opens the dedicated browser rather than a native selector.
        return "Choose a channel or category…"

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This Fix Access screen belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Server owner or Manage Server, Manage Channels, or Administrator authority is required.",
            )

        browser = TargetChannelPickerView(
            self.state,
            actor=interaction.user,
            page=0,
        )
        await interaction.response.edit_message(
            embed=browser.embed(),
            view=browser,
        )


class TargetPermissionRepairView(core.TargetPermissionRepairView):
    """Fix Access view backed by the shared Dank Shield resource browser."""

    def __init__(self, state: core.PermissionRepairState) -> None:
        super().__init__(state)
        for child in list(self.children):
            if str(getattr(child, "custom_id", "") or "") == "dank_permission_repair:target":
                self.remove_item(child)
        self.add_item(_TargetBrowserButton(state))

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        _ = item
        error_id = ""
        try:
            from .interaction_guard import log_interaction_failure

            record = log_interaction_failure(
                interaction,
                error,
                stage="access_repair_callback_failed",
                action_name="specific_access_repair",
                fix_hint=(
                    "Reopen Repair Bot Access and preview the target before retrying. "
                    "Do not assume a failed render means Discord made no change."
                ),
            )
            error_id = record.error_id
        except Exception:
            pass
        try:
            print(
                "[permission_repair_ui] Fix Access callback failed "
                f"error_id={error_id or '-'} "
                f"{type(error).__name__}: {error}"
            )
        except Exception:
            pass
        suffix = f" Error ID: `{error_id}`." if error_id else ""
        await _safe_ephemeral(
            interaction,
            "⚠️ Fix Access could not finish that interaction. "
            "Reopen **Repair Bot Access** and preview the target before retrying. "
            "Do not assume the target is unchanged until the preview confirms it."
            + suffix,
        )


async def open_target_permission_repair(interaction: discord.Interaction) -> None:
    if interaction.guild is None or int(getattr(interaction.user, "id", 0) or 0) <= 0:
        return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
    if not core._actor_can_manage(interaction):
        return await _safe_ephemeral(
            interaction,
            "❌ Server owner or Manage Server, Manage Channels, or Administrator authority is required.",
        )

    state = core.PermissionRepairState(
        guild=interaction.guild,
        actor_id=int(interaction.user.id),
    )
    embed = core.build_preview_embed(state)
    view = TargetPermissionRepairView(state)

    try:
        if getattr(interaction, "message", None) is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=view)
            return
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception as error:
        try:
            print(
                "[permission_repair_ui] failed to open Fix Access: "
                f"{type(error).__name__}: {error}"
            )
        except Exception:
            pass
        await _safe_ephemeral(
            interaction,
            "⚠️ Fix Access could not open its target browser. Nothing was changed. "
            "Retry **Fix Access**.",
        )


# The old implementation lives in the internal core module so mutation/audit
# behavior remains byte-for-byte unchanged. Bind its UI return points to this
# shared surface so inherited callbacks never fall back to the native picker.
core.TargetPermissionRepairView = TargetPermissionRepairView
core.open_target_permission_repair = open_target_permission_repair


__all__ = [
    "TargetChannelPickerView",
    "TargetPermissionRepairView",
    "TargetSearchModal",
    "open_target_permission_repair",
]
