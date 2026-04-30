from __future__ import annotations

"""Top-level /commands alias.

Ticket Tool exposes a visible /commands command. Stoney's preferred boring
layout keeps help/audit under /stoney, but Discord global command propagation
and crowded grouped menus can make a newly-added /stoney commands subcommand
hard to spot immediately.

This guard adds one safe top-level /commands fallback that calls the same command
surface audit callback used by /stoney commands. It gives server owners a stable,
obvious way to inspect the current command surface while we keep consolidating
old command spam into grouped families.
"""

from typing import Any

from discord import app_commands

_PATCHED = False


def install_top_level_commands_alias() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        from stoney_verify.globals import bot
        from stoney_verify.commands_ext.public_help_group import stoney_commands_callback
    except Exception as e:
        try:
            print(f"⚠️ top_level_commands_alias import failed: {e!r}")
        except Exception:
            pass
        return

    try:
        existing = bot.tree.get_command("commands", guild=None)
    except Exception:
        existing = None

    if existing is None:
        try:
            bot.tree.add_command(
                app_commands.Command(
                    name="commands",
                    description="Audit Stoney's current slash command surface.",
                    callback=stoney_commands_callback,
                )
            )
            print("✅ top_level_commands_alias: registered /commands audit fallback")
        except Exception as e:
            print(f"⚠️ top_level_commands_alias failed registering /commands: {e!r}")
            return
    else:
        try:
            print("✅ top_level_commands_alias: /commands already exists")
        except Exception:
            pass

    _PATCHED = True


install_top_level_commands_alias()


__all__ = ["install_top_level_commands_alias"]
