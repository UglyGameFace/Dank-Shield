from __future__ import annotations

"""Public core bootstrap for member join/leave and welcome-card runtime.

This keeps member lifecycle listeners on the normal public registration path,
not on startup-guard import side effects. The join/leave audit router and the
optional personalized welcome-card listener remain separate and idempotent.
"""

from typing import Any


_REGISTERED = False


def register_public_member_lifecycle_runtime(bot: Any, tree: Any) -> None:
    global _REGISTERED

    router_registered = False
    cards_registered = False

    try:
        from stoney_verify.startup_guards import member_lifecycle_router_guard as router

        router_registered = bool(router.install())
        print(
            "✅ public_member_lifecycle_runtime: "
            f"member lifecycle router installed={router_registered}"
        )
    except Exception as exc:
        try:
            print(
                "⚠️ public_member_lifecycle_runtime: failed installing router: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

    try:
        from stoney_verify.commands_ext.public_welcome_card_group import (
            register_public_welcome_card_commands,
        )

        register_public_welcome_card_commands(bot, tree)
        cards_registered = True
    except Exception as exc:
        try:
            print(
                "⚠️ public_member_lifecycle_runtime: "
                "failed registering welcome cards: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

    _REGISTERED = bool(_REGISTERED or router_registered or cards_registered)


__all__ = ["register_public_member_lifecycle_runtime"]
