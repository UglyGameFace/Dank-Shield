from __future__ import annotations

"""Load and advertise the shared guild operation queue.

The actual queue lives in stoney_verify.operation_queue so commands, dashboard
API handlers, setup guards, and Channel Builder code can import one shared
implementation instead of each feature inventing its own locks.
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


def _load_integration_guards() -> None:
    for module_name in (
        "command_sync_operation_queue_guard",
        "channel_builder_api_guard",
        "channel_builder_rollback_api_guard",
        "verification_operation_queue_guard",
        "member_cleanup_operation_queue_guard",
        "spam_guard_operation_queue_guard",
    ):
        try:
            __import__(f"stoney_verify.startup_guards.{module_name}")
            _log(f"integration guard loaded module={module_name}")
        except Exception as e:
            _warn(f"integration guard failed module={module_name}: {e!r}")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        _load_integration_guards()
        return True
    _INSTALLED = True

    try:
        from ..operation_queue import operation_queue_health_summary

        summary: dict[str, Any] = operation_queue_health_summary()
        global_state = dict(summary.get("global") or {})
        _log(
            "loaded; shared guild operation queue active "
            f"max_global={global_state.get('max_global')} "
            f"persistence={global_state.get('persistence')}"
        )
        _load_integration_guards()
        return True
    except Exception as e:
        _warn(f"failed to load operation queue: {e!r}")
        return False


install()

__all__ = ["install"]
