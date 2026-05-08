from __future__ import annotations

# Load Discord API throttling/retry safety before the app imports anything that
# can call audit logs, send modlogs, or edit ticket channels.
import stoney_verify.startup_guards.discord_api_safety  # noqa: F401

from stoney_verify.startup_guards import (
    load_all_startup_guards,
    start_process_health_loop,
)

# Load all pre-app compatibility/safety guards from one ordered package loader.
# This keeps root startup clean and makes the next permanent-refactor pass easier.
load_all_startup_guards()

from stoney_verify.app import run


if __name__ == "__main__":
    start_process_health_loop()
    run()
