from __future__ import annotations

"""Authoritative guild-membership enumeration safety helpers.

A Discord member cache is useful positive evidence that a member is present, but
it is never authoritative negative evidence after a failed full member fetch.
Departure marking must only run from a successfully completed authoritative
enumeration.
"""

from dataclasses import dataclass
from typing import Any

import discord


@dataclass(frozen=True)
class MembershipSnapshot:
    members: tuple[Any, ...]
    authoritative: bool
    source: str
    error: str = ""

    @property
    def active_user_ids(self) -> set[int]:
        return {
            int(member.id)
            for member in self.members
            if int(getattr(member, "id", 0) or 0) > 0
        }


async def collect_membership_snapshot(guild: discord.Guild) -> MembershipSnapshot:
    """Fetch all live members, falling back to cache only for positive sync work.

    The fallback snapshot is deliberately marked non-authoritative so callers
    cannot use cache absence to mark database rows departed.
    """

    try:
        members = tuple(member async for member in guild.fetch_members(limit=None))
        return MembershipSnapshot(
            members=members,
            authoritative=True,
            source="discord_fetch_members",
        )
    except Exception as exc:
        cached = tuple(list(getattr(guild, "members", []) or []))
        return MembershipSnapshot(
            members=cached,
            authoritative=False,
            source="discord_member_cache",
            error=f"{type(exc).__name__}: {str(exc)[:350]}",
        )


def departure_reconciliation_allowed(snapshot: MembershipSnapshot) -> bool:
    """Return True only for a completed authoritative Discord enumeration."""

    return bool(snapshot.authoritative and snapshot.source == "discord_fetch_members")


__all__ = [
    "MembershipSnapshot",
    "collect_membership_snapshot",
    "departure_reconciliation_allowed",
]
