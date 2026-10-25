"""Read-only operator check for Embed Studio managed-artwork budgets.

Run only in a configured private bot environment:
    python -m tools.audit_embed_studio_artwork --guild-id GUILD_ID

Reports aggregate allocation and uncertain upload count, not members, tokens,
file URLs, or image contents. Never automatically removes a public asset.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from stoney_verify.embed_studio_artwork_budget import (
    get_budget, read_artwork_usage,
)


async def report(guild_id: int) -> dict:
    budget=get_budget()
    usage=await read_artwork_usage(guild_id)
    return {
        "guild_id": str(guild_id),
        "guild_allocated_bytes": budget.guild_bytes,
        "global_allocated_bytes": budget.global_bytes,
        "guild_used_bytes": usage["guild_used"],
        "global_used_bytes": usage["global_used"],
        "stored_objects": usage["asset_count"],
        "uncertain_uploads": usage["reservation_count"],
        "uncertain_older_than_30_minutes": usage["stale_reservations"],
        "safe_to_delete_automatically": False,
        "next_step": (
            "Compare stale reservations with the bucket's actual objects "
            "before manual cleanup; published image references must be preserved."
        ),
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--guild-id",type=int,required=True)
    args=parser.parse_args()
    if args.guild_id <= 0:
        parser.error("guild-id must be positive")
    try:
        result=asyncio.run(report(args.guild_id))
    except Exception as exc:
        print(json.dumps({
            "status":"unavailable",
            "error_type":type(exc).__name__,
            "note":"No files or database records were modified.",
        }))
        return 2
    print(json.dumps({"status":"read_only","result":result},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
