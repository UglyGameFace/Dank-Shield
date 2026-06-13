from __future__ import annotations

"""Compatibility loader for stabilized ticket/setup checks."""


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


def _apply_optional_guard(module_name: str, label: str) -> bool:
    try:
        module = __import__(f"stoney_verify.startup_guards.{module_name}", fromlist=["apply"])
    except Exception as exc:
        _warn(f"could not import {label}: {exc!r}")
        return False
    try:
        ok = bool(module.apply())
        if ok:
            _log(f"loaded {label}")
        return ok
    except Exception as exc:
        _warn(f"{label} apply failed: {exc!r}")
        return False


def apply() -> bool:
    epoch_ok = _apply_optional_guard("ticket_panel_command_epoch_guard", "ticket-panel slash refresh guard")
    identity_ok = _apply_optional_guard("ticket_staff_identity_guard", "ticket staff identity guard")
    controls_ok = _apply_optional_guard("ticket_open_controls_status_guard", "open ticket controls status guard")
    repair_ok = _apply_optional_guard("ticket_panel_repair_records_command", "ticket record repair command")
    doctor_ok = _apply_optional_guard("ticket_panel_doctor_stability_guard", "doctor stability guard")
    setup_ok = _apply_optional_guard("setup_check_existing_server_inference_guard", "setup-check inference guard")
    return bool(epoch_ok and identity_ok and controls_ok and repair_ok and doctor_ok and setup_ok)


apply()

__all__ = ["apply"]
