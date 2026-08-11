from __future__ import annotations

"""Load and advertise the shared guild operation queue.

Integration guards remain only for legacy flows that have not yet moved their
queue call into the canonical owner module. New/updated flows must import
``stoney_verify.operation_queue`` directly.
"""

from typing import Any

_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"🧱 guild_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_operation_queue_guard {message}")
    except Exception:
        pass


def _load_remaining_legacy_integrations() -> None:
    for module_name in (
        "command_sync_operation_queue_guard",
        "verification_operation_queue_guard",
        "spam_guard_operation_queue_guard",
    ):
        try:
            __import__(f"stoney_verify.startup_guards.{module_name}")
            _log(f"legacy integration loaded module={module_name}")
        except Exception as e:
            _warn(f"legacy integration failed module={module_name}: {e!r}")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        _load_remaining_legacy_integrations()
        return True
    _INSTALLED = True

    try:
        from ..operation_queue import (
            ensure_operation_queue_started_background,
            operation_queue_health_summary,
        )

        ensure_operation_queue_started_background()
        summary: dict[str, Any] = operation_queue_health_summary()
        global_state = dict(summary.get("global") or {})
        _log(
            "loaded; canonical guild operation queue active "
            f"max_global={global_state.get('max_global')} "
            f"max_per_guild={global_state.get('max_per_guild')} "
            f"max_per_type={global_state.get('max_per_type')} "
            f"persistence={global_state.get('persistence')}"
        )
        _load_remaining_legacy_integrations()
        return True
    except Exception as e:
        _warn(f"failed to load operation queue: {e!r}")
        return False


install()

__all__ = ["install"]
