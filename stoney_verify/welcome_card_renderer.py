from __future__ import annotations

"""Production-safe welcome card rendering.

The renderer owns the canvas, avatar crop, dynamic text, and built-in themes.
Backgrounds never contain baked-in usernames or member counts. A guild may use
one of the built-in themes or provide a validated custom 3:1 image.
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Optional

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

_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
)
_FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
)


def normalize_theme_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return key if key in BUILTIN_THEMES else DEFAULT_THEME_KEY


def theme_choices() -> list[tuple[str, str]]:
    return [(theme.key, theme.label) for theme in BUILTIN_THEMES.values()]


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates: Iterable[str] = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    for path in candidates:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, max(8, int(size)))
        except Exception:
            continue
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
) -> tuple[str, ImageFont.ImageFont]:
    start = max(int(start_size), int(min_size))
    minimum = max(8, int(min_size))
    sizes = list(range(start, minimum - 1, -2))
    if not sizes or sizes[-1] != minimum:
        sizes.append(minimum)

    for size in sizes:
        candidate_font = _font(size, bold=bold)
        if _text_width(
            draw,
            text,
            font=candidate_font,
            stroke_width=stroke_width,
        ) <= max_width:
            return text, candidate_font

    fitted_font = _font(minimum, bold=bold)
    suffix = preserve_suffix if preserve_suffix and text.endswith(preserve_suffix) else ""
    if suffix:
        prefix = text[: -len(suffix)]
        suffix_width = _text_width(
            draw,
            suffix,
            font=fitted_font,
            stroke_width=stroke_width,
        )
        prefix_width = max(0, max_width - suffix_width)
        fitted_prefix = _ellipsize_text(
            draw,
            prefix,
            font=fitted_font,
            max_width=prefix_width,
            stroke_width=stroke_width,
        )
        fitted_text = fitted_prefix + suffix
        if _text_width(
            draw,
            fitted_text,
            font=fitted_font,
            stroke_width=stroke_width,
        ) <= max_width:
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


def _draw_glow_line(
    base: Image.Image,
    points: list[tuple[int, int]],
    color: tuple[int, int, int],
    *,
    width: int = 3,
) -> None:
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
    canvas = _gradient((CARD_WIDTH, CARD_HEIGHT), theme.background, theme.panel)
    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Dark text-safe panel on the right.
    draw.rounded_rectangle((380, 34, 1168, 366), radius=34, fill=(*theme.panel, 210), outline=(*theme.secondary, 70), width=2)

    # Futuristic frame and low-contrast geometry.
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
        # Abstract glass/dab-rig silhouette outside the text-safe area.
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


def _avatar_layer(avatar_bytes: bytes, theme: WelcomeCardTheme) -> Image.Image:
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
    gd.ellipse((39, 39, 361, 361), outline=(*theme.primary, 220), width=20)
    gd.ellipse((47, 47, 353, 353), outline=(*theme.secondary, 200), width=12)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    layer.alpha_composite(glow)
    ld = ImageDraw.Draw(layer, "RGBA")
    ld.ellipse((43, 43, 357, 357), fill=(*theme.panel, 230), outline=(*theme.primary, 245), width=8)
    ld.ellipse((50, 50, 350, 350), outline=(*theme.secondary, 230), width=5)
    layer.alpha_composite(avatar, (63, 63))
    return layer


def render_welcome_card(
    *,
    avatar_bytes: bytes,
    display_name: Any,
    server_name: Any,
    member_count: int,
    theme_key: Any = DEFAULT_THEME_KEY,
    custom_background_bytes: Optional[bytes] = None,
) -> bytes:
    theme = BUILTIN_THEMES[normalize_theme_key(theme_key)]
    if custom_background_bytes:
        validate_custom_background(custom_background_bytes)
        with Image.open(BytesIO(custom_background_bytes)) as custom:
            canvas = _cover(custom, (CARD_WIDTH, CARD_HEIGHT))
        # Preserve readability regardless of the uploaded image.
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay, "RGBA")
        od.rounded_rectangle((380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 165))
        od.rounded_rectangle((16, 16, 1184, 384), radius=34, outline=(*theme.primary, 210), width=3)
        canvas.alpha_composite(overlay)
    else:
        canvas = _base_background(theme)

    canvas.alpha_composite(_avatar_layer(avatar_bytes, theme))
    draw = ImageDraw.Draw(canvas, "RGBA")

    name = _safe_text(display_name, fallback="New Member", max_chars=64)
    server = _safe_text(server_name, fallback="Your Server", max_chars=72)
    ordinal = _ordinal(member_count)

    x = 420
    max_width = 710
    label_font = _font(42, bold=True)
    name, name_font = _fit_text(
        draw,
        name,
        max_width=max_width,
        start_size=78,
        min_size=38,
        bold=True,
        stroke_width=1,
    )
    subtitle_suffix = f"  •  You are the {ordinal} member!"
    subtitle = f"to {server}{subtitle_suffix}"
    subtitle, subtitle_font = _fit_text(
        draw,
        subtitle,
        max_width=max_width,
        start_size=31,
        min_size=20,
        bold=False,
        stroke_width=1,
        preserve_suffix=subtitle_suffix,
    )

    draw.text((x, 82), "WELCOME", font=label_font, fill=(*theme.text, 245), stroke_width=1, stroke_fill=(0, 0, 0, 210))

    # Gradient name text mask.
    name_box = draw.textbbox((0, 0), name, font=name_font, stroke_width=1)
    name_w = max(1, name_box[2] - name_box[0])
    name_h = max(1, name_box[3] - name_box[1])
    name_mask = Image.new("L", (name_w + 10, name_h + 12), 0)
    md = ImageDraw.Draw(name_mask)
    md.text((5 - name_box[0], 5 - name_box[1]), name, font=name_font, fill=255, stroke_width=1, stroke_fill=255)
    name_gradient = _gradient(name_mask.size, theme.primary, theme.secondary)
    canvas.paste(name_gradient, (x - 5, 140 - 5), name_mask)

    line_y = 260
    draw.line((x, line_y, 1135, line_y), fill=(*theme.primary, 155), width=2)
    draw.ellipse((775, line_y - 4, 783, line_y + 4), fill=(*theme.secondary, 230))
    draw.text((x, 286), subtitle, font=subtitle_font, fill=(*theme.text, 245), stroke_width=1, stroke_fill=(0, 0, 0, 200))

    # Small theme label. This is dynamic text, never baked into a background asset.
    theme_font = _font(17, bold=True)
    draw.text((x, 48), theme.label.upper(), font=theme_font, fill=(*theme.muted, 185))

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


__all__ = [
    "BUILTIN_THEMES",
    "CARD_HEIGHT",
    "CARD_RATIO",
    "CARD_WIDTH",
    "DEFAULT_THEME_KEY",
    "MAX_CUSTOM_BACKGROUND_BYTES",
    "WelcomeCardTheme",
    "normalize_theme_key",
    "render_welcome_card",
    "theme_choices",
    "validate_custom_background",
]
