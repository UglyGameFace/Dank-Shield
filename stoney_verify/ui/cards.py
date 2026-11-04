from __future__ import annotations

"""Shared card building blocks for Dank Shield's refreshed panels.

These helpers wrap discord.py's layout components (``discord.ui.LayoutView``,
``Container``, ``Section``, ``TextDisplay``, ``Separator``) so every refreshed
panel shares one look: a single card with an accent bar, a short heading,
setting rows with the action beside the value, and a small number of clear
buttons instead of long button grids.

Feature modules own behavior, permissions and persistence. This module only
builds components; it never reads config or touches Discord state.

Note: a message sent with layout components cannot be edited back into the
old embed format. Anything that edits a message built from these cards must
also edit it with a layout view.
"""

from typing import Iterable, Optional, Union

import discord

BRAND = 0x3DD68C
ATTENTION = 0xF2A65A
DANGER = 0xE5534B
NEUTRAL = 0x4E5058

# Discord limits for layout messages.
MAX_COMPONENTS = 40
MAX_TEXT_TOTAL = 4000
MAX_BUTTON_LABEL = 80

_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

StyleLike = Union[str, discord.ButtonStyle]


def clip(text: object, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def summarize(names: Iterable[object], *, shown: int = 3, empty: str = "Not set") -> str:
    """Return ``"A, B, C +2"`` for a list of labels, or ``empty`` if none."""

    items = [str(name).strip() for name in names if str(name or "").strip()]
    if not items:
        return empty
    head = ", ".join(items[:shown])
    extra = len(items) - shown
    return f"{head} +{extra}" if extra > 0 else head


def heading(title: str, body: Optional[str] = None) -> discord.ui.TextDisplay:
    text = f"## {clip(title, 120)}"
    if body:
        text += f"\n{clip(body, 600)}"
    return discord.ui.TextDisplay(text)


def text(body: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(clip(body, 1500))


def note(body: str) -> discord.ui.TextDisplay:
    """Small grey helper text."""

    return discord.ui.TextDisplay(f"-# {clip(body, 300)}")


def chips(labels: Iterable[object]) -> discord.ui.TextDisplay:
    """Render short labels as inline pills."""

    parts = [f"`{clip(label, 32)}`" for label in labels if str(label or "").strip()]
    return discord.ui.TextDisplay("  ".join(parts) or "-# Nothing here yet")


def divider(*, large: bool = False, visible: bool = True) -> discord.ui.Separator:
    spacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
    return discord.ui.Separator(visible=visible, spacing=spacing)


def button(
    label: str,
    custom_id: str,
    *,
    style: StyleLike = "secondary",
    emoji: Optional[str] = None,
    disabled: bool = False,
) -> discord.ui.Button:
    resolved = _STYLES.get(style, discord.ButtonStyle.secondary) if isinstance(style, str) else style
    return discord.ui.Button(
        label=clip(label, MAX_BUTTON_LABEL),
        custom_id=custom_id,
        style=resolved,
        emoji=emoji,
        disabled=disabled,
    )


def row(*buttons: discord.ui.Item) -> discord.ui.ActionRow:
    return discord.ui.ActionRow(*buttons)


def setting_row(label: str, value: str, action: discord.ui.Button) -> discord.ui.Section:
    """One setting: small label, current value in bold, action button beside it."""

    return discord.ui.Section(
        discord.ui.TextDisplay(f"-# {clip(label, 80)}\n**{clip(value, 200)}**"),
        accessory=action,
    )


def line_with_action(body: str, action: discord.ui.Button) -> discord.ui.Section:
    """A sentence with a button beside it (for notices and secondary links)."""

    return discord.ui.Section(discord.ui.TextDisplay(clip(body, 400)), accessory=action)


def card(*items: discord.ui.Item, accent: Optional[int] = BRAND) -> discord.ui.Container:
    return discord.ui.Container(*items, accent_colour=accent)


def component_count(view: discord.ui.LayoutView) -> int:
    """Count every component Discord will receive, including nested ones."""

    return sum(1 for _ in view.walk_children())


__all__ = [
    "ATTENTION",
    "BRAND",
    "DANGER",
    "MAX_COMPONENTS",
    "NEUTRAL",
    "button",
    "card",
    "chips",
    "clip",
    "component_count",
    "divider",
    "heading",
    "line_with_action",
    "note",
    "row",
    "setting_row",
    "summarize",
    "text",
]
