"""Opt-in normalized Embed Studio persistence. Never writes the legacy guild config.

The old editor remains canonical; this module changes only its storage adapter.
Production defaults to the legacy CAS path until a deliberate, verified cutover.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any, Mapping
from urllib.parse import urlsplit

from .embed_studio_core import STUDIO_KEY, StudioValidationError, library_payload, parse_library

LIBRARY_TABLE = "dank_embed_studio_libraries"
ITEM_TABLE = "dank_embed_studio_items"
WRITE_RPC = "dank_embed_studio_write"
SECTIONS = ("drafts", "templates", "themes", "media", "published")
EXPECTED_SUPABASE_REF = "viamriafkbqhquvrsnua"
FLAG = "DANK_EMBED_STUDIO_V2_ENABLED"


class StudioV2Unavailable(StudioValidationError):
    """Fail closed: never overwrite V1 after a partially enabled V2 cutover."""


@dataclass(frozen=True)
class StudioV2Source:
    guild_id: int
    expected_revision: int
    legacy_snapshot: dict[str, Any]


def storage_v2_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


def verify_project(environ: Mapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    url = str(env.get("SUPABASE_URL") or "").strip()
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc.lower()
            != f"{EXPECTED_SUPABASE_REF}.supabase.co"):
        raise StudioV2Unavailable(
            "Studio v2 is pinned to the new DankShield Supabase project. "
            "The configured project does not match; nothing was changed."
        )


def _client() -> Any:
    from .globals import get_supabase
    db = get_supabase()
    if db is None:
        raise StudioV2Unavailable("Studio v2 database is unavailable. Edits remain private.")
    return db


def _rows(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "data", None)
    if not isinstance(data, list) or any(not isinstance(row, Mapping) for row in data):
        raise StudioV2Unavailable("Studio v2 returned an invalid database response.")
    return [dict(row) for row in data]


def _read_sync(guild_id: int, legacy: Mapping[str, Any]) -> tuple[StudioV2Source, dict[str, Any]]:
    verify_project()
    db = _client()
    gid = str(guild_id)
    head = _rows(
        db.table(LIBRARY_TABLE).select("revision,legacy_snapshot")
          .eq("guild_id", gid).limit(1).execute()
    )
    legacy_raw = dict(legacy.get(STUDIO_KEY) or {}) if isinstance(legacy.get(STUDIO_KEY), Mapping) else {}
    if not head:
        if legacy_raw:
            raise StudioV2Unavailable(
                "Saved legacy Embed Studio records need owner-approved migration before enabling v2."
            )
        return StudioV2Source(guild_id, -1, {}), parse_library(None)
    meta = head[0]
    snapshot = meta.get("legacy_snapshot")
    if not isinstance(snapshot, Mapping) or dict(snapshot) != legacy_raw:
        raise StudioV2Unavailable(
            "Legacy Embed Studio settings changed since the v2 snapshot. "
            "Stop and reconcile; no data was overwritten."
        )
    try:
        version = int(meta["revision"])
    except (ValueError, TypeError, KeyError) as exc:
        raise StudioV2Unavailable("Invalid v2 revision.") from exc
    if version < 0:
        raise StudioV2Unavailable("Invalid v2 revision.")
    rows = _rows(
        db.table(ITEM_TABLE).select("section,item_id,payload")
          .eq("guild_id", gid).limit(201).execute()
    )
    if len(rows) > 200:
        raise StudioV2Unavailable("Studio v2 exceeds the bounded reader; no changes attempted.")
    lib: dict[str, Any] = {"revision": version, **{section: {} for section in SECTIONS}}
    for row in rows:
        section, ident, payload = row.get("section"), row.get("item_id"), row.get("payload")
        if (section not in SECTIONS or not isinstance(ident, str) or not ident
                or len(ident) > 100 or not isinstance(payload, Mapping)
                or ident in lib[section]):
            raise StudioV2Unavailable("Studio v2 contains invalid or duplicate records.")
        lib[section][ident] = dict(payload)
    checked = parse_library({STUDIO_KEY: lib})
    # parse_library truncates for the *legacy* UI size constraints; fail closed
    # rather than silently erasing previously recorded v2 data on save.
    if any(len(checked[section]) != len(lib[section]) for section in SECTIONS):
        raise StudioV2Unavailable("Studio v2 exceeds the current editor's supported item counts.")
    return StudioV2Source(guild_id, version, dict(snapshot)), checked


async def read_library(
    guild_id: int, legacy: Mapping[str, Any],
) -> tuple[StudioV2Source, dict[str, Any]]:
    if int(guild_id) <= 0:
        raise StudioV2Unavailable("Open Embed Studio in a real server.")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_read_sync, int(guild_id), legacy), timeout=12
        )
    except (StudioValidationError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise StudioV2Unavailable(
            f"Studio v2 read unavailable ({type(exc).__name__}). No legacy writes attempted."
        ) from exc


def _write_sync(source: StudioV2Source, lib: Mapping[str, Any]) -> bool:
    verify_project()
    if int(source.guild_id) <= 0:
        raise StudioV2Unavailable("Invalid Studio v2 server.")
    # Preserve exactly the canonical existing model validation, including
    # the published-role controls and a bounded overall serialized payload.
    validated = library_payload(lib)
    parts = {section: validated[section] for section in SECTIONS}
    db = _client()
    response = db.rpc(WRITE_RPC, {
        "p_guild_id": str(source.guild_id),
        "p_expected_revision": source.expected_revision,
        "p_sections": parts,
        "p_legacy_snapshot": source.legacy_snapshot,
    }).execute()
    data = getattr(response, "data", None)
    if not isinstance(data, Mapping) or type(data.get("applied")) is not bool:
        raise StudioV2Unavailable("Studio v2 write returned no atomic confirmation.")
    return bool(data["applied"])


async def save_library(source: StudioV2Source, lib: Mapping[str, Any]) -> bool:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_write_sync, source, lib), timeout=15)
    except (StudioValidationError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise StudioV2Unavailable(
            f"Studio v2 save unavailable ({type(exc).__name__}). The draft is still only local."
        ) from exc


async def seed_legacy(guild_id: int, legacy: Mapping[str, Any]) -> dict[str, Any]:
    """Manual, one-server, additive copy. Never deletes the source v1 blob.

    Requires the owner to run the explicit staging tool; NOT called from
    ordinary bot startup, editor opening, or background workers.
    """
    verify_project()
    if int(guild_id) <= 0:
        raise StudioV2Unavailable("Invalid guild.")
    source = dict(legacy.get(STUDIO_KEY) or {}) if isinstance(legacy.get(STUDIO_KEY), Mapping) else {}
    library = parse_library({STUDIO_KEY: source})
    checked = library_payload(library)
    for section in SECTIONS:
        original = source.get(section, {})
        if original is not None and (
            not isinstance(original, Mapping) or len(original) != len(checked[section])
        ):
            raise StudioV2Unavailable("Legacy library has invalid or oversized records; repair before migration.")
    initial = StudioV2Source(int(guild_id), -1, source)
    success = await save_library(initial, library)
    if not success:
        raise StudioV2Unavailable("V2 data already exists. Do not overwrite or merge it automatically.")
    reread, data = await read_library(guild_id, legacy)
    if reread.expected_revision != 1 or any(
        data[section] != checked[section] for section in SECTIONS
    ):
        raise StudioV2Unavailable("Post-copy v2 parity mismatch. Leave legacy records untouched.")
    return {"copied": True, "sections": {s: len(data[s]) for s in SECTIONS},
            "v2_revision": reread.expected_revision, "source_preserved": True}
