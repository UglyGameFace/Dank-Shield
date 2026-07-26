from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


style_path = Path("stoney_verify/profile_signature_style.py")
replace_once(
    style_path,
    """from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    FONT_STYLES,
    parse_hex_color,
)""",
    """from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    DEFAULT_THEME_KEY,
    FONT_STYLES,
    parse_hex_color,
)""",
    label="style default-theme import",
)
replace_once(
    style_path,
    """DEFAULT_SERVER_PROFILE_STYLE: dict[str, str] = {
    "theme": "default",""",
    """DEFAULT_SERVER_PROFILE_STYLE: dict[str, str] = {
    "theme": DEFAULT_THEME_KEY,""",
    label="style valid default theme",
)
replace_once(
    style_path,
    """def palette_style_updates(preset_key: str, *, member: bool) -> dict[str, str]:""",
    """def theme_style_updates(theme_key: str, *, member: bool) -> dict[str, str]:
    clean = str(theme_key or "").strip().lower().replace("-", "_")
    if member and clean == PROFILE_THEME_INHERIT:
        return {
            "signature_theme": PROFILE_THEME_INHERIT,
            "signature_color_mode": PROFILE_COLOR_INHERIT,
            "signature_background_mode": PROFILE_BACKGROUND_INHERIT,
        }
    if clean not in BUILTIN_THEMES:
        raise ValueError("That profile-signature theme is no longer available.")
    if member:
        return {
            "signature_theme": clean,
            "signature_color_mode": "theme",
            "signature_background_mode": "theme",
        }
    return {
        SERVER_STYLE_CONFIG_KEYS["theme"]: clean,
        SERVER_STYLE_CONFIG_KEYS["color_mode"]: "theme",
        SERVER_STYLE_CONFIG_KEYS["background_mode"]: "theme",
    }


def palette_style_updates(preset_key: str, *, member: bool) -> dict[str, str]:""",
    label="style complete theme updates helper",
)
replace_once(
    style_path,
    """    "server_style_updates",
]""",
    """    "server_style_updates",
    "theme_style_updates",
]""",
    label="style helper export",
)

studio_path = Path("stoney_verify/profile_signature_studio.py")
replace_once(
    studio_path,
    """    normalize_member_profile_style,
    server_profile_style,
)""",
    """    normalize_member_profile_style,
    server_profile_style,
    theme_style_updates,
)""",
    label="studio theme helper import",
)
replace_once(
    studio_path,
    """                {SERVER_STYLE_CONFIG_KEYS["theme"]: value},
                message=f"Server profile-signature theme set to **{BUILTIN_THEMES[value].label}**.",""",
    """                theme_style_updates(value, member=False),
                message=f"Server profile-signature theme set to **{BUILTIN_THEMES[value].label}** with its colors and background.",""",
    label="studio server theme save",
)
replace_once(
    studio_path,
    """                {"signature_theme": value},
                message=f"Your signature theme is now **{label}**.",""",
    """                theme_style_updates(value, member=True),
                message=f"Your signature theme is now **{label}** with its colors and background.",""",
    label="studio member theme save",
)
replace_once(
    studio_path,
    """        choices.append(make_choice("Server Default", "server", description="Follow this server's profile-signature theme.", emoji="🏠", default=current == "server"))""",
    """        choices.append(make_choice("Server Default", "server", description="Restore the server's complete signature look.", emoji="🏠", default=current == "server"))""",
    label="studio server default copy",
)
replace_once(
    studio_path,
    """            description="Compact signature theme",""",
    """            description="Apply this theme's colors, background, and artwork",""",
    label="studio theme option copy",
)
replace_once(
    studio_path,
    """        content="## 🖼️ Signature Themes\nPick a look. Your preview updates after saving.",""",
    """        content="## 🖼️ Signature Themes\nPick a complete look. Its colors, background, and artwork apply immediately; you can override individual parts afterward.",""",
    label="studio theme picker guidance",
)

renderer_path = Path("stoney_verify/profile_signature_renderer.py")
replace_once(
    renderer_path,
    """    return canvas


def _avatar_tile(avatar_bytes: bytes, display_name: str, primary: tuple[int, int, int], size: int) -> Image.Image:""",
    """    return canvas


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


def _avatar_tile(avatar_bytes: bytes, display_name: str, primary: tuple[int, int, int], size: int) -> Image.Image:""",
    label="renderer theme motif helper",
)
replace_once(
    renderer_path,
    """    if layout != "minimal":
        draw.ellipse((810, -170, 1180, 200), fill=primary + (24,), outline=primary + (78,), width=2)
        draw.ellipse((880, -90, 1210, 240), fill=secondary + (18,), outline=secondary + (72,), width=2)
        for offset in range(-80, 1180, 92):
            draw.line((offset, 220, offset + 180, 0), fill=secondary + (20,), width=2)
""",
    """    _draw_theme_motif(
        draw,
        motif=getattr(theme, "motif", "generic"),
        primary=primary,
        secondary=secondary,
        layout=layout,
    )
""",
    label="renderer apply theme motif",
)

Path("tests/test_profile_signature_theme_application.py").write_text(
    '''from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from stoney_verify.profile_signature_renderer import (
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    _draw_theme_motif,
    render_profile_signature,
)
from stoney_verify.profile_signature_style import (
    DEFAULT_SERVER_PROFILE_STYLE,
    SERVER_STYLE_CONFIG_KEYS,
    theme_style_updates,
)
from stoney_verify.welcome_card_typography_engine import BUILTIN_THEMES, DEFAULT_THEME_KEY


def _render(theme: str) -> bytes:
    return render_profile_signature(
        avatar_bytes=b"",
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        role_labels=["Interests: gaming • music"],
        date_labels=["Discord since Oct 2019"],
        platform_labels=["Xbox: UglyGameFace"],
        style={
            "theme": theme,
            "font": "clean",
            "color_mode": "theme",
            "background_mode": "theme",
            "layout": "classic",
            "avatar_frame": "glow",
        },
    )


def test_default_profile_theme_is_a_real_builtin_theme() -> None:
    assert DEFAULT_SERVER_PROFILE_STYLE["theme"] == DEFAULT_THEME_KEY
    assert DEFAULT_SERVER_PROFILE_STYLE["theme"] in BUILTIN_THEMES


def test_member_theme_selection_applies_the_complete_theme_look() -> None:
    assert theme_style_updates("420_lobby", member=True) == {
        "signature_theme": "420_lobby",
        "signature_color_mode": "theme",
        "signature_background_mode": "theme",
    }
    assert theme_style_updates("server", member=True) == {
        "signature_theme": "server",
        "signature_color_mode": "server",
        "signature_background_mode": "server",
    }


def test_server_theme_selection_applies_theme_palette_and_background() -> None:
    assert theme_style_updates("cyber_neon", member=False) == {
        SERVER_STYLE_CONFIG_KEYS["theme"]: "cyber_neon",
        SERVER_STYLE_CONFIG_KEYS["color_mode"]: "theme",
        SERVER_STYLE_CONFIG_KEYS["background_mode"]: "theme",
    }


def test_420_lobby_theme_renders_its_lime_and_purple_accents() -> None:
    with Image.open(BytesIO(_render("420_lobby"))) as image:
        pixels = set(image.convert("RGB").getdata())
    assert BUILTIN_THEMES["420_lobby"].primary in pixels
    assert BUILTIN_THEMES["420_lobby"].secondary in pixels


def test_each_builtin_theme_motif_has_a_distinct_compact_treatment() -> None:
    rendered: list[bytes] = []
    for theme in BUILTIN_THEMES.values():
        image = Image.new("RGBA", (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        _draw_theme_motif(
            draw,
            motif=theme.motif,
            primary=(90, 255, 45),
            secondary=(174, 75, 255),
            layout="classic",
        )
        rendered.append(image.tobytes())
    assert len(set(rendered)) == len(BUILTIN_THEMES)
''',
    encoding="utf-8",
)

Path("ACTIVE_TASK.md").write_text(
    '''# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-006 — Make Profile Signature themes visibly apply

**Status:** IMPLEMENTED / EXACT-HEAD VALIDATION REQUIRED
**Branch:** `fix/profile-theme-application`
**PR:** `#135`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until selecting a Profile Signature theme visibly changes the saved preview and the deployed live signature.

## Live finding

The owner selected **420 Lobby Neon** and the confirmation stored the theme key, but the preview remained beige/gray. The member's inherited `profile` color mode continued overriding the theme's lime/purple palette, and the compact renderer reused one generic decoration for every built-in theme.

## Root causes

- The theme picker updated only `signature_theme`.
- Existing color/background overrides remained active, so the selected theme was mostly hidden.
- The profile renderer ignored each theme's motif and drew the same circles/diagonal lines for every preset.
- The server profile default used the invalid key `default` rather than the canonical built-in default.

## Changes

- Theme selection now atomically applies the chosen theme, theme colors, and theme background.
- Selecting **Server Default** restores the complete inherited server look.
- Every built-in theme receives a distinct compact motif while preserving readability.
- The server default theme key now points to the canonical built-in default.
- Regression tests prove 420 Lobby Neon renders lime/purple accents and all built-in motifs are distinct.

## Validation

- [ ] Focused theme tests pass.
- [ ] Changed Python modules compile.
- [ ] Full unit suite passes.
- [ ] Standalone checks and every repository audit pass.
- [ ] Branch is conflict-free with current `main`.
- [ ] Deployed Discord smoke confirms changing between at least two themes produces visibly different previews.

## Cleanup

- Temporary materialization files are removed before final validation.
- No runtime shim, monkey patch, duplicate renderer, or temporary migration path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
''',
    encoding="utf-8",
)
