#!/usr/bin/env python3
from __future__ import annotations

"""Prepare the next events.py split: member sync ownership.

This script intentionally stops with instructions unless the service can accept
risk_profile. The events.py legacy member sync body still preserves join-risk
fields, while members_new.sync_service currently owns the safer DB/write path.
Before removing events.py fallback bodies, sync_service must accept and persist
risk_profile so anti-raid/alt-cluster evidence is not lost.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"

REQUIRED_SYNC_SERVICE_SUPPORT = (
    "risk_profile: Optional[Dict[str, Any]] = None",
    "_build_risk_payload_from_profile",
    "last_join_risk_score",
    "alt_cluster_key",
    "suspicion_flags",
)

LEGACY_EVENTS_MEMBER_SYNC_MARKERS = (
    "async def _sync_member_to_supabase(",
    "await _guild_members_upsert_async(sb, full_payload",
    "async def _mark_member_left(member: discord.Member) -> None:",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def main() -> int:
    if not EVENTS.exists():
        print(f"❌ Missing {EVENTS}")
        return 1
    if not SYNC_SERVICE.exists():
        print(f"❌ Missing {SYNC_SERVICE}")
        return 1

    events = read(EVENTS)
    sync_service = read(SYNC_SERVICE)

    missing = [marker for marker in REQUIRED_SYNC_SERVICE_SUPPORT if marker not in sync_service]
    legacy_present = [marker for marker in LEGACY_EVENTS_MEMBER_SYNC_MARKERS if marker in events]

    print("Member sync handoff readiness:")
    print(f" - sync_service risk-profile support: {'ready' if not missing else 'missing'}")
    print(f" - events.py legacy member sync bodies: {'present' if legacy_present else 'not found'}")

    if missing:
        print("\n❌ Do not remove events.py member-sync fallback yet.")
        print("members_new/sync_service.py is missing risk-profile preservation markers:")
        for marker in missing:
            print(f" - {marker}")
        print("\nNext implementation step:")
        print("  1. Add risk_profile support to members_new/sync_service.sync_member_to_supabase.")
        print("  2. Persist risk_score/risk_level/fingerprint/alt_cluster fields there.")
        print("  3. Update events._new_sync_member_safe to pass risk_profile to the service.")
        print("  4. Then replace legacy events._sync_member_to_supabase and _mark_member_left with thin service delegates.")
        return 2

    if not legacy_present:
        print("✅ No legacy member sync bodies detected in events.py.")
        return 0

    print("\n✅ sync_service appears ready. You can now physically replace legacy events.py member-sync bodies.")
    print("This script currently performs readiness checks only; update it to apply the replacements once reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
