from __future__ import annotations

import builtins
import importlib
import os
from contextlib import contextmanager
from types import ModuleType
from typing import Dict, Iterable, Iterator, Tuple

from .process_health import start_health_loop as start_process_health_loop

_LOADED: Dict[str, ModuleType] = {}
_ERRORS: Dict[str, BaseException] = {}

_STARTUP_GUARDS: Tuple[str, ...] = (
    "stoney_verify.startup_guards.process_health",
    "stoney_verify.startup_guards.command_safety",
    "stoney_verify.startup_guards.slash_command_cleanup",
    "stoney_verify.startup_guards.public_verify_admin_command_skip",
    "stoney_verify.startup_guards.auto_schema_bootstrap",
    "stoney_verify.startup_guards.operation_queue_schema_guard",
    "stoney_verify.startup_guards.guild_operation_queue_guard",
    "stoney_verify.startup_guards.guild_config_write_safety",
    "stoney_verify.startup_guards.setup_category_modal_compat",
    "stoney_verify.startup_guards.setup_service_modes",
    "stoney_verify.startup_guards.setup_feature_health_scoreboard",
    "stoney_verify.startup_guards.dank_shield_branding_guard",
    "stoney_verify.startup_guards.runtime_safety",
    "stoney_verify.startup_guards.invite_intent_safety",
    "stoney_verify.startup_guards.raidguard_hard_stop",
    "stoney_verify.startup_guards.raidguard_bot_heuristics",
    "stoney_verify.startup_guards.raidguard_risk_engine_v2",
    "stoney_verify.startup_guards.alt_identity_link_safety",
    "stoney_verify.startup_guards.member_join_removal_safety",
    "stoney_verify.members_new.role_state_compat_guard",
    "stoney_verify.startup_guards.setup_role_safety",
    "stoney_verify.commands_ext.public_moderation_command_guard",
    "stoney_verify.startup_guards.member_update_modlog",
    "stoney_verify.startup_guards.resource_modlog_coverage",
    "stoney_verify.tickets_new.guild_config_ticket_guard",
    "stoney_verify.tickets_new.creation_category_guard",
    "stoney_verify.tickets_new.channel_panel_repair",
    "stoney_verify.tickets_new.category_enforcer",
    "stoney_verify.tickets_new.sync_native_guard",
    "stoney_verify.tickets_new.sync_alias_guard",
    "stoney_verify.api_new.guild_config_guard",
    "stoney_verify.startup_guards.api_operation_queue_guard",
    "stoney_verify.tickets_new.panel_creation_guard_runtime",
    "stoney_verify.startup_guards.unverified_ticket_panel_flow",
    "stoney_verify.startup_guards.unverified_legacy_panel_patch_disable",
    "stoney_verify.startup_guards.vc_request_setup_clarity",
    "stoney_verify.startup_guards.vc_setup_one_press_fix",
    "stoney_verify.startup_guards.vc_per_guild_access_fix",
    "stoney_verify.startup_guards.public_no_env_runtime_config",
    "stoney_verify.startup_guards.legacy_public_ticket_panel_disable",
    "stoney_verify.startup_guards.ticket_panel_doctor_command",
    "stoney_verify.startup_guards.ticket_panel_doctor_production_wording",
    "stoney_verify.startup_guards.public_ticket_panel_clean_hardening",
    "stoney_verify.startup_guards.external_ticket_history_sequence_guard",
    "stoney_verify.startup_guards.vc_accept_claim_guard",
    "stoney_verify.startup_guards.ticket_action_lock_guard",
    "stoney_verify.startup_guards.ticket_delete_lifecycle_guard",
    "stoney_verify.panel_bootstrap_runtime",
    "stoney_verify.startup_guards.public_startup_scope",
    "stoney_verify.startup_guards.event_safety",
    "stoney_verify.startup_guards.shard_safety",
    "stoney_verify.startup_guards.job_dedupe",
)

_IMPORT_CHATTER_PREFIXES: Tuple[str, ...] = ("🧷 ", "🌐 public_startup_scope loaded", "🩹 ", "🔗 ", "🧯 raidguard_hard_stop patched", "🧪 ")
_ERROR_CHATTER_PREFIXES: Tuple[str, ...] = ("⚠️ ", "❌ ", "🛑 ")


def _log_style() -> str:
    return os.getenv("STONEY_STARTUP_LOG_STYLE", "compact").strip().lower()


@contextmanager
def _maybe_suppress_import_chatter(module_name: str) -> Iterator[None]:
    if _log_style() not in {"compact", "quiet"}:
        yield
        return
    original_print = builtins.print

    def filtered_print(*args, **kwargs):
        try:
            message = " ".join(str(arg) for arg in args)
        except Exception:
            message = ""
        if any(message.startswith(prefix) for prefix in _ERROR_CHATTER_PREFIXES):
            return original_print(*args, **kwargs)
        if any(message.startswith(prefix) for prefix in _IMPORT_CHATTER_PREFIXES):
            return None
        return original_print(*args, **kwargs)

    builtins.print = filtered_print
    try:
        yield
    finally:
        builtins.print = original_print


def load_startup_guards(modules: Iterable[str] = _STARTUP_GUARDS) -> Dict[str, ModuleType]:
    for module_name in modules:
        if module_name in _LOADED:
            continue
        try:
            with _maybe_suppress_import_chatter(module_name):
                _LOADED[module_name] = importlib.import_module(module_name)
        except Exception as exc:
            _ERRORS[module_name] = exc
            print(f"⚠️ startup_guard loader failed module={module_name}: {exc!r}")
    if _log_style() != "quiet":
        print(f"🧩 startup_guard loader complete loaded={len(_LOADED)}")
    return dict(_LOADED)


load_all_startup_guards = load_startup_guards


def startup_guard_errors() -> Dict[str, BaseException]:
    return dict(_ERRORS)


__all__ = ["load_all_startup_guards", "load_startup_guards", "start_process_health_loop", "startup_guard_errors"]
