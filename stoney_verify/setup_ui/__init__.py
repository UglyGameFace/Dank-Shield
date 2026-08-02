from __future__ import annotations

"""Presentation-only setup modules.

The compact setup implementation lives outside ``commands_ext`` because it
registers no command. These aliases preserve its relative imports while the
canonical command owners remain in ``commands_ext``.
"""

import sys

from stoney_verify.commands_ext import public_setup_fresh_choice
from stoney_verify.commands_ext import public_setup_recommend
from stoney_verify.commands_ext import public_ticket_panel_commands
from stoney_verify.commands_ext import public_verify_basic_panel

_ALIASES = {
    "public_setup_fresh_choice": public_setup_fresh_choice,
    "public_setup_recommend": public_setup_recommend,
    "public_ticket_panel_commands": public_ticket_panel_commands,
    "public_verify_basic_panel": public_verify_basic_panel,
}

for _name, _module in _ALIASES.items():
    sys.modules[f"{__name__}.{_name}"] = _module

__all__: list[str] = []
