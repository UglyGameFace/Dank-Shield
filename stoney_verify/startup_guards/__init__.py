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

_LOADED: Dict[str, ModuleType] = {}
_ERRORS: Dict[str, BaseException] = {}

# Keep this order intentional. Some later guards depend on earlier import hooks
# already being installed.
_STARTUP_GUARDS: Tuple[str, ...] = (
    "stoney_verify.startup_guards.process_health",
    "stoney_verify.startup_guards.command_safety",
    "stoney_verify.startup_guards.slash_command_cleanup",

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

    # Harden the live public category-menu ticket panel without introducing a
    # second ticket creation path. This fixes duplicate/wrong menu rows and
    # prevents ticket numbers from restarting at #0001 on existing servers.
    "stoney_verify.startup_guards.public_ticket_panel_clean_hardening",

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
    "🧠 ",
    "🧭 setup_category_modal_compat patched",
    "🧭 setup_service_modes installed",
    "🛡️ dank_shield_branding_guard active",
    "🛡️ member_join_removal_safety patched",
    "🛡️ member_join_removal_safety attached",
    "🛡️ member_join_removal_safety loaded",
    "🧾 role_state_compat_guard loaded",
    "🪄 public_moderation_command_guard loaded",
    "✅ public_member_update_modlog:",
    "🧍 member_update_modlog registered",
    "🧾 resource_modlog_coverage registered",
    "🧭 guild_config_ticket_guard loaded",
    "🎫 ticket_creation_category_guard loaded",
    "🧰 ticket_channel_panel_repair attached",
    "🧰 ticket_channel_panel_repair loaded",
    "🎯 ticket_category_enforcer attached",
    "🎯 ticket_category_enforcer loaded",
    "🧩 ticket_sync_native_guard loaded",
    "🧭 ticket_sync_alias_guard loaded",
    "🧭 api_guild_config_guard loaded",
    "🛡️ panel_creation_guard_runtime panel denial",
    "🛡️ panel_creation_guard_runtime ticket creation guard installed",
    "🎫 ticket_creation_category_guard patched",
    "🎫 ticket_creation_category_guard updated",
    "🧰 ticket_channel_panel_repair patched",
    "🎯 ticket_category_enforcer patched",
    "🎟️ unverified_ticket_panel_flow patched",
    "✅ unverified_legacy_panel_patch_disable:",
    "✅ vc_request_setup_clarity:",
    "✅ vc_setup_one_press_fix:",
    "✅ vc_per_guild_access_fix:",
    "✅ public_no_env_runtime_config:",
    "✅ legacy_public_ticket_panel_disable:",
    "✅ public_ticket_panel_clean_hardening:",
    "✅ vc_accept_claim_guard:",
    "✅ ticket_action_lock_guard:",
    "✅ ticket_delete_lifecycle_guard:",
    "🧩 panel_bootstrap_runtime runtime listeners registered",
    "🧯 event_safety loaded",
    "🛰️ shard_safety patched",
    "🛰️ shard_safety loaded",
    "🧬 job_dedupe loaded",
)

_IMPORT_CHATTER_CONTAINS: Tuple[str, ...] = (
    " loaded; ",
    " patched ",
    " attached ",
    " registered ",
    " enabled ",
)


def _env_str(name: str, default: str = "") -> str:
    try:
        value = os.getenv(name)
        return str(value).strip() if value is not None and str(value).strip() else default
    except Exception:
        return default


def _startup_log_style() -> str:
    return _env_str("STONEY_STARTUP_LOG_STYLE", "compact").lower()


def _verbose_startup_logs() -> bool:
    return _startup_log_style() in {"verbose", "debug", "trace", "full"}


def _should_suppress_import_line(text: str) -> bool:
    if _verbose_startup_logs():
        return False
    line = str(text or "")
    if not line:
        return False

    # Never hide warnings, blockers, crashes, or actual errors.
    if line.startswith(("⚠️", "🚫", "❌", "Traceback", "RuntimeError", "Error")):
        return False

    if any(line.startswith(prefix) for prefix in _IMPORT_CHATTER_PREFIXES):
        return True

    # Only suppress low-value import chatter from startup guard modules. This is
    # intentionally conservative so runtime health, Discord, DB, and ticket sync
    # logs still show normally.
    if any(token in line for token in _IMPORT_CHATTER_CONTAINS):
        if any(
            name in line
            for name in (
                "safety",
                "guard",
                "startup",
                "ticket_",
                "guild_config",
                "raidguard",
                "panel_bootstrap",
                "vc_setup",
                "public_no_env",
                "auto_schema",
            )
        ):
            return True

    return False


@contextmanager
def _compact_import_print_filter() -> Iterator[None]:
    if _verbose_startup_logs():
        yield
        return

    original_print = builtins.print

    def filtered_print(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            text = " ".join(str(arg) for arg in args)
            if _should_suppress_import_line(text):
                return None
        except Exception:
            pass
        return original_print(*args, **kwargs)

    builtins.print = filtered_print
    try:
        yield
    finally:
        builtins.print = original_print


def _log(message: str) -> None:
    try:
        print(f"🧩 startup_guards {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ startup_guards {message}")
    except Exception:
        pass


def _refresh_late_runtime_patches() -> None:
    try:
        guard = _LOADED.get("stoney_verify.tickets_new.panel_creation_guard_runtime")
        if guard is None:
            return

        refresher = getattr(guard, "refresh_panel_creation_guard_patch_targets", None)
        if callable(refresher):
            refresher()
    except Exception as e:
        _warn(f"late runtime patch refresh failed: {e!r}")


def load_startup_guard(module_name: str) -> ModuleType | None:
    """Import one startup guard safely and remember the result."""
    if module_name in _LOADED:
        return _LOADED[module_name]

    try:
        module = importlib.import_module(module_name)
        _LOADED[module_name] = module
        _ERRORS.pop(module_name, None)
        return module
    except Exception as e:
        _ERRORS[module_name] = e
        _warn(f"failed to import {module_name}: {e!r}")
        return None


def load_all_startup_guards(extra_guards: Iterable[str] | None = None) -> Dict[str, ModuleType]:
    """Load all pre-app startup guards in the safest known order.

    This function is intentionally tolerant. One guard failing should be logged,
    but it should not hide the real startup error by crashing before the bot can
    report diagnostics.
    """
    ordered = list(_STARTUP_GUARDS)
    if extra_guards:
        for name in extra_guards:
            if name and name not in ordered:
                ordered.append(name)

    with _compact_import_print_filter():
        for module_name in ordered:
            load_startup_guard(module_name)
        _refresh_late_runtime_patches()

    _log(f"loaded={len(_LOADED)} failed={len(_ERRORS)} mode={_startup_log_style()}")
    if _ERRORS:
        for name, error in _ERRORS.items():
            _warn(f"{name}: {error!r}")
    return dict(_LOADED)


def startup_guard_errors() -> Dict[str, BaseException]:
    """Return startup guard import errors for diagnostics/tests."""
    return dict(_ERRORS)


def start_process_health_loop() -> None:
    """Start the process health heartbeat loop if that guard is available."""
    module = _LOADED.get("stoney_verify.startup_guards.process_health")
    if module is None:
        module = load_startup_guard("stoney_verify.startup_guards.process_health")

    try:
        starter = getattr(module, "start_health_loop", None)
        if callable(starter):
            starter()
    except Exception as e:
        _warn(f"process health loop start failed: {e!r}")


__all__ = [
    "load_all_startup_guards",
    "load_startup_guard",
    "startup_guard_errors",
    "start_process_health_loop",
]
