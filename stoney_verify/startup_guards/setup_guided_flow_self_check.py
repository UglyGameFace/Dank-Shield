from __future__ import annotations

"""Startup self-check for the guided /dank setup stack.

This does not change setup behavior. It makes deploys easier to verify by
checking that the guided setup guards imported and patched the expected public
setup hooks.
"""

import sys
from typing import Any

REQUIRED_GUARDS = (
    "stoney_verify.startup_guards.setup_first_run_ux_guard",
    "stoney_verify.startup_guards.setup_success_next_step_guard",
    "stoney_verify.startup_guards.setup_health_next_action_guard",
    "stoney_verify.startup_guards.setup_health_action_buttons_guard",
    "stoney_verify.startup_guards.setup_save_next_step_guard",
)


def _has_wrapped_marker(obj: Any, marker: str) -> bool:
    try:
        return bool(getattr(obj, marker, False))
    except Exception:
        return False


def apply() -> bool:
    missing = [name.rsplit(".", 1)[-1] for name in REQUIRED_GUARDS if name not in sys.modules]
    hook_warnings: list[str] = []

    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not callable(getattr(solid, "_build_main_setup_payload", None)):
            hook_warnings.append("main_payload_missing")
        if not callable(getattr(solid, "_build_health_embed", None)):
            hook_warnings.append("health_builder_missing")
        if not callable(getattr(solid, "_edit_or_followup", None)):
            hook_warnings.append("edit_or_followup_missing")
        elif not _has_wrapped_marker(getattr(solid, "_edit_or_followup"), "_health_action_buttons_wrapped"):
            hook_warnings.append("health_action_buttons_not_wrapped")
    except Exception as exc:
        hook_warnings.append(f"public_setup_solid_error:{type(exc).__name__}")

    try:
        import discord

        send_message = getattr(discord.InteractionResponse, "send_message", None)
        if not callable(send_message):
            hook_warnings.append("interaction_send_missing")
        elif not _has_wrapped_marker(send_message, "_setup_save_next_step_wrapped"):
            hook_warnings.append("save_next_step_not_wrapped")
    except Exception as exc:
        hook_warnings.append(f"discord_hook_error:{type(exc).__name__}")

    if missing or hook_warnings:
        print(
            "⚠️ guided_setup_self_check incomplete "
            f"missing={missing or []} warnings={hook_warnings or []}"
        )
        return False

    print(
        "🧭 guided_setup_self_check ready; "
        "guided_setup_guards=5 health_actions=ok save_next_steps=ok"
    )
    return True


apply()

__all__ = ["apply"]
