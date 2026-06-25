from __future__ import annotations

"""Public core bootstrap for member join/leave logging.

This exists so join/leave logging is registered through the normal public
command/runtime registration path, not only through startup-guard import side
effects.
"""

from typing import Any


_REGISTERED = False


def register_public_member_lifecycle_runtime(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree

    try:
        from stoney_verify.startup_guards import member_lifecycle_router_guard as router

        installed = bool(router.install())
        _REGISTERED = installed or _REGISTERED
        print(f"✅ public_member_lifecycle_runtime: member lifecycle router installed={installed}")
    except Exception as exc:
        try:
            print(f"⚠️ public_member_lifecycle_runtime: failed installing router: {type(exc).__name__}: {exc}")
        except Exception:
            pass


__all__ = ["register_public_member_lifecycle_runtime"]
