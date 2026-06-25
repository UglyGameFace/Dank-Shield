from __future__ import annotations

"""Shared picker UI primitives for Dank Shield.

Goal
----
Every setup/protection/design/ticket/member flow should feel like the same bot.
This module provides one reusable picker contract instead of each feature
inventing its own dropdown wording, back button, cancel behavior, owner checks,
empty-state handling, and value parsing.

Adoption rule
-------------
New picker/dropdown surfaces should use :class:`DankPickerView`. Flows that need
Discord-native entity selectors should use the Dank Shield wrappers in this file:
:class:`DankRoleSelect`, :class:`DankChannelSelect`, :class:`DankUserSelect`, or
:class:`DankMentionableSelect`.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

import discord

PickerAction = Callable[[discord.Interaction, str], Awaitable[None]]
MultiPickerAction = Callable[[discord.Interaction, list[str]], Awaitable[None]]
RolePickAction = Callable[[discord.Interaction, discord.Role], Awaitable[None]]
ChannelPickAction = Callable[[discord.Interaction, discord.abc.GuildChannel], Awaitable[None]]
UserPickAction = Callable[[discord.Interaction, discord.abc.User], Awaitable[None]]
MentionablePickAction = Callable[[discord.Interaction, Any], Awaitable[None]]

_MAX_SELECT_OPTIONS = 25
_DEFAULT_TIMEOUT_SECONDS = 900
_HOME_VALUE = "__dank_home__"
_CANCEL_VALUE = "__dank_cancel__"


@dataclass(frozen=True)
class DankChoice:
    """One normalized selectable item for Dank Shield picker surfaces."""

    label: str
    value: str
    description: str = ""
    emoji: Optional[str] = None
    default: bool = False

    def to_option(self) -> discord.SelectOption:
        label = _clip(self.label, 100)
        description = _clip(self.description, 100) if self.description else None
        value = _clip(self.value, 100)
        return discord.SelectOption(
            label=label,
            value=value,
            description=description,
            emoji=self.emoji,
            default=bool(self.default),
        )


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def make_choice(
    label: str,
    value: str,
    *,
    description: str = "",
    emoji: Optional[str] = None,
    default: bool = False,
) -> DankChoice:
    return DankChoice(label=label, value=value, description=description, emoji=emoji, default=default)


def make_home_choice(*, label: str = "Back to menu", description: str = "Return without changing anything.") -> DankChoice:
    return DankChoice(label=label, value=_HOME_VALUE, description=description, emoji="↩️")


def chunk_choices(choices: Sequence[DankChoice], *, size: int = _MAX_SELECT_OPTIONS) -> list[list[DankChoice]]:
    safe_size = max(1, min(int(size or _MAX_SELECT_OPTIONS), _MAX_SELECT_OPTIONS))
    return [list(choices[index : index + safe_size]) for index in range(0, len(choices), safe_size)]


async def _safe_ephemeral(interaction: discord.Interaction, message: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def _interaction_user_id(interaction: discord.Interaction) -> int:
    try:
        return int(getattr(getattr(interaction, "user", None), "id", 0) or 0)
    except Exception:
        return 0


async def _owner_check(interaction: discord.Interaction, *, author_id: int, allow_anyone: bool) -> bool:
    if allow_anyone or int(author_id or 0) <= 0:
        return True
    if _interaction_user_id(interaction) == int(author_id or 0):
        return True
    await _safe_ephemeral(interaction, "Only the person who opened this picker can use it.")
    return False


class _DankPickerSelect(discord.ui.Select):
    def __init__(self, owner: "DankPickerView") -> None:
        self._owner = owner
        options = [choice.to_option() for choice in owner.choices[:_MAX_SELECT_OPTIONS]]
        if not options:
            options = [
                DankChoice(
                    label="Nothing to choose yet",
                    value="__dank_empty__",
                    description="This section has no available options.",
                    emoji="ℹ️",
                ).to_option()
            ]
        super().__init__(
            placeholder=_clip(owner.placeholder, 150),
            custom_id=owner.custom_id,
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self._owner.handle_pick(interaction, str((self.values or [""])[0]))


class DankPickerView(discord.ui.View):
    """Standard Dank Shield dropdown surface.

    This intentionally solves the repeated rough edges seen across the bot:
    - owner-only interactions for setup flows
    - consistent home/back/cancel behavior
    - normalized empty states
    - Discord's 25-option select limit
    - safe ephemeral errors instead of interaction failed
    """

    def __init__(
        self,
        *,
        author_id: int,
        choices: Sequence[DankChoice],
        on_pick: PickerAction,
        custom_id: str,
        placeholder: str = "Choose an option…",
        timeout: Optional[float] = _DEFAULT_TIMEOUT_SECONDS,
        title: str = "Dank Shield Picker",
        home_label: str = "Back",
        cancel_label: str = "Close",
        allow_anyone: bool = False,
        include_cancel: bool = True,
        on_home: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        on_cancel: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = int(author_id or 0)
        self.choices = list(choices or [])[:_MAX_SELECT_OPTIONS]
        self.on_pick = on_pick
        self.custom_id = _clip(custom_id, 100) or "dank:picker"
        self.placeholder = placeholder
        self.title = title
        self.allow_anyone = bool(allow_anyone)
        self.on_home = on_home
        self.on_cancel = on_cancel
        self.add_item(_DankPickerSelect(self))
        if on_home is not None:
            self.add_item(_PickerHomeButton(label=home_label))
        if include_cancel:
            self.add_item(_PickerCancelButton(label=cancel_label))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone)

    async def handle_pick(self, interaction: discord.Interaction, value: str) -> None:
        if value in {"", "__dank_empty__"}:
            return await _safe_ephemeral(interaction, "There is nothing to pick here yet.")
        if value == _HOME_VALUE:
            if self.on_home is not None:
                await self.on_home(interaction)
            return
        if value == _CANCEL_VALUE:
            await self.close(interaction)
            return
        await self.on_pick(interaction, value)

    async def close(self, interaction: discord.Interaction) -> None:
        if self.on_cancel is not None:
            await self.on_cancel(interaction)
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="Picker closed.", embed=None, view=None)
            else:
                await interaction.edit_original_response(content="Picker closed.", embed=None, view=None)
        except Exception:
            await _safe_ephemeral(interaction, "Picker closed.")


class _PickerHomeButton(discord.ui.Button):
    def __init__(self, *, label: str) -> None:
        super().__init__(label=_clip(label, 80), emoji="↩️", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        owner = self.view
        if isinstance(owner, DankPickerView) and owner.on_home is not None:
            await owner.on_home(interaction)


class _PickerCancelButton(discord.ui.Button):
    def __init__(self, *, label: str) -> None:
        super().__init__(label=_clip(label, 80), emoji="✖️", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        owner = self.view
        if isinstance(owner, (DankPickerView, DankMultiPickerView)):
            await owner.close(interaction)


class _DankMultiPickerSelect(discord.ui.Select):
    def __init__(self, owner: "DankMultiPickerView") -> None:
        self._owner = owner
        options = [choice.to_option() for choice in owner.choices[:_MAX_SELECT_OPTIONS]]
        if not options:
            options = [
                DankChoice(
                    label="Nothing to choose yet",
                    value="__dank_empty__",
                    description="This section has no available options.",
                    emoji="ℹ️",
                ).to_option()
            ]

        max_values = max(1, min(int(owner.max_values or len(options)), len(options), _MAX_SELECT_OPTIONS))
        min_values = max(0, min(int(owner.min_values or 0), max_values))

        super().__init__(
            placeholder=_clip(owner.placeholder, 150),
            custom_id=owner.custom_id,
            min_values=min_values,
            max_values=max_values,
            options=options,
            row=0,
            disabled=options[0].value == "__dank_empty__",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self._owner.handle_pick(interaction, [str(value) for value in (self.values or [])])


class DankMultiPickerView(discord.ui.View):
    """Standard Dank Shield multi-select dropdown surface.

    Use this for official multi-choice surfaces instead of creating raw
    discord.ui.Select views in individual feature modules.
    """

    def __init__(
        self,
        *,
        author_id: int,
        choices: Sequence[DankChoice],
        on_pick: MultiPickerAction,
        custom_id: str,
        placeholder: str = "Choose one or more options…",
        timeout: Optional[float] = _DEFAULT_TIMEOUT_SECONDS,
        title: str = "Dank Shield Multi Picker",
        home_label: str = "Back",
        cancel_label: str = "Close",
        allow_anyone: bool = False,
        include_cancel: bool = True,
        on_home: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        on_cancel: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        min_values: int = 0,
        max_values: Optional[int] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = int(author_id or 0)
        self.choices = list(choices or [])[:_MAX_SELECT_OPTIONS]
        self.on_pick = on_pick
        self.custom_id = _clip(custom_id, 100) or "dank:multi_picker"
        self.placeholder = placeholder
        self.title = title
        self.allow_anyone = bool(allow_anyone)
        self.on_home = on_home
        self.on_cancel = on_cancel
        self.min_values = max(0, int(min_values or 0))
        self.max_values = max_values if max_values is not None else len(self.choices)

        self.add_item(_DankMultiPickerSelect(self))

        if on_home is not None:
            self.add_item(_PickerHomeButton(label=home_label))
        if include_cancel:
            self.add_item(_PickerCancelButton(label=cancel_label))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone)

    async def handle_pick(self, interaction: discord.Interaction, values: list[str]) -> None:
        clean = [str(value) for value in values if str(value or "") not in {"", "__dank_empty__"}]
        await self.on_pick(interaction, clean)

    async def close(self, interaction: discord.Interaction) -> None:
        if self.on_cancel is not None:
            await self.on_cancel(interaction)
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="Picker closed.", embed=None, view=None)
            else:
                await interaction.edit_original_response(content="Picker closed.", embed=None, view=None)
        except Exception:
            await _safe_ephemeral(interaction, "Picker closed.")


class DankRoleSelect(discord.ui.RoleSelect):
    """Dank Shield wrapper for Discord role picking."""

    def __init__(
        self,
        *,
        author_id: int,
        on_pick: RolePickAction,
        placeholder: str,
        row: int = 0,
        allow_anyone: bool = False,
        min_values: int = 1,
        max_values: int = 1,
    ) -> None:
        super().__init__(placeholder=_clip(placeholder, 150), min_values=min_values, max_values=max_values, row=row)
        self.author_id = int(author_id or 0)
        self.allow_anyone = bool(allow_anyone)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone):
            return
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role):
            return await _safe_ephemeral(interaction, "Pick a role first.")
        await self.on_pick(interaction, role)


class DankChannelSelect(discord.ui.ChannelSelect):
    """Dank Shield wrapper for Discord channel/category picking."""

    def __init__(
        self,
        *,
        author_id: int,
        on_pick: ChannelPickAction,
        placeholder: str,
        channel_types: Optional[list[discord.ChannelType]] = None,
        row: int = 0,
        allow_anyone: bool = False,
        min_values: int = 1,
        max_values: int = 1,
    ) -> None:
        super().__init__(
            placeholder=_clip(placeholder, 150),
            min_values=min_values,
            max_values=max_values,
            channel_types=channel_types,
            row=row,
        )
        self.author_id = int(author_id or 0)
        self.allow_anyone = bool(allow_anyone)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone):
            return
        channel = self.values[0] if self.values else None
        if not isinstance(channel, discord.abc.GuildChannel):
            return await _safe_ephemeral(interaction, "Pick a server channel first.")
        await self.on_pick(interaction, channel)


class DankUserSelect(discord.ui.UserSelect):
    """Dank Shield wrapper for Discord user/member picking."""

    def __init__(
        self,
        *,
        author_id: int,
        on_pick: UserPickAction,
        placeholder: str,
        row: int = 0,
        allow_anyone: bool = False,
        min_values: int = 1,
        max_values: int = 1,
    ) -> None:
        super().__init__(placeholder=_clip(placeholder, 150), min_values=min_values, max_values=max_values, row=row)
        self.author_id = int(author_id or 0)
        self.allow_anyone = bool(allow_anyone)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone):
            return
        user = self.values[0] if self.values else None
        if user is None:
            return await _safe_ephemeral(interaction, "Pick a user first.")
        await self.on_pick(interaction, user)


class DankMentionableSelect(discord.ui.MentionableSelect):
    """Dank Shield wrapper for role/user mentionable picking."""

    def __init__(
        self,
        *,
        author_id: int,
        on_pick: MentionablePickAction,
        placeholder: str,
        row: int = 0,
        allow_anyone: bool = False,
        min_values: int = 1,
        max_values: int = 1,
    ) -> None:
        super().__init__(placeholder=_clip(placeholder, 150), min_values=min_values, max_values=max_values, row=row)
        self.author_id = int(author_id or 0)
        self.allow_anyone = bool(allow_anyone)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _owner_check(interaction, author_id=self.author_id, allow_anyone=self.allow_anyone):
            return
        value = self.values[0] if self.values else None
        if value is None:
            return await _safe_ephemeral(interaction, "Pick a user or role first.")
        await self.on_pick(interaction, value)


__all__ = [
    "ChannelPickAction",
    "DankChannelSelect",
    "DankChoice",
    "DankMentionableSelect",
    "DankMultiPickerView",
    "DankPickerView",
    "DankRoleSelect",
    "DankUserSelect",
    "MentionablePickAction",
    "MultiPickerAction",
    "PickerAction",
    "RolePickAction",
    "UserPickAction",
    "chunk_choices",
    "make_choice",
    "make_home_choice",
]
