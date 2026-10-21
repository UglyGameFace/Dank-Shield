"""Owner-confirmed one-time schema repair in the OFFLINE dedicated Supabase only.

Never called by the read-only audit or bot. No source database credentials are
available to this workflow. Reject data that cannot fit the exact source type.
"""
from __future__ import annotations

import os
import sys

from stoney_verify.source_pg_backup import validate_source_dsn

REPO = "UglyGameFace/Dank-Shield"
CANDIDATE_REF = "viamriafkbqhquvrsnua"
CONFIRMATION = "FIX_OFFLINE_TICKET_CONFIDENCE_PRECISION_ONLY"
COLUMNS = ("ai_category_confidence", "mod_suggestion_confidence")


def validate_request() -> dict[str, str]:
    if not (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_REPOSITORY") == REPO
        and os.getenv("GITHUB_REF") == "refs/heads/main"
        and os.getenv("GITHUB_ACTOR") == "UglyGameFace"
        and os.getenv("DANK_TICKET_PRECISION_CONFIRMATION") == CONFIRMATION
    ):
        raise RuntimeError("not_owner_confirmed_offline_repair")
    # The pinned project/URL validator rejects all other Supabase projects,
    # including the original source and any Discloud hostname.
    return validate_source_dsn(
        os.getenv("DANK_FRESH_DATABASE_URL", ""), CANDIDATE_REF,
    )


def repair() -> str:
    import psycopg
    settings = validate_request()
    kwargs = {
        "host": settings["PGHOST"],
        "port": int(settings["PGPORT"]),
        "user": settings["PGUSER"],
        "password": settings["PGPASSWORD"],
        "dbname": settings["PGDATABASE"],
        "sslmode": "require",
        "connect_timeout": 10,
        "application_name": "dankshield_offline_ticket_precision_repair",
        "options": "-c statement_timeout=60000 -c lock_timeout=4000",
    }
    with psycopg.connect(**kwargs) as conn:
        with conn.transaction():
            # Exclusively lock the *offline* table so the verified rows cannot
            # change between checking and the transactional type correction.
            conn.execute("lock table public.tickets in access exclusive mode")
            rows = conn.execute(
                """
                select a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod)
                  from pg_attribute a
                 where a.attrelid='public.tickets'::regclass
                   and a.attname = any(%s) and a.attnum>0 and not a.attisdropped
                 order by a.attname
                """, (list(COLUMNS),),
            ).fetchall()
            types = {name: typ for name, typ in rows}
            if set(types) != set(COLUMNS):
                raise RuntimeError("unexpected_offline_ticket_columns")
            if all(types[k] == "numeric(5,2)" for k in COLUMNS):
                return "already_exact"
            if any(types[k] not in ("numeric", "numeric(5,2)") for k in COLUMNS):
                raise RuntimeError("unexpected_offline_ticket_type")
            # Explicitly prohibit rounding and overflow, even though PostgreSQL
            # normally rounds during a numeric(5,2) cast.
            unsafe = conn.execute(
                """
                select exists(
                  select 1 from public.tickets
                   where (ai_category_confidence is not null and
                         (abs(ai_category_confidence)>999.99 or
                          ai_category_confidence<>trunc(ai_category_confidence,2)))
                      or (mod_suggestion_confidence is not null and
                         (abs(mod_suggestion_confidence)>999.99 or
                          mod_suggestion_confidence<>trunc(mod_suggestion_confidence,2)))
                )
                """
            ).fetchone()[0]
            if unsafe:
                raise RuntimeError("offline_confidence_values_not_representable")
            conn.execute(
                "alter table public.tickets "
                "alter column ai_category_confidence type numeric(5,2), "
                "alter column mod_suggestion_confidence type numeric(5,2)"
            )
            verified = conn.execute(
                """
                select a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod)
                  from pg_attribute a
                 where a.attrelid='public.tickets'::regclass
                   and a.attname = any(%s) and a.attnum>0 and not a.attisdropped
                """, (list(COLUMNS),),
            ).fetchall()
            if {name: typ for name, typ in verified} != {
                k: "numeric(5,2)" for k in COLUMNS
            }:
                raise RuntimeError("offline_ticket_type_verification_failed")
            # Transaction commits only after both columns are verified.
    return "corrected"


def main() -> int:
    try:
        outcome = repair()
    except Exception as exc:
        safe = {
            "not_owner_confirmed_offline_repair",
            "unexpected_offline_ticket_columns",
            "unexpected_offline_ticket_type",
            "offline_confidence_values_not_representable",
            "offline_ticket_type_verification_failed",
        }
        code = str(exc) if type(exc) is RuntimeError and str(exc) in safe else "offline_repair_failed_redacted"
        print("OFFLINE_TICKET_PRECISION_REPAIR_FAILED: " + code, file=sys.stderr)
        return 2
    print("OFFLINE_TICKET_PRECISION_REPAIR: " + outcome)
    print("OFFLINE_REPAIR_SOURCE_WRITES: 0")
    print("PRODUCTION_CUTOVER_AUTHORIZED: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
