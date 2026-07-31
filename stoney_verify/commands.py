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


# Ticket security is intentionally loaded outside the tolerant command-module
# registrar. The general registrar may log and continue when an optional command
# module fails; this security scope is mandatory and must abort startup instead.
try:
    from .commands_ext.public_staff_scope import (
        register_public_staff_scope as register_public_ticket_security_scope,
    )
except Exception as e:
    print(f"❌ commands.py could not import mandatory ticket security scope: {repr(e)}")
    raise RuntimeError("Dank Shield ticket security bootstrap failed closed during import.") from e


def _register_public_ticket_security_scope_strict(tree: Any) -> None:
    try:
        register_public_ticket_security_scope(bot, tree)
    except Exception as e:
        try:
            print(f"❌ commands.py mandatory ticket security bootstrap failed: {repr(e)}")
        except Exception:
            pass
        raise RuntimeError(
            "Dank Shield ticket security bootstrap failed closed; command and API startup were aborted."
        ) from e


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


try:
    from .commands_ext.public_command_hub import compact_public_dank_surface
except Exception as e:
    print(f"⚠️ commands.py failed to import public_command_hub: {repr(e)}")

    def compact_public_dank_surface(bot: Any, tree: Any) -> int:  # type: ignore
        return 0


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
_register_public_ticket_security_scope_strict(bot.tree)

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

try:
    compact_public_dank_surface(bot, bot.tree)
except Exception as e:
    try:
        print(f"❌ commands.py failed to compact /dank command surface: {repr(e)}")
    except Exception:
        pass
    raise


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
    _register_public_ticket_security_scope_strict(tree)

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

    compact_public_dank_surface(bot, tree)


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
        print("⚙️ commands.py on_ready (no-op)")


@bot.event
async def on_guild_channel_create(channel):
    """
    Passive observer only.

    Initial Verify UI posting is owned by tickets_new/service.py.
    Leaving active posting here causes duplicate verify panels.
    """
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        print(
            f"🧩 channel_create: name='{channel.name}' id={channel.id} "
            f"cat={int(channel.category_id or 0)} in_scope={is_verification_ticket_channel(channel)}"
        )
    except Exception:
        pass

    return


@bot.event
async def on_guild_channel_update(before, after):
    """
    Passive observer only.

    Prevent duplicate Verify UI posts from channel lifecycle hooks.
    """
    return


@bot.event
async def on_thread_create(thread: discord.Thread):
    """
    Passive observer only.

    Verify UI should not be auto-posted from thread lifecycle hooks.
    """
    try:
        if not isinstance(thread, discord.Thread):
            return
        if is_verification_ticket_channel(thread):
            try:
                print(
                    f"🧩 thread_create: name='{thread.name}' id={thread.id} "
                    f"parent={(getattr(thread.parent, 'id', 0))}"
                )
            except Exception:
                pass
    except Exception:
        return


@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    """
    Passive observer only.

    Prevent duplicate Verify UI posts from thread scope transitions.
    """
    return


__all__ = [
    "_cancel_kick_timer",
    "kick_timer_persist_delete",
    "kick_timer_resume_all",
    "start_join_grace_then_kick_timer_for_member",
    "cancel_verification_wait_timers_for_member",
    "handle_possible_submission",
    "register_extra_commands",
]