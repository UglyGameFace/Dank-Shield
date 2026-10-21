"""Owner-only, strictly read-only preflight of the offline dedicated Supabase.

This is a database/RPC contract gate, NOT a Discord bot canary, writer freeze,
Discloud connectivity proof, data-parity gate, or authorization to cut over.
No source credentials, sensitive row contents, mutations, or secret output.
"""
from __future__ import annotations

import json
import os
import sys

from stoney_verify.source_pg_backup import validate_source_dsn
from tools.audit_dankshield_cutover_delta import connection_params

EXPECTED_REPO = "UglyGameFace/Dank-Shield"
EXPECTED_CANDIDATE = "viamriafkbqhquvrsnua"
EXPECTED_FUNCTIONS = (
    "public.patch_dank_guild_config(text,jsonb,text[],jsonb)",
    "public.record_member_activity_batch(uuid,jsonb)",
    "public.ack_member_activity_batches(uuid[])",
    "public.record_member_activity(text,text,text,timestamp with time zone,text,text)",
    "public.heartbeat_member_activity_tracker(text,text,timestamp with time zone,integer)",
    "public.fail_member_activity_tracker(text,text,timestamp with time zone,text)",
    "public.resume_member_activity_tracker(text,text,text,timestamp with time zone,timestamp with time zone)",
    "public.start_member_activity_tracker(text,text,timestamp with time zone)",
)
ERROR_CODES = frozenset((
    "not_trusted_owner_main",
    "unexpected_postgres_target",
    "missing_public_tables",
    "public_table_rls_missing",
    "public_table_role_exposure",
    "public_table_backend_crud_missing",
    "public_definer_function_role_exposure",
    "required_backend_function_missing",
    "required_backend_function_permissions",
    "dedicated_operation_queues_not_empty",
    "unclassified_preflight_error",
))


class TargetPreflightError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "unclassified_preflight_error"
        super().__init__(self.code)


def validate_runner() -> dict[str, str]:
    if not (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REPOSITORY") == EXPECTED_REPO
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_ACTOR") == "UglyGameFace"
    ):
        raise TargetPreflightError("not_trusted_owner_main")
    return validate_source_dsn(
        os.environ.get("DANK_FRESH_DATABASE_URL", ""), EXPECTED_CANDIDATE,
    )


def inspect(conn) -> dict[str, object]:
    current_db, version_num, read_only = conn.execute(
        "select current_database(), current_setting('server_version_num')::integer,"
        " current_setting('transaction_read_only')"
    ).fetchone()
    if current_db != "postgres" or int(version_num) // 10000 != 17 or read_only != "on":
        raise TargetPreflightError("unexpected_postgres_target")

    (tables, no_rls, exposed, missing_crud) = conn.execute(
        """
        select count(*)::int,
               count(*) filter (where not c.relrowsecurity)::int,
               count(*) filter (
                   where has_table_privilege('anon',c.oid,'SELECT')
                      or has_table_privilege('authenticated',c.oid,'SELECT')
                      or has_table_privilege('anon',c.oid,'INSERT')
                      or has_table_privilege('authenticated',c.oid,'INSERT')
                      or has_table_privilege('anon',c.oid,'UPDATE')
                      or has_table_privilege('authenticated',c.oid,'UPDATE')
                      or has_table_privilege('anon',c.oid,'DELETE')
                      or has_table_privilege('authenticated',c.oid,'DELETE')
               )::int,
               count(*) filter (
                   where not (
                       has_table_privilege('service_role',c.oid,'SELECT')
                       and has_table_privilege('service_role',c.oid,'INSERT')
                       and has_table_privilege('service_role',c.oid,'UPDATE')
                       and has_table_privilege('service_role',c.oid,'DELETE')
                   )
               )::int
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relkind in ('r','p')
        """
    ).fetchone()
    if tables < 75:
        raise TargetPreflightError("missing_public_tables")
    if no_rls:
        raise TargetPreflightError("public_table_rls_missing")
    if exposed:
        raise TargetPreflightError("public_table_role_exposure")
    if missing_crud:
        raise TargetPreflightError("public_table_backend_crud_missing")

    # A public-schema SECURITY DEFINER function callable by browser roles
    # could bypass row policies even if all 75 tables have RLS enabled.
    exposed_definer = conn.execute(
        """
        select count(*)::int from pg_proc p
        join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='public' and p.prosecdef
          and (has_function_privilege('anon',p.oid,'EXECUTE')
            or has_function_privilege('authenticated',p.oid,'EXECUTE'))
        """
    ).fetchone()[0]
    if exposed_definer:
        raise TargetPreflightError("public_definer_function_role_exposure")

    for signature in EXPECTED_FUNCTIONS:
        present, backend, anon, authenticated = conn.execute(
            """
            with f as (select to_regprocedure(%s) as id)
            select id is not null,
                   coalesce(has_function_privilege('service_role',id,'EXECUTE'),false),
                   coalesce(has_function_privilege('anon',id,'EXECUTE'),false),
                   coalesce(has_function_privilege('authenticated',id,'EXECUTE'),false)
            from f
            """, (signature,),
        ).fetchone()
        if not present:
            raise TargetPreflightError("required_backend_function_missing")
        if not backend or anon or authenticated:
            raise TargetPreflightError("required_backend_function_permissions")

    queues = conn.execute(
        """
        select (select count(*)::int from public.bot_commands),
               (select count(*)::int from public.bot_operation_jobs)
        """
    ).fetchone()
    if any(queues):
        raise TargetPreflightError("dedicated_operation_queues_not_empty")

    return {
        "result": "DEDICATED_DATABASE_RUNTIME_CONTRACTS_READY",
        "checked_public_tables": int(tables),
        "checked_backend_functions": len(EXPECTED_FUNCTIONS),
        "public_table_rls_missing": 0,
        "public_table_role_exposure": 0,
        "public_definer_function_role_exposure": 0,
        "dedicated_execution_queue_items": 0,
        "target_writes": 0,
        "source_writes": 0,
        "discloud_network_proven": False,
        "supabase_rest_api_runtime_proven": False,
        "discord_guild_canary_proven": False,
        "source_writers_frozen": False,
        "final_hash_parity_proven": False,
        "production_cutover_authorized": False,
    }


def preview() -> dict[str, object]:
    import psycopg
    parsed = validate_runner()
    # Uses source audit's hardened read-only session options, but the pinned
    # URL is ONLY the dedicated candidate. No source URL is loaded.
    with psycopg.connect(**connection_params(parsed)) as conn:
        with conn.transaction():
            conn.execute("set transaction isolation level repeatable read, read only")
            return inspect(conn)


def main() -> int:
    try:
        report = preview()
    except Exception as exc:
        code = (exc.code if isinstance(exc, TargetPreflightError)
                else "unclassified_preflight_error")
        print("DEDICATED_RUNTIME_PREFLIGHT_FAILED: " + code, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
