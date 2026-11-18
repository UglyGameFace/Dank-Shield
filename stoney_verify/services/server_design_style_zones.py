from __future__ import annotations

"""Dank Design style-zone detection.

Pure helper module. It does not mutate Discord, channels, roles, permissions,
database settings, channel order, topics, slowmode, NSFW state, or ticket config.

This module must stay generic for public servers. Server-specific taste belongs
in saved per-server design rules, not global hardcoded defaults.
"""

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def _text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _plain(value: Any) -> str:
    text = _text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s/-]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_any(text: str, words: set[str]) -> bool:
    tokens = set(_plain(text).split())
    if tokens & words:
        return True
    joined = " ".join(tokens)
    return any(word in joined for word in words)


_ZONE_WORDS: list[tuple[str, set[str]]] = [
    ("onboarding", {"start", "welcome", "rules", "announcement", "announcements", "levelups", "giveaway"}),
    ("verification", {"verify", "verification", "unverified", "verified"}),
    ("support_tickets", {"support", "ticket", "tickets", "transcript", "transcripts", "archive", "archived"}),
    ("safety_logs", {"mod", "mods", "staff", "log", "logs", "ban", "bans", "blacklist", "bot", "spam"}),
    ("media_pics", {"pic", "pics", "media", "meme", "memes", "photo", "photos", "clip", "clips", "pet", "pets", "quote", "quotes", "song", "music"}),
    ("gaming_voice", {"game", "games", "gaming", "chat", "voice", "voicechat", "lounge"}),
]


def zone_for_name(value: Any, *, kind: str = "text") -> str:
    """Return a broad style zone from visible category/channel wording."""

    text = _plain(value)
    if not text:
        return "unknown"

    if kind == "voice":
        return "gaming_voice"

    for zone, words in _ZONE_WORDS:
        if _has_any(text, words):
            return zone

    return "general"


def zone_for_item(item: Mapping[str, Any]) -> str:
    kind = _text(item.get("kind"), "text")
    before = _text(item.get("before") or item.get("name") or item.get("category") or "")
    return zone_for_name(before, kind=kind)


def zone_label(zone: Any) -> str:
    labels = {
        "onboarding": "Onboarding / public start",
        "verification": "Verification",
        "support_tickets": "Support / tickets",
        "safety_logs": "Safety / staff logs",
        "media_pics": "Media / pics",
        "gaming_voice": "Gaming / voice",
        "general": "General",
        "unknown": "Unknown",
    }
    return labels.get(_text(zone), _text(zone, "Unknown").replace("_", " ").title())


# Zone style templates: named font/frame pairings a server owner can stamp
# onto one category in a single pick so categories can differ while still
# following one scheme. A template resolves to one ``category_styles`` entry
# (the shape validated by server_design_studio.normalize_category_style):
# ``font`` styles the channels inside the category, ``category_font`` styles
# the category header when set (otherwise the header follows ``font``), and a
# ``None`` field leaves that piece to the server-wide design.
CATEGORY_STYLE_TEMPLATES: dict[str, dict[str, Any]] = {
    "front_door": {
        "label": "🚪 Front Door",
        "description": "Clean Sans headers and channels for entrances and info.",
        "font": "sans",
        "category_font": "sans",
        "separator_id": None,
        "category_frame_id": None,
    },
    "social": {
        "label": "💬 Social",
        "description": "Small Caps headers and channels for everyday chat.",
        "font": "small_caps",
        "category_font": "small_caps",
        "separator_id": None,
        "category_frame_id": None,
    },
    "smoke": {
        "label": "🌬️ Smoke Lounge",
        "description": "Serif Italic header over Italic Sans channels.",
        "font": "italic_sans",
        "category_font": "serif_italic",
        "separator_id": None,
        "category_frame_id": None,
    },
    "gaming": {
        "label": "🎮 Gaming",
        "description": "Bold Italic Sans headers and channels for play areas.",
        "font": "bold_italic_sans",
        "category_font": "bold_italic_sans",
        "separator_id": None,
        "category_frame_id": None,
    },
    "arcade": {
        "label": "🕹️ Arcade",
        "description": "Bold Sans throughout for games and media.",
        "font": "bold_sans",
        "category_font": None,
        "separator_id": None,
        "category_frame_id": None,
    },
    "garden": {
        "label": "🌿 Garden",
        "description": "Serif Bold Italic header over Italic Sans channels.",
        "font": "italic_sans",
        "category_font": "serif_bold_italic",
        "separator_id": None,
        "category_frame_id": None,
    },
    "nightlife": {
        "label": "🍸 Nightlife",
        "description": "Bold Script header over Bold Sans channels.",
        "font": "bold_sans",
        "category_font": "bold_script",
        "separator_id": None,
        "category_frame_id": None,
    },
    "deals": {
        "label": "🔥 Deals",
        "description": "Bold Sans throughout for deals and drops.",
        "font": "bold_sans",
        "category_font": None,
        "separator_id": None,
        "category_frame_id": None,
    },
    "staff": {
        "label": "🛡️ Staff",
        "description": "Bold Sans with a Heavy Line category frame.",
        "font": "bold_sans",
        "category_font": None,
        "separator_id": None,
        "category_frame_id": "heavy_line",
    },
    "support": {
        "label": "🎫 Support",
        "description": "Clean Sans throughout for help and tickets.",
        "font": "sans",
        "category_font": None,
        "separator_id": None,
        "category_frame_id": None,
    },
}


def category_style_template_ids() -> tuple[str, ...]:
    return tuple(CATEGORY_STYLE_TEMPLATES)


def category_style_template_label(template_id: Any) -> str:
    template = CATEGORY_STYLE_TEMPLATES.get(_text(template_id))
    if template is None:
        return "Custom"
    return _text(template.get("label"), "Custom")


def category_style_from_template(template_id: Any) -> dict[str, Any] | None:
    """Resolve a zone template id to a fresh ``category_styles`` entry."""

    template = CATEGORY_STYLE_TEMPLATES.get(_text(template_id))
    if template is None:
        return None
    return {
        "font": template.get("font"),
        "category_font": template.get("category_font"),
        "separator_id": template.get("separator_id"),
        "category_frame_id": template.get("category_frame_id"),
    }


def annotate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add design_zone metadata to plan rows without changing rename decisions."""

    for item in items:
        item["design_zone"] = zone_for_item(item)
        item["design_zone_label"] = zone_label(item["design_zone"])
    return items


def zone_summary(items: list[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        zone = _text(item.get("design_zone") or zone_for_item(item), "unknown")
        out[zone] = out.get(zone, 0) + 1
    return out


def zone_summary_text(items: list[Mapping[str, Any]], *, limit: int = 6) -> str:
    summary = zone_summary(items)
    if not summary:
        return "No zones detected."

    ordered = sorted(summary.items(), key=lambda pair: (-pair[1], pair[0]))
    lines = [f"• **{zone_label(zone)}:** {count}" for zone, count in ordered[:limit]]
    return "\n".join(lines)[:1024]


__all__ = [
    "CATEGORY_STYLE_TEMPLATES",
    "annotate_items",
    "category_style_from_template",
    "category_style_template_ids",
    "category_style_template_label",
    "zone_for_item",
    "zone_for_name",
    "zone_label",
    "zone_summary",
    "zone_summary_text",
]
