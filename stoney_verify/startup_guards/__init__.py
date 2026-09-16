from __future__ import annotations

"""Startup-guard package boundary.

Normal Dank Shield boot does **not** iterate a startup-guard registry. Production
startup ownership is explicit in ``main.py``, the host hooks, and canonical
feature modules. The tuple below is retained only as inert historical metadata
while older audits/tests are migrated away from treating registry membership as
runtime activation.

Importing this package must not activate process health or other unrelated
runtime behavior. Process health is installed and attached explicitly by
``main.py``.

Do not add an executable bulk loader here. Importing every historical guard would
reactivate old monkey patches, listeners, command-tree mutations, compatibility
layers, and schema-era code with duplicate ownership risk.
"""

from typing import Tuple


# Historical only. This is NOT an activation plan and nothing in this package
# iterates it. Keep the legacy private name temporarily because a few focused
# compatibility tests use the list as historical metadata; new code must use
# neither name to decide what runs in production. Retired/migrated owners are
# removed from this inventory as their ownership migration completes.
_STARTUP_GUARDS: Tuple[str, ...] = (
    "stoney_verify.startup_guards.embed_literal_newline_guard",
    "stoney_verify.startup_guards.global_interaction_trace_guard",
    "stoney_verify.startup_guards.slash_command_cleanup",
    "stoney_verify.startup_guards.public_verify_admin_command_skip",
    "stoney_verify.startup_guards.auto_schema_bootstrap",
    "stoney_verify.startup_guards.ticket_category_schema_bootstrap_guard",
    "stoney_verify.startup_guards.operation_queue_schema_guard",
    "stoney_verify.startup_guards.guild_operation_queue_guard",
    "stoney_verify.startup_guards.guild_config_write_safety",
    "stoney_verify.startup_guards.setup_category_modal_compat",
    "stoney_verify.startup_guards.spam_guard_invite_hard_block",
    "stoney_verify.startup_guards.spam_guard_default_state_guard",
    "stoney_verify.startup_guards.spam_guard_invite_override_options",
    "stoney_verify.startup_guards.discord_invite_blocker_runtime_guard",
    "stoney_verify.startup_guards.invite_live_enforcer_guard",
    "stoney_verify.startup_guards.protection_invite_target_precedence_guard",
    "stoney_verify.startup_guards.member_lifecycle_router_guard",
    "stoney_verify.startup_guards.member_lifecycle_verify_runtime_hardening",
    "stoney_verify.startup_guards.member_lifecycle_audit_context_guard",
    "stoney_verify.startup_guards.basic_verify_panel_auto_refresh_guard",
    "stoney_verify.startup_guards.profile_role_editor_guard",
    "stoney_verify.startup_guards.self_roles_command_guard",
    "stoney_verify.startup_guards.modlog_probot_parity_guard",
    "stoney_verify.startup_guards.vc_join_leave_modlog_labels_guard",
    "stoney_verify.startup_guards.automod_public_guard",
    "stoney_verify.startup_guards.protection_center_command_guard",
    "stoney_verify.startup_guards.protection_center_clear_categories_guard",
    "stoney_verify.startup_guards.embed_builder_command_guard",
    "stoney_verify.startup_guards.share_router_guard",
    "stoney_verify.startup_guards.setup_overview_command_guard",
    "stoney_verify.startup_guards.protection_pack_manual_import_guard",
    "stoney_verify.startup_guards.protection_import_button_patch",
    "stoney_verify.startup_guards.setup_permission_repair_modlog_silence_guard",
    "stoney_verify.startup_guards.dank_shield_branding_guard",
    "stoney_verify.startup_guards.invite_intent_safety",
    "stoney_verify.startup_guards.alt_identity_link_safety",
    "stoney_verify.startup_guards.member_join_removal_safety",
    "stoney_verify.startup_guards.guild_role_order_guard",
    "stoney_verify.members_new.role_state_compat_guard",
    "stoney_verify.startup_guards.setup_role_safety",
    "stoney_verify.commands_ext.public_moderation_command_guard",
    "stoney_verify.startup_guards.member_activity_notices_db_safety",
    "stoney_verify.startup_guards.member_update_modlog",
    "stoney_verify.startup_guards.resource_modlog_coverage",
    "stoney_verify.tickets_new.guild_config_ticket_guard",
    "stoney_verify.tickets_new.creation_category_guard",
    "stoney_verify.tickets_new.channel_panel_repair",
    "stoney_verify.tickets_new.category_enforcer",
    "stoney_verify.tickets_new.sync_native_guard",
    "stoney_verify.tickets_new.sync_alias_guard",
    "stoney_verify.api_new.guild_config_guard",
    "stoney_verify.tickets_new.panel_creation_guard_runtime",
    "stoney_verify.startup_guards.unverified_ticket_panel_flow",
    "stoney_verify.startup_guards.unverified_legacy_panel_patch_disable",
    "stoney_verify.startup_guards.vc_request_setup_clarity",
    "stoney_verify.startup_guards.vc_setup_one_press_fix",
    "stoney_verify.startup_guards.vc_per_guild_access_fix",
    "stoney_verify.startup_guards.public_no_env_runtime_config",
    "stoney_verify.startup_guards.ticket_panel_doctor_command",
    "stoney_verify.startup_guards.ticket_panel_doctor_production_wording",
    "stoney_verify.startup_guards.ticket_forms_foundation_guard",
    "stoney_verify.startup_guards.ticket_category_setup_guard",
    "stoney_verify.startup_guards.vc_accept_claim_guard",
    "stoney_verify.startup_guards.ticket_action_lock_guard",
    "stoney_verify.startup_guards.production_command_surface_guard",
    "stoney_verify.panel_bootstrap_runtime",
    "stoney_verify.startup_guards.event_safety",
    "stoney_verify.startup_guards.shard_safety",
    "stoney_verify.startup_guards.job_dedupe",
)

LEGACY_DORMANT_STARTUP_GUARDS: Tuple[str, ...] = _STARTUP_GUARDS


__all__ = [
    "LEGACY_DORMANT_STARTUP_GUARDS",
]
