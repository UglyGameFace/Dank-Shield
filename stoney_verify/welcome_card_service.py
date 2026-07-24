from __future__ import annotations

"""Async delivery helpers for personalized welcome cards."""

import asyncio
import base64
from io import BytesIO
from typing import Any, Mapping, Optional

import discord
from PIL import Image, ImageOps

from .welcome_card_renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    DEFAULT_THEME_KEY,
    normalize_theme_key,
    render_welcome_card,
    validate_custom_background,
)

MAX_STORED_BACKGROUND_BYTES = 450 * 1024


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
    # Public installs keep their existing embed behavior until an admin
    # explicitly picks a theme, uploads a background, or enables cards.
    return _cfg_bool(cfg, "welcome_card_enabled", False)


def configured_theme_key(cfg: Any) -> str:
    return normalize_theme_key(_cfg_value(cfg, "welcome_card_theme", DEFAULT_THEME_KEY))


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

    # Prefer WEBP because it keeps a 1200×400 background small enough for JSONB
    # configuration storage without depending on expiring Discord CDN URLs.
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
        # The renderer has a native neutral avatar fallback. A temporary CDN
        # failure must not drop the entire welcome card.
        return b""


async def render_member_welcome_card(
    member: discord.Member,
    cfg: Any,
    *,
    theme_override: Optional[str] = None,
) -> bytes:
    avatar = await _avatar_bytes(member)
    theme_key = normalize_theme_key(theme_override or configured_theme_key(cfg))
    # A one-off built-in theme preview must not be hidden by a saved
    # custom background. Normal live renders still use the custom background.
    custom_background = (
        None
        if theme_override is not None
        else decode_custom_background(cfg)
    )
    return await asyncio.to_thread(
        render_welcome_card,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(member.guild, "name", None) or "Your Server",
        member_count=int(getattr(member.guild, "member_count", 0) or 0),
        theme_key=theme_key,
        custom_background_bytes=custom_background,
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
    "configured_theme_key",
    "decode_custom_background",
    "encode_custom_background",
    "normalize_custom_background_for_storage",
    "render_member_welcome_card",
    "welcome_card_file",
    "welcome_cards_enabled",
]
