from __future__ import annotations

"""Stable identity helpers for Dank Shield Share Router infrastructure.

Share Router source channels intentionally keep boring, predictable names so
mobile share sheets can find them. Server Designer must therefore treat the hub
as functional infrastructure rather than ordinary decorative server content.
"""

import re
import unicodedata
from typing import Any, Optional

SHARE_ROUTER_CATEGORY_NAME = "🔗 SHARE ROUTES"
SHARE_ROUTER_CATEGORY_KEY = "share-routes"
# Extra categories are allocated only when the primary category reaches Discord's 50-channel limit.
SHARE_ROUTER_ADDITIONAL_CATEGORY_RE = re.compile(r"^share-routes-(?:[2-9]|[1-9][0-9]+)$")

DEFAULT_SHARE_CHANNELS: tuple[str, ...] = (
    "share-gaming-news",
    "share-deals",
    "share-memes",
    "share-announcements",
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_share_router_name(value: Any) -> str:
    """Return an ASCII-ish key that survives Dank Design decoration.

    NFKC collapses the mathematical/full-width alphabets used by decorative
    fonts. Keeping only ASCII words also removes emoji, bracket separators, and
    category-frame glyphs without coupling this runtime to Server Designer.
    """

    try:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    except Exception:
        text = str(value or "").casefold()
    return "-".join(_WORD_RE.findall(text))


def share_source_key(value: Any) -> Optional[str]:
    key = normalize_share_router_name(value)
    return key if key in DEFAULT_SHARE_CHANNELS else None


def is_share_router_category_name(value: Any) -> bool:
    return normalize_share_router_name(value) == SHARE_ROUTER_CATEGORY_KEY


def is_share_router_managed_category_name(value: Any) -> bool:
    key = normalize_share_router_name(value)
    return key == SHARE_ROUTER_CATEGORY_KEY or bool(SHARE_ROUTER_ADDITIONAL_CATEGORY_RE.fullmatch(key))


def custom_share_source_name(value: Any) -> str:
    """Validate a plain, mobile-share-friendly name for a new proxy channel."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 100:
        raise ValueError("Enter a source name between 1 and 100 characters.")
    key = normalize_share_router_name(raw)
    if key.startswith("share-"):
        key = key[6:]
    name = f"share-{key}"
    if not key or len(name) > 100 or name == "share-routes":
        raise ValueError("Use a shorter name with letters or numbers, such as Gaming Clips.")
    if name in DEFAULT_SHARE_CHANNELS:
        raise ValueError("That is a built-in Share Router channel. Use Add / Change Route instead.")
    return name


def is_share_router_design_resource(resource: Any) -> bool:
    """Whether a Discord category/channel is reserved Share Router plumbing."""

    try:
        name = getattr(resource, "name", "")
        if is_share_router_managed_category_name(name):
            return True

        # Protect all resources inside managed proxy hubs, including custom ones,
        # from the server-name designer. Outside the hub names have no special status.
        parent = getattr(resource, "category", None)
        return parent is not None and is_share_router_managed_category_name(getattr(parent, "name", ""))
    except Exception:
        return False


__all__ = [
    "DEFAULT_SHARE_CHANNELS",
    "custom_share_source_name",
    "is_share_router_managed_category_name",
    "SHARE_ROUTER_CATEGORY_KEY",
    "SHARE_ROUTER_CATEGORY_NAME",
    "is_share_router_category_name",
    "is_share_router_design_resource",
    "normalize_share_router_name",
    "share_source_key",
]
