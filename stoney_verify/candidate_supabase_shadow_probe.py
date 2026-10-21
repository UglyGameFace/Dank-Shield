"""Opt-in read-only REST probe from the existing Discloud bot process.

This is a SHADOW probe, never a database switch or Discord canary.
Only the pinned, offline Dank Shield Supabase project may be queried.
Candidate credentials are independent of active SUPABASE_* and never logged.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

SOURCE_REF = "hyncpbvqyjojxhjzcdzg"
CANDIDATE_REF = "viamriafkbqhquvrsnua"
PROBES = (
    ("guild_configs", "id"),
    ("tickets", "id"),
    ("member_activity_ledger", "guild_id"),
    ("staff_action_logs", "id"),
    ("activity_feed_events", "id"),
)
ENABLED_ENV = "DANK_CANDIDATE_REST_SHADOW_PROBE"
CANDIDATE_URL_ENV = "DANK_CANDIDATE_SUPABASE_URL"
CANDIDATE_KEY_ENV = "DANK_CANDIDATE_SUPABASE_SERVICE_ROLE_KEY"


class ShadowProbeError(RuntimeError):
    """Fixed error code only; never emit HTTP exception strings."""
    def __init__(self, code: str):
        self.code = code if code in (
            "active_source_configuration_mismatch",
            "candidate_connection_not_configured",
            "candidate_project_identity_mismatch",
            "candidate_key_matches_active_key",
            "candidate_rest_read_failed",
        ) else "candidate_rest_read_failed"
        super().__init__(self.code)


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").lower().strip() in ("1", "true", "yes", "on")


def _host_is_exact(url: str, project_ref: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.netloc == project_ref + ".supabase.co"
            and parsed.path in ("", "/")
            and not parsed.query and not parsed.fragment
            and parsed.username is None and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def _credentials() -> tuple[str, str]:
    # Refuse to run if this isn't still the original runtime. The probe is
    # intended BEFORE any production credential cutover.
    if not _host_is_exact(os.environ.get("SUPABASE_URL", ""), SOURCE_REF):
        raise ShadowProbeError("active_source_configuration_mismatch")
    active_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if len(active_key) < 24:
        raise ShadowProbeError("active_source_configuration_mismatch")

    url = os.environ.get(CANDIDATE_URL_ENV, "").strip()
    key = os.environ.get(CANDIDATE_KEY_ENV, "").strip()
    if not url or len(key) < 24:
        raise ShadowProbeError("candidate_connection_not_configured")
    if not _host_is_exact(url, CANDIDATE_REF):
        raise ShadowProbeError("candidate_project_identity_mismatch")
    if key == active_key:
        raise ShadowProbeError("candidate_key_matches_active_key")
    return url, key


def run_shadow_probe() -> dict[str, object]:
    """Queries 5 private API endpoints; never routes bot operations to target."""
    if not enabled():
        return {"status": "disabled", "candidate_writes": 0}
    url, key = _credentials()
    # Independent client. NEVER touch globals.get_supabase(), the singleton,
    # live environment variables, Postgres connections, or Discord clients.
    from supabase import create_client

    try:
        client = create_client(url, key)
        for table, column in PROBES:
            result = client.table(table).select(column).limit(1).execute()
            if not isinstance(getattr(result, "data", None), list):
                raise ShadowProbeError("candidate_rest_read_failed")
    except Exception:
        raise ShadowProbeError("candidate_rest_read_failed") from None
    return {
        "status": "ready",
        "candidate_project": CANDIDATE_REF,
        "rest_table_checks": len(PROBES),
        "candidate_writes": 0,
        "source_writes": 0,
        "production_database_switched": False,
        "discord_canary_proven": False,
    }


async def run_shadow_probe_at_startup() -> None:
    """Non-blocking optional startup diagnostic with a firm wait limit."""
    import asyncio

    if not enabled():
        return
    try:
        result = await asyncio.wait_for(asyncio.to_thread(run_shadow_probe), timeout=30)
        print(
            "DANK_CANDIDATE_REST_SHADOW_PROBE: "
            + ("pass" if result.get("status") == "ready" else "disabled")
            + " checks=" + str(result.get("rest_table_checks", 0))
            + " production_switched=false source_writes=0 candidate_writes=0"
        )
    except ShadowProbeError as exc:
        print("DANK_CANDIDATE_REST_SHADOW_PROBE_FAILED: " + exc.code)
    except Exception:
        # Network exceptions, API errors, and client messages may contain
        # tokens, endpoints or sensitive values. Never print or propagate them.
        print("DANK_CANDIDATE_REST_SHADOW_PROBE_FAILED: candidate_rest_read_failed")
