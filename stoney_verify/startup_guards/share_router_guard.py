from __future__ import annotations

"""Historical Share Router compatibility shim.

Production ownership moved to stoney_verify.share_router_runtime and
commands_ext.public_share_router. Importing this legacy startup-guard path is now
side-effect free so the historical guard inventory cannot accidentally mutate
the command tree or attach a second message listener.
"""

from stoney_verify.share_router_resources import DEFAULT_SHARE_CHANNELS
from stoney_verify.share_router_runtime import (
    ROUTES_FILE,
    create_or_repair_hidden_share_hub,
    ensure_share_router_runtime,
    route_message,
)


def install() -> bool:
    """Compatibility entry point for explicit legacy callers only."""

    try:
        from stoney_verify.globals import bot
    except Exception:
        return False
    return ensure_share_router_runtime(bot)


__all__ = [
    "DEFAULT_SHARE_CHANNELS",
    "ROUTES_FILE",
    "create_or_repair_hidden_share_hub",
    "install",
    "route_message",
]
