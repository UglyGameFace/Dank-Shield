from __future__ import annotations

"""Legible live-signature renderer optimized for Discord mobile and desktop."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

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
SIGNATURE_HEIGHT = 340
SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT

PLATFORM_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "platform_logos"
_PLATFORM_LOGO_BYTES_CACHE: dict[str, bytes] = {}


def _safe_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[: max(0, int(limit))]


def _fit_text_width(
    draw: ImageDraw.ImageDraw,
    value: Any,
    font: Any,
    *,
    max_width: int,
    limit: int = 160,
) -> str:
    """Return one safe line that cannot cross its reserved pixel boundary."""
    clean = _safe_text(value, limit)
    width_limit = max(0, int(max_width))
    if not clean or width_limit <= 0:
        return ""

    def width(text: str) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return max(0, box[2] - box[0])

    if width(clean) <= width_limit:
        return clean

    ellipsis = "…"
    if width(ellipsis) > width_limit:
        return ""
    low = 0
    high = len(clean)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = clean[:middle].rstrip() + ellipsis
        if width(candidate) <= width_limit:
            low = middle
        else:
            high = middle - 1
    return clean[:low].rstrip() + ellipsis


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


def _chip_width(draw: ImageDraw.ImageDraw, label: str, font: Any) -> int:
    box = draw.textbbox((0, 0), label, font=font)
    return max(54, box[2] - box[0] + 32)


def _draw_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    font: Any,
    accent: tuple[int, int, int],
    text: tuple[int, int, int],
) -> int:
    width = _chip_width(draw, label, font)
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=18, fill=accent + (62,), outline=accent + (155,), width=2)
    draw.text((x + 16, y + 10), label, font=font, fill=text + (255,))
    return width


def _pack_chips(
    draw: ImageDraw.ImageDraw,
    chips: Sequence[tuple[str, tuple[int, int, int]]],
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
    for raw_label, accent in chips:
        label = _safe_text(raw_label, 44)
        if not label:
            continue
        width = _chip_width(draw, label, font)
        if x + width > max_x and x > start_x:
            row += 1
            if row >= max_rows:
                break
            x = start_x
            y += 52
        width = _draw_chip(draw, x, y, label, font=font, accent=accent, text=text)
        x += width + 10


def _asset_tile(asset_bytes: bytes, size: int, *, fallback_text: str = "") -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if asset_bytes:
        try:
            with Image.open(BytesIO(asset_bytes)) as source:
                image = ImageOps.contain(source.convert("RGBA"), (size - 18, size - 18), Image.Resampling.LANCZOS)
            tile.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
            return tile
        except Exception:
            pass
    if fallback_text:
        draw = ImageDraw.Draw(tile)
        font = _font(max(16, int(size * 0.24)), style_key="clean", regular=True)
        text = _safe_text(fallback_text, 8).upper()
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            ((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 2),
            text,
            font=font,
            fill=(235, 239, 247, 255),
        )
    return tile


def _draw_logo_box(
    image: Image.Image,
    *,
    x: int,
    y: int,
    size: int,
    logo_bytes: bytes,
    label: str,
    accent: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(
        (x, y, x + size, y + size),
        radius=14,
        fill=(7, 10, 17, 215),
        outline=accent + (118,),
        width=2,
    )
    tile = _asset_tile(logo_bytes, size - 8, fallback_text=label[:3])
    image.alpha_composite(tile, (x + 4, y + 4))


def _draw_compact_label(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    label: str,
    font: Any,
    accent: tuple[int, int, int],
) -> int:
    clean = _safe_text(label, 34)
    if not clean:
        return 0
    width = _chip_width(draw, clean, font)
    draw.rounded_rectangle(
        (x, y, x + width, y + 38),
        radius=15,
        fill=(5, 8, 14, 198),
        outline=accent + (135,),
        width=2,
    )
    draw.text((x + 15, y + 8), clean, font=font, fill=(236, 241, 249, 255))
    return width


def _bundled_platform_logo_bytes(platform: str) -> bytes:
    key = str(platform or "")
    if key in _PLATFORM_LOGO_BYTES_CACHE:
        return bytes(_PLATFORM_LOGO_BYTES_CACHE[key])
    try:
        payload = (PLATFORM_LOGO_DIR / f"{key}.png").read_bytes()
    except Exception:
        payload = b""
    _PLATFORM_LOGO_BYTES_CACHE[key] = bytes(payload)
    return bytes(payload)


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
) -> bytes:
    """Render a wide, real-logo profile banner inspired by The 420 Lobby cards."""
    theme, primary, secondary = _resolve_colors(style, avatar_bytes)
    style_key = str(style.get("font") or "clean")
    if style_key not in FONT_STYLES and style_key != CUSTOM_FONT_STYLE_KEY:
        style_key = "clean"
    custom_font = bytes(style.get("custom_font") or b"")

    image = _background(style, theme, avatar_bytes)
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_motif(
        draw,
        motif=getattr(theme, "motif", "generic"),
        primary=primary,
        secondary=secondary,
        layout="classic",
    )

    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    draw.rounded_rectangle(panel, radius=34, fill=tuple(theme.panel) + (220,), outline=primary + (175,), width=3)
    draw.rounded_rectangle((20, 20, 31, SIGNATURE_HEIGHT - 20), radius=6, fill=primary + (245,))

    avatar_size = 218
    avatar_x = 52
    avatar_y = (SIGNATURE_HEIGHT - avatar_size) // 2
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse(
        (avatar_x - 12, avatar_y - 12, avatar_x + avatar_size + 12, avatar_y + avatar_size + 12),
        fill=primary + (135,),
    )
    image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(18)))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse(
        (avatar_x - 7, avatar_y - 7, avatar_x + avatar_size + 7, avatar_y + avatar_size + 7),
        fill=tuple(theme.panel) + (255,), outline=primary + (245,), width=6,
    )
    image.alpha_composite(_avatar_tile(avatar_bytes, display_name, primary, avatar_size), (avatar_x, avatar_y))
    draw = ImageDraw.Draw(image, "RGBA")

    content_x = 306
    eyebrow_font = _font(21, style_key=style_key, custom_font=custom_font, regular=True)
    small_font = _font(20, style_key=style_key, custom_font=custom_font, regular=True)
    chip_font = _font(18, style_key=style_key, custom_font=custom_font, regular=True)
    platform_font = _font(19, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = _safe_text(server_name, 45).upper() or "DISCORD SERVER"
    draw.text((content_x, 51), eyebrow, font=eyebrow_font, fill=primary + (255,))
    name_tile = render_styled_text_tile(
        _safe_text(display_name, 72) or "Member",
        style_key=style_key,
        start_size=56,
        min_size=34,
        max_width=535,
        max_height=82,
        primary=primary,
        secondary=secondary,
        role="name",
        custom_font_bytes=custom_font,
    )
    image.alpha_composite(name_tile, (content_x, 77))
    draw = ImageDraw.Draw(image, "RGBA")

    dates = [str(value) for value in date_labels if str(value).strip()]
    if dates:
        draw.text((content_x, 165), "  •  ".join(dates[:2]), font=small_font, fill=tuple(theme.text) + (220,))

    server_roles = [str(value) for value in server_role_labels if str(value).strip()]
    primary_role = server_roles[0] if server_roles else ""
    if primary_role:
        badge_x = 857
        badge_max_width = 335
        badge = _fit_text_width(
            draw,
            primary_role.upper(),
            small_font,
            max_width=badge_max_width - 32,
            limit=80,
        )
        if badge:
            badge_width = min(badge_max_width, _chip_width(draw, badge, small_font))
            draw.rounded_rectangle(
                (badge_x, 43, badge_x + badge_width, 83),
                radius=16,
                fill=primary + (42,),
                outline=primary + (180,),
                width=2,
            )
            draw.text((badge_x + 16, 52), badge, font=small_font, fill=primary + (255,))

    entries = [dict(entry) for entry in platform_entries if isinstance(entry, Mapping)]
    logo_map = dict(platform_logo_bytes or {})
    logo_x = 850
    logo_y = 100
    logo_size = 58
    for index, entry in enumerate(entries[:5]):
        key = str(entry.get("platform") or "")
        spec = PLATFORM_SPECS.get(key)
        if spec is None:
            continue
        _draw_logo_box(
            image,
            x=logo_x + index * 66,
            y=logo_y,
            size=logo_size,
            logo_bytes=bytes(logo_map.get(key) or b""),
            label=spec.label,
            accent=primary if index % 2 == 0 else secondary,
        )
    draw = ImageDraw.Draw(image, "RGBA")

    shared_names: list[str] = []
    for entry in entries:
        if platform_entry_mode(entry) == "logo":
            continue
        key = str(entry.get("platform") or "")
        spec = PLATFORM_SPECS.get(key)
        username = _safe_text(entry.get("username"), 34)
        if spec is not None and username:
            shared_names.append(f"{spec.label}: {username}")
    if shared_names:
        platform_line = _fit_text_width(
            draw,
            "  •  ".join(shared_names[:2]),
            platform_font,
            max_width=335,
            limit=160,
        )
        if platform_line:
            draw.text((857, 177), platform_line, font=platform_font, fill=secondary + (255,))

    # Keep server roles and member-selected profile tags visually distinct.
    chip_x = content_x
    chip_y = 225
    max_chip_x = 1190
    labels: list[tuple[str, tuple[int, int, int]]] = []
    for value in server_roles[1:3]:
        labels.append((f"Role: {value}", primary))
    for value in list(profile_tag_labels)[:3]:
        labels.append((str(value), secondary))
    for value in list(role_labels)[:2]:
        labels.append((str(value), secondary))
    if not labels and not primary_role:
        labels.append(("Private profile", primary))
    row = 0
    for label, accent in labels:
        clean_label = _safe_text(label, 120)
        width = _chip_width(draw, clean_label, chip_font)
        if chip_x + width > max_chip_x and chip_x > content_x:
            row += 1
            if row >= 2:
                break
            chip_x = content_x
            chip_y += 48
        available_text_width = max(0, max_chip_x - chip_x - 32)
        fitted_label = _fit_text_width(
            draw,
            clean_label,
            chip_font,
            max_width=available_text_width,
            limit=120,
        )
        if not fitted_label:
            continue
        width = _draw_compact_label(
            draw,
            x=chip_x,
            y=chip_y,
            label=fitted_label,
            font=chip_font,
            accent=accent,
        )
        chip_x += width + 10

    # Dynamic server branding on the far right replaces baked-in mockup text.
    server_box_x = 1224
    server_box_y = 87
    server_size = 126
    draw.rounded_rectangle(
        (server_box_x - 10, server_box_y - 10, server_box_x + server_size + 10, server_box_y + server_size + 10),
        radius=26,
        fill=(4, 7, 12, 190),
        outline=secondary + (105,),
        width=2,
    )
    server_tile = _asset_tile(guild_icon_bytes, server_size, fallback_text=server_name[:3])
    image.alpha_composite(server_tile, (server_box_x, server_box_y))
    draw = ImageDraw.Draw(image, "RGBA")
    server_label = _fit_text_width(
        draw,
        server_name,
        chip_font,
        max_width=server_size,
        limit=80,
    )
    if server_label:
        label_box = draw.textbbox((0, 0), server_label, font=chip_font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (server_box_x + (server_size - label_width) / 2, 232),
            server_label,
            font=chip_font,
            fill=tuple(theme.text) + (225,),
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
    role_labels: Sequence[str] = (),
    date_labels: Sequence[str] = (),
    platform_labels: Sequence[str] = (),
    server_role_labels: Sequence[str] = (),
    profile_tag_labels: Sequence[str] = (),
    platform_entries: Sequence[Mapping[str, Any]] = (),
) -> bytes:
    entries = [dict(entry) for entry in platform_entries if isinstance(entry, Mapping)]
    logo_keys = [str(entry.get("platform") or "") for entry in entries[:5]]
    avatar_task = asyncio.create_task(_avatar_bytes(member))
    guild_icon_task = asyncio.create_task(_guild_icon_bytes(member))
    avatar = await avatar_task
    guild_icon = await guild_icon_task
    logos = {key: _bundled_platform_logo_bytes(key) for key in logo_keys if key}
    return await asyncio.to_thread(
        render_profile_signature,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(getattr(member, "guild", None), "name", None) or "Discord Server",
        role_labels=list(role_labels),
        date_labels=list(date_labels),
        platform_labels=list(platform_labels),
        style=dict(style or {}),
        server_role_labels=list(server_role_labels),
        profile_tag_labels=list(profile_tag_labels),
        platform_entries=entries,
        platform_logo_bytes=logos,
        guild_icon_bytes=guild_icon,
    )


__all__ = [
    "SIGNATURE_HEIGHT",
    "SIGNATURE_RATIO",
    "SIGNATURE_WIDTH",
    "render_member_profile_signature",
    "render_profile_signature",
]
