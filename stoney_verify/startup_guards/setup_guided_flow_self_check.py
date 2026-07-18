from __future__ import annotations

"""Startup self-check for the guided /dank setup stack.

This does not change setup behavior. It makes deploys easier to verify by
checking that the guided setup guards imported and patched the expected public
setup hooks.
"""

import sys
from typing import Any

REQUIRED_GUARDS = (
    "stoney_verify.startup_guards.setup_save_next_step_guard",
)


def _load_visibility_health_guard() -> bool:
    try:
        from stoney_verify.startup_guards import setup_visibility_health_guard

        return bool(setup_visibility_health_guard.apply())
    except Exception as exc:
        try:
            print(f"⚠️ guided_setup_self_check visibility health guard failed: {exc!r}")
        except Exception:
            pass
        return False


def _has_wrapped_marker(obj: Any, marker: str) -> bool:
    try:
        return bool(getattr(obj, marker, False))
    except Exception:
        return False


def apply() -> bool:
    visibility_health_ok = _load_visibility_health_guard()
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

    if not visibility_health_ok:
        hook_warnings.append("visibility_health_not_loaded")

    if missing or hook_warnings:
        print(
            "⚠️ guided_setup_self_check incomplete "
            f"missing={missing or []} warnings={hook_warnings or []}"
        )
        return False

    print(
        "🧭 guided_setup_self_check ready; "
        "native_guided_review=ok save_next_steps=ok visibility_health=ok"
    )
    return True


apply()

__all__ = ["apply"]
