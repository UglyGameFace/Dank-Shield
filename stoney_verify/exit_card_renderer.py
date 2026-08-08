from __future__ import annotations

"""Exit-card renderer built on the canonical Welcome Card visual engine.

This intentionally reuses the same themes, palette resolution, avatar treatment,
font effects, fitting, and safety dimensions as Welcome Card Studio. Only the
lifecycle-specific headline/subtitle differ.
"""

from io import BytesIO
from typing import Any, Optional

from PIL import Image, ImageDraw

from . import welcome_card_renderer as legacy
from . import welcome_card_typography_engine as engine


def render_exit_card(
    *,
    avatar_bytes: bytes,
    display_name: Any,
    server_name: Any,
    member_count: int,
    theme_key: Any = engine.DEFAULT_THEME_KEY,
    custom_background_bytes: Optional[bytes] = None,
    font_style_key: Any = engine.DEFAULT_FONT_STYLE_KEY,
    custom_font_bytes: Optional[bytes] = None,
    color_mode: Any = engine.DEFAULT_COLOR_MODE,
    custom_primary: Any = None,
    custom_secondary: Any = None,
    profile_banner_bytes: Optional[bytes] = None,
    profile_accent: Any = None,
) -> bytes:
    theme = engine.BUILTIN_THEMES[engine.normalize_theme_key(theme_key)]
    style = engine._render_style(font_style_key, custom_font_bytes)
    palette = engine.resolve_card_palette(
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
        engine.validate_custom_background(custom_background_bytes)
        with Image.open(BytesIO(custom_background_bytes)) as custom:
            canvas = legacy._cover(custom, (engine.CARD_WIDTH, engine.CARD_HEIGHT))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay, "RGBA").rounded_rectangle(
            (380, 30, 1170, 370), radius=36, fill=(0, 0, 0, 158)
        )
        canvas.alpha_composite(overlay)
    else:
        canvas = legacy._base_background(theme)

    frame = ImageDraw.Draw(canvas, "RGBA")
    frame.rounded_rectangle(
        (16, 16, 1184, 384),
        radius=34,
        outline=(*primary, 225),
        width=3,
    )
    frame.rounded_rectangle(
        (24, 24, 1176, 376),
        radius=30,
        outline=(*secondary, 145),
        width=2,
    )
    canvas.alpha_composite(
        legacy._avatar_layer(
            avatar_bytes,
            theme,
            primary=primary,
            secondary=secondary,
        )
    )

    name = legacy._safe_text(display_name, fallback="Member", max_chars=64)
    server = legacy._safe_text(server_name, fallback="Your Server", max_chars=72)
    x = 420
    engine._draw_theme_label(
        canvas,
        theme=theme,
        primary=primary,
        secondary=secondary,
    )

    _, goodbye_tile = engine._fitted_tile(
        "GOODBYE",
        style=style,
        start_size=style.welcome_size,
        min_size=max(32, style.welcome_size - 18),
        max_width=engine.NAME_SAFE_WIDTH,
        max_height=engine.WELCOME_SAFE_HEIGHT,
        role="welcome",
        primary=theme.text,
        secondary=tuple(min(255, part + 80) for part in primary),
        custom_font_bytes=custom_font_bytes,
    )
    engine._place_tile(
        canvas,
        goodbye_tile,
        left=x,
        top=73,
        box_width=engine.NAME_SAFE_WIDTH,
        box_height=engine.WELCOME_SAFE_HEIGHT,
    )

    _, name_tile = engine._fitted_tile(
        name,
        style=style,
        start_size=style.name_start_size,
        min_size=style.name_min_size,
        max_width=engine.NAME_SAFE_WIDTH,
        max_height=engine.NAME_SAFE_HEIGHT,
        role="name",
        primary=primary,
        secondary=secondary,
        custom_font_bytes=custom_font_bytes,
    )
    engine._place_tile(
        canvas,
        name_tile,
        left=x,
        top=137,
        box_width=engine.NAME_SAFE_WIDTH,
        box_height=engine.NAME_SAFE_HEIGHT,
    )

    draw = ImageDraw.Draw(canvas, "RGBA")
    line_y = 270
    draw.line((x, line_y, 1135, line_y), fill=(*primary, 190), width=3)
    engine._draw_sparkle(draw, (782, line_y), 14, secondary)

    count_text = f"Members now: {max(0, int(member_count or 0)):,}"
    subtitle = f"from {server}  •  {count_text}"
    subtitle_style = engine.FONT_STYLES.get("clean", style)
    _, subtitle_tile = engine._fitted_tile(
        subtitle,
        style=subtitle_style,
        start_size=30,
        min_size=18,
        max_width=engine.NAME_SAFE_WIDTH,
        max_height=62,
        role="subtitle",
        primary=primary,
        secondary=secondary,
        custom_font_bytes=None,
    )
    engine._place_tile(
        canvas,
        subtitle_tile,
        left=x,
        top=292,
        box_width=engine.NAME_SAFE_WIDTH,
        box_height=62,
    )

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


__all__ = ["render_exit_card"]
