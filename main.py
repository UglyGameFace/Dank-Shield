from __future__ import annotations

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
