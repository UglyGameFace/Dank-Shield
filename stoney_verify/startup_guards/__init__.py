from __future__ import annotations

"""Startup guard loader for Stoney Verify.

The guard modules still need to load before ``stoney_verify.app`` because they
patch/import-wrap older production paths while the project is being fully
refactored. Keeping the order here makes ``main.py`` small and makes future
cleanup obvious.
"""

import importlib
from types import ModuleType
from typing import Dict, Iterable, Tuple

_LOADED: Dict[str, ModuleType] = {}
_ERRORS: Dict[str, BaseException] = {}

# Keep this order intentional. Some later guards depend on earlier import hooks
# already being installed.
_STARTUP_GUARDS: Tuple[str, ...] = (
    "stoney_verify.startup_guards.process_health",
    "stoney_verify.startup_guards.command_safety",

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
    "stoney_verify.startup_guards.fresh_join_role_recovery",
    "stoney_verify.members_new.role_state_compat_guard",
    "stoney_verify.startup_guards.setup_role_safety",
    "stoney_verify.commands_ext.public_ticket_panel_command_guard",
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

    for module_name in ordered:
        load_startup_guard(module_name)

    _log(f"loaded={len(_LOADED)} failed={len(_ERRORS)}")
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
