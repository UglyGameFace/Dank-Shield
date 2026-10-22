"""Controlled OFFLINE catch-up for the 34 approved Dank Shield durable tables.

The ORIGINAL Supabase project remains read-only from this process, including
during an owner-confirmed stage. No automatic deployment, Discord actions,
source deletes, source trigger changes or production credential rotation.

Mode=preview is the default and runs read-only against BOTH projects.
Mode=stage is a manual, owner-only, offline-only candidate transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

from stoney_verify.source_pg_backup import validate_source_dsn
from tools import audit_dankshield_cutover_delta as audit

CONFIRM = "RECONCILE_OFFLINE_DANKSHIELD_ONLY"
FROZEN = "I_PAUSED_ALL_SOURCE_WRITERS"
MAX_CHANGED_ROWS = 5000
MAX_CHANGED_BYTES = 40 * 1024 * 1024


def validate_mode(mode: str, confirmation: str, frozen: str, receipt: str) -> str:
    if mode not in {"preview", "stage"}:
        raise ValueError("invalid_delta_mode")
    if mode == "stage":
        if confirmation != CONFIRM:
            raise ValueError("missing_offline_only_confirmation")
        if frozen != FROZEN:
            raise ValueError("source_writers_not_acknowledged_frozen")
        if len(receipt.strip()) < 4 or len(receipt) > 160:
            raise ValueError("offline_backup_receipt_required")
    return mode


def connection_params(parsed: dict[str, str], *, readonly: bool) -> dict[str, Any]:
    params = audit.connection_params(parsed)
    params["application_name"] = "dankshield_offline_final_delta_catchup"
    if not readonly:
        # NEVER pass the original DSN here. Caller independently validates
        # that the destination hostname and project reference are pinned.
        params["options"] = (
            "-c statement_timeout=180000 -c lock_timeout=4000 "
            "-c idle_in_transaction_session_timeout=300000"
        )
    return params


def approved_dsns() -> tuple[dict[str, str], dict[str, str]]:
    # The audit validator fails if either value identifies the wrong project,
    # including an accidental original==destination collision.
    return audit.validate_readonly_sql_and_inputs()


def row_query(table: str, primary_key: tuple[str, ...], *, excluded: frozenset[str]):
    from psycopg import sql

    if table not in audit.TABLES or not primary_key:
        raise ValueError("unapproved_table")
    key_columns = sql.SQL(", ").join(
        sql.SQL("x.{}::text").format(sql.Identifier(k)) for k in primary_key
    )
    order_columns = sql.SQL(", ").join(
        sql.SQL("x.{}").format(sql.Identifier(k)) for k in primary_key
    )
    expr = sql.SQL("to_jsonb(x)")
    for column in sorted(excluded):
        expr = sql.SQL("({} - {})").format(expr, sql.Literal(column))
    return sql.SQL(
        "select {}, {}::text from public.{} x order by {}"
    ).format(key_columns, expr, sql.Identifier(table), order_columns)


def load_rows(
    conn: Any, table: str, primary_key: tuple[str, ...], *, retain_payload: bool
) -> dict[tuple[str, ...], tuple[str, str | None]]:
    excluded = audit.ALLOW_DESTINATION_ONLY_COLUMNS.get(table, frozenset())
    rows: dict[tuple[str, ...], tuple[str, str | None]] = {}
    size = 0
    with conn.cursor(name="catchup_" + table) as cur:
        cur.itersize = 100
        cur.execute(row_query(table, primary_key, excluded=excluded))
        while True:
            page = cur.fetchmany(100)
            if not page:
                break
            for record in page:
                pk_values = tuple(str(v) for v in record[:-1])
                raw = str(record[-1])
                raw_size = len(raw.encode("utf-8"))
                size += raw_size
                if (raw_size > audit.MAX_ROW_BYTES
                        or size > audit.MAX_TABLE_BYTES
                        or len(rows) >= 100_000):
                    raise ValueError("bounded_table_limit_exceeded")
                if pk_values in rows:
                    raise ValueError("duplicate_primary_key")
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                rows[pk_values] = (digest, raw if retain_payload else None)
    return rows


def describe_delta(
    source: dict[tuple[str, ...], tuple[str, str | None]],
    target: dict[tuple[str, ...], tuple[str, str | None]],
) -> dict[str, Any]:
    source_keys, target_keys = set(source), set(target)
    source_only = source_keys - target_keys
    target_only = target_keys - source_keys
    changed = {
        key for key in source_keys & target_keys
        if source[key][0] != target[key][0]
    }
    return {
        "source_only": len(source_only),
        "target_only": len(target_only),
        "changed": len(changed),
        "write_keys": source_only | changed,
    }


def upsert_query(table: str, columns: tuple[str, ...], primary_key: tuple[str, ...]):
    from psycopg import sql

    if table not in audit.TABLES or not primary_key or not columns:
        raise ValueError("unapproved_table")
    if not set(primary_key).issubset(set(columns)):
        raise ValueError("primary_key_not_in_column_contract")
    update_columns = [name for name in columns if name not in primary_key]
    if not update_columns:
        raise ValueError("cannot_update_pk_only_table")
    fields = sql.SQL(", ").join(map(sql.Identifier, columns))
    selected = sql.SQL(", ").join(
        sql.SQL("r.{}").format(sql.Identifier(c)) for c in columns
    )
    conflict = sql.SQL(", ").join(map(sql.Identifier, primary_key))
    setters = sql.SQL(", ").join(
        sql.SQL("{} = excluded.{}").format(sql.Identifier(c), sql.Identifier(c))
        for c in update_columns
    )
    return sql.SQL(
        "insert into public.{} ({}) "
        "select {} from jsonb_populate_record(null::public.{}, %s::jsonb) r "
        "on conflict ({}) do update set {}"
    ).format(
        sql.Identifier(table), fields, selected, sql.Identifier(table),
        conflict, setters,
    )


def recheck_original_snapshot(
    source_params: dict[str, Any],
    first: dict[str, dict[tuple[str, ...], tuple[str, str | None]]],
    keys: dict[str, tuple[str, ...]],
) -> None:
    """Independently sample a NEW read-only snapshot before candidate commit."""
    import psycopg

    with psycopg.connect(**source_params) as source:
        with source.transaction():
            source.execute("set transaction isolation level repeatable read, read only")
            source.execute("set local timezone to 'UTC'")
            for table in audit.TABLES:
                latest = load_rows(source, table, keys[table], retain_payload=False)
                original = first[table]
                if len(latest) != len(original) or any(
                    key not in original or item[0] != original[key][0]
                    for key, item in latest.items()
                ):
                    raise RuntimeError("source_changed_during_offline_catchup")


# The OFFLINE candidate's BEFORE UPDATE trigger overwrites updated_at with
# now(), making an exact original source snapshot impossible to stage. Its
# version-history trigger would also manufacture new (excluded) history from
# the migration itself. Suspend JUST these two known candidate USER triggers
# during the guild_configs copy; restore in the same transaction before commit.
# A database failure rolls back ALTER TABLE as well as all data changes.
_CONFIG_COPY_TRIGGERS = (
    "trg_capture_guild_config_version",
    "trg_guild_configs_updated_at",
)

# Verified against the actual pinned OFFLINE candidate's pg_trigger catalog.
# Data migration is not a new Discord event. In particular, replaying a
# historical member join/event with its normal AFTER INSERT trigger creates
# a NEW activity_feed_events UUID which is absent from the source snapshot.
# That synthetic destination-only row caused the confirmed Oct 21 Stage abort.
# Keep FK/internal triggers enabled. Switch ONLY these named USER triggers,
# ONLY for a table with changed rows, and restore within the SAME transaction.
# Activity-feed sync rewrites historical meta/metadata pairs; it must also be
# suppressed while copying exact original feed records.
_CANDIDATE_COPY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "guild_configs": _CONFIG_COPY_TRIGGERS,
    "member_joins": ("member_joins_to_activity_feed_ai",),
    "member_events": ("member_events_to_activity_feed_ai",),
    "ticket_messages": ("ticket_messages_to_activity_feed_ai",),
    "ticket_notes": ("ticket_notes_to_activity_feed_ai",),
    "tickets": (
        "tickets_insert_to_activity_feed_ai",
        "tickets_update_to_activity_feed_au",
        "trg_sync_ticket_compat_columns",
    ),
    "ticket_categories": (
        "trg_capture_ticket_category_version",
        "trg_ticket_categories_updated_at",
    ),
    "ticket_panel_presets": ("set_ticket_panel_presets_updated_at",),
    "ticket_panel_rules": ("set_ticket_panel_rules_updated_at",),
    "ticket_panels": ("set_ticket_panels_updated_at",),
    "activity_feed_events": ("trg_sync_activity_feed_meta_columns",),
}


class StageBlocker(RuntimeError):
    """Fixed code and approved table only; never include source record data."""

    def __init__(self, code: str, table: str):
        self.code = code if code in SAFE_STOP_CODES else "unexpected_catchup_failure"
        self.table = table if table in audit.TABLES else "unknown"
        super().__init__(self.code)

SAFE_STOP_CODES = frozenset((
    "invalid_delta_mode",
    "missing_offline_only_confirmation",
    "source_writers_not_acknowledged_frozen",
    "offline_backup_receipt_required",
    "unapproved_table",
    "unapproved_generated_column",
    "candidate_only_rows_need_review",
    "changed_row_budget_exceeded",
    "changed_byte_budget_exceeded",
    "source_payload_missing",
    "offline_catchup_row_mismatch",
    "final_offline_snapshot_not_equal",
    "source_changed_during_offline_catchup",
    "unapproved_candidate_config_triggers",
    "candidate_config_trigger_toggle_failed",
    "candidate_config_triggers_not_restored",
    "unapproved_candidate_copy_triggers",
    "candidate_copy_trigger_toggle_failed",
    "candidate_copy_triggers_not_restored",
    "primary_key_not_in_column_contract",
    "cannot_update_pk_only_table",
    "bounded_table_limit_exceeded",
    "duplicate_primary_key",
))


def _candidate_copy_trigger_guard(conn: Any, table: str, *, enable: bool) -> None:
    from psycopg import sql

    names = _CANDIDATE_COPY_TRIGGERS.get(table)
    if not names or table not in audit.TABLES:
        raise RuntimeError("unapproved_candidate_copy_triggers")

    # Guard both the exact names AND their current enabled state. The source
    # never calls this; the verified candidate is the only write connection.
    # This ensures unexpected triggers never fire during historical replay.
    state_before = "D" if enable else "O"
    observed = conn.execute(
        "select tgname, tgenabled from pg_trigger where "
        "tgrelid=%s::regclass and not tgisinternal order by tgname",
        (f"public.{table}",),
    ).fetchall()
    if sorted((str(name), str(state)) for name, state in observed) != [
        (name, state_before) for name in sorted(names)
    ]:
        raise RuntimeError(
            "unapproved_candidate_config_triggers"
            if table == "guild_configs" else "unapproved_candidate_copy_triggers"
        )

    command = "ENABLE" if enable else "DISABLE"
    for name in names:
        try:
            conn.execute(
                sql.SQL("alter table public.{} {} trigger {}").format(
                    sql.Identifier(table), sql.SQL(command), sql.Identifier(name)
                )
            )
        except Exception:
            raise RuntimeError(
                "candidate_config_trigger_toggle_failed"
                if table == "guild_configs" else "candidate_copy_trigger_toggle_failed"
            ) from None

    observed_after = conn.execute(
        "select tgname, tgenabled from pg_trigger where "
        "tgrelid=%s::regclass and not tgisinternal order by tgname",
        (f"public.{table}",),
    ).fetchall()
    state_after = "O" if enable else "D"
    if sorted((str(name), str(state)) for name, state in observed_after) != [
        (name, state_after) for name in sorted(names)
    ]:
        raise RuntimeError(
            "candidate_config_triggers_not_restored"
            if table == "guild_configs" else "candidate_copy_triggers_not_restored"
        )


def _candidate_config_trigger_guard(conn: Any, *, enable: bool) -> None:
    """Backward-compatible narrow wrapper for the existing config audit."""
    _candidate_copy_trigger_guard(conn, "guild_configs", enable=enable)


def reconcile(mode: str) -> dict[str, Any]:
    import psycopg
    from psycopg import sql

    src_dsn, dst_dsn = approved_dsns()
    source_params = connection_params(src_dsn, readonly=True)
    dest_params = connection_params(dst_dsn, readonly=False)
    summaries: list[dict[str, Any]] = []
    stage_count = 0
    changed_bytes = 0
    with psycopg.connect(**source_params) as source:
        with psycopg.connect(**dest_params) as target:
            with source.transaction(), target.transaction():
                source.execute("set transaction isolation level repeatable read, read only")
                source.execute("set local timezone to 'UTC'")
                target.execute("set transaction isolation level repeatable read, " +
                               ("read write" if mode == "stage" else "read only"))
                target.execute("set local timezone to 'UTC'")
                if mode == "stage":
                    # Candidate ONLY. Lock all approved tables to avoid an
                    # accidental conflicting candidate writer during staging.
                    target.execute(sql.SQL(
                        "lock table {} in share row exclusive mode"
                    ).format(sql.SQL(", ").join(
                        sql.SQL("public.{}").format(sql.Identifier(t))
                        for t in audit.TABLES
                    )))
                keys: dict[str, tuple[str, ...]] = {}
                source_snapshots: dict[str, dict] = {}
                for table in audit.TABLES:
                    pk = audit.table_contract(source, target, table)
                    keys[table] = pk
                    source_columns = tuple(
                        name for name, _, _ in audit.column_contract(source, table)
                    )
                    # Generated columns or identity-always semantics should
                    # require a reviewed table-specific migration instead.
                    if source.execute(
                        "select count(*) from pg_attribute where "
                        "attrelid=%s::regclass and attnum>0 and not attisdropped "
                        "and attgenerated<>''",
                        (f"public.{table}",),
                    ).fetchone()[0]:
                        raise RuntimeError("unapproved_generated_column")
                    src = load_rows(
                        source, table, pk, retain_payload=mode == "stage"
                    )
                    dst = load_rows(target, table, pk, retain_payload=False)
                    source_snapshots[table] = src
                    difference = describe_delta(src, dst)
                    if difference["target_only"] and mode == "stage":
                        # Never silently delete candidate evidence or silently
                        # treat fresh trigger-generated rows as source data.
                        raise StageBlocker("candidate_only_rows_need_review", table)
                    writes = difference["write_keys"]
                    stage_count += len(writes)
                    if mode == "stage" and stage_count > MAX_CHANGED_ROWS:
                        raise RuntimeError("changed_row_budget_exceeded")
                    if mode == "stage":
                        # The source's explicit updated_at must survive. The
                        # candidate's normal runtime updated_at and historical
                        # snapshot triggers are not appropriate during a
                        # one-time exact offline data transfer.
                        if writes and table in _CANDIDATE_COPY_TRIGGERS:
                            _candidate_copy_trigger_guard(target, table, enable=False)
                        query = upsert_query(table, source_columns, pk)
                        for key in sorted(writes):
                            payload = src[key][1]
                            if not isinstance(payload, str):
                                raise RuntimeError("source_payload_missing")
                            changed_bytes += len(payload.encode("utf-8"))
                            if changed_bytes > MAX_CHANGED_BYTES:
                                raise RuntimeError("changed_byte_budget_exceeded")
                            target.execute(query, (payload,))
                        # Triggers, type conversion, constraints and the
                        # destination write itself must preserve EXACT rows.
                        revised = load_rows(target, table, pk, retain_payload=False)
                        if describe_delta(src, revised)["source_only"] or (
                            describe_delta(src, revised)["target_only"]
                            or describe_delta(src, revised)["changed"]
                        ):
                            raise RuntimeError("offline_catchup_row_mismatch")
                        if writes and table in _CANDIDATE_COPY_TRIGGERS:
                            _candidate_copy_trigger_guard(target, table, enable=True)
                    summaries.append({
                        "table": table,
                        "source_rows": len(src),
                        "candidate_rows_before": len(dst),
                        "source_only": difference["source_only"],
                        "candidate_only": difference["target_only"],
                        "changed": difference["changed"],
                    })
                if mode == "stage":
                    # A later table's insert/update trigger could have modified
                    # an earlier table after its individual check. Require final
                    # exact content parity across ALL 34 tables before commit.
                    for table in audit.TABLES:
                        final_rows = load_rows(
                            target, table, keys[table], retain_payload=False
                        )
                        summary = describe_delta(source_snapshots[table], final_rows)
                        if any(summary[k] for k in ("source_only", "target_only", "changed")):
                            raise RuntimeError("final_offline_snapshot_not_equal")
                    # All SOURCE sessions in this script are READ ONLY.
                    # A new source snapshot must still match the first one.
                    recheck_original_snapshot(source_params, source_snapshots, keys)

    changed = [s for s in summaries if s["source_only"] or
               s["candidate_only"] or s["changed"]]
    return {
        "status": "OFFLINE_ONLY_CATCHUP_COMMITTED" if mode == "stage" else "PREVIEW_ONLY",
        "audited_tables": len(summaries),
        "drifted_tables": len(changed),
        "changes": changed,
        "candidate_rows_inserted_or_updated": stage_count if mode == "stage" else 0,
        "original_source_writes": 0,
        "discord_or_discloud_mutations": 0,
        "production_cutover_authorized": False,
        "requires_final_frozen_writer_audit": True,
    }


def main() -> int:
    try:
        mode = validate_mode(
            os.getenv("DANK_DELTA_CATCHUP_MODE", "preview").strip(),
            os.getenv("DANK_DELTA_CATCHUP_CONFIRM", "").strip(),
            os.getenv("DANK_SOURCE_WRITERS_FROZEN", "").strip(),
            os.getenv("DANK_CANDIDATE_BACKUP_RECEIPT", "").strip(),
        )
        report = reconcile(mode)
    except Exception as exc:
        # Print ONLY code-owned, fixed diagnostic codes; never arbitrary
        # database exceptions, raw SQL, user IDs, URLs or credentials.
        code = exc.code if isinstance(exc, StageBlocker) else str(exc)
        if code not in SAFE_STOP_CODES:
            code = (
                "audit_contract_blocked"
                if isinstance(exc, audit.AuditBlocker)
                else "unexpected_catchup_failure"
            )
        if isinstance(exc, StageBlocker):
            code += " table=" + exc.table
        print("OFFLINE_DELTA_CATCHUP_STOPPED: " + code, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
