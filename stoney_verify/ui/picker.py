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
        self.values = clean
        await self.on_pick(interaction, clean)

    @property
    def options(self) -> list[discord.SelectOption]:
        for child in getattr(self, "children", []):
            if isinstance(child, discord.ui.Select):
                return list(child.options)
        return []

    @property
    def values(self) -> list[str]:
        return list(self.__dict__.get("_values") or [])

    @values.setter
    def values(self, new_values: Any) -> None:
        self.__dict__["_values"] = [str(value) for value in (new_values or [])]

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


async def _resolve_selected_guild_channel(
    interaction: discord.Interaction,
    selected: Any,
) -> Optional[discord.abc.GuildChannel]:
    """Resolve Discord UI channel-select partials to cached guild channels.

    discord.py channel selects can yield AppCommandChannel partial objects rather
    than concrete GuildChannel instances. Normal Dank Shield callbacks expect the
    concrete cached object so permission checks and type-specific handling remain
    consistent.
    """

    if isinstance(selected, discord.abc.GuildChannel):
        return selected
    if selected is None:
        return None

    resolver = getattr(selected, "resolve", None)
    if callable(resolver):
        try:
            resolved = resolver()
        except Exception:
            resolved = None
        if isinstance(resolved, discord.abc.GuildChannel):
            return resolved

    guild = getattr(interaction, "guild", None)
    channel_id = getattr(selected, "id", None)
    if guild is not None and channel_id is not None:
        try:
            resolved = guild.get_channel(int(channel_id))
        except Exception:
            resolved = None
        if isinstance(resolved, discord.abc.GuildChannel):
            return resolved

    return None


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
        selected = self.values[0] if self.values else None
        channel = await _resolve_selected_guild_channel(interaction, selected)
        if channel is None:
            return await _safe_ephemeral(
                interaction,
                "I received that channel selection but could not resolve it from this server. Reopen the picker and try again.",
            )
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


class DankPagedPickerView(DankPickerView):
    """House picker that pages long choice lists with ``chunk_choices``.

    Every choice stays reachable past Discord's 25-option select limit:
    pages carry page-suffixed stable custom_ids and Prev/Next buttons.
    Also keeps the legacy select-shaped surface (``options`` / ``values``
    / ``callback``) that older callers and tests used to drive the raw
    selects this replaces, so a migrated picker can be driven exactly
    like the select it superseded.
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
        page: int = 0,
    ) -> None:
        all_choices = list(choices or [])
        pages = chunk_choices(all_choices) or [[]]
        page = max(0, min(int(page), len(pages) - 1))
        base_custom_id = _clip(custom_id, 96) or "dank:picker"
        super().__init__(
            author_id=int(author_id or 0),
            choices=pages[page],
            on_pick=on_pick,
            custom_id=f"{base_custom_id}:{page}" if len(pages) > 1 else base_custom_id,
            placeholder=(
                f"{placeholder} • Page {page + 1}/{len(pages)}"
                if len(pages) > 1
                else placeholder
            ),
            timeout=timeout,
            title=title,
            home_label=home_label,
            cancel_label=cancel_label,
            allow_anyone=allow_anyone,
            include_cancel=include_cancel,
            on_home=on_home,
            on_cancel=on_cancel,
        )
        self.all_choices = all_choices
        self.page = page
        self.pages = len(pages)
        self._paged_kwargs: dict[str, Any] = {
            "author_id": int(author_id or 0),
            "choices": all_choices,
            "on_pick": on_pick,
            "custom_id": base_custom_id,
            "placeholder": placeholder,
            "timeout": timeout,
            "title": title,
            "home_label": home_label,
            "cancel_label": cancel_label,
            "allow_anyone": allow_anyone,
            "include_cancel": include_cancel,
            "on_home": on_home,
            "on_cancel": on_cancel,
        }
        self._base_custom_id = base_custom_id
        if len(pages) > 1:
            self.add_item(_PagedPickerPageButton(delta=-1, disabled=page <= 0, base_custom_id=base_custom_id))
            self.add_item(_PagedPickerPageButton(delta=1, disabled=page >= len(pages) - 1, base_custom_id=base_custom_id))

    def _for_page(self, page: int) -> "DankPagedPickerView":
        return DankPagedPickerView(**self._paged_kwargs, page=page)

    @property
    def options(self) -> list[discord.SelectOption]:
        for child in getattr(self, "children", []):
            if isinstance(child, discord.ui.Select):
                return list(child.options)
        return []

    @property
    def values(self) -> list[str]:
        return list(self.__dict__.get("_values") or [])

    @values.setter
    def values(self, new_values: Any) -> None:
        self.__dict__["_values"] = [str(value) for value in (new_values or [])]

    async def callback(self, interaction: discord.Interaction) -> None:
        vals = self.values
        if vals:
            await self.handle_pick(interaction, str(vals[0]))


class _PagedPickerPageButton(discord.ui.Button):
    def __init__(self, *, delta: int, disabled: bool, base_custom_id: str) -> None:
        super().__init__(
            label="Previous" if delta < 0 else "Next",
            emoji="◀️" if delta < 0 else "▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{base_custom_id}:prev" if delta < 0 else f"{base_custom_id}:next",
            row=2,
            disabled=bool(disabled),
        )
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, DankPagedPickerView):
            return
        if not await view.interaction_check(interaction):
            return
        page = max(0, min(view.page + self.delta, view.pages - 1))
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view._for_page(page))
        else:
            await interaction.response.edit_message(view=view._for_page(page))


class DankPickerOpenButton(discord.ui.Button):
    """Opener that swaps a host panel for its house picker on the same message.

    Replaces an embedded raw select 1-for-1 on the host view: the host
    view's own interaction check still gates the press, the current
    embed is preserved unless an explicit one is supplied, and the
    picker is built fresh at press time so it always reflects live
    host state. ``build`` may be a plain callable returning the picker
    view.
    """

    def __init__(
        self,
        *,
        label: str,
        custom_id: str,
        build: Callable[[discord.Interaction], discord.ui.View],
        emoji: Optional[str] = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
        embed: Optional[discord.Embed] = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            label=_clip(label, 80),
            emoji=emoji,
            style=style,
            custom_id=_clip(custom_id, 100),
            row=row,
            disabled=bool(disabled),
        )
        self._build = build
        self._embed = embed

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        host = self.view
        check = getattr(host, "interaction_check", None)
        if callable(check):
            try:
                allowed = await check(interaction)
            except Exception:
                allowed = True
            if not allowed:
                return
        picker = self._build(interaction)
        embed = self._embed
        if embed is None:
            message = getattr(interaction, "message", None)
            embeds = getattr(message, "embeds", None) if message is not None else None
            embed = embeds[0] if embeds else None
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=picker)
        else:
            await interaction.response.edit_message(embed=embed, view=picker)


class DankPersistentSelect(discord.ui.Select):
    """Framework select item for persistent, globally-resolved components.

    Some published components (e.g. Embed Studio self-role posts) live
    on messages with ``timeout=None`` views and are resolved by a
    global interaction listener keyed on a stable custom_id, not by a
    live view callback. Those cannot become an ephemeral picker view
    without breaking already-posted panels, so the framework owns the
    raw component here: callers must supply the stable custom_id and
    the exact options, and the component keeps no callback of its own.
    """

    def __init__(
        self,
        *,
        custom_id: str,
        placeholder: str,
        options: list[discord.SelectOption],
        min_values: int = 1,
        max_values: int = 1,
        row: Optional[int] = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            custom_id=_clip(custom_id, 100),
            placeholder=_clip(placeholder, 150),
            min_values=int(min_values),
            max_values=int(max_values),
            options=options,
            row=row,
            disabled=bool(disabled),
        )


__all__ = [
    "ChannelPickAction",
    "DankChannelSelect",
    "DankChoice",
    "DankMentionableSelect",
    "DankMultiPickerView",
    "DankPagedPickerView",
    "DankPersistentSelect",
    "DankPickerOpenButton",
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
