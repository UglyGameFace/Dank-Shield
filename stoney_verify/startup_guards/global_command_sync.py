from __future__ import annotations

"""Retired global CommandTree.sync monkey-patch compatibility surface.

Global sync budget policy is now implemented by
``stoney_verify.command_runtime.validate_global_sync_budget`` and invoked by the
canonical command-sync owner. Importing this module is inert and must never
replace ``discord.app_commands.CommandTree.sync``.
"""


def install_global_command_sync_guard() -> bool:
    """Compatibility no-op retained while old imports/tests are removed."""

    try:
        print(
            "🧭 global_command_sync compatibility shim loaded; "
            "global CommandTree.sync patch is retired"
        )
    except Exception:
        pass
    return False


__all__ = ["install_global_command_sync_guard"]
