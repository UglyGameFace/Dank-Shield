from __future__ import annotations

# Load Discord API throttling/retry safety before the app imports anything that
# can call audit logs, send modlogs, or edit ticket channels.
import stoney_verify.startup_guards.discord_api_safety  # noqa: F401

# Keep production/public slash commands on one surface. This runs before app.py
# so the app does not create beta guild command copies unless explicitly enabled.
import stoney_verify.startup_guards.command_safety  # noqa: F401
import stoney_verify.startup_guards.command_scope_dedupe  # noqa: F401

# Public production must never read deployment-level Discord role/channel/
# category/home-guild IDs. This runs before the package guard loader and before
# app.py imports globals consumers.
import stoney_verify.startup_guards.public_server_env_id_guard  # noqa: F401
# =====================================================
# SAFE MINIMAL STARTUP GUARDS (Production Audit Fix)
# Only keeping essential safety guards. Everything else
# has been commented out for stability and maintainability.
# =====================================================

from stoney_verify.startup_guards import (
    load_all_startup_guards,

    # Core safety guards (keep these)
    discord_api_safety,
    command_safety,
    command_scope_dedupe,
    public_server_env_id_guard,
    guild_config_runtime_validator,
    interaction_action_lock_guard,
)

# ============================================================
# COMMENTED OUT (can be re-enabled later if needed)
# Most of these are redundant patches or non-critical guards.
# ============================================================

# from stoney_verify.startup_guards import (
#     full_setup_health_autofix,
#     setup_role_visibility_repair_guard,
#     setup_health_precision_guard,
#     setup_health_defer_guard,
#     setup_health_next_action_guard,
#     setup_health_action_buttons_guard,
#     setup_scoreboard_command,
#     setup_idle_kick_scoreboard_guard,
#     setup_feature_health_scoreboard,
#     setup_permission_repair_guard,
#     setup_permission_repair_truth_guard,
#     setup_permission_repair_modlog_silence_guard,
#     setup_permission_repair_preview_clarity_guard,
#     setup_safety_repair_service_guard,
#     setup_role_safety,
#     setup_visibility_health_guard,
#     setup_ux_clarity_guard,
#     setup_first_run_ux_guard,
#     setup_guided_flow_self_check,
#     setup_check_ready_next_step_guard,
#     setup_save_next_step_guard,
#     setup_success_next_step_guard,
#     setup_smart_home_menu_guard,
#     setup_service_navigation_guard,
#     setup_service_modes,
#     setup_modal_defer_compat_guard,
#     setup_operation_lock_guard,
#     setup_overview_command_guard,
#     setup_picker_permission_error_guard,
#     setup_check_existing_server_inference_guard,
#     setup_ticket_transcripts_picker_guard,
#     setup_ticket_tool_style_setup_guard,
#     setup_category_modal_compat,
#     setup_channel_font_mode_guard,
#     setup_verification_toggle_independence_guard,
#     setup_verification_idle_kick_controls,
#     setup_vc_health_precision_guard,
#     # ... (many more commented out for safety)
# )

# =====================================================
# DISCORD BOT ENTRYPOINT
# Discloud starts main.py, so main.py must hand off to
# stoney_verify.app where bot.run(DISCORD_TOKEN) lives.
# =====================================================

def main() -> None:
    from stoney_verify.app import run as _run_dank_shield
    _run_dank_shield()


if __name__ == "__main__":
    main()

