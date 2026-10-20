"""Manual, owner-approved private source-row migration to offline DankShield DB.

Zero impact on source: repeatable-read transaction READ ONLY; destination
writes only if exact new candidate identity, empty allowlisted tables, matching
column contracts, bounded sizes, and explicit owner confirmation pass.
Do not use this to authorize production cutover or late-write parity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sys
from typing import Any
from uuid import UUID

from stoney_verify.source_pg_backup import validate_source_dsn

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
DESTINATION_REF = "viamriafkbqhquvrsnua"
STAGE_CONFIRMATION = "IMPORT_DANKSHIELD_PRIVATE_SOURCE_ROWS_ONLY"
TABLES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "warns": (
        ("id", "uuid", True),
        ("guild_id", "text", True),
        ("user_id", "text", True),
        ("username", "text", False),
        ("reason", "text", True),
        ("source_message", "text", False),
        ("created_at", "timestamp with time zone", False),
    ),
    "dank_profile_users": (
        ("user_id", "text", True),
        ("preferences", "jsonb", True),
        ("platforms", "jsonb", True),
        ("created_at", "timestamp with time zone", True),
        ("updated_at", "timestamp with time zone", True),
    ),
    "dank_profile_guild_settings": (
        ("guild_id", "text", True),
        ("user_id", "text", True),
        ("settings", "jsonb", True),
        ("created_at", "timestamp with time zone", True),
        ("updated_at", "timestamp with time zone", True),
    ),
}
KEYS = {
    "warns": ("id",),
    "dank_profile_users": ("user_id",),
    "dank_profile_guild_settings": ("guild_id", "user_id"),
}
CAPS = {
    "warns": 1000,
    "dank_profile_users": 3000,
    "dank_profile_guild_settings": 5000,
}
JSONB_COLUMNS = frozenset({"preferences", "platforms", "settings"})
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ROW_BYTES = 128 * 1024


def _safe_shape() -> None:
    import re
    if SOURCE_REF == DESTINATION_REF:
        raise ValueError("source/destination identity collision")
    if set(TABLES) != set(KEYS) or set(TABLES) != set(CAPS):
        raise ValueError("incomplete private migration allowlist")
    for table, fields in TABLES.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", table):
            raise ValueError("unsafe table name")
        if len({name for name, _, _ in fields}) != len(fields):
            raise ValueError("duplicate source field")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name)
               for name, _, _ in fields):
            raise ValueError("unsafe field")
        if not set(KEYS[table]) <= {field[0] for field in fields}:
            raise ValueError("invalid source key")


def _column_contract(conn: Any, table: str) -> tuple[tuple[str, str, bool], ...]:
    from psycopg import sql
    # Names, types, and true nullability; no defaults, content or PII.
    rows = conn.execute(
        """
        select a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
               a.attnotnull
          from pg_catalog.pg_attribute a
         where a.attrelid=%s::regclass and a.attnum>0 and not a.attisdropped
         order by a.attnum
        """, (f"public.{table}",)
    ).fetchall()
    return tuple((str(n), str(t), bool(req)) for n,t,req in rows)


def _primary_key(conn: Any, table: str) -> tuple[str, ...]:
    rows = conn.execute(
        """
        select a.attname
          from pg_catalog.pg_constraint c
          join lateral unnest(c.conkey) with ordinality as k(n, idx) on true
          join pg_catalog.pg_attribute a
            on a.attrelid=c.conrelid and a.attnum=k.n
         where c.conrelid=%s::regclass and c.contype='p'
         order by k.idx
        """, (f"public.{table}",)
    ).fetchall()
    return tuple(str(item[0]) for item in rows)


def _assert_schema(source: Any, dest: Any) -> None:
    for table, expected in TABLES.items():
        for role, connection in (("source", source), ("destination", dest)):
            if _column_contract(connection, table) != expected:
                raise RuntimeError(f"{role} schema mismatch for {table}")
            if _primary_key(connection, table) != KEYS[table]:
                raise RuntimeError(f"{role} primary key mismatch for {table}")
            if role == "destination":
                policy = connection.execute(
                    "select relrowsecurity from pg_class where oid=%s::regclass",
                    (f"public.{table}",),
                ).fetchone()
                if not policy or not bool(policy[0]):
                    raise RuntimeError("destination private RLS disabled")
                for blocked in ("anon", "authenticated"):
                    priv = connection.execute(
                        "select has_table_privilege(%s,%s,'SELECT')",
                        (blocked, f"public.{table}"),
                    ).fetchone()
                    if bool(priv[0]):
                        raise RuntimeError("destination would expose private data")


def _normal(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normal(item) for key,item in value.items()}
    if isinstance(value, (list,tuple)):
        return [_normal(item) for item in value]
    return value


def _serialize(row: tuple[Any, ...]) -> bytes:
    return json.dumps(
        _normal(row), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _snapshot(conn: Any) -> dict[str, list[tuple[Any, ...]]]:
    from psycopg import sql
    original: dict[str, list[tuple[Any, ...]]] = {}
    budget = 0
    for name, columns in TABLES.items():
        cols = sql.SQL(",").join(sql.Identifier(c) for c,_,_ in columns)
        keys = sql.SQL(",").join(sql.Identifier(c) for c in KEYS[name])
        query = sql.SQL("select {} from {}.{} order by {} limit %s").format(
            cols, sql.Identifier("public"), sql.Identifier(name), keys
        )
        rows = list(conn.execute(query,(CAPS[name]+1,)).fetchall())
        if len(rows)>CAPS[name]:
            raise RuntimeError("bounded source row cap exceeded")
        for row in rows:
            b=len(_serialize(row))
            if b>MAX_ROW_BYTES:
                raise RuntimeError("oversized private source row")
            budget+=b
            if budget>MAX_TOTAL_BYTES:
                raise RuntimeError("private source payload budget exceeded")
        original[name]=rows
    return original


def _fingerprint(rows: list[tuple[Any, ...]]) -> str:
    state=sha256()
    for row in rows:
        data=_serialize(row)
        state.update(len(data).to_bytes(8,"big"))
        state.update(data)
    return state.hexdigest()


def _require_empty(conn: Any) -> None:
    from psycopg import sql
    for name in TABLES:
        statement=sql.SQL("select 1 from {}.{} limit 1").format(
            sql.Identifier("public"),sql.Identifier(name)
        )
        if conn.execute(statement).fetchone() is not None:
            raise RuntimeError("destination already contains private rows; no destructive merge")


def _write_snapshot(conn: Any, rows_by_table: dict[str,list[tuple[Any,...]]]) -> None:
    from psycopg import sql
    from psycopg.types.json import Jsonb

    for name, rows in rows_by_table.items():
        cols=tuple(f[0] for f in TABLES[name])
        query=sql.SQL("insert into {}.{} ({}) values ({})").format(
            sql.Identifier("public"),sql.Identifier(name),
            sql.SQL(",").join(map(sql.Identifier,cols)),
            sql.SQL(",").join(sql.Placeholder() for _ in cols)
        )
        json_positions=frozenset(i for i,col in enumerate(cols)
                                 if col in JSONB_COLUMNS)
        for row in rows:
            params=tuple(Jsonb(value) if i in json_positions and value is not None
                         else value for i,value in enumerate(row))
            conn.execute(query,params)


def _verify_target(conn: Any, snapshot: dict[str,list[tuple[Any,...]]]) -> None:
    from psycopg import sql
    for name,expected in snapshot.items():
        cols=sql.SQL(",").join(sql.Identifier(c) for c,_,_ in TABLES[name])
        keys=sql.SQL(",").join(sql.Identifier(c) for c in KEYS[name])
        query=sql.SQL("select {} from {}.{} order by {} limit %s").format(
            cols, sql.Identifier("public"),sql.Identifier(name),keys
        )
        actual=list(conn.execute(query,(CAPS[name]+1,)).fetchall())
        if len(actual)!=len(expected) or _fingerprint(actual)!=_fingerprint(expected):
            raise RuntimeError("destination private record integrity mismatch")


def _options(params: dict[str,str],name:str,read_only:bool) -> dict[str,Any]:
    return {
        "host":params["PGHOST"],"port":int(params["PGPORT"]),
        "user":params["PGUSER"],"password":params["PGPASSWORD"],
        "dbname":params["PGDATABASE"],"sslmode":"require",
        "connect_timeout":10,"application_name":name,
        "options":(
            "-c default_transaction_read_only=on " if read_only else ""
        )+"-c statement_timeout=15000 -c lock_timeout=1500 -c idle_in_transaction_session_timeout=60000",
        "autocommit":True,
    }


def run() -> dict[str,Any]:
    _safe_shape()
    mode=os.environ.get("DANK_PRIVATE_STAGE_MODE","preview").strip()
    if mode not in ("preview","stage"):
        raise ValueError("unapproved private staging mode")
    if mode=="stage" and os.environ.get("DANK_PRIVATE_STAGE_CONFIRMATION")!=STAGE_CONFIRMATION:
        raise ValueError("explicit source-to-dedicated import confirmation required")
    source=validate_source_dsn(
        os.environ.get("DANK_SOURCE_DATABASE_URL",""),SOURCE_REF
    )
    target=validate_source_dsn(
        os.environ.get("DANK_FRESH_DATABASE_URL",""),DESTINATION_REF
    )
    if source["PGHOST"]==target["PGHOST"] and source["PGUSER"]==target["PGUSER"]:
        raise ValueError("source/destination are not distinct")
    import psycopg
    # Source never permits writes. Capture one snapshot, then hash-compare
    # actual destination rows inside an atomic transaction before COMMIT.
    with psycopg.connect(**_options(source,"dankshield_private_source_readonly",True)) as src:
        with src.transaction():
            src.execute("set transaction isolation level repeatable read, read only")
            with psycopg.connect(**_options(target,"dankshield_private_target",False)) as dst:
                with dst.transaction():
                    if mode=="preview":
                        dst.execute("set transaction read only")
                    _assert_schema(src,dst)
                    _require_empty(dst)
                    original=_snapshot(src)
                    if mode=="stage":
                        _write_snapshot(dst,original)
                        _verify_target(dst,original)
                    results={name:len(rows) for name,rows in original.items()}
    return {
        "mode":mode,
        "result":"COPIED_AND_VERIFIED" if mode=="stage" else "PREVIEW_ONLY",
        "source_rows":results,
        "cutover_authorized":False,
        "source_frozen":False,
        "late_write_reconciliation_required":True,
        "excluded_live_card_message_ownership":True,
    }


def main() -> int:
    try:
        output=run()
        # Log only fixed allowlisted table names and counts, never user data or
        # credentials. No row hashes or raw SQL/backend error strings logged.
        print(json.dumps(output,sort_keys=True))
        return 0
    except Exception:
        print("PRIVATE SOURCE MIGRATION FAILED; connection and row details withheld",
              file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
