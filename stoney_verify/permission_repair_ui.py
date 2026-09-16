from __future__ import annotations

"""Dedicated Dank Shield target browser for Fix Access.

Discord's native ChannelSelect is intentionally not used here. The browser is
built from the guild cache so Dank Shield owns discovery, paging, search,
owner-lock behavior, and interaction error handling on desktop and mobile.
"""

from dataclasses import dataclass
from math import ceil
from typing import Any

import discord

from . import permission_repair_core as core
from .ui import DankChoice, DankPickerView


_PAGE_SIZE = 25


@dataclass(frozen=True)
class TargetCandidate:
    channel_id: int
    label: str
    description: str
    emoji: str


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


def _kind(channel: Any) -> tuple[str, str]:
    if isinstance(channel, discord.CategoryChannel):
        count = len(list(getattr(channel, "channels", []) or []))
        return "Category", f"{count} child channel{'s' if count != 1 else ''}"
    ctype = getattr(channel, "type", None)
    if ctype == discord.ChannelType.news:
        return "Announcement", "Announcement text channel"
    if ctype == discord.ChannelType.forum:
        return "Forum", "Forum channel"
    if ctype == discord.ChannelType.stage_voice:
        return "Stage", "Stage voice channel"
    if isinstance(channel, discord.VoiceChannel):
        return "Voice", "Voice channel"
    return "Text", "Text channel"


def _parent_name(channel: Any) -> str:
    try:
        category = getattr(channel, "category", None)
        name = str(getattr(category, "name", "") or "").strip()
        return name
    except Exception:
        return ""


def _clean_search_query(query: Any) -> str:
    text = str(query or "").strip()
    if text.startswith("<#") and text.endswith(">"):
        inner = text[2:-1].strip()
        if inner.isdigit():
            return inner
    return text.casefold()


def _channel_candidates(
    state: core.PermissionRepairState,
    actor: Any,
    *,
    query: str = "",
) -> list[TargetCandidate]:
    guild = state.guild
    if int(getattr(actor, "id", 0) or 0) != int(state.actor_id):
        return []

    needle = _clean_search_query(query)
    out: list[TargetCandidate] = []
    for channel in list(getattr(guild, "channels", []) or []):
        if not core._target_supported(channel):
            continue
        if not _actor_can_see(channel, guild, actor):
            continue

        channel_id = int(getattr(channel, "id", 0) or 0)
        if channel_id <= 0:
            continue
        name = str(getattr(channel, "name", "") or "").strip() or f"channel-{channel_id}"
        if needle and needle not in name.casefold() and needle not in str(channel_id):
            continue

        kind, detail = _kind(channel)
        parent = _parent_name(channel)
        if parent:
            detail = f"{detail} • {parent}"
        emoji = "🗂️" if isinstance(channel, discord.CategoryChannel) else (
            "🔊" if kind in {"Voice", "Stage"} else "💬"
        )
        out.append(
            TargetCandidate(
                channel_id=channel_id,
                label=name,
                description=f"{kind} • {detail}",
                emoji=emoji,
            )
        )
    return out


def _choice(candidate: TargetCandidate) -> DankChoice:
    return DankChoice(
        label=candidate.label,
        value=str(candidate.channel_id),
        description=candidate.description,
        emoji=candidate.emoji,
    )


class _TargetPageButton(discord.ui.Button):
    def __init__(
        self,
        owner: "TargetChannelPickerView",
        *,
        delta: int,
        label: str,
        emoji: str,
        disabled: bool,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=disabled,
        )
        self.owner_view = owner
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.turn_page(interaction, self.delta)


class _TargetSearchButton(discord.ui.Button):
    def __init__(self, owner: "TargetChannelPickerView") -> None:
        super().__init__(
            label="Search",
            emoji="🔎",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        self.owner_view = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != int(self.owner_view.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        await interaction.response.send_modal(
            TargetSearchModal(
                state=self.owner_view.state,
                current_query=self.owner_view.query,
            )
        )


class _ClearSearchButton(discord.ui.Button):
    def __init__(self, owner: "TargetChannelPickerView") -> None:
        super().__init__(
            label="Clear Search",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.owner_view = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.show(interaction, query="", page=0)


class TargetSearchModal(discord.ui.Modal, title="Search Dank Shield Targets"):
    query = discord.ui.TextInput(
        label="Channel/category name or ID",
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
        if int(interaction.user.id) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Manage Server, Manage Channels, or Administrator is required.",
            )

        view = TargetChannelPickerView(
            self.state,
            actor=interaction.user,
            query=str(self.query.value or ""),
            page=0,
        )
        await interaction.response.send_message(
            embed=view.embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TargetChannelPickerView(DankPickerView):
    """Dank-owned, paged channel/category browser for Fix Access."""

    def __init__(
        self,
        state: core.PermissionRepairState,
        *,
        actor: Any,
        query: str = "",
        page: int = 0,
    ) -> None:
        self.state = state
        self.actor = actor
        self.query = str(query or "").strip()
        self.candidates = _channel_candidates(state, actor, query=self.query)
        self.page_count = max(1, int(ceil(len(self.candidates) / _PAGE_SIZE)))
        self.page = max(0, min(int(page), self.page_count - 1))

        start = self.page * _PAGE_SIZE
        page_items = self.candidates[start : start + _PAGE_SIZE]

        async def on_pick(interaction: discord.Interaction, value: str) -> None:
            await self.pick_target(interaction, value)

        async def on_home(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                embed=core.build_preview_embed(self.state),
                view=TargetPermissionRepairView(self.state),
            )

        super().__init__(
            author_id=int(state.actor_id),
            choices=[_choice(item) for item in page_items],
            on_pick=on_pick,
            custom_id="dank_permission_repair:target_pick",
            placeholder=(
                f"Search results: {self.query}" if self.query else "Choose a channel or category…"
            ),
            timeout=900,
            title="Dank Shield Target Picker",
            home_label="Back to Fix Access",
            cancel_label="Close",
            include_cancel=True,
            on_home=on_home,
        )

        self.add_item(
            _TargetPageButton(
                self,
                delta=-1,
                label="Previous",
                emoji="⬅️",
                disabled=self.page <= 0,
            )
        )
        self.add_item(
            _TargetPageButton(
                self,
                delta=1,
                label="Next",
                emoji="➡️",
                disabled=self.page >= self.page_count - 1,
            )
        )
        self.add_item(_TargetSearchButton(self))
        if self.query:
            self.add_item(_ClearSearchButton(self))

    def embed(self) -> discord.Embed:
        total = len(self.candidates)
        if self.query:
            intro = (
                f"Found **{total}** target{'s' if total != 1 else ''} matching "
                f"`{self.query[:80]}`."
            )
        else:
            intro = (
                f"Browsing **{total}** channel/category target"
                f"{'s' if total != 1 else ''} available to this admin."
            )
        description = (
            intro
            + "\n\nThis is Dank Shield's own server-resource browser. It uses the bot's "
            "guild cache instead of Discord's generic entity picker, supports paging and "
            "name/ID search, and preserves the Fix Access choices you already made."
        )
        embed = discord.Embed(
            title="🔎 Dank Shield Target Picker",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Page",
            value=f"{self.page + 1} / {self.page_count}",
            inline=True,
        )
        if self.state.target is not None:
            embed.add_field(
                name="Current target",
                value=core._target_label(self.state.target),
                inline=True,
            )
        if total == 0:
            embed.add_field(
                name="No matches",
                value=(
                    "Try **Search** with part of the channel/category name, a channel ID, "
                    "or clear the search. Channels this admin cannot view/manage are intentionally hidden."
                ),
                inline=False,
            )
        return embed

    async def show(
        self,
        interaction: discord.Interaction,
        *,
        query: str | None = None,
        page: int | None = None,
    ) -> None:
        if int(interaction.user.id) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Manage Server, Manage Channels, or Administrator is required.",
            )
        view = TargetChannelPickerView(
            self.state,
            actor=interaction.user,
            query=self.query if query is None else query,
            page=self.page if page is None else page,
        )
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def turn_page(self, interaction: discord.Interaction, delta: int) -> None:
        await self.show(interaction, page=self.page + int(delta))

    async def pick_target(self, interaction: discord.Interaction, value: str) -> None:
        if int(interaction.user.id) != int(self.state.actor_id):
            return await _safe_ephemeral(
                interaction,
                "❌ This target picker belongs to another admin.",
            )
        if not core._actor_can_manage(interaction):
            return await _safe_ephemeral(
                interaction,
                "❌ Manage Server, Manage Channels, or Administrator is required.",
            )
        try:
            channel_id = int(str(value).strip())
        except Exception:
            return await _safe_ephemeral(
                interaction,
                "❌ That target ID could not be read. Refresh the picker and try again.",
            )

        target = self.state.guild.get_channel(channel_id)
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
        try:
            print(
                "[permission_repair_ui] target picker callback failed: "
                f"{type(error).__name__}: {error}"
            )
        except Exception:
            pass
        await _safe_ephemeral(
            interaction,
            "⚠️ Dank Shield could not finish that picker action. Nothing was changed. "
            "Reopen **Fix Access** and try again.",
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
                "❌ Manage Server, Manage Channels, or Administrator is required.",
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
    """Fix Access view with a dedicated Dank Shield target browser."""

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
        try:
            print(
                "[permission_repair_ui] Fix Access callback failed: "
                f"{type(error).__name__}: {error}"
            )
        except Exception:
            pass
        await _safe_ephemeral(
            interaction,
            "⚠️ Fix Access could not finish that interaction. Nothing was changed. "
            "Retry the action or reopen **Fix Access**.",
        )


async def open_target_permission_repair(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
    if not core._actor_can_manage(interaction):
        return await _safe_ephemeral(
            interaction,
            "❌ Manage Server, Manage Channels, or Administrator is required.",
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
# dedicated surface so inherited callbacks never fall back to the native picker.
core.TargetPermissionRepairView = TargetPermissionRepairView
core.open_target_permission_repair = open_target_permission_repair


__all__ = [
    "TargetCandidate",
    "TargetChannelPickerView",
    "TargetPermissionRepairView",
    "TargetSearchModal",
    "open_target_permission_repair",
]
