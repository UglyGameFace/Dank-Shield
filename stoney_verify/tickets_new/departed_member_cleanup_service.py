from __future__ import annotations

"""Departed-member verification ticket cleanup service.

This owns verification-ticket repair when a member leaves or when startup finds
stale open verification tickets for users who are no longer in the guild.

Events should call this service instead of directly touching ticket rows,
transcripts, and Discord ticket channels.
"""

import traceback
from typing import Any, Dict, Iterable, Optional

import discord

from .repository import (
    find_open_ticket_for_owner,
    list_open_tickets_for_guild,
    mark_ticket_closed,
    mark_ticket_deleted,
)


def _log(message: str) -> None:
    try:
        print(f"🧹 departed_ticket_cleanup {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ departed_ticket_cleanup {message}")
    except Exception:
        pass


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _ticket_channel_id(row: Dict[str, Any]) -> int:
    return _as_int(row.get("channel_id") or row.get("discord_thread_id") or 0, 0)


async def _resolve_guild_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    if channel_id <= 0:
        return None
    try:
        channel = guild.get_channel(channel_id)
        if channel is not None:
            return channel
    except Exception:
        pass
    try:
        return await guild.fetch_channel(channel_id)
    except Exception:
        return None


async def _send_transcript_if_possible(
    channel: discord.TextChannel,
    member: Optional[discord.Member],
    *,
    owner_id: int,
    decision: str,
) -> None:
    try:
        from ..transcripts import send_tickettool_style_transcript
    except Exception:
        send_tickettool_style_transcript = None  # type: ignore

    if send_tickettool_style_transcript is None:
        return

    try:
        await send_tickettool_style_transcript(
            channel,
            member,
            owner_id=int(owner_id),
            closed_by=None,
            decision=decision,
        )
    except Exception as e:
        _warn(f"transcript post failed channel={channel.id} owner={owner_id}: {e!r}")


async def _mark_deleted_or_closed(
    *,
    channel_id: int,
    reason: str,
) -> bool:
    deleted_ok = False
    try:
        deleted_ok = await mark_ticket_deleted(
            channel_id=channel_id,
            deleted_by=None,
            reason=reason,
        )
    except Exception as e:
        _warn(f"mark_ticket_deleted failed channel={channel_id}: {e!r}")

    if deleted_ok:
        return True

    try:
        return bool(
            await mark_ticket_closed(
                channel_id=channel_id,
                closed_by=None,
                reason=reason,
            )
        )
    except Exception as e:
        _warn(f"mark_ticket_closed fallback failed channel={channel_id}: {e!r}")
        return False


async def close_verification_ticket_for_departed_member(
    member: discord.Member,
    *,
    leave_reason: str,
) -> bool:
    """Close/delete a departed member's open verification ticket if one exists."""
    try:
        row = await find_open_ticket_for_owner(
            guild_id=member.guild.id,
            owner_id=member.id,
            category="verification_issue",
        )
        if not isinstance(row, dict):
            return False

        channel_id = _ticket_channel_id(row)
        if channel_id <= 0:
            return False

        channel = await _resolve_guild_channel(member.guild, channel_id)
        if isinstance(channel, discord.TextChannel):
            _log(f"auto-closing verification ticket for departed member member={member.id} channel={channel.id} reason={leave_reason}")
            await _send_transcript_if_possible(
                channel,
                member,
                owner_id=int(member.id),
                decision=leave_reason,
            )
            await _mark_deleted_or_closed(channel_id=channel.id, reason=leave_reason)
            try:
                await channel.delete(reason=leave_reason)
            except discord.Forbidden:
                _warn(f"missing permission to delete departed member ticket channel={channel.id}")
            except Exception as e:
                _warn(f"failed deleting departed member ticket channel={channel.id}: {e!r}")
            return True

        repaired = await _mark_deleted_or_closed(channel_id=channel_id, reason=leave_reason)
        if repaired:
            _log(f"repaired stale verification ticket row for departed member member={member.id} channel_id={channel_id}")
        return bool(repaired)
    except Exception as e:
        _warn(f"close_verification_ticket_for_departed_member error: {e!r}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


async def reconcile_stale_open_verification_tickets(guilds: Iterable[discord.Guild]) -> int:
    """Repair open verification tickets whose owners are no longer in guild."""
    repaired = 0
    try:
        for guild in list(guilds or []):
            try:
                rows = await list_open_tickets_for_guild(
                    guild_id=guild.id,
                    category="verification_issue",
                    statuses=("open", "claimed"),
                )
            except Exception as e:
                _warn(f"stale verification ticket query failed guild={getattr(guild, 'id', 'unknown')}: {e!r}")
                continue

            for row in rows:
                if not isinstance(row, dict):
                    continue

                owner_id = _as_int(row.get("user_id") or row.get("owner_id") or row.get("requester_id"), 0)
                if owner_id <= 0:
                    continue

                try:
                    member = guild.get_member(owner_id)
                    if member is None:
                        member = await guild.fetch_member(owner_id)
                except Exception:
                    member = None

                if isinstance(member, discord.Member):
                    continue

                channel_id = _ticket_channel_id(row)
                if channel_id <= 0:
                    continue

                channel = await _resolve_guild_channel(guild, channel_id)
                reason = "AUTO CLOSED: user already left server"

                if isinstance(channel, discord.TextChannel):
                    await _send_transcript_if_possible(
                        channel,
                        None,
                        owner_id=owner_id,
                        decision=reason,
                    )
                    await _mark_deleted_or_closed(channel_id=channel.id, reason=reason)
                    try:
                        await channel.delete(reason="Verification ticket cleanup for departed user")
                    except Exception as e:
                        _warn(f"startup ticket delete failed channel={channel.id}: {e!r}")
                else:
                    await _mark_deleted_or_closed(channel_id=channel_id, reason=reason)

                repaired += 1

        _log(f"stale verification ticket reconciliation complete: repaired={repaired}")
        return repaired
    except Exception as e:
        _warn(f"reconcile_stale_open_verification_tickets error: {e!r}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return repaired


__all__ = [
    "close_verification_ticket_for_departed_member",
    "reconcile_stale_open_verification_tickets",
]
