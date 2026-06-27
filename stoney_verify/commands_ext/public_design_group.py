from __future__ import annotations

"""Public /dank design command registrar.

This module exists so Server Design Studio is attached during the normal
commands_ext registration pass, before Discord slash command sync runs. Runtime
startup guards can still provide safety services, but visible slash commands must
be present before the tree is synced.
"""

from typing import Any

import discord

from stoney_verify.interaction_guard import run_guarded_interaction

_REGISTERED = False

_DESIGN_ERROR_GUIDANCE = (
    "Nothing was changed. Reopen `/dank design`, then check `/dank diagnostics` "
    "with the Error ID if it keeps happening."
)


def register_public_design_group_commands(bot: Any = None, tree: Any = None) -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    # Dank Design command registration is native. Layout enhancements are now
    # activated from commands_ext, not from startup boot.
    import stoney_verify.commands_ext as commands_ext
    from stoney_verify.commands_ext.public_setup_group import dank_group
    from stoney_verify.commands_ext import public_design_studio as design
    from stoney_verify.commands_ext import public_design_enhancements as enhancements

    allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
    allowed.add("design")
    commands_ext._ALLOWED_DANK_CHILDREN = allowed

    if dank_group.get_command("design") is None:
        @dank_group.command(name="design", description="Open Dank Design Studio for channel/category name styling.")
        async def dank_design(interaction: discord.Interaction) -> None:
            async def action() -> None:
                await design.open_design_studio(interaction)

            await run_guarded_interaction(
                interaction,
                action,
                defer=False,
                ephemeral=True,
                action_name="/dank design",
                error_title="❌ Dank Design failed safely",
                error_guidance=_DESIGN_ERROR_GUIDANCE,
            )

    enhancements.activate_public_design_enhancements()
    _REGISTERED = True
    print("✅ public_design_group registered guarded native /dank design")


__all__ = ["register_public_design_group_commands"]
