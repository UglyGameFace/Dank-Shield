from __future__ import annotations

"""Independent style state for compact member profile signatures.

Profile signatures may share the same safe visual catalog as join cards, but
these keys never read or mutate welcome-card settings unless an administrator
explicitly imports that look as a one-time copy.
"""

import base64
from typing import Any, Mapping, Optional

from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
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

DEFAULT_SERVER_PROFILE_STYLE: dict[str, str] = {
    "theme": "default",
    "font": "clean",
    "color_mode": "profile",
    "custom_primary": "",
    "custom_secondary": "",
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


def normalize_member_profile_style(value: Optional[Mapping[str, Any]]) -> dict[str, str]:
    raw = dict(value or {})
    themes = set(BUILTIN_THEMES) | {PROFILE_THEME_INHERIT}
    fonts = set(FONT_STYLES) | {PROFILE_FONT_INHERIT}
    return {
        "signature_theme": _clean_choice(raw.get("signature_theme"), themes, PROFILE_THEME_INHERIT),
        "signature_font": _clean_choice(raw.get("signature_font"), fonts, PROFILE_FONT_INHERIT),
        "signature_color_mode": _clean_choice(
            raw.get("signature_color_mode"), PROFILE_COLOR_MODES, PROFILE_COLOR_INHERIT
        ),
        "signature_custom_primary": _clean_hex(raw.get("signature_custom_primary")),
        "signature_custom_secondary": _clean_hex(raw.get("signature_custom_secondary")),
        "signature_background_mode": _clean_choice(
            raw.get("signature_background_mode"), PROFILE_BACKGROUND_MODES, PROFILE_BACKGROUND_INHERIT
        ),
        "signature_layout": _clean_choice(raw.get("signature_layout"), PROFILE_LAYOUTS, PROFILE_LAYOUT_INHERIT),
        "signature_avatar_frame": _clean_choice(
            raw.get("signature_avatar_frame"), PROFILE_AVATAR_FRAMES, PROFILE_FRAME_INHERIT
        ),
    }


def server_profile_style(config: Any) -> dict[str, str]:
    themes = set(BUILTIN_THEMES)
    fonts = set(FONT_STYLES)
    return {
        "theme": _clean_choice(
            _value(config, SERVER_STYLE_CONFIG_KEYS["theme"]), themes, DEFAULT_SERVER_PROFILE_STYLE["theme"]
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
    primary = member["signature_custom_primary"] if color_mode == "custom" else server["custom_primary"]
    secondary = member["signature_custom_secondary"] if color_mode == "custom" else server["custom_secondary"]
    if color_mode == "custom" and (not primary or not secondary):
        color_mode = server["color_mode"] if server["color_mode"] != "custom" else "profile"
        primary = server["custom_primary"]
        secondary = server["custom_secondary"]

    return {
        "theme": resolved("signature_theme", "theme", PROFILE_THEME_INHERIT),
        "font": resolved("signature_font", "font", PROFILE_FONT_INHERIT),
        "color_mode": color_mode,
        "custom_primary": primary,
        "custom_secondary": secondary,
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


def palette_style_updates(preset_key: str, *, member: bool) -> dict[str, str]:
    preset = COLOR_PRESETS.get(str(preset_key or "").strip().lower())
    if preset is None:
        raise ValueError("That color palette is no longer available.")
    prefix = "signature_" if member else "profile_signature_"
    return {
        f"{prefix}color_mode": "custom",
        f"{prefix}custom_primary": preset.primary,
        f"{prefix}custom_secondary": preset.secondary,
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
    "SERVER_STYLE_CONFIG_KEYS",
    "decode_profile_asset",
    "effective_profile_style",
    "encode_profile_asset",
    "normalize_member_profile_style",
    "palette_style_updates",
    "server_profile_style",
    "server_style_updates",
]
