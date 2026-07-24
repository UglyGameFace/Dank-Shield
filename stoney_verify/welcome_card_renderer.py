from __future__ import annotations

"""Production-safe, profile-aware welcome card rendering.

The renderer owns the canvas, avatar crop, palette detection, typography, and
built-in themes. Backgrounds never contain baked-in usernames or member counts.
A guild may use a built-in theme or provide a validated custom 3:1 image.
"""

import colorsys
import math
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

CARD_WIDTH = 1200
CARD_HEIGHT = 400
CARD_RATIO = CARD_WIDTH / CARD_HEIGHT
MAX_CUSTOM_BACKGROUND_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class WelcomeCardTheme:
    key: str
    label: str
    background: tuple[int, int, int]
    panel: tuple[int, int, int]
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]
    text: tuple[int, int, int]
    muted: tuple[int, int, int]
    motif: str = "generic"


@dataclass(frozen=True)
class WelcomeCardFontStyle:
    key: str
    label: str
    display_candidates: tuple[str, ...]
    body_candidates: tuple[str, ...]
    welcome_size: int
    name_start_size: int
    name_min_size: int
    subtitle_start_size: int
    subtitle_min_size: int
    name_stroke: int
    glow_radius: int
    glow_alpha: int


@dataclass(frozen=True)
class WelcomeCardPalette:
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]
    source: str


BUILTIN_THEMES: dict[str, WelcomeCardTheme] = {
    "420_lobby": WelcomeCardTheme(
        key="420_lobby",
        label="420 Lobby Neon",
        background=(7, 8, 12),
        panel=(12, 14, 20),
        primary=(90, 255, 45),
        secondary=(174, 75, 255),
        text=(248, 249, 252),
        muted=(205, 210, 220),
        motif="420",
    ),
    "cyber_neon": WelcomeCardTheme(
        key="cyber_neon",
        label="Cyber Neon",
        background=(4, 8, 18),
        panel=(9, 14, 28),
        primary=(34, 220, 255),
        secondary=(188, 66, 255),
        text=(250, 252, 255),
        muted=(195, 205, 225),
        motif="cyber",
    ),
    "premium_gold": WelcomeCardTheme(
        key="premium_gold",
        label="Premium Gold",
        background=(6, 13, 17),
        panel=(10, 22, 26),
        primary=(43, 220, 210),
        secondary=(242, 188, 73),
        text=(252, 252, 248),
        muted=(210, 218, 216),
        motif="premium",
    ),
    "community_glow": WelcomeCardTheme(
        key="community_glow",
        label="Community Glow",
        background=(10, 8, 18),
        panel=(18, 13, 30),
        primary=(255, 139, 39),
        secondary=(92, 157, 255),
        text=(255, 252, 249),
        muted=(220, 211, 225),
        motif="community",
    ),
    "esports": WelcomeCardTheme(
        key="esports",
        label="Esports",
        background=(5, 7, 13),
        panel=(12, 14, 22),
        primary=(255, 75, 33),
        secondary=(34, 120, 255),
        text=(250, 251, 255),
        muted=(198, 206, 222),
        motif="esports",
    ),
    "minimal_glass": WelcomeCardTheme(
        key="minimal_glass",
        label="Minimal Glass",
        background=(14, 17, 24),
        panel=(28, 33, 44),
        primary=(114, 225, 255),
        secondary=(170, 145, 255),
        text=(252, 253, 255),
        muted=(205, 211, 224),
        motif="minimal",
    ),
}

DEFAULT_THEME_KEY = "cyber_neon"
DEFAULT_FONT_STYLE_KEY = "neon"
DEFAULT_COLOR_MODE = "auto"

_SANS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
)
_SANS_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
)
_CONDENSED_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansCondensed-Bold.ttf",
    *_SANS_BOLD,
)
_CONDENSED_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansCondensed.ttf",
    *_SANS_REGULAR,
)
_MONO_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
    *_CONDENSED_BOLD,
)
_MONO_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    *_CONDENSED_REGULAR,
)

FONT_STYLES: dict[str, WelcomeCardFontStyle] = {
    "neon": WelcomeCardFontStyle(
        key="neon",
        label="Neon Display",
        display_candidates=_CONDENSED_BOLD,
        body_candidates=_CONDENSED_REGULAR,
        welcome_size=56,
        name_start_size=98,
        name_min_size=46,
        subtitle_start_size=33,
        subtitle_min_size=22,
        name_stroke=2,
        glow_radius=13,
        glow_alpha=155,
    ),
    "tech": WelcomeCardFontStyle(
        key="tech",
        label="Tech Mono",
        display_candidates=_MONO_BOLD,
        body_candidates=_MONO_REGULAR,
        welcome_size=52,
        name_start_size=86,
        name_min_size=42,
        subtitle_start_size=29,
        subtitle_min_size=20,
        name_stroke=2,
        glow_radius=9,
        glow_alpha=130,
    ),
    "bold": WelcomeCardFontStyle(
        key="bold",
        label="Heavy Bold",
        display_candidates=_SANS_BOLD,
        body_candidates=_SANS_REGULAR,
        welcome_size=58,
        name_start_size=96,
        name_min_size=44,
        subtitle_start_size=33,
        subtitle_min_size=22,
        name_stroke=3,
        glow_radius=8,
        glow_alpha=115,
    ),
    "clean": WelcomeCardFontStyle(
        key="clean",
        label="Clean Modern",
        display_candidates=_SANS_BOLD,
        body_candidates=_SANS_REGULAR,
        welcome_size=50,
        name_start_size=88,
        name_min_size=42,
        subtitle_start_size=31,
        subtitle_min_size=21,
        name_stroke=1,
        glow_radius=4,
        glow_alpha=80,
    ),
}

COLOR_MODES: dict[str, str] = {
    "auto": "Smart Auto",
    "profile": "Member Profile",
    "card": "Card Background",
    "theme": "Selected Theme",
    "custom": "Custom Colors",
}

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def normalize_theme_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return key if key in BUILTIN_THEMES else DEFAULT_THEME_KEY


def normalize_font_style_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"modern": "clean", "heavy": "bold", "mono": "tech", "default": "neon"}
    key = aliases.get(key, key)
    return key if key in FONT_STYLES else DEFAULT_FONT_STYLE_KEY


def normalize_color_mode(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "smart": "auto",
        "automatic": "auto",
        "profile_auto": "profile",
        "background": "card",
        "banner": "card",
        "preset": "theme",
    }
    key = aliases.get(key, key)
    return key if key in COLOR_MODES else DEFAULT_COLOR_MODE


def normalize_hex_color(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        text = "".join(ch * 2 for ch in text)
    if not _HEX_RE.fullmatch(text):
        raise ValueError("Use a six-digit hex color such as #22DCFF.")
    return f"#{text.upper()}"


def parse_hex_color(value: Any) -> Optional[tuple[int, int, int]]:
    if value is None or str(value).strip() == "":
        return None
    normalized = normalize_hex_color(value)
    return tuple(int(normalized[index : index + 2], 16) for index in (1, 3, 5))


def theme_choices() -> list[tuple[str, str]]:
    return [(theme.key, theme.label) for theme in BUILTIN_THEMES.values()]


def font_style_choices() -> list[tuple[str, str]]:
    return [(style.key, style.label) for style in FONT_STYLES.values()]


def color_mode_choices() -> list[tuple[str, str]]:
    return list(COLOR_MODES.items())


def _font_candidates(style_key: Any, *, bold: bool, role: str) -> tuple[str, ...]:
    style = FONT_STYLES[normalize_font_style_key(style_key)]
    if role in {"display", "name", "welcome", "label"}:
        return style.display_candidates
    return style.body_candidates if not bold else style.display_candidates


def _font(
    size: int,
    *,
    bold: bool = False,
    style_key: Any = DEFAULT_FONT_STYLE_KEY,
    role: str = "body",
) -> ImageFont.ImageFont:
    for path in _font_candidates(style_key, bold=bold, role=role):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, max(8, int(size)))
        except Exception:
            continue

    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.ImageFont,
    stroke_width: int = 0,
) -> int:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=max(0, int(stroke_width)))
    return max(0, box[2] - box[0])


def _ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.ImageFont,
    max_width: int,
    stroke_width: int = 0,
) -> str:
    if _text_width(draw, text, font=font, stroke_width=stroke_width) <= max_width:
        return text
    ellipsis = "…"
    if _text_width(draw, ellipsis, font=font, stroke_width=stroke_width) > max_width:
        return ""
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + ellipsis
        if _text_width(draw, candidate, font=font, stroke_width=stroke_width) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ellipsis


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    start_size: int,
    min_size: int,
    bold: bool = True,
    stroke_width: int = 0,
    preserve_suffix: str = "",
    style_key: Any = DEFAULT_FONT_STYLE_KEY,
    role: str = "body",
) -> tuple[str, ImageFont.ImageFont]:
    start = max(int(start_size), int(min_size))
    minimum = max(8, int(min_size))
    sizes = list(range(start, minimum - 1, -2))
    if not sizes or sizes[-1] != minimum:
        sizes.append(minimum)
    for size in sizes:
        candidate_font = _font(size, bold=bold, style_key=style_key, role=role)
        if _text_width(draw, text, font=candidate_font, stroke_width=stroke_width) <= max_width:
            return text, candidate_font
    fitted_font = _font(minimum, bold=bold, style_key=style_key, role=role)
    suffix = preserve_suffix if preserve_suffix and text.endswith(preserve_suffix) else ""
    if suffix:
        prefix = text[: -len(suffix)]
        suffix_width = _text_width(draw, suffix, font=fitted_font, stroke_width=stroke_width)
        prefix_width = max(0, max_width - suffix_width)
        fitted_prefix = _ellipsize_text(
            draw,
            prefix,
            font=fitted_font,
            max_width=prefix_width,
            stroke_width=stroke_width,
        )
        fitted_text = fitted_prefix + suffix
        if _text_width(draw, fitted_text, font=fitted_font, stroke_width=stroke_width) <= max_width:
            return fitted_text, fitted_font
    return (
        _ellipsize_text(
            draw,
            text,
            font=fitted_font,
            max_width=max_width,
            stroke_width=stroke_width,
        ),
        fitted_font,
    )


def _safe_text(value: Any, *, fallback: str, max_chars: int) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split()).strip()
    if not text:
        text = fallback
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


def _ordinal(number: int) -> str:
    value = max(0, int(number or 0))
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGBA"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def validate_custom_background(data: bytes) -> None:
    if not data:
        raise ValueError("Custom background is empty.")
    if len(data) > MAX_CUSTOM_BACKGROUND_BYTES:
        raise ValueError("Custom background exceeds the 8 MB limit.")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if width < 600 or height < 200:
                raise ValueError("Custom background must be at least 600×200 pixels.")
            ratio = width / max(1, height)
            if ratio < 2.4 or ratio > 3.6:
                raise ValueError("Custom background should be close to a 3:1 banner.")
            image.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Custom background must be a valid PNG, JPG, or WEBP image.") from exc


def _gradient(size: tuple[int, int], left: tuple[int, int, int], right: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    pixels = image.load()
    for x in range(width):
        t = x / max(1, width - 1)
        color = tuple(int(left[i] * (1 - t) + right[i] * t) for i in range(3)) + (255,)
        for y in range(height):
            pixels[x, y] = color
    return image


def _color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((int(left[i]) - int(right[i])) ** 2 for i in range(3)))


def _vibrant_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = (max(0, min(255, int(part))) / 255.0 for part in color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    s = max(0.58, min(1.0, s * 1.12))
    v = max(0.74, min(0.98, v if v > 0 else 0.82))
    rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
    return (int(rr * 255), int(gg * 255), int(bb * 255))


def _companion_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = (part / 255.0 for part in color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    rr, gg, bb = colorsys.hsv_to_rgb((h + 0.34) % 1.0, max(0.66, s), max(0.78, v))
    return (int(rr * 255), int(gg * 255), int(bb * 255))


def extract_image_palette(data: Optional[bytes]) -> Optional[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.fit(source.convert("RGB"), (96, 96), method=Image.Resampling.LANCZOS)
        image = image.filter(ImageFilter.GaussianBlur(0.8))
        quantized = image.quantize(colors=16, method=0)
        palette = quantized.getpalette() or []
        counted = quantized.getcolors(maxcolors=256) or []
    except Exception:
        return None
    ranked: list[tuple[float, tuple[int, int, int]]] = []
    for count, index in counted:
        offset = int(index) * 3
        if offset + 2 >= len(palette):
            continue
        raw = (int(palette[offset]), int(palette[offset + 1]), int(palette[offset + 2]))
        _h, saturation, value = colorsys.rgb_to_hsv(*(part / 255.0 for part in raw))
        if value < 0.12:
            continue
        if saturation < 0.10 and value > 0.82:
            continue
        if saturation < 0.08:
            continue
        score = float(count) * (0.45 + saturation * 1.55) * (0.58 + min(1.0, value + 0.18))
        ranked.append((score, _vibrant_color(raw)))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    primary = ranked[0][1]
    secondary: Optional[tuple[int, int, int]] = None
    secondary_rank = -1.0
    for score, candidate in ranked[1:]:
        distance = _color_distance(primary, candidate)
        if distance < 62:
            continue
        rank = score * (0.75 + min(1.5, distance / 180.0))
        if rank > secondary_rank:
            secondary = candidate
            secondary_rank = rank
    if secondary is None:
        secondary = _companion_color(primary)
    return primary, secondary


def _accent_tuple(value: Any) -> Optional[tuple[int, int, int]]:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            return _vibrant_color((int(value[0]), int(value[1]), int(value[2])))
        except Exception:
            return None
    try:
        parsed = parse_hex_color(value)
        return _vibrant_color(parsed) if parsed else None
    except Exception:
        return None


def _pair_with_distinct_secondary(
    primary: tuple[int, int, int],
    *palettes: Optional[tuple[tuple[int, int, int], tuple[int, int, int]]],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    for palette in palettes:
        if not palette:
            continue
        for candidate in palette:
            if _color_distance(primary, candidate) >= 68:
                return primary, candidate
    return primary, _companion_color(primary)


def resolve_card_palette(
    *,
    theme: WelcomeCardTheme,
    color_mode: Any = DEFAULT_COLOR_MODE,
    custom_primary: Any = None,
    custom_secondary: Any = None,
    profile_banner_bytes: Optional[bytes] = None,
    profile_accent: Any = None,
    avatar_bytes: Optional[bytes] = None,
    card_background_bytes: Optional[bytes] = None,
) -> WelcomeCardPalette:
    mode = normalize_color_mode(color_mode)
    theme_pair = (theme.primary, theme.secondary)
    banner_pair = extract_image_palette(profile_banner_bytes)
    card_pair = extract_image_palette(card_background_bytes)
    avatar_pair = extract_image_palette(avatar_bytes)
    accent = _accent_tuple(profile_accent)
    if mode == "custom":
        primary = parse_hex_color(custom_primary)
        secondary = parse_hex_color(custom_secondary)
        if primary and secondary:
            primary = _vibrant_color(primary)
            secondary = _vibrant_color(secondary)
            if _color_distance(primary, secondary) < 54:
                secondary = _companion_color(primary)
            return WelcomeCardPalette(primary, secondary, "custom")
        mode = DEFAULT_COLOR_MODE
    if mode == "profile":
        if banner_pair:
            return WelcomeCardPalette(*banner_pair, source="profile-banner")
        if accent:
            pair = _pair_with_distinct_secondary(accent, avatar_pair, card_pair, theme_pair)
            return WelcomeCardPalette(*pair, source="profile-accent")
        if avatar_pair:
            return WelcomeCardPalette(*avatar_pair, source="avatar")
        return WelcomeCardPalette(*theme_pair, source="theme-fallback")
    if mode == "card":
        if card_pair:
            return WelcomeCardPalette(*card_pair, source="card-background")
        return WelcomeCardPalette(*theme_pair, source="theme-fallback")
    if mode == "theme":
        return WelcomeCardPalette(*theme_pair, source="theme")
    if banner_pair:
        return WelcomeCardPalette(*banner_pair, source="profile-banner")
    if accent:
        pair = _pair_with_distinct_secondary(accent, card_pair, avatar_pair, theme_pair)
        return WelcomeCardPalette(*pair, source="profile-accent")
    if card_pair:
        return WelcomeCardPalette(*card_pair, source="card-background")
    if avatar_pair:
        return WelcomeCardPalette(*avatar_pair, source="avatar")
    return WelcomeCardPalette(*theme_pair, source="theme-fallback")


def _draw_glow_line(base: Image.Image, points: list[tuple[int, int]], color: tuple[int, int, int], *, width: int = 3) -> None:
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.line(points, fill=(*color, 190), width=width + 8, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    base.alpha_composite(glow)
    ImageDraw.Draw(base).line(points, fill=(*color, 235), width=width, joint="curve")


def _draw_leaf(draw: ImageDraw.ImageDraw, center: tuple[int, int], size: int, color: tuple[int, int, int, int]) -> None:
    cx, cy = center
    draw.line((cx, cy + size // 2, cx, cy - size // 3), fill=color, width=max(2, size // 14))
    for angle_x, angle_y, scale in ((-1, -1, 0.7), (1, -1, 0.7), (-1, 0, 0.55), (1, 0, 0.55), (0, -1, 0.85)):
        w = int(size * 0.22 * scale)
        h = int(size * 0.55 * scale)
        x = cx + int(angle_x * size * 0.18) - w
        y = cy + int(angle_y * size * 0.18) - h // 2
        draw.ellipse((x, y, x + 2 * w, y + h), fill=color)


def _draw_mushroom(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, primary: tuple[int, int, int], secondary: tuple[int, int, int]) -> None:
    stem_w = max(5, size // 5)
    draw.rounded_rectangle((x - stem_w // 2, y, x + stem_w // 2, y + size), radius=stem_w // 2, fill=(*secondary, 140))
    draw.pieslice((x - size, y - size // 2, x + size, y + size // 2), 180, 360, fill=(*primary, 190))
    for dx in (-size // 2, 0, size // 2):
        draw.ellipse((x + dx - 3, y - size // 4 - 3, x + dx + 3, y - size // 4 + 3), fill=(255, 255, 255, 165))


def _base_background(theme: WelcomeCardTheme) -> Image.Image:
    canvas = _gradient((CARD_WIDTH, CARD_HEIGHT), theme.background, theme.panel).convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((380, 34, 1168, 366), radius=34, fill=(*theme.panel, 210), outline=(*theme.secondary, 70), width=2)
    draw.rounded_rectangle((16, 16, CARD_WIDTH - 16, CARD_HEIGHT - 16), radius=34, outline=(*theme.primary, 210), width=3)
    draw.rounded_rectangle((24, 24, CARD_WIDTH - 24, CARD_HEIGHT - 24), radius=30, outline=(*theme.secondary, 135), width=2)
    for x in range(430, 1140, 48):
        draw.ellipse((x, 54, x + 4, 58), fill=(*theme.secondary, 55))
        draw.ellipse((x, 342, x + 4, 346), fill=(*theme.primary, 45))
    _draw_glow_line(canvas, [(28, 26), (310, 26), (350, 58)], theme.primary, width=3)
    _draw_glow_line(canvas, [(852, 374), (1165, 374), (1180, 350)], theme.secondary, width=3)
    if theme.motif == "420":
        smoke = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(smoke, "RGBA")
        for offset in range(5):
            sd.arc((5 + offset * 16, 10, 270 + offset * 18, 250), 190, 355, fill=(*theme.primary, 70), width=10)
            sd.arc((920 - offset * 10, -60, 1220, 260 + offset * 16), 100, 270, fill=(*theme.secondary, 65), width=12)
        smoke = smoke.filter(ImageFilter.GaussianBlur(12))
        canvas.alpha_composite(smoke)
        draw = ImageDraw.Draw(canvas, "RGBA")
        _draw_mushroom(draw, 58, 310, 28, theme.secondary, theme.primary)
        _draw_mushroom(draw, 100, 325, 20, theme.primary, theme.secondary)
        _draw_leaf(draw, (1125, 316), 42, (*theme.primary, 135))
        _draw_leaf(draw, (1090, 332), 30, (*theme.secondary, 110))
        draw.ellipse((1090, 245, 1160, 335), outline=(*theme.secondary, 95), width=5)
        draw.rounded_rectangle((1120, 170, 1137, 260), radius=8, outline=(*theme.primary, 95), width=5)
    elif theme.motif == "esports":
        draw.polygon([(0, 0), (210, 0), (120, 90), (0, 145)], fill=(*theme.primary, 45))
        draw.polygon([(CARD_WIDTH, CARD_HEIGHT), (980, CARD_HEIGHT), (1080, 300), (CARD_WIDTH, 250)], fill=(*theme.secondary, 45))
    elif theme.motif == "premium":
        draw.arc((930, -90, 1270, 250), 100, 250, fill=(*theme.secondary, 90), width=3)
        draw.arc((-100, 210, 250, 520), 270, 70, fill=(*theme.primary, 80), width=3)
    elif theme.motif == "community":
        for radius, alpha in ((130, 30), (90, 45), (50, 60)):
            draw.ellipse((970 - radius, 190 - radius, 970 + radius, 190 + radius), outline=(*theme.secondary, alpha), width=3)
    return canvas


def _avatar_layer(
    avatar_bytes: bytes,
    theme: WelcomeCardTheme,
    *,
    primary: Optional[tuple[int, int, int]] = None,
    secondary: Optional[tuple[int, int, int]] = None,
) -> Image.Image:
    primary = primary or theme.primary
    secondary = secondary or theme.secondary
    try:
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
    except Exception:
        avatar = Image.new("RGBA", (512, 512), (*theme.panel, 255))
        fallback_draw = ImageDraw.Draw(avatar, "RGBA")
        fallback_draw.ellipse((140, 70, 372, 302), fill=(*theme.muted, 180))
        fallback_draw.rounded_rectangle((95, 280, 417, 480), radius=140, fill=(*theme.muted, 180))
    avatar = ImageOps.fit(avatar, (274, 274), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    mask = Image.new("L", avatar.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
    avatar.putalpha(mask)
    layer = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    gd.ellipse((39, 39, 361, 361), outline=(*primary, 220), width=20)
    gd.ellipse((47, 47, 353, 353), outline=(*secondary, 200), width=12)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    layer.alpha_composite(glow)
    ld = ImageDraw.Draw(layer, "RGBA")
    ld.ellipse((43, 43, 357, 357), fill=(*theme.panel, 230), outline=(*primary, 245), width=8)
    ld.ellipse((50, 50, 350, 350), outline=(*secondary, 230), width=5)
    layer.alpha_composite(avatar, (63, 63))
    return layer


def _paste_gradient_text(
    canvas: Image.Image,
    *,
    position: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    stroke_width: int,
    glow_radius: int,
    glow_alpha: int,
) -> tuple[int, int]:
    if not text:
        return (0, 0)
    probe = ImageDraw.Draw(canvas, "RGBA")
    box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    padding = max(10, glow_radius * 2, stroke_width * 4)
    size = (width + padding * 2, height + padding * 2)
    outline_mask = Image.new("L", size, 0)
    fill_mask = Image.new("L", size, 0)
    outline_draw = ImageDraw.Draw(outline_mask)
    fill_draw = ImageDraw.Draw(fill_mask)
    origin = (padding - box[0], padding - box[1])
    outline_draw.text(origin, text, font=font, fill=255, stroke_width=stroke_width, stroke_fill=255)
    fill_draw.text(origin, text, font=font, fill=255)
    target = (int(position[0]) - padding, int(position[1]) - padding)
    if glow_radius > 0 and glow_alpha > 0:
        blurred = outline_mask.filter(ImageFilter.GaussianBlur(glow_radius))
        alpha = blurred.point(lambda value: int(value * min(255, glow_alpha) / 255))
        glow = Image.new("RGBA", size, (*primary, 0))
        glow.putalpha(alpha)
        canvas.alpha_composite(glow, target)
    dark_outline = Image.new("RGBA", size, (2, 4, 9, 235))
    dark_outline.putalpha(outline_mask)
    canvas.alpha_composite(dark_outline, target)
    gradient = _gradient(size, primary, secondary)
    gradient.putalpha(fill_mask)
    canvas.alpha_composite(gradient, target)
    return width, height


def _draw_label(
    canvas: Image.Image,
    *,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    label_width = _text_width(draw, text, font=font, stroke_width=1)
    draw.line((x, y + 12, x + 28, y + 12), fill=(*primary, 215), width=3)
    draw.text((x + 39, y), text, font=font, fill=(*primary, 245), stroke_width=1, stroke_fill=(0, 0, 0, 210))
    tail_x = x + 50 + label_width
    draw.line((tail_x, y + 12, min(1135, tail_x + 56), y + 12), fill=(*secondary, 210), width=3)
    draw.ellipse((min(1132, tail_x + 62), y + 8, min(1140, tail_x + 70), y + 16), fill=(*secondary, 230))


def _draw_subtitle(
    canvas: Image.Image,
    *,
    x: int,
    y: int,
    fitted_text: str,
    suffix: str,
    ordinal: str,
    font: ImageFont.ImageFont,
    text_color: tuple[int, int, int],
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    common = {"font": font, "stroke_width": 1, "stroke_fill": (0, 0, 0, 210)}
    if not suffix or not fitted_text.endswith(suffix):
        draw.text((x, y), fitted_text, fill=(*text_color, 248), **common)
        return
    prefix = fitted_text[: -len(suffix)]
    suffix_lead = "  •  You are the "
    suffix_tail = " member!"
    cursor = float(x)
    draw.text((cursor, y), prefix, fill=(*text_color, 248), **common)
    cursor += float(draw.textlength(prefix, font=font))
    draw.text((cursor, y), suffix_lead, fill=(*text_color, 248), **common)
    cursor += float(draw.textlength(suffix_lead, font=font))
    draw.text((cursor, y), ordinal, fill=(*primary, 255), **common)
    cursor += float(draw.textlength(ordinal, font=font))
    draw.text((cursor, y), suffix_tail, fill=(*text_color, 248), **common)
    bullet_x = x + int(draw.textlength(prefix + "  ", font=font))
    draw.ellipse((bullet_x, y + 15, bullet_x + 7, y + 22), fill=(*secondary, 245))


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
            canvas = _cover(custom, (CARD_WIDTH, CARD_HEIGHT))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay, "RGBA")
        od.rounded_rectangle((380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 158))
        canvas.alpha_composite(overlay)
    else:
        canvas = _base_background(theme)
    frame = ImageDraw.Draw(canvas, "RGBA")
    frame.rounded_rectangle((16, 16, 1184, 384), radius=34, outline=(*primary, 225), width=3)
    frame.rounded_rectangle((24, 24, 1176, 376), radius=30, outline=(*secondary, 145), width=2)
    canvas.alpha_composite(_avatar_layer(avatar_bytes, theme, primary=primary, secondary=secondary))
    draw = ImageDraw.Draw(canvas, "RGBA")
    name = _safe_text(display_name, fallback="New Member", max_chars=64)
    server = _safe_text(server_name, fallback="Your Server", max_chars=72)
    ordinal = _ordinal(member_count)
    x = 420
    max_width = 710
    label_font = _font(20, bold=True, style_key=style.key, role="label")
    welcome_font = _font(style.welcome_size, bold=True, style_key=style.key, role="welcome")
    name, name_font = _fit_text(
        draw,
        name,
        max_width=max_width,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        bold=True,
        stroke_width=style.name_stroke,
        style_key=style.key,
        role="name",
    )
    subtitle_suffix = f"  •  You are the {ordinal} member!"
    subtitle = f"to {server}{subtitle_suffix}"
    subtitle, subtitle_font = _fit_text(
        draw,
        subtitle,
        max_width=max_width,
        start_size=style.subtitle_start_size,
        min_size=style.subtitle_min_size,
        bold=True,
        stroke_width=1,
        preserve_suffix=subtitle_suffix,
        style_key=style.key,
        role="body",
    )
    _draw_label(
        canvas,
        x=x,
        y=42,
        text=theme.label.upper(),
        font=label_font,
        primary=primary,
        secondary=secondary,
    )
    _paste_gradient_text(
        canvas,
        position=(x, 79),
        text="WELCOME",
        font=welcome_font,
        primary=theme.text,
        secondary=theme.text,
        stroke_width=2,
        glow_radius=max(3, style.glow_radius // 2),
        glow_alpha=max(70, style.glow_alpha - 25),
    )
    _paste_gradient_text(
        canvas,
        position=(x, 139),
        text=name,
        font=name_font,
        primary=primary,
        secondary=secondary,
        stroke_width=style.name_stroke,
        glow_radius=style.glow_radius,
        glow_alpha=style.glow_alpha,
    )
    line_y = 270
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.line((x, line_y, 1135, line_y), fill=(*primary, 195), width=3)
    draw.ellipse((775, line_y - 5, 785, line_y + 5), fill=(*secondary, 245))
    _draw_subtitle(
        canvas,
        x=x,
        y=292,
        fitted_text=subtitle,
        suffix=subtitle_suffix,
        ordinal=ordinal,
        font=subtitle_font,
        text_color=theme.text,
        primary=primary,
        secondary=secondary,
    )
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


__all__ = [
    "BUILTIN_THEMES",
    "CARD_HEIGHT",
    "CARD_RATIO",
    "CARD_WIDTH",
    "COLOR_MODES",
    "DEFAULT_COLOR_MODE",
    "DEFAULT_FONT_STYLE_KEY",
    "DEFAULT_THEME_KEY",
    "FONT_STYLES",
    "MAX_CUSTOM_BACKGROUND_BYTES",
    "WelcomeCardFontStyle",
    "WelcomeCardPalette",
    "WelcomeCardTheme",
    "color_mode_choices",
    "extract_image_palette",
    "font_style_choices",
    "normalize_color_mode",
    "normalize_font_style_key",
    "normalize_hex_color",
    "normalize_theme_key",
    "parse_hex_color",
    "render_welcome_card",
    "resolve_card_palette",
    "theme_choices",
    "validate_custom_background",
]
