from __future__ import annotations

"""Compact, independently customizable member profile signatures."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

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
    style = FONT_STYLES.get(style_key) or FONT_STYLES.get("clean") or next(iter(FONT_STYLES.values()))
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


def _avatar_image(avatar_bytes: bytes, size: tuple[int, int]) -> Optional[Image.Image]:
    if not avatar_bytes:
        return None
    try:
        with Image.open(BytesIO(avatar_bytes)) as source:
            return ImageOps.fit(
                source.convert("RGBA"),
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except Exception:
        return None


def _avatar_colors(avatar_bytes: bytes, fallback: tuple[int, int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    avatar = _avatar_image(avatar_bytes, (64, 64))
    if avatar is None:
        return fallback, _mix(fallback, (255, 255, 255), 0.34)
    try:
        rgb = avatar.convert("RGB")
        mean = tuple(int(value) for value in ImageStat.Stat(rgb).mean[:3])
        quantized = rgb.quantize(colors=6, method=Image.Quantize.MEDIANCUT).convert("RGB")
        colors = quantized.getcolors(maxcolors=4096) or []
        ranked = sorted(colors, key=lambda item: item[0], reverse=True)
        secondary = ranked[1][1] if len(ranked) > 1 else _mix(mean, (255, 255, 255), 0.3)
        primary = tuple(max(45, min(235, int(part))) for part in mean)
        secondary = tuple(max(45, min(235, int(part))) for part in secondary)
        return primary, secondary
    except Exception:
        return fallback, _mix(fallback, (255, 255, 255), 0.34)


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


def _background(
    style: Mapping[str, Any],
    theme: Any,
    avatar_bytes: bytes,
) -> Image.Image:
    mode = str(style.get("background_mode") or "theme")
    if mode == "custom":
        custom = bytes(style.get("custom_background") or b"")
        image = _avatar_image(custom, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if image is not None:
            veil = Image.new("RGBA", image.size, (3, 5, 10, 132))
            return Image.alpha_composite(image, veil)
    if mode == "profile":
        image = _avatar_image(avatar_bytes, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT))
        if image is not None:
            image = image.filter(ImageFilter.GaussianBlur(18))
            veil = Image.new("RGBA", image.size, (4, 6, 12, 150))
            return Image.alpha_composite(image, veil)

    canvas = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), tuple(theme.background) + (255,))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for x in range(SIGNATURE_WIDTH):
        amount = x / max(1, SIGNATURE_WIDTH - 1)
        color = _mix(tuple(theme.background), tuple(theme.panel), amount)
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=color + (255,))
    return canvas


def _draw_theme_motif(
    draw: ImageDraw.ImageDraw,
    *,
    motif: str,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    layout: str,
) -> None:
    key = str(motif or "generic").strip().lower()
    if key == "minimal" or layout == "minimal":
        draw.rounded_rectangle((36, 22, SIGNATURE_WIDTH - 36, 28), radius=3, fill=primary + (150,))
        draw.rounded_rectangle((36, 31, 360, 34), radius=2, fill=secondary + (90,))
        return
    if key == "420":
        for radius, alpha, color in (
            (150, 30, primary),
            (112, 42, secondary),
            (76, 54, primary),
        ):
            draw.ellipse(
                (SIGNATURE_WIDTH - 185 - radius, 110 - radius, SIGNATURE_WIDTH - 185 + radius, 110 + radius),
                outline=color + (alpha,),
                width=4,
            )
        stem_x = SIGNATURE_WIDTH - 178
        draw.line((stem_x, 188, stem_x + 24, 66), fill=primary + (115,), width=5)
        for y, direction, color in (
            (92, -1, primary),
            (116, 1, secondary),
            (140, -1, secondary),
            (162, 1, primary),
        ):
            x = stem_x + int((188 - y) * 0.2)
            tip_x = x + (42 * direction)
            draw.polygon(
                [(x, y), (tip_x, y - 18), (tip_x - (7 * direction), y + 14)],
                fill=color + (35,),
                outline=color + (95,),
            )
        return
    if key == "cyber":
        for x in range(690, SIGNATURE_WIDTH + 1, 48):
            draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=primary + (24,), width=1)
        for y in range(18, SIGNATURE_HEIGHT, 36):
            draw.line((650, y, SIGNATURE_WIDTH, y), fill=secondary + (22,), width=1)
        points = ((738, 48), (822, 48), (822, 92), (920, 92), (920, 146), (1030, 146))
        draw.line(points, fill=primary + (115,), width=3, joint="curve")
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=secondary + (170,))
        return
    if key == "premium":
        draw.line((690, 35, 1040, 35), fill=secondary + (115,), width=3)
        draw.line((740, 184, 1040, 184), fill=primary + (95,), width=2)
        for x in (725, 810, 895, 980):
            draw.polygon(
                [(x, 110), (x + 18, 92), (x + 36, 110), (x + 18, 128)],
                fill=secondary + (24,),
                outline=secondary + (90,),
            )
        return
    if key == "community":
        for x, y, radius, color in (
            (770, 72, 54, primary),
            (850, 126, 72, secondary),
            (960, 76, 46, primary),
            (1030, 145, 58, secondary),
        ):
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=color + (18,),
                outline=color + (68,),
                width=3,
            )
        return
    if key == "esports":
        for index in range(6):
            x = 660 + index * 78
            color = primary if index % 2 == 0 else secondary
            draw.polygon(
                [(x, 220), (x + 80, 220), (x + 220, 0), (x + 140, 0)],
                fill=color + (18 + index * 3,),
                outline=color + (55,),
            )
        return
    draw.ellipse((810, -170, 1180, 200), fill=primary + (24,), outline=primary + (78,), width=2)
    draw.ellipse((880, -90, 1210, 240), fill=secondary + (18,), outline=secondary + (72,), width=2)
    for offset in range(-80, 1180, 92):
        draw.line((offset, 220, offset + 180, 0), fill=secondary + (20,), width=2)


def _avatar_tile(avatar_bytes: bytes, display_name: str, primary: tuple[int, int, int], size: int) -> Image.Image:
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
    font = _font(max(34, int(size * 0.46)), style_key="clean")
    box = fallback_draw.textbbox((0, 0), initial, font=font)
    fallback_draw.text(
        ((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 5),
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


def _layout_metrics(layout: str) -> dict[str, int]:
    if layout == "minimal":
        return {
            "avatar_size": 118,
            "avatar_x": 48,
            "content_x": 194,
            "name_start": 40,
            "name_min": 26,
            "eyebrow_y": 42,
            "name_y": 65,
            "chips_y": 126,
            "rows": 1,
        }
    if layout == "spotlight":
        return {
            "avatar_size": 168,
            "avatar_x": 862,
            "content_x": 52,
            "name_start": 50,
            "name_min": 30,
            "eyebrow_y": 32,
            "name_y": 54,
            "chips_y": 126,
            "rows": 2,
        }
    return {
        "avatar_size": _AVATAR_SIZE,
        "avatar_x": 42,
        "content_x": 220,
        "name_start": 46,
        "name_min": 28,
        "eyebrow_y": 34,
        "name_y": 55,
        "chips_y": 124,
        "rows": 2,
    }


def render_profile_signature(
    *,
    avatar_bytes: bytes,
    display_name: str,
    server_name: str,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
    style: Mapping[str, Any],
) -> bytes:
    theme, primary, secondary = _resolve_colors(style, avatar_bytes)
    style_key = str(style.get("font") or "clean")
    custom_font = bytes(style.get("custom_font") or b"")
    layout = str(style.get("layout") or "classic")
    frame = str(style.get("avatar_frame") or "glow")
    metrics = _layout_metrics(layout)

    image = _background(style, theme, avatar_bytes)
    draw = ImageDraw.Draw(image, "RGBA")

    _draw_theme_motif(
        draw,
        motif=getattr(theme, "motif", "generic"),
        primary=primary,
        secondary=secondary,
        layout=layout,
    )

    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    panel_alpha = 205 if layout == "minimal" else 218
    draw.rounded_rectangle(panel, radius=28, fill=tuple(theme.panel) + (panel_alpha,), outline=primary + (125,), width=2)

    avatar_size = metrics["avatar_size"]
    avatar_x = metrics["avatar_x"]
    avatar_y = (SIGNATURE_HEIGHT - avatar_size) // 2
    if frame == "glow":
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow, "RGBA")
        glow_draw.ellipse(
            (avatar_x - 8, avatar_y - 8, avatar_x + avatar_size + 8, avatar_y + avatar_size + 8),
            fill=primary + (130,),
        )
        image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(13)))
        draw = ImageDraw.Draw(image, "RGBA")
    if frame in {"glow", "ring"}:
        draw.ellipse(
            (avatar_x - 5, avatar_y - 5, avatar_x + avatar_size + 5, avatar_y + avatar_size + 5),
            fill=tuple(theme.panel) + (255,),
            outline=primary + (235,),
            width=4 if frame == "glow" else 3,
        )
    image.alpha_composite(_avatar_tile(avatar_bytes, display_name, primary, avatar_size), (avatar_x, avatar_y))
    draw = ImageDraw.Draw(image, "RGBA")

    text = tuple(theme.text)
    content_x = metrics["content_x"]
    right_edge = avatar_x - 28 if layout == "spotlight" else SIGNATURE_WIDTH - 42
    eyebrow_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)
    name_text = _safe_text(display_name, 80) or "Member"
    name_font = _fit_font(
        draw,
        name_text,
        style_key=style_key,
        custom_font=custom_font,
        max_width=max(300, right_edge - content_x),
        start=metrics["name_start"],
        minimum=metrics["name_min"],
    )
    chip_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = f"MEMBER SIGNATURE  •  {_safe_text(server_name, 48).upper()}"
    draw.text((content_x, metrics["eyebrow_y"]), eyebrow, font=eyebrow_font, fill=primary + (255,))
    draw.text(
        (content_x, metrics["name_y"]),
        name_text,
        font=name_font,
        fill=text + (255,),
        stroke_width=1,
        stroke_fill=(0, 0, 0, 170),
    )

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
        style=dict(style or {}),
    )


__all__ = [
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "render_member_profile_signature",
    "render_profile_signature",
]
