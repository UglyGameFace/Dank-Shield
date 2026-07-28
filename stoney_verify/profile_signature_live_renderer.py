from __future__ import annotations

"""Legible live-signature renderer optimized for Discord mobile and desktop."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .profile_card_service import PLATFORM_SPECS, platform_entry_mode

from . import profile_signature_renderer as _legacy
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
    render_styled_text_tile,
)

SIGNATURE_WIDTH = 1080
SIGNATURE_HEIGHT = 300
SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT
PLATFORM_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "platform_logos"
_PLATFORM_LOGO_CACHE: dict[tuple[str, int], Optional[Image.Image]] = {}


def _safe_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[: max(0, int(limit))]


def _mix(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return _legacy._mix(left, right, amount)


def _font(
    size: int,
    *,
    style_key: str,
    custom_font: Optional[bytes] = None,
    regular: bool = False,
):
    return _legacy._font(
        size,
        style_key=style_key,
        custom_font=custom_font,
        regular=regular,
    )


def _avatar_image(avatar_bytes: bytes, size: tuple[int, int]) -> Optional[Image.Image]:
    return _legacy._avatar_image(avatar_bytes, size)


def _avatar_colors(
    avatar_bytes: bytes,
    fallback: tuple[int, int, int],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return _legacy._avatar_colors(avatar_bytes, fallback)


def _resolve_colors(
    style: Mapping[str, Any],
    avatar_bytes: bytes,
) -> tuple[Any, tuple[int, int, int], tuple[int, int, int]]:
    theme_key = str(style.get("theme") or "default")
    theme = BUILTIN_THEMES.get(theme_key) or BUILTIN_THEMES.get("default") or next(iter(BUILTIN_THEMES.values()))
    primary = tuple(theme.primary)
    secondary = tuple(theme.secondary)
    mode = str(style.get("color_mode") or "profile")
    if mode in {"profile", "auto"}:
        primary, secondary = _avatar_colors(avatar_bytes, primary)
    elif mode == "custom":
        try:
            parsed = parse_hex_color(str(style.get("custom_primary") or ""))
            if parsed:
                primary = parsed
        except Exception:
            pass
        try:
            parsed = parse_hex_color(str(style.get("custom_secondary") or ""))
            if parsed:
                secondary = parsed
        except Exception:
            pass
    return theme, primary, secondary


def _background(style: Mapping[str, Any], theme: Any, avatar_bytes: bytes) -> Image.Image:
    mode = str(style.get("background_mode") or "theme")
    if mode == "custom":
        custom = bytes(style.get("custom_background") or b"")
        image = _avatar_image(custom, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if image is not None:
            veil = Image.new("RGBA", image.size, (3, 5, 10, 142))
            return Image.alpha_composite(image, veil)
    if mode == "profile":
        image = _avatar_image(avatar_bytes, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if image is not None:
            image = image.filter(ImageFilter.GaussianBlur(22))
            veil = Image.new("RGBA", image.size, (4, 6, 12, 158))
            return Image.alpha_composite(image, veil)

    canvas = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), tuple(theme.background) + (255,))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for x in range(SIGNATURE_WIDTH):
        amount = x / max(1, SIGNATURE_WIDTH - 1)
        color = _mix(tuple(theme.background), tuple(theme.panel), amount)
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=color + (255,))
    return canvas


def _draw_motif(
    draw: ImageDraw.ImageDraw,
    *,
    motif: str,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    layout: str,
) -> None:
    key = str(motif or "generic").strip().lower()
    if key == "minimal" or layout == "minimal":
        draw.rounded_rectangle((38, 24, SIGNATURE_WIDTH - 38, 31), radius=4, fill=primary + (150,))
        draw.rounded_rectangle((38, 37, 410, 41), radius=2, fill=secondary + (95,))
        return
    if key == "cyber":
        for x in range(650, SIGNATURE_WIDTH + 1, 48):
            draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=primary + (24,), width=1)
        for y in range(20, SIGNATURE_HEIGHT, 40):
            draw.line((620, y, SIGNATURE_WIDTH, y), fill=secondary + (24,), width=1)
        points = ((690, 58), (790, 58), (790, 112), (900, 112), (900, 182), (1040, 182))
        draw.line(points, fill=primary + (120,), width=3, joint="curve")
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=secondary + (180,))
        return
    if key == "premium":
        draw.line((650, 48, 1035, 48), fill=secondary + (120,), width=3)
        draw.line((720, 245, 1035, 245), fill=primary + (100,), width=2)
        for x in (690, 790, 890, 990):
            draw.polygon(
                [(x, 150), (x + 20, 130), (x + 40, 150), (x + 20, 170)],
                fill=secondary + (28,),
                outline=secondary + (95,),
            )
        return
    if key == "community":
        for x, y, radius, color in (
            (740, 92, 62, primary),
            (850, 185, 86, secondary),
            (970, 92, 58, primary),
            (1050, 205, 68, secondary),
        ):
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=color + (20,),
                outline=color + (72,),
                width=3,
            )
        return
    if key == "esports":
        for index in range(6):
            x = 620 + index * 92
            color = primary if index % 2 == 0 else secondary
            draw.polygon(
                [(x, SIGNATURE_HEIGHT), (x + 94, SIGNATURE_HEIGHT), (x + 270, 0), (x + 176, 0)],
                fill=color + (20 + index * 3,),
                outline=color + (58,),
            )
        return
    if key == "420":
        center_x = SIGNATURE_WIDTH - 190
        center_y = SIGNATURE_HEIGHT // 2
        for radius, alpha, color in (
            (170, 30, primary),
            (126, 42, secondary),
            (82, 58, primary),
        ):
            draw.ellipse(
                (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                outline=color + (alpha,),
                width=4,
            )
        draw.line((center_x - 4, 254, center_x + 25, 78), fill=primary + (120,), width=6)
        return

    draw.ellipse((790, -160, 1190, 240), fill=primary + (24,), outline=primary + (80,), width=2)
    draw.ellipse((870, -55, 1230, 305), fill=secondary + (20,), outline=secondary + (74,), width=2)
    for offset in range(-90, 1190, 96):
        draw.line((offset, SIGNATURE_HEIGHT, offset + 230, 0), fill=secondary + (22,), width=2)


def _layout_metrics(layout: str) -> dict[str, int]:
    if layout == "minimal":
        return {
            "avatar_size": 156,
            "avatar_x": 52,
            "content_x": 242,
            "name_start": 54,
            "name_min": 34,
            "eyebrow_y": 56,
            "name_y": 84,
            "chips_y": 182,
            "rows": 2,
        }
    if layout == "spotlight":
        return {
            "avatar_size": 206,
            "avatar_x": 824,
            "content_x": 58,
            "name_start": 62,
            "name_min": 36,
            "eyebrow_y": 50,
            "name_y": 78,
            "chips_y": 180,
            "rows": 2,
        }
    return {
        "avatar_size": 190,
        "avatar_x": 52,
        "content_x": 274,
        "name_start": 60,
        "name_min": 36,
        "eyebrow_y": 52,
        "name_y": 80,
        "chips_y": 180,
        "rows": 2,
    }


def _avatar_tile(
    avatar_bytes: bytes,
    display_name: str,
    primary: tuple[int, int, int],
    size: int,
) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    avatar = _avatar_image(avatar_bytes, tile.size)
    if avatar is not None:
        tile.paste(avatar, (0, 0), mask)
        return tile

    fallback = Image.new("RGBA", tile.size, _mix(primary, (12, 14, 20), 0.62) + (255,))
    fallback_draw = ImageDraw.Draw(fallback)
    initial = (_safe_text(display_name, 1) or "?").upper()
    font = _font(max(40, int(size * 0.46)), style_key="clean")
    box = fallback_draw.textbbox((0, 0), initial, font=font)
    fallback_draw.text(
        ((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 6),
        initial,
        font=font,
        fill=(255, 255, 255, 255),
    )
    tile.paste(fallback, (0, 0), mask)
    return tile


def _platform_logo(platform: str, size: int = 24) -> Optional[Image.Image]:
    cache_key = (str(platform or ""), int(size))
    if cache_key in _PLATFORM_LOGO_CACHE:
        cached = _PLATFORM_LOGO_CACHE[cache_key]
        return cached.copy() if cached is not None else None
    try:
        with Image.open(PLATFORM_LOGO_DIR / f"{cache_key[0]}.png") as source:
            logo = source.convert("RGBA")
            logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    except Exception:
        _PLATFORM_LOGO_CACHE[cache_key] = None
        return None
    _PLATFORM_LOGO_CACHE[cache_key] = logo.copy()
    return logo


def _chip_width(draw: ImageDraw.ImageDraw, label: str, font: Any, *, has_logo: bool = False) -> int:
    text_width = 0
    if label:
        box = draw.textbbox((0, 0), label, font=font)
        text_width = box[2] - box[0]
    return max(54, text_width + 32 + (34 if has_logo else 0))


def _draw_chip(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    platform: str = "",
    font: Any,
    accent: tuple[int, int, int],
    text: tuple[int, int, int],
) -> int:
    logo = _platform_logo(platform, 24) if platform else None
    width = _chip_width(draw, label, font, has_logo=logo is not None)
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=18, fill=accent + (62,), outline=accent + (155,), width=2)
    text_x = x + 16
    if logo is not None:
        image.alpha_composite(logo, (x + 12, y + 9))
        text_x = x + 46
    if label:
        draw.text((text_x, y + 10), label, font=font, fill=text + (255,))
    return width


def _pack_chips(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    chips: Sequence[tuple[str, tuple[int, int, int], str]],
    *,
    start_x: int,
    start_y: int,
    max_x: int,
    font: Any,
    text: tuple[int, int, int],
    max_rows: int,
) -> None:
    x = start_x
    y = start_y
    row = 0
    for raw_label, accent, platform in chips:
        label = _safe_text(raw_label, 44)
        logo = _platform_logo(platform, 24) if platform else None
        if not label and logo is None:
            continue
        width = _chip_width(draw, label, font, has_logo=logo is not None)
        if x + width > max_x and x > start_x:
            row += 1
            if row >= max_rows:
                break
            x = start_x
            y += 52
        width = _draw_chip(
            image,
            draw,
            x,
            y,
            label,
            platform=platform,
            font=font,
            accent=accent,
            text=text,
        )
        x += width + 10


def render_profile_signature(
    *,
    avatar_bytes: bytes,
    display_name: str,
    server_name: str,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
    platform_entries: Sequence[Mapping[str, Any]] = (),
    style: Mapping[str, Any],
) -> bytes:
    theme, primary, secondary = _resolve_colors(style, avatar_bytes)
    style_key = str(style.get("font") or "clean")
    if style_key not in FONT_STYLES and style_key != CUSTOM_FONT_STYLE_KEY:
        style_key = "clean"
    custom_font = bytes(style.get("custom_font") or b"")
    layout = str(style.get("layout") or "classic")
    frame = str(style.get("avatar_frame") or "glow")
    metrics = _layout_metrics(layout)

    image = _background(style, theme, avatar_bytes)
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_motif(
        draw,
        motif=getattr(theme, "motif", "generic"),
        primary=primary,
        secondary=secondary,
        layout=layout,
    )

    panel = (20, 20, SIGNATURE_WIDTH - 20, SIGNATURE_HEIGHT - 20)
    background_mode = str(style.get("background_mode") or "theme")
    panel_alpha = 176 if background_mode in {"profile", "custom"} else 208
    if layout == "minimal":
        panel_alpha -= 10
    draw.rounded_rectangle(panel, radius=34, fill=tuple(theme.panel) + (panel_alpha,), outline=primary + (140,), width=3)

    avatar_size = metrics["avatar_size"]
    avatar_x = metrics["avatar_x"]
    avatar_y = (SIGNATURE_HEIGHT - avatar_size) // 2
    if frame == "glow":
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow, "RGBA")
        glow_draw.ellipse(
            (avatar_x - 10, avatar_y - 10, avatar_x + avatar_size + 10, avatar_y + avatar_size + 10),
            fill=primary + (135,),
        )
        image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(16)))
        draw = ImageDraw.Draw(image, "RGBA")
    if frame in {"glow", "ring"}:
        draw.ellipse(
            (avatar_x - 6, avatar_y - 6, avatar_x + avatar_size + 6, avatar_y + avatar_size + 6),
            fill=tuple(theme.panel) + (255,),
            outline=primary + (240,),
            width=5 if frame == "glow" else 4,
        )
    image.alpha_composite(_avatar_tile(avatar_bytes, display_name, primary, avatar_size), (avatar_x, avatar_y))
    draw = ImageDraw.Draw(image, "RGBA")

    text = tuple(theme.text)
    content_x = metrics["content_x"]
    right_edge = avatar_x - 34 if layout == "spotlight" else SIGNATURE_WIDTH - 48
    eyebrow_font = _font(19, style_key=style_key, custom_font=custom_font, regular=True)
    name_text = _safe_text(display_name, 80) or "Member"
    name_tile = render_styled_text_tile(
        name_text,
        style_key=style_key,
        start_size=metrics["name_start"],
        min_size=metrics["name_min"],
        max_width=max(320, right_edge - content_x),
        max_height=max(74, metrics["chips_y"] - metrics["name_y"] - 2),
        primary=primary,
        secondary=secondary,
        role="name",
        custom_font_bytes=custom_font,
    )
    chip_font = _font(19, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = _safe_text(server_name, 52).upper() or "DISCORD SERVER"
    draw.text((content_x, metrics["eyebrow_y"]), eyebrow, font=eyebrow_font, fill=primary + (255,))
    image.alpha_composite(name_tile, (content_x, metrics["name_y"] - 6))
    draw = ImageDraw.Draw(image, "RGBA")

    chips: list[tuple[str, tuple[int, int, int], str]] = []
    for index, label in enumerate(role_labels[:3]):
        chips.append((label, primary if index % 2 == 0 else secondary, ""))
    if platform_entries:
        for index, entry in enumerate(platform_entries[:4]):
            platform = str(entry.get("platform") or "")
            if platform not in PLATFORM_SPECS:
                continue
            mode = platform_entry_mode(entry)
            username = _safe_text(entry.get("username"), 32)
            label = "" if mode == "logo" else (username or PLATFORM_SPECS[platform].label)
            chips.append((label, secondary if index % 2 == 0 else primary, platform))
    else:
        for index, label in enumerate(platform_labels[:3]):
            chips.append((label, secondary if index % 2 == 0 else primary, ""))
    for index, label in enumerate(date_labels[:2]):
        chips.append((label, secondary if index % 2 == 0 else primary, ""))
    if not chips:
        chips.append(("Private profile", primary, ""))

    _pack_chips(
        image,
        draw,
        chips,
        start_x=content_x,
        start_y=metrics["chips_y"],
        max_x=right_edge,
        font=chip_font,
        text=text,
        max_rows=metrics["rows"],
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
    style: Mapping[str, Any],
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
    platform_entries: Sequence[Mapping[str, Any]] = (),
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
        platform_entries=[dict(entry) for entry in platform_entries],
        style=dict(style or {}),
    )


__all__ = [
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "render_member_profile_signature",
    "render_profile_signature",
]
