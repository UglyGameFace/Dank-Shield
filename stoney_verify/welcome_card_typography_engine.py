from __future__ import annotations

"""Proportion-safe welcome-card typography with custom font support.

The module intentionally reuses the existing production background, palette,
avatar, and vector-icon primitives. It owns only typography selection/fitting so
there remains one approved avatar geometry and one live delivery path.
"""

from io import BytesIO
from typing import Any, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import welcome_card_studio_renderer as base

CARD_WIDTH = base.CARD_WIDTH
CARD_HEIGHT = base.CARD_HEIGHT
CARD_RATIO = base.CARD_RATIO
MAX_CUSTOM_BACKGROUND_BYTES = base.MAX_CUSTOM_BACKGROUND_BYTES
BUILTIN_THEMES = base.BUILTIN_THEMES
DEFAULT_THEME_KEY = base.DEFAULT_THEME_KEY
DEFAULT_COLOR_MODE = base.DEFAULT_COLOR_MODE
COLOR_MODES = base.COLOR_MODES
COLOR_PRESETS = base.COLOR_PRESETS
COLOR_SWATCHES = base.COLOR_SWATCHES
WelcomeCardFontStyle = base.WelcomeCardFontStyle
WelcomeCardPalette = base.WelcomeCardPalette
WelcomeCardTheme = base.WelcomeCardTheme

DEFAULT_FONT_STYLE_KEY = "neon"
CUSTOM_FONT_STYLE_KEY = "custom"
NAME_SAFE_WIDTH = 710
NAME_SAFE_HEIGHT = 104
WELCOME_SAFE_HEIGHT = 58


# No built-in style uses horizontal stretching. Visual differences come from
# font family, tracking, outline, cut, shadow, glow, pixel, and slant effects.
FONT_STYLES: dict[str, WelcomeCardFontStyle] = {
    "neon": WelcomeCardFontStyle(
        "neon", "Neon Display", "Bright condensed glow with a clean gaming look.",
        "sans", "neon", 58, 100, 42, 32, 21, 0, 2, 13, 165, 1.0, 0.0, False,
    ),
    "tech": WelcomeCardFontStyle(
        "tech", "Tech Circuit", "Tracked mono lettering with digital scan cuts.",
        "mono", "tech", 52, 84, 38, 28, 19, 3, 2, 7, 125, 1.0, 0.0, True,
    ),
    "bold": WelcomeCardFontStyle(
        "bold", "Heavy Impact", "Strong esports type with a controlled offset shadow.",
        "sans", "impact", 56, 88, 38, 31, 20, 0, 2, 4, 72, 1.0, 0.0, False,
    ),
    "clean": WelcomeCardFontStyle(
        "clean", "Clean Modern", "Polished minimal type with restrained effects.",
        "sans", "clean", 50, 88, 40, 30, 20, 0, 1, 2, 45, 1.0, 0.0, False,
    ),
    "chrome": WelcomeCardFontStyle(
        "chrome", "Chrome Luxe", "Metallic highlights with premium depth.",
        "serif", "chrome", 54, 90, 40, 30, 20, 0, 2, 5, 88, 1.0, 0.0, False,
    ),
    "outline": WelcomeCardFontStyle(
        "outline", "Hollow Outline", "Open center with a bright two-color border.",
        "sans", "outline", 56, 92, 40, 30, 20, 1, 4, 8, 120, 1.0, 0.0, False,
    ),
    "arcade": WelcomeCardFontStyle(
        "arcade", "Arcade Pixel", "Chunky pixel treatment for retro gaming servers.",
        "mono", "arcade", 52, 84, 36, 28, 19, 2, 2, 4, 82, 1.0, 0.0, True,
    ),
    "street": WelcomeCardFontStyle(
        "street", "Street Slant", "Skewed layered type with a bold offset shadow.",
        "sans", "street", 55, 90, 38, 30, 20, 0, 2, 5, 82, 1.0, 0.16, False,
    ),
    "future": WelcomeCardFontStyle(
        "future", "Future Wide", "Tracked capitals with angular technology cuts.",
        "sans", "future", 52, 82, 36, 28, 19, 4, 2, 7, 118, 1.0, 0.0, True,
    ),
    "soft": WelcomeCardFontStyle(
        "soft", "Soft Luxe", "Smooth serif styling with a softer profile glow.",
        "serif", "soft", 52, 88, 40, 30, 20, 0, 1, 10, 90, 1.0, 0.07, False,
    ),
    "stencil": WelcomeCardFontStyle(
        "stencil", "Combat Stencil", "Bold tactical capitals with deliberate cut lines.",
        "sans", "stencil", 53, 86, 36, 29, 19, 2, 2, 5, 85, 1.0, 0.0, True,
    ),
    "varsity": WelcomeCardFontStyle(
        "varsity", "Varsity Badge", "Athletic outlined lettering for teams and clans.",
        "serif", "outline", 54, 88, 38, 29, 19, 1, 3, 5, 90, 1.0, 0.0, True,
    ),
    "blackletter": WelcomeCardFontStyle(
        "blackletter", "Midnight Gothic", "Dark premium serif type with metallic depth.",
        "serif", "chrome", 53, 88, 38, 29, 19, 0, 2, 6, 92, 1.0, 0.0, False,
    ),
    "prism": WelcomeCardFontStyle(
        "prism", "Prism Glow", "Clean lettering with a brighter two-tone aura.",
        "sans", "prism", 55, 92, 40, 30, 20, 1, 2, 15, 185, 1.0, 0.0, False,
    ),
    "terminal": WelcomeCardFontStyle(
        "terminal", "Terminal Code", "Compact mono capitals with fine scan lines.",
        "mono", "terminal", 50, 80, 35, 27, 18, 2, 1, 4, 72, 1.0, 0.0, True,
    ),
    "retro": WelcomeCardFontStyle(
        "retro", "Retro Wave", "Soft pixel edges with an arcade-era glow.",
        "sans", "retro", 54, 88, 38, 29, 19, 1, 2, 9, 130, 1.0, 0.0, False,
    ),
}

CUSTOM_FONT_STYLE = WelcomeCardFontStyle(
    CUSTOM_FONT_STYLE_KEY,
    "Uploaded Font",
    "The server's validated custom font.",
    "sans",
    "custom",
    56,
    94,
    36,
    30,
    20,
    0,
    2,
    8,
    110,
    1.0,
    0.0,
    False,
)


def normalize_theme_key(value: Any) -> str:
    return base.normalize_theme_key(value)


def normalize_color_mode(value: Any) -> str:
    return base.normalize_color_mode(value)


def normalize_hex_color(value: Any) -> str:
    return base.normalize_hex_color(value)


def parse_hex_color(value: Any) -> Optional[tuple[int, int, int]]:
    return base.parse_hex_color(value)


def validate_custom_background(data: bytes) -> None:
    base.validate_custom_background(data)


def resolve_card_palette(**kwargs: Any) -> WelcomeCardPalette:
    return base.resolve_card_palette(**kwargs)


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
    return base._font(max(8, int(size)), family=style.family, bold=bold)


def _tracked_text_mask(
    text: str,
    *,
    font: ImageFont.ImageFont,
    tracking: int,
    stroke_width: int,
) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("L", (8, 8), 0))
    padding = max(8, stroke_width * 3)
    if tracking <= 0:
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        width = max(1, box[2] - box[0])
        height = max(1, box[3] - box[1])
        mask = Image.new("L", (width + padding * 2, height + padding * 2), 0)
        ImageDraw.Draw(mask).text(
            (padding - box[0], padding - box[1]),
            text,
            font=font,
            fill=255,
            stroke_width=stroke_width,
            stroke_fill=255,
        )
        return mask

    metrics: list[tuple[str, tuple[int, int, int, int], int]] = []
    max_height = 1
    for character in text:
        box = probe.textbbox((0, 0), character, font=font, stroke_width=stroke_width)
        width = max(1, box[2] - box[0])
        max_height = max(max_height, box[3] - box[1])
        metrics.append((character, box, width))
    total_width = sum(item[2] for item in metrics) + max(0, len(metrics) - 1) * tracking
    mask = Image.new("L", (max(1, total_width) + padding * 2, max_height + padding * 2), 0)
    draw = ImageDraw.Draw(mask)
    cursor = padding
    for character, box, width in metrics:
        draw.text(
            (cursor - box[0], padding - box[1]),
            character,
            font=font,
            fill=255,
            stroke_width=stroke_width,
            stroke_fill=255,
        )
        cursor += width + tracking
    return mask


def _crop_mask(mask: Image.Image) -> Image.Image:
    box = mask.getbbox()
    return mask.crop(box) if box else Image.new("L", (1, 1), 0)


def _transform_mask(mask: Image.Image, style: WelcomeCardFontStyle) -> Image.Image:
    transformed = _crop_mask(mask)
    # Shearing changes angle but does not squeeze or stretch the glyph proportions.
    if style.shear:
        offset = int(abs(style.shear) * transformed.height) + 4
        transformed = transformed.transform(
            (transformed.width + offset, transformed.height),
            Image.Transform.AFFINE,
            (1, -style.shear, offset if style.shear > 0 else 0, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
        transformed = _crop_mask(transformed)
    if style.effect in {"arcade", "retro"}:
        small = transformed.resize(
            (max(1, transformed.width // 3), max(1, transformed.height // 3)),
            Image.Resampling.BILINEAR,
        )
        transformed = small.resize(transformed.size, Image.Resampling.NEAREST)
    if style.effect in {"tech", "future", "stencil", "terminal"}:
        cut = Image.new("L", transformed.size, 255)
        cut_draw = ImageDraw.Draw(cut)
        spacing = {"tech": 12, "future": 17, "stencil": 22, "terminal": 9}[style.effect]
        thickness = 2 if style.effect == "stencil" else 1
        for y in range(6, transformed.height, spacing):
            cut_draw.rectangle((0, y, transformed.width, y + thickness), fill=0)
        transformed = ImageChops.multiply(transformed, cut)
    if style.effect == "street":
        transformed = transformed.rotate(-1.2, resample=Image.Resampling.BICUBIC, expand=True)
        transformed = _crop_mask(transformed)
    return transformed


def _mask_for_text(
    text: str,
    *,
    style: WelcomeCardFontStyle,
    size: int,
    role: str,
    stroke_width: int,
    custom_font_bytes: Optional[bytes],
) -> Image.Image:
    font = _font(size, style=style, bold=True, custom_font_bytes=custom_font_bytes)
    mask = _tracked_text_mask(
        text,
        font=font,
        tracking=style.tracking if role in {"name", "welcome"} else 0,
        stroke_width=stroke_width,
    )
    return _transform_mask(mask, style)


def _fitted_mask(
    text: str,
    *,
    style: WelcomeCardFontStyle,
    start_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    role: str,
    stroke_width: int,
    custom_font_bytes: Optional[bytes] = None,
) -> tuple[str, Image.Image]:
    candidate = text.upper() if style.uppercase_name and role == "name" else text
    minimum = max(8, int(min_size))
    for size in range(max(int(start_size), minimum), minimum - 1, -2):
        mask = _mask_for_text(
            candidate,
            style=style,
            size=size,
            role=role,
            stroke_width=stroke_width,
            custom_font_bytes=custom_font_bytes,
        )
        if mask.width <= max_width and mask.height <= max_height:
            return candidate, mask

    suffix = "..."
    base_text = candidate
    while base_text:
        attempt = base_text.rstrip() + suffix
        mask = _mask_for_text(
            attempt,
            style=style,
            size=minimum,
            role=role,
            stroke_width=stroke_width,
            custom_font_bytes=custom_font_bytes,
        )
        if mask.width <= max_width and mask.height <= max_height:
            return attempt, mask
        base_text = base_text[:-1]
    return "", Image.new("L", (1, 1), 0)


def _render_style(style_key: Any, custom_font_bytes: Optional[bytes]) -> WelcomeCardFontStyle:
    key = normalize_font_style_key(style_key)
    if key == CUSTOM_FONT_STYLE_KEY and custom_font_bytes:
        return CUSTOM_FONT_STYLE
    return FONT_STYLES.get(key, FONT_STYLES[DEFAULT_FONT_STYLE_KEY])


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
            canvas = base.legacy._cover(custom, (CARD_WIDTH, CARD_HEIGHT))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay, "RGBA").rounded_rectangle(
            (380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 158)
        )
        canvas.alpha_composite(overlay)
    else:
        canvas = base.legacy._base_background(theme)

    frame = ImageDraw.Draw(canvas, "RGBA")
    frame.rounded_rectangle((16, 16, 1184, 384), radius=34, outline=(*primary, 225), width=3)
    frame.rounded_rectangle((24, 24, 1176, 376), radius=30, outline=(*secondary, 145), width=2)
    # Exact approved avatar geometry is retained from the production primitive.
    canvas.alpha_composite(
        base.legacy._avatar_layer(avatar_bytes, theme, primary=primary, secondary=secondary)
    )

    name = base.legacy._safe_text(display_name, fallback="New Member", max_chars=64)
    server = base.legacy._safe_text(server_name, fallback="Your Server", max_chars=72)
    ordinal = base.legacy._ordinal(member_count)
    x = 420
    base._draw_theme_label(canvas, theme=theme, primary=primary, secondary=secondary)

    _, welcome_mask = _fitted_mask(
        "WELCOME",
        style=style,
        start_size=style.welcome_size,
        min_size=max(34, style.welcome_size - 18),
        max_width=NAME_SAFE_WIDTH,
        max_height=WELCOME_SAFE_HEIGHT,
        role="welcome",
        stroke_width=min(2, style.name_stroke),
        custom_font_bytes=custom_font_bytes,
    )
    base._composite_mask_text(
        canvas,
        position=(x, 76),
        mask=welcome_mask,
        style=style,
        primary=theme.text,
        secondary=tuple(min(255, part + 85) for part in primary),
        role="welcome",
    )

    _, name_mask = _fitted_mask(
        name,
        style=style,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        max_width=NAME_SAFE_WIDTH,
        max_height=NAME_SAFE_HEIGHT,
        role="name",
        stroke_width=style.name_stroke,
        custom_font_bytes=custom_font_bytes,
    )
    base._composite_mask_text(
        canvas,
        position=(x, 137),
        mask=name_mask,
        style=style,
        primary=primary,
        secondary=secondary,
        role="name",
    )

    draw = ImageDraw.Draw(canvas, "RGBA")
    line_y = 272
    draw.line((x, line_y, 1135, line_y), fill=(*primary, 190), width=3)
    base._draw_sparkle(draw, (782, line_y), 14, secondary)
    prefix, _tail, subtitle_font = base._fit_subtitle(
        server,
        ordinal,
        style=style,
        max_width=NAME_SAFE_WIDTH,
    )
    base._draw_subtitle(
        canvas,
        x=x,
        y=295,
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
    name = base.legacy._safe_text(display_name, fallback="New Member", max_chars=30)
    entries: list[tuple[WelcomeCardFontStyle, Optional[bytes], str]] = [
        (style, None, style.label) for style in FONT_STYLES.values()
    ]
    if custom_font_bytes:
        entries.append((CUSTOM_FONT_STYLE, custom_font_bytes, custom_font_name or "Uploaded Font"))

    width, row_height = 1200, 108
    height = 82 + row_height * len(entries)
    canvas = Image.new("RGBA", (width, height), (7, 9, 15, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.text(
        (38, 24),
        "WELCOME CARD FONT PICKER",
        font=base._font(38, family="sans", bold=True),
        fill=(248, 250, 255, 255),
    )
    draw.text(
        (720, 34),
        "All previews are fitted to the same safe box.",
        font=base._font(18, family="sans"),
        fill=(190, 198, 218, 255),
    )

    for index, (style, font_bytes, label) in enumerate(entries):
        top = 76 + index * row_height
        draw.rounded_rectangle(
            (26, top, width - 26, top + row_height - 12),
            radius=20,
            fill=(15, 19, 30, 235),
            outline=(*primary, 90),
            width=2,
        )
        draw.text(
            (50, top + 18),
            label[:34],
            font=base._font(22, family="sans", bold=True),
            fill=(245, 247, 252, 255),
        )
        draw.text(
            (50, top + 52),
            style.description,
            font=base._font(15, family="sans"),
            fill=(172, 182, 205, 255),
        )
        _, mask = _fitted_mask(
            name,
            style=style,
            start_size=62,
            min_size=26,
            max_width=655,
            max_height=66,
            role="name",
            stroke_width=style.name_stroke,
            custom_font_bytes=font_bytes,
        )
        base._composite_mask_text(
            canvas,
            position=(505, top + 18),
            mask=mask,
            style=style,
            primary=primary,
            secondary=secondary,
            role="name",
        )

    output = BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def render_color_catalog(*, swatches: bool = False) -> bytes:
    return base.render_color_catalog(swatches=swatches)


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
