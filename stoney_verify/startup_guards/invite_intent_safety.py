from __future__ import annotations

"""Ensure invite gateway events are enabled before Discord login.

Discord invite create/delete modlog coverage depends on the bot identifying with
invite events enabled. This guard runs during startup before ``stoney_verify.app``
logs in, so the configured bot can receive ``on_invite_create`` and
``on_invite_delete`` events without relying on server-specific hardcoding.
"""

from typing import Any


def _log(message: str) -> None:
    try:
        print(f"🔗 invite_intent_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ invite_intent_safety {message}")
    except Exception:
        pass


def _set_invites_intent(intents: Any) -> bool:
    if intents is None:
        return False
    try:
        if hasattr(intents, "invites"):
            setattr(intents, "invites", True)
            return True
    except Exception:
        pass
    return False


def enable_invite_intents() -> bool:
    """Enable invite events on the global bot before IDENTIFY/login."""
    try:
        from stoney_verify import globals as g
    except Exception as e:
        _warn(f"could not import globals: {e!r}")
        return False

    changed = False

    try:
        bot = getattr(g, "bot", None)
        changed = _set_invites_intent(getattr(bot, "intents", None)) or changed
    except Exception as e:
        _warn(f"could not patch bot.intents.invites: {e!r}")

    # discord.py stores the identify intents on the connection state too. Set it
    # there as well for versions where bot.intents is only a copy/proxy.
    try:
        bot = getattr(g, "bot", None)
        connection = getattr(bot, "_connection", None)
        changed = _set_invites_intent(getattr(connection, "intents", None)) or changed
    except Exception as e:
        _warn(f"could not patch connection intents: {e!r}")

    if changed:
        _log("enabled Discord invite create/delete gateway intent before login")
    else:
        _warn("Discord library does not expose an invites intent attribute; invite events may depend on library defaults")

    return changed


enable_invite_intents()


__all__ = ["enable_invite_intents"]
