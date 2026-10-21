"""Offline-only, owner-confirmed history copy for Dank Shield activity feed.

Source stays read-only and active; destination must be empty. PostgreSQL's
to_jsonb(row)::text is streamed without JavaScript numeric decoding. Historical
rows preserve divergent meta/metadata; the source-aligned trigger remains
enabled for NEW events, disabled ONLY inside the atomic source-history copy.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from stoney_verify.source_pg_backup import validate_source_dsn

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
DESTINATION_REF = "viamriafkbqhquvrsnua"
CONFIRMATION = "COPY_DANKSHIELD_ACTIVITY_FEED_OFFLINE_ONLY"
MAX_ROWS = 8000
MAX_BYTES = 100 * 1024 * 1024
MAX_ROW_BYTES = 128 * 1024
BATCH_SIZE = 100
TABLE = "public.activity_feed_events"
TRIGGER = "trg_sync_activity_feed_meta_columns"
EXPECTED_COLUMNS = (
    "id", "guild_id", "event_family", "event_type", "source",
    "actor_user_id", "actor_name", "target_user_id", "target_name",
    "channel_id", "channel_name", "ticket_id", "ticket_message_id",
    "related_table", "related_id", "title", "description", "reason",
    "search_text", "metadata", "created_at", "meta", "actor_id",
    "target_id", "message",
)


def validate_mode(mode: str, confirmation: str) -> None:
    if mode not in ("preview", "stage"):
        raise ValueError("invalid mode")
    if mode == "stage" and confirmation != CONFIRMATION:
        raise ValueError("missing exact owner confirmation")


def connect_params(parsed: dict[str, str], *, source: bool) -> dict[str, Any]:
    return {
        "host": parsed["PGHOST"],
        "port": int(parsed["PGPORT"]),
        "user": parsed["PGUSER"],
        "password": parsed["PGPASSWORD"],
        "dbname": parsed["PGDATABASE"],
        "sslmode": "require",
        "connect_timeout": 10,
        "autocommit": True,
        "application_name": "dankshield_activity_history_offline",
        "options": ("-c default_transaction_read_only=on " if source else "")
            + "-c statement_timeout=180000 -c lock_timeout=2000 "
              "-c idle_in_transaction_session_timeout=300000",
    }


def schema_columns(conn) -> tuple[tuple[str, str, bool], ...]:
    records = conn.execute(
        """
        select att.attname,
               pg_catalog.format_type(att.atttypid,att.atttypmod),
               att.attnotnull
        from pg_attribute att
        where att.attrelid='public.activity_feed_events'::regclass
          and att.attnum>0 and not att.attisdropped
        order by att.attnum
        """
    ).fetchall()
    result = tuple((str(n), str(t), bool(required)) for n,t,required in records)
    if tuple(x[0] for x in result) != EXPECTED_COLUMNS:
        raise RuntimeError("feed table does not have 25 approved source columns")
    pk = conn.execute(
        """
        select att.attname
        from pg_constraint c
        join lateral unnest(c.conkey) with ordinality k(num,ord) on true
        join pg_attribute att on att.attrelid=c.conrelid and att.attnum=k.num
        where c.conrelid='public.activity_feed_events'::regclass
          and c.contype='p' order by k.ord
        """
    ).fetchall()
    if tuple(item[0] for item in pk) != ("id",):
        raise RuntimeError("feed primary key mismatch")
    return result


def check_target(conn) -> None:
    if not conn.execute(
        "select relrowsecurity from pg_class where oid='public.activity_feed_events'::regclass"
    ).fetchone()[0]:
        raise RuntimeError("candidate feed RLS missing")
    for op in ("SELECT","INSERT","UPDATE","DELETE"):
        for role in ("anon","authenticated"):
            if conn.execute(
                "select has_table_privilege(%s,'public.activity_feed_events',%s)", (role,op)
            ).fetchone()[0]:
                raise RuntimeError("candidate feed publicly accessible")
        if not conn.execute(
            "select has_table_privilege('service_role','public.activity_feed_events',%s)",
            (op,),
        ).fetchone()[0]:
            raise RuntimeError("candidate feed backend role missing")
    trigger = conn.execute(
        """select tgenabled from pg_trigger
           where tgrelid='public.activity_feed_events'::regclass
           and tgname='trg_sync_activity_feed_meta_columns'"""
    ).fetchone()
    if not trigger or trigger[0] != "O":
        raise RuntimeError("source-aligned live feed trigger not enabled")
    if conn.execute(
        "select exists(select 1 from public.activity_feed_events)"
    ).fetchone()[0]:
        raise RuntimeError("candidate feed already populated; do not duplicate source history")
    for table in ("guild_configs","member_events","staff_action_logs"):
        if conn.execute("select to_regclass(%s)", ("public."+table,)).fetchone()[0] is None:
            raise RuntimeError("candidate feed related history table missing")


def source_preflight(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        select count(*),count(distinct id),
               coalesce(sum(octet_length(to_jsonb(f)::text)),0),
               coalesce(max(octet_length(to_jsonb(f)::text)),0),
               count(*) filter(where not exists(
                 select 1 from public.guild_configs g where g.guild_id=f.guild_id
               )),
               count(*) filter(where f.related_table='staff_action_logs'
                 and not exists(
                   select 1 from public.staff_action_logs s
                   where s.id::text=f.related_id
                 )),
               count(*) filter(where f.related_table='member_events'
                 and not exists(
                   select 1 from public.member_events m
                   where m.id::text=f.related_id
                 )),
               count(*) filter(where f.meta is distinct from f.metadata),
               count(*) filter(where f.related_table='staff_action_logs'),
               count(*) filter(where f.related_table='member_events')
         from public.activity_feed_events f
        """
    ).fetchone()
    count,unique,total_bytes,max_byte,missing_guild,missing_staff,missing_member,divergent,staff_refs,member_refs=map(int,row)
    if not 1 <= count <= MAX_ROWS or not 0 <= total_bytes <= MAX_BYTES or max_byte > MAX_ROW_BYTES:
        raise RuntimeError("history volume exceeds approved bounds")
    if unique != count or missing_guild or missing_staff or missing_member:
        raise RuntimeError("source activity history has broken identity or references")
    return {
        "count":count, "size":total_bytes, "divergent":divergent,
        "staff_refs":staff_refs, "member_refs":member_refs
    }


def digest(conn) -> str:
    return str(conn.execute(
        """
        select md5(coalesce(
          jsonb_agg(to_jsonb(f) order by f.id)::text,'[]'
        )) from public.activity_feed_events f
        """
    ).fetchone()[0])


def write_batch(dest, batch: list[tuple[str,str,str]]) -> int:
    if not batch:
        return 0
    keys=[x[0] for x in batch]
    if len(set(keys)) != len(keys):
        raise RuntimeError("duplicate UUID within source page")
    payload="["+",".join(x[2] for x in batch)+"]"
    if len(payload.encode("utf-8")) > 3*1024*1024:
        raise RuntimeError("source history page exceeds size bound")
    if dest.execute(
        """
        select exists(
          select 1 from jsonb_to_recordset(%s::jsonb) as f(guild_id text)
          where not exists(
            select 1 from public.guild_configs g where g.guild_id=f.guild_id
          )
        )
        """,(payload,)
    ).fetchone()[0]:
        raise RuntimeError("destination missing history guild")
    dest.execute(
        """insert into public.activity_feed_events
           select * from jsonb_populate_recordset(
             null::public.activity_feed_events,%s::jsonb
           )""",(payload,)
    )
    saved_count,equal=dest.execute(
        """
        select count(*),coalesce(jsonb_agg(to_jsonb(f) order by f.id),'[]'::jsonb)=%s::jsonb
        from public.activity_feed_events f
        where f.id = any(%s::uuid[])
        """,(payload,keys)
    ).fetchone()
    if int(saved_count)!=len(batch) or not equal:
        raise RuntimeError("activity history original-vs-destination batch mismatch")
    return len(batch)


def stage() -> dict[str, Any]:
    mode=os.environ.get("DANK_ACTIVITY_FEED_MODE","preview").strip()
    validate_mode(mode,os.environ.get("DANK_ACTIVITY_FEED_CONFIRMATION",""))
    source=validate_source_dsn(
        os.environ.get("DANK_SOURCE_DATABASE_URL",""),SOURCE_REF
    )
    target=validate_source_dsn(
        os.environ.get("DANK_FRESH_DATABASE_URL",""),DESTINATION_REF
    )
    if source["PGHOST"]==target["PGHOST"] and source["PGUSER"]==target["PGUSER"]:
        raise RuntimeError("source/destination collision")
    import psycopg

    with psycopg.connect(**connect_params(source,source=True)) as src:
        with src.transaction():
            src.execute("set transaction isolation level repeatable read, read only")
            original_contract=schema_columns(src)
            data=source_preflight(src)
            original_digest=digest(src)
            with psycopg.connect(**connect_params(target,source=False)) as dest:
                with dest.transaction():
                    if mode=="preview":
                        dest.execute("set transaction read only")
                    else:
                        dest.execute("select pg_advisory_xact_lock(64232667025)")
                    if schema_columns(dest)!=original_contract:
                        raise RuntimeError("dedicated feed differs from original schema")
                    check_target(dest)
                    if mode=="stage":
                        dest.execute(
                            "lock table public.activity_feed_events in share row exclusive mode"
                        )
                        dest.execute(
                            "alter table public.activity_feed_events "
                            "disable trigger trg_sync_activity_feed_meta_columns"
                        )
                        copied=0
                        with src.cursor(name="dankshield_activity_feed_import") as cursor:
                            cursor.itersize=BATCH_SIZE
                            cursor.execute(
                                """select f.id::text,f.guild_id,to_jsonb(f)::text
                                     from public.activity_feed_events f order by f.id"""
                            )
                            while True:
                                batch=cursor.fetchmany(BATCH_SIZE)
                                if not batch:
                                    break
                                for _,guild_id,payload in batch:
                                    if (not guild_id or not payload.startswith("{")
                                        or len(payload.encode("utf-8"))>MAX_ROW_BYTES):
                                        raise RuntimeError("invalid source row in page")
                                copied+=write_batch(dest,batch)
                        dest.execute(
                            "alter table public.activity_feed_events "
                            "enable trigger trg_sync_activity_feed_meta_columns"
                        )
                        if copied!=data["count"] or digest(dest)!=original_digest:
                            raise RuntimeError("full activity history digest mismatch")
                        assert_equal=dest.execute(
                            """
                            select count(*) filter(where meta is distinct from metadata),
                                   count(*) filter(where related_table='staff_action_logs'
                                     and not exists(
                                     select 1 from public.staff_action_logs s
                                     where s.id::text=activity_feed_events.related_id
                                     )),
                                   count(*) filter(where related_table='member_events'
                                     and not exists(
                                     select 1 from public.member_events m
                                     where m.id::text=activity_feed_events.related_id
                                     ))
                              from public.activity_feed_events
                            """
                        ).fetchone()
                        if tuple(map(int,assert_equal))!=(data["divergent"],0,0):
                            raise RuntimeError("history metadata or moderator references changed")
    return {
       "result":"EXACT_HISTORY_COMMITTED" if mode=="stage" else "PREVIEW_ONLY",
       "source_feed_events":data["count"],
       "staff_action_history_links":data["staff_refs"],
       "member_event_history_links":data["member_refs"],
       "source_rows_with_different_meta":data["divergent"],
       "candidate_history_verified":mode=="stage",
       "original_source_writes":0,
       "production_cutover_authorized":False,
    }


def main() -> int:
    try:
        output=stage()
    except Exception as exc:
        # Do NOT print SQL, source data, connection URLs or exception message.
        print("ACTIVITY FEED COPY STOPPED: "+type(exc).__name__,file=sys.stderr)
        return 2
    import json
    print(json.dumps(output,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
