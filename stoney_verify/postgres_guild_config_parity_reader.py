"""Explicit, read-only PG guild-config comparison for migration rehearsals.

Not connected to production routing. Never logs row values or credentials.
A mismatch is a hard failure, not permission to fall back silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from .core_data_stage import canonical_json
from .postgres_guild_config_repo import GuildConfigPgRepository, _guild_id


@dataclass(frozen=True)
class GuildConfigParity:
    guild_id: str
    matches: bool
    reason: str
    pg_revision: int | None = None


def _digest(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(dict(row)).encode("utf-8")).hexdigest()


def compare_guild_config(
    connection: Any, guild_id: Any, source_row: Mapping[str, Any] | None
) -> GuildConfigParity:
    """Compare a trusted source snapshot against a PG state row.

    The caller supplies an independently retrieved source row. This function
    never queries or mutates Supabase, and performs no PG writes.
    """
    gid = _guild_id(guild_id)
    if source_row is None:
        return GuildConfigParity(gid, False, "source_missing")
    if not isinstance(source_row, Mapping) or str(source_row.get("guild_id")) != gid:
        return GuildConfigParity(gid, False, "source_identity_mismatch")
    pg = GuildConfigPgRepository(connection).read(gid)
    if pg is None:
        return GuildConfigParity(gid, False, "postgres_missing")
    if _digest(source_row) != _digest(pg.row):
        return GuildConfigParity(gid, False, "payload_mismatch", pg.revision)
    return GuildConfigParity(gid, True, "match", pg.revision)


def require_guild_config_parity(
    connection: Any, guild_id: Any, source_row: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return PG row only when independently supplied source data matches.

    Does not authorize a production cutover: other tables, writes, rollback
    and exact-head CI must still pass independently.
    """
    parity = compare_guild_config(connection, guild_id, source_row)
    if not parity.matches:
        raise RuntimeError(f"guild config parity gate failed: {parity.reason}")
    row = GuildConfigPgRepository(connection).read(parity.guild_id)
    if row is None or row.revision != parity.pg_revision:
        raise RuntimeError("guild config changed during parity check")
    if _digest(row.row) != _digest(source_row):
        raise RuntimeError("guild config changed during parity check")
    return row.row
