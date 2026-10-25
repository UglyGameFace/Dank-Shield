"""Explicit, one-server Embed Studio v2 staging command.

Usage:
 python -m tools.stage_embed_studio_v2 --guild-id 123456 --dry-run
 python -m tools.stage_embed_studio_v2 --guild-id 123456 --apply --confirm SEED_123456

The default is a read-only report. Never runs at bot startup. No secrets or
stored message contents are printed, and legacy configuration is never erased.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from stoney_verify.embed_studio_core import STUDIO_KEY, library_payload, parse_library
from stoney_verify.embed_studio_storage_v2 import (
    SECTIONS, seed_legacy, storage_v2_enabled, verify_project,
)
from stoney_verify.guild_config import get_guild_config


async def _run(guild_id: int, *, apply: bool) -> dict:
    verify_project()
    if storage_v2_enabled():
        raise RuntimeError("Disable the production v2 routing flag before running the staging tool")
    config = await get_guild_config(guild_id, refresh=True)
    if str(config.get("source") or "").startswith("unavailable:"):
        raise RuntimeError("Canonical guild configuration unavailable; nothing was staged")
    legacy = config.get(STUDIO_KEY)
    if legacy is not None and not isinstance(legacy, dict):
        raise RuntimeError("Legacy Studio library is not a JSON object")
    model = parse_library(config)
    checked = library_payload(model)
    sections = {s: len(checked[s]) for s in SECTIONS}
    # Protect against invisible truncation in the older bounded parser.
    if legacy:
        for section in SECTIONS:
            original = legacy.get(section, {})
            if not isinstance(original, dict) or len(original) != sections[section]:
                raise RuntimeError("Legacy collection is inconsistent; stop before migration")
    if not apply:
        return {"mode": "dry_run", "source_preserved": True, "guild_id": guild_id,
                "sections": sections, "has_legacy_data": bool(legacy),
                "note": "No migration or database writes attempted"}
    if not legacy:
        return {"mode": "no_op", "guild_id": guild_id,
                "note": "No legacy Studio data. Enable v2 only after schema is verified."}
    return {"mode": "copied", "guild_id": guild_id,
            **await seed_legacy(guild_id, config)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--guild-id", type=int, required=True)
    op = p.add_mutually_exclusive_group(required=True)
    op.add_argument("--dry-run", action="store_true")
    op.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", default="")
    args = p.parse_args()
    if args.guild_id <= 0:
        p.error("guild ID must be positive")
    if args.apply and args.confirm != f"SEED_{args.guild_id}":
        p.error("Manual seed requires --confirm SEED_<guild-id>")
    try:
        out = asyncio.run(_run(args.guild_id, apply=bool(args.apply)))
        print(json.dumps(out, sort_keys=True))
        return 0
    except Exception as exc:
        # Private database errors may contain credentials. Only expose type.
        print(json.dumps({"state": "blocked", "error_type": type(exc).__name__,
                          "note": "Legacy data remains authoritative and untouched."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
