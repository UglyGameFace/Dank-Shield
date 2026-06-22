from __future__ import annotations

"""Public /dank design command registrar.

This module exists so Server Design Studio is attached during the normal
commands_ext registration pass, before Discord slash command sync runs. Runtime
startup guards can still provide safety services, but visible slash commands must
be present before the tree is synced.
"""

from typing import Any

_REGISTERED = False


def register_public_design_group_commands(bot: Any = None, tree: Any = None) -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    # TEMPORARY BRIDGE:
    # Dank Design is still being migrated out of startup guards. Keep one clean
    # registration point here until server_design_studio_command_guard is split
    # into a native command module.
    from stoney_verify.startup_guards import server_design_strict_layout_guard as strict_layout
    from stoney_verify.startup_guards import server_design_studio_command_guard as design
    from stoney_verify.startup_guards import server_design_majority_layout_guard as majority_layout

    strict_layout.apply()
    design.apply()
    majority_layout.apply()
    _REGISTERED = True
    print("✅ public_design_group registered /dank design via temporary design bridge")


__all__ = ["register_public_design_group_commands"]
