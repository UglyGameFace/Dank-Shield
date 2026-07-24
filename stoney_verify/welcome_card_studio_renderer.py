from __future__ import annotations

"""Deterministic welcome-card typography and color studio.

This module is the live renderer used by welcome_card_service. It reuses the
battle-tested background, avatar, and palette primitives from
welcome_card_renderer while owning all production typography and visual picker
catalogs. Font presets remain visibly distinct even when the host has no font
packages because their differences are rendered as transforms/effects rather
than depending only on font filenames.
"""

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import welcome_card_renderer as legacy

CARD_WIDTH = legacy.CARD_WIDTH
CARD_HEIGHT = legacy.CARD_HEIGHT
CARD_RATIO = legacy.CARD_RATIO
MAX_CUSTOM_BACKGROUND_BYTES = legacy.MAX_CUSTOM_BACKGROUND_BYTES
BUILTIN_THEMES = legacy.BUILTIN_THEMES
DEFAULT_THEME_KEY = legacy.DEFAULT_THEME_KEY
DEFAULT_COLOR_MODE = legacy.DEFAULT_COLOR_MODE
COLOR_MODES = legacy.COLOR_MODES
WelcomeCardPalette = legacy.WelcomeCardPalette
WelcomeCardTheme = legacy.WelcomeCardTheme

DEFAULT_FONT_STYLE_KEY = "neon"


@dataclass(frozen=True)
class WelcomeCardFontStyle:
    key: str
    label: str
    description: str
    family: str
    effect: str
    welcome_size: int
    name_start_size: int
    name_min_size: int
    subtitle_start_size: int
    subtitle_min_size: int
    tracking: int = 0
    name_stroke: int = 2
    glow_radius: int = 8
    glow_alpha: int = 120
    x_scale: float = 1.0
    shear: float = 0.0
    uppercase_name: bool = False


@dataclass(frozen=True)
class WelcomeColorPreset:
    key: str
    label: str
    description: str
    primary: str
    secondary: str
    emoji: str


@dataclass(frozen=True)
class WelcomeColorSwatch:
    key: str
    label: str
    hex_value: str
    emoji: str


_SANS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
)
_SANS_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
)
_MONO_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
)
_MONO_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
)
_SERIF_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
)
_SERIF_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
)


FONT_STYLES: dict[str, WelcomeCardFontStyle] = {
    "neon": WelcomeCardFontStyle(
        key="neon", label="Neon Display",
        description="Bright condensed glow with a clean gaming look.",
        family="sans", effect="neon", welcome_size=58,
        name_start_size=102, name_min_size=46,
        subtitle_start_size=32, subtitle_min_size=21,
        name_stroke=2, glow_radius=13, glow_alpha=165, x_scale=0.94,
    ),
    "tech": WelcomeCardFontStyle(
        key="tech", label="Tech Circuit",
        description="Tracked mono lettering with digital scan cuts.",
        family="mono", effect="tech", welcome_size=52,
        name_start_size=84, name_min_size=40,
        subtitle_start_size=28, subtitle_min_size=19,
        tracking=3, name_stroke=2, glow_radius=7, glow_alpha=125,
        x_scale=0.94, uppercase_name=True,
    ),
    "bold": WelcomeCardFontStyle(
        key="bold", label="Heavy Impact",
        description="Thick, wide, high-contrast esports lettering.",
        family="sans", effect="impact", welcome_size=60,
        name_start_size=100, name_min_size=44,
        subtitle_start_size=32, subtitle_min_size=21,
        name_stroke=5, glow_radius=5, glow_alpha=90, x_scale=1.03,
    ),
    "clean": WelcomeCardFontStyle(
        key="clean", label="Clean Modern",
        description="Polished minimal type with restrained effects.",
        family="sans", effect="clean", welcome_size=50,
        name_start_size=88, name_min_size=42,
        subtitle_start_size=30, subtitle_min_size=20,
        name_stroke=1, glow_radius=2, glow_alpha=45,
    ),
    "chrome": WelcomeCardFontStyle(
        key="chrome", label="Chrome Luxe",
        description="Metallic highlight treatment with premium depth.",
        family="serif", effect="chrome", welcome_size=54,
        name_start_size=94, name_min_size=42,
        subtitle_start_size=30, subtitle_min_size=20,
        name_stroke=3, glow_radius=5, glow_alpha=90, x_scale=1.01,
    ),
    "outline": WelcomeCardFontStyle(
        key="outline", label="Hollow Outline",
        description="Open center with a bright dual-color border.",
        family="sans", effect="outline", welcome_size=56,
        name_start_size=98, name_min_size=44,
        subtitle_start_size=30, subtitle_min_size=20,
        tracking=1, name_stroke=5, glow_radius=8, glow_alpha=125,
    ),
    "arcade": WelcomeCardFontStyle(
        key="arcade", label="Arcade Pixel",
        description="Chunky pixel treatment for retro gaming servers.",
        family="mono", effect="arcade", welcome_size=52,
        name_start_size=88, name_min_size=40,
        subtitle_start_size=28, subtitle_min_size=19,
        tracking=2, name_stroke=2, glow_radius=4, glow_alpha=85,
        uppercase_name=True,
    ),
    "street": WelcomeCardFontStyle(
        key="street", label="Street Slant",
        description="Skewed layered type with a bold offset shadow.",
        family="sans", effect="street", welcome_size=56,
        name_start_size=96, name_min_size=42,
        subtitle_start_size=30, subtitle_min_size=20,
        name_stroke=3, glow_radius=5, glow_alpha=85, shear=0.18,
    ),
    "future": WelcomeCardFontStyle(
        key="future", label="Future Wide",
        description="Wide tracked capitals with angular tech cuts.",
        family="sans", effect="future", welcome_size=52,
        name_start_size=84, name_min_size=38,
        subtitle_start_size=28, subtitle_min_size=19,
        tracking=4, name_stroke=2, glow_radius=7, glow_alpha=120,
        x_scale=1.08, uppercase_name=True,
    ),
    "soft": WelcomeCardFontStyle(
        key="soft", label="Soft Luxe",
        description="Smooth serif styling with a softer profile glow.",
        family="serif", effect="soft", welcome_size=52,
        name_start_size=92, name_min_size=42,
        subtitle_start_size=30, subtitle_min_size=20,
        name_stroke=1, glow_radius=11, glow_alpha=95, shear=0.08,
    ),
}


COLOR_PRESETS: dict[str, WelcomeColorPreset] = {
    "electric_420": WelcomeColorPreset("electric_420", "Electric 420", "Lime and purple neon.", "#5AFF2D", "#AE4BFF", "🟢"),
    "cyberwave": WelcomeColorPreset("cyberwave", "Cyberwave", "Cyan and ultraviolet.", "#22DCFF", "#BC42FF", "🔵"),
    "toxic_night": WelcomeColorPreset("toxic_night", "Toxic Night", "Acid lime and electric cyan.", "#C6FF00", "#00E5FF", "🟡"),
    "purple_haze": WelcomeColorPreset("purple_haze", "Purple Haze", "Violet and hot pink.", "#A855F7", "#EC4899", "🟣"),
    "hot_pink": WelcomeColorPreset("hot_pink", "Hot Pink", "Candy pink and deep violet.", "#FF3CAC", "#784BA0", "🩷"),
    "firestorm": WelcomeColorPreset("firestorm", "Firestorm", "Hot orange and gold.", "#FF5A36", "#FFC857", "🟠"),
    "sunset": WelcomeColorPreset("sunset", "Sunset Rush", "Orange fading into crimson.", "#FF7A18", "#AF002D", "🔴"),
    "ocean": WelcomeColorPreset("ocean", "Ocean Drive", "Aqua and saturated blue.", "#00D4FF", "#0066FF", "🌊"),
    "midnight_ice": WelcomeColorPreset("midnight_ice", "Midnight Ice", "Icy blue and indigo.", "#5BE7FF", "#647BFF", "🧊"),
    "emerald": WelcomeColorPreset("emerald", "Emerald Glow", "Emerald and fresh lime.", "#22E58B", "#7CFF4F", "🟢"),
    "gold": WelcomeColorPreset("gold", "Premium Gold", "Bright gold and bronze.", "#FFD166", "#B8860B", "🟡"),
    "royal": WelcomeColorPreset("royal", "Royal Crown", "Royal violet and gold.", "#7C3AED", "#F6C85F", "👑"),
    "rivalry": WelcomeColorPreset("rivalry", "Red vs Blue", "Competitive red and blue.", "#FF3B30", "#3478F6", "⚔️"),
    "mint_lavender": WelcomeColorPreset("mint_lavender", "Mint Lavender", "Soft mint and lavender.", "#5EEAD4", "#C084FC", "🟦"),
    "rose_gold": WelcomeColorPreset("rose_gold", "Rose Gold", "Rose pink and warm gold.", "#FF8FA3", "#F6C453", "🌹"),
    "monochrome": WelcomeColorPreset("monochrome", "Monochrome", "White and graphite.", "#F4F4F5", "#71717A", "⚪"),
}


COLOR_SWATCHES: dict[str, WelcomeColorSwatch] = {
    "red": WelcomeColorSwatch("red", "Red", "#FF3B30", "🔴"),
    "orange": WelcomeColorSwatch("orange", "Orange", "#FF7A18", "🟠"),
    "amber": WelcomeColorSwatch("amber", "Amber", "#FFB020", "🟠"),
    "yellow": WelcomeColorSwatch("yellow", "Yellow", "#FFD60A", "🟡"),
    "lime": WelcomeColorSwatch("lime", "Lime", "#A7F432", "🟢"),
    "green": WelcomeColorSwatch("green", "Green", "#34C759", "🟢"),
    "emerald": WelcomeColorSwatch("emerald", "Emerald", "#22E58B", "🟢"),
    "teal": WelcomeColorSwatch("teal", "Teal", "#20C7B7", "🔵"),
    "cyan": WelcomeColorSwatch("cyan", "Cyan", "#22DCFF", "🔵"),
    "sky": WelcomeColorSwatch("sky", "Sky Blue", "#5AC8FA", "🔵"),
    "blue": WelcomeColorSwatch("blue", "Blue", "#3478F6", "🔵"),
    "indigo": WelcomeColorSwatch("indigo", "Indigo", "#5856D6", "🟣"),
    "violet": WelcomeColorSwatch("violet", "Violet", "#8B5CF6", "🟣"),
    "purple": WelcomeColorSwatch("purple", "Purple", "#AE4BFF", "🟣"),
    "fuchsia": WelcomeColorSwatch("fuchsia", "Fuchsia", "#D946EF", "🟣"),
    "pink": WelcomeColorSwatch("pink", "Pink", "#FF3CAC", "🩷"),
    "rose": WelcomeColorSwatch("rose", "Rose", "#FF5D8F", "🩷"),
    "white": WelcomeColorSwatch("white", "White", "#F8FAFC", "⚪"),
    "silver": WelcomeColorSwatch("silver", "Silver", "#A1A1AA", "⚪"),
    "gold": WelcomeColorSwatch("gold", "Gold", "#FFD166", "🟡"),
}


def normalize_theme_key(value: Any) -> str:
    return legacy.normalize_theme_key(value)


def normalize_color_mode(value: Any) -> str:
    return legacy.normalize_color_mode(value)


def normalize_hex_color(value: Any) -> str:
    return legacy.normalize_hex_color(value)


def parse_hex_color(value: Any) -> Optional[tuple[int, int, int]]:
    return legacy.parse_hex_color(value)


def validate_custom_background(data: bytes) -> None:
    legacy.validate_custom_background(data)


def resolve_card_palette(**kwargs: Any) -> WelcomeCardPalette:
    return legacy.resolve_card_palette(**kwargs)


def normalize_font_style_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "default": "neon", "mono": "tech", "heavy": "bold",
        "impact": "bold", "modern": "clean", "metal": "chrome",
        "hollow": "outline", "pixel": "arcade", "slant": "street",
        "futuristic": "future", "luxury": "soft",
    }
    key = aliases.get(key, key)
    return key if key in FONT_STYLES else DEFAULT_FONT_STYLE_KEY


def font_style_choices() -> list[tuple[str, str]]:
    return [(style.key, style.label) for style in FONT_STYLES.values()]


def color_mode_choices() -> list[tuple[str, str]]:
    return list(COLOR_MODES.items())


def color_preset_choices() -> list[tuple[str, str]]:
    return [(preset.key, preset.label) for preset in COLOR_PRESETS.values()]


def _family_candidates(family: str, *, bold: bool) -> tuple[str, ...]:
    if family == "mono":
        return _MONO_BOLD if bold else _MONO_REGULAR
    if family == "serif":
        return _SERIF_BOLD if bold else _SERIF_REGULAR
    return _SANS_BOLD if bold else _SANS_REGULAR


def _font(size: int, *, family: str = "sans", bold: bool = False) -> ImageFont.ImageFont:
    for path in _family_candidates(family, bold=bold):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, max(8, int(size)))
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


def _gradient(
    size: tuple[int, int],
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    *,
    vertical: bool = False,
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = image.load()
    span = max(1, (height if vertical else width) - 1)
    for y in range(height):
        for x in range(width):
            t = (y if vertical else x) / span
            pixels[x, y] = tuple(
                int(start[index] * (1 - t) + end[index] * t)
                for index in range(3)
            ) + (255,)
    return image


def _tracked_text_mask(
    text: str,
    *,
    font: ImageFont.ImageFont,
    tracking: int,
    stroke_width: int,
) -> Image.Image:
    probe = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(probe)
    if tracking <= 0:
        box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        width = max(1, box[2] - box[0])
        height = max(1, box[3] - box[1])
        padding = max(8, stroke_width * 3)
        mask = Image.new("L", (width + padding * 2, height + padding * 2), 0)
        ImageDraw.Draw(mask).text(
            (padding - box[0], padding - box[1]), text, font=font, fill=255,
            stroke_width=stroke_width, stroke_fill=255,
        )
        return mask
    widths: list[int] = []
    boxes: list[tuple[int, int, int, int]] = []
    max_height = 1
    for character in text:
        box = draw.textbbox((0, 0), character, font=font, stroke_width=stroke_width)
        boxes.append(box)
        width = max(1, box[2] - box[0])
        widths.append(width)
        max_height = max(max_height, box[3] - box[1])
    padding = max(8, stroke_width * 3)
    total_width = sum(widths) + max(0, len(widths) - 1) * tracking
    mask = Image.new("L", (max(1, total_width) + padding * 2, max_height + padding * 2), 0)
    md = ImageDraw.Draw(mask)
    cursor = padding
    for character, width, box in zip(text, widths, boxes):
        md.text(
            (cursor - box[0], padding - box[1]), character, font=font,
            fill=255, stroke_width=stroke_width, stroke_fill=255,
        )
        cursor += width + tracking
    return mask


def _crop_mask(mask: Image.Image) -> Image.Image:
    bbox = mask.getbbox()
    return mask.crop(bbox) if bbox else Image.new("L", (1, 1), 0)


def _apply_mask_transform(mask: Image.Image, style: WelcomeCardFontStyle) -> Image.Image:
    transformed = _crop_mask(mask)
    if style.x_scale != 1.0:
        transformed = transformed.resize(
            (max(1, int(transformed.width * style.x_scale)), transformed.height),
            Image.Resampling.LANCZOS,
        )
    if style.shear:
        offset = int(abs(style.shear) * transformed.height) + 4
        transformed = transformed.transform(
            (transformed.width + offset, transformed.height),
            Image.Transform.AFFINE,
            (1, -style.shear, offset if style.shear > 0 else 0, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
        transformed = _crop_mask(transformed)
    if style.effect == "arcade":
        small = transformed.resize(
            (max(1, transformed.width // 3), max(1, transformed.height // 3)),
            Image.Resampling.BILINEAR,
        )
        transformed = small.resize(transformed.size, Image.Resampling.NEAREST)
    if style.effect in {"tech", "future"}:
        cut = Image.new("L", transformed.size, 255)
        cd = ImageDraw.Draw(cut)
        spacing = 12 if style.effect == "tech" else 17
        for y in range(6, transformed.height, spacing):
            cd.rectangle((0, y, transformed.width, y + 1), fill=0)
        transformed = ImageChops.multiply(transformed, cut)
    if style.effect == "street":
        transformed = transformed.rotate(-1.2, resample=Image.Resampling.BICUBIC, expand=True)
        transformed = _crop_mask(transformed)
    return transformed


def _fitted_mask(
    text: str,
    *,
    style: WelcomeCardFontStyle,
    start_size: int,
    min_size: int,
    max_width: int,
    role: str,
    stroke_width: int,
) -> tuple[str, Image.Image]:
    candidate_text = text.upper() if style.uppercase_name and role == "name" else text
    minimum = max(8, int(min_size))
    for size in range(max(start_size, minimum), minimum - 1, -2):
        font = _font(size, family=style.family, bold=True)
        mask = _tracked_text_mask(
            candidate_text, font=font,
            tracking=style.tracking if role in {"name", "welcome"} else 0,
            stroke_width=stroke_width,
        )
        transformed = _apply_mask_transform(mask, style)
        if transformed.width <= max_width:
            return candidate_text, transformed
    suffix = "..."
    base = candidate_text
    while base:
        attempt = base.rstrip() + suffix
        font = _font(minimum, family=style.family, bold=True)
        mask = _tracked_text_mask(
            attempt, font=font,
            tracking=style.tracking if role in {"name", "welcome"} else 0,
            stroke_width=stroke_width,
        )
        transformed = _apply_mask_transform(mask, style)
        if transformed.width <= max_width:
            return attempt, transformed
        base = base[:-1]
    return "", Image.new("L", (1, 1), 0)


def _outline_from_mask(mask: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        return mask.copy()
    size = max(3, width * 2 + 1)
    if size % 2 == 0:
        size += 1
    return mask.filter(ImageFilter.MaxFilter(size=size))


def _chrome_gradient(
    size: tuple[int, int],
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = image.load()
    stops = (
        (0.0, (255, 255, 255)), (0.22, secondary),
        (0.48, (245, 248, 255)), (0.58, primary),
        (1.0, tuple(max(0, int(part * 0.38)) for part in primary)),
    )
    for y in range(height):
        t = y / max(1, height - 1)
        left, right = stops[0], stops[-1]
        for index in range(len(stops) - 1):
            if stops[index][0] <= t <= stops[index + 1][0]:
                left, right = stops[index], stops[index + 1]
                break
        local = (t - left[0]) / max(0.0001, right[0] - left[0])
        color = tuple(
            int(left[1][channel] * (1 - local) + right[1][channel] * local)
            for channel in range(3)
        )
        for x in range(width):
            pixels[x, y] = (*color, 255)
    return image


def _composite_mask_text(
    canvas: Image.Image,
    *,
    position: tuple[int, int],
    mask: Image.Image,
    style: WelcomeCardFontStyle,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    role: str,
) -> tuple[int, int]:
    mask = _crop_mask(mask)
    width, height = mask.size
    padding = max(14, style.glow_radius * 2 + 4, style.name_stroke * 4)
    size = (width + padding * 2, height + padding * 2)
    centered = Image.new("L", size, 0)
    centered.paste(mask, (padding, padding))
    outline = _outline_from_mask(centered, style.name_stroke)
    target = (int(position[0]) - padding, int(position[1]) - padding)
    if style.effect in {"impact", "street", "arcade"}:
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow.putalpha(outline.point(lambda value: int(value * 0.72)))
        offset = (target[0] + (7 if style.effect == "street" else 5), target[1] + 6)
        canvas.alpha_composite(shadow, offset)
    if style.glow_radius > 0 and style.glow_alpha > 0:
        blurred = outline.filter(ImageFilter.GaussianBlur(style.glow_radius))
        alpha = blurred.point(lambda value: int(value * min(255, style.glow_alpha) / 255))
        glow = Image.new("RGBA", size, (*primary, 0))
        glow.putalpha(alpha)
        canvas.alpha_composite(glow, target)
    if style.effect == "outline":
        outer = ImageChops.subtract(outline, centered)
        gradient = _gradient(size, primary, secondary)
        gradient.putalpha(outer)
        canvas.alpha_composite(gradient, target)
        inner = Image.new("RGBA", size, (4, 7, 13, 220))
        inner.putalpha(centered.point(lambda value: int(value * 0.60)))
        canvas.alpha_composite(inner, target)
        return width, height
    dark_outline = Image.new("RGBA", size, (2, 4, 9, 238))
    dark_outline.putalpha(outline)
    canvas.alpha_composite(dark_outline, target)
    if style.effect == "chrome":
        fill = _chrome_gradient(size, primary, secondary)
    elif role == "welcome":
        fill = _gradient(
            size, (255, 255, 255),
            tuple(min(255, part + 95) for part in primary), vertical=True,
        )
    elif style.effect == "clean":
        fill = _gradient(size, tuple(min(255, part + 45) for part in primary), secondary)
    elif style.effect == "soft":
        fill = _gradient(
            size, tuple(min(255, part + 70) for part in primary),
            tuple(min(255, part + 45) for part in secondary),
        )
    else:
        fill = _gradient(size, primary, secondary)
    fill.putalpha(centered)
    canvas.alpha_composite(fill, target)
    if style.effect == "chrome":
        highlight = Image.new("L", size, 0)
        hd = ImageDraw.Draw(highlight)
        hd.rectangle(
            (0, padding + max(1, height // 5), size[0], padding + max(2, height // 5 + 2)),
            fill=160,
        )
        highlight = ImageChops.multiply(highlight, centered)
        shine = Image.new("RGBA", size, (255, 255, 255, 0))
        shine.putalpha(highlight)
        canvas.alpha_composite(shine, target)
    return width, height


def _draw_sparkle(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    size: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    half = max(3, size // 2)
    points = [
        (cx, cy - half), (cx + half // 3, cy - half // 3),
        (cx + half, cy), (cx + half // 3, cy + half // 3),
        (cx, cy + half), (cx - half // 3, cy + half // 3),
        (cx - half, cy), (cx - half // 3, cy - half // 3),
    ]
    draw.polygon(points, fill=(*color, 245))
    dot = max(2, size // 7)
    draw.ellipse(
        (cx + half + 4, cy - half, cx + half + 4 + dot, cy - half + dot),
        fill=(*color, 180),
    )
    draw.ellipse(
        (cx - half - dot - 5, cy + half // 2, cx - half - 5, cy + half // 2 + dot),
        fill=(*color, 150),
    )


def _draw_member_icon(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    size: int,
    color: tuple[int, int, int],
) -> None:
    x, y = origin
    head = max(5, size // 4)
    draw.ellipse((x, y, x + head * 2, y + head * 2), fill=(*color, 245))
    draw.rounded_rectangle(
        (x - head // 2, y + head * 2 + 2, x + head * 2 + head // 2, y + size),
        radius=head, fill=(*color, 210),
    )


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    return max(0, box[2] - box[0])


def _fit_subtitle(
    server: str,
    ordinal: str,
    *,
    style: WelcomeCardFontStyle,
    max_width: int,
) -> tuple[str, str, ImageFont.ImageFont]:
    prefix_base = f"to {server}"
    tail = f"You are the {ordinal} member!"
    minimum = style.subtitle_min_size
    for size in range(style.subtitle_start_size, minimum - 1, -1):
        font = _font(size, family=style.family, bold=True)
        probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
        width = _text_width(probe, prefix_base, font) + 68 + _text_width(probe, tail, font)
        if width <= max_width:
            return prefix_base, tail, font
    font = _font(minimum, family=style.family, bold=True)
    probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    allowed = max(30, max_width - 68 - _text_width(probe, tail, font))
    text = prefix_base
    while text and _text_width(probe, text + "...", font) > allowed:
        text = text[:-1]
    return text.rstrip() + ("..." if text != prefix_base else ""), tail, font


def _draw_subtitle(
    canvas: Image.Image,
    *,
    x: int,
    y: int,
    prefix: str,
    ordinal: str,
    font: ImageFont.ImageFont,
    text_color: tuple[int, int, int],
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    common = {"font": font, "stroke_width": 1, "stroke_fill": (0, 0, 0, 220)}
    draw.text((x, y), prefix, fill=(*text_color, 250), **common)
    cursor = x + int(draw.textlength(prefix, font=font)) + 20
    _draw_sparkle(draw, (cursor + 7, y + 15), 16, secondary)
    cursor += 30
    _draw_member_icon(draw, (cursor, y + 3), 20, primary)
    cursor += 28
    lead = "You are the "
    draw.text((cursor, y), lead, fill=(*text_color, 250), **common)
    cursor += int(draw.textlength(lead, font=font))
    draw.text((cursor, y), ordinal, fill=(*primary, 255), **common)
    cursor += int(draw.textlength(ordinal, font=font))
    draw.text((cursor, y), " member!", fill=(*text_color, 250), **common)


def _draw_theme_label(
    canvas: Image.Image,
    *,
    theme: WelcomeCardTheme,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(18, family="mono", bold=True)
    x, y = 420, 40
    _draw_sparkle(draw, (x + 8, y + 12), 14, primary)
    draw.line((x + 24, y + 12, x + 56, y + 12), fill=(*primary, 220), width=3)
    draw.text(
        (x + 66, y), theme.label.upper(), font=font, fill=(*primary, 245),
        stroke_width=1, stroke_fill=(0, 0, 0, 210),
    )
    label_width = int(draw.textlength(theme.label.upper(), font=font))
    tail = x + 78 + label_width
    draw.line((tail, y + 12, min(1130, tail + 48), y + 12), fill=(*secondary, 210), width=3)


def render_welcome_card(
    *,
    avatar_bytes: bytes,
    display_name: Any,
    server_name: Any,
    member_count: int,
    theme_key: Any = DEFAULT_THEME_KEY,
    custom_background_bytes: Optional[bytes] = None,
    font_style_key: Any = DEFAULT_FONT_STYLE_KEY,
    color_mode: Any = DEFAULT_COLOR_MODE,
    custom_primary: Any = None,
    custom_secondary: Any = None,
    profile_banner_bytes: Optional[bytes] = None,
    profile_accent: Any = None,
) -> bytes:
    theme = BUILTIN_THEMES[normalize_theme_key(theme_key)]
    style = FONT_STYLES[normalize_font_style_key(font_style_key)]
    palette = resolve_card_palette(
        theme=theme, color_mode=color_mode,
        custom_primary=custom_primary, custom_secondary=custom_secondary,
        profile_banner_bytes=profile_banner_bytes, profile_accent=profile_accent,
        avatar_bytes=avatar_bytes, card_background_bytes=custom_background_bytes,
    )
    primary, secondary = palette.primary, palette.secondary
    if custom_background_bytes:
        validate_custom_background(custom_background_bytes)
        with Image.open(BytesIO(custom_background_bytes)) as custom:
            canvas = legacy._cover(custom, (CARD_WIDTH, CARD_HEIGHT))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay, "RGBA").rounded_rectangle(
            (380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 158),
        )
        canvas.alpha_composite(overlay)
    else:
        canvas = legacy._base_background(theme)
    frame = ImageDraw.Draw(canvas, "RGBA")
    frame.rounded_rectangle((16, 16, 1184, 384), radius=34, outline=(*primary, 225), width=3)
    frame.rounded_rectangle((24, 24, 1176, 376), radius=30, outline=(*secondary, 145), width=2)
    # Preserve the exact avatar geometry approved in production.
    canvas.alpha_composite(
        legacy._avatar_layer(avatar_bytes, theme, primary=primary, secondary=secondary)
    )
    name = legacy._safe_text(display_name, fallback="New Member", max_chars=64)
    server = legacy._safe_text(server_name, fallback="Your Server", max_chars=72)
    ordinal = legacy._ordinal(member_count)
    x, max_width = 420, 710
    _draw_theme_label(canvas, theme=theme, primary=primary, secondary=secondary)
    _, welcome_mask = _fitted_mask(
        "WELCOME", style=style, start_size=style.welcome_size,
        min_size=max(36, style.welcome_size - 16), max_width=max_width,
        role="welcome", stroke_width=2,
    )
    _composite_mask_text(
        canvas, position=(x, 76), mask=welcome_mask, style=style,
        primary=theme.text, secondary=tuple(min(255, part + 85) for part in primary),
        role="welcome",
    )
    _, name_mask = _fitted_mask(
        name, style=style, start_size=style.name_start_size,
        min_size=style.name_min_size, max_width=max_width,
        role="name", stroke_width=style.name_stroke,
    )
    _composite_mask_text(
        canvas, position=(x, 137), mask=name_mask, style=style,
        primary=primary, secondary=secondary, role="name",
    )
    draw = ImageDraw.Draw(canvas, "RGBA")
    line_y = 272
    draw.line((x, line_y, 1135, line_y), fill=(*primary, 190), width=3)
    _draw_sparkle(draw, (782, line_y), 14, secondary)
    prefix, _tail, subtitle_font = _fit_subtitle(
        server, ordinal, style=style, max_width=max_width,
    )
    _draw_subtitle(
        canvas, x=x, y=295, prefix=prefix, ordinal=ordinal,
        font=subtitle_font, text_color=theme.text,
        primary=primary, secondary=secondary,
    )
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_font_catalog(
    *,
    display_name: Any,
    primary: tuple[int, int, int] = (90, 255, 45),
    secondary: tuple[int, int, int] = (174, 75, 255),
) -> bytes:
    name = legacy._safe_text(display_name, fallback="New Member", max_chars=30)
    width, row_height = 1200, 108
    height = 82 + row_height * len(FONT_STYLES)
    canvas = Image.new("RGBA", (width, height), (7, 9, 15, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.text(
        (38, 24), "WELCOME CARD FONT PICKER",
        font=_font(38, family="sans", bold=True), fill=(248, 250, 255, 255),
    )
    draw.text(
        (720, 34), "Choose by name in the Discord picker below.",
        font=_font(18, family="sans"), fill=(190, 198, 218, 255),
    )
    for index, style in enumerate(FONT_STYLES.values()):
        top = 76 + index * row_height
        draw.rounded_rectangle(
            (26, top, width - 26, top + row_height - 12), radius=20,
            fill=(15, 19, 30, 235), outline=(*primary, 90), width=2,
        )
        draw.text(
            (50, top + 18), style.label,
            font=_font(22, family="sans", bold=True), fill=(245, 247, 252, 255),
        )
        draw.text(
            (50, top + 52), style.description,
            font=_font(15, family="sans"), fill=(172, 182, 205, 255),
        )
        _, mask = _fitted_mask(
            name, style=style, start_size=62, min_size=28,
            max_width=655, role="name", stroke_width=style.name_stroke,
        )
        _composite_mask_text(
            canvas, position=(505, top + 18), mask=mask, style=style,
            primary=primary, secondary=secondary, role="name",
        )
    output = BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def render_color_catalog(*, swatches: bool = False) -> bytes:
    items = list(COLOR_SWATCHES.values()) if swatches else list(COLOR_PRESETS.values())
    columns, cell_w, cell_h, margin, header = 4, 280, 120, 28, 92
    rows = math.ceil(len(items) / columns)
    width = columns * cell_w + margin * 2
    height = header + rows * cell_h + margin
    canvas = Image.new("RGBA", (width, height), (7, 9, 15, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    title = "CUSTOM COLOR PICKER" if swatches else "READY-MADE COLOR PALETTES"
    draw.text(
        (margin, 25), title,
        font=_font(34, family="sans", bold=True), fill=(248, 250, 255, 255),
    )
    subtitle = "Pick a named swatch - no code needed." if swatches else "Pick a full two-color look in one tap."
    draw.text(
        (margin, 64), subtitle,
        font=_font(18, family="sans"), fill=(185, 194, 215, 255),
    )
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        x, y = margin + column * cell_w, header + row * cell_h
        if swatches:
            color = parse_hex_color(item.hex_value) or (255, 255, 255)
            fill = _gradient(
                (cell_w - 16, cell_h - 16),
                tuple(max(0, int(part * 0.45)) for part in color), color,
            )
            label, detail = item.label, "Pick by name - no code needed"
        else:
            first = parse_hex_color(item.primary) or (255, 255, 255)
            second = parse_hex_color(item.secondary) or (180, 180, 180)
            fill = _gradient((cell_w - 16, cell_h - 16), first, second)
            label, detail = item.label, item.description
        canvas.alpha_composite(fill, (x + 8, y + 8))
        draw.rounded_rectangle(
            (x + 8, y + 8, x + cell_w - 8, y + cell_h - 8), radius=18,
            outline=(255, 255, 255, 95), width=2,
        )
        draw.text(
            (x + 24, y + 25), label,
            font=_font(21, family="sans", bold=True), fill=(255, 255, 255, 255),
            stroke_width=1, stroke_fill=(0, 0, 0, 220),
        )
        draw.text(
            (x + 24, y + 61), detail,
            font=_font(14, family="sans"), fill=(255, 255, 255, 235),
            stroke_width=1, stroke_fill=(0, 0, 0, 210),
        )
    output = BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


__all__ = [
    "BUILTIN_THEMES", "CARD_HEIGHT", "CARD_RATIO", "CARD_WIDTH",
    "COLOR_MODES", "COLOR_PRESETS", "COLOR_SWATCHES",
    "DEFAULT_COLOR_MODE", "DEFAULT_FONT_STYLE_KEY", "DEFAULT_THEME_KEY",
    "FONT_STYLES", "MAX_CUSTOM_BACKGROUND_BYTES",
    "WelcomeCardFontStyle", "WelcomeCardPalette", "WelcomeCardTheme",
    "WelcomeColorPreset", "WelcomeColorSwatch",
    "color_mode_choices", "color_preset_choices", "font_style_choices",
    "normalize_color_mode", "normalize_font_style_key", "normalize_hex_color",
    "normalize_theme_key", "parse_hex_color", "render_color_catalog",
    "render_font_catalog", "render_welcome_card", "resolve_card_palette",
    "validate_custom_background",
]
