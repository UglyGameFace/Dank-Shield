from __future__ import annotations

"""Async delivery helpers for personalized welcome cards."""

import asyncio
import base64
import time
from io import BytesIO
from typing import Any, Mapping, Optional

import discord
from PIL import Image, ImageOps

from .welcome_card_studio_renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    DEFAULT_COLOR_MODE,
    DEFAULT_FONT_STYLE_KEY,
    DEFAULT_THEME_KEY,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_theme_key,
    render_welcome_card,
    validate_custom_background,
)

MAX_STORED_BACKGROUND_BYTES = 450 * 1024
_PROFILE_VISUAL_CACHE_TTL_SECONDS = 6 * 60 * 60
_PROFILE_VISUAL_CACHE_MAX = 512
_PROFILE_VISUAL_CACHE: dict[int, tuple[float, Optional[bytes], Optional[tuple[int, int, int]]]] = {}


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    raw = _cfg_value(cfg, key, None)
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def welcome_cards_enabled(cfg: Any) -> bool:
    return _cfg_bool(cfg, "welcome_card_enabled", False)


def configured_theme_key(cfg: Any) -> str:
    return normalize_theme_key(_cfg_value(cfg, "welcome_card_theme", DEFAULT_THEME_KEY))


def configured_font_style_key(cfg: Any) -> str:
    return normalize_font_style_key(_cfg_value(cfg, "welcome_card_font_style", DEFAULT_FONT_STYLE_KEY))


def configured_color_mode(cfg: Any) -> str:
    return normalize_color_mode(_cfg_value(cfg, "welcome_card_color_mode", DEFAULT_COLOR_MODE))


def configured_custom_colors(cfg: Any) -> tuple[str, str]:
    primary = str(_cfg_value(cfg, "welcome_card_custom_primary", "") or "").strip()
    secondary = str(_cfg_value(cfg, "welcome_card_custom_secondary", "") or "").strip()
    return primary, secondary


def decode_custom_background(cfg: Any) -> Optional[bytes]:
    raw = str(_cfg_value(cfg, "welcome_card_background_b64", "") or "").strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
        validate_custom_background(data)
        return data
    except Exception:
        return None


def normalize_custom_background_for_storage(data: bytes) -> tuple[bytes, str]:
    """Validate, crop, and compress a custom upload for durable per-guild storage."""

    validate_custom_background(data)
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.fit(
            source.convert("RGB"),
            (CARD_WIDTH, CARD_HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    for quality in (86, 80, 74, 68, 60):
        output = BytesIO()
        image.save(output, format="WEBP", quality=quality, method=6)
        encoded = output.getvalue()
        if len(encoded) <= MAX_STORED_BACKGROUND_BYTES:
            return encoded, "image/webp"
    raise ValueError("Custom background is too detailed to store safely. Try a simpler image or a smaller file.")


def encode_custom_background(data: bytes) -> str:
    if len(data) > MAX_STORED_BACKGROUND_BYTES:
        raise ValueError("Normalized custom background exceeds the storage limit.")
    return base64.b64encode(data).decode("ascii")


async def _avatar_bytes(member: discord.Member) -> bytes:
    try:
        try:
            asset = member.display_avatar.replace(size=512, format="png")
        except TypeError:
            asset = member.display_avatar.with_size(512).with_format("png")
        return await asset.read()
    except Exception:
        return b""


def _cached_profile_visuals(user_id: int) -> Optional[tuple[Optional[bytes], Optional[tuple[int, int, int]]]]:
    cached = _PROFILE_VISUAL_CACHE.get(int(user_id))
    if not cached:
        return None
    expires_at, banner, accent = cached
    if expires_at <= time.monotonic():
        _PROFILE_VISUAL_CACHE.pop(int(user_id), None)
        return None
    return banner, accent


def _store_profile_visuals(
    user_id: int,
    banner: Optional[bytes],
    accent: Optional[tuple[int, int, int]],
) -> None:
    if len(_PROFILE_VISUAL_CACHE) >= _PROFILE_VISUAL_CACHE_MAX:
        oldest_key = min(_PROFILE_VISUAL_CACHE, key=lambda key: _PROFILE_VISUAL_CACHE[key][0])
        _PROFILE_VISUAL_CACHE.pop(oldest_key, None)
    _PROFILE_VISUAL_CACHE[int(user_id)] = (
        time.monotonic() + _PROFILE_VISUAL_CACHE_TTL_SECONDS,
        banner,
        accent,
    )


def _discord_client_for(member: Any) -> Any:
    try:
        state = getattr(member, "_state", None)
        getter = getattr(state, "_get_client", None)
        if callable(getter):
            return getter()
    except Exception:
        pass
    try:
        state = getattr(getattr(member, "guild", None), "_state", None)
        getter = getattr(state, "_get_client", None)
        if callable(getter):
            return getter()
    except Exception:
        pass
    return None


def _accent_rgb(user: Any) -> Optional[tuple[int, int, int]]:
    try:
        accent = getattr(user, "accent_color", None) or getattr(user, "accent_colour", None)
        if accent is None:
            return None
        to_rgb = getattr(accent, "to_rgb", None)
        if callable(to_rgb):
            rgb = to_rgb()
            if isinstance(rgb, tuple) and len(rgb) >= 3:
                return int(rgb[0]), int(rgb[1]), int(rgb[2])
        value = int(getattr(accent, "value", accent))
        return ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    except Exception:
        return None


async def _profile_visuals(member: discord.Member) -> tuple[Optional[bytes], Optional[tuple[int, int, int]]]:
    user_id = int(getattr(member, "id", 0) or 0)
    if user_id <= 0:
        return None, None
    cached = _cached_profile_visuals(user_id)
    if cached is not None:
        return cached
    client = _discord_client_for(member)
    fetch_user = getattr(client, "fetch_user", None)
    if not callable(fetch_user):
        _store_profile_visuals(user_id, None, None)
        return None, None
    banner_bytes: Optional[bytes] = None
    accent: Optional[tuple[int, int, int]] = None
    try:
        user = await asyncio.wait_for(fetch_user(user_id), timeout=3.0)
        accent = _accent_rgb(user)
        banner = getattr(user, "banner", None)
        if banner is not None:
            try:
                asset = banner.replace(size=512, format="png")
            except TypeError:
                asset = banner.with_size(512).with_format("png")
            banner_bytes = await asyncio.wait_for(asset.read(), timeout=3.0)
    except Exception:
        banner_bytes = None
        accent = None
    _store_profile_visuals(user_id, banner_bytes, accent)
    return banner_bytes, accent


async def render_member_welcome_card(
    member: discord.Member,
    cfg: Any,
    *,
    theme_override: Optional[str] = None,
) -> bytes:
    theme_key = normalize_theme_key(theme_override or configured_theme_key(cfg))
    font_style_key = configured_font_style_key(cfg)
    color_mode = configured_color_mode(cfg)
    custom_primary, custom_secondary = configured_custom_colors(cfg)
    avatar_task = asyncio.create_task(_avatar_bytes(member))
    profile_task: Optional[asyncio.Task] = None
    if color_mode in {"auto", "profile"}:
        profile_task = asyncio.create_task(_profile_visuals(member))
    avatar = await avatar_task
    profile_banner: Optional[bytes] = None
    profile_accent: Optional[tuple[int, int, int]] = None
    if profile_task is not None:
        profile_banner, profile_accent = await profile_task
    custom_background = None if theme_override is not None else decode_custom_background(cfg)
    return await asyncio.to_thread(
        render_welcome_card,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(member.guild, "name", None) or "Your Server",
        member_count=int(getattr(member.guild, "member_count", 0) or 0),
        theme_key=theme_key,
        custom_background_bytes=custom_background,
        font_style_key=font_style_key,
        color_mode=color_mode,
        custom_primary=custom_primary,
        custom_secondary=custom_secondary,
        profile_banner_bytes=profile_banner,
        profile_accent=profile_accent,
    )


async def welcome_card_file(
    member: discord.Member,
    cfg: Any,
    *,
    theme_override: Optional[str] = None,
) -> discord.File:
    rendered = await render_member_welcome_card(member, cfg, theme_override=theme_override)
    safe_id = int(getattr(member, "id", 0) or 0)
    return discord.File(BytesIO(rendered), filename=f"welcome-{safe_id}.png")


__all__ = [
    "MAX_STORED_BACKGROUND_BYTES",
    "configured_color_mode",
    "configured_custom_colors",
    "configured_font_style_key",
    "configured_theme_key",
    "decode_custom_background",
    "encode_custom_background",
    "normalize_custom_background_for_storage",
    "render_member_welcome_card",
    "welcome_card_file",
    "welcome_cards_enabled",
]
