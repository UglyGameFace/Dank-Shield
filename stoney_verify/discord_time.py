from __future__ import annotations

"""Discord-native timestamp formatting for user-facing dates and times.

Persist timestamps in UTC, but render them with Discord timestamp markup so each
viewer sees the same instant in their own client timezone and locale.
"""

from datetime import datetime, timezone
from typing import Any, Optional

_ALLOWED_STYLES = {"t", "T", "d", "D", "f", "F", "R"}


def coerce_datetime(value: Any) -> Optional[datetime]:
    """Return an aware UTC datetime from a datetime or ISO-8601 value."""

    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value or "").strip()
            if not text:
                return None
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def discord_timestamp(
    value: Any,
    *,
    style: str = "f",
    fallback: str = "Unknown time",
) -> str:
    """Format one instant for Discord's per-viewer local rendering."""

    parsed = coerce_datetime(value)
    if parsed is None:
        return str(fallback)

    safe_style = str(style or "f")
    if safe_style not in _ALLOWED_STYLES:
        safe_style = "f"

    return f"<t:{int(parsed.timestamp())}:{safe_style}>"


def discord_timestamp_pair(
    value: Any,
    *,
    absolute_style: str = "f",
    fallback: str = "Unknown time",
) -> str:
    """Show a localized absolute time followed by Discord's relative time."""

    parsed = coerce_datetime(value)
    if parsed is None:
        return str(fallback)

    absolute = discord_timestamp(parsed, style=absolute_style, fallback=fallback)
    relative = discord_timestamp(parsed, style="R", fallback=fallback)
    return f"{absolute} • {relative}"


__all__ = [
    "coerce_datetime",
    "discord_timestamp",
    "discord_timestamp_pair",
]
