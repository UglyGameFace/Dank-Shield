from __future__ import annotations

"""Retired command-scope dedupe startup guard compatibility surface.

Beta-sync defaults and stale configured guild-copy cleanup now live in
``stoney_verify.command_runtime`` and are executed by the shared Dank Shield
bot's native setup hook / command tree. Importing this module is inert and must
not attach an ``on_ready`` listener or sync the command tree.
"""

from ..command_runtime import configured_guild_cleanup_ids


def apply() -> bool:
    """Compatibility no-op retained while old imports/tests are removed."""

    try:
        print(
            "🧭 command_scope_dedupe compatibility shim loaded; "
            f"native_cleanup_ids={sorted(configured_guild_cleanup_ids())}"
        )
    except Exception:
        pass
    return False


__all__ = ["apply"]
