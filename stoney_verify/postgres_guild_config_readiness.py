"""Read-only, fail-closed checks for staged Discloud guild configuration state.

No production routing, schema changes, or database writes. The caller provides
an already-opened PostgreSQL connection with read-only permissions.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from .core_data_stage import canonical_json
from .postgres_guild_config_repo import _guild_id


def inspect_guild_config_state(connection: Any, *, expected_guilds: int) -> dict[str, Any]:
    if not 1 <= expected_guilds <= 500:
        raise ValueError("expected_guilds must be between 1 and 500")
    with connection.cursor() as cursor:
        cursor.execute(
            "select guild_id, raw_row, source_sha256 from "
            "dank_core.guild_config_shadow order by guild_id limit %s",
            (expected_guilds + 1,),
        )
        shadow = cursor.fetchall()
        cursor.execute(
            "select guild_id, row_payload, revision from "
            "dank_core.guild_config_state order by guild_id limit %s",
            (expected_guilds + 1,),
        )
        state = cursor.fetchall()
    errors: list[str] = []
    if len(shadow) != expected_guilds:
        errors.append("shadow_count_mismatch")
    if len(state) != expected_guilds:
        errors.append("state_count_mismatch")
    shadow_by_id = {}
    for gid, payload, digest in shadow:
        try:
            gid = _guild_id(gid)
            if gid in shadow_by_id or _guild_id(payload["guild_id"]) != gid:
                raise ValueError("duplicate or mismatched guild ID")
            if sha256(canonical_json(payload).encode("utf-8")).hexdigest() != digest:
                raise ValueError("shadow hash mismatch")
            shadow_by_id[gid] = payload
        except (TypeError, KeyError, ValueError):
            errors.append("invalid_shadow_row")
    seen = set()
    for gid, payload, revision in state:
        try:
            gid = _guild_id(gid)
            if gid in seen or _guild_id(payload["guild_id"]) != gid:
                raise ValueError("duplicate or mismatched guild ID")
            seen.add(gid)
            if gid not in shadow_by_id or canonical_json(payload) != canonical_json(shadow_by_id[gid]):
                errors.append("state_payload_mismatch")
            if not isinstance(revision, int) or revision < 0:
                errors.append("invalid_revision")
        except (TypeError, KeyError, ValueError):
            errors.append("invalid_state_row")
    if seen != set(shadow_by_id):
        errors.append("guild_set_mismatch")
    return {
        "guild_configs_verified": not errors,
        "production_cutover_authorized": False,
        "shadow_rows": len(shadow),
        "state_rows": len(state),
        "errors": sorted(set(errors)),
    }
