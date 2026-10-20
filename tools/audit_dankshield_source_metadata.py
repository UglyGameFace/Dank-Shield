"""Private, bounded, read-only schema/source inventory for Dank Shield cutover.

Runs only by manual GitHub Actions dispatch against the precisely pinned old
shared Supabase project. Never outputs row data, IDs, JSON payloads or DB URLs.
Do not treat this metadata check as a complete row digest or cutover approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

from stoney_verify.source_pg_backup import validate_source_dsn

# Same exact production SOURCE_REF as tools/aiven_guild_config_stage.py.
# Test contract pins this match so an unrelated project never gets audited.
SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
CONFIRMATION = "AUDIT_DANKSHIELD_SOURCE_METADATA_ONLY"
ROW_PROBE_CAP = 2000

GROUPS: dict[str, tuple[str, ...]] = {
    "moderation_and_security": (
        "warns", "raid_events", "audit_events", "audit_logs",
        "staff_metrics", "guild_security_actor_reputation",
        "guild_security_settings", "dank_invite_block_stats",
    ),
    "community_hub": (
        "dank_community_hub_settings", "dank_community_sessions",
        "dank_community_session_members", "dank_community_managed_resources",
        "dank_community_session_events", "dank_community_metrics_hourly",
        "dank_community_game_metrics_hourly", "dank_community_notification_prefs",
        "dank_community_notification_state", "dank_community_availability",
        "dank_community_user_blocks", "dank_community_reports",
        "dank_community_events", "dank_community_event_attendees",
        "dank_community_partner_links", "dank_community_hub_link_codes",
    ),
    "profile_privacy": (
        "dank_profile_users", "dank_profile_guild_settings",
        "dank_live_profile_cards",
    ),
    "cinema_library": (
        "dank_cinema_users", "dank_cinema_user_media",
        "dank_cinema_feed_discoveries", "dank_cinema_notifications",
        "dank_cinema_feed_rules", "dank_cinema_user_feed_discoveries",
        "dank_cinema_feed_source_health", "dank_cinema_watch_sessions",
        "dank_cinema_lists", "dank_cinema_list_items",
    ),
    "unreconciled_runtime": (
        "ticket_messages", "ticket_counters", "ticket_macros",
        "ticket_panel_categories", "ticket_automation_settings",
        "ticket_automation_state", "dank_quiet_notices",
        "dank_stickies", "dank_sticky_polls", "bot_operation_jobs",
    ),
}

# Only missing, feature-critical source definitions expose sanitized SQL
# *metadata* (not defaults, constraint check values, or any row contents).
DETAIL_TABLES = frozenset((
    "warns", "raid_events", "audit_events", "audit_logs", "staff_metrics",
))
EXPECTED_TABLES = tuple(table for tables in GROUPS.values() for table in tables)


def _safe_table_contract() -> None:
    import re
    if len(set(EXPECTED_TABLES)) != len(EXPECTED_TABLES):
        raise ValueError("duplicate source audit table")
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", t)
           for t in EXPECTED_TABLES):
        raise ValueError("invalid pinned source table")
    if not DETAIL_TABLES.issubset(set(EXPECTED_TABLES)):
        raise ValueError("unapproved table metadata detail")


def _columns(connection: Any, oid: int) -> list[dict[str, Any]]:
    # No row values, column defaults, or CHECK-expression string literals.
    records = connection.execute(
        """
        select a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
               a.attnotnull, a.attidentity <> '', a.attgenerated <> '',
               d.adbin is not null
          from pg_catalog.pg_attribute a
          left join pg_catalog.pg_attrdef d
            on d.adrelid=a.attrelid and d.adnum=a.attnum
         where a.attrelid=%s and a.attnum>0 and not a.attisdropped
         order by a.attnum
        """, (oid,)
    ).fetchall()
    return [
        {"name": str(name), "type": str(datatype),
         "not_null": bool(required), "identity": bool(identity),
         "generated": bool(generated), "has_default": bool(has_default)}
        for name, datatype, required, identity, generated, has_default in records
    ]


def _key_contract(connection: Any, oid: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        select c.contype, c.conname, coalesce(array_agg(a.attname order by k.ordinality)
               filter (where a.attname is not null), array[]::text[])
          from pg_catalog.pg_constraint c
          left join lateral unnest(c.conkey) with ordinality as k(attnum, ordinality)
            on true
          left join pg_catalog.pg_attribute a
            on a.attrelid=c.conrelid and a.attnum=k.attnum
         where c.conrelid=%s and c.contype in ('p','u','f')
         group by c.oid, c.contype, c.conname
         order by c.contype, c.conname
        """, (oid,)
    ).fetchall()
    return [
        {"kind": str(kind), "name": str(name), "columns": list(columns or [])}
        for kind, name, columns in rows
    ]


def collect_source_metadata(connection: Any) -> dict[str, Any]:
    from psycopg import sql

    _safe_table_contract()
    groups: dict[str, dict[str, Any]] = {}
    for group, names in GROUPS.items():
        entries: dict[str, Any] = {}
        for name in names:
            found = connection.execute(
                """
                select c.oid
                  from pg_catalog.pg_class c
                  join pg_catalog.pg_namespace n on n.oid=c.relnamespace
                 where n.nspname='public' and c.relname=%s
                   and c.relkind in ('r','p')
                """, (name,)
            ).fetchone()
            if not found:
                entries[name] = {"exists": False}
                continue
            oid = int(found[0])
            columns = _columns(connection, oid)
            keys = _key_contract(connection, oid)
            # Bounded existence/count estimate: never scan an entire huge log.
            # We intentionally DO NOT read rows, user IDs, text or JSONB values.
            count = int(connection.execute(
                sql.SQL("select count(*) from (select 1 from {}.{} limit %s) as probe")
                   .format(sql.Identifier("public"), sql.Identifier(name)),
                (ROW_PROBE_CAP + 1,),
            ).fetchone()[0])
            contract = {"columns": columns, "keys": keys}
            signature = hashlib.sha256(json.dumps(
                contract, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            item: dict[str, Any] = {
                "exists": True,
                "sampled_rows": min(count, ROW_PROBE_CAP),
                "count_at_least_cap": count > ROW_PROBE_CAP,
                "column_count": len(columns),
                "schema_signature_sha256": signature,
            }
            if name in DETAIL_TABLES:
                item["sanitized_columns"] = columns
                item["key_constraints"] = keys
            entries[name] = item
        groups[group] = entries

    return {
        "version": 1,
        "scope": "pinned_shared_source_read_only",
        "row_probe_cap": ROW_PROBE_CAP,
        "not_final_source_parity": True,
        "no_production_cutover_authorized": True,
        "groups": groups,
    }


def run_audit() -> dict[str, Any]:
    _safe_table_contract()
    if os.environ.get("DANK_SOURCE_AUDIT_CONFIRMATION") != CONFIRMATION:
        raise ValueError("explicit read-only metadata confirmation required")
    params = validate_source_dsn(
        os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
    )
    import psycopg
    with psycopg.connect(
        host=params["PGHOST"],
        port=int(params["PGPORT"]),
        user=params["PGUSER"],
        password=params["PGPASSWORD"],
        dbname=params["PGDATABASE"],
        sslmode="require", connect_timeout=10,
        application_name="dankshield_fresh_source_readonly_inventory",
        options=(
            "-c default_transaction_read_only=on "
            "-c statement_timeout=4000 -c lock_timeout=1000 "
            "-c idle_in_transaction_session_timeout=45000"
        ),
        autocommit=True,
    ) as connection:
        with connection.transaction():
            connection.execute(
                "set transaction isolation level repeatable read, read only"
            )
            return collect_source_metadata(connection)


def main() -> int:
    try:
        data = run_audit()
        # GitHub logs contain only pre-approved object metadata and capped
        # counts. Never upload an artifact or print driver exception strings.
        print(json.dumps(data, sort_keys=True, indent=2))
        print("READ-ONLY SOURCE METADATA AUDIT: COMPLETE; CUTOVER: NOT AUTHORIZED")
        return 0
    except Exception:
        print("SOURCE METADATA AUDIT FAILED (details and credentials withheld)",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
