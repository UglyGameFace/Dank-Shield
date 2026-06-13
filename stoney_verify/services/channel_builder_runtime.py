from __future__ import annotations

"""First-class Channel Builder runtime service.

This module owns the Discord-facing Channel Builder behavior. API/startup guards
should call into this service instead of carrying mutation logic inline.
"""

import re
import unicodedata
from typing import Any, Optional

import discord
from aiohttp import web

DISCORD_CHANNEL_LIMIT = 500
CATEGORY_CHILD_LIMIT = 50


def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _range_map(chars: str, start: int) -> dict[str, str]:
    return {char: chr(start + index) for index, char in enumerate(chars)}


def _lookup(name: str, fallback: str | None = None) -> str | None:
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return fallback


def _math_letters(prefix: str, *, digit_prefix: str | None = None, special: dict[str, str] | None = None) -> dict[str, str]:
    """Build exact math alphabet maps by Unicode name.

    Several decorative alphabets have gaps in the Unicode block. Range-only maps
    leave random letters unchanged. Name lookup plus explicit special cases keeps
    Script/Fraktur/Bold Script/etc. consistent.
    """

    special = special or {}
    out: dict[str, str] = {}
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        out[ch] = special.get(ch) or _lookup(f"MATHEMATICAL {prefix} CAPITAL {ch}", ch) or ch
    for ch in "abcdefghijklmnopqrstuvwxyz":
        out[ch] = special.get(ch) or _lookup(f"MATHEMATICAL {prefix} SMALL {ch.upper()}", ch) or ch
    if digit_prefix:
        for digit, word in (
            ("0", "ZERO"),
            ("1", "ONE"),
            ("2", "TWO"),
            ("3", "THREE"),
            ("4", "FOUR"),
            ("5", "FIVE"),
            ("6", "SIX"),
            ("7", "SEVEN"),
            ("8", "EIGHT"),
            ("9", "NINE"),
        ):
            out[digit] = _lookup(f"MATHEMATICAL {digit_prefix} DIGIT {word}", digit) or digit
    return out


def _explicit(rows: list[tuple[str, int | str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in rows:
        out[key] = chr(value) if isinstance(value, int) else value
    return out


def _unicode_map(style: str) -> dict[str, str]:
    style = safe_str(style).lower().replace("-", "_")
    if style == "bold_sans":
        return _math_letters("SANS-SERIF BOLD", digit_prefix="SANS-SERIF BOLD")
    if style == "italic_sans":
        return _math_letters("SANS-SERIF ITALIC")
    if style == "bold_italic_sans":
        return _math_letters("SANS-SERIF BOLD ITALIC")
    if style == "monospace":
        return _math_letters("MONOSPACE", digit_prefix="MONOSPACE")
    if style == "fullwidth":
        return {**_range_map("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 0xFF21), **_range_map("abcdefghijklmnopqrstuvwxyz", 0xFF41), **_range_map("0123456789", 0xFF10), "-": chr(0xFF0D), " ": chr(0x3000)}
    if style == "serif_bold":
        return _math_letters("BOLD", digit_prefix="BOLD")
    if style == "serif_italic":
        return _math_letters("ITALIC", special={"h": chr(0x210E)})
    if style == "serif_bold_italic":
        return _math_letters("BOLD ITALIC")
    if style == "script":
        return _math_letters(
            "SCRIPT",
            special={
                "B": chr(0x212C),
                "E": chr(0x2130),
                "F": chr(0x2131),
                "H": chr(0x210B),
                "I": chr(0x2110),
                "L": chr(0x2112),
                "M": chr(0x2133),
                "R": chr(0x211B),
                "e": chr(0x212F),
                "g": chr(0x210A),
                "o": chr(0x2134),
            },
        )
    if style == "bold_script":
        return _math_letters("BOLD SCRIPT")
    if style == "fraktur":
        return _math_letters(
            "FRAKTUR",
            special={"C": chr(0x212D), "H": chr(0x210C), "I": chr(0x2111), "R": chr(0x211C), "Z": chr(0x2128)},
        )
    if style == "bold_fraktur":
        return _math_letters("BOLD FRAKTUR")
    if style == "circled":
        return {**_range_map("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 0x24B6), **_range_map("abcdefghijklmnopqrstuvwxyz", 0x24D0), "0": chr(0x24EA), "1": chr(0x2460), "2": chr(0x2461), "3": chr(0x2462), "4": chr(0x2463), "5": chr(0x2464), "6": chr(0x2465), "7": chr(0x2466), "8": chr(0x2467), "9": chr(0x2468)}
    if style == "parenthesized":
        return _explicit([
            ("a", 0x249C), ("b", 0x249D), ("c", 0x249E), ("d", 0x249F), ("e", 0x24A0), ("f", 0x24A1), ("g", 0x24A2), ("h", 0x24A3), ("i", 0x24A4), ("j", 0x24A5), ("k", 0x24A6), ("l", 0x24A7), ("m", 0x24A8), ("n", 0x24A9), ("o", 0x24AA), ("p", 0x24AB), ("q", 0x24AC), ("r", 0x24AD), ("s", 0x24AE), ("t", 0x24AF), ("u", 0x24B0), ("v", 0x24B1), ("w", 0x24B2), ("x", 0x24B3), ("y", 0x24B4), ("z", 0x24B5),
            ("1", 0x2474), ("2", 0x2475), ("3", 0x2476), ("4", 0x2477), ("5", 0x2478), ("6", 0x2479), ("7", 0x247A), ("8", 0x247B), ("9", 0x247C),
        ])
    if style == "small_caps":
        return _explicit([
            ("a", 0x1D00), ("b", 0x0299), ("c", 0x1D04), ("d", 0x1D05), ("e", 0x1D07), ("f", 0xA730), ("g", 0x0262), ("h", 0x029C), ("i", 0x026A), ("j", 0x1D0A), ("k", 0x1D0B), ("l", 0x029F), ("m", 0x1D0D), ("n", 0x0274), ("o", 0x1D0F), ("p", 0x1D18), ("q", 0x01EB), ("r", 0x0280), ("s", 0xA731), ("t", 0x1D1B), ("u", 0x1D1C), ("v", 0x1D20), ("w", 0x1D21), ("x", "x"), ("y", 0x028F), ("z", 0x1D22),
        ])
    if style == "upside_down":
        return _explicit([
            ("a", 0x0250), ("b", "q"), ("c", 0x0254), ("d", "p"), ("e", 0x01DD), ("f", 0x025F), ("g", 0x0183), ("h", 0x0265), ("i", 0x1D09), ("j", 0x027E), ("k", 0x029E), ("l", "l"), ("m", 0x026F), ("n", "u"), ("o", "o"), ("p", "d"), ("q", "b"), ("r", 0x0279), ("s", "s"), ("t", 0x0287), ("u", "n"), ("v", 0x028C), ("w", 0x028D), ("x", "x"), ("y", 0x028E), ("z", "z"),
            ("A", 0x2200), ("C", 0x0186), ("E", 0x018E), ("F", 0x2132), ("G", 0x05E4), ("J", 0x017F), ("L", 0x02E5), ("P", 0x0500), ("T", 0x22A5), ("U", 0x0548), ("V", 0x039B), ("W", "M"), ("Y", 0x2144),
            ("0", "0"), ("1", 0x0196), ("2", 0x1105), ("3", 0x0190), ("4", 0x3123), ("5", 0x03DB), ("6", "9"), ("7", 0x3125), ("8", "8"), ("9", "6"),
        ])
    return {}


def _strip_hidden(value: str) -> str:
    return "".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf")


def _style_options(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    style = safe_str(raw.get("unicodeStyle") or raw.get("unicode_style") or raw.get("font") or "normal").lower().replace("-", "_")
    if style != "normal" and not _unicode_map(style):
        style = "normal"
    scope = safe_str(raw.get("unicodeStyleScope") or raw.get("unicode_style_scope") or raw.get("fontApplyMode") or raw.get("font_apply_mode") or "whole_name").lower().replace("-", "_")
    if scope not in {"whole_name", "text_only"}:
        scope = "whole_name"
    separator = safe_str(raw.get("separator") or chr(0x30FB))
    if separator == "none":
        separator = ""
    return {
        "emoji": None if raw.get("emoji") is None else safe_str(raw.get("emoji")),
        "emoji_position": safe_str(raw.get("emojiPosition") or raw.get("emoji_position") or "first").lower(),
        "separator": separator[:4],
        "case_mode": safe_str(raw.get("caseMode") or raw.get("case_mode") or "lower").lower(),
        "style": style,
        "scope": scope,
    }


def _normalize_base(value: Any, case_mode: str = "lower") -> str:
    cleaned = unicodedata.normalize("NFKC", _strip_hidden(safe_str(value))).replace("&", " and ")
    cleaned = cleaned.replace("'", "").replace("`", "").replace(chr(0x2019), "")
    text = re.sub(r"-+", "-", "".join(ch if ch.isalnum() else "-" for ch in cleaned)).strip("-")
    if case_mode == "preserve":
        return text
    if case_mode == "compact":
        return text.replace("-", "").lower()
    if case_mode == "title":
        return "-".join(part[:1].upper() + part[1:].lower() for part in text.split("-") if part)
    return text.lower()


def _transform(value: str, style: str) -> str:
    mapping = _unicode_map(style)
    if not mapping:
        return value
    return "".join(mapping.get(ch, ch) for ch in value)


def _split_text_decoration(value: Any) -> tuple[str, str, str]:
    normalized = unicodedata.normalize("NFKC", _strip_hidden(safe_str(value)))
    chars = [*normalized]
    first = -1
    last = -1
    for index, char in enumerate(chars):
        if char.isalnum():
            if first < 0:
                first = index
            last = index
    if first < 0 or last < 0:
        return "", normalized, ""
    return "".join(chars[:first]), "".join(chars[first : last + 1]), "".join(chars[last + 1 :])


def format_channel_builder_name(value: Any, options: Any = None) -> str:
    opts = _style_options(options)
    source = safe_str(value)
    if not source:
        return ""
    if opts["scope"] == "text_only":
        prefix, text, suffix = _split_text_decoration(source)
        final = f"{prefix}{_transform(_normalize_base(text or source, opts['case_mode']), opts['style'])}{suffix}"
    else:
        core = _transform(_normalize_base(source, opts["case_mode"]), opts["style"])
        emoji = safe_str(opts.get("emoji"))
        sep = safe_str(opts.get("separator"))
        if emoji and opts.get("emoji_position") != "none":
            final = f"{core}{sep}{emoji}" if opts.get("emoji_position") == "last" else f"{emoji}{sep}{core}"
        else:
            final = core
    return "".join([*_strip_hidden(final)][:100]).rstrip("-" + chr(0x30FB))


def normalize_action(value: Any) -> str:
    text = safe_str(value).lower().replace("-", "_")
    if text in {"create", "rename", "keep", "skip", "conflict"}:
        return text
    return "skip"


def normalize_channel_type(value: Any) -> str:
    text = safe_str(value).lower().replace("announcement", "news")
    if text in {"text", "voice", "forum", "news", "category"}:
        return text
    return "text"


def normalize_channel_builder_items(raw: Any, *, options: Any = None, limit: int = 150) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    base_options = _style_options(options)
    items: list[dict[str, Any]] = []
    for index, row in enumerate(raw[:limit]):
        if not isinstance(row, dict):
            continue
        action = normalize_action(row.get("action"))
        selected = row.get("selected") is not False
        if not selected:
            action = "skip"
        row_options = {**base_options, **_style_options(row.get("options") or row.get("styleOptions") or row.get("style_options"))}
        base_name = safe_str(row.get("baseName") or row.get("base_name") or row.get("name"))[:100]
        final_name = safe_str(row.get("finalName") or row.get("final_name"))[:100]
        if not final_name:
            final_name = format_channel_builder_name(base_name or row.get("currentName") or row.get("current_name"), row_options)
        items.append(
            {
                "index": index,
                "id": safe_str(row.get("id") or f"row-{index + 1}"),
                "action": action,
                "type": normalize_channel_type(row.get("type")),
                "base_name": base_name,
                "final_name": final_name[:100],
                "current_name": safe_str(row.get("currentName") or row.get("current_name"))[:100],
                "current_id": safe_int(
                    row.get("channelId")
                    or row.get("channel_id")
                    or row.get("currentChannelId")
                    or row.get("current_channel_id")
                    or row.get("currentId")
                    or row.get("current_id"),
                    0,
                ),
                "category": safe_str(row.get("category"))[:100],
                "protected": bool(row.get("protected")),
                "selected": selected,
                "unicode_style": row_options.get("style"),
                "unicode_style_scope": row_options.get("scope"),
            }
        )
    return items


def validate_channel_builder_items(items: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    targets: dict[str, int] = {}
    for item in items:
        action = item.get("action")
        if action in {"skip", "keep"}:
            continue
        final_name = safe_str(item.get("final_name"))
        if not final_name:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name required")
            continue
        if len([*final_name]) > 100:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name is over Discord's 100 character limit")
        key = final_name.lower()
        if key in targets:
            errors.append(f"duplicate target name #{final_name}")
        targets[key] = int(item.get("index", 0))
        if action == "conflict":
            errors.append(f"row {int(item.get('index', 0)) + 1}: conflict must be fixed before queueing")
        if action == "rename" and not item.get("current_id") and not item.get("current_name"):
            errors.append(f"row {int(item.get('index', 0)) + 1}: rename requires current channel id or current name")
    return errors[:25]


async def get_guild_or_response(server: Any, guild_id: Any) -> tuple[Optional[discord.Guild], Optional[web.Response]]:
    if hasattr(server, "_get_guild_or_error"):
        return await server._get_guild_or_error(guild_id)
    gid = safe_int(guild_id, 0)
    guild = server.bot.get_guild(gid) if gid else None
    if guild is None:
        return None, server._json_error("Guild not found", 404)
    return guild, None


def channel_kind(channel: Any) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if getattr(discord, "ForumChannel", None) and isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.TextChannel):
        try:
            return "news" if bool(channel.is_news()) else "text"
        except Exception:
            return "text"
    return safe_str(getattr(channel, "type", "unknown"), "unknown")


def channel_payload(channel: Any) -> dict[str, Any]:
    parent = getattr(channel, "category", None)
    return {
        "id": str(getattr(channel, "id", "")),
        "name": safe_str(getattr(channel, "name", "")),
        "type": channel_kind(channel),
        "position": safe_int(getattr(channel, "position", 0), 0),
        "categoryId": str(getattr(parent, "id", "")) if parent else "",
        "categoryName": safe_str(getattr(parent, "name", "")) if parent else "",
    }
