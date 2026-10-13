"""Safe, bounded first staging slice for Supabase -> private Discloud PG17.

This does not replace any Supabase repository and never runs automatically.
No client secrets or data values are written to logs. The target is an isolated
`dank_migration` schema, not any public/voice-bot table.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Sequence

# Explicitly mapped primary keys, verified against the source Supabase schema.
# No arbitrary user-supplied table name is allowed. This is a FIRST SLICE,
# not a full backup or an authorization to retire Supabase.
SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "guild_configs": ("id",),
    "guild_security_settings": ("guild_id",),
    "guild_role_rules": ("guild_id", "role_id"),
    "guild_roles": ("id",),
    "member_activity_ledger": ("guild_id", "user_id"),
    "member_activity_tracker_state": ("guild_id",),
    "member_cleanup_settings": ("guild_id",),
    "member_verify_timers": ("guild_id", "user_id", "timer_type"),
    "ticket_categories": ("id",),
    "ticket_panels": ("id",),
    "ticket_panel_presets": ("id",),
    "ticket_panel_rules": ("id",),
    "tickets": ("id",),
    "ticket_notes": ("id",),
    "ticket_form_responses": ("id",),
    "dank_community_hub_settings": ("guild_id",),
}
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200
DEFAULT_MAX_BYTES = 12 * 1024 * 1024
MAX_ALLOWED_BYTES = 32 * 1024 * 1024
MAX_TOTAL_ROWS = 20000


def canonical_json(value: Any) -> str:
    # PostgREST JSON is already JSON-safe (timestamps are ISO text).
    # Reject non-JSON values instead of stringifying away semantic type.
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True)
class StageRecord:
    source_table: str
    key_hash: str
    source_pk: str
    guild_id: str | None
    row_json: str
    payload_sha256: str
    accounted_bytes: int


@dataclass(frozen=True)
class SourcePlan:
    rows: tuple[StageRecord, ...]
    table_counts: Mapping[str, int]
    table_digests: Mapping[str, str]
    accounted_bytes: int

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "tables": dict(self.table_counts),
            "table_sha256": dict(self.table_digests),
            "row_count": len(self.rows),
            "accounted_bytes": self.accounted_bytes,
            "warning": (
                "Source was read through paginated REST, not a repeatable-read "
                "PostgreSQL snapshot. This staging is NOT a complete backup "
                "or sufficient to authorize live database cutover."
            ),
        }


def plan_source(
    fetch_page: Callable[[str, tuple[str, ...], int, int], Sequence[Mapping[str, Any]]],
    *,
    selected_tables: Sequence[str] | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> SourcePlan:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page size must be between 1 and 200")
    if not 1 <= max_bytes <= MAX_ALLOWED_BYTES:
        raise ValueError("source payload limit must be 1..33554432 bytes")
    selected = tuple(selected_tables) if selected_tables is not None else tuple(SOURCE_KEYS)
    if len(selected) != len(set(selected)) or any(
        table not in SOURCE_KEYS for table in selected
    ):
        raise ValueError("unknown or duplicate table in staging selection")
    if not selected:
        raise ValueError("at least one source table is required")

    collected: list[StageRecord] = []
    seen: set[tuple[str, str]] = set()
    table_counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    total_bytes = 0
    for name in selected:
        pks = SOURCE_KEYS[name]
        table_digest = sha256()
        count = 0
        while True:
            page = list(fetch_page(name, pks, count, page_size))
            if len(page) > page_size:
                raise RuntimeError("source pagination returned oversized page")
            for entry in page:
                if not isinstance(entry, Mapping):
                    raise ValueError("non-object source record")
                if any(pk not in entry or entry[pk] is None for pk in pks):
                    raise ValueError("source record missing required primary key")
                pk = canonical_json({key: entry[key] for key in pks})
                key_hash = sha256(pk.encode("utf-8")).hexdigest()
                marker = (name, key_hash)
                if marker in seen:
                    raise RuntimeError("overlapping source pagination or duplicate key")
                seen.add(marker)
                payload = canonical_json(dict(entry))
                encoded = payload.encode("utf-8")
                if len(encoded) > 2 * 1024 * 1024:
                    raise RuntimeError("single source record exceeds safe staging limit")
                digest = sha256(encoded).hexdigest()
                accounted = len(encoded) + len(pk.encode("utf-8")) + 256
                total_bytes += accounted
                if total_bytes > max_bytes:
                    raise RuntimeError("staging payload exceeds approved byte budget")
                if len(collected) >= MAX_TOTAL_ROWS:
                    raise RuntimeError("staging record ceiling exceeded")
                guild_id = entry.get("guild_id")
                collected.append(StageRecord(
                    source_table=name,
                    key_hash=key_hash,
                    source_pk=pk,
                    guild_id=str(guild_id) if guild_id is not None else None,
                    row_json=payload,
                    payload_sha256=digest,
                    accounted_bytes=accounted,
                ))
                table_digest.update(key_hash.encode("ascii"))
                table_digest.update(digest.encode("ascii"))
            count += len(page)
            if len(page) < page_size:
                break
        table_counts[name] = count
        digests[name] = table_digest.hexdigest()
    return SourcePlan(
        rows=tuple(collected),
        table_counts=table_counts,
        table_digests=digests,
        accounted_bytes=total_bytes,
    )


def rest_page_reader(client: Any) -> Callable[
    [str, tuple[str, ...], int, int], Sequence[Mapping[str, Any]]
]:
    def fetch(name: str, keys: tuple[str, ...], offset: int, size: int):
        if name not in SOURCE_KEYS or keys != SOURCE_KEYS[name]:
            raise ValueError("unapproved source read")
        query = client.table(name).select("*")
        for pk in keys:
            query = query.order(pk)
        result = query.range(offset, offset + size - 1).execute()
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            raise RuntimeError("Supabase source did not return a row list")
        return rows
    return fetch


def _stage_record(cursor: Any, record: StageRecord) -> None:
    from psycopg.types.json import Jsonb
    cursor.execute(
        """insert into dank_migration.staged_records
           (source_table, record_key, source_pk, guild_id,
            row_payload, payload_sha256, payload_bytes)
           values (%s,%s,%s,%s,%s,%s,%s)
           on conflict (source_table, record_key) do nothing
           returning payload_sha256""",
        (record.source_table, record.key_hash, Jsonb(json.loads(record.source_pk)),
         record.guild_id, Jsonb(json.loads(record.row_json)),
         record.payload_sha256, len(record.row_json.encode("utf-8"))),
    )
    row = cursor.fetchone()
    if row is not None:
        if row[0] != record.payload_sha256:
            raise RuntimeError("unexpected PostgreSQL staging digest")
        return
    cursor.execute(
        """select payload_sha256 from dank_migration.staged_records
           where source_table=%s and record_key=%s""",
        (record.source_table, record.key_hash),
    )
    existing = cursor.fetchone()
    if not existing or existing[0] != record.payload_sha256:
        raise RuntimeError("source row changed since the previous staging run")


def _verify_stage(cursor: Any, plan: SourcePlan) -> None:
    by_table: dict[str, list[StageRecord]] = {}
    for record in plan.rows:
        by_table.setdefault(record.source_table, []).append(record)
    for name in plan.table_counts:
        cursor.execute(
            """select record_key,payload_sha256 from dank_migration.staged_records
               where source_table=%s order by record_key""", (name,),
        )
        actual = cursor.fetchall()
        expected = sorted(
            (item.key_hash, item.payload_sha256)
            for item in by_table.get(name, [])
        )
        if actual != expected:
            raise RuntimeError("target staging row parity failed")


def apply_stage(
    plan: SourcePlan, connection: Any, *,
    expected_db: str, disk_budget_mb: int,
    source_reread: Callable[[], SourcePlan],
) -> dict[str, Any]:
    if not expected_db or not expected_db.isidentifier():
        raise ValueError("an expected destination database name is required")
    if disk_budget_mb <= 0:
        raise ValueError("verified disk budget required before any staging write")
    if plan.accounted_bytes > MAX_ALLOWED_BYTES:
        raise ValueError("staging is over the hard data budget")
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("select current_database(),pg_database_size(current_database())")
            actual_db, used_bytes = cursor.fetchone()
            if actual_db != expected_db:
                raise RuntimeError("destination database identity mismatch")
            allowed_bytes = disk_budget_mb * 1024 * 1024
            # Conservative headroom, accounting for indexes, TOAST and WAL.
            if int(used_bytes) + 4 * plan.accounted_bytes > int(allowed_bytes * 0.70):
                raise RuntimeError("insufficient verified headroom for staging")
            cursor.execute("select to_regclass('dank_migration.staged_records')")
            exists = cursor.fetchone()
            if not exists or exists[0] is None:
                raise RuntimeError("migration staging schema is not installed")
            for row in plan.rows:
                _stage_record(cursor, row)
            _verify_stage(cursor, plan)
            check = source_reread()
            if (check.table_counts != plan.table_counts
                    or check.table_digests != plan.table_digests):
                raise RuntimeError("source records changed during staging; rolled back")
            cursor.execute("select pg_database_size(current_database())")
            actual_used = int(cursor.fetchone()[0])
            if actual_used > allowed_bytes * 0.70:
                raise RuntimeError("PostgreSQL storage crossed staging limit")
    return {
        "staged_count": len(plan.rows),
        "tables": dict(plan.table_counts),
        "note": "Staging-only; no runtime repositories or voice tables were changed.",
    }
