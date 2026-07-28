from __future__ import annotations

"""Independent style state for compact member profile signatures.

Profile signatures use their own theme catalog and never mutate welcome-card
settings unless an administrator explicitly imports that look as a one-time copy.
"""

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .welcome_card_typography_engine import (
    COLOR_PRESETS,
    FONT_STYLES,
    parse_hex_color,
)

PROFILE_THEME_INHERIT = "server"
PROFILE_FONT_INHERIT = "server"
PROFILE_COLOR_INHERIT = "server"
PROFILE_BACKGROUND_INHERIT = "server"
PROFILE_LAYOUT_INHERIT = "server"
PROFILE_FRAME_INHERIT = "server"

PROFILE_COLOR_MODES = frozenset({"server", "auto", "profile", "theme", "custom"})
PROFILE_BACKGROUND_MODES = frozenset({"server", "theme", "profile", "custom"})
PROFILE_LAYOUTS = frozenset({"server", "classic", "minimal", "spotlight"})
PROFILE_AVATAR_FRAMES = frozenset({"server", "glow", "ring", "none"})


@dataclass(frozen=True)
class ProfileThemeSpec:
    key: str
    label: str
    description: str
    emoji: str


PROFILE_THEME_SPECS: dict[str, ProfileThemeSpec] = {
    "default": ProfileThemeSpec("default", "420 Lobby Neon", "Green leaf-and-smoke community card.", "🌿"),
    "forest": ProfileThemeSpec("forest", "420 Lobby Forest", "Deep green variation with brighter natural accents.", "🌲"),
    "purple": ProfileThemeSpec("purple", "Cyber Neon", "Purple neon smoke and futuristic rings.", "💜"),
    "galaxy": ProfileThemeSpec("galaxy", "Galaxy Neon", "Violet cosmic variation with soft particles.", "🌌"),
    "dark": ProfileThemeSpec("dark", "Premium Gold", "Black-and-gold premium member card.", "🏆"),
    "minimal": ProfileThemeSpec("minimal", "Community Glow", "Clean teal community treatment.", "🩵"),
    "sunset": ProfileThemeSpec("sunset", "Esports Ember", "Competitive red ember treatment.", "🔥"),
    "ocean": ProfileThemeSpec("ocean", "Minimal Glass", "Blue ice and glass treatment.", "🧊"),
    "steam_focus": ProfileThemeSpec("steam_focus", "Steam Command", "Steam-focused layout with a large real Steam mark.", "🎮"),
    "xbox_focus": ProfileThemeSpec("xbox_focus", "Xbox Arena", "Xbox-focused layout with green arena geometry.", "🟢"),
    "playstation_focus": ProfileThemeSpec("playstation_focus", "PlayStation Pulse", "PlayStation-focused layout with blue pulse geometry.", "🔷"),
    "epic_focus": ProfileThemeSpec("epic_focus", "Epic Vault", "Epic-focused black, white, and violet vault design.", "⬛"),
    "multi_platform": ProfileThemeSpec("multi_platform", "Multi-Platform Grid", "A balanced platform grid for players active everywhere.", "🕹️"),
}
PROFILE_THEME_KEYS = frozenset(PROFILE_THEME_SPECS)

DEFAULT_SERVER_PROFILE_STYLE: dict[str, str] = {
    "theme": "default",
    "font": "clean",
    "color_mode": "profile",
    "custom_primary": "",
    "custom_secondary": "",
    "custom_tertiary": "",
    "custom_highlight": "",
    "background_mode": "theme",
    "layout": "classic",
    "avatar_frame": "glow",
}

DEFAULT_MEMBER_PROFILE_STYLE: dict[str, str] = {
    "signature_theme": PROFILE_THEME_INHERIT,
    "signature_font": PROFILE_FONT_INHERIT,
    "signature_color_mode": PROFILE_COLOR_INHERIT,
    "signature_custom_primary": "",
    "signature_custom_secondary": "",
    "signature_custom_tertiary": "",
    "signature_custom_highlight": "",
    "signature_background_mode": PROFILE_BACKGROUND_INHERIT,
    "signature_layout": PROFILE_LAYOUT_INHERIT,
    "signature_avatar_frame": PROFILE_FRAME_INHERIT,
}

SERVER_STYLE_CONFIG_KEYS: dict[str, str] = {
    "theme": "profile_signature_theme",
    "font": "profile_signature_font",
    "color_mode": "profile_signature_color_mode",
    "custom_primary": "profile_signature_custom_primary",
    "custom_secondary": "profile_signature_custom_secondary",
    "custom_tertiary": "profile_signature_custom_tertiary",
    "custom_highlight": "profile_signature_custom_highlight",
    "background_mode": "profile_signature_background_mode",
    "layout": "profile_signature_layout",
    "avatar_frame": "profile_signature_avatar_frame",
}

PROFILE_CUSTOM_BACKGROUND_KEY = "profile_signature_custom_background_b64"
PROFILE_CUSTOM_FONT_KEY = "profile_signature_custom_font_b64"
PROFILE_CUSTOM_FONT_NAME_KEY = "profile_signature_custom_font_name"


def _value(source: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(source, Mapping):
            return source.get(key, default)
        found = getattr(source, key, default)
        return default if found is None else found
    except Exception:
        return default


def _clean_choice(value: Any, allowed: set[str] | frozenset[str], default: str) -> str:
    clean = str(value or "").strip().lower().replace("-", "_")
    return clean if clean in allowed else default


def _clean_hex(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parse_hex_color(raw)
    except Exception:
        return ""
    if not parsed:
        return ""
    return "#" + "".join(f"{part:02X}" for part in parsed)


def _mix_rgb(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(int(round(a + (b - a) * amount)) for a, b in zip(left, right))


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(value))):02X}" for value in rgb)


def derive_custom_colors(
    primary: Any,
    secondary: Any = "",
    tertiary: Any = "",
    highlight: Any = "",
) -> tuple[str, str, str, str]:
    """Return four valid accents while preserving every explicitly chosen value."""

    first = _clean_hex(primary)
    if not first:
        return "", "", "", ""
    first_rgb = parse_hex_color(first)
    second = _clean_hex(secondary)
    if not second:
        second = _hex(_mix_rgb(first_rgb, (255, 255, 255), 0.28))
    second_rgb = parse_hex_color(second)
    third = _clean_hex(tertiary)
    if not third:
        third = _hex(_mix_rgb(first_rgb, second_rgb, 0.50))
    third_rgb = parse_hex_color(third)
    fourth = _clean_hex(highlight)
    if not fourth:
        fourth = _hex(_mix_rgb(third_rgb, (255, 255, 255), 0.58))
    return first, second, third, fourth


def normalize_member_profile_style(value: Optional[Mapping[str, Any]]) -> dict[str, str]:
    raw = dict(value or {})
    themes = set(PROFILE_THEME_KEYS) | {PROFILE_THEME_INHERIT}
    fonts = set(FONT_STYLES) | {PROFILE_FONT_INHERIT}
    return {
        "signature_theme": _clean_choice(raw.get("signature_theme"), themes, PROFILE_THEME_INHERIT),
        "signature_font": _clean_choice(raw.get("signature_font"), fonts, PROFILE_FONT_INHERIT),
        "signature_color_mode": _clean_choice(
            raw.get("signature_color_mode"), PROFILE_COLOR_MODES, PROFILE_COLOR_INHERIT
        ),
        "signature_custom_primary": _clean_hex(raw.get("signature_custom_primary")),
        "signature_custom_secondary": _clean_hex(raw.get("signature_custom_secondary")),
        "signature_custom_tertiary": _clean_hex(raw.get("signature_custom_tertiary")),
        "signature_custom_highlight": _clean_hex(raw.get("signature_custom_highlight")),
        "signature_background_mode": _clean_choice(
            raw.get("signature_background_mode"), PROFILE_BACKGROUND_MODES, PROFILE_BACKGROUND_INHERIT
        ),
        "signature_layout": _clean_choice(raw.get("signature_layout"), PROFILE_LAYOUTS, PROFILE_LAYOUT_INHERIT),
        "signature_avatar_frame": _clean_choice(
            raw.get("signature_avatar_frame"), PROFILE_AVATAR_FRAMES, PROFILE_FRAME_INHERIT
        ),
    }


def server_profile_style(config: Any) -> dict[str, str]:
    fonts = set(FONT_STYLES)
    return {
        "theme": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["theme"]),
            PROFILE_THEME_KEYS,
            DEFAULT_SERVER_PROFILE_STYLE["theme"],
        ),
        "font": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["font"]), fonts, DEFAULT_SERVER_PROFILE_STYLE["font"]
        ),
        "color_mode": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["color_mode"]),
            PROFILE_COLOR_MODES - {PROFILE_COLOR_INHERIT},
            DEFAULT_SERVER_PROFILE_STYLE["color_mode"],
        ),
        "custom_primary": _clean_hex(_value(config, SERVER_STYLE_CONFIG_KEYS["custom_primary"])),
        "custom_secondary": _clean_hex(_value(config, SERVER_STYLE_CONFIG_KEYS["custom_secondary"])),
        "custom_tertiary": _clean_hex(_value(config, SERVER_STYLE_CONFIG_KEYS["custom_tertiary"])),
        "custom_highlight": _clean_hex(_value(config, SERVER_STYLE_CONFIG_KEYS["custom_highlight"])),
        "background_mode": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["background_mode"]),
            PROFILE_BACKGROUND_MODES - {PROFILE_BACKGROUND_INHERIT},
            DEFAULT_SERVER_PROFILE_STYLE["background_mode"],
        ),
        "layout": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["layout"]),
            PROFILE_LAYOUTS - {PROFILE_LAYOUT_INHERIT},
            DEFAULT_SERVER_PROFILE_STYLE["layout"],
        ),
        "avatar_frame": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["avatar_frame"]),
            PROFILE_AVATAR_FRAMES - {PROFILE_FRAME_INHERIT},
            DEFAULT_SERVER_PROFILE_STYLE["avatar_frame"],
        ),
    }


def effective_profile_style(preferences: Mapping[str, Any], config: Any) -> dict[str, Any]:
    member = normalize_member_profile_style(preferences)
    server = server_profile_style(config)

    def resolved(member_key: str, server_key: str, inherit: str) -> str:
        value = member[member_key]
        return server[server_key] if value == inherit else value

    color_mode = resolved("signature_color_mode", "color_mode", PROFILE_COLOR_INHERIT)
    member_owns_custom = member["signature_color_mode"] == "custom"
    custom_source = member if member_owns_custom else server
    prefix = "signature_" if member_owns_custom else ""
    primary, secondary, tertiary, highlight = derive_custom_colors(
        custom_source.get(f"{prefix}custom_primary"),
        custom_source.get(f"{prefix}custom_secondary"),
        custom_source.get(f"{prefix}custom_tertiary"),
        custom_source.get(f"{prefix}custom_highlight"),
    )
    if color_mode == "custom" and not primary:
        color_mode = server["color_mode"] if server["color_mode"] != "custom" else "profile"
        primary, secondary, tertiary, highlight = derive_custom_colors(
            server["custom_primary"],
            server["custom_secondary"],
            server["custom_tertiary"],
            server["custom_highlight"],
        )

    return {
        "theme": resolved("signature_theme", "theme", PROFILE_THEME_INHERIT),
        "font": resolved("signature_font", "font", PROFILE_FONT_INHERIT),
        "color_mode": color_mode,
        "custom_primary": primary,
        "custom_secondary": secondary,
        "custom_tertiary": tertiary,
        "custom_highlight": highlight,
        "background_mode": resolved(
            "signature_background_mode", "background_mode", PROFILE_BACKGROUND_INHERIT
        ),
        "layout": resolved("signature_layout", "layout", PROFILE_LAYOUT_INHERIT),
        "avatar_frame": resolved(
            "signature_avatar_frame", "avatar_frame", PROFILE_FRAME_INHERIT
        ),
        "custom_background": decode_profile_asset(_value(config, PROFILE_CUSTOM_BACKGROUND_KEY, "")),
        "custom_font": decode_profile_asset(_value(config, PROFILE_CUSTOM_FONT_KEY, "")),
        "custom_font_name": str(_value(config, PROFILE_CUSTOM_FONT_NAME_KEY, "") or "")[:120],
    }


def server_style_updates(style: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_SERVER_PROFILE_STYLE)
    normalized.update(server_profile_style({SERVER_STYLE_CONFIG_KEYS[k]: style.get(k) for k in SERVER_STYLE_CONFIG_KEYS}))
    return {SERVER_STYLE_CONFIG_KEYS[key]: normalized[key] for key in SERVER_STYLE_CONFIG_KEYS}


def theme_style_updates(theme_key: str, *, member: bool) -> dict[str, str]:
    clean = str(theme_key or "").strip().lower().replace("-", "_")
    if member and clean == PROFILE_THEME_INHERIT:
        return {
            "signature_theme": PROFILE_THEME_INHERIT,
            "signature_color_mode": PROFILE_COLOR_INHERIT,
            "signature_background_mode": PROFILE_BACKGROUND_INHERIT,
        }
    if clean not in PROFILE_THEME_KEYS:
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


def palette_style_updates(preset_key: str, *, member: bool) -> dict[str, str]:
    preset = COLOR_PRESETS.get(str(preset_key or "").strip().lower())
    if preset is None:
        raise ValueError("That color palette is no longer available.")
    primary, secondary, tertiary, highlight = derive_custom_colors(preset.primary, preset.secondary)
    prefix = "signature_" if member else "profile_signature_"
    return {
        f"{prefix}color_mode": "custom",
        f"{prefix}custom_primary": primary,
        f"{prefix}custom_secondary": secondary,
        f"{prefix}custom_tertiary": tertiary,
        f"{prefix}custom_highlight": highlight,
    }


def decode_profile_asset(value: Any) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        return b""
    try:
        return base64.b64decode(raw.encode("ascii"), validate=True)
    except Exception:
        return b""


def encode_profile_asset(data: bytes) -> str:
    return base64.b64encode(bytes(data or b"")).decode("ascii") if data else ""


__all__ = [
    "DEFAULT_MEMBER_PROFILE_STYLE",
    "DEFAULT_SERVER_PROFILE_STYLE",
    "PROFILE_AVATAR_FRAMES",
    "PROFILE_BACKGROUND_MODES",
    "PROFILE_COLOR_MODES",
    "PROFILE_CUSTOM_BACKGROUND_KEY",
    "PROFILE_CUSTOM_FONT_KEY",
    "PROFILE_CUSTOM_FONT_NAME_KEY",
    "PROFILE_LAYOUTS",
    "PROFILE_THEME_KEYS",
    "PROFILE_THEME_SPECS",
    "ProfileThemeSpec",
    "SERVER_STYLE_CONFIG_KEYS",
    "decode_profile_asset",
    "derive_custom_colors",
    "effective_profile_style",
    "encode_profile_asset",
    "normalize_member_profile_style",
    "palette_style_updates",
    "server_profile_style",
    "server_style_updates",
    "theme_style_updates",
]
