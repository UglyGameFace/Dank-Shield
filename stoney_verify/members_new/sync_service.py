from __future__ import annotations

"""Safe public facade for member persistence and departure reconciliation.

The historical persistence implementation lives in ``sync_service_impl``. This
facade keeps the established import path stable while making member-enumeration
authority explicit: cached members are positive evidence only, and absence from
a cache can never be used to mark a database member departed.
"""

from typing import Any, Dict

import discord

from . import sync_service_impl as _impl
from .membership_authority import (
    MembershipSnapshot,
    collect_membership_snapshot,
    departure_reconciliation_allowed,
)

# Stable persistence APIs. Existing callers keep importing these names from the
# original module path while the implementation remains byte-for-byte intact.
sync_member_to_supabase = _impl.sync_member_to_supabase
mark_member_left = _impl.mark_member_left


def __getattr__(name: str) -> Any:
    """Delegate compatibility/private attributes to the preserved implementation."""

    return getattr(_impl, name)


def _snapshot_summary(summary: Dict[str, Any], snapshot: MembershipSnapshot) -> None:
    summary["membership_source"] = snapshot.source
    summary["membership_authoritative"] = bool(snapshot.authoritative)
    if snapshot.error:
        summary["member_fetch_error"] = snapshot.error


def _departure_skip(summary: Dict[str, Any], snapshot: MembershipSnapshot) -> Dict[str, Any]:
    summary["marked_departed"] = 0
    summary["departure_reconciliation_skipped"] = True
    summary["departure_skip_reason"] = "authoritative_member_fetch_failed"
    _snapshot_summary(summary, snapshot)
    return summary


async def run_full_member_sync_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Sync positive live-member evidence and reconcile departures only authoritatively."""

    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = _impl.get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        _snapshot_summary(summary, snapshot)
        members = list(snapshot.members)
        summary["checked"] = len(members)

        active_ids: set[str] = set()
        for idx, member in enumerate(members, start=1):
            try:
                member_id = int(getattr(member, "id", 0) or 0)
                if member_id <= 0:
                    summary["errors"] += 1
                    continue
                # Bots/system accounts are intentionally retained in active_ids.
                # A live Discord member is live membership evidence regardless of
                # whether the account is human or automated.
                active_ids.add(str(member_id))
                await _impl.sync_member_to_supabase(member, in_guild=True)
                summary["active_members_synced"] += 1
                if idx % 10 == 0:
                    await _impl.asyncio.sleep(0)
            except Exception:
                summary["errors"] += 1

        if not departure_reconciliation_allowed(snapshot):
            _departure_skip(summary, snapshot)
            print(
                "⚠️ Member departure reconciliation skipped: full Discord member enumeration failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(members)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        try:
            summary["marked_departed"] = await _impl._bulk_mark_departed_members_async(
                sb,
                str(guild.id),
                active_ids,
            )
        except Exception as exc:
            summary["errors"] += 1
            summary["departure_reconciliation_error"] = f"{type(exc).__name__}: {str(exc)[:350]}"
        return summary

    except Exception as exc:
        summary["error"] = repr(exc)
        summary["errors"] = max(1, int(summary.get("errors") or 0))
        print("⚠️ members_new.sync_service.run_full_member_sync_for_guild error:", repr(exc))
        return summary


async def run_departed_reconciliation_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Mark stale DB rows departed only after a complete Discord member fetch."""

    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = _impl.get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        _snapshot_summary(summary, snapshot)
        active_ids = {str(user_id) for user_id in snapshot.active_user_ids}
        summary["checked"] = len(active_ids)

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] = 1
            _departure_skip(summary, snapshot)
            print(
                "⚠️ Departed-member reconciliation skipped: full Discord member enumeration failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        summary["marked_departed"] = await _impl._bulk_mark_departed_members_async(
            sb,
            str(guild.id),
            active_ids,
        )
        return summary

    except Exception as exc:
        summary["error"] = repr(exc)
        summary["errors"] = 1
        print("⚠️ members_new.sync_service.run_departed_reconciliation_for_guild error:", repr(exc))
        return summary


async def run_full_member_sync_for_all_guilds(bot_instance=None) -> Dict[str, Any]:
    """Run the safe full-sync facade for every connected guild."""

    if bot_instance is None:
        bot_instance = _impl.bot
    out: Dict[str, Any] = {
        "guilds": 0,
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
        "rows": [],
    }
    for guild in list(getattr(bot_instance, "guilds", []) or []):
        try:
            row = await run_full_member_sync_for_guild(guild)
            out["guilds"] += 1
            out["checked"] += int(row.get("checked") or 0)
            out["active_members_synced"] += int(row.get("active_members_synced") or 0)
            out["marked_departed"] += int(row.get("marked_departed") or 0)
            out["errors"] += int(row.get("errors") or 0)
            out["rows"].append(row)
        except Exception:
            out["errors"] += 1
    return out


async def run_departed_reconciliation_for_all_guilds(bot_instance=None) -> Dict[str, Any]:
    """Run authoritative-only departure reconciliation for every connected guild."""

    if bot_instance is None:
        bot_instance = _impl.bot
    out: Dict[str, Any] = {
        "guilds": 0,
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
        "rows": [],
    }
    for guild in list(getattr(bot_instance, "guilds", []) or []):
        try:
            row = await run_departed_reconciliation_for_guild(guild)
            out["guilds"] += 1
            out["checked"] += int(row.get("checked") or 0)
            out["marked_departed"] += int(row.get("marked_departed") or 0)
            out["errors"] += int(row.get("errors") or 0)
            out["rows"].append(row)
        except Exception:
            out["errors"] += 1
    return out


__all__ = [
    "sync_member_to_supabase",
    "mark_member_left",
    "run_full_member_sync_for_guild",
    "run_departed_reconciliation_for_guild",
    "run_full_member_sync_for_all_guilds",
    "run_departed_reconciliation_for_all_guilds",
]
