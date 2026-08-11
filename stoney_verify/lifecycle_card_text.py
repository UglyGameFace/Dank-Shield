from __future__ import annotations

"""Image-safe text adapters for canonical join/exit lifecycle cards.

Discord can display compatibility alphabets such as mathematical bold/script
letters that the Pillow fonts available in production do not necessarily
contain. Keep the original Discord-facing member text untouched, but normalize
only the copy handed to the bitmap renderer so those names remain readable.
"""

import unicodedata
from typing import Any


def image_safe_text(value: Any, *, fallback: str) -> str:
    """Return readable text for Pillow without changing Discord-facing text.

    NFKC maps decorative compatibility alphabets (for example 𝔼𝕪𝕖𝕫) back to
    their ordinary Unicode equivalents while preserving normal names, accents,
    emoji, and non-Latin scripts. Whitespace is collapsed because cards are
    single-line display surfaces.
    """

    raw = " ".join(str(value or "").replace("\n", " ").split()).strip()
    if not raw:
        raw = fallback
    normalized = unicodedata.normalize("NFKC", raw)
    return normalized.strip() or fallback


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
