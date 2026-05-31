from __future__ import annotations

# Load Discord API throttling/retry safety before the app imports anything that
# can call audit logs, send modlogs, or edit ticket channels.
import stoney_verify.startup_guards.discord_api_safety  # noqa: F401

# Keep production/public slash commands on one surface. This runs before app.py
# so the app does not create beta guild command copies unless explicitly enabled.
import stoney_verify.startup_guards.command_scope_dedupe  # noqa: F401

from stoney_verify.startup_guards import (
    load_all_startup_guards,
    start_process_health_loop,
)

# Load all pre-app compatibility/safety guards from one ordered package loader.
# This keeps root startup clean and makes the next permanent-refactor pass easier.
load_all_startup_guards()

# Ticket categories can hit Discord's child-channel limit. Load overflow routing
# before extra ticket UI patches so creation/reopen paths choose a usable parent.
import stoney_verify.startup_guards.ticket_overflow_category_guard  # noqa: F401,E402

# Setup actions can create channels/roles and write config. Load this before the
# app starts so duplicate taps are blocked instead of racing setup state.
import stoney_verify.startup_guards.setup_operation_lock_guard  # noqa: F401,E402

# Ticket open controls should show live staff context instead of plain buttons.
import stoney_verify.startup_guards.ticket_open_controls_status_guard  # noqa: F401,E402

# Keep that live status panel fresh after claim/unclaim/transfer/priority edits.
import stoney_verify.startup_guards.ticket_open_controls_refresh_guard  # noqa: F401,E402

# Staff can add/remove extra members or roles from a ticket through More Actions.
import stoney_verify.startup_guards.ticket_access_management_guard  # noqa: F401,E402

# Make transcript posts easier for staff/server owners to read at a glance.
import stoney_verify.startup_guards.transcript_summary_card_guard  # noqa: F401,E402

from stoney_verify.app import run


if __name__ == "__main__":
    start_process_health_loop()
    run()
