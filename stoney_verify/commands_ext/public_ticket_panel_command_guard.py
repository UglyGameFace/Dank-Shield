from __future__ import annotations

from typing import Any


def register_public_ticket_panel_command_guard_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    try:
        print("public_ticket_panel_command_guard loaded compatibility registrar")
    except Exception:
        pass


__all__ = ["register_public_ticket_panel_command_guard_commands"]
