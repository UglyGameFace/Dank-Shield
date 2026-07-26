# stoney_verify/commands.py
from __future__ import annotations

from typing import Any, Optional

import discord

from .globals import *  # noqa: F401,F403

# ============================================================
# Ticket helpers still used by passive lifecycle observers
# ============================================================
from .tickets import is_verification_ticket_channel

# ============================================================
# Split command module loader
# ============================================================
try:
    from .commands_ext import register_all_commands
except Exception:
    def register_all_commands(bot: Any, tree: Any) -> None:  # type: ignore
        return None


# Welcome cards use an explicit canonical registration path. This is deliberately
# outside startup_guards and never removes/replaces an existing command.
try:
    from .commands_ext.public_welcome_card_studio import (
        register_public_welcome_card_studio_commands,
    )
except Exception as e:
    print(f"⚠️ commands.py failed to import public_welcome_card_studio: {repr(e)}")

    def register_public_welcome_card_studio_commands(bot: Any, tree: Any) -> None:  # type: ignore
        return None


# Member profile cards extend the existing /dank profile group through one
# canonical registration path. The runtime owns exactly one additive on_message
# listener and never replaces user messages or the join-only welcome system.
try:
    from .commands_ext.public_profile_cards import register_public_profile_cards
except Exception as e:
    print(f"⚠️ commands.py failed to import public_profile_cards: {repr(e)}")

    def register_public_profile_cards(bot: Any, tree: Any) -> None:  # type: ignore
        return None


# ============================================================
# Kick timer bridges
# events.py imports these from commands.py, so keep them exposed
# ============================================================
try:
    from .commands_ext.kick_timers import (
        _cancel_kick_timer,
        kick_timer_persist_delete,
        kick_timer_resume_all,
        start_join_grace_then_kick_timer_for_member,
        cancel_verification_wait_timers_for_member,
    )
except Exception:
    def _cancel_kick_timer(channel_id: int) -> bool:
        return False

    async def kick_timer_persist_delete(channel_id: int) -> None:
        return None

    async def kick_timer_resume_all() -> None:
        return None

    async def start_join_grace_then_kick_timer_for_member(
        member: discord.Member,
        source_channel: Optional[discord.TextChannel] = None,
        grace_minutes: Optional[int] = None,
    ) -> bool:
        return False

    async def cancel_verification_wait_timers_for_member(guild_id: int, owner_id: int) -> bool:
        return False


# ============================================================
# Centralized interaction/submission handlers
# ============================================================
try:
    from .interaction_handlers import (
        handle_possible_submission,
        register_interaction_handlers,
    )
except Exception as e:
    print(f"⚠️ commands.py failed to import interaction_handlers: {repr(e)}")

    async def handle_possible_submission(message: discord.Message) -> None:  # type: ignore
        return None

    def register_interaction_handlers(bot_instance: Any) -> None:  # type: ignore
        return None


# ============================================================
# Register split slash commands
# ============================================================
try:
    register_all_commands(bot, bot.tree)
    register_public_welcome_card_studio_commands(bot, bot.tree)
except Exception as e:
    try:
        print(f"⚠️ commands.py failed to register split command modules: {repr(e)}")
    except Exception:
        pass

# Live profile cards must still attach their existing-channel runtime when an
# unrelated command module fails. Their registrar is idempotent per bot and does
# not replace application commands or event owners.
try:
    register_public_profile_cards(bot, bot.tree)
except Exception as e:
    try:
        print(f"⚠️ commands.py failed to register public profile cards: {repr(e)}")
    except Exception:
        pass


# ============================================================
# Register centralized component interaction handler
# ============================================================
try:
    register_interaction_handlers(bot)
except Exception as e:
    try:
        print(f"⚠️ commands.py failed to register interaction handlers: {repr(e)}")
    except Exception:
        pass


# ============================================================
# Hook for app.py probing
# ============================================================
def register_extra_commands(tree) -> None:
    try:
        register_all_commands(bot, tree)
        register_public_welcome_card_studio_commands(bot, tree)
    except Exception as e:
        try:
            print(f"⚠️ register_extra_commands failed: {repr(e)}")
        except Exception:
            pass

    try:
        register_public_profile_cards(bot, tree)
    except Exception as e:
        try:
            print(f"⚠️ register_extra_commands profile cards failed: {repr(e)}")
        except Exception:
            pass


# ============================================================
# Events
# IMPORTANT:
# - Slash registration is owned by commands_ext/*
# - Component interaction handling is owned by interaction_handlers.py
# - events.py should remain the final real on_ready owner
# ============================================================
@bot.event
async def on_ready():
    """
    No-op startup hook.

    Keep this here because app import order expects commands.py to define
    a harmless on_ready before events.py overrides it with the real one.
    """
    try:
        u = getattr(bot, "user", None)
        print(f"⚙️ commands.py on_ready (no-op) for: {u}")
    except Exception:
        pass
