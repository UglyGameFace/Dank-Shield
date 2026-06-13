from __future__ import annotations

"""Compatibility loader for the stabilized ticket-panel doctor path."""


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_doctor_production_wording: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_doctor_production_wording: {message}")
    except Exception:
        pass


def apply() -> bool:
    try:
        from . import ticket_panel_doctor_stability_guard as stability
    except Exception as exc:
        _warn(f"could not import stability guard: {exc!r}")
        return False

    try:
        ok = bool(stability.apply())
        if ok:
            _log("delegated doctor command to stability guard")
        return ok
    except Exception as exc:
        _warn(f"stability guard apply failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
