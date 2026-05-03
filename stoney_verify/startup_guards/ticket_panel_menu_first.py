from __future__ import annotations

from typing import Any


def install_ticket_panel_menu_first_patch() -> bool:
    try:
        print("ticket_panel_menu_first disabled; existing ticket panel flow is authoritative")
    except Exception:
        pass
    return False


install_ticket_panel_menu_first_patch()

__all__ = ["install_ticket_panel_menu_first_patch"]
