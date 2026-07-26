from __future__ import annotations

"""Compact member-owned profile signature rendering.

The renderer shares safe visual assets with welcome cards, but it never reads
welcome-card settings. Member appearance is stored and rendered separately.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .profile_signature_style import (
    PROFILE_SIGNATURE_HEIGHT,
    PROFILE_SIGNATURE_WIDTH,
    decode_profile_background,
    decode_profile_custom_font,
    normalize_profile_appearance,
)
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
)

SIGNATURE_WIDTH = PROFILE_SIGNATURE_WIDTH
SIGNATURE_HEIGHT = PROFILE_SIGNATURE_HEIGHT
SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT

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


def _image_average(data: Optional[bytes]) -> Optional[tuple[int, int, int]]:
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.fit(source.convert("RGB"), (64, 64), method=Image.Resampling.LANCZOS)
            stat = ImageStat.Stat(image)
            return tuple(max(0, min(255, int(value))) for value in stat.mean[:3])
    except Exception:
        return None


def _resolve_colors(
    appearance: Any,
    *,
    avatar_bytes: bytes,
    profile_banner_bytes: Optional[bytes],
    profile_accent: Optional[tuple[int, int, int]],
) -> tuple[Any, tuple[int, int, int], tuple[int, int, int]]:
    normalized = normalize_profile_appearance(appearance)
    theme = BUILTIN_THEMES[str(normalized["theme_key"])]
    primary = tuple(theme.primary)
    secondary = tuple(theme.secondary)
    mode = str(normalized["color_mode"])

    if mode == "custom":
        parsed_primary = parse_hex_color(normalized.get("custom_primary"))
        parsed_secondary = parse_hex_color(normalized.get("custom_secondary"))
        if parsed_primary:
            primary = parsed_primary
        if parsed_secondary:
            secondary = parsed_secondary
    elif mode == "auto":
        sampled = _image_average(profile_banner_bytes) or _image_average(avatar_bytes)
        if profile_accent:
            primary = tuple(int(value) for value in profile_accent[:3])
        elif sampled:
            primary = sampled
        if sampled:
            secondary = _mix(sampled, tuple(theme.secondary), 0.42)
        else:
            secondary = _mix(primary, tuple(theme.secondary), 0.55)
    return theme, primary, secondary


def _background(
    appearance: Any,
    theme: Any,
    *,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    profile_banner_bytes: Optional[bytes],
) -> Image.Image:
    normalized = normalize_profile_appearance(appearance)
    custom = decode_profile_background(normalized)
    source_bytes = custom
    if source_bytes is None and normalized.get("color_mode") == "auto":
        source_bytes = profile_banner_bytes

    if source_bytes:
        try:
            with Image.open(BytesIO(source_bytes)) as source:
                image = ImageOps.fit(
                    source.convert("RGBA"),
                    (SIGNATURE_WIDTH, SIGNATURE_HEIGHT),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            veil = Image.new("RGBA", image.size, (3, 5, 10, 142))
            return Image.alpha_composite(image, veil)
        except Exception:
            pass

    canvas = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), tuple(theme.background) + (255,))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for x in range(SIGNATURE_WIDTH):
        amount = x / max(1, SIGNATURE_WIDTH - 1)
        base = _mix(tuple(theme.background), tuple(theme.panel), amount)
        color = _mix(base, primary if amount < 0.5 else secondary, 0.13)
        draw.line((x, 0, x, SIGNATURE_HEIGHT), fill=color + (255,))
    return canvas


def _avatar_mask(size: int, shape: str) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    if shape == "rounded":
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=max(16, size // 5), fill=255)
    else:
        draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def _avatar_tile(
    avatar_bytes: bytes,
    display_name: str,
    primary: tuple[int, int, int],
    *,
    size: int,
    shape: str,
) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = _avatar_mask(size, shape)
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
    font = _font(max(34, size // 2), style_key="clean")
    box = fallback_draw.textbbox((0, 0), initial, font=font)
    fallback_draw.text(
        ((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 4),
        initial,
        font=font,
        fill=(255, 255, 255, 255),
    )
    tile.paste(fallback, (0, 0), mask)
    return tile


def _draw_avatar_frame(
    image: Image.Image,
    *,
    x: int,
    y: int,
    size: int,
    shape: str,
    primary: tuple[int, int, int],
    panel: tuple[int, int, int],
    avatar_bytes: bytes,
    display_name: str,
) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    bounds = (x - 8, y - 8, x + size + 8, y + size + 8)
    if shape == "rounded":
        glow_draw.rounded_rectangle(bounds, radius=max(18, size // 5), fill=primary + (125,))
    else:
        glow_draw.ellipse(bounds, fill=primary + (125,))
    glow = glow.filter(ImageFilter.GaussianBlur(13))
    image.alpha_composite(glow)

    draw = ImageDraw.Draw(image, "RGBA")
    frame = (x - 5, y - 5, x + size + 5, y + size + 5)
    if shape == "rounded":
        draw.rounded_rectangle(frame, radius=max(20, size // 5), fill=panel + (255,), outline=primary + (235,), width=4)
    else:
        draw.ellipse(frame, fill=panel + (255,), outline=primary + (235,), width=4)
    image.alpha_composite(
        _avatar_tile(avatar_bytes, display_name, primary, size=size, shape=shape),
        (x, y),
    )


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
    draw.rounded_rectangle((x, y, x + width, y + 32), radius=14, fill=accent + (58,), outline=accent + (150,), width=1)
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
    max_rows: int,
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
            "avatar_size": 108,
            "avatar_x": 40,
            "content_x": 184,
            "eyebrow_y": 45,
            "name_y": 66,
            "name_start": 42,
            "name_min": 27,
            "chip_y": 132,
            "chip_rows": 1,
        }
    if layout == "showcase":
        return {
            "avatar_size": 166,
            "avatar_x": 34,
            "content_x": 238,
            "eyebrow_y": 30,
            "name_y": 52,
            "name_start": 52,
            "name_min": 30,
            "chip_y": 126,
            "chip_rows": 2,
        }
    return {
        "avatar_size": 148,
        "avatar_x": 42,
        "content_x": 220,
        "eyebrow_y": 34,
        "name_y": 55,
        "name_start": 46,
        "name_min": 28,
        "chip_y": 124,
        "chip_rows": 2,
    }


def render_profile_signature(
    *,
    avatar_bytes: bytes,
    display_name: str,
    server_name: str,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
    appearance: Any,
    profile_banner_bytes: Optional[bytes] = None,
    profile_accent: Optional[tuple[int, int, int]] = None,
) -> bytes:
    normalized = normalize_profile_appearance(appearance)
    theme, primary, secondary = _resolve_colors(
        normalized,
        avatar_bytes=avatar_bytes,
        profile_banner_bytes=profile_banner_bytes,
        profile_accent=profile_accent,
    )
    style_key = str(normalized["font_style"])
    custom_font, _font_name = decode_profile_custom_font(normalized)
    layout = str(normalized["layout"])
    shape = str(normalized["avatar_shape"])
    metrics = _layout_metrics(layout)

    image = _background(
        normalized,
        theme,
        primary=primary,
        secondary=secondary,
        profile_banner_bytes=profile_banner_bytes,
    )
    draw = ImageDraw.Draw(image, "RGBA")

    if layout != "minimal":
        draw.ellipse((810, -170, 1180, 200), fill=primary + (24,), outline=primary + (78,), width=2)
        draw.ellipse((880, -90, 1210, 240), fill=secondary + (18,), outline=secondary + (72,), width=2)
        for offset in range(-80, 1180, 92):
            draw.line((offset, 220, offset + 180, 0), fill=secondary + (18,), width=2)

    panel_alpha = 205 if layout == "minimal" else 218
    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    draw.rounded_rectangle(panel, radius=28, fill=tuple(theme.panel) + (panel_alpha,), outline=primary + (125,), width=2)

    avatar_size = metrics["avatar_size"]
    avatar_x = metrics["avatar_x"]
    avatar_y = (SIGNATURE_HEIGHT - avatar_size) // 2
    _draw_avatar_frame(
        image,
        x=avatar_x,
        y=avatar_y,
        size=avatar_size,
        shape=shape,
        primary=primary,
        panel=tuple(theme.panel),
        avatar_bytes=avatar_bytes,
        display_name=display_name,
    )
    draw = ImageDraw.Draw(image, "RGBA")

    text = tuple(theme.text)
    content_x = metrics["content_x"]
    right_edge = SIGNATURE_WIDTH - 42
    eyebrow_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)
    name_text = _safe_text(display_name, 80) or "Member"
    name_font = _fit_font(
        draw,
        name_text,
        style_key=style_key,
        custom_font=custom_font,
        max_width=max(420, right_edge - content_x),
        start=metrics["name_start"],
        minimum=metrics["name_min"],
    )
    chip_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = "MEMBER SIGNATURE"
    if normalized.get("show_server_name", True):
        eyebrow += "  |  " + _safe_text(server_name, 48).upper()
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
        start_y=metrics["chip_y"],
        max_x=right_edge,
        font=chip_font,
        text=text,
        max_rows=metrics["chip_rows"],
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


async def _member_profile_visuals(
    member: discord.Member,
    *,
    enabled: bool,
) -> tuple[Optional[bytes], Optional[tuple[int, int, int]]]:
    if not enabled:
        return None, None
    try:
        from .welcome_card_service import _profile_visuals

        return await _profile_visuals(member)
    except Exception:
        return None, None


async def render_member_profile_signature(
    member: discord.Member,
    *,
    appearance: Any = None,
    cfg: Any = None,
    role_labels: Sequence[str],
    date_labels: Sequence[str],
    platform_labels: Sequence[str],
) -> bytes:
    # ``cfg`` is accepted only for backward compatibility with the first compact
    # runtime. Welcome-card/server appearance is deliberately ignored.
    del cfg
    if appearance is None:
        from .profile_signature_service import get_profile_signature_appearance

        appearance = await get_profile_signature_appearance(int(member.id))
    normalized = normalize_profile_appearance(appearance)
    avatar_task = asyncio.create_task(_avatar_bytes(member))
    visuals_task = asyncio.create_task(
        _member_profile_visuals(
            member,
            enabled=str(normalized.get("color_mode")) == "auto",
        )
    )
    avatar = await avatar_task
    banner, accent = await visuals_task
    return await asyncio.to_thread(
        render_profile_signature,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(getattr(member, "guild", None), "name", None) or "Discord Server",
        role_labels=list(role_labels),
        date_labels=list(date_labels),
        platform_labels=list(platform_labels),
        appearance=normalized,
        profile_banner_bytes=banner,
        profile_accent=accent,
    )


__all__ = [
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "render_member_profile_signature",
    "render_profile_signature",
]
