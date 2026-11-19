"""One-time migration: legacy Community Tools stickies -> Post Builder.

Usage:
 python -m tools.migrate_community_stickies --dry-run
 python -m tools.migrate_community_stickies --apply --confirm MIGRATE_STICKIES

For every ENABLED legacy sticky (all guilds), this creates a Post Builder
published record that adopts the sticky's own live message (same bot, so
no repost and no deleted message) and marks it sticky in the new system,
then disables the legacy config. Legacy rows are never deleted — disabled
rows stay in ``dank_stickies`` as the inert archive, next to the JSON
backup captured before the migration. Poll stickies carry their question,
choices, votes, and state across unchanged. Disabled legacy stickies are
reported and left exactly as they are.

Never runs at bot startup. Run inside the bot container (storage access),
then restart the app so both runtimes re-index: the Studio sticky runtime
picks the adopted records up, and the legacy runtime no longer owns the
channels.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from stoney_verify.community_tools_service import (
    get_sticky_poll,
    list_stickies,
    set_sticky_enabled,
)
from stoney_verify.embed_studio_core import trim_published_history
from stoney_verify.embed_studio_polls import studio_record_from_legacy_sticky
from stoney_verify.embed_studio_sticky import post_is_sticky
from stoney_verify.embed_studio_ui import _load_library, _save_library


async def _migrate_one(config, *, apply: bool) -> dict:
    poll = None
    if config.mode == "poll":
        legacy_poll = await get_sticky_poll(int(config.channel_id))
        if legacy_poll is not None:
            poll = {
                "question": legacy_poll.question,
                "options": list(legacy_poll.options),
                "votes": dict(legacy_poll.votes),
                "state": legacy_poll.state,
            }
    config_row = {
        "guild_id": config.guild_id, "channel_id": config.channel_id,
        "mode": config.mode, "last_message_id": config.last_message_id,
        "updated_by": config.updated_by,
    }
    try:
        post_key, record = studio_record_from_legacy_sticky(config_row, poll)
    except Exception as exc:
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": f"skipped: {exc}"}
    if not apply:
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "dry_run", "post_key": post_key,
                "kind": "poll" if poll else config.mode}
    source, lib = await _load_library(int(config.guild_id))
    existing = lib["published"].get(post_key)
    if isinstance(existing, dict) and post_is_sticky(existing):
        await set_sticky_enabled(int(config.channel_id), False,
                                 actor_id=config.updated_by)
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "already_migrated", "post_key": post_key}
    lib["published"][post_key] = record
    lib["published"] = trim_published_history(lib["published"], max_posts=20)
    if post_key not in lib["published"]:
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "failed: published history full of live panels"}
    if not await _save_library(int(config.guild_id), source, lib):
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "failed: library save lost a race; rerun"}
    # Verify the record reads back sticky before touching the legacy config.
    _source2, lib2 = await _load_library(int(config.guild_id))
    saved = lib2["published"].get(post_key)
    if not isinstance(saved, dict) or not post_is_sticky(saved):
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "failed: read-back did not show the sticky record"}
    if poll is not None and saved.get("poll") != record.get("poll"):
        return {"channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "failed: read-back poll differs; legacy config left enabled"}
    await set_sticky_enabled(int(config.channel_id), False,
                             actor_id=config.updated_by)
    return {"channel_id": config.channel_id, "guild_id": config.guild_id,
            "status": "migrated", "post_key": post_key,
            "kind": "poll" if poll else config.mode}


async def _run(*, apply: bool) -> dict:
    stickies = await list_stickies()
    results = []
    for config in stickies:
        if not config.enabled:
            results.append({
                "channel_id": config.channel_id, "guild_id": config.guild_id,
                "status": "left_disabled_inert", "mode": config.mode,
            })
            continue
        results.append(await _migrate_one(config, apply=apply))
    return {"mode": "apply" if apply else "dry_run", "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.apply and args.confirm != "MIGRATE_STICKIES":
        raise SystemExit("Refusing to apply without --confirm MIGRATE_STICKIES")
    print(json.dumps(asyncio.run(_run(apply=bool(args.apply))), indent=2))


if __name__ == "__main__":
    main()
