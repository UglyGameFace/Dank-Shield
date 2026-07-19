from __future__ import annotations

"""Authoritative guild-membership enumeration safety helpers.

A Discord member cache is useful positive evidence that a member is present, but
it is never authoritative negative evidence after a failed full member fetch.
Departure marking must only run from a successfully completed authoritative
enumeration.
"""

import asyncio
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


def _snapshot_metadata(summary: dict[str, Any], snapshot: MembershipSnapshot) -> None:
    summary["membership_source"] = snapshot.source
    summary["membership_authoritative"] = bool(snapshot.authoritative)
    if snapshot.error:
        summary["member_fetch_error"] = snapshot.error


def _mark_reconciliation_skipped(summary: dict[str, Any]) -> None:
    summary["marked_departed"] = 0
    summary["departure_reconciliation_skipped"] = True
    summary["departure_skip_reason"] = "authoritative_member_fetch_failed"


async def run_safe_departed_reconciliation_for_guild(guild: discord.Guild) -> dict[str, Any]:
    """Reconcile stale DB membership only after a complete Discord enumeration.

    This is the runtime entry point for departure marking. It intentionally does
    not call the legacy sync-service reconciliation function because that function
    falls back to ``guild.members`` after a failed fetch and therefore cannot use
    absence as authoritative evidence.
    """

    from . import sync_service

    summary: dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = sync_service.get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        _snapshot_metadata(summary, snapshot)
        active_ids = {str(user_id) for user_id in snapshot.active_user_ids}
        summary["checked"] = len(active_ids)

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] = 1
            _mark_reconciliation_skipped(summary)
            print(
                "⚠️ Departed-member reconciliation skipped: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        summary["marked_departed"] = await sync_service._bulk_mark_departed_members_async(
            sb,
            str(guild.id),
            active_ids,
        )
        return summary
    except Exception as exc:
        summary["error"] = repr(exc)
        summary["errors"] = 1
        print("⚠️ membership_authority safe departed reconciliation error:", repr(exc))
        return summary


async def run_safe_full_member_sync_for_guild(guild: discord.Guild) -> dict[str, Any]:
    """Persist live members and mark departures only from authoritative evidence.

    When Discord's full enumeration fails, cached members are still synced as
    positive ``in_guild=True`` evidence, but no database row is marked departed.
    """

    from . import sync_service

    summary: dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = sync_service.get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        _snapshot_metadata(summary, snapshot)
        members = list(snapshot.members)
        summary["checked"] = len(members)

        active_ids: set[str] = set()
        for index, member in enumerate(members, start=1):
            member_id = int(getattr(member, "id", 0) or 0)
            if member_id <= 0:
                summary["errors"] += 1
                continue
            active_ids.add(str(member_id))
            try:
                await sync_service.sync_member_to_supabase(member, in_guild=True)
                summary["active_members_synced"] += 1
            except Exception:
                summary["errors"] += 1
            if index % 10 == 0:
                await asyncio.sleep(0)

        if not departure_reconciliation_allowed(snapshot):
            _mark_reconciliation_skipped(summary)
            print(
                "⚠️ Member departure reconciliation skipped during full sync: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        summary["marked_departed"] = await sync_service._bulk_mark_departed_members_async(
            sb,
            str(guild.id),
            active_ids,
        )
        return summary
    except Exception as exc:
        summary["error"] = repr(exc)
        summary["errors"] = max(1, int(summary.get("errors") or 0))
        print("⚠️ membership_authority safe full member sync error:", repr(exc))
        return summary


__all__ = [
    "MembershipSnapshot",
    "collect_membership_snapshot",
    "departure_reconciliation_allowed",
    "run_safe_departed_reconciliation_for_guild",
    "run_safe_full_member_sync_for_guild",
]
