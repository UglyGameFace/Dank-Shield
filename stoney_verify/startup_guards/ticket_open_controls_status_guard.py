from __future__ import annotations

"""Add live ticket status fields to open-ticket controls.

TicketTool-style buttons are useful, but staff should not need to click
`Ticket Info` just to see who owns/claimed the ticket or whether a transcript
already exists. This patch keeps the existing controls and replaces only the
open-controls poster with a richer status embed.
"""

from typing import Any, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_open_controls_status_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_open_controls_status_guard: {message}")
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


def _fmt_ticket_number(row: Optional[dict[str, Any]]) -> str:
    number = _safe_int((row or {}).get("ticket_number"), 0)
    return f"#{number:04d}" if number > 0 else "Unknown"


def _fmt_status(row: Optional[dict[str, Any]]) -> str:
    status = _safe_str((row or {}).get("status"), "open").lower()
    return status.title() if status else "Open"


def _fmt_owner(owner: Optional[discord.Member], row: Optional[dict[str, Any]]) -> str:
    if isinstance(owner, discord.Member):
        return owner.mention
    user_id = _safe_int((row or {}).get("user_id") or (row or {}).get("owner_id"), 0)
    if user_id > 0:
        return f"<@{user_id}>"
    return _safe_str((row or {}).get("username") or (row or {}).get("owner_name"), "Unknown")


def _claimed_member(channel: discord.TextChannel, row: Optional[dict[str, Any]]) -> Optional[discord.Member]:
    claimed_id = _safe_int((row or {}).get("claimed_by") or (row or {}).get("assigned_to") or (row or {}).get("staff_id"), 0)
    if claimed_id <= 0:
        return None
    try:
        member = channel.guild.get_member(claimed_id)
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _member_display(member: Optional[discord.Member]) -> str:
    if not isinstance(member, discord.Member):
        return ""
    return _safe_str(getattr(member, "display_name", None) or getattr(member, "name", None), "")


def _member_avatar_url(member: Optional[discord.Member]) -> str:
    if not isinstance(member, discord.Member):
        return ""
    try:
        return _safe_str(getattr(getattr(member, "display_avatar", None), "url", None), "")
    except Exception:
        return ""


def _fmt_claimed(channel: discord.TextChannel, row: Optional[dict[str, Any]]) -> str:
    data = row or {}
    member = _claimed_member(channel, data)
    if member is not None:
        display = _member_display(member)
        return f"{member.mention}" + (f" — `{display}`" if display else "")

    claimed_id = _safe_int(data.get("claimed_by") or data.get("assigned_to") or data.get("staff_id"), 0)
    claimed_name = _safe_str(
        data.get("claimed_by_name")
        or data.get("assigned_to_name")
        or data.get("claimed_by_display_name"),
        "",
    )
    if claimed_id > 0 and claimed_name:
        return f"<@{claimed_id}> — `{claimed_name}`"
    if claimed_id > 0:
        return f"<@{claimed_id}>"
    return claimed_name or "Unclaimed"


def _fmt_priority(row: Optional[dict[str, Any]]) -> str:
    priority = _safe_str((row or {}).get("priority"), "normal").lower()
    labels = {
        "low": "Low",
        "normal": "Normal",
        "medium": "Normal",
        "high": "High",
        "urgent": "Urgent",
        "critical": "Critical",
    }
    return labels.get(priority, priority.title() if priority else "Normal")


def _has_transcript(row: Optional[dict[str, Any]]) -> bool:
    data = row or {}
    return bool(
        _safe_str(data.get("transcript_url"), "")
        or _safe_str(data.get("transcript_message_id"), "")
        or _safe_str(data.get("transcript_channel_id"), "")
    )


async def _rich_open_controls(tx: Any, channel: discord.TextChannel) -> Optional[discord.Message]:
    lock = tx._lock_for(tx._OPEN_CONTROLS_LOCKS, channel.id)
    async with lock:
        if await tx._ticket_is_deleted(channel):
            return None
        if await tx._ticket_is_closed(channel):
            return None

        owner = await tx._resolve_ticket_owner(channel)
        row = await tx._ticket_row(channel.id)
        claimed_member = _claimed_member(channel, row)

        embed = discord.Embed(
            title="🟢 Ticket Open",
            description=tx._open_ticket_embed_description(owner),
            color=discord.Color.green(),
            timestamp=tx.now_utc(),
        )
        embed.add_field(name="Ticket", value=_fmt_ticket_number(row), inline=True)
        embed.add_field(name="Status", value=_fmt_status(row), inline=True)
        embed.add_field(name="Priority", value=_fmt_priority(row), inline=True)
        embed.add_field(name="Owner", value=_fmt_owner(owner, row), inline=True)
        embed.add_field(name="Claimed By", value=_fmt_claimed(channel, row), inline=True)
        embed.add_field(name="Transcript", value="Saved" if _has_transcript(row) else "Not posted yet", inline=True)
        avatar_url = _member_avatar_url(claimed_member)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"{tx._OPEN_CONTROLS_MARKER} • staff status refreshes after ticket actions")

        view = tx.TicketOpenActionsView()
        existing_messages = await tx._find_bot_control_messages(
            channel,
            marker=tx._OPEN_CONTROLS_MARKER,
            custom_ids={"sv:ticket:close", "sv:ticket:delete_open"},
            limit=80,
        )

        if existing_messages:
            latest = existing_messages[0]
            try:
                await latest.edit(embed=embed, view=view, content=tx._OPEN_CONTROLS_MARKER)
                await tx._cleanup_duplicate_control_messages(
                    existing_messages,
                    keep_message_id=latest.id,
                    suffix="ℹ️ Replaced by latest open-ticket controls.",
                )
                return latest
            except Exception:
                pass

        try:
            return await channel.send(
                content=tx._OPEN_CONTROLS_MARKER,
                embed=embed,
                view=view,
            )
        except Exception as e:
            _warn(f"failed to post rich open controls channel={channel.id}: {type(e).__name__}: {e}")
            return None


def apply() -> bool:
    try:
        from .. import transcripts as tx
    except Exception as e:
        _warn(f"could not import transcripts: {e!r}")
        return False

    if getattr(tx, "_TICKET_OPEN_CONTROLS_STATUS_GUARD_APPLIED", False):
        return True

    try:
        tx.post_or_replace_open_ticket_controls = lambda channel: _rich_open_controls(tx, channel)
        setattr(tx, "_TICKET_OPEN_CONTROLS_STATUS_GUARD_APPLIED", True)
        _log("patched open-ticket controls with live status fields")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
