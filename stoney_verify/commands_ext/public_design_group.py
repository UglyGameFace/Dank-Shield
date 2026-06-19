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

    # Load the strict layout consistency guard before the design command module
    # builds previews. This makes missing/different separators count as real
    # drift instead of being hidden by the smart font/base semantic skip.
    from stoney_verify.startup_guards import server_design_strict_layout_guard as strict_layout
    from stoney_verify.startup_guards import server_design_studio_command_guard as design

    strict_layout.apply()
    design.apply()
    _REGISTERED = True
    print("✅ public_design_group registered /dank design")


__all__ = ["register_public_design_group_commands"]
