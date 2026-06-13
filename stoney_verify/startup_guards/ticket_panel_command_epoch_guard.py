from __future__ import annotations

"""Force a one-time ticket-panel slash command refresh.

This guard bumps the slash cleanup epoch after command-surface changes around
`/ticket-panel health`, `/ticket-panel doctor`, and `/ticket-panel repair-records`.
It avoids an unchanged-sync skip so Discord receives the new group shape and
clients stop showing stale "command outdated" copies after deployment.
"""

_EPOCH = "2026-06-13-ticket-panel-health-doctor-repair-v1"


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_command_epoch_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_command_epoch_guard: {message}")
    except Exception:
        pass


def apply() -> bool:
    try:
        from . import slash_command_cleanup as cleanup
    except Exception as exc:
        _warn(f"could not import slash_command_cleanup: {exc!r}")
        return False

    try:
        previous = str(getattr(cleanup, "COMMAND_CLEANUP_EPOCH", "") or "")
        if previous != _EPOCH:
            cleanup.COMMAND_CLEANUP_EPOCH = _EPOCH
            _log(f"bumped slash cleanup epoch from {previous or 'unset'} to {_EPOCH}")
        return True
    except Exception as exc:
        _warn(f"epoch patch failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
