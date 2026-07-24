from __future__ import annotations

import asyncio
import base64
import io
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except Exception as exc:  # pragma: no cover
    Image = ImageDraw = ImageFilter = ImageFont = ImageOps = None  # type: ignore
    _PIL_ERROR: Optional[Exception] = exc
else:
    _PIL_ERROR = None

CARD_WIDTH = 1200
CARD_HEIGHT = 400
CARD_SIZE = (CARD_WIDTH, CARD_HEIGHT)
DEFAULT_THEME = "neon_pulse"


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    bg_a: tuple[int, int, int]
    bg_b: tuple[int, int, int]
    accent_a: tuple[int, int, int]
    accent_b: tuple[int, int, int]
    special: str = ""


THEMES: dict[str, Theme] = {
    "420_lobby": Theme("420_lobby", "420 Lobby Neon", (5, 12, 10), (17, 7, 25), (91, 255, 74), (178, 78, 255), "420"),
    "neon_pulse": Theme("neon_pulse", "Neon Pulse", (5, 14, 26), (18, 6, 31), (36, 222, 255), (182, 73, 255)),
    "royal_gold": Theme("royal_gold", "Royal Gold", (4, 19, 22), (19, 14, 8), (49, 216, 215), (255, 193, 74), "gold"),
    "sunset_arena": Theme("sunset_arena", "Sunset Arena", (25, 7, 7), (4, 16, 39), (255, 117, 33), (47, 132, 255), "arena"),
}


def pillow_available() -> bool:
    return _PIL_ERROR is None and Image is not None


def normalize_theme_name(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "420": "420_lobby",
        "the_420_lobby": "420_lobby",
        "neon": "neon_pulse",
        "cyber": "neon_pulse",
        "gold": "royal_gold",
        "premium_gold": "royal_gold",
        "sunset": "sunset_arena",
        "esports": "sunset_arena",
    }
    key = aliases.get(key, key)
    return key if key in THEMES else DEFAULT_THEME


def ordinal(value: int) -> str:
    number = max(0, int(value or 0))
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
    return default


def _font(size: int, *, bold: bool = False):
    assert ImageFont is not None
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ) if bold else (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, max(8, int(size)))
        except Exception:
            pass
    return ImageFont.load_default()


def _fit_font(draw: Any, text: str, max_width: int, max_size: int, min_size: int, *, bold: bool):
    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, size // 35))
        if box[2] - box[0] <= max_width:
            return font
    return _font(min_size, bold=bold)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _background(theme: Theme, custom_background_bytes: Optional[bytes] = None):
    assert Image is not None and ImageDraw is not None and ImageFilter is not None
    if custom_background_bytes:
        try:
            with Image.open(io.BytesIO(custom_background_bytes)) as source:
                source.load()
                image = _cover(source, CARD_SIZE)
            image.alpha_composite(Image.new("RGBA", CARD_SIZE, (2, 5, 10, 105)))
        except Exception:
            image = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 255))
    else:
        image = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        for x in range(CARD_WIDTH):
            t = x / max(1, CARD_WIDTH - 1)
            color = tuple(_lerp(theme.bg_a[i], theme.bg_b[i], t) for i in range(3))
            draw.line((x, 0, x, CARD_HEIGHT), fill=(*color, 255))

    smoke = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    smoke_draw = ImageDraw.Draw(smoke)
    smoke_draw.ellipse((-80, -80, 360, 300), fill=(*theme.accent_a, 42))
    smoke_draw.ellipse((820, -120, 1280, 250), fill=(*theme.accent_b, 40))
    smoke_draw.ellipse((760, 230, 1260, 520), fill=(*theme.accent_b, 30))
    image.alpha_composite(smoke.filter(ImageFilter.GaussianBlur(38)))

    draw = ImageDraw.Draw(image, "RGBA")
    for pad, alpha in ((18, 180), (25, 80)):
        draw.rounded_rectangle((pad, pad, CARD_WIDTH - pad, CARD_HEIGHT - pad), radius=32, outline=(*theme.accent_a, alpha), width=3)
        draw.arc((pad, pad, CARD_WIDTH - pad, CARD_HEIGHT - pad), 270, 80, fill=(*theme.accent_b, alpha), width=4)
    draw.line((32, 54, 315, 54, 355, 24, 840, 24, 885, 54, 1168, 54), fill=(*theme.accent_b, 150), width=3)
    draw.line((32, 348, 330, 348, 380, 376, 820, 376, 870, 348, 1168, 348), fill=(*theme.accent_a, 150), width=3)

    for x in range(1035, 1150, 24):
        for y in range(72, 145, 22):
            draw.ellipse((x, y, x + 4, y + 4), fill=(*theme.accent_b, 42))

    if theme.special == "420":
        _draw_leaf(draw, (1144, 315), 38, theme.accent_a)
        _draw_mushrooms(draw, (95, 315), theme)
        _draw_controller(draw, (1040, 72, 1148, 138), theme.accent_b)
    elif theme.special == "gold":
        draw.polygon([(1080, 300), (1115, 345), (1045, 345)], outline=(*theme.accent_b, 100))
    else:
        _draw_controller(draw, (1040, 72, 1148, 138), theme.accent_b)
    return image


def _draw_leaf(draw: Any, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    cx, cy = center
    for angle, scale in ((-90, 1.0), (-65, 0.78), (-115, 0.78), (-42, 0.56), (-138, 0.56), (-20, 0.36), (-160, 0.36)):
        radians = math.radians(angle)
        tip_x = cx + int(math.cos(radians) * radius * scale)
        tip_y = cy + int(math.sin(radians) * radius * scale)
        perpendicular = math.radians(angle + 90)
        offset_x = int(math.cos(perpendicular) * radius * 0.13 * scale)
        offset_y = int(math.sin(perpendicular) * radius * 0.13 * scale)
        draw.polygon([(cx, cy), (tip_x + offset_x, tip_y + offset_y), (tip_x, tip_y), (tip_x - offset_x, tip_y - offset_y)], fill=(*color, 180))
    draw.line((cx, cy, cx, cy + int(radius * 0.7)), fill=(*color, 200), width=3)


def _draw_mushrooms(draw: Any, origin: tuple[int, int], theme: Theme) -> None:
    origin_x, origin_y = origin
    for delta_x, scale, color in ((0, 1.0, theme.accent_a), (52, 0.65, theme.accent_b), (-38, 0.52, theme.accent_b)):
        x = origin_x + delta_x
        width = int(62 * scale)
        height = int(32 * scale)
        draw.rounded_rectangle((x - 6, origin_y, x + 6, origin_y + int(42 * scale)), radius=6, fill=(*color, 110))
        draw.pieslice((x - width // 2, origin_y - height // 2, x + width // 2, origin_y + height // 2), 180, 360, fill=(*color, 175))


def _draw_controller(draw: Any, box: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=28, outline=(*color, 60), width=4)
    center_y = (top + bottom) // 2
    draw.line((left + 35, center_y, left + 75, center_y), fill=(*color, 80), width=5)
    draw.line((left + 55, center_y - 18, left + 55, center_y + 18), fill=(*color, 80), width=5)
    draw.ellipse((right - 75, center_y - 20, right - 57, center_y - 2), outline=(*color, 85), width=3)
    draw.ellipse((right - 45, center_y + 2, right - 27, center_y + 20), outline=(*color, 85), width=3)


def _cover(image: Any, size: tuple[int, int]):
    assert Image is not None and ImageOps is not None
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    return ImageOps.fit(image.convert("RGBA"), size, method=resample, centering=(0.5, 0.5))


def _avatar(data: Optional[bytes], size: int, theme: Theme):
    assert Image is not None and ImageDraw is not None
    if data:
        try:
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                return _cover(source, (size, size))
        except Exception:
            pass
    image = Image.new("RGBA", (size, size), (17, 22, 29, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((size * 0.38, size * 0.22, size * 0.62, size * 0.46), fill=(220, 226, 232, 160))
    draw.rounded_rectangle((size * 0.30, size * 0.49, size * 0.70, size * 0.80), radius=size // 10, fill=(220, 226, 232, 135))
    return image


def _paste_avatar(base: Any, avatar: Any, theme: Theme) -> None:
    assert Image is not None and ImageDraw is not None and ImageFilter is not None
    x, y, size = 62, 61, 278
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    glow = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((x - 18, y - 18, x + size + 18, y + size + 18), outline=(*theme.accent_a, 140), width=18)
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(13)))
    base.paste(avatar, (x, y), mask)
    draw = ImageDraw.Draw(base)
    draw.ellipse((x - 7, y - 7, x + size + 7, y + size + 7), outline=(*theme.accent_b, 230), width=8)
    draw.arc((x - 12, y - 12, x + size + 12, y + size + 12), 155, 335, fill=(*theme.accent_a, 255), width=9)
    if theme.special == "420":
        draw.ellipse((286, 296, 348, 358), fill=(8, 13, 17, 230), outline=(*theme.accent_a, 220), width=4)
        _draw_leaf(draw, (317, 326), 24, theme.accent_a)


def _gradient_text(base: Any, xy: tuple[int, int], text: str, font: Any, start: tuple[int, int, int], end: tuple[int, int, int]) -> None:
    assert Image is not None and ImageDraw is not None
    draw = ImageDraw.Draw(base)
    box = draw.textbbox(xy, text, font=font, stroke_width=2)
    mask = Image.new("L", CARD_SIZE, 0)
    ImageDraw.Draw(mask).text(xy, text, font=font, fill=255, stroke_width=2, stroke_fill=255)
    layer = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    width = max(1, box[2] - box[0])
    for column in range(width):
        ratio = column / max(1, width - 1)
        color = tuple(_lerp(start[index], end[index], ratio) for index in range(3))
        layer_draw.line((box[0] + column, box[1] - 6, box[0] + column, box[3] + 6), fill=(*color, 255))
    base.alpha_composite(Image.composite(layer, Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0)), mask))


def render_welcome_card_sync(*, avatar_bytes: Optional[bytes], display_name: str, server_name: str, member_count: int, theme_name: str = DEFAULT_THEME, custom_background_bytes: Optional[bytes] = None) -> bytes:
    if not pillow_available():
        raise RuntimeError(f"Pillow is unavailable: {_PIL_ERROR!r}")
    assert ImageDraw is not None
    theme = THEMES[normalize_theme_name(theme_name)]
    base = _background(theme, custom_background_bytes=custom_background_bytes)
    _paste_avatar(base, _avatar(avatar_bytes, 278, theme), theme)
    draw = ImageDraw.Draw(base)
    text_x, text_right = 390, 1022
    draw.text((text_x, 60), "WELCOME", font=_font(28, bold=True), fill=(248, 250, 252, 245), stroke_width=1, stroke_fill=(0, 0, 0, 180))
    name = str(display_name or "New Member").strip()[:80]
    name_font = _fit_font(draw, name, text_right - text_x, 76, 36, bold=True)
    _gradient_text(base, (text_x, 104), name, name_font, theme.accent_a, theme.accent_b)
    draw.line((text_x, 231, text_right, 231), fill=(220, 226, 232, 80), width=2)
    subtitle = f"to {str(server_name or 'this server')[:80]}  •  You are the {ordinal(member_count)} member!"
    subtitle_font = _fit_font(draw, subtitle, text_right - text_x, 34, 21, bold=False)
    draw.text((text_x, 255), subtitle, font=subtitle_font, fill=(220, 226, 234, 245), stroke_width=1, stroke_fill=(0, 0, 0, 180))
    label_font = _font(18, bold=True)
    label = theme.label.upper()
    label_box = draw.textbbox((0, 0), label, font=label_font)
    draw.text((1140 - (label_box[2] - label_box[0]), 322), label, font=label_font, fill=(*theme.accent_b, 180))
    output = io.BytesIO()
    base.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


async def render_member_welcome_card(member: Any, cfg: Any) -> bytes:
    avatar_bytes: Optional[bytes] = None
    try:
        avatar_bytes = await member.display_avatar.replace(size=512, static_format="png").read()
    except Exception:
        pass
    count = int(getattr(member.guild, "member_count", 0) or 0)
    if count <= 0:
        count = len(list(getattr(member.guild, "members", []) or []))
    custom_background_bytes: Optional[bytes] = None
    encoded_background = str(_cfg_value(cfg, "welcome_card_background_b64", "") or "").strip()
    if encoded_background:
        try:
            custom_background_bytes = base64.b64decode(encoded_background, validate=True)
        except Exception:
            custom_background_bytes = None

    return await asyncio.to_thread(
        render_welcome_card_sync,
        avatar_bytes=avatar_bytes,
        display_name=str(getattr(member, "display_name", "") or member),
        server_name=str(getattr(member.guild, "name", "") or "this server"),
        member_count=count,
        theme_name=normalize_theme_name(_cfg_value(cfg, "welcome_card_theme", DEFAULT_THEME)),
        custom_background_bytes=custom_background_bytes,
    )


async def build_welcome_card_file(member: Any, cfg: Any) -> Any:
    import discord

    payload = await render_member_welcome_card(member, cfg)
    return discord.File(io.BytesIO(payload), filename=f"welcome-{member.guild.id}-{member.id}.png")


__all__ = [
    "CARD_HEIGHT", "CARD_SIZE", "CARD_WIDTH", "DEFAULT_THEME", "THEMES", "Theme",
    "build_welcome_card_file", "normalize_theme_name", "ordinal", "pillow_available",
    "render_member_welcome_card", "render_welcome_card_sync",
]
