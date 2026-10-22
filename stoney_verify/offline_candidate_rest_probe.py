"""Opt-in Discloud-origin REST check with the entire Discord bot disabled.

This is the ONLY safe way to run the existing candidate REST probe during a
source writer freeze using stoneyverify's main.py entry point. No Discord or
ordinary app modules are imported on this boot path. The application remains
alive but inert after the one-time probe so AUTORESTART=true cannot accidentally
start the Discord gateway after a completed diagnostic.
"""
from __future__ import annotations

import os
import time

from stoney_verify.candidate_supabase_shadow_probe import (
    ShadowProbeError,
    enabled as shadow_enabled,
    run_shadow_probe,
)

OFFLINE_PROBE_ONLY_ENV = "DANK_OFFLINE_CANDIDATE_REST_PROBE_ONLY"
OFFLINE_PROBE_ONLY_CONFIRM = "I_AUTHORIZE_OFFLINE_REST_PROBE_NO_DISCORD"
ALLOWED_ERRORS = frozenset({
    "active_source_configuration_mismatch",
    "candidate_connection_not_configured",
    "candidate_project_identity_mismatch",
    "candidate_key_matches_active_key",
    "candidate_rest_read_failed",
})


def probe_once() -> int:
    # The entry point routes here on ANY nonempty value of this variable.
    # A typo must DISABLE the bot rather than fall through to normal startup.
    if os.getenv(OFFLINE_PROBE_ONLY_ENV, "") != OFFLINE_PROBE_ONLY_CONFIRM:
        print("DANK_OFFLINE_REST_PROBE_FAILED: invalid_explicit_opt_in", flush=True)
        return 2
    if not shadow_enabled():
        print("DANK_OFFLINE_REST_PROBE_FAILED: shadow_probe_not_enabled", flush=True)
        return 2

    try:
        result = run_shadow_probe()
        if (
            result.get("status") != "ready"
            or result.get("rest_table_checks") != 5
            or result.get("source_writes") != 0
            or result.get("candidate_writes") != 0
            or result.get("production_database_switched") is not False
        ):
            raise ShadowProbeError("candidate_rest_read_failed")
    except ShadowProbeError as exc:
        code = exc.code if exc.code in ALLOWED_ERRORS else "candidate_rest_read_failed"
        print("DANK_OFFLINE_REST_PROBE_FAILED: " + code, flush=True)
        return 2
    except Exception:
        # Absolutely no raw SQL, HTTP errors, bearer keys, IDs or response
        # bodies leave this diagnostic path.
        print("DANK_OFFLINE_REST_PROBE_FAILED: candidate_rest_read_failed", flush=True)
        return 2

    print(
        "DANK_OFFLINE_REST_PROBE: pass checks=5"
        " discord_started=false bot_workers_started=false"
        " source_writes=0 candidate_writes=0 production_switched=false",
        flush=True,
    )
    return 0


def run_guarded(*, hold: bool = True) -> int:
    result = probe_once()
    if hold:
        # TYPE=site, AUTORESTART=true. Do not exit and accidentally start the
        # bot on subsequent automatic retries. Keep this process inert until
        # the owner stops the Discloud app and changes the opt-in variables.
        print(
            "DANK_OFFLINE_REST_PROBE_HOLD: discord_disabled=true"
            " awaiting_explicit_owner_stop",
            flush=True,
        )
        try:
            while True:
                time.sleep(300)
        except KeyboardInterrupt:
            pass
    return result


if __name__ == "__main__":
    raise SystemExit(run_guarded())
