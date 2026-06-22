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

    # Dank Design command registration is now native.
    # Strict/majority helpers are still temporary until their behavior is moved
    # into the design service module in the next cleanup pass.
    from stoney_verify.startup_guards import server_design_strict_layout_guard as strict_layout
    from stoney_verify.startup_guards import server_design_majority_layout_guard as majority_layout
    from stoney_verify.commands_ext import public_design_studio as design

    strict_layout.apply()
    design.register_public_design_studio_command(bot, tree)
    majority_layout.apply()
    _REGISTERED = True
    print("✅ public_design_group registered native /dank design")


__all__ = ["register_public_design_group_commands"]
