from __future__ import annotations

"""Retired Bot-class monkey-patch compatibility surface.

``stoney_verify.globals`` now constructs the shared bot through
``stoney_verify.command_runtime.create_discord_bot`` and therefore chooses
``Bot`` vs ``AutoShardedBot`` directly. Importing this module is inert and must
never replace ``discord.ext.commands.Bot`` process-wide.
"""

from ..command_runtime import auto_shard_enabled, configured_shard_count


def install_auto_shard_guard() -> bool:
    """Compatibility no-op retained while old imports/tests are removed."""

    try:
        print(
            "🧭 auto_shard compatibility shim loaded; "
            f"native_bot_selection=true enabled={auto_shard_enabled()} "
            f"configured_shard_count={configured_shard_count() or 'auto'}"
        )
    except Exception:
        pass
    return False


__all__ = [
    "auto_shard_enabled",
    "configured_shard_count",
    "install_auto_shard_guard",
]
