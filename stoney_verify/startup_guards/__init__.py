from __future__ import annotations

"""Startup guard loader for Dank Shield.

The guard modules still need to load before ``stoney_verify.app`` because they
patch/import-wrap older production paths while the project is being fully
refactored. Keeping the order here makes ``main.py`` small and makes future
cleanup obvious.

Logging policy:
- compact by default for public deployments
- warnings/errors are always shown
- verbose import chatter can be restored with STONEY_STARTUP_LOG_STYLE=verbose
"""

import builtins
import importlib
import os
from contextlib import contextmanager
from types import ModuleType
from typing import Dict, Iterable, Iterator, Tuple

from .process_health import start_health_loop as start_process_health_loop

_LOADED: Dict[str, ModuleType] = {}
_ERRORS: Dict[str, BaseException] = {}

# Keep this order intentional. Some later guards depend on earlier import hooks
# already being installed.
_STARTUP_GUARDS: Tuple[str, ...] = (
    "stoney_verify.startup_guards.process_health",
    "stoney_verify.startup_guards.command_safety",
    "stoney_verify.startup_guards.slash_command_cleanup",

    # Public command-surface hygiene: stop legacy verify-admin side-effect
    # commands from registering before events.py imports the old module.
    "stoney_verify.startup_guards.public_verify_admin_command_skip",

    # Optional idempotent DB table/column creation. This only runs when a direct
    # Postgres DSN is configured; Supabase REST cannot create missing tables.
    "stoney_verify.startup_guards.auto_schema_bootstrap",

    # Central config write protection must load before setup/verify/ticket
    # modules can write guild_configs. It prevents accidental overwrites of
    # owner-picked roles/channels/categories and makes discovery fill blanks only.
    "stoney_verify.startup_guards.guild_config_write_safety",

    # Hotfix: old advanced setup persistent buttons still call
    # public_setup_solid.AddTicketCategoryModal. Keep that attribute restored on
    # the deployed main branch so Add Custom Menu Option does not crash.
    "stoney_verify.startup_guards.setup_category_modal_compat",

    # Phase 2: service-mode setup picker for Tickets-only / Verification-only /
    # SpamGuard-only / combinations, plus service-focused health checks.
    "stoney_verify.startup_guards.setup_service_modes",

    # Product-grade service readiness scoreboard on the existing Health Check.
    "stoney_verify.startup_guards.setup_feature_health_scoreboard",

    # Normalize remaining legacy public text to Dank Shield + /dank setup.
    "stoney_verify.startup_guards.dank_shield_branding_guard",

    # Broad event-loop DB/modlog/ticket safety layer.
    # sitecustomize.py remains as a tiny host fallback, but main startup loads
    # the real package module directly.
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

    # DB-backed panel creation enforcement.
    # This wraps tickets_new.service.create_ticket_channel so panel rules,
    # per-owner limits, and concurrency protection apply even when the huge
    # panel.py flow calls create_ticket_channel directly.
    "stoney_verify.tickets_new.panel_creation_guard_runtime",

    # Keep verification helper functions available for the clean panel, but then
    # immediately disable its old legacy TicketPanelView patch side effect.
    "stoney_verify.startup_guards.unverified_ticket_panel_flow",
    "stoney_verify.startup_guards.unverified_legacy_panel_patch_disable",

    # Make VC setup failures readable instead of saying only that the staff
    # panel could not be posted, and make setup health check the real VC path.
    "stoney_verify.startup_guards.vc_request_setup_clarity",

    # Add a one-press setup button that repairs common VC permission blockers.
    "stoney_verify.startup_guards.vc_setup_one_press_fix",

    # Force VC staff Accept/Reissue to use the per-guild saved voice channel
    # instead of the old global/env fallback channel id.
    "stoney_verify.startup_guards.vc_per_guild_access_fix",

    # Public production isolation: VC and verification approval must use the
    # current server's saved setup config, never deployment/global .env IDs.
    "stoney_verify.startup_guards.public_no_env_runtime_config",

    # Disable stale public TicketPanelView creation while keeping staff ticket
    # channel action controls alive. The clean ticket panel below is canonical.
    "stoney_verify.startup_guards.legacy_public_ticket_panel_disable",

    # Add `/ticket-panel doctor` before public_ticket_panel_clean registers the
    # app command group so the doctor subcommand ships with /ticket-panel post.
    "stoney_verify.startup_guards.ticket_panel_doctor_command",

    # Production wording layer for `/ticket-panel doctor`; it keeps diagnostics
    # useful for normal server owners who did not build the bot with us.
    "stoney_verify.startup_guards.ticket_panel_doctor_production_wording",

    # Harden the live public category-menu ticket panel without introducing a
    # second ticket creation path. This fixes duplicate/wrong menu rows and
    # prevents ticket numbers from restarting at #0001 on existing servers.
    "stoney_verify.startup_guards.public_ticket_panel_clean_hardening",

    # Final numbering authority: external/imported ticket-bot history is visible
    # in diagnostics but cannot control Dank Shield's new ticket sequence.
    "stoney_verify.startup_guards.external_ticket_history_sequence_guard",

    # VC Accept must claim the ticket through tickets_new.service.assign_ticket
    # before granting voice access, so claimed-by state and logs stay correct.
    "stoney_verify.startup_guards.vc_accept_claim_guard",

    # Serialize ticket channel controls so double-clicks or two staff clicking at
    # once cannot duplicate messages or race ticket state.
    "stoney_verify.startup_guards.ticket_action_lock_guard",

    # Better-than-TicketTool safety: open tickets must be closed before delete,
    # so transcript/archive/audit lifecycle stays predictable.
    "stoney_verify.startup_guards.ticket_delete_lifecycle_guard",

    # DB-backed panel/config bootstrap runtime.
    # This self-registers on_ready/on_guild_join listeners and starts the
    # panel bootstrap worker after the bot is ready. It does not create roles,
    # channels, or post panels automatically.
    "stoney_verify.panel_bootstrap_runtime",

    "stoney_verify.startup_guards.public_startup_scope",
    "stoney_verify.startup_guards.event_safety",
    "stoney_verify.startup_guards.shard_safety",
    "stoney_verify.startup_guards.job_dedupe",
)

_IMPORT_CHATTER_PREFIXES: Tuple[str, ...] = (
    "🧷 ",
    "🌐 public_startup_scope loaded",
    "🩹 ",
    "🔗 ",
    "🧯 raidguard_hard_stop patched",
    "🧪 ",
)

_ERROR_CHATTER_PREFIXES: Tuple[str, ...] = (
    "⚠️ ",
    "❌ ",
    "🛑 ",
)


def _log_style() -> str:
    return os.getenv("STONEY_STARTUP_LOG_STYLE", "compact").strip().lower()


@contextmanager
def _maybe_suppress_import_chatter(module_name: str) -> Iterator[None]:
    if _log_style() not in {"compact", "quiet"}:
        yield
        return

    original_print = builtins.print

    def filtered_print(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            message = " ".join(str(arg) for arg in args)
        except Exception:
            message = ""
        if any(message.startswith(prefix) for prefix in _ERROR_CHATTER_PREFIXES):
            return original_print(*args, **kwargs)
        if any(message.startswith(prefix) for prefix in _IMPORT_CHATTER_PREFIXES):
            return None
        return original_print(*args, **kwargs)

    builtins.print = filtered_print  # type: ignore[assignment]
    try:
        yield
    finally:
        builtins.print = original_print  # type: ignore[assignment]


def load_startup_guards(modules: Iterable[str] = _STARTUP_GUARDS) -> Dict[str, ModuleType]:
    for module_name in modules:
        if module_name in _LOADED:
            continue
        try:
            with _maybe_suppress_import_chatter(module_name):
                _LOADED[module_name] = importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - startup diagnostics only
            _ERRORS[module_name] = exc
            print(f"⚠️ startup_guard loader failed module={module_name}: {exc!r}")
    if _log_style() != "quiet":
        print(f"🧩 startup_guard loader complete loaded={len(_LOADED)}")
    return dict(_LOADED)


def startup_guard_errors() -> Dict[str, BaseException]:
    return dict(_ERRORS)


__all__ = [
    "load_startup_guards",
    "start_process_health_loop",
    "startup_guard_errors",
]
