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

    # Dank Design command registration is native. Layout enhancements are now
    # activated from commands_ext, not from startup boot.
    from stoney_verify.commands_ext import public_design_studio as design
    from stoney_verify.commands_ext import public_design_enhancements as enhancements

    design.register_public_design_studio_command(bot, tree)
    enhancements.activate_public_design_enhancements()
    _REGISTERED = True
    print("✅ public_design_group registered native /dank design")


__all__ = ["register_public_design_group_commands"]
