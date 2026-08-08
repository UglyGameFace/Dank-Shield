from __future__ import annotations

"""Shared placeholder rendering for public member join/exit messages.

The live runtimes and their previews must use the same renderer. Tokens are
matched case-insensitively and tolerate harmless whitespace/zero-width format
characters inside braces so stored templates cannot leak variants such as
``{ username }`` or ``{UserName}`` into a public Discord message.
"""

import re
from typing import Any, Mapping, Optional

import discord

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_TOKEN_RE = re.compile(
    r"\{\{?[\s\u200b\u200c\u200d\u2060\ufeff]*"
    r"([A-Za-z][A-Za-z0-9_\-\s\u200b\u200c\u200d\u2060\ufeff]{0,63}?)"
    r"[\s\u200b\u200c\u200d\u2060\ufeff]*\}\}?"
)

KNOWN_PLACEHOLDERS = frozenset(
    {
        "server_name",
        "member",
        "member_name",
        "user",
        "mention",
        "member_mention",
        "username",
        "display_name",
        "member_count",
        "account_age",
        "joined_at",
        "rules_channel",
        "verify_channel",
        "support_channel",
        "random_welcome_line",
        "invite_code",
        "invite_link",
        "invite_source",
        "invite_channel",
        "invite_owner",
        "invite_inviter",
        "invite_owner_id",
        "invite_inviter_id",
    }
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def normalize_placeholder_key(value: Any) -> str:
    text = _ZERO_WIDTH_RE.sub("", str(value or ""))
    text = re.sub(r"\s+", "", text).strip().lower().replace("-", "_")
    return text


def _clean_channel_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _channel_mention(
    guild: discord.Guild,
    cfg: Any,
    *,
    keys: tuple[str, ...],
    names: tuple[str, ...],
) -> str:
    for key in keys:
        channel_id = _safe_int(_cfg_value(cfg, key, None), 0)
        channel = guild.get_channel(channel_id) if channel_id > 0 else None
        if isinstance(channel, discord.TextChannel):
            return channel.mention

    wanted = tuple(_clean_channel_name(name) for name in names if str(name or "").strip())
    try:
        for channel in list(getattr(guild, "text_channels", []) or []):
            if not isinstance(channel, discord.TextChannel):
                continue
            name = _clean_channel_name(getattr(channel, "name", ""))
            if any(token and token in name for token in wanted):
                return channel.mention
    except Exception:
        pass
    return "not set"


def _age_text(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        now = discord.utils.utcnow()
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        days = max(0, int((now - dt).total_seconds()) // 86400)
        if days >= 365:
            years = days // 365
            months = (days % 365) // 30
            return f"{years}y {months}mo" if months else f"{years}y"
        if days >= 30:
            months = days // 30
            rem = days % 30
            return f"{months}mo {rem}d" if rem else f"{months}mo"
        if days >= 1:
            return f"{days}d"
        return "today"
    except Exception:
        return "unknown"


def _discord_time(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        return f"<t:{int(dt.timestamp())}:F> (<t:{int(dt.timestamp())}:R>)"
    except Exception:
        return "unknown"


def _server_profile(guild: discord.Guild) -> str:
    parts = [str(getattr(guild, "name", "") or "")]
    try:
        parts.extend(str(c.name or "") for c in getattr(guild, "categories", []) or [])
        parts.extend(str(c.name or "") for c in getattr(guild, "text_channels", []) or [])
    except Exception:
        pass
    haystack = " ".join(parts).lower()
    if any(word in haystack for word in ("game", "gaming", "clips", "lobby", "ranked", "xbox", "playstation", "cod", "minecraft")):
        return "gaming"
    if any(word in haystack for word in ("support", "ticket", "help", "docs", "faq")):
        return "support"
    if any(word in haystack for word in ("class", "study", "school", "course", "learn", "lesson")):
        return "education"
    if any(word in haystack for word in ("shop", "store", "client", "business", "orders", "sales")):
        return "business"
    if any(word in haystack for word in ("stream", "creator", "youtube", "twitch", "media", "art")):
        return "creator"
    return "community"


def _smart_welcome_line(guild: discord.Guild) -> str:
    lines = {
        "gaming": "Welcome in — get verified, find your channels, and enjoy the games.",
        "support": "Welcome in — check the getting-started info and open a ticket if you need help.",
        "education": "Welcome in — start with the rules, then check the learning channels.",
        "business": "Welcome — please review the rules and start-here information before posting.",
        "creator": "Welcome in — check the rules, introduce yourself, and explore the creator channels.",
        "community": "Welcome in — start with the rules, verify if needed, and enjoy the community.",
    }
    return lines.get(_server_profile(guild), lines["community"])


def lifecycle_placeholder_values(
    member: discord.Member,
    cfg: Any,
    *,
    invite_values: Optional[Mapping[str, Any]] = None,
    preview: bool = False,
) -> dict[str, str]:
    guild = member.guild
    display_name = str(getattr(member, "display_name", "") or getattr(member, "name", "") or member)
    username = str(getattr(member, "name", "") or member)
    invite_fallback = "real join only" if preview else "unavailable"
    supplied_invites = dict(invite_values or {})

    values = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": display_name,
        "member_name": display_name,
        "user": display_name,
        "mention": str(getattr(member, "mention", "") or display_name),
        "member_mention": str(getattr(member, "mention", "") or display_name),
        "username": username,
        "display_name": display_name,
        "member_count": str(getattr(guild, "member_count", "") or "unknown"),
        "account_age": _age_text(getattr(member, "created_at", None)),
        "joined_at": _discord_time(getattr(member, "joined_at", None)),
        "rules_channel": _channel_mention(
            guild,
            cfg,
            keys=("rules_channel_id", "rules_id"),
            names=("rules",),
        ),
        "verify_channel": _channel_mention(
            guild,
            cfg,
            keys=("verify_channel_id", "verification_channel_id", "verify_id"),
            names=("verification", "verify"),
        ),
        "support_channel": _channel_mention(
            guild,
            cfg,
            keys=("support_channel_id", "ticket_channel_id", "tickets_channel_id", "support_id"),
            names=("support", "ticket", "help"),
        ),
        "random_welcome_line": _smart_welcome_line(guild),
    }

    for key in (
        "invite_code",
        "invite_link",
        "invite_source",
        "invite_channel",
        "invite_owner",
        "invite_inviter",
        "invite_owner_id",
        "invite_inviter_id",
    ):
        raw = supplied_invites.get(key)
        values[key] = str(raw).strip() if raw is not None and str(raw).strip() else invite_fallback
    return values


def render_lifecycle_template(
    text: Any,
    member: discord.Member,
    cfg: Any,
    *,
    invite_values: Optional[Mapping[str, Any]] = None,
    preview: bool = False,
) -> str:
    values = lifecycle_placeholder_values(
        member,
        cfg,
        invite_values=invite_values,
        preview=preview,
    )

    def replace(match: re.Match[str]) -> str:
        key = normalize_placeholder_key(match.group(1))
        if key not in KNOWN_PLACEHOLDERS:
            return match.group(0)
        return values.get(key, "unavailable")

    rendered = _TOKEN_RE.sub(replace, str(text or ""))

    # A second pass is deliberately defensive. A recognized token must never be
    # allowed to leak publicly even if a malformed legacy template slipped past
    # the normal parser. Unknown owner-authored brace text remains untouched.
    def scrub(match: re.Match[str]) -> str:
        key = normalize_placeholder_key(match.group(1))
        if key in KNOWN_PLACEHOLDERS:
            return values.get(key, "unavailable")
        return match.group(0)

    return _TOKEN_RE.sub(scrub, rendered)


def unresolved_known_placeholders(text: Any) -> tuple[str, ...]:
    found: list[str] = []
    for match in _TOKEN_RE.finditer(str(text or "")):
        key = normalize_placeholder_key(match.group(1))
        if key in KNOWN_PLACEHOLDERS and key not in found:
            found.append(key)
    return tuple(found)


__all__ = [
    "KNOWN_PLACEHOLDERS",
    "lifecycle_placeholder_values",
    "normalize_placeholder_key",
    "render_lifecycle_template",
    "unresolved_known_placeholders",
]
