from __future__ import annotations

"""Compact premium profile-card renderer for Discord."""

import asyncio
import colorsys
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
    BUILTIN_THEMES,
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
    muted: tuple[int, int, int] = (199, 208, 220)
    motif: str = "leaf"


PROFILE_THEME_PALETTES: dict[str, ProfilePalette] = {
    "420_lobby": ProfilePalette((3, 9, 5), (6, 17, 10), (143, 255, 82), (32, 207, 112), motif="leaf"),
    "cyber_neon": ProfilePalette((8, 4, 15), (17, 8, 28), (184, 91, 255), (117, 55, 224), motif="smoke"),
    "premium_gold": ProfilePalette((12, 9, 3), (24, 17, 7), (255, 204, 82), (207, 143, 31), motif="flow"),
    "community_glow": ProfilePalette((2, 12, 12), (5, 24, 22), (45, 232, 205), (14, 159, 151), motif="leaf"),
    "esports": ProfilePalette((14, 4, 5), (28, 7, 9), (255, 78, 68), (213, 30, 46), motif="slash"),
    "minimal_glass": ProfilePalette((3, 9, 17), (7, 18, 31), (70, 177, 255), (25, 112, 229), motif="ice"),
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
    "classic": Layout(54, 198, 286, 812, 67, 58, 196, 842, 1174),
    "minimal": Layout(62, 164, 254, 822, 70, 54, 198, 850, 1178),
    "spotlight": Layout(42, 224, 292, 804, 58, 62, 198, 832, 1170),
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


def _bright(color: tuple[int, int, int], floor: float = 0.54) -> tuple[int, int, int]:
    red, green, blue = (max(0, min(255, int(v))) / 255 for v in color)
    hue, light, saturation = colorsys.rgb_to_hls(red, green, blue)
    red, green, blue = colorsys.hls_to_rgb(hue, max(floor, min(0.72, light)), max(0.58, saturation))
    return tuple(int(round(v * 255)) for v in (red, green, blue))


def _palette(style: Mapping[str, Any], avatar_bytes: bytes) -> ProfilePalette:
    key = str(style.get("theme") or "420_lobby").strip().lower()
    theme = BUILTIN_THEMES.get(key) or BUILTIN_THEMES.get("420_lobby") or next(iter(BUILTIN_THEMES.values()))
    base = PROFILE_THEME_PALETTES.get(key) or ProfilePalette(
        tuple(theme.background), tuple(theme.panel), tuple(theme.primary), tuple(theme.secondary),
        tuple(theme.text), tuple(theme.muted), str(getattr(theme, "motif", "leaf")),
    )
    primary, secondary = base.primary, base.secondary
    mode = str(style.get("color_mode") or "theme").lower()
    if mode in {"profile", "auto"}:
        primary, secondary = _legacy._avatar_colors(avatar_bytes, primary)
    elif mode == "custom":
        try:
            primary = parse_hex_color(str(style.get("custom_primary") or "")) or primary
            secondary = parse_hex_color(str(style.get("custom_secondary") or "")) or secondary
        except Exception:
            pass
    return ProfilePalette(base.background, base.panel, _bright(tuple(primary)), _bright(tuple(secondary), 0.51), base.text, base.muted, base.motif)


def _background(style: Mapping[str, Any], palette: ProfilePalette, avatar_bytes: bytes) -> Image.Image:
    mode = str(style.get("background_mode") or "theme").lower()
    payload = bytes(style.get("custom_background") or b"") if mode == "custom" else avatar_bytes if mode == "profile" else b""
    if payload:
        found = _avatar_image(payload, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if found is not None:
            if mode == "profile":
                found = found.filter(ImageFilter.GaussianBlur(24))
            return Image.alpha_composite(found, Image.new("RGBA", found.size, palette.background + (185,)))
    image = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), palette.background + (255,))
    draw = ImageDraw.Draw(image)
    right = _mix(palette.background, palette.panel, 0.78)
    for x in range(SIGNATURE_WIDTH):
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=_mix(palette.background, right, x / (SIGNATURE_WIDTH - 1)) + (255,))
    return image


def _draw_leaf(draw: ImageDraw.ImageDraw, cx: int, cy: int, scale: float, color: tuple[int, int, int, int]) -> None:
    draw.line((cx, cy + int(40 * scale), cx, cy - int(42 * scale)), fill=color, width=max(1, int(3 * scale)))
    for ox, oy, radius in ((0, -62, 13), (-27, -43, 12), (27, -43, 12), (-46, -20, 10), (46, -20, 10), (-27, 2, 9), (27, 2, 9)):
        tip = (cx + int(ox * scale), cy + int(oy * scale))
        draw.polygon([(cx, cy + int(9 * scale)), (tip[0] - int(radius * scale), tip[1] + int(8 * scale)), tip, (tip[0] + int(radius * scale), tip[1] + int(8 * scale))], fill=color)


def _texture(image: Image.Image, palette: ProfilePalette, layout: str) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    if layout == "minimal":
        draw.line((36, 34, 1140, 34), fill=palette.primary + (85,), width=2)
    elif palette.motif == "leaf":
        for x, y, scale in ((540, 80, 0.7), (735, 235, 1.0), (1035, 78, 0.72)):
            _draw_leaf(draw, x, y, scale, palette.primary + (20,))
    elif palette.motif == "flow":
        for offset in range(-100, 500, 70):
            draw.arc((500 + offset, -120, 1160 + offset, 400), 175, 350, fill=palette.primary + (35,), width=3)
    elif palette.motif == "slash":
        for x in range(520, 1140, 100):
            draw.polygon([(x, 300), (x + 52, 300), (x + 240, 0), (x + 188, 0)], fill=palette.primary + (18,))
    else:
        for radius in (75, 120, 170):
            draw.ellipse((910 - radius, 145 - radius, 910 + radius, 145 + radius), outline=palette.primary + (24,), width=3)
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(2)))


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
        seq = ch
        index += 1
        if regional and index < len(text) and 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
            seq += text[index]
            index += 1
        if index < len(text) and text[index] == "\ufe0f":
            seq += text[index]
            index += 1
        while index + 1 < len(text) and text[index] == "\u200d":
            seq += text[index] + text[index + 1]
            index += 2
            if index < len(text) and text[index] == "\ufe0f":
                seq += text[index]
                index += 1
        out.append(seq)
    return out


def _emoji_tokens(text: str) -> list[tuple[str, bool]]:
    found = set(_emoji_sequences(text))
    if not found:
        return [(text, False)] if text else []
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        match = next((emoji for emoji in sorted(found, key=len, reverse=True) if text.startswith(emoji, index)), None)
        if match:
            tokens.append((match, True))
            index += len(match)
        else:
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
            if emoji not in emojis and len(emojis) < 12:
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
            width += emoji_size if assets.get(token) else max(10, emoji_size // 2)
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
            token = "•"
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
    font = _font(max(15, size // 5), regular=True)
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


def _draw_avatar(image: Image.Image, avatar_bytes: bytes, display_name: str, palette: ProfilePalette, spec: Layout, frame: str) -> None:
    size = spec.avatar_size
    x, y = spec.avatar_x, (SIGNATURE_HEIGHT - size) // 2
    if frame == "glow":
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((x - 11, y - 11, x + size + 11, y + size + 11), fill=palette.primary + (125,))
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(17)))
    draw = ImageDraw.Draw(image, "RGBA")
    if frame != "none":
        draw.ellipse((x - 6, y - 6, x + size + 6, y + size + 6), fill=palette.panel + (255,), outline=palette.primary + (245,), width=5 if frame == "glow" else 3)
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
            x, y = spec.content_x, y + 42
        available = spec.content_right - x
        fitted = _fit(draw, clean, font, max(0, available - 28), assets, 18, 110)
        if not fitted:
            continue
        width = min(available, _rich_width(draw, fitted, font, assets, 18) + 28)
        fill = _mix(palette.panel, accent, 0.16)
        draw.rounded_rectangle((x, y, x + width, y + 34), radius=15, fill=fill + (235,), outline=accent + (170,), width=2)
        _draw_rich(image, draw, (x + 14, y + 6), fitted, font=font, fill=palette.text + (245,), assets=assets, emoji_size=18)
        x += width + 8


def _draw_platforms(image: Image.Image, entries: Sequence[Mapping[str, Any]], logos: Mapping[str, bytes], role: str, palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    role_font = _font(17, regular=True)
    fitted_role = _fit(draw, role.upper(), role_font, 260, assets, 18, 60) or "MEMBER"
    role_width = min(276, _rich_width(draw, fitted_role, role_font, assets, 18) + 30)
    role_fill = _mix(palette.panel, palette.primary, 0.15)
    draw.rounded_rectangle((spec.platform_x, 40, spec.platform_x + role_width, 76), radius=15, fill=role_fill + (240,), outline=palette.primary + (175,), width=2)
    _draw_rich(image, draw, (spec.platform_x + 15, 47), fitted_role, font=role_font, fill=palette.primary + (255,), assets=assets, emoji_size=18)

    x, y, size = spec.platform_x, 92, 56
    shared: list[str] = []
    for index, entry in enumerate(list(entries)[:4]):
        platform = str(entry.get("platform") or "")
        spec_data = PLATFORM_SPECS.get(platform)
        if spec_data is None:
            continue
        accent = palette.primary if index % 2 == 0 else palette.secondary
        draw.rounded_rectangle((x, y, x + size, y + size), radius=14, fill=(4, 7, 12, 224), outline=accent + (150,), width=2)
        image.alpha_composite(_asset_tile(bytes(logos.get(platform) or b""), size - 8, spec_data.label), (x + 4, y + 4))
        username = _safe(entry.get("username"), 34)
        if username and platform_entry_mode(entry) != "logo":
            shared.append(f"{spec_data.label}: {username}")
        x += 66
    if shared:
        draw = ImageDraw.Draw(image, "RGBA")
        font = _font(17, regular=True)
        line = _fit(draw, "  •  ".join(shared[:2]), font, 286, assets, 18, 150)
        _draw_rich(image, draw, (spec.platform_x, 166), line, font=font, fill=palette.secondary + (255,), assets=assets, emoji_size=18)


def _draw_brand(image: Image.Image, server_name: str, icon_bytes: bytes, palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x = spec.brand_x
    draw.polygon([(x, 22), (1382, 22), (1382, 278), (x - 44, 278)], fill=(2, 4, 7, 198))
    draw.line((x, 24, x - 42, 276), fill=palette.primary + (210,), width=3)
    _draw_leaf(draw, 1288, 145, 0.72, palette.primary + (18,))
    size, icon_x, icon_y = 104, x + 30, 54
    draw.rounded_rectangle((icon_x - 6, icon_y - 6, icon_x + size + 6, icon_y + size + 6), radius=24, fill=(5, 8, 12, 215), outline=palette.primary + (130,), width=2)
    image.alpha_composite(_asset_tile(icon_bytes, size, server_name), (icon_x, icon_y))
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(17, regular=True)
    label = _fit(draw, server_name, font, size + 4, assets, 18, 70)
    width = _rich_width(draw, label, font, assets, 18)
    _draw_rich(image, draw, (icon_x + max(0, (size - width) // 2), 179), label, font=font, fill=palette.text + (245,), assets=assets, emoji_size=18)


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
    layout_key = str(style.get("layout") or "classic").lower()
    spec = _LAYOUTS.get(layout_key, _LAYOUTS["classic"])
    frame = str(style.get("avatar_frame") or "glow").lower()
    frame = frame if frame in {"glow", "ring", "none"} else "glow"
    style_key = str(style.get("font") or "clean")
    if style_key not in FONT_STYLES and style_key != CUSTOM_FONT_STYLE_KEY:
        style_key = "clean"
    assets = dict(emoji_assets or {})

    image = _background(style, palette, avatar_bytes)
    panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel, "RGBA").rounded_rectangle((18, 18, 1382, 282), radius=30, fill=palette.panel + (205,))
    image = Image.alpha_composite(image, panel)
    _texture(image, palette, layout_key)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((18, 18, 1382, 282), radius=30, outline=palette.primary + (165,), width=2)
    draw.rounded_rectangle((20, 20, 27, 280), radius=4, fill=palette.primary + (255,))
    _draw_avatar(image, avatar_bytes, display_name, palette, spec, frame)
    draw = ImageDraw.Draw(image, "RGBA")

    eyebrow_font = _font(18, regular=True)
    eyebrow = _fit(draw, server_name.upper(), eyebrow_font, spec.content_right - spec.content_x, assets, 19, 70)
    _draw_rich(image, draw, (spec.content_x, 45), eyebrow, font=eyebrow_font, fill=palette.primary + (255,), assets=assets, emoji_size=19)
    name = _safe(display_name, 72) or "Member"
    if _emoji_sequences(name):
        font = _font(spec.name_size)
        fitted = _fit(draw, name, font, spec.content_right - spec.content_x, assets, 52, 72)
        _draw_rich(image, draw, (spec.content_x, spec.name_y), fitted, font=font, fill=palette.text + (255,), assets=assets, emoji_size=52)
    else:
        name_tile = render_styled_text_tile(
            name, style_key=style_key, start_size=spec.name_size, min_size=34,
            max_width=spec.content_right - spec.content_x, max_height=76,
            primary=palette.primary, secondary=palette.secondary, role="name",
            custom_font_bytes=bytes(style.get("custom_font") or b""),
        )
        image.alpha_composite(name_tile, (spec.content_x, spec.name_y - 5))

    draw = ImageDraw.Draw(image, "RGBA")
    dates = [_safe(value, 70) for value in date_labels if _safe(value, 70)]
    if dates:
        font = _font(18, regular=True)
        line = _fit(draw, "  •  ".join(dates[:2]), font, spec.content_right - spec.content_x, assets, 19, 150)
        _draw_rich(image, draw, (spec.content_x, 151), line, font=font, fill=palette.muted + (245,), assets=assets, emoji_size=19)

    server_roles = [_safe(value, 80) for value in server_role_labels if _safe(value, 80)]
    tags = [_safe(value, 110) for value in profile_tag_labels if _safe(value, 110)]
    pills: list[tuple[str, tuple[int, int, int]]] = [(f"Role: {value}", palette.primary) for value in server_roles[1:3]]
    pills.extend((value, palette.secondary) for value in tags[:4])
    pills.extend((value, palette.secondary) for value in role_labels[:2] if value not in {item[0] for item in pills})
    if not pills and not server_roles:
        pills.append(("Private profile", palette.primary))
    _draw_pills(image, pills, palette, spec, assets)

    entries = [dict(entry) for entry in platform_entries if isinstance(entry, Mapping)]
    _draw_platforms(image, entries, dict(platform_logo_bytes or {}), server_roles[0] if server_roles else "Member", palette, spec, assets)
    _draw_brand(image, server_name, guild_icon_bytes, palette, spec, assets)

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
    "render_member_profile_signature",
    "render_profile_signature",
]
