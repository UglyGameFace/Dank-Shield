"""Isolated PostgreSQL *shadow* repository for imported guild settings.

No monkey patches, no runtime routing, no writes to Supabase, no changes to
existing voice tables. This is a full-fidelity comparison store, not a live
replacement for the canonical guild-config service.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

from .core_data_stage import canonical_json

MAX_SHADOW_ROWS_PER_RUN = 500
MAX_SHADOW_BYTES_PER_RUN = 12 * 1024 * 1024

_STAGED_ROWS_SQL = """
select source_pk, row_payload, payload_sha256, payload_bytes
from dank_migration.staged_records
where source_table = 'guild_configs'
order by record_key
limit %s
"""
_SHADOW_INSERT_SQL = """
insert into dank_core.guild_config_shadow
    (guild_id, source_pk, raw_row, source_sha256)
values (%s, %s, %s, %s)
on conflict (guild_id) do nothing
returning source_sha256
"""
_SHADOW_SELECT_SQL = """
select raw_row,source_sha256
from dank_core.guild_config_shadow
where guild_id = %s
"""


def _safe_guild_id(value: Any) -> str:
    clean = str(value or "").strip()
    if (
        not clean or len(clean) > 20 or not clean.isascii()
        or not clean.isdecimal() or clean[0] == "0"
    ):
        raise ValueError("invalid guild ID in staged configuration")
    return clean


def _payload_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_staged(
    source_pk: Any, raw: Any, source_sha: str, payload_bytes: int
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not isinstance(source_pk, Mapping) or not isinstance(raw, Mapping):
        raise RuntimeError("staged configuration has invalid JSON shape")
    if set(source_pk) != {"id"} or source_pk["id"] != raw.get("id"):
        raise RuntimeError("staged configuration source primary key mismatch")
    guild_id = _safe_guild_id(raw.get("guild_id"))
    actual_digest = _payload_hash(raw)
    if actual_digest != source_sha:
        raise RuntimeError("staged configuration checksum mismatch")
    expected_bytes = len(canonical_json(raw).encode("utf-8"))
    if expected_bytes != payload_bytes or expected_bytes > 2 * 1024 * 1024:
        raise RuntimeError("staged configuration size mismatch")
    return guild_id, dict(source_pk), dict(raw)


def _require_shadow_schema(cursor: Any) -> None:
    cursor.execute(
        "select to_regclass('dank_migration.staged_records'),"
        "to_regclass('dank_core.guild_config_shadow')"
    )
    found = cursor.fetchone()
    if not found or found[0] is None or found[1] is None:
        raise RuntimeError("explicit private PostgreSQL schema migrations required")


def _read_staged(cursor: Any) -> list[tuple[str, dict, dict, str]]:
    _require_shadow_schema(cursor)
    cursor.execute(_STAGED_ROWS_SQL, (MAX_SHADOW_ROWS_PER_RUN + 1,))
    rows = cursor.fetchall()
    if not rows:
        raise RuntimeError("no staged guild configuration records to import")
    if len(rows) > MAX_SHADOW_ROWS_PER_RUN:
        raise RuntimeError("staged guild count exceeds first-slice safety limit")
    records = []
    seen: set[str] = set()
    total = 0
    for source_pk, payload, digest, encoded_size in rows:
        guild_id, pk, raw = _validate_staged(
            source_pk, payload, str(digest), int(encoded_size)
        )
        if guild_id in seen:
            raise RuntimeError("multiple staged configs reference the same guild")
        seen.add(guild_id)
        total += int(encoded_size) + 256
        if total > MAX_SHADOW_BYTES_PER_RUN:
            raise RuntimeError("guild settings shadow exceeds memory budget")
        records.append((guild_id, pk, raw, str(digest)))
    return records


def inspect_staged_config(cursor: Any) -> dict[str, Any]:
    """Read-only counts/checksums for private staging, never returns row data."""
    records = _read_staged(cursor)
    digest = sha256()
    for guild_id, _pk, _row, raw_digest in sorted(records):
        digest.update(guild_id.encode("ascii"))
        digest.update(raw_digest.encode("ascii"))
    return {
        "guild_count": len(records),
        "source_digest": digest.hexdigest(),
        "status": "shadow_import_not_verified",
    }


def import_config_shadow(
    connection: Any, *, expected_database: str, disk_budget_mb: int
) -> dict[str, Any]:
    """Explicit manual import, atomically checked against *staged* snapshots.

    This does not prove that the original live Supabase state is unchanged.
    Existing differing shadow configs are NEVER silently overwritten.
    """
    if not expected_database or not expected_database.isidentifier():
        raise ValueError("expected database identity is required")
    if disk_budget_mb <= 0:
        raise ValueError("verified disk allowance required")
    from psycopg.types.json import Jsonb

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("select current_database(),pg_database_size(current_database())")
            actual_database, allocated = cursor.fetchone()
            if actual_database != expected_database:
                raise RuntimeError("unexpected PostgreSQL target database")
            records = _read_staged(cursor)
            written = sum(len(canonical_json(row).encode("utf-8")) + 256
                          for _gid, _pk, row, _hash in records)
            ceiling = disk_budget_mb * 1024 * 1024 * 0.70
            if int(allocated) + 4 * written >= ceiling:
                raise RuntimeError("insufficient database disk headroom")
            for guild_id, pk, raw, digest in records:
                cursor.execute(
                    _SHADOW_INSERT_SQL, (guild_id, Jsonb(pk), Jsonb(raw), digest)
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "select source_sha256 from dank_core.guild_config_shadow "
                        "where guild_id=%s", (guild_id,)
                    )
                    existing = cursor.fetchone()
                    if not existing or existing[0] != digest:
                        raise RuntimeError("existing shadow row differs; do not overwrite")
                elif inserted[0] != digest:
                    raise RuntimeError("unexpected imported row checksum")
            cursor.execute(
                "select guild_id, source_sha256 from "
                "dank_core.guild_config_shadow order by guild_id"
            )
            actual = cursor.fetchall()
            expected = sorted((guild, digest)
                              for guild, _pk, _raw, digest in records)
            if actual != expected:
                raise RuntimeError("shadow row count or checksum mismatch")
            cursor.execute("select pg_database_size(current_database())")
            if int(cursor.fetchone()[0]) >= ceiling:
                raise RuntimeError("PostgreSQL shadow exhausted safe disk headroom")
    return {
        "guild_count": len(records),
        "result": "shadow_verified_only",
        "live_guild_storage_changed": False,
    }


def read_shadow_config(connection: Any, guild_id: int) -> dict[str, Any] | None:
    """Read and validate one shadow copy. Not called by production guild_config."""
    clean_id = _safe_guild_id(guild_id)
    with connection.cursor() as cursor:
        cursor.execute(_SHADOW_SELECT_SQL, (clean_id,))
        result = cursor.fetchone()
    if result is None:
        return None
    raw, digest = result
    if (not isinstance(raw, Mapping)
            or _safe_guild_id(raw.get("guild_id")) != clean_id
            or _payload_hash(raw) != digest):
        raise RuntimeError("guild config shadow integrity check failed")
    return dict(raw)
