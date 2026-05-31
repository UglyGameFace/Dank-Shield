from __future__ import annotations

"""Improve transcript summary cards for production use.

The transcript files are already generated safely. This guard improves the Discord
summary embed so staff see useful ticket context instead of mostly raw IDs.
"""

from typing import Any, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ transcript_summary_card_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ transcript_summary_card_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _truncate(text: Any, limit: int = 1024) -> str:
    out = _safe_str(text, "Not provided")
    return out if len(out) <= limit else out[: max(0, limit - 1)] + "…"


def _ticket_number(row: Optional[dict[str, Any]], channel: discord.TextChannel) -> str:
    num = _safe_int((row or {}).get("ticket_number"), 0)
    if num > 0:
        return f"#{num:04d}"
    try:
        import re
        match = re.search(r"(\d{1,8})$", str(channel.name or ""))
        if match:
            return f"#{_safe_int(match.group(1), 0):04d}"
    except Exception:
        pass
    return "Unknown"


def _owner_text(row: Optional[dict[str, Any]]) -> str:
    data = row or {}
    user_id = _safe_int(data.get("user_id") or data.get("owner_id") or data.get("requester_id"), 0)
    if user_id > 0:
        return f"<@{user_id}>\n`{user_id}`"
    name = _safe_str(data.get("username") or data.get("owner_name") or data.get("requester_name"), "Unknown")
    return name


def _claimed_text(row: Optional[dict[str, Any]]) -> str:
    data = row or {}
    staff_id = _safe_int(data.get("claimed_by") or data.get("assigned_to") or data.get("staff_id"), 0)
    if staff_id > 0:
        return f"<@{staff_id}>\n`{staff_id}`"
    name = _safe_str(data.get("claimed_by_name") or data.get("assigned_to_name"), "")
    return name or "Unclaimed"


def _priority_text(row: Optional[dict[str, Any]]) -> str:
    priority = _safe_str((row or {}).get("priority"), "normal").lower()
    labels = {
        "low": "Low",
        "medium": "Normal",
        "normal": "Normal",
        "high": "High",
        "urgent": "Urgent",
        "critical": "Critical",
    }
    return labels.get(priority, priority.title() if priority else "Normal")


def _status_text(row: Optional[dict[str, Any]]) -> str:
    status = _safe_str((row or {}).get("status"), "unknown").lower()
    return status.title() if status else "Unknown"


def _category_text(row: Optional[dict[str, Any]]) -> str:
    category = _safe_str((row or {}).get("category") or (row or {}).get("category_slug"), "Unknown")
    return category.replace("_", " ").replace("-", " ").title() if category else "Unknown"


def _actor_text(actor: Optional[discord.abc.User]) -> str:
    if actor is None:
        return "Unknown"
    try:
        mention = getattr(actor, "mention", None)
        uid = _safe_int(getattr(actor, "id", 0), 0)
        if mention and uid > 0:
            return f"{mention}\n`{uid}`"
    except Exception:
        pass
    return _safe_str(actor, "Unknown")


def _channel_location(channel: discord.TextChannel) -> str:
    try:
        cat = getattr(channel, "category", None)
        if isinstance(cat, discord.CategoryChannel):
            return cat.name
    except Exception:
        pass
    return "No category"


def _build_summary_embed(ts: Any, *, ticket_channel: discord.TextChannel, deleted_by: Optional[discord.abc.User], reason: Optional[str], message_count: int, ticket_row: Optional[dict[str, Any]]) -> discord.Embed:
    number = _ticket_number(ticket_row, ticket_channel)
    reason_text = reason or "Ticket transcript requested"

    embed = discord.Embed(
        title=f"🧾 Ticket Transcript {number}",
        description=(
            f"Transcript saved for {ticket_channel.mention}.\n"
            "HTML and text copies are attached to this message."
        ),
        color=discord.Color.blurple(),
        timestamp=ts.now_utc(),
    )
    embed.add_field(name="Ticket", value=number, inline=True)
    embed.add_field(name="Status", value=_status_text(ticket_row), inline=True)
    embed.add_field(name="Priority", value=_priority_text(ticket_row), inline=True)
    embed.add_field(name="Owner", value=_owner_text(ticket_row), inline=True)
    embed.add_field(name="Claimed By", value=_claimed_text(ticket_row), inline=True)
    embed.add_field(name="Category", value=_category_text(ticket_row), inline=True)
    embed.add_field(name="Messages Captured", value=f"`{int(message_count or 0)}`", inline=True)
    embed.add_field(name="Saved By", value=_actor_text(deleted_by), inline=True)
    embed.add_field(name="Location", value=_truncate(_channel_location(ticket_channel), 256), inline=True)
    embed.add_field(name="Reason", value=_truncate(reason_text, 1024), inline=False)
    embed.add_field(name="Channel ID", value=f"`{ticket_channel.id}`", inline=True)
    embed.set_footer(text=getattr(ts, "_TRANSCRIPT_MARKER", "dank_shield:transcript_posted"))
    return embed


def apply() -> bool:
    try:
        from ..tickets_new import transcript_service as ts
    except Exception as e:
        _warn(f"could not import transcript_service: {e!r}")
        return False

    if getattr(ts, "_TRANSCRIPT_SUMMARY_CARD_GUARD_APPLIED", False):
        return True

    try:
        ts._transcript_summary_embed = lambda **kwargs: _build_summary_embed(ts, **kwargs)
        setattr(ts, "_TRANSCRIPT_SUMMARY_CARD_GUARD_APPLIED", True)
        _log("patched transcript summary cards with production context")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
