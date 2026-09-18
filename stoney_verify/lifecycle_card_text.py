from __future__ import annotations

"""Image-safe text adapters for canonical join/exit lifecycle cards.

Lifecycle image text preserves the exact Discord Unicode spelling.  The bitmap
renderer owns font fallback and shaping; this adapter only collapses line breaks
and repeated whitespace because card labels are single-line surfaces.
"""

from typing import Any


def image_safe_text(value: Any, *, fallback: str) -> str:
    """Return exact Unicode card text with single-line whitespace cleanup."""

    raw = " ".join(str(value or "").replace("\n", " ").split()).strip()
    return raw or fallback


class _ImageCardGuild:
    __slots__ = ("_guild",)

    def __init__(self, guild: Any) -> None:
        self._guild = guild

    @property
    def name(self) -> str:
        return image_safe_text(getattr(self._guild, "name", ""), fallback="Your Server")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._guild, name)


class ImageCardMember:
    """Read-only member adapter used only while rendering lifecycle images."""

    __slots__ = ("_member", "_guild")

    def __init__(self, member: Any) -> None:
        self._member = member
        self._guild = _ImageCardGuild(getattr(member, "guild", None))

    @property
    def guild(self) -> _ImageCardGuild:
        return self._guild

    @property
    def display_name(self) -> str:
        fallback = str(getattr(self._member, "name", "") or self._member or "Member")
        return image_safe_text(getattr(self._member, "display_name", ""), fallback=fallback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._member, name)

    def __str__(self) -> str:
        return image_safe_text(str(self._member), fallback="Member")


def image_card_member(member: Any) -> ImageCardMember:
    """Wrap a Discord member for bitmap rendering only."""

    return ImageCardMember(member)


__all__ = ["ImageCardMember", "image_card_member", "image_safe_text"]
