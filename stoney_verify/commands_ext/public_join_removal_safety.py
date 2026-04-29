from __future__ import annotations

"""
Public fresh-join removal safety listener.

Native registration point for fresh-join stale timer cleanup. The runtime shim
still intercepts low-level kick/ban calls, but this listener makes the normal
public command profile own the join cleanup step directly.
"""

import discord

from ..members_new.join_removal_safety import clear_stale_timers_for_join


_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ public_join_removal_safety: {message}")
    except Exception:
        pass


async def _on_member_join_clear_stale_timers(member: discord.Member) -> None:
    try:
        await clear_stale_timers_for_join(member, reason="public join listener fresh member join")
    except Exception as e:
        try:
            print(
                "⚠️ public_join_removal_safety join cleanup failed "
                f"guild={getattr(getattr(member, 'guild', None), 'id', None)} "
                f"user={getattr(member, 'id', None)} error={e!r}"
            )
        except Exception:
            pass


def register_public_join_removal_safety(bot, tree) -> None:
    global _REGISTERED
    _ = tree
    if _REGISTERED:
        return

    bot.add_listener(_on_member_join_clear_stale_timers, "on_member_join")
    _REGISTERED = True
    _log("registered native stale verification timer cleanup listener on member join")


__all__ = ["register_public_join_removal_safety"]
