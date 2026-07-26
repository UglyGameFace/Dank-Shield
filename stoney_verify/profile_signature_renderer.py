from __future__ import annotations

"""Compact forum-style profile signature rendering.

This renderer intentionally shares the configured welcome-card visual language
(theme, palette, font family, and optional custom background) without reusing
the large join-card layout or any welcome/join behavior.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .welcome_card_service import (
    configured_color_mode,
    configured_custom_colors,
    configured_custom_font,
    configured_font_style_key,
    configured_theme_key,
    decode_custom_background,
)
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
)

SIGNATURE_WIDTH = 1080
SIGNATURE_HEIGHT = 220
SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT
_AVATAR_SIZE = 148

_FONT_FAMILIES: dict[str, tuple[str, ...]] = {
    "sans": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
    ),
    "mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    ),
}
_REGULAR_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
)


def _safe_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[: max(0, int(limit))]


def _font(
    size: int,
    *,
    style_key: str,
    custom_font: Optional[bytes] = None,
    regular: bool = False,
) -> ImageFont.ImageFont:
    if custom_font and style_key == CUSTOM_FONT_STYLE_KEY:
        try:
            return ImageFont.truetype(BytesIO(custom_font), max(8, int(size)))
        except Exception:
            pass
    style = FONT_STYLES.get(style_key) or next(iter(FONT_STYLES.values()))
    candidates = _REGULAR_FONTS if regular else _FONT_FAMILIES.get(style.family, _FONT_FAMILIES["sans"])
    for candidate in candidates:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, max(8, int(size)))
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    style_key: str,
    custom_font: Optional[bytes],
    max_width: int,
    start: int,
    minimum: int,
) -> ImageFont.ImageFont:
    for size in range(int(start), int(minimum) - 1, -2):
        font = _font(size, style_key=style_key, custom_font=custom_font)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if box[2] - box[0] <= max_width:
            return font
    return _font(minimum, style_key=style_key, custom_font=custom_font)


def _mix(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(amount)))
    return tuple(int(left[index] * (1.0 - t) + right[index] * t) for index in range(3))


def _resolve_colors(cfg: Any) -> tuple[Any, tuple[int, int, int], tuple[int, int, int]]:
    theme = BUILTIN_THEMES[configured_theme_key(cfg)]
    primary = tuple(theme.primary)
    secondary = tuple(theme.secondary)
    if configured_color_mode(cfg) == "custom":
        custom_primary, custom_secondary = configured_custom_colors(cfg)
        try:
            parsed = parse_hex_color(custom_primary)
            if parsed:
                primary = parsed
        except Exception:
            pass
        try:
            parsed = parse_hex_color(custom_secondary)
            if parsed:
                secondary = parsed
        except Exception:
            pass
    return theme, primary, secondary


def _background(cfg: Any, theme: Any) -> Image.Image:
    custom = decode_custom_background(cfg)
    if custom:
        try:
            with Image.open(BytesIO(custom)) as source:
                image = ImageOps.fit(
                    source.convert("RGBA"),
                    (SIGNATURE_WIDTH, SIGNATURE_HEIGHT),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            veil = Image.new("RGBA", image.size, (3, 5, 10, 132))
            return Image.alpha_composite(image, veil)
        except Exception:
            pass

    canvas = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), tuple(theme.background) + (255,))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for x in range(SIGNATURE_WIDTH):
        amount = x / max(1, SIGNATURE_WIDTH - 1)
        color = _mix(tuple(theme.background), tuple(theme.panel), amount)
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=color + (255,))
    return canvas


def _avatar_tile(avatar_bytes: bytes, display_name: str, primary: tuple[int, int, int]) -> Image.Image:
    tile = Image.new("RGBA", (_AVATAR_SIZE, _AVATAR_SIZE), (0, 0, 0, 0))
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, _AVATAR_SIZE - 1, _AVATAR_SIZE - 1), fill=255)
    if avatar_bytes:
        try:
            with Image.open(BytesIO(avatar_bytes)) as source:
                avatar = ImageOps.fit(
                    source.convert("RGBA"),
                    tile.size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            tile.paste(avatar, (0, 0), mask)
            return tile
        except Exception:
            pass

    fallback = Image.new("RGBA", tile.size, _mix(primary, (12, 14, 20), 0.62) + (255,))
    fallback_draw = ImageDraw.Draw(fallback)
    initial = (_safe_text(display_name, 1) or "?").upper()
    font = _font(68, style_key="clean")
    box = fallback_draw.textbbox((0, 0), initial, font=font)
    fallback_draw.text(
        ((_AVATAR_SIZE - (box[2] - box[0])) / 2, (_AVATAR_SIZE - (box[3] - box[1])) / 2 - 5),
        initial,
        font=font,
        fill=(255, 255, 255, 255),
    )
    tile.paste(fallback, (0, 0), mask)
    return tile


def _chip_width(draw: ImageDraw.ImageDraw, label: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), label, font=font)
    return max(42, box[2] - box[0] + 24)


def _draw_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    text: tuple[int, int, int],
) -> int:
    width = _chip_width(draw, label, font)
    draw.rounded_rectangle((x, y, x + width, y + 32), radius=14, fill=accent + (56,), outline=accent + (145,), width=1)
    draw.text((x + 12, y + 7), label, font=font, fill=text + (255,))
    return width


def _pack_chips(
    draw: ImageDraw.ImageDraw,
    chips: Sequence[tuple[str, tuple[int, int, int]]],
    *,
    start_x: int,
    start_y: int,
    max_x: int,
    font: ImageFont.ImageFont,
    text: tuple[int, int, int],
    max_rows: int = 2,
) -> None:
    x = start_x
    y = start_y
    row = 0
    for raw_label, accent in chips:
        label = _safe_text(raw_label, 42)
        if not label:
            continue
        width = _chip_width(draw, label, font)
        if x + width > max_x and x > start_x:
            row += 1
            if row >= max_rows:
                break
            x = start_x
            y += 40
        width = _draw_chip(draw, x, y, label, font=font, accent=accent, text=text)
        x += width + 8


def render_profile_signature(
    *,
    avatar_bytes: bytes,
    display_name: str,
    server_name: str,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
    cfg: Any,
) -> bytes:
    theme, primary, secondary = _resolve_colors(cfg)
    style_key = configured_font_style_key(cfg)
    custom_font, _font_name = configured_custom_font(cfg)

    image = _background(cfg, theme)
    draw = ImageDraw.Draw(image, "RGBA")

    # Welcome-card visual language, compressed into a forum-signature footprint.
    draw.ellipse((810, -170, 1180, 200), fill=primary + (24,), outline=primary + (78,), width=2)
    draw.ellipse((880, -90, 1210, 240), fill=secondary + (18,), outline=secondary + (72,), width=2)
    for offset in range(-80, 1180, 92):
        draw.line((offset, 220, offset + 180, 0), fill=secondary + (20,), width=2)

    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    draw.rounded_rectangle(panel, radius=28, fill=tuple(theme.panel) + (218,), outline=primary + (125,), width=2)

    avatar_x = 42
    avatar_y = (SIGNATURE_HEIGHT - _AVATAR_SIZE) // 2
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse(
        (avatar_x - 8, avatar_y - 8, avatar_x + _AVATAR_SIZE + 8, avatar_y + _AVATAR_SIZE + 8),
        fill=primary + (130,),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(13))
    image = Image.alpha_composite(image, glow)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse(
        (avatar_x - 5, avatar_y - 5, avatar_x + _AVATAR_SIZE + 5, avatar_y + _AVATAR_SIZE + 5),
        fill=tuple(theme.panel) + (255,),
        outline=primary + (235,),
        width=4,
    )
    image.alpha_composite(_avatar_tile(avatar_bytes, display_name, primary), (avatar_x, avatar_y))
    draw = ImageDraw.Draw(image, "RGBA")

    text = tuple(theme.text)
    muted = tuple(theme.muted)
    content_x = 220
    right_edge = SIGNATURE_WIDTH - 42

    eyebrow_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)
    name_text = _safe_text(display_name, 80) or "Member"
    name_font = _fit_font(
        draw,
        name_text,
        style_key=style_key,
        custom_font=custom_font,
        max_width=760,
        start=46,
        minimum=28,
    )
    chip_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = f"MEMBER SIGNATURE  •  {_safe_text(server_name, 48).upper()}"
    draw.text((content_x, 34), eyebrow, font=eyebrow_font, fill=primary + (255,))
    draw.text((content_x, 55), name_text, font=name_font, fill=text + (255,), stroke_width=1, stroke_fill=(0, 0, 0, 170))

    chips: list[tuple[str, tuple[int, int, int]]] = []
    for index, label in enumerate(role_labels[:4]):
        chips.append((label, primary if index % 2 == 0 else secondary))
    for index, label in enumerate(platform_labels[:3]):
        chips.append((label, secondary if index % 2 == 0 else primary))
    for index, label in enumerate(date_labels[:2]):
        chips.append((label, secondary if index % 2 == 0 else primary))
    if not chips:
        chips.append(("Private profile", primary))

    _pack_chips(
        draw,
        chips,
        start_x=content_x,
        start_y=124,
        max_x=right_edge,
        font=chip_font,
        text=text,
        max_rows=2,
    )

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


async def render_member_profile_signature(
    member: discord.Member,
    *,
    cfg: Any,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
) -> bytes:
    avatar = await _avatar_bytes(member)
    return await asyncio.to_thread(
        render_profile_signature,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(getattr(member, "guild", None), "name", None) or "Discord Server",
        role_labels=list(role_labels),
        date_labels=list(date_labels),
        platform_labels=list(platform_labels),
        cfg=cfg,
    )


__all__ = [
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "render_member_profile_signature",
    "render_profile_signature",
]
