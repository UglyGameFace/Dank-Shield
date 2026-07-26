from __future__ import annotations

"""Validated member-owned appearance settings for compact profile signatures.

Profile signatures and join-only welcome cards intentionally share safe visual
primitives (themes, palettes, fonts) while keeping separate storage and UI.
"""

import base64
from io import BytesIO
from typing import Any, Mapping, Optional

from PIL import Image, ImageFont, ImageOps

from .welcome_card_font_assets import MAX_STORED_FONT_BYTES
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    DEFAULT_FONT_STYLE_KEY,
    DEFAULT_THEME_KEY,
    FONT_STYLES,
    normalize_font_style_key,
    normalize_hex_color,
    normalize_theme_key,
)

PROFILE_SIGNATURE_WIDTH = 1080
PROFILE_SIGNATURE_HEIGHT = 220
MAX_PROFILE_BACKGROUND_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_STORED_PROFILE_BACKGROUND_BYTES = 320 * 1024

PROFILE_LAYOUTS: dict[str, str] = {
    "classic": "Classic",
    "minimal": "Minimal",
    "showcase": "Showcase",
}
PROFILE_AVATAR_SHAPES: dict[str, str] = {
    "circle": "Circle",
    "rounded": "Rounded square",
}
PROFILE_COLOR_MODES: dict[str, str] = {
    "auto": "Match my profile",
    "theme": "Theme colors",
    "custom": "Custom colors",
}

DEFAULT_PROFILE_APPEARANCE: dict[str, Any] = {
    "theme_key": DEFAULT_THEME_KEY,
    "font_style": DEFAULT_FONT_STYLE_KEY,
    "color_mode": "auto",
    "custom_primary": "",
    "custom_secondary": "",
    "layout": "classic",
    "avatar_shape": "circle",
    "show_server_name": True,
    "background_b64": "",
    "background_type": "",
    "background_name": "",
    "custom_font_b64": "",
    "custom_font_name": "",
}


def _safe_name(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    return (text or fallback)[:100]


def _safe_b64(value: Any, *, limit: int) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception:
        return ""
    if not decoded or len(decoded) > int(limit):
        return ""
    return raw


def normalize_profile_appearance(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    out = dict(DEFAULT_PROFILE_APPEARANCE)

    out["theme_key"] = normalize_theme_key(raw.get("theme_key", DEFAULT_THEME_KEY))

    font_key = normalize_font_style_key(raw.get("font_style", DEFAULT_FONT_STYLE_KEY))
    custom_font_b64 = _safe_b64(raw.get("custom_font_b64"), limit=MAX_STORED_FONT_BYTES)
    custom_font_name = _safe_name(raw.get("custom_font_name"), "Uploaded Font") if custom_font_b64 else ""
    if font_key == CUSTOM_FONT_STYLE_KEY and not custom_font_b64:
        font_key = DEFAULT_FONT_STYLE_KEY
    out["font_style"] = font_key
    out["custom_font_b64"] = custom_font_b64
    out["custom_font_name"] = custom_font_name

    color_mode = str(raw.get("color_mode") or "auto").strip().lower()
    out["color_mode"] = color_mode if color_mode in PROFILE_COLOR_MODES else "auto"
    try:
        out["custom_primary"] = normalize_hex_color(raw.get("custom_primary"))
    except Exception:
        out["custom_primary"] = ""
    try:
        out["custom_secondary"] = normalize_hex_color(raw.get("custom_secondary"))
    except Exception:
        out["custom_secondary"] = ""
    if out["color_mode"] == "custom" and not (
        out["custom_primary"] and out["custom_secondary"]
    ):
        out["color_mode"] = "auto"

    layout = str(raw.get("layout") or "classic").strip().lower()
    out["layout"] = layout if layout in PROFILE_LAYOUTS else "classic"
    shape = str(raw.get("avatar_shape") or "circle").strip().lower()
    out["avatar_shape"] = shape if shape in PROFILE_AVATAR_SHAPES else "circle"
    out["show_server_name"] = bool(raw.get("show_server_name", True))

    background_b64 = _safe_b64(
        raw.get("background_b64"),
        limit=MAX_STORED_PROFILE_BACKGROUND_BYTES,
    )
    out["background_b64"] = background_b64
    out["background_type"] = "image/webp" if background_b64 else ""
    out["background_name"] = (
        _safe_name(raw.get("background_name"), "Profile background") if background_b64 else ""
    )
    return out


def apply_profile_appearance_updates(
    current: Optional[Mapping[str, Any]],
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    merged = normalize_profile_appearance(current)
    for key in DEFAULT_PROFILE_APPEARANCE:
        if key in updates:
            merged[key] = updates.get(key)
    return normalize_profile_appearance(merged)


def reset_profile_appearance() -> dict[str, Any]:
    return dict(DEFAULT_PROFILE_APPEARANCE)


def decode_profile_background(appearance: Any) -> Optional[bytes]:
    normalized = normalize_profile_appearance(appearance if isinstance(appearance, Mapping) else {})
    raw = normalized.get("background_b64")
    if not raw:
        return None
    try:
        data = base64.b64decode(str(raw), validate=True)
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return data
    except Exception:
        return None


def normalize_profile_background_for_storage(data: bytes) -> tuple[bytes, str]:
    if not data:
        raise ValueError("The uploaded background is empty.")
    if len(data) > MAX_PROFILE_BACKGROUND_UPLOAD_BYTES:
        raise ValueError("Profile backgrounds must be 8 MB or smaller.")
    try:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.fit(
                source.convert("RGB"),
                (PROFILE_SIGNATURE_WIDTH, PROFILE_SIGNATURE_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except Exception as exc:
        raise ValueError("Upload a valid PNG, JPG, or WEBP image.") from exc

    for quality in (84, 78, 72, 66, 58):
        output = BytesIO()
        image.save(output, format="WEBP", quality=quality, method=6)
        encoded = output.getvalue()
        if len(encoded) <= MAX_STORED_PROFILE_BACKGROUND_BYTES:
            return encoded, "image/webp"
    raise ValueError("That image is too detailed to store safely. Try a simpler background.")


def encode_profile_background(data: bytes) -> str:
    if len(data) > MAX_STORED_PROFILE_BACKGROUND_BYTES:
        raise ValueError("Normalized profile background exceeds the storage limit.")
    return base64.b64encode(data).decode("ascii")


def decode_profile_custom_font(appearance: Any) -> tuple[Optional[bytes], str]:
    normalized = normalize_profile_appearance(appearance if isinstance(appearance, Mapping) else {})
    raw = str(normalized.get("custom_font_b64") or "").strip()
    name = _safe_name(normalized.get("custom_font_name"), "Uploaded Font")
    if not raw:
        return None, name
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:
        return None, name
    if not data or len(data) > MAX_STORED_FONT_BYTES:
        return None, name
    try:
        font = ImageFont.truetype(BytesIO(data), 48)
        box = font.getbbox("Profile UglyGameFace 123")
        if not box or box[2] <= box[0]:
            return None, name
    except Exception:
        return None, name
    return data, name


def appearance_summary(appearance: Any) -> dict[str, str]:
    normalized = normalize_profile_appearance(appearance if isinstance(appearance, Mapping) else {})
    font_key = str(normalized["font_style"])
    custom_font, custom_name = decode_profile_custom_font(normalized)
    font_label = (
        custom_name
        if font_key == CUSTOM_FONT_STYLE_KEY and custom_font
        else FONT_STYLES[font_key].label
    )
    theme = BUILTIN_THEMES[str(normalized["theme_key"])]
    color_label = PROFILE_COLOR_MODES[str(normalized["color_mode"])]
    if normalized["color_mode"] == "custom":
        preset = next(
            (
                item.label
                for item in COLOR_PRESETS.values()
                if item.primary.upper() == str(normalized["custom_primary"]).upper()
                and item.secondary.upper() == str(normalized["custom_secondary"]).upper()
            ),
            "Custom colors",
        )
        color_label = preset
    return {
        "font": font_label,
        "theme": theme.label,
        "colors": color_label,
        "layout": PROFILE_LAYOUTS[str(normalized["layout"])],
        "avatar": PROFILE_AVATAR_SHAPES[str(normalized["avatar_shape"])],
        "background": str(normalized.get("background_name") or "Built-in theme"),
        "server_name": "Shown" if normalized.get("show_server_name", True) else "Hidden",
    }


__all__ = [
    "DEFAULT_PROFILE_APPEARANCE",
    "MAX_PROFILE_BACKGROUND_UPLOAD_BYTES",
    "MAX_STORED_PROFILE_BACKGROUND_BYTES",
    "PROFILE_AVATAR_SHAPES",
    "PROFILE_COLOR_MODES",
    "PROFILE_LAYOUTS",
    "PROFILE_SIGNATURE_HEIGHT",
    "PROFILE_SIGNATURE_WIDTH",
    "appearance_summary",
    "apply_profile_appearance_updates",
    "decode_profile_background",
    "decode_profile_custom_font",
    "encode_profile_background",
    "normalize_profile_appearance",
    "normalize_profile_background_for_storage",
    "reset_profile_appearance",
]
