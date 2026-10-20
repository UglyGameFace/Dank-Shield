"""Owner-gated, source read-only, full-row verified staff evidence transfer.

Runs only from workflow_dispatch on main. Source original shared Supabase stays
authoritative; destination is the new offline dedicated Supabase. NO Discord
actions, source writes, row logging or automatic history retention.
"""
from __future__ import annotations

from hashlib import sha256
import os
import sys

from stoney_verify.source_pg_backup import validate_source_dsn

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
DESTINATION_REF = "viamriafkbqhquvrsnua"
CONFIRMATION = "COPY_DANKSHIELD_STAFF_EVIDENCE_TO_OFFLINE_ONLY"
MAX_ROWS = 5000
MAX_BYTES = 40 * 1024 * 1024
SOURCE_COLUMNS = (
    ("id", "bigint", True),
    ("guild_id", "text", True),
    ("actor_id", "text", False),
    ("actor_display", "text", True),
    ("target_id", "text", False),
    ("target_display", "text", False),
    ("action", "text", True),
    ("reason", "text", False),
    ("duration", "text", False),
    ("metadata", "jsonb", True),
    ("created_at", "timestamp with time zone", True),
)


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in ("preview", "stage"):
        raise ValueError("mode must be preview or stage")
    if mode == "stage" and confirmation != CONFIRMATION:
        raise ValueError("owner confirmation missing")


def connect_args(params: dict[str, str], *, read_only: bool) -> dict:
    return {
        "host": params["PGHOST"],
        "port": int(params["PGPORT"]),
        "user": params["PGUSER"],
        "password": params["PGPASSWORD"],
        "dbname": params["PGDATABASE"],
        "sslmode": "require",
        "connect_timeout": 10,
        "autocommit": True,
        "application_name": "dankshield_staff_evidence_offline",
        "options": ("-c default_transaction_read_only=on " if read_only else "")
            + "-c statement_timeout=90000 -c lock_timeout=2000 "
              "-c idle_in_transaction_session_timeout=120000",
    }


def contract(db, name: str) -> None:
    columns = db.execute(
        """
        select a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull
          from pg_attribute a
         where a.attrelid='public.staff_action_logs'::regclass
           and a.attnum>0 and not a.attisdropped
         order by a.attnum
        """
    ).fetchall()
    if tuple((str(n), str(t), bool(b)) for n, t, b in columns) != SOURCE_COLUMNS:
        raise RuntimeError("staff moderation evidence columns do not match")
    pk = db.execute(
        """
        select a.attname from pg_constraint c
        join lateral unnest(c.conkey) with ordinality as k(n,pos) on true
        join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k.n
        where c.conrelid='public.staff_action_logs'::regclass
          and c.contype='p' order by k.pos
        """
    ).fetchall()
    if tuple(row[0] for row in pk) != ("id",):
        raise RuntimeError("moderation evidence primary key mismatch")
    if name == "candidate":
        rls = db.execute(
            "select relrowsecurity from pg_class "
            "where oid='public.staff_action_logs'::regclass"
        ).fetchone()
        if not rls or not rls[0]:
            raise RuntimeError("candidate staff log RLS disabled")
        for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            for role in ("anon", "authenticated"):
                if db.execute(
                    "select has_table_privilege(%s,'public.staff_action_logs',%s)",
                    (role, operation),
                ).fetchone()[0]:
                    raise RuntimeError("candidate staff history publicly accessible")
            if not db.execute(
                "select has_table_privilege('service_role','public.staff_action_logs',%s)",
                (operation,),
            ).fetchone()[0]:
                raise RuntimeError("candidate backend history grant missing")
        if db.execute(
            "select has_table_privilege('service_role','public.staff_action_logs','TRUNCATE')"
        ).fetchone()[0]:
            raise RuntimeError("history TRUNCATE grant forbidden")


def preview_source(src) -> tuple[int, int]:
    row = src.execute(
        """
        select count(*),coalesce(sum(octet_length(to_jsonb(x)::text)),0),
               count(distinct id),
               count(*) filter (where not guild_id ~ '^[1-9][0-9]{0,19}$'),
               count(*) filter (where not exists(
                  select 1 from public.guild_configs g where g.guild_id=x.guild_id
               ))
          from public.staff_action_logs x
        """
    ).fetchone()
    count, size, unique, invalid, orphans = tuple(map(int, row))
    if not 0 < count <= MAX_ROWS or size > MAX_BYTES:
        raise RuntimeError("source history exceeds bounded transfer")
    if count != unique or invalid or orphans:
        raise RuntimeError("source history ownership is invalid")
    return count, size


def inspect_candidate(dest, *, empty: bool) -> None:
    if dest.execute(
        "select count(*) from public.guild_configs"
    ).fetchone()[0] < 1:
        raise RuntimeError("candidate guild configs missing")
    exists = dest.execute(
        "select exists(select 1 from public.staff_action_logs limit 1)"
    ).fetchone()[0]
    if empty and exists:
        raise RuntimeError("candidate staff logs already populated; no destructive merge")


def snapshot_payload(source, count: int, size: int) -> str:
    # ONLY PostgreSQL JSON text is used. No int/float decoding of original
    # Discord snowflake identifiers, counters, timestamps or staff evidence.
    payload = source.execute(
        """
        select coalesce(jsonb_agg(to_jsonb(x) order by x.id)::text,'[]')
          from public.staff_action_logs x
        """
    ).fetchone()[0]
    length = len(payload.encode("utf-8"))
    if not 2 <= length <= MAX_BYTES:
        raise RuntimeError("source JSON snapshot exceeds byte budget")
    if source.execute(
        "select count(*) from public.staff_action_logs"
    ).fetchone()[0] != count:
        raise RuntimeError("source row count changed during snapshot")
    # JSON punctuation may add overhead over individual-row source size.
    if length > size + 12 * count:
        raise RuntimeError("unexpected source JSON snapshot size")
    return payload


def ensure_source_guilds_in_candidate(dest, payload: str) -> None:
    unrecognized = dest.execute(
        """
        select exists(
          select 1 from jsonb_to_recordset(%s::jsonb) as r(guild_id text)
          where not exists(
            select 1 from public.guild_configs g where g.guild_id=r.guild_id
          )
        )
        """, (payload,),
    ).fetchone()[0]
    if unrecognized:
        raise RuntimeError("some moderator evidence guilds are missing from candidate")


def verify_destination(dest, payload: str, count: int) -> None:
    saved = dest.execute(
        """
        select coalesce(jsonb_agg(to_jsonb(x) order by x.id)::text,'[]')
        from public.staff_action_logs x
        """
    ).fetchone()[0]
    if sha256(saved.encode("utf-8")).digest() != sha256(payload.encode("utf-8")).digest():
        raise RuntimeError("private staff history full-content mismatch")
    if dest.execute(
        "select count(*) from public.staff_action_logs"
    ).fetchone()[0] != count:
        raise RuntimeError("private staff history source/target count mismatch")


def stage() -> dict:
    mode = os.environ.get("DANK_STAFF_HISTORY_MODE", "preview").strip()
    validate_mode(mode, os.environ.get("DANK_STAFF_HISTORY_CONFIRMATION", ""))
    source = validate_source_dsn(
        os.environ.get("DANK_SOURCE_DATABASE_URL", ""), SOURCE_REF
    )
    target = validate_source_dsn(
        os.environ.get("DANK_FRESH_DATABASE_URL", ""), DESTINATION_REF
    )
    if source["PGHOST"] == target["PGHOST"] and source["PGUSER"] == target["PGUSER"]:
        raise RuntimeError("source and candidate credentials appear identical")
    import psycopg

    with psycopg.connect(**connect_args(source, read_only=True)) as src:
        with src.transaction():
            src.execute("set transaction isolation level repeatable read, read only")
            contract(src, "source")
            count, size = preview_source(src)
            with psycopg.connect(**connect_args(target, read_only=False)) as dest:
                with dest.transaction():
                    if mode == "preview":
                        dest.execute("set transaction read only")
                    else:
                        dest.execute(
                            "select pg_advisory_xact_lock(64232667024)"
                        )
                    contract(dest, "candidate")
                    inspect_candidate(dest, empty=True)
                    if mode == "stage":
                        payload = snapshot_payload(src, count, size)
                        ensure_source_guilds_in_candidate(dest, payload)
                        dest.execute(
                            """
                            insert into public.staff_action_logs
                              (id,guild_id,actor_id,actor_display,target_id,
                               target_display,action,reason,duration,metadata,created_at)
                            select id,guild_id,actor_id,actor_display,target_id,
                                   target_display,action,reason,duration,metadata,created_at
                              from jsonb_populate_recordset(
                                null::public.staff_action_logs,%s::jsonb
                              )
                            """,
                            (payload,),
                        )
                        verify_destination(dest, payload, count)
                    # Preview transaction is strictly read only; stage commits
                    # once, only after every source row has been compared.
    return {
        "result": "EXACT_ROWS_COMMITTED" if mode == "stage" else "PREVIEW_ONLY",
        "source_staff_actions": count,
        "candidate_history_verified": mode == "stage",
        "original_source_writes": 0,
        "production_cutover_authorized": False,
    }


def main() -> int:
    try:
        result = stage()
    except Exception as exc:
        # Never print DB error strings: they can contain SQL data or secrets.
        print("STAFF HISTORY TRANSFER STOPPED: " + type(exc).__name__,
              file=sys.stderr)
        return 1
    import json
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
