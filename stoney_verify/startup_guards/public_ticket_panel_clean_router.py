from __future__ import annotations

"""Route the old public ticket-panel registrar to the clean direct flow.

This keeps the existing command registry stable while removing the old
modal/service-signature ticket creation path at runtime.
"""

import importlib
from typing import Any

_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_clean_router {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_clean_router {message}")
    except Exception:
        pass


def _install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        legacy = importlib.import_module("stoney_verify.commands_ext.public_tickettool_parity_polish")
        clean = importlib.import_module("stoney_verify.commands_ext.public_ticket_panel_clean")
    except Exception as e:
        _warn(f"could not import ticket panel modules: {e!r}")
        return

    clean_registrar = getattr(clean, "register_public_ticket_panel_clean", None)
    if not callable(clean_registrar):
        _warn("clean registrar missing; leaving legacy ticket panel registrar untouched")
        return

    def register_public_tickettool_parity_polish(bot: Any, tree: Any) -> None:
        return clean_registrar(bot, tree)

    setattr(legacy, "register_public_tickettool_parity_polish", register_public_tickettool_parity_polish)
    setattr(legacy, "__all__", ["register_public_tickettool_parity_polish"])

    _INSTALLED = True
    _log("active; legacy public_tickettool_parity_polish now delegates to public_ticket_panel_clean")


_install()


__all__ = []
