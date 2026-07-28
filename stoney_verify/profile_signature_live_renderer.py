from __future__ import annotations

"""Reference-faithful premium profile-card renderer for Discord."""

import asyncio
import colorsys
import random
import re
from dataclasses import dataclass
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
    motif: str = "leaf"


PROFILE_THEME_PALETTES: dict[str, ProfilePalette] = {
    "420_lobby": ProfilePalette((2, 8, 4), (4, 15, 8), (137, 255, 76), (37, 211, 111), motif="leaf"),
    "cyber_neon": ProfilePalette((7, 3, 15), (16, 7, 29), (190, 94, 255), (123, 54, 232), motif="smoke"),
    "premium_gold": ProfilePalette((10, 7, 2), (23, 16, 6), (255, 207, 78), (210, 141, 28), motif="flow"),
    "community_glow": ProfilePalette((1, 11, 11), (4, 23, 21), (43, 234, 206), (12, 165, 151), motif="leaf"),
    "esports": ProfilePalette((13, 3, 4), (27, 6, 8), (255, 76, 65), (215, 27, 45), motif="embers"),
    "minimal_glass": ProfilePalette((2, 8, 16), (6, 17, 31), (69, 179, 255), (24, 112, 232), motif="ice"),
}

# Keep the studio's existing names while routing them to the approved profile
# families. Previously these old keys fell back to unrelated welcome-card colors.
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
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


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
    primary, secondary = base.primary, base.secondary

    if mode == "custom":
        try:
            primary = parse_hex_color(str(style.get("custom_primary") or "")) or primary
            secondary = parse_hex_color(str(style.get("custom_secondary") or "")) or secondary
        except Exception:
            primary, secondary = base.primary, base.secondary
    elif mode in {"profile", "auto"}:
        # Avatar matching is a restrained tint now, not a replacement for the
        # selected theme. This prevents green cards becoming orange/pink.
        sampled_primary, sampled_secondary = _legacy._avatar_colors(avatar_bytes, base.primary)
        strength = 0.12 if mode == "profile" else 0.07
        primary = _mix(base.primary, sampled_primary, strength)
        secondary = _mix(base.secondary, sampled_secondary, strength)

    return ProfilePalette(
        background=base.background,
        panel=base.panel,
        primary=_bright(tuple(primary)),
        secondary=_bright(tuple(secondary), 0.52),
        text=base.text,
        muted=base.muted,
        motif=base.motif,
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
    for index in range(13):
        left = 390 + rng.randint(-40, 610)
        top = rng.randint(-110, 220)
        width = rng.randint(260, 560)
        height = rng.randint(100, 260)
        color = palette.primary if index % 2 == 0 else palette.secondary
        draw.arc((left, top, left + width, top + height), rng.randint(150, 205), rng.randint(300, 355), fill=color + (28,), width=rng.randint(3, 8))
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(11)))


def _draw_particles(image: Image.Image, palette: ProfilePalette, seed: int, *, icy: bool = False) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    rng = random.Random(seed)
    for _ in range(95):
        x = rng.randint(420, 1370)
        y = rng.randint(18, 282)
        radius = rng.randint(1, 3 if icy else 4)
        color = palette.primary if rng.random() > 0.4 else palette.secondary
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
        draw.line((32, 270, 1138, 270), fill=palette.secondary + (52,), width=1)
    elif palette.motif == "leaf":
        for x, y, scale in ((520, 122, 0.78), (712, 250, 1.04), (980, 112, 0.78)):
            _draw_leaf(draw, x, y, scale, palette.primary + (22,))
        _draw_wisps(image, palette, 420 if theme_key == "420_lobby" else 1337)
    elif palette.motif == "smoke":
        _draw_wisps(image, palette, 690)
        for radius in (64, 104, 152):
            draw.ellipse((925 - radius, 145 - radius, 925 + radius, 145 + radius), outline=palette.primary + (20,), width=2)
    elif palette.motif == "flow":
        for offset in range(-180, 520, 65):
            draw.arc((430 + offset, -170, 1120 + offset, 430), 178, 350, fill=palette.primary + (34,), width=3)
        _draw_particles(image, palette, 100)
    elif palette.motif == "embers":
        for x in range(500, 1140, 86):
            draw.polygon([(x, 300), (x + 42, 300), (x + 230, 0), (x + 188, 0)], fill=palette.primary + (15,))
        _draw_particles(image, palette, 77)
    elif palette.motif == "ice":
        for x in range(470, 1100, 90):
            draw.line((x, 300, x + 170, 0), fill=palette.primary + (20,), width=2)
            draw.line((x + 20, 300, x + 190, 0), fill=palette.secondary + (11,), width=1)
        _draw_particles(image, palette, 55, icy=True)
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.4)))


def _emoji_sequences(text: str) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(text):
        ch = text[index]
        code = ord(ch)
        regional = 0x1F1E6 <= code <= 0x1F1FF
        if not (_EMOJI_BASE_RE.fullmatch(ch) or regional or ch in "©®™"):
            index += 1
            continue
        sequence = ch
        index += 1
        if regional and index < len(text) and 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
            sequence += text[index]
            index += 1
        if index < len(text) and text[index] == "\ufe0f":
            sequence += text[index]
            index += 1
        while index + 1 < len(text) and text[index] == "\u200d":
            sequence += text[index] + text[index + 1]
            index += 2
            if index < len(text) and text[index] == "\ufe0f":
                sequence += text[index]
                index += 1
        out.append(sequence)
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
    for url in (
        f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{code}.png",
        f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{code}.png",
    ):
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    payload = await response.read()
                    if payload:
                        _EMOJIS[emoji] = payload
                        return payload
        except Exception:
            pass
    _EMOJI_MISSES.add(emoji)
    return b""


async def _emoji_assets(values: Sequence[Any]) -> dict[str, bytes]:
    emojis: list[str] = []
    for value in values:
        for emoji in _emoji_sequences(str(value or "")):
            if emoji not in emojis and len(emojis) < 16:
                emojis.append(emoji)
    missing = [emoji for emoji in emojis if emoji not in _EMOJIS and emoji not in _EMOJI_MISSES]
    if missing:
        timeout = aiohttp.ClientTimeout(total=3.5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
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
    draw.rounded_rectangle((22, 22, 1378, 278), radius=25, outline=(255, 255, 255, 24), width=1)


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
        draw.ellipse((x - 2, y - 2, x + size + 2, y + size + 2), outline=palette.secondary + (135,), width=2)
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
    font = _font(17, regular=True)
    x, y = spec.content_x, spec.tags_y
    row = 0
    for raw, accent in labels:
        clean = _safe(raw, 110)
        natural = _rich_width(draw, clean, font, assets, 18) + 28
        if x + natural > spec.content_right and x > spec.content_x:
            row += 1
            if row >= 2:
                break
            x, y = spec.content_x, y + 39
        available = spec.content_right - x
        fitted = _fit(draw, clean, font, max(0, available - 28), assets, 18, 110)
        if not fitted:
            continue
        width = min(available, _rich_width(draw, fitted, font, assets, 18) + 28)
        fill = _mix((3, 7, 10), accent, 0.12)
        draw.rounded_rectangle((x, y, x + width, y + 32), radius=14, fill=fill + (224,), outline=accent + (185,), width=2)
        _draw_rich(image, draw, (x + 14, y + 5), fitted, font=font, fill=palette.text + (248,), assets=assets, emoji_size=18)
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


def _draw_platforms(image: Image.Image, entries: Sequence[Mapping[str, Any]], logos: Mapping[str, bytes], role: str, palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes], theme_key: str) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    role_font = _font(17, regular=True)
    fitted_role = _fit(draw, role.upper(), role_font, 245, assets, 18, 60) or "MEMBER"
    role_width = min(278, _rich_width(draw, fitted_role, role_font, assets, 18) + 58)
    role_fill = _mix((3, 7, 10), palette.primary, 0.13)
    draw.rounded_rectangle((spec.platform_x, 37, spec.platform_x + role_width, 75), radius=16, fill=role_fill + (225,), outline=palette.primary + (185,), width=2)
    _role_icon(draw, spec.platform_x + 13, 45, palette, theme_key)
    _draw_rich(image, draw, (spec.platform_x + 43, 46), fitted_role, font=role_font, fill=palette.primary + (255,), assets=assets, emoji_size=18)

    x, y, size = spec.platform_x, 91, 57
    shared: list[str] = []
    for index, entry in enumerate(list(entries)[:5]):
        platform = str(entry.get("platform") or "")
        spec_data = PLATFORM_SPECS.get(platform)
        if spec_data is None:
            continue
        accent = palette.primary if index % 2 == 0 else palette.secondary
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow, "RGBA")
        sdraw.rounded_rectangle((x - 2, y - 2, x + size + 2, y + size + 2), radius=15, fill=accent + (42,))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle((x, y, x + size, y + size), radius=14, fill=(3, 6, 10, 226), outline=accent + (135,), width=2)
        image.alpha_composite(_asset_tile(bytes(logos.get(platform) or b""), size - 8, spec_data.label), (x + 4, y + 4))
        username = _safe(entry.get("username"), 34)
        if username and platform_entry_mode(entry) != "logo":
            shared.append(f"{spec_data.label}: {username}")
        x += 66

    if shared:
        draw = ImageDraw.Draw(image, "RGBA")
        font = _font(17, regular=True)
        line = _fit(draw, "   ".join(shared[:2]), font, 304, assets, 18, 150)
        _draw_rich(image, draw, (spec.platform_x, 168), line, font=font, fill=palette.secondary + (255,), assets=assets, emoji_size=18)


def _draw_brand(image: Image.Image, server_name: str, icon_bytes: bytes, palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes], theme_key: str) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x = spec.brand_x
    draw.polygon([(x, 20), (1382, 20), (1382, 280), (x - 48, 280)], fill=(2, 4, 7, 208))
    draw.line((x, 24, x - 44, 276), fill=palette.primary + (230,), width=3)
    draw.line((x + 7, 24, x - 37, 276), fill=palette.secondary + (70,), width=1)
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
    draw.text((icon_x + (size - small_width) / 2, 160), small_text, font=small, fill=palette.primary + (225,))
    font = _font(18, regular=True)
    label = _fit(draw, server_name, font, 160, assets, 18, 70)
    width = _rich_width(draw, label, font, assets, 18)
    _draw_rich(image, draw, (icon_x + max(-24, (size - width) // 2), 184), label, font=font, fill=palette.text + (248,), assets=assets, emoji_size=18)


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
) -> bytes:
    palette = _palette(style, avatar_bytes)
    theme_key = _canonical_theme(style)
    layout_key = str(style.get("layout") or "classic").lower()
    spec = _LAYOUTS.get(layout_key, _LAYOUTS["classic"])
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
    eyebrow = _fit(draw, server_name.upper(), eyebrow_font, spec.content_right - spec.content_x, assets, 18, 70)
    _draw_rich(image, draw, (spec.content_x, 42), eyebrow, font=eyebrow_font, fill=palette.primary + (255,), assets=assets, emoji_size=18)

    name = _safe(display_name, 72) or "Member"
    if _emoji_sequences(name):
        font = _font(spec.name_size)
        fitted = _fit(draw, name, font, spec.content_right - spec.content_x, assets, 50, 72)
        _draw_rich(image, draw, (spec.content_x, spec.name_y), fitted, font=font, fill=palette.text + (255,), assets=assets, emoji_size=50)
    else:
        name_tile = render_styled_text_tile(
            name,
            style_key=style_key,
            start_size=spec.name_size,
            min_size=34,
            max_width=spec.content_right - spec.content_x,
            max_height=78,
            primary=palette.primary,
            secondary=palette.secondary,
            role="name",
            custom_font_bytes=bytes(style.get("custom_font") or b""),
        )
        image.alpha_composite(name_tile, (spec.content_x, spec.name_y - 6))

    _draw_meta_dates(image, date_labels, palette, spec, assets)

    server_roles = [_safe(value, 80) for value in server_role_labels if _safe(value, 80)]
    tags = [_safe(value, 110) for value in profile_tag_labels if _safe(value, 110)]
    pills: list[tuple[str, tuple[int, int, int]]] = []
    pills.extend((value, palette.secondary) for value in tags[:4])
    pills.extend((f"Role: {value}", palette.primary) for value in server_roles[1:3])
    pills.extend((value, palette.secondary) for value in role_labels[:2] if value not in {item[0] for item in pills})
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
