"""Opt-in Voice Safety membership lifecycle.

Server joining, verification status or historical membership backfills are not
voice-processing consent. Only an explicit member action on a disclosed
Voice Safety control may authorize speech; leaving revokes it.
"""
from __future__ import annotations

from typing import Any

_INSTALL_ATTR = "_voice_safety_membership_sync_installed"


async def _run_backfill(bot: Any) -> None:
    """Retired: never grandfather members into microphone capture."""
    del bot


async def _on_member_join(member: Any) -> None:
    """Joining a Discord guild never authorizes monitoring."""
    del member


async def _on_member_remove(member: Any) -> None:
    """Withdraw affirmative consent when a member leaves; no auto regrant."""
    if getattr(member, "bot", False):
        return
    guild = getattr(member, "guild", None)
    if guild is None:
        return
    from .voice_safety_consent import withdraw_agreement_on_leave
    try:
        await withdraw_agreement_on_leave(guild.id, member.id)
    except Exception as exc:
        # No speech content, secrets, or error body in diagnostics.
        print(f"Voice Safety consent withdrawal failed: {type(exc).__name__}")


def install_voice_safety_membership_sync(bot: Any) -> bool:
    """Only attach withdrawal; never install join/backfill consent writers."""
    if bot is None or getattr(bot, _INSTALL_ATTR, False):
        return True
    try:
        bot.add_listener(_on_member_remove, "on_member_remove")
        setattr(bot, _INSTALL_ATTR, True)
        return True
    except Exception:
        return False
