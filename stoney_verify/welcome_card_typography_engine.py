from __future__ import annotations

"""Canonical welcome-card typography and visual-style engine.

The engine reuses only the stable background, avatar, and palette primitives
from ``welcome_card_renderer``. Every font effect is rendered into its final
RGBA tile before fitting, so glow, outline, shear, and shadow pixels are all
included in the width/height decision. No command registration or runtime
monkey-patching lives in this module.
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
CUSTOM_FONT_STYLE_KEY = "custom"
NAME_SAFE_WIDTH = 710
NAME_SAFE_HEIGHT = 102
WELCOME_SAFE_HEIGHT = 62


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
    outline_width: int = 2
    glow_radius: int = 6
    glow_alpha: int = 100
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
    *_SANS_BOLD,
)
_MONO_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    *_SANS_REGULAR,
)
_SERIF_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    *_SANS_BOLD,
)
_SERIF_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    *_SANS_REGULAR,
)


FONT_STYLES: dict[str, WelcomeCardFontStyle] = {
    "neon": WelcomeCardFontStyle("neon", "Neon Display", "Bright condensed glow with a clean gaming look.", "sans", "neon", 56, 96, 40, 31, 20, 0, 2, 10, 150),
    "tech": WelcomeCardFontStyle("tech", "Tech Circuit", "Tracked mono lettering with digital scan cuts.", "mono", "tech", 50, 82, 36, 28, 19, 3, 2, 5, 105, 0.0, True),
    "bold": WelcomeCardFontStyle("bold", "Heavy Impact", "Strong esports type with a controlled offset shadow.", "sans", "impact", 54, 86, 36, 30, 20, 0, 2, 3, 65),
    "clean": WelcomeCardFontStyle("clean", "Clean Modern", "Polished minimal type with restrained effects.", "sans", "clean", 48, 86, 38, 29, 20, 0, 1, 1, 35),
    "chrome": WelcomeCardFontStyle("chrome", "Chrome Luxe", "Metallic highlights with premium depth.", "serif", "chrome", 52, 88, 38, 29, 20, 0, 2, 4, 75),
    "outline": WelcomeCardFontStyle("outline", "Hollow Outline", "Open counters with a crisp two-color contour.", "sans", "outline", 52, 86, 38, 29, 20, 1, 2, 4, 80),
    "arcade": WelcomeCardFontStyle("arcade", "Arcade Pixel", "Chunky pixel treatment for retro gaming servers.", "mono", "arcade", 50, 82, 34, 27, 18, 2, 1, 3, 70, 0.0, True),
    "street": WelcomeCardFontStyle("street", "Street Slant", "Controlled slant with a compact offset shadow.", "sans", "street", 52, 86, 36, 29, 20, 0, 2, 3, 65, 0.10),
    "future": WelcomeCardFontStyle("future", "Future Wide", "Tracked capitals with angular technology cuts.", "sans", "future", 50, 80, 34, 27, 18, 4, 1, 4, 85, 0.0, True),
    "soft": WelcomeCardFontStyle("soft", "Soft Luxe", "Smooth serif styling with a softer profile glow.", "serif", "soft", 50, 86, 38, 29, 20, 0, 1, 6, 65, 0.04),
    "stencil": WelcomeCardFontStyle("stencil", "Combat Stencil", "Bold tactical capitals with deliberate cut lines.", "sans", "stencil", 50, 82, 34, 27, 18, 2, 1, 3, 65, 0.0, True),
    "varsity": WelcomeCardFontStyle("varsity", "Varsity Badge", "Athletic contour lettering for teams and clans.", "serif", "varsity", 50, 84, 36, 28, 19, 1, 2, 3, 65, 0.0, True),
    "blackletter": WelcomeCardFontStyle("blackletter", "Midnight Gothic", "Dark premium serif type with metallic depth.", "serif", "gothic", 51, 86, 36, 28, 19, 0, 2, 4, 70),
    "prism": WelcomeCardFontStyle("prism", "Prism Glow", "Clean lettering with a brighter two-tone aura.", "sans", "prism", 52, 88, 38, 29, 20, 1, 2, 9, 145),
    "terminal": WelcomeCardFontStyle("terminal", "Terminal Code", "Compact mono capitals with fine scan lines.", "mono", "terminal", 48, 78, 32, 26, 18, 2, 1, 2, 55, 0.0, True),
    "retro": WelcomeCardFontStyle("retro", "Retro Wave", "Crisp pixel edges with a restrained arcade glow.", "sans", "retro", 50, 84, 36, 28, 19, 1, 1, 4, 75),
}

CUSTOM_FONT_STYLE = WelcomeCardFontStyle(
    CUSTOM_FONT_STYLE_KEY,
    "Uploaded Font",
    "The server's validated custom font.",
    "sans",
    "custom",
    54,
    90,
    34,
    29,
    19,
    0,
    2,
    5,
    85,
)


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
        "default": "neon",
        "mono": "tech",
        "heavy": "bold",
        "impact": "bold",
        "modern": "clean",
        "metal": "chrome",
        "hollow": "outline",
        "pixel": "arcade",
        "slant": "street",
        "futuristic": "future",
        "luxury": "soft",
        "gothic": "blackletter",
        "uploaded": CUSTOM_FONT_STYLE_KEY,
    }
    key = aliases.get(key, key)
    if key == CUSTOM_FONT_STYLE_KEY:
        return key
    return key if key in FONT_STYLES else DEFAULT_FONT_STYLE_KEY


def font_style_choices() -> list[tuple[str, str]]:
    return [(style.key, style.label) for style in FONT_STYLES.values()]


def color_mode_choices() -> list[tuple[str, str]]:
    return list(COLOR_MODES.items())


def _family_candidates(family: str, *, bold: bool) -> tuple[str, ...]:
    if family == "mono":
        return _MONO_BOLD if bold else _MONO_REGULAR
    if family == "serif":
        return _SERIF_BOLD if bold else _SERIF_REGULAR
    return _SANS_BOLD if bold else _SANS_REGULAR


def _font(
    size: int,
    *,
    style: WelcomeCardFontStyle,
    bold: bool = True,
    custom_font_bytes: Optional[bytes] = None,
) -> ImageFont.ImageFont:
    if style.key == CUSTOM_FONT_STYLE_KEY and custom_font_bytes:
        try:
            return ImageFont.truetype(BytesIO(custom_font_bytes), max(8, int(size)))
        except Exception:
            pass
    for path in _family_candidates(style.family, bold=bold):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, max(8, int(size)))
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


def _crop_alpha(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    box = alpha.getbbox()
    return image.crop(box) if box else Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def _crop_mask(mask: Image.Image) -> Image.Image:
    box = mask.getbbox()
    return mask.crop(box) if box else Image.new("L", (1, 1), 0)


def _tracked_mask(text: str, *, font: ImageFont.ImageFont, tracking: int) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    padding = 8
    metrics: list[tuple[str, tuple[int, int, int, int], int]] = []
    max_height = 1
    for character in text:
        box = probe.textbbox((0, 0), character, font=font)
        width = max(1, box[2] - box[0])
        max_height = max(max_height, box[3] - box[1])
        metrics.append((character, box, width))
    total_width = sum(item[2] for item in metrics) + max(0, len(metrics) - 1) * max(0, tracking)
    mask = Image.new("L", (max(1, total_width) + padding * 2, max_height + padding * 2), 0)
    draw = ImageDraw.Draw(mask)
    cursor = padding
    for character, box, width in metrics:
        draw.text((cursor - box[0], padding - box[1]), character, font=font, fill=255)
        cursor += width + max(0, tracking)
    return _crop_mask(mask)


def _transform_mask(mask: Image.Image, style: WelcomeCardFontStyle) -> Image.Image:
    transformed = _crop_mask(mask)
    if style.shear:
        extra = int(abs(style.shear) * transformed.height) + 8
        transformed = transformed.transform(
            (transformed.width + extra, transformed.height),
            Image.Transform.AFFINE,
            (1, -style.shear, extra if style.shear > 0 else 0, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
        transformed = _crop_mask(transformed)
    if style.effect in {"arcade", "retro"}:
        divisor = 3 if style.effect == "arcade" else 2
        tiny = transformed.resize(
            (max(1, transformed.width // divisor), max(1, transformed.height // divisor)),
            Image.Resampling.BILINEAR,
        )
        transformed = tiny.resize(transformed.size, Image.Resampling.NEAREST)
    if style.effect in {"tech", "future", "stencil", "terminal"}:
        cut = Image.new("L", transformed.size, 255)
        draw = ImageDraw.Draw(cut)
        spacing = {"tech": 12, "future": 17, "stencil": 22, "terminal": 9}[style.effect]
        thickness = 2 if style.effect == "stencil" else 1
        for y in range(6, transformed.height, spacing):
            draw.rectangle((0, y, transformed.width, y + thickness), fill=0)
        transformed = ImageChops.multiply(transformed, cut)
    return _crop_mask(transformed)


def _dilate(mask: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        return mask.copy()
    size = max(3, width * 2 + 1)
    if size % 2 == 0:
        size += 1
    return mask.filter(ImageFilter.MaxFilter(size))


def _erode(mask: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        return mask.copy()
    size = max(3, width * 2 + 1)
    if size % 2 == 0:
        size += 1
    return mask.filter(ImageFilter.MinFilter(size))


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


def _chrome_gradient(
    size: tuple[int, int],
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = image.load()
    stops = (
        (0.0, (255, 255, 255)),
        (0.24, secondary),
        (0.48, (248, 250, 255)),
        (0.62, primary),
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


def _styled_tile(
    text: str,
    *,
    style: WelcomeCardFontStyle,
    size: int,
    role: str,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    custom_font_bytes: Optional[bytes] = None,
) -> Image.Image:
    rendered_text = text.upper() if style.uppercase_name and role == "name" else text
    font = _font(size, style=style, bold=True, custom_font_bytes=custom_font_bytes)
    mask = _tracked_mask(
        rendered_text,
        font=font,
        tracking=style.tracking if role in {"name", "welcome"} else 0,
    )
    mask = _transform_mask(mask, style)

    shadow_x = 4 if style.effect in {"impact", "street", "arcade"} else 0
    shadow_y = 4 if style.effect in {"impact", "street", "arcade"} else 0
    glow_margin = max(0, style.glow_radius * 3)
    border_margin = max(4, style.outline_width * 3)
    padding = max(10, glow_margin, border_margin) + max(shadow_x, shadow_y)
    tile_size = (mask.width + padding * 2, mask.height + padding * 2)
    centered = Image.new("L", tile_size, 0)
    centered.paste(mask, (padding, padding))
    outline = _dilate(centered, style.outline_width)
    tile = Image.new("RGBA", tile_size, (0, 0, 0, 0))

    if shadow_x or shadow_y:
        shadow_alpha = outline.point(lambda value: int(value * 0.72))
        shadow = Image.new("RGBA", tile_size, (0, 0, 0, 0))
        shifted = Image.new("L", tile_size, 0)
        shifted.paste(shadow_alpha, (shadow_x, shadow_y))
        shadow.putalpha(shifted)
        tile.alpha_composite(shadow)

    if style.glow_radius > 0 and style.glow_alpha > 0:
        glow_mask = outline.filter(ImageFilter.GaussianBlur(style.glow_radius))
        glow_alpha = glow_mask.point(
            lambda value: int(value * min(255, style.glow_alpha) / 255)
        )
        glow = Image.new("RGBA", tile_size, (*primary, 0))
        glow.putalpha(glow_alpha)
        tile.alpha_composite(glow)

    if style.effect in {"outline", "varsity"}:
        inner = _erode(centered, 1)
        ring = ImageChops.subtract(outline, inner)
        fill = _gradient(tile_size, primary, secondary)
        fill.putalpha(ring)
        tile.alpha_composite(fill)
        faint = Image.new("RGBA", tile_size, (5, 8, 15, 0))
        faint.putalpha(inner.point(lambda value: int(value * 0.18)))
        tile.alpha_composite(faint)
        return _crop_alpha(tile)

    dark = Image.new("RGBA", tile_size, (2, 4, 9, 0))
    dark.putalpha(outline.point(lambda value: int(value * 0.92)))
    tile.alpha_composite(dark)

    if style.effect in {"chrome", "gothic"}:
        fill = _chrome_gradient(tile_size, primary, secondary)
    elif role == "welcome":
        fill = _gradient(
            tile_size,
            (255, 255, 255),
            tuple(min(255, part + 80) for part in primary),
            vertical=True,
        )
    elif style.effect == "clean":
        fill = _gradient(
            tile_size,
            tuple(min(255, part + 40) for part in primary),
            secondary,
        )
    else:
        fill = _gradient(tile_size, primary, secondary)
    fill.putalpha(centered)
    tile.alpha_composite(fill)
    return _crop_alpha(tile)


def _fits(tile: Image.Image, *, max_width: int, max_height: int) -> bool:
    return tile.width <= max_width and tile.height <= max_height


def _fitted_tile(
    text: str,
    *,
    style: WelcomeCardFontStyle,
    start_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    role: str,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    custom_font_bytes: Optional[bytes] = None,
) -> tuple[str, Image.Image]:
    candidate = text.upper() if style.uppercase_name and role == "name" else text
    minimum = max(8, int(min_size))
    for size in range(max(int(start_size), minimum), minimum - 1, -2):
        tile = _styled_tile(
            candidate,
            style=style,
            size=size,
            role=role,
            primary=primary,
            secondary=secondary,
            custom_font_bytes=custom_font_bytes,
        )
        if _fits(tile, max_width=max_width, max_height=max_height):
            return candidate, tile

    suffix = "..."
    base_text = candidate
    while base_text:
        attempt = base_text.rstrip() + suffix
        tile = _styled_tile(
            attempt,
            style=style,
            size=minimum,
            role=role,
            primary=primary,
            secondary=secondary,
            custom_font_bytes=custom_font_bytes,
        )
        if _fits(tile, max_width=max_width, max_height=max_height):
            return attempt, tile
        base_text = base_text[:-1]
    return "", Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def _place_tile(
    canvas: Image.Image,
    tile: Image.Image,
    *,
    left: int,
    top: int,
    box_width: int,
    box_height: int,
    center_x: bool = False,
) -> None:
    x = left + ((box_width - tile.width) // 2 if center_x else 0)
    y = top + max(0, (box_height - tile.height) // 2)
    canvas.alpha_composite(tile, (x, y))


def _render_style(style_key: Any, custom_font_bytes: Optional[bytes]) -> WelcomeCardFontStyle:
    key = normalize_font_style_key(style_key)
    if key == CUSTOM_FONT_STYLE_KEY and custom_font_bytes:
        return CUSTOM_FONT_STYLE
    return FONT_STYLES.get(key, FONT_STYLES[DEFAULT_FONT_STYLE_KEY])


def _draw_sparkle(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    size: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    half = max(3, size // 2)
    draw.polygon(
        [
            (cx, cy - half),
            (cx + half // 3, cy - half // 3),
            (cx + half, cy),
            (cx + half // 3, cy + half // 3),
            (cx, cy + half),
            (cx - half // 3, cy + half // 3),
            (cx - half, cy),
            (cx - half // 3, cy - half // 3),
        ],
        fill=(*color, 245),
    )


def _draw_member_icon(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    size: int,
    color: tuple[int, int, int],
) -> None:
    x, y = origin
    head = max(4, size // 4)
    draw.ellipse((x, y, x + head * 2, y + head * 2), fill=(*color, 245))
    draw.rounded_rectangle(
        (x - head // 2, y + head * 2 + 2, x + head * 2 + head // 2, y + size),
        radius=head,
        fill=(*color, 210),
    )


def _draw_theme_label(
    canvas: Image.Image,
    *,
    theme: WelcomeCardTheme,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    style = FONT_STYLES["terminal"]
    font = _font(18, style=style, bold=True)
    x, y = 420, 40
    _draw_sparkle(draw, (x + 8, y + 12), 14, primary)
    draw.line((x + 24, y + 12, x + 56, y + 12), fill=(*primary, 220), width=3)
    label = theme.label.upper()
    draw.text((x + 66, y), label, font=font, fill=(*primary, 245), stroke_width=1, stroke_fill=(0, 0, 0, 210))
    label_width = int(draw.textlength(label, font=font))
    tail = x + 78 + label_width
    draw.line((tail, y + 12, min(1130, tail + 48), y + 12), fill=(*secondary, 210), width=3)


def _fit_subtitle(
    server: str,
    ordinal: str,
    *,
    style: WelcomeCardFontStyle,
    max_width: int,
    custom_font_bytes: Optional[bytes],
) -> tuple[str, ImageFont.ImageFont]:
    prefix_base = f"to {server}"
    tail = f"You are the {ordinal} member!"
    for size in range(style.subtitle_start_size, style.subtitle_min_size - 1, -1):
        font = _font(size, style=style, bold=True, custom_font_bytes=custom_font_bytes)
        draw = ImageDraw.Draw(Image.new("L", (8, 8), 0))
        width = int(draw.textlength(prefix_base, font=font)) + 64 + int(draw.textlength(tail, font=font))
        if width <= max_width:
            return prefix_base, font
    font = _font(style.subtitle_min_size, style=style, bold=True, custom_font_bytes=custom_font_bytes)
    draw = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    allowed = max(24, max_width - 64 - int(draw.textlength(tail, font=font)))
    text = prefix_base
    while text and int(draw.textlength(text + "...", font=font)) > allowed:
        text = text[:-1]
    return text.rstrip() + ("..." if text != prefix_base else ""), font


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
    cursor = x + int(draw.textlength(prefix, font=font)) + 16
    _draw_sparkle(draw, (cursor + 7, y + 15), 14, secondary)
    cursor += 27
    _draw_member_icon(draw, (cursor, y + 3), 19, primary)
    cursor += 26
    lead = "You are the "
    draw.text((cursor, y), lead, fill=(*text_color, 250), **common)
    cursor += int(draw.textlength(lead, font=font))
    draw.text((cursor, y), ordinal, fill=(*primary, 255), **common)
    cursor += int(draw.textlength(ordinal, font=font))
    draw.text((cursor, y), " member!", fill=(*text_color, 250), **common)


def render_welcome_card(
    *,
    avatar_bytes: bytes,
    display_name: Any,
    server_name: Any,
    member_count: int,
    theme_key: Any = DEFAULT_THEME_KEY,
    custom_background_bytes: Optional[bytes] = None,
    font_style_key: Any = DEFAULT_FONT_STYLE_KEY,
    custom_font_bytes: Optional[bytes] = None,
    color_mode: Any = DEFAULT_COLOR_MODE,
    custom_primary: Any = None,
    custom_secondary: Any = None,
    profile_banner_bytes: Optional[bytes] = None,
    profile_accent: Any = None,
) -> bytes:
    theme = BUILTIN_THEMES[normalize_theme_key(theme_key)]
    style = _render_style(font_style_key, custom_font_bytes)
    palette = resolve_card_palette(
        theme=theme,
        color_mode=color_mode,
        custom_primary=custom_primary,
        custom_secondary=custom_secondary,
        profile_banner_bytes=profile_banner_bytes,
        profile_accent=profile_accent,
        avatar_bytes=avatar_bytes,
        card_background_bytes=custom_background_bytes,
    )
    primary, secondary = palette.primary, palette.secondary

    if custom_background_bytes:
        validate_custom_background(custom_background_bytes)
        with Image.open(BytesIO(custom_background_bytes)) as custom:
            canvas = legacy._cover(custom, (CARD_WIDTH, CARD_HEIGHT))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay, "RGBA").rounded_rectangle(
            (380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 158)
        )
        canvas.alpha_composite(overlay)
    else:
        canvas = legacy._base_background(theme)

    frame = ImageDraw.Draw(canvas, "RGBA")
    frame.rounded_rectangle((16, 16, 1184, 384), radius=34, outline=(*primary, 225), width=3)
    frame.rounded_rectangle((24, 24, 1176, 376), radius=30, outline=(*secondary, 145), width=2)
    canvas.alpha_composite(
        legacy._avatar_layer(avatar_bytes, theme, primary=primary, secondary=secondary)
    )

    name = legacy._safe_text(display_name, fallback="New Member", max_chars=64)
    server = legacy._safe_text(server_name, fallback="Your Server", max_chars=72)
    ordinal = legacy._ordinal(member_count)
    x = 420
    _draw_theme_label(canvas, theme=theme, primary=primary, secondary=secondary)

    _, welcome_tile = _fitted_tile(
        "WELCOME",
        style=style,
        start_size=style.welcome_size,
        min_size=max(32, style.welcome_size - 18),
        max_width=NAME_SAFE_WIDTH,
        max_height=WELCOME_SAFE_HEIGHT,
        role="welcome",
        primary=theme.text,
        secondary=tuple(min(255, part + 80) for part in primary),
        custom_font_bytes=custom_font_bytes,
    )
    _place_tile(canvas, welcome_tile, left=x, top=73, box_width=NAME_SAFE_WIDTH, box_height=WELCOME_SAFE_HEIGHT)

    _, name_tile = _fitted_tile(
        name,
        style=style,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        max_width=NAME_SAFE_WIDTH,
        max_height=NAME_SAFE_HEIGHT,
        role="name",
        primary=primary,
        secondary=secondary,
        custom_font_bytes=custom_font_bytes,
    )
    _place_tile(canvas, name_tile, left=x, top=137, box_width=NAME_SAFE_WIDTH, box_height=NAME_SAFE_HEIGHT)

    draw = ImageDraw.Draw(canvas, "RGBA")
    line_y = 270
    draw.line((x, line_y, 1135, line_y), fill=(*primary, 190), width=3)
    _draw_sparkle(draw, (782, line_y), 14, secondary)
    prefix, subtitle_font = _fit_subtitle(
        server,
        ordinal,
        style=style,
        max_width=NAME_SAFE_WIDTH,
        custom_font_bytes=custom_font_bytes,
    )
    _draw_subtitle(
        canvas,
        x=x,
        y=294,
        prefix=prefix,
        ordinal=ordinal,
        font=subtitle_font,
        text_color=theme.text,
        primary=primary,
        secondary=secondary,
    )

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_font_catalog(
    *,
    display_name: Any,
    primary: tuple[int, int, int] = (90, 255, 45),
    secondary: tuple[int, int, int] = (174, 75, 255),
    custom_font_bytes: Optional[bytes] = None,
    custom_font_name: str = "Uploaded Font",
) -> bytes:
    name = legacy._safe_text(display_name, fallback="New Member", max_chars=30)
    entries: list[tuple[WelcomeCardFontStyle, Optional[bytes], str]] = [
        (style, None, style.label) for style in FONT_STYLES.values()
    ]
    if custom_font_bytes:
        entries.append((CUSTOM_FONT_STYLE, custom_font_bytes, custom_font_name or "Uploaded Font"))

    width, row_height = 1200, 108
    height = 82 + row_height * len(entries)
    canvas = Image.new("RGBA", (width, height), (7, 9, 15, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    header_style = FONT_STYLES["clean"]
    draw.text((38, 24), "WELCOME CARD FONT PICKER", font=_font(38, style=header_style), fill=(248, 250, 255, 255))
    draw.text((720, 34), "Final effects are fitted inside every preview box.", font=_font(18, style=header_style, bold=False), fill=(190, 198, 218, 255))

    for index, (style, font_bytes, label) in enumerate(entries):
        top = 76 + index * row_height
        draw.rounded_rectangle(
            (26, top, width - 26, top + row_height - 12),
            radius=20,
            fill=(15, 19, 30, 235),
            outline=(*primary, 90),
            width=2,
        )
        draw.text((50, top + 18), label[:34], font=_font(22, style=header_style), fill=(245, 247, 252, 255))
        draw.text((50, top + 52), style.description, font=_font(15, style=header_style, bold=False), fill=(172, 182, 205, 255))
        _, tile = _fitted_tile(
            name,
            style=style,
            start_size=62,
            min_size=24,
            max_width=680,
            max_height=68,
            role="name",
            primary=primary,
            secondary=secondary,
            custom_font_bytes=font_bytes,
        )
        _place_tile(
            canvas,
            tile,
            left=475,
            top=top + 9,
            box_width=680,
            box_height=70,
            center_x=True,
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
    header_style = FONT_STYLES["clean"]
    title = "CUSTOM COLOR PICKER" if swatches else "READY-MADE COLOR PALETTES"
    subtitle = "Pick a named swatch - no code needed." if swatches else "Pick a full two-color look in one tap."
    draw.text((margin, 25), title, font=_font(34, style=header_style), fill=(248, 250, 255, 255))
    draw.text((margin, 64), subtitle, font=_font(18, style=header_style, bold=False), fill=(185, 194, 215, 255))
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        x, y = margin + column * cell_w, header + row * cell_h
        if swatches:
            color = parse_hex_color(item.hex_value) or (255, 255, 255)
            fill = _gradient((cell_w - 16, cell_h - 16), tuple(max(0, int(part * 0.45)) for part in color), color)
            label, detail = item.label, "Pick by name - no code needed"
        else:
            first = parse_hex_color(item.primary) or (255, 255, 255)
            second = parse_hex_color(item.secondary) or (180, 180, 180)
            fill = _gradient((cell_w - 16, cell_h - 16), first, second)
            label, detail = item.label, item.description
        canvas.alpha_composite(fill, (x + 8, y + 8))
        draw.rounded_rectangle((x + 8, y + 8, x + cell_w - 8, y + cell_h - 8), radius=18, outline=(255, 255, 255, 95), width=2)
        draw.text((x + 24, y + 25), label, font=_font(21, style=header_style), fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 220))
        draw.text((x + 24, y + 61), detail, font=_font(14, style=header_style, bold=False), fill=(255, 255, 255, 235), stroke_width=1, stroke_fill=(0, 0, 0, 210))
    output = BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


__all__ = [
    "BUILTIN_THEMES",
    "CARD_HEIGHT",
    "CARD_RATIO",
    "CARD_WIDTH",
    "COLOR_MODES",
    "COLOR_PRESETS",
    "COLOR_SWATCHES",
    "CUSTOM_FONT_STYLE",
    "CUSTOM_FONT_STYLE_KEY",
    "DEFAULT_COLOR_MODE",
    "DEFAULT_FONT_STYLE_KEY",
    "DEFAULT_THEME_KEY",
    "FONT_STYLES",
    "MAX_CUSTOM_BACKGROUND_BYTES",
    "NAME_SAFE_HEIGHT",
    "NAME_SAFE_WIDTH",
    "WelcomeCardFontStyle",
    "WelcomeCardPalette",
    "WelcomeCardTheme",
    "color_mode_choices",
    "font_style_choices",
    "normalize_color_mode",
    "normalize_font_style_key",
    "normalize_hex_color",
    "normalize_theme_key",
    "parse_hex_color",
    "render_color_catalog",
    "render_font_catalog",
    "render_welcome_card",
    "resolve_card_palette",
    "validate_custom_background",
]
