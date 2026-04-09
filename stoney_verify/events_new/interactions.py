from __future__ import annotations

from typing import Any

import discord


_EVENTS_NEW_INTERACTIONS_REGISTERED = False


async def _dispatch_possible_submission(message: discord.Message) -> None:
    """
    Lazy-import the shared submission handler to avoid circular import problems.
    """
    try:
        from ..commands import handle_possible_submission
        await handle_possible_submission(message)
    except Exception as e:
        try:
            print(f"⚠️ events_new.interactions submission dispatch failed: {repr(e)}")
        except Exception:
            pass


async def _on_message(message: discord.Message) -> None:
    """
    Bridge normal Discord message events into the centralized submission handler.

    This is mainly for webhook upload messages that land in verification tickets.
    The handler itself is defensive and will no-op for irrelevant messages.
    """
    try:
        if not isinstance(message, discord.Message):
            return

        if not message.guild:
            return

        await _dispatch_possible_submission(message)

    except Exception as e:
        try:
            print(f"⚠️ events_new.interactions.on_message error: {repr(e)}")
        except Exception:
            pass


async def _on_message_edit(before: discord.Message, after: discord.Message) -> None:
    """
    Some webhook/content states may settle right after the initial post.
    Re-check edited webhook messages as a best-effort fallback.
    """
    try:
        if not isinstance(after, discord.Message):
            return

        if not after.guild:
            return

        # Only bother re-processing edits that are likely relevant to webhook uploads.
        if not getattr(after, "webhook_id", None) and not getattr(before, "webhook_id", None):
            return

        await _dispatch_possible_submission(after)

    except Exception as e:
        try:
            print(f"⚠️ events_new.interactions.on_message_edit error: {repr(e)}")
        except Exception:
            pass


def setup(bot: Any) -> None:
    """
    Register interaction/message bridge listeners once.
    Safe to call multiple times.
    """
    global _EVENTS_NEW_INTERACTIONS_REGISTERED

    if _EVENTS_NEW_INTERACTIONS_REGISTERED:
        try:
            print("ℹ️ events_new.interactions.setup already ran; skipping duplicate registration.")
        except Exception:
            pass
        return

    try:
        bot.add_listener(_on_message, "on_message")
        bot.add_listener(_on_message_edit, "on_message_edit")
        _EVENTS_NEW_INTERACTIONS_REGISTERED = True
        try:
            print("✅ events_new.interactions listeners registered.")
        except Exception:
            pass
    except Exception as e:
        try:
            print(f"❌ events_new.interactions.setup failed: {repr(e)}")
        except Exception:
            pass
        raise


__all__ = [
    "setup",
]