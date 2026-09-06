from __future__ import annotations

"""Public /dank design command registrar.

Server Design is registered during the normal commands_ext pass. Runtime startup
layers do not inject commands or replace Studio behavior after registration.
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

    from stoney_verify.commands_ext.public_setup_group import dank_group
    from stoney_verify.commands_ext import public_design_studio_v2 as design

    # Command/profile ownership is declarative in commands_ext.__init__.py.
    # Registration must not rewrite that registry at runtime.

    if dank_group.get_command("design") is None:
        @dank_group.command(name="design", description="Open Dank Design Studio for safe server name styling.")
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

    _REGISTERED = True
    print("✅ public_design_group registered consolidated native /dank design")


__all__ = ["register_public_design_group_commands"]
