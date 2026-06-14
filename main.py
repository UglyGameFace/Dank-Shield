from __future__ import annotations

# Load Discord API throttling/retry safety before the app imports anything that
# can call audit logs, send modlogs, or edit ticket channels.
import stoney_verify.startup_guards.discord_api_safety  # noqa: F401

# Keep production/public slash commands on one surface. This runs before app.py
# so the app does not create beta guild command copies unless explicitly enabled.
import stoney_verify.startup_guards.command_scope_dedupe  # noqa: F401

# Public production must never read deployment-level Discord role/channel/
# category/home-guild IDs. This runs before the package guard loader and before
# app.py imports globals consumers.
import stoney_verify.startup_guards.public_server_env_id_guard  # noqa: F401

from stoney_verify.startup_guards import (
    load_all_startup_guards,
    start_process_health_loop,
)

# Load all pre-app compatibility/safety guards from one ordered package loader.
# This keeps root startup clean and makes the next permanent-refactor pass easier.
load_all_startup_guards()

import stoney_verify.startup_guards.full_setup_health_autofix  # noqa: F401,E402
import stoney_verify.startup_guards.setup_visibility_health_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_role_visibility_repair_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_health_precision_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_vc_health_precision_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_check_existing_server_inference_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_health_defer_guard  # noqa: F401,E402

# Make worker starter return values match the live task they create. This keeps
# startup logs from saying a worker was not started right before that worker says
# it started.
