"""Read-only, owner-only final-delta audit for the offline dedicated Supabase.

No updates, deletes, notification delivery, session resolution, token writes,
source backfills or Discloud changes. This produces an advisory only and cannot
authorize a cutover without a frozen writer, restore drill and explicit owner
approval. Omitted stale or shared audit tables are documented separately.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
import sys
from typing import Any

from stoney_verify.source_pg_backup import validate_source_dsn

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
DESTINATION_REF = "viamriafkbqhquvrsnua"
TABLES = (
    "guild_configs", "guild_members", "guild_roles", "guild_role_rules",
    "guild_security_settings", "guild_security_actor_reputation",
    "member_activity_ledger", "member_activity_tracker_state",
    "member_activity_scan_locks", "member_joins", "member_events",
    "dank_invite_block_stats", "ticket_categories", "ticket_counters",
    "ticket_form_responses", "ticket_messages", "ticket_notes",
    "ticket_panel_presets", "ticket_panel_rules", "ticket_panels", "tickets",
    "warns", "dank_profile_users", "dank_profile_guild_settings",
    "dank_community_hub_settings", "staff_metrics", "dank_cinema_users",
    "dank_cinema_user_media", "dank_community_metrics_hourly",
    "dank_community_game_metrics_hourly", "dank_stickies",
    "dank_quiet_notices", "staff_action_logs", "activity_feed_events",
)
ALLOW_DESTINATION_ONLY_COLUMNS = {
    "staff_metrics": frozenset({"created_at", "updated_at"}),
}
# Old staff KPIs permit NULL; dedicated candidate intentionally requires
# these five fields. This exception is allowed only if no source row has NULL.
ALLOW_STRICTER_TARGET_NOT_NULL = {
    "staff_metrics": frozenset({
        "staff_name", "tickets_handled", "approvals",
        "denials", "avg_response_minutes",
    }),
}
LIVE_SESSION_STATES = ("PENDING", "STAFF_ACCEPTED", "IN_PROGRESS", "ACTIVE")
ACTIVE_OPERATION_STATES = ("queued", "running", "waiting_rate_limit")
ACTIVE_COMMAND_STATES = ("pending", "processing")
TERMINAL_COMMUNITY_STATES = (
    "cleaned", "ended", "expired", "closed", "cancelled", "archived",
    "completed", "canceled",
)
MAX_ROW_BYTES = 1024 * 1024
MAX_TABLE_BYTES = 150 * 1024 * 1024
MAX_TOTAL_BYTES = 350 * 1024 * 1024


def connection_params(parsed: dict[str, str]) -> dict[str, Any]:
    return {
        "host": parsed["PGHOST"], "port": int(parsed["PGPORT"]),
        "user": parsed["PGUSER"], "password": parsed["PGPASSWORD"],
        "dbname": parsed["PGDATABASE"], "sslmode": "require",
        "connect_timeout": 10, "autocommit": True,
        "application_name": "dankshield_offline_cutover_delta_audit",
        "options": "-c default_transaction_read_only=on "
                   "-c statement_timeout=150000 -c lock_timeout=2000 "
                   "-c idle_in_transaction_session_timeout=250000",
    }


def column_contract(conn, table: str) -> tuple[tuple[str, str, bool], ...]:
    rows = conn.execute(
        """
        select a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
               a.attnotnull
          from pg_catalog.pg_attribute a
         where a.attrelid=%s::regclass and a.attnum>0
           and not a.attisdropped
         order by a.attnum
        """,
        (f"public.{table}",),
    ).fetchall()
    return tuple((str(name), str(typ), bool(required))
                 for name, typ, required in rows)


def primary_key(conn, table: str) -> tuple[str, ...]:
    rows = conn.execute(
        """
        select a.attname from pg_constraint c
        join lateral unnest(c.conkey) with ordinality k(num,ordinal) on true
        join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k.num
        where c.conrelid=%s::regclass and c.contype='p'
        order by k.ordinal
        """, (f"public.{table}",),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def table_contract(source, target, table: str) -> tuple[str, ...]:
    original = column_contract(source, table)
    dedicated = column_contract(target, table)
    excluded = ALLOW_DESTINATION_ONLY_COLUMNS.get(table, frozenset())
    without_extra = tuple(row for row in dedicated if row[0] not in excluded)
    # Deliberate schema exception only for target-only staff timestamps.
    strict = ALLOW_STRICTER_TARGET_NOT_NULL.get(table, frozenset())
    original_names = {name for name, _, _ in original}
    if excluded and ({row[0] for row in dedicated} - original_names) != excluded:
        raise RuntimeError(f"{table}: unexpected target-only columns")
    normalized = tuple(
        (name, typ, True if name in strict else required)
        for name, typ, required in original
    )
    if not original or without_extra != normalized:
        raise RuntimeError(f"{table}: source/destination schema differs")
    # Tightening NULL -> NOT NULL is only safe if source has no violating data.
    if strict and table == "staff_metrics":
        bad = source.execute(
            "select count(*) from public.staff_metrics where "
            "staff_name is null or tickets_handled is null or "
            "approvals is null or denials is null or avg_response_minutes is null"
        ).fetchone()[0]
        if int(bad):
            raise RuntimeError("staff_metrics: unsafe source nulls")
    pk1, pk2 = primary_key(source, table), primary_key(target, table)
    if not pk1 or pk1 != pk2:
        raise RuntimeError(f"{table}: source/destination primary key differs")
    if not target.execute(
        "select relrowsecurity from pg_class where oid=%s::regclass",
        (f"public.{table}",),
    ).fetchone()[0]:
        raise RuntimeError(f"{table}: dedicated private RLS absent")
    for op in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        for role in ("anon", "authenticated"):
            if target.execute(
                "select has_table_privilege(%s,%s,%s)",
                (role, f"public.{table}", op),
            ).fetchone()[0]:
                raise RuntimeError(f"{table}: private data role exposure")
        if not target.execute(
            "select has_table_privilege('service_role',%s,%s)",
            (f"public.{table}", op),
        ).fetchone()[0]:
            raise RuntimeError(f"{table}: backend CRUD unavailable")
    return pk1


def streamed_fingerprint(conn, table: str, pk: tuple[str, ...]) -> tuple[int, str, int]:
    from psycopg import sql
    excluded = ALLOW_DESTINATION_ONLY_COLUMNS.get(table, frozenset())
    expression = sql.SQL("to_jsonb(x)")
    for column in sorted(excluded):
        expression = sql.SQL("({} - {})").format(expression, sql.Literal(column))
    query = sql.SQL("select {}::text from public.{} x order by {}").format(
        expression, sql.Identifier(table),
        sql.SQL(",").join(sql.SQL("x.{}").format(sql.Identifier(k)) for k in pk),
    )
    digest = sha256()
    count = size = 0
    # Database-side server cursor: never accumulate full historical evidence
    # or log original Discord/user/actor IDs and moderation reasons.
    with conn.cursor(name="delta_" + table) as cursor:
        cursor.itersize = 100
        cursor.execute(query)
        while True:
            chunk = cursor.fetchmany(100)
            if not chunk:
                break
            for (payload,) in chunk:
                raw = payload.encode("utf-8")
                if len(raw) > MAX_ROW_BYTES:
                    raise RuntimeError(f"{table}: individual row too large")
                size += len(raw)
                if size > MAX_TABLE_BYTES:
                    raise RuntimeError(f"{table}: table exceeds audit byte cap")
                count += 1
                if count > 100_000:
                    raise RuntimeError(f"{table}: row count exceeds audit cap")
                # Framing prevents concatenation ambiguity.
                digest.update(len(raw).to_bytes(4, "big"))
                digest.update(raw)
    return count, digest.hexdigest(), size


def live_work_gates(source, target) -> dict[str, int]:
    q = """
    select
      (select count(*) from public.bot_operation_jobs
       where status in ('queued','running','waiting_rate_limit')),
      (select count(*) from public.bot_commands
       where status in ('pending','processing')),
      (select count(*) from public.verification_tokens
       where expires_at > now() and not used),
      (select count(*) from public.vc_verify_sessions
       where status in ('PENDING','STAFF_ACCEPTED','IN_PROGRESS','ACTIVE')),
      (select count(*) from public.dank_cinema_watch_sessions
       where last_seen_at > now()-interval '90 minutes'),
      (select count(*) from public.dank_community_sessions
       where state not in ('cleaned','ended','expired','closed',
                           'cancelled','archived','completed','canceled'))
    """
    values = source.execute(q).fetchone()
    names = ("active_operation_jobs", "active_dashboard_commands",
             "live_verification_tokens", "unresolved_voice_verifications",
             "recent_cinema_sessions", "unclosed_community_sessions")
    counts = dict(zip(names, (int(x) for x in values)))
    commands, operations = target.execute(
        """select
           (select count(*) from public.bot_commands),
           (select count(*) from public.bot_operation_jobs)"""
    ).fetchone()
    counts["dedicated_dashboard_commands"] = int(commands)
    counts["dedicated_operation_jobs"] = int(operations)
    if any(value < 0 for value in counts.values()):
        raise RuntimeError("invalid live work count")
    return counts


def validate_readonly_sql_and_inputs() -> tuple[dict[str, str], dict[str, str]]:
    source = validate_source_dsn(
        os.getenv("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF,
    )
    target = validate_source_dsn(
        os.getenv("DANK_FRESH_DATABASE_URL", ""), DESTINATION_REF,
    )
    if source["PGHOST"] == target["PGHOST"] and source["PGUSER"] == target["PGUSER"]:
        raise RuntimeError("source and candidate connection collision")
    return source, target


def preview() -> dict[str, Any]:
    import psycopg
    original, new = validate_readonly_sql_and_inputs()
    compared: list[str] = []
    mismatches: list[str] = []
    total_bytes = 0
    with psycopg.connect(**connection_params(original)) as src:
        with psycopg.connect(**connection_params(new)) as dest:
            # These two snapshots are independent. Final approval additionally
            # REQUIRES the source writers to be quiesced.
            with src.transaction(), dest.transaction():
                src.execute("set transaction isolation level repeatable read, read only")
                dest.execute("set transaction isolation level repeatable read, read only")
                for table in TABLES:
                    keys = table_contract(src, dest, table)
                    a_count, a_hash, a_bytes = streamed_fingerprint(src, table, keys)
                    b_count, b_hash, b_bytes = streamed_fingerprint(dest, table, keys)
                    total_bytes += a_bytes + b_bytes
                    if total_bytes > 2 * MAX_TOTAL_BYTES:
                        raise RuntimeError("total audit payload exceeds allowed budget")
                    compared.append(table)
                    if (a_count, a_hash) != (b_count, b_hash):
                        mismatches.append(table)
                gates = live_work_gates(src, dest)
    unresolved = [label for label, count in gates.items() if count > 0]
    return {
        "result": "READ_ONLY_CANDIDATE_DELTA_AUDIT",
        "matched_tables": len(compared) - len(mismatches),
        "checked_tables": len(compared),
        "mismatched_tables": mismatches,
        "live_work_blockers": gates,
        "blocked_by": unresolved + (["record_parity"] if mismatches else []),
        "requires_source_writer_pause_and_second_audit": True,
        "requires_independent_encrypted_backup_restore": True,
        "requires_discord_canary_and_rollback_plan": True,
        "unclassified_shared_audit_history_remains_excluded": True,
        "readiness": "BLOCKED" if unresolved or mismatches else "DATA_SNAPSHOT_ONLY",
        "source_writes": 0,
        "destination_writes": 0,
        "production_cutover_authorized": False,
    }


def main() -> int:
    try:
        report = preview()
    except Exception as exc:
        # Never emit original data, exception arguments, DB credentials or SQL.
        print("READ_ONLY_CUTOVER_AUDIT_FAILED: " + type(exc).__name__,
              file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
