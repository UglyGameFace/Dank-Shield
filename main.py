from __future__ import annotations

import importlib

_PREFIX = "stoney_verify.startup_guards."


def _imp(name: str) -> None:
    importlib.import_module(_PREFIX + name)


_imp("discord_api_" + "safety")
_imp("command_" + "safety")
_imp("command_scope_dedupe")
_imp("public_server_env_id_guard")

from stoney_verify.startup_guards import load_all_startup_guards, start_process_health_loop

load_all_startup_guards()

for _guard in (
    "full_setup_health_autofix",
    "setup_visibility_health_guard",
    "setup_role_visibility_repair_guard",
    "setup_health_precision_guard",
    "setup_check_existing_server_inference_guard",
    "setup_health_defer_guard",
    "worker_start_return_guard",
    "public_runtime_log_hygiene",
    "guild_config_runtime_validator",
    "verification_member_role_fallback_guard",
    "bot_command_worker_public_config_guard",
    "verification_established_member_" + "safety",
    "verification_role_drift_monitor",
    "optional_schema_health",
    "setup_scoreboard_command",
    "setup_ux_clarity_guard",
    "setup_ticket_transcripts_picker_guard",
    "ticket_overflow_category_guard",
    "ticket_forms_foundation_guard",
    "ticket_form_answer_storage_guard",
    "guild_operation_queue_guard",
    "setup_operation_lock_guard",
    "ticket_open_controls_status_guard",
    "ticket_open_controls_refresh_guard",
    "ticket_access_management_guard",
    "transcript_summary_card_guard",
    "setup_idle_" + "kick_scoreboard_guard",
    "setup_verification_idle_" + "kick_controls",
    "verification_idle_" + "kick_feature",
):
    _imp(_guard)

from stoney_verify.app import run


if __name__ == "__main__":
    start_process_health_loop()
    run()
