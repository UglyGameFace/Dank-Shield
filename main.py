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
import stoney_verify.startup_guards.setup_check_existing_server_inference_guard  # noqa: F401,E402
import stoney_verify.startup_guards.setup_health_defer_guard  # noqa: F401,E402

# Make worker starter return values match the live task they create. This keeps
# startup logs from saying a worker was not started right before that worker says
# it started.
import stoney_verify.startup_guards.worker_start_return_guard  # noqa: F401,E402

# Compact giant known-good startup summaries without hiding warnings/errors.
import stoney_verify.startup_guards.public_runtime_log_hygiene  # noqa: F401,E402

# Validate saved per-guild IDs against the live Discord guild before runtime
# discovery trusts them.
import stoney_verify.startup_guards.guild_config_runtime_validator  # noqa: F401,E402

# Keep dashboard bot-command worker role decisions scoped to the command guild.
import stoney_verify.startup_guards.bot_command_worker_public_config_guard  # noqa: F401,E402

# Prevent automatic verification fail-closed removal of established members.
import stoney_verify.startup_guards.verification_established_member_safety  # noqa: F401,E402

# Passive feature: alert staff when established members lose all safe access roles.
import stoney_verify.startup_guards.verification_role_drift_monitor  # noqa: F401,E402

# Optional per-server feature: remove pending users who never start verification.
import stoney_verify.startup_guards.verification_idle_kick_feature  # noqa: F401,E402

# Probe optional REST-readable tables and print exact migration guidance.
import stoney_verify.startup_guards.optional_schema_health  # noqa: F401,E402

# Shortcut command for the same setup feature scoreboard shown in Health Check.
import stoney_verify.startup_guards.setup_scoreboard_command  # noqa: F401,E402

# Show optional no-start auto-remove status in the setup health scoreboard.
import stoney_verify.startup_guards.setup_idle_kick_scoreboard_guard  # noqa: F401,E402

# Plain-language setup labels/help text for normal Discord server owners.
import stoney_verify.startup_guards.setup_ux_clarity_guard  # noqa: F401,E402

# Ensure Ticket Basics can save every field the setup scoreboard requires.
import stoney_verify.startup_guards.setup_ticket_transcripts_picker_guard  # noqa: F401,E402

# Add per-server controls for optional no-start verification auto-remove.
import stoney_verify.startup_guards.setup_verification_idle_kick_controls  # noqa: F401,E402

# Ticket categories can hit Discord's child-channel limit. Load overflow routing
# before extra ticket UI patches so creation/reopen paths choose a usable parent.
import stoney_verify.startup_guards.ticket_overflow_category_guard  # noqa: F401,E402

# Ticket categories can optionally define dashboard-managed form questions.
import stoney_verify.startup_guards.ticket_forms_foundation_guard  # noqa: F401,E402

# Store completed form answers for dashboard views when the DB migration exists.
import stoney_verify.startup_guards.ticket_form_answer_storage_guard  # noqa: F401,E402

# Global safety backbone for all dangerous guild mutations. Setup and future
# Channel Builder actions use this to prevent spam-click duplicate jobs.
import stoney_verify.startup_guards.guild_operation_queue_guard  # noqa: F401,E402

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
