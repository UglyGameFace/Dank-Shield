#!/usr/bin/env python3
from __future__ import annotations

"""
Backfill the current owner/home guild env settings into guild_configs.

Why this exists:
- The original private Stoney Balonney server used global env IDs.
- Public multi-guild mode needs per-guild config to prevent cross-server leaks.
- This script copies the existing owner server values into the same config table
  every other server will use, without changing the server's actual channels/roles.

Usage:
  python scripts/backfill_owner_guild_config.py --dry-run
  python scripts/backfill_owner_guild_config.py

Required env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  STONEY_OWNER_GUILD_ID or GUILD_ID

The script is idempotent. It upserts by guild_id.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from supabase import create_client


CONFIG_TABLE = os.getenv("STONEY_GUILD_CONFIG_TABLE", "guild_configs").strip() or "guild_configs"


FIELD_ENV_MAP = {
    "modlog_channel_id": "MODLOG_CHANNEL_ID",
    "transcripts_channel_id": "TRANSCRIPTS_CHANNEL_ID",
    "ticket_category_id": "TICKET_CATEGORY_ID",
    "ticket_archive_category_id": "TICKET_ARCHIVE_CATEGORY_ID",
    "verify_channel_id": "VERIFY_CHANNEL_ID",
    "vc_verify_channel_id": "VC_VERIFY_CHANNEL_ID",
    "vc_verify_queue_channel_id": "VC_VERIFY_QUEUE_CHANNEL_ID",
    "unverified_role_id": "UNVERIFIED_ROLE_ID",
    "verified_role_id": "VERIFIED_ROLE_ID",
    "resident_role_id": "RESIDENT_ROLE_ID",
    "staff_role_id": "STAFF_ROLE_ID",
}


def _clean_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text == "0":
        return None
    if not text.isdigit():
        return None
    return text


def _owner_guild_id() -> str:
    for key in (
        "STONEY_OWNER_GUILD_ID",
        "STONEY_HOME_GUILD_ID",
        "OWNER_GUILD_ID",
        "HOME_GUILD_ID",
        "GUILD_ID",
    ):
        value = _clean_id(os.getenv(key, ""))
        if value:
            return value
    raise RuntimeError(
        "Missing owner guild id. Set STONEY_OWNER_GUILD_ID to your main Stoney Balonney guild ID."
    )


def _build_payload(owner_guild_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "guild_id": owner_guild_id,
        "setup_completed": True,
        "setup_source": "owner_env_backfill",
        "setup_notes": "Backfilled from existing owner-server env globals; should preserve current Stoney Balonney behavior.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    missing = []
    for field, env_name in FIELD_ENV_MAP.items():
        value = _clean_id(os.getenv(env_name, ""))
        if value:
            payload[field] = value
        else:
            payload[field] = None
            missing.append(env_name)

    if missing:
        payload["setup_notes"] += " Missing/empty env values during backfill: " + ", ".join(missing)

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill owner guild env settings into guild_configs.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without writing to Supabase.")
    args = parser.parse_args()

    try:
        owner_gid = _owner_guild_id()
        payload = _build_payload(owner_gid)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"Target table: {CONFIG_TABLE}")
    print(f"Owner guild: {owner_gid}")
    print("Payload:")
    for key in sorted(payload.keys()):
        print(f"  {key}: {payload[key]}")

    if args.dry_run:
        print("Dry run only; no Supabase write performed.")
        return 0

    url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not service_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.", file=sys.stderr)
        return 2

    client = create_client(url, service_key)
    result = client.table(CONFIG_TABLE).upsert(payload, on_conflict="guild_id").execute()
    data = getattr(result, "data", None)
    print("Upsert complete.")
    print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
