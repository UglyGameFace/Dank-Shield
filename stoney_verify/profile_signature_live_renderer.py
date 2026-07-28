from __future__ import annotations

"""Reference-faithful premium profile-card renderer for Discord."""

import asyncio
import colorsys
import random
import re
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import profile_signature_renderer as _legacy
from .profile_card_service import PLATFORM_SPECS, platform_entry_mode
from .welcome_card_typography_engine import (
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
    render_styled_text_tile,
)

SIGNATURE_WIDTH = 1400
SIGNATURE_HEIGHT = 300
SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT
PLATFORM_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "platform_logos"
_LOGOS: dict[str, bytes] = {}
_EMOJIS: dict[str, bytes] = {}
_EMOJI_MISSES: set[str] = set()


@dataclass(frozen=True)
class ProfilePalette:
    background: tuple[int, int, int]
    panel: tuple[int, int, int]
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]
    text: tuple[int, int, int] = (248, 250, 253)
    muted: tuple[int, int, int] = (197, 207, 220)
    tertiary: tuple[int, int, int] = (120, 220, 255)
    highlight: tuple[int, int, int] = (255, 255, 255)
    motif: str = "leaf"
    focus_platform: str = ""

    @property
    def accents(self) -> tuple[tuple[int, int, int], ...]:
        return self.primary, self.secondary, self.tertiary, self.highlight


PROFILE_THEME_PALETTES: dict[str, ProfilePalette] = {
    "420_lobby": ProfilePalette((2, 8, 4), (4, 15, 8), (137, 255, 76), (37, 211, 111), tertiary=(255, 204, 82), highlight=(225, 255, 215), motif="leaf"),
    "cyber_neon": ProfilePalette((7, 3, 15), (16, 7, 29), (190, 94, 255), (255, 61, 154), tertiary=(45, 232, 205), highlight=(248, 250, 253), motif="smoke"),
    "premium_gold": ProfilePalette((10, 7, 2), (23, 16, 6), (255, 207, 78), (210, 141, 28), tertiary=(255, 78, 68), highlight=(255, 247, 214), motif="flow"),
    "community_glow": ProfilePalette((1, 11, 11), (4, 23, 21), (43, 234, 206), (12, 165, 151), tertiary=(143, 255, 82), highlight=(226, 255, 250), motif="leaf"),
    "esports": ProfilePalette((13, 3, 4), (27, 6, 8), (255, 76, 65), (255, 138, 61), tertiary=(255, 204, 82), highlight=(255, 241, 236), motif="embers"),
    "minimal_glass": ProfilePalette((2, 8, 16), (6, 17, 31), (69, 179, 255), (45, 232, 205), tertiary=(184, 91, 255), highlight=(238, 248, 255), motif="ice"),
    "steam_focus": ProfilePalette((3, 11, 19), (7, 27, 43), (45, 154, 220), (102, 192, 244), tertiary=(123, 135, 152), highlight=(245, 251, 255), motif="steam", focus_platform="steam"),
    "xbox_focus": ProfilePalette((2, 11, 4), (6, 27, 10), (16, 180, 71), (143, 255, 82), tertiary=(45, 232, 205), highlight=(240, 255, 242), motif="xbox", focus_platform="xbox"),
    "playstation_focus": ProfilePalette((2, 7, 18), (6, 17, 38), (37, 108, 229), (70, 177, 255), tertiary=(184, 91, 255), highlight=(242, 247, 255), motif="playstation", focus_platform="playstation"),
    "epic_focus": ProfilePalette((7, 7, 9), (20, 18, 25), (248, 250, 253), (184, 91, 255), tertiary=(45, 232, 205), highlight=(255, 204, 82), motif="epic", focus_platform="epic"),
    "multi_platform": ProfilePalette((4, 6, 15), (12, 15, 34), (45, 232, 205), (255, 61, 154), tertiary=(143, 255, 82), highlight=(255, 204, 82), motif="multi"),
}

THEME_ALIASES: dict[str, str] = {
    "420_lobby": "420_lobby",
    "default": "420_lobby",
    "forest": "420_lobby",
    "cyber_neon": "cyber_neon",
    "purple": "cyber_neon",
    "galaxy": "cyber_neon",
    "premium_gold": "premium_gold",
    "dark": "premium_gold",
    "community_glow": "community_glow",
    "minimal": "community_glow",
    "esports": "esports",
    "sunset": "esports",
    "minimal_glass": "minimal_glass",
    "ocean": "minimal_glass",
    "steam_focus": "steam_focus",
    "xbox_focus": "xbox_focus",
    "playstation_focus": "playstation_focus",
    "epic_focus": "epic_focus",
    "multi_platform": "multi_platform",
}


@dataclass(frozen=True)
class Layout:
    avatar_x: int
    avatar_size: int
    content_x: int
    content_right: int
    name_y: int
    name_size: int
    tags_y: int
    platform_x: int
    brand_x: int


_LAYOUTS = {
    "classic": Layout(48, 210, 278, 790, 57, 60, 203, 820, 1162),
    "minimal": Layout(58, 174, 258, 806, 64, 54, 205, 832, 1168),
    "spotlight": Layout(36, 226, 288, 782, 48, 64, 202, 808, 1154),
}

_EMOJI_BASE_RE = re.compile("[\\U0001F000-\\U0001FAFF\\u2300-\\u23FF\\u2600-\\u27BF\\u2B00-\\u2BFF]")


def _safe(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    text = text.replace("\ufffd", "").replace(" • ", " / ")
    return text[:limit]


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(int(round(x + (y - x) * amount)) for x, y in zip(a, b))


def _font(size: int, *, style_key: str = "clean", custom_font: Optional[bytes] = None, regular: bool = False):
    return _legacy._font(size, style_key=style_key, custom_font=custom_font, regular=regular)


def _avatar_image(payload: bytes, size: tuple[int, int]) -> Optional[Image.Image]:
    return _legacy._avatar_image(payload, size)


def _bright(color: tuple[int, int, int], floor: float = 0.55) -> tuple[int, int, int]:
    red, green, blue = (max(0, min(255, int(v))) / 255 for v in color)
    hue, light, saturation = colorsys.rgb_to_hls(red, green, blue)
    red, green, blue = colorsys.hls_to_rgb(hue, max(floor, min(0.73, light)), max(0.62, saturation))
    return tuple(int(round(v * 255)) for v in (red, green, blue))


def _canonical_theme(style: Mapping[str, Any]) -> str:
    raw = str(style.get("theme") or "default").strip().lower().replace("-", "_")
    return THEME_ALIASES.get(raw, "420_lobby")


def _palette(style: Mapping[str, Any], avatar_bytes: bytes) -> ProfilePalette:
    base = PROFILE_THEME_PALETTES[_canonical_theme(style)]
    mode = str(style.get("color_mode") or "theme").strip().lower()
    primary, secondary, tertiary, highlight = base.accents

    if mode == "custom":
        try:
            primary = parse_hex_color(str(style.get("custom_primary") or "")) or primary
            secondary = parse_hex_color(str(style.get("custom_secondary") or "")) or _mix(primary, (255, 255, 255), 0.28)
            tertiary = parse_hex_color(str(style.get("custom_tertiary") or "")) or _mix(primary, secondary, 0.50)
            highlight = parse_hex_color(str(style.get("custom_highlight") or "")) or _mix(tertiary, (255, 255, 255), 0.58)
        except Exception:
            primary, secondary, tertiary, highlight = base.accents
    elif mode in {"profile", "auto"}:
        sampled_primary, sampled_secondary = _legacy._avatar_colors(avatar_bytes, base.primary)
        strength = 0.12 if mode == "profile" else 0.07
        primary = _mix(base.primary, sampled_primary, strength)
        secondary = _mix(base.secondary, sampled_secondary, strength)
        tertiary = _mix(base.tertiary, sampled_primary, strength / 2)
        highlight = _mix(base.highlight, sampled_secondary, strength / 3)

    return ProfilePalette(
        background=base.background,
        panel=base.panel,
        primary=_bright(tuple(primary)),
        secondary=_bright(tuple(secondary), 0.52),
        tertiary=_bright(tuple(tertiary), 0.50),
        highlight=_bright(tuple(highlight), 0.68),
        text=base.text,
        muted=base.muted,
        motif=base.motif,
        focus_platform=base.focus_platform,
    )

def _linear_background(palette: ProfilePalette) -> Image.Image:
    image = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), palette.background + (255,))
    draw = ImageDraw.Draw(image)
    middle = _mix(palette.background, palette.panel, 0.82)
    right = _mix(palette.background, palette.primary, 0.08)
    for x in range(SIGNATURE_WIDTH):
        ratio = x / max(1, SIGNATURE_WIDTH - 1)
        if ratio < 0.68:
            color = _mix(palette.background, middle, ratio / 0.68)
        else:
            color = _mix(middle, right, (ratio - 0.68) / 0.32)
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=color + (255,))
    return image


def _background(style: Mapping[str, Any], palette: ProfilePalette, avatar_bytes: bytes) -> Image.Image:
    mode = str(style.get("background_mode") or "theme").strip().lower()
    payload = bytes(style.get("custom_background") or b"") if mode == "custom" else avatar_bytes if mode == "profile" else b""
    if payload:
        found = _avatar_image(payload, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if found is not None:
            if mode == "profile":
                found = found.filter(ImageFilter.GaussianBlur(30))
            return Image.alpha_composite(found, Image.new("RGBA", found.size, palette.background + (205,)))
    return _linear_background(palette)


def _draw_leaf(draw: ImageDraw.ImageDraw, cx: int, cy: int, scale: float, color: tuple[int, int, int, int]) -> None:
    draw.line((cx, cy + int(42 * scale), cx, cy - int(44 * scale)), fill=color, width=max(1, int(3 * scale)))
    for ox, oy, radius in ((0, -64, 15), (-28, -45, 13), (28, -45, 13), (-49, -21, 11), (49, -21, 11), (-31, 4, 10), (31, 4, 10)):
        tx, ty = cx + int(ox * scale), cy + int(oy * scale)
        r = int(radius * scale)
        draw.polygon([(cx, cy + int(10 * scale)), (tx - r, ty + int(9 * scale)), (tx, ty - r), (tx + r, ty + int(9 * scale))], fill=color)


def _draw_wisps(image: Image.Image, palette: ProfilePalette, seed: int) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    rng = random.Random(seed)
    accents = palette.accents
    for index in range(16):
        left = 360 + rng.randint(-40, 680)
        top = rng.randint(-120, 220)
        width = rng.randint(240, 570)
        height = rng.randint(90, 270)
        color = accents[index % len(accents)]
        draw.arc(
            (left, top, left + width, top + height),
            rng.randint(150, 205),
            rng.randint(300, 355),
            fill=color + (24 + (index % 3) * 5,),
            width=rng.randint(3, 8),
        )
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(11)))


def _draw_particles(image: Image.Image, palette: ProfilePalette, seed: int, *, icy: bool = False) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    rng = random.Random(seed)
    accents = palette.accents
    for index in range(105):
        x = rng.randint(410, 1370)
        y = rng.randint(18, 282)
        radius = rng.randint(1, 3 if icy else 4)
        color = accents[index % len(accents)]
        alpha = rng.randint(18, 82)
        if icy:
            draw.line((x - radius * 2, y, x + radius * 2, y), fill=color + (alpha,), width=1)
            draw.line((x, y - radius * 2, x, y + radius * 2), fill=color + (alpha,), width=1)
        else:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color + (alpha,))
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.6)))


def _texture(image: Image.Image, palette: ProfilePalette, layout: str, theme_key: str) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    if layout == "minimal":
        draw.line((32, 30, 1138, 30), fill=palette.primary + (92,), width=2)
        draw.line((32, 270, 1138, 270), fill=palette.tertiary + (52,), width=1)

    if palette.motif == "leaf":
        for index, (x, y, scale) in enumerate(((520, 122, 0.78), (712, 250, 1.04), (980, 112, 0.78))):
            _draw_leaf(draw, x, y, scale, palette.accents[index % 4] + (22,))
        _draw_wisps(image, palette, 420 if theme_key == "420_lobby" else 1337)
    elif palette.motif == "smoke":
        _draw_wisps(image, palette, 690)
        for index, radius in enumerate((64, 104, 152, 202)):
            draw.ellipse(
                (925 - radius, 145 - radius, 925 + radius, 145 + radius),
                outline=palette.accents[index % 4] + (20,),
                width=2,
            )
    elif palette.motif == "flow":
        for index, offset in enumerate(range(-180, 520, 65)):
            draw.arc(
                (430 + offset, -170, 1120 + offset, 430),
                178,
                350,
                fill=palette.accents[index % 4] + (32,),
                width=3,
            )
        _draw_particles(image, palette, 100)
    elif palette.motif == "embers":
        for index, x in enumerate(range(500, 1140, 86)):
            draw.polygon(
                [(x, 300), (x + 42, 300), (x + 230, 0), (x + 188, 0)],
                fill=palette.accents[index % 4] + (15,),
            )
        _draw_particles(image, palette, 77)
    elif palette.motif == "ice":
        for index, x in enumerate(range(470, 1100, 90)):
            draw.line((x, 300, x + 170, 0), fill=palette.accents[index % 4] + (20,), width=2)
            draw.line((x + 20, 300, x + 190, 0), fill=palette.highlight + (11,), width=1)
        _draw_particles(image, palette, 55, icy=True)
    elif palette.motif == "steam":
        for index, radius in enumerate((42, 78, 118, 164)):
            draw.ellipse((915 - radius, 145 - radius, 915 + radius, 145 + radius), outline=palette.accents[index % 4] + (28,), width=3)
        draw.line((760, 230, 1070, 70), fill=palette.highlight + (35,), width=8)
        _draw_particles(image, palette, 145)
    elif palette.motif == "xbox":
        for index, offset in enumerate(range(-50, 250, 55)):
            color = palette.accents[index % 4] + (24,)
            draw.arc((675 + offset, -125, 1115 + offset, 315), 212, 328, fill=color, width=8)
            draw.arc((675 + offset, -125, 1115 + offset, 315), 32, 148, fill=color, width=8)
        _draw_particles(image, palette, 360)
    elif palette.motif == "playstation":
        for index, x in enumerate(range(560, 1130, 95)):
            color = palette.accents[index % 4] + (25,)
            draw.line((x, 245, x + 120, 55), fill=color, width=5)
            draw.rectangle((x + 18, 65, x + 76, 123), outline=color, width=3)
        _draw_particles(image, palette, 1994, icy=True)
    elif palette.motif == "epic":
        for index, x in enumerate(range(500, 1120, 110)):
            color = palette.accents[index % 4] + (24,)
            draw.polygon([(x, 25), (x + 80, 25), (x + 20, 275), (x - 60, 275)], fill=color)
        _draw_particles(image, palette, 2017)
    elif palette.motif == "multi":
        for index, y in enumerate((48, 94, 140, 186, 232)):
            draw.line((470, y, 1135, y - 28), fill=palette.accents[index % 4] + (30,), width=10)
        _draw_particles(image, palette, 404)

    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.4)))

def _is_emoji_base(ch: str) -> bool:
    code = ord(ch)
    return bool(
        _EMOJI_BASE_RE.fullmatch(ch)
        or 0x1F1E6 <= code <= 0x1F1FF
        or ch in "©®™"
    )


def _consume_emoji(text: str, index: int) -> tuple[str, int]:
    if index >= len(text):
        return "", index
    ch = text[index]
    if ch in "#*0123456789":
        cursor = index + 1
        if cursor < len(text) and text[cursor] == "\ufe0f":
            cursor += 1
        if cursor < len(text) and text[cursor] == "\u20e3":
            return text[index : cursor + 1], cursor + 1
        return "", index

    code = ord(ch)
    if 0x1F1E6 <= code <= 0x1F1FF:
        cursor = index + 1
        if cursor < len(text) and 0x1F1E6 <= ord(text[cursor]) <= 0x1F1FF:
            cursor += 1
        return text[index:cursor], cursor

    if not _is_emoji_base(ch):
        return "", index

    cursor = index + 1

    def consume_suffix(position: int) -> int:
        if position < len(text) and text[position] == "\ufe0f":
            position += 1
        if position < len(text) and 0x1F3FB <= ord(text[position]) <= 0x1F3FF:
            position += 1
        while position < len(text) and 0xE0020 <= ord(text[position]) <= 0xE007E:
            position += 1
        if position < len(text) and ord(text[position]) == 0xE007F:
            position += 1
        return position

    cursor = consume_suffix(cursor)
    while cursor + 1 < len(text) and text[cursor] == "\u200d" and _is_emoji_base(text[cursor + 1]):
        cursor += 2
        cursor = consume_suffix(cursor)
    return text[index:cursor], cursor


def _emoji_sequences(text: str) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(text):
        sequence, next_index = _consume_emoji(text, index)
        if sequence:
            out.append(sequence)
            index = next_index
        else:
            index += 1
    return out


def _emoji_tokens(text: str) -> list[tuple[str, bool]]:
    found = sorted(set(_emoji_sequences(text)), key=len, reverse=True)
    if not found:
        return [(text, False)] if text else []
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        match = next((emoji for emoji in found if text.startswith(emoji, index)), None)
        if match:
            tokens.append((match, True))
            index += len(match)
            continue
        start = index
        index += 1
        while index < len(text) and not any(text.startswith(emoji, index) for emoji in found):
            index += 1
        tokens.append((text[start:index], False))
    return tokens


def _twemoji_code(emoji: str) -> str:
    return "-".join(f"{ord(ch):x}" for ch in emoji if ord(ch) != 0xFE0F)


async def _fetch_emoji(session: aiohttp.ClientSession, emoji: str) -> bytes:
    if emoji in _EMOJIS:
        return _EMOJIS[emoji]
    if emoji in _EMOJI_MISSES:
        return b""
    code = _twemoji_code(emoji)
    urls = (
        f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{code}.png",
        f"https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/72x72/{code}.png",
        f"https://raw.githubusercontent.com/jdecked/twemoji/main/assets/72x72/{code}.png",
    )
    for url in urls:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    payload = await response.read()
                    if payload:
                        with Image.open(BytesIO(payload)) as opened:
                            opened.verify()
                        _EMOJIS[emoji] = payload
                        return payload
        except Exception:
            continue
    _EMOJI_MISSES.add(emoji)
    return b""


async def _emoji_assets(values: Sequence[Any]) -> dict[str, bytes]:
    emojis: list[str] = []
    for value in values:
        for emoji in _emoji_sequences(str(value or "")):
            if emoji not in emojis and len(emojis) < 64:
                emojis.append(emoji)
    missing = [emoji for emoji in emojis if emoji not in _EMOJIS and emoji not in _EMOJI_MISSES]
    if missing:
        timeout = aiohttp.ClientTimeout(total=8.0, connect=3.0)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "DankShield-ProfileRenderer/1.0"},
            ) as session:
                await asyncio.gather(*(_fetch_emoji(session, emoji) for emoji in missing))
        except Exception:
            pass
    return {emoji: _EMOJIS[emoji] for emoji in emojis if emoji in _EMOJIS}

def _rich_width(draw: ImageDraw.ImageDraw, text: str, font: Any, assets: Mapping[str, bytes], emoji_size: int) -> int:
    width = 0
    for token, is_emoji in _emoji_tokens(text):
        if is_emoji:
            width += emoji_size
        elif token:
            box = draw.textbbox((0, 0), token, font=font)
            width += box[2] - box[0]
    return width


def _fit(draw: ImageDraw.ImageDraw, value: Any, font: Any, max_width: int, assets: Mapping[str, bytes], emoji_size: int, limit: int = 160) -> str:
    clean = _safe(value, limit)
    if _rich_width(draw, clean, font, assets, emoji_size) <= max_width:
        return clean
    ellipsis = "…"
    low, high = 0, len(clean)
    while low < high:
        middle = (low + high + 1) // 2
        if _rich_width(draw, clean[:middle].rstrip() + ellipsis, font, assets, emoji_size) <= max_width:
            low = middle
        else:
            high = middle - 1
    return clean[:low].rstrip() + ellipsis if low else ""


def _complete_lines(
    draw: ImageDraw.ImageDraw,
    value: Any,
    *,
    max_width: int,
    max_lines: int,
    assets: Mapping[str, bytes],
    start_size: int,
    min_size: int,
    limit: int = 240,
) -> tuple[Any, list[str], int]:
    # Fit every character by shrinking/wrapping; never return ellipsis.
    clean = _safe(value, limit)
    if not clean:
        return None, [], min_size
    for size in range(start_size, min_size - 1, -1):
        font = _font(size, regular=True)
        remaining = clean
        lines: list[str] = []
        while remaining and len(lines) < max_lines:
            if _rich_width(draw, remaining, font, assets, size) <= max_width:
                lines.append(remaining)
                remaining = ""
                break
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if _rich_width(draw, remaining[:middle], font, assets, size) <= max_width:
                    low = middle
                else:
                    high = middle - 1
            split_at = max(1, low)
            space_at = remaining.rfind(" ", 0, split_at + 1)
            if space_at > 0:
                line = remaining[:space_at].rstrip()
                remaining = remaining[space_at + 1 :].lstrip()
            else:
                line = remaining[:split_at]
                remaining = remaining[split_at:]
            if not line:
                break
            lines.append(line)
        if lines and not remaining:
            return font, lines, size
    return None, [], min_size


def _draw_complete(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: Any,
    *,
    max_width: int,
    max_lines: int,
    assets: Mapping[str, bytes],
    start_size: int,
    min_size: int,
    fill: tuple[int, int, int, int],
    limit: int = 240,
    line_gap: int = 2,
) -> int:
    font, lines, size = _complete_lines(
        draw,
        value,
        max_width=max_width,
        max_lines=max_lines,
        assets=assets,
        start_size=start_size,
        min_size=min_size,
        limit=limit,
    )
    if font is None:
        return 0
    x, y = xy
    for index, line in enumerate(lines):
        _draw_rich(
            image,
            draw,
            (x, y + index * (size + line_gap)),
            line,
            font=font,
            fill=fill,
            assets=assets,
            emoji_size=size,
        )
    return len(lines) * size + max(0, len(lines) - 1) * line_gap


def _draw_rich(image: Image.Image, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, font: Any, fill: tuple[int, int, int, int], assets: Mapping[str, bytes], emoji_size: int) -> int:
    x, y = xy
    for token, is_emoji in _emoji_tokens(text):
        if is_emoji:
            payload = assets.get(token, b"")
            if payload:
                try:
                    with Image.open(BytesIO(payload)) as opened:
                        icon = ImageOps.contain(opened.convert("RGBA"), (emoji_size, emoji_size), Image.Resampling.LANCZOS)
                    image.alpha_composite(icon, (x, y + 1))
                    x += emoji_size
                    continue
                except Exception:
                    pass
            radius = max(3, emoji_size // 5)
            cy = y + emoji_size // 2
            draw.ellipse((x + 2, cy - radius, x + 2 + radius * 2, cy + radius), fill=fill)
            x += emoji_size
            continue
        draw.text((x, y), token, font=font, fill=fill)
        box = draw.textbbox((0, 0), token, font=font)
        x += box[2] - box[0]
    return x


def _asset_tile(payload: bytes, size: int, fallback: str) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    try:
        with Image.open(BytesIO(payload)) as source:
            icon = ImageOps.contain(source.convert("RGBA"), (size - 14, size - 14), Image.Resampling.LANCZOS)
        tile.alpha_composite(icon, ((size - icon.width) // 2, (size - icon.height) // 2))
        return tile
    except Exception:
        pass
    draw = ImageDraw.Draw(tile)
    font = _font(max(14, size // 5), regular=True)
    label = _safe(fallback, 3).upper() or "?"
    box = draw.textbbox((0, 0), label, font=font)
    draw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 2), label, font=font, fill=(240, 244, 250, 255))
    return tile


def _logo_bytes(platform: str) -> bytes:
    if platform not in _LOGOS:
        try:
            _LOGOS[platform] = (PLATFORM_LOGO_DIR / f"{platform}.png").read_bytes()
        except Exception:
            _LOGOS[platform] = b""
    return _LOGOS[platform]


def _draw_card_frame(image: Image.Image, palette: ProfilePalette) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow, "RGBA")
    gdraw.rounded_rectangle((18, 18, 1382, 282), radius=28, outline=palette.primary + (120,), width=7)
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(12)))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((18, 18, 1382, 282), radius=28, outline=palette.primary + (175,), width=2)
    draw.rounded_rectangle((22, 22, 1378, 278), radius=25, outline=palette.highlight + (36,), width=1)


def _draw_avatar(image: Image.Image, avatar_bytes: bytes, display_name: str, palette: ProfilePalette, spec: Layout, frame: str) -> None:
    size = spec.avatar_size
    x, y = spec.avatar_x, (SIGNATURE_HEIGHT - size) // 2
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow, "RGBA")
    if frame == "glow":
        gdraw.ellipse((x - 14, y - 14, x + size + 14, y + size + 14), fill=palette.primary + (115,))
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    draw = ImageDraw.Draw(image, "RGBA")
    if frame != "none":
        draw.ellipse((x - 8, y - 8, x + size + 8, y + size + 8), fill=(1, 4, 6, 225), outline=palette.primary + (250,), width=4 if frame == "glow" else 3)
        draw.ellipse((x - 2, y - 2, x + size + 2, y + size + 2), outline=palette.tertiary + (150,), width=2)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    avatar = _avatar_image(avatar_bytes, (size, size))
    if avatar is None:
        avatar = Image.new("RGBA", (size, size), _mix(palette.panel, palette.primary, 0.25) + (255,))
        adraw = ImageDraw.Draw(avatar)
        initial = (_safe(display_name, 1) or "?").upper()
        font = _font(max(42, size // 2))
        box = adraw.textbbox((0, 0), initial, font=font)
        adraw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 8), initial, font=font, fill=(255, 255, 255, 255))
    image.paste(avatar, (x, y), mask)


def _bounded_name_tile(
    name: str,
    *,
    style_key: str,
    spec: Layout,
    palette: ProfilePalette,
    custom_font_bytes: bytes,
) -> Image.Image:
    max_width = max(120, spec.content_right - spec.content_x - 24)
    max_height = 76
    tile = render_styled_text_tile(
        name,
        style_key=style_key,
        start_size=spec.name_size,
        min_size=30,
        max_width=max_width - 8,
        max_height=max_height,
        primary=palette.primary,
        secondary=palette.secondary,
        role="name",
        custom_font_bytes=custom_font_bytes,
    )
    bounds = tile.getbbox()
    cropped = tile.crop(bounds) if bounds else tile
    if cropped.width > max_width or cropped.height > max_height:
        cropped.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (max_width, max_height), (0, 0, 0, 0))
    layer.alpha_composite(cropped, (0, max(0, (max_height - cropped.height) // 2)))
    return layer

def _draw_meta_dates(image: Image.Image, dates: Sequence[str], palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes]) -> None:
    values = [_safe(value, 70) for value in dates if _safe(value, 70)][:2]
    if not values:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(18, regular=True)
    x, y = spec.content_x, 151
    for index, value in enumerate(values):
        fitted = _fit(draw, value, font, 245 if index == 0 else max(100, spec.content_right - x), assets, 18, 70)
        x = _draw_rich(image, draw, (x, y), fitted, font=font, fill=palette.muted + (245,), assets=assets, emoji_size=18)
        if index < len(values) - 1:
            x += 13
            draw.ellipse((x, y + 9, x + 6, y + 15), fill=palette.primary + (220,))
            x += 19


def _draw_pills(image: Image.Image, labels: Sequence[tuple[str, tuple[int, int, int]]], palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x, y = spec.content_x, spec.tags_y
    row = 0
    for raw, accent in labels:
        clean = _safe(raw, 110)
        if not clean:
            continue
        row_width = spec.content_right - spec.content_x
        font = None
        size = 17
        width = 0
        for candidate in range(17, 10, -1):
            trial = _font(candidate, regular=True)
            trial_width = _rich_width(draw, clean, trial, assets, candidate) + 28
            if trial_width <= row_width:
                font, size, width = trial, candidate, trial_width
                break
        if font is None:
            continue
        if x + width > spec.content_right and x > spec.content_x:
            row += 1
            if row >= 2:
                break
            x, y = spec.content_x, y + 39
        if x + width > spec.content_right:
            continue
        fill = _mix((3, 7, 10), accent, 0.12)
        draw.rounded_rectangle((x, y, x + width, y + 32), radius=14, fill=fill + (224,), outline=accent + (185,), width=2)
        _draw_rich(image, draw, (x + 14, y + max(3, (32 - size) // 2 - 1)), clean, font=font, fill=palette.text + (248,), assets=assets, emoji_size=size)
        x += width + 8


def _role_icon(draw: ImageDraw.ImageDraw, x: int, y: int, palette: ProfilePalette, theme_key: str) -> None:
    color = palette.primary + (255,)
    if theme_key == "premium_gold":
        draw.polygon([(x, y + 11), (x + 5, y + 3), (x + 11, y + 10), (x + 17, y + 2), (x + 22, y + 11), (x + 20, y + 18), (x + 2, y + 18)], fill=color)
    elif theme_key == "esports":
        draw.polygon([(x + 11, y), (x + 20, y + 13), (x + 13, y + 20), (x + 9, y + 13), (x + 2, y + 19), (x + 4, y + 8)], fill=color)
    elif theme_key == "minimal_glass":
        draw.line((x + 11, y, x + 11, y + 22), fill=color, width=2)
        draw.line((x, y + 11, x + 22, y + 11), fill=color, width=2)
        draw.line((x + 3, y + 3, x + 19, y + 19), fill=color, width=2)
        draw.line((x + 19, y + 3, x + 3, y + 19), fill=color, width=2)
    else:
        _draw_leaf(draw, x + 11, y + 14, 0.18, color)


def _draw_platforms(
    image: Image.Image,
    entries: Sequence[Mapping[str, Any]],
    logos: Mapping[str, bytes],
    role: str,
    palette: ProfilePalette,
    spec: Layout,
    assets: Mapping[str, bytes],
    theme_key: str,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    role_max = max(150, spec.brand_x - spec.platform_x - 24)
    role_font, role_lines, role_size = _complete_lines(
        draw,
        role.upper() or "MEMBER",
        max_width=role_max - 58,
        max_lines=2,
        assets=assets,
        start_size=17,
        min_size=10,
        limit=100,
    )
    if role_font is None:
        role_font, role_lines, role_size = _font(15, regular=True), ["MEMBER"], 15
    role_width = min(role_max, max(_rich_width(draw, line, role_font, assets, role_size) for line in role_lines) + 58)
    role_fill = _mix((3, 7, 10), palette.primary, 0.13)
    draw.rounded_rectangle(
        (spec.platform_x, 32, spec.platform_x + role_width, 82),
        radius=18,
        fill=role_fill + (225,),
        outline=palette.primary + (185,),
        width=2,
    )
    _role_icon(draw, spec.platform_x + 13, 46, palette, theme_key)
    role_y = 40 if len(role_lines) == 1 else 35
    for index, line in enumerate(role_lines):
        _draw_rich(
            image,
            draw,
            (spec.platform_x + 43, role_y + index * (role_size + 1)),
            line,
            font=role_font,
            fill=palette.highlight + (255,),
            assets=assets,
            emoji_size=role_size,
        )

    normalized = [dict(entry) for entry in entries if isinstance(entry, Mapping)]
    focus = palette.focus_platform
    if focus:
        focus_spec = PLATFORM_SPECS.get(focus)
        focus_entry = next((entry for entry in normalized if str(entry.get("platform") or "") == focus), {})
        size = 92
        x, y = spec.platform_x, 91
        accent = palette.primary
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow, "RGBA").rounded_rectangle(
            (x - 5, y - 5, x + size + 5, y + size + 5),
            radius=24,
            fill=accent + (70,),
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle(
            (x, y, x + size, y + size),
            radius=22,
            fill=(3, 6, 10, 236),
            outline=palette.highlight + (190,),
            width=3,
        )
        focus_logo = bytes(logos.get(focus) or _logo_bytes(focus))
        image.alpha_composite(
            _asset_tile(focus_logo, size - 12, getattr(focus_spec, "label", focus)),
            (x + 6, y + 6),
        )
        label_font = _font(15, regular=True)
        label = f"{getattr(focus_spec, 'label', focus).upper()} FOCUS"
        draw.text((x + size + 16, y + 5), label, font=label_font, fill=palette.primary + (255,))
        username = _safe(focus_entry.get("username"), 80)
        username_text = username if username and platform_entry_mode(focus_entry) != "logo" else "Platform-focused style"
        _draw_complete(
            image,
            draw,
            (x + size + 16, y + 31),
            username_text,
            max_width=max(100, spec.brand_x - (x + size + 16) - 20),
            max_lines=2,
            assets=assets,
            start_size=18,
            min_size=10,
            fill=palette.highlight + (250,),
            limit=100,
        )
        others = [entry for entry in normalized if str(entry.get("platform") or "") != focus][:3]
        small_x = x + size + 16
        small_y = y + 68
        for index, entry in enumerate(others):
            platform = str(entry.get("platform") or "")
            platform_spec = PLATFORM_SPECS.get(platform)
            if platform_spec is None:
                continue
            tile_size = 38
            tile_accent = palette.accents[(index + 1) % 4]
            draw.rounded_rectangle(
                (small_x, small_y, small_x + tile_size, small_y + tile_size),
                radius=10,
                fill=(3, 6, 10, 225),
                outline=tile_accent + (150,),
                width=2,
            )
            image.alpha_composite(
                _asset_tile(bytes(logos.get(platform) or _logo_bytes(platform)), tile_size - 6, platform_spec.label),
                (small_x + 3, small_y + 3),
            )
            small_x += 46
        return

    if theme_key == "multi_platform":
        x, y, size = spec.platform_x, 91, 52
        for index, entry in enumerate(normalized[:4]):
            platform = str(entry.get("platform") or "")
            platform_spec = PLATFORM_SPECS.get(platform)
            if platform_spec is None:
                continue
            column, row = index % 2, index // 2
            tx = x + column * 64
            ty = y + row * 64
            accent = palette.accents[index % 4]
            draw.rounded_rectangle(
                (tx, ty, tx + size, ty + size),
                radius=14,
                fill=(3, 6, 10, 226),
                outline=accent + (175,),
                width=2,
            )
            image.alpha_composite(
                _asset_tile(bytes(logos.get(platform) or _logo_bytes(platform)), size - 8, platform_spec.label),
                (tx + 4, ty + 4),
            )
        label_font = _font(15, regular=True)
        draw.text((x + 140, y + 12), "MULTI-PLATFORM", font=label_font, fill=palette.highlight + (245,))
        draw.text((x + 140, y + 42), "PLAYER GRID", font=label_font, fill=palette.tertiary + (245,))
        return

    x, y, size = spec.platform_x, 91, 57
    shared: list[str] = []
    for index, entry in enumerate(normalized[:5]):
        platform = str(entry.get("platform") or "")
        platform_spec = PLATFORM_SPECS.get(platform)
        if platform_spec is None:
            continue
        accent = palette.accents[index % 4]
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow, "RGBA")
        sdraw.rounded_rectangle((x - 2, y - 2, x + size + 2, y + size + 2), radius=15, fill=accent + (42,))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle((x, y, x + size, y + size), radius=14, fill=(3, 6, 10, 226), outline=accent + (135,), width=2)
        image.alpha_composite(
            _asset_tile(bytes(logos.get(platform) or _logo_bytes(platform)), size - 8, platform_spec.label),
            (x + 4, y + 4),
        )
        username = _safe(entry.get("username"), 80)
        if username and platform_entry_mode(entry) != "logo":
            shared.append(f"{platform_spec.label}: {username}")
        x += 66

    if shared:
        draw = ImageDraw.Draw(image, "RGBA")
        line_y = 164
        max_width = max(120, spec.brand_x - spec.platform_x - 24)
        for value in shared[:2]:
            used = _draw_complete(
                image,
                draw,
                (spec.platform_x, line_y),
                value,
                max_width=max_width,
                max_lines=2,
                assets=assets,
                start_size=16,
                min_size=10,
                fill=palette.tertiary + (255,),
                limit=120,
            )
            if used:
                line_y += used + 5

def _draw_brand(image: Image.Image, server_name: str, icon_bytes: bytes, palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes], theme_key: str) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x = spec.brand_x
    draw.polygon([(x, 20), (1382, 20), (1382, 280), (x - 48, 280)], fill=(2, 4, 7, 208))
    draw.line((x, 24, x - 44, 276), fill=palette.primary + (230,), width=3)
    draw.line((x + 7, 24, x - 37, 276), fill=palette.tertiary + (92,), width=1)
    if theme_key in {"420_lobby", "community_glow"}:
        _draw_leaf(draw, 1290, 146, 0.74, palette.primary + (22,))

    size, icon_x, icon_y = 96, x + 43, 49
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow, "RGBA")
    gdraw.rounded_rectangle((icon_x - 8, icon_y - 8, icon_x + size + 8, icon_y + size + 8), radius=23, fill=palette.primary + (55,))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((icon_x - 5, icon_y - 5, icon_x + size + 5, icon_y + size + 5), radius=21, fill=(4, 7, 11, 222), outline=palette.primary + (165,), width=2)
    image.alpha_composite(_asset_tile(icon_bytes, size, server_name), (icon_x, icon_y))

    draw = ImageDraw.Draw(image, "RGBA")
    small = _font(13, regular=True)
    small_text = "COMMUNITY"
    small_width = draw.textbbox((0, 0), small_text, font=small)[2]
    draw.text((icon_x + (size - small_width) / 2, 160), small_text, font=small, fill=palette.highlight + (235,))
    font, lines, brand_size = _complete_lines(
        draw,
        server_name,
        max_width=176,
        max_lines=2,
        assets=assets,
        start_size=18,
        min_size=11,
        limit=100,
    )
    if font is not None:
        for index, line in enumerate(lines):
            width = _rich_width(draw, line, font, assets, brand_size)
            _draw_rich(
                image,
                draw,
                (icon_x + (size - width) // 2, 182 + index * (brand_size + 2)),
                line,
                font=font,
                fill=palette.text + (248,),
                assets=assets,
                emoji_size=brand_size,
            )


def render_profile_signature(
    *,
    avatar_bytes: bytes,
    display_name: str,
    server_name: str,
    role_labels: Sequence[str] = (),
    date_labels: Sequence[str] = (),
    platform_labels: Sequence[str] = (),
    style: Mapping[str, Any],
    server_role_labels: Sequence[str] = (),
    profile_tag_labels: Sequence[str] = (),
    platform_entries: Sequence[Mapping[str, Any]] = (),
    platform_logo_bytes: Optional[Mapping[str, bytes]] = None,
    guild_icon_bytes: bytes = b"",
    emoji_assets: Optional[Mapping[str, bytes]] = None,
    show_server_branding: bool = True,
) -> bytes:
    palette = _palette(style, avatar_bytes)
    theme_key = _canonical_theme(style)
    layout_key = str(style.get("layout") or "classic").lower()
    spec = _LAYOUTS.get(layout_key, _LAYOUTS["classic"])
    if not show_server_branding:
        spec = replace(spec, brand_x=1370)
    frame = str(style.get("avatar_frame") or "glow").lower()
    frame = frame if frame in {"glow", "ring", "none"} else "glow"
    style_key = str(style.get("font") or "clean")
    if style_key not in FONT_STYLES and style_key != CUSTOM_FONT_STYLE_KEY:
        style_key = "clean"
    assets = dict(emoji_assets or {})

    image = _background(style, palette, avatar_bytes)
    veil = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(veil, "RGBA").rounded_rectangle((18, 18, 1382, 282), radius=28, fill=palette.panel + (126,))
    image = Image.alpha_composite(image, veil)
    _texture(image, palette, layout_key, theme_key)
    _draw_card_frame(image, palette)
    _draw_avatar(image, avatar_bytes, display_name, palette, spec, frame)

    draw = ImageDraw.Draw(image, "RGBA")
    eyebrow_font = _font(17, regular=True)
    eyebrow_value = server_name.upper() if show_server_branding else "MEMBER PROFILE"
    eyebrow = _fit(draw, eyebrow_value, eyebrow_font, spec.content_right - spec.content_x, assets, 18, 70)
    _draw_rich(image, draw, (spec.content_x, 42), eyebrow, font=eyebrow_font, fill=palette.primary + (255,), assets=assets, emoji_size=18)

    name = _safe(display_name, 72) or "Member"
    name_max_width = max(120, spec.content_right - spec.content_x - 24)
    if _emoji_sequences(name):
        font = _font(spec.name_size)
        fitted = _fit(draw, name, font, name_max_width, assets, 50, 72)
        name_layer = Image.new("RGBA", (name_max_width, 76), (0, 0, 0, 0))
        name_draw = ImageDraw.Draw(name_layer, "RGBA")
        _draw_rich(
            name_layer,
            name_draw,
            (0, 6),
            fitted,
            font=font,
            fill=palette.text + (255,),
            assets=assets,
            emoji_size=50,
        )
        image.alpha_composite(name_layer, (spec.content_x, spec.name_y - 6))
    else:
        name_tile = _bounded_name_tile(
            name,
            style_key=style_key,
            spec=spec,
            palette=palette,
            custom_font_bytes=bytes(style.get("custom_font") or b""),
        )
        image.alpha_composite(name_tile, (spec.content_x, spec.name_y - 6))

    _draw_meta_dates(image, date_labels, palette, spec, assets)

    server_roles = [_safe(value, 80) for value in server_role_labels if _safe(value, 80)]
    tags = [_safe(value, 110) for value in profile_tag_labels if _safe(value, 110)]
    pills: list[tuple[str, tuple[int, int, int]]] = []
    for index, value in enumerate(tags[:4]):
        pills.append((value, palette.accents[index % 4]))
    for index, value in enumerate(server_roles[1:3], start=len(pills)):
        pills.append((f"Role: {value}", palette.accents[index % 4]))
    for value in role_labels[:2]:
        if value not in {item[0] for item in pills}:
            pills.append((value, palette.accents[len(pills) % 4]))
    if not pills and not server_roles:
        pills.append(("Private profile", palette.primary))
    _draw_pills(image, pills, palette, spec, assets)

    entries = [dict(entry) for entry in platform_entries if isinstance(entry, Mapping)]
    _draw_platforms(
        image,
        entries,
        dict(platform_logo_bytes or {}),
        server_roles[0] if server_roles else "Member",
        palette,
        spec,
        assets,
        theme_key,
    )
    if show_server_branding:
        _draw_brand(image, server_name, guild_icon_bytes, palette, spec, assets, theme_key)

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


async def _avatar_bytes(member: discord.Member) -> bytes:
    try:
        try:
            asset = member.display_avatar.replace(size=256, format="png")
        except TypeError:
            asset = member.display_avatar.with_size(256).with_format("png")
        return await asset.read()
    except Exception:
        return b""


async def _guild_icon_bytes(member: discord.Member) -> bytes:
    icon = getattr(getattr(member, "guild", None), "icon", None)
    if icon is None:
        return b""
    try:
        try:
            asset = icon.replace(size=256, format="png")
        except TypeError:
            asset = icon.with_size(256).with_format("png")
        return await asset.read()
    except Exception:
        return b""


async def render_member_profile_signature(
    member: discord.Member,
    *,
    style: Mapping[str, Any],
    role_labels: Sequence[str] = (),
    date_labels: Sequence[str] = (),
    platform_labels: Sequence[str] = (),
    server_role_labels: Sequence[str] = (),
    profile_tag_labels: Sequence[str] = (),
    platform_entries: Sequence[Mapping[str, Any]] = (),
    show_server_branding: bool = True,
) -> bytes:
    entries = [dict(entry) for entry in platform_entries if isinstance(entry, Mapping)]
    display_name = getattr(member, "display_name", None) or str(member)
    server_name = getattr(getattr(member, "guild", None), "name", None) or "Discord Server"
    values: list[Any] = [display_name, server_name, *role_labels, *date_labels, *platform_labels, *server_role_labels, *profile_tag_labels]
    values.extend(entry.get("username") for entry in entries)
    avatar, guild_icon, emojis = await asyncio.gather(_avatar_bytes(member), _guild_icon_bytes(member), _emoji_assets(values))
    logos = {key: _logo_bytes(key) for key in (str(entry.get("platform") or "") for entry in entries[:5]) if key}
    return await asyncio.to_thread(
        render_profile_signature,
        avatar_bytes=avatar,
        display_name=display_name,
        server_name=server_name,
        role_labels=list(role_labels),
        date_labels=list(date_labels),
        platform_labels=list(platform_labels),
        style=dict(style or {}),
        server_role_labels=list(server_role_labels),
        profile_tag_labels=list(profile_tag_labels),
        platform_entries=entries,
        platform_logo_bytes=logos,
        guild_icon_bytes=guild_icon,
        emoji_assets=emojis,
        show_server_branding=show_server_branding,
    )


__all__ = [
    "PROFILE_THEME_PALETTES",
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "THEME_ALIASES",
    "render_member_profile_signature",
    "render_profile_signature",
]
