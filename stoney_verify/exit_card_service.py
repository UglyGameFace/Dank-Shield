from __future__ import annotations

"""Configuration and rendering service for canonical member exit cards."""

import asyncio
import base64
import hashlib
from io import BytesIO
from typing import Any, Mapping, Optional

import discord

from .exit_card_renderer import render_exit_card
from .welcome_card_service import (
    WELCOME_CARD_SHUFFLE_MODES,
    _avatar_bytes,
    _profile_visuals,
    configured_color_mode,
    configured_custom_colors,
    configured_custom_font,
    configured_font_style_key,
    configured_shuffle_mode,
    configured_theme_key,
    decode_custom_background,
)
from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    COLOR_PRESETS,
    CUSTOM_FONT_STYLE_KEY,
    DEFAULT_FONT_STYLE_KEY,
    FONT_STYLES,
    normalize_color_mode,
    normalize_font_style_key,
    normalize_theme_key,
    validate_custom_background,
)

_LEGACY_EXIT_CHANNEL_KEYS = (
    "goodbye_channel_id",
    "welcome_exit_channel_id",
    "welcome_exit_log_channel_id",
    "leave_channel_id",
    "join_leave_log_channel_id",
    "join_leave_channel_id",
    "member_join_leave_log_channel_id",
    "member_lifecycle_log_channel_id",
    "member_log_channel_id",
    "member_logs_channel_id",
    "leave_log_channel_id",
    "join_exit_log_channel_id",
    "joinleave_channel_id",
    "welcome_leave_channel_id",
)


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
            if isinstance(nested, Mapping) and key in nested:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and key in nested:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _positive_id(value: Any) -> bool:
    try:
        return int(str(value or "").strip().strip("<#@!&>")) > 0
    except Exception:
        return False


def exit_cards_enabled(cfg: Any) -> bool:
    explicit = _cfg_value(cfg, "exit_card_enabled", None)
    if explicit is not None:
        return _cfg_bool_value(explicit, False)

    saw_legacy_toggle = False
    for key in ("welcome_leave_enabled", "goodbye_enabled", "leave_message_enabled"):
        value = _cfg_value(cfg, key, None)
        if value is None:
            continue
        saw_legacy_toggle = True
        if _cfg_bool_value(value, False):
            return True
    if saw_legacy_toggle:
        return False

    # The retired v4 sender historically posted whenever an explicit leave-log
    # route existed, even on very old guilds that never stored a boolean toggle.
    # Preserve that behavior only until the owner saves an explicit Exit setting.
    return any(_positive_id(_cfg_value(cfg, key, None)) for key in _LEGACY_EXIT_CHANNEL_KEYS)


def configured_exit_theme_key(cfg: Any) -> str:
    raw = _cfg_value(cfg, "exit_card_theme", None)
    return normalize_theme_key(raw if raw not in (None, "") else configured_theme_key(cfg))


def configured_exit_font_style_key(cfg: Any) -> str:
    raw = _cfg_value(cfg, "exit_card_font_style", None)
    key = normalize_font_style_key(
        raw if raw not in (None, "") else configured_font_style_key(cfg)
    )
    if key == CUSTOM_FONT_STYLE_KEY:
        custom_font, _name = configured_custom_font(cfg)
        if not custom_font:
            return DEFAULT_FONT_STYLE_KEY
    return key


def configured_exit_color_mode(cfg: Any) -> str:
    raw = _cfg_value(cfg, "exit_card_color_mode", None)
    return normalize_color_mode(
        raw if raw not in (None, "") else configured_color_mode(cfg)
    )


def configured_exit_custom_colors(cfg: Any) -> tuple[str, str]:
    welcome_primary, welcome_secondary = configured_custom_colors(cfg)
    primary = _cfg_value(cfg, "exit_card_custom_primary", None)
    secondary = _cfg_value(cfg, "exit_card_custom_secondary", None)
    return (
        str(welcome_primary if primary is None else primary or "").strip(),
        str(welcome_secondary if secondary is None else secondary or "").strip(),
    )


def configured_exit_shuffle_mode(cfg: Any) -> str:
    raw = _cfg_value(cfg, "exit_card_shuffle_mode", None)
    mode = str(
        configured_shuffle_mode(cfg) if raw in (None, "") else raw
    ).strip().lower()
    return mode if mode in WELCOME_CARD_SHUFFLE_MODES else "off"


def decode_exit_custom_background(cfg: Any) -> Optional[bytes]:
    mode = str(_cfg_value(cfg, "exit_card_background_mode", "") or "").strip().lower()
    raw = str(_cfg_value(cfg, "exit_card_background_b64", "") or "").strip()
    if raw:
        try:
            data = base64.b64decode(raw, validate=True)
            validate_custom_background(data)
            return data
        except Exception:
            return None
    if mode == "builtin":
        return None
    # Existing guilds inherit their Welcome Studio artwork until an owner makes
    # an explicit Exit Studio background choice.
    return decode_custom_background(cfg)


def _stable_choice(
    values: list[str],
    *,
    guild_id: int,
    user_id: int,
    component: str,
) -> str:
    if not values:
        raise ValueError("Exit-card shuffle requires at least one choice.")
    material = (
        f"dank-exit-shuffle-v1:{int(guild_id)}:{int(user_id)}:{component}"
    ).encode("utf-8", "ignore")
    digest = hashlib.sha256(material).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


def _resolve_effective_exit_style(
    *,
    guild_id: int,
    user_id: int,
    cfg: Any,
    custom_font_present: bool,
) -> tuple[str, Optional[bytes], str, str, str, str]:
    theme_key = configured_exit_theme_key(cfg)
    background = decode_exit_custom_background(cfg)
    font_key = configured_exit_font_style_key(cfg)
    color_mode = configured_exit_color_mode(cfg)
    primary, secondary = configured_exit_custom_colors(cfg)
    shuffle = configured_exit_shuffle_mode(cfg)

    if shuffle in {"fonts", "fonts_themes", "everything"}:
        choices = list(FONT_STYLES)
        if custom_font_present:
            choices.append(CUSTOM_FONT_STYLE_KEY)
        font_key = _stable_choice(
            choices,
            guild_id=guild_id,
            user_id=user_id,
            component=f"{shuffle}:font",
        )

    if shuffle in {"themes", "fonts_themes", "everything"}:
        theme_key = _stable_choice(
            list(BUILTIN_THEMES),
            guild_id=guild_id,
            user_id=user_id,
            component=f"{shuffle}:theme",
        )
        background = None

    if shuffle == "everything":
        palette = COLOR_PRESETS[
            _stable_choice(
                list(COLOR_PRESETS),
                guild_id=guild_id,
                user_id=user_id,
                component="everything:palette",
            )
        ]
        color_mode = "custom"
        primary, secondary = palette.primary, palette.secondary

    return theme_key, background, font_key, color_mode, primary, secondary


async def render_member_exit_card(
    member: discord.Member,
    cfg: Any,
    *,
    theme_override: Optional[str] = None,
) -> bytes:
    custom_font, _custom_name = configured_custom_font(cfg)

    if theme_override is not None:
        theme_key = normalize_theme_key(theme_override)
        background = None
        font_key = configured_exit_font_style_key(cfg)
        color_mode = configured_exit_color_mode(cfg)
        primary, secondary = configured_exit_custom_colors(cfg)
    else:
        (
            theme_key,
            background,
            font_key,
            color_mode,
            primary,
            secondary,
        ) = _resolve_effective_exit_style(
            guild_id=int(getattr(member.guild, "id", 0) or 0),
            user_id=int(getattr(member, "id", 0) or 0),
            cfg=cfg,
            custom_font_present=bool(custom_font),
        )

    avatar_task = asyncio.create_task(_avatar_bytes(member))
    profile_task: Optional[asyncio.Task] = None
    if color_mode in {"auto", "profile"}:
        profile_task = asyncio.create_task(_profile_visuals(member))

    avatar = await avatar_task
    banner: Optional[bytes] = None
    accent: Optional[tuple[int, int, int]] = None
    if profile_task is not None:
        banner, accent = await profile_task

    return await asyncio.to_thread(
        render_exit_card,
        avatar_bytes=avatar,
        display_name=getattr(member, "display_name", None) or str(member),
        server_name=getattr(member.guild, "name", None) or "Your Server",
        member_count=int(getattr(member.guild, "member_count", 0) or 0),
        theme_key=theme_key,
        custom_background_bytes=background,
        font_style_key=font_key,
        custom_font_bytes=custom_font,
        color_mode=color_mode,
        custom_primary=primary,
        custom_secondary=secondary,
        profile_banner_bytes=banner,
        profile_accent=accent,
    )


async def exit_card_file(
    member: discord.Member,
    cfg: Any,
    *,
    theme_override: Optional[str] = None,
) -> discord.File:
    rendered = await render_member_exit_card(
        member,
        cfg,
        theme_override=theme_override,
    )
    safe_id = int(getattr(member, "id", 0) or 0)
    return discord.File(BytesIO(rendered), filename=f"exit-{safe_id}.png")


__all__ = [
    "configured_exit_color_mode",
    "configured_exit_custom_colors",
    "configured_exit_font_style_key",
    "configured_exit_shuffle_mode",
    "configured_exit_theme_key",
    "decode_exit_custom_background",
    "exit_card_file",
    "exit_cards_enabled",
    "render_member_exit_card",
]
